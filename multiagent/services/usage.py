# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Usage services (USO-T02): plan usage per app, tokens per app/model/level/role and estimated API cost.

Read-only (but ``notify_budgets``, see below) and neutral of domain. Three questions, one method each, with the
same answer from Python, the token-protected ``/gui/api/usage/*`` routes, ``/api/v1/usage/costs`` and
``multiagent usage-report``:

* ``plans()``: what each app's plan allows and how much is used, **only where the app leaves the number on
  disk or documents it** (USO-T01). Codex: the percent and reset of its 5 hour and weekly windows. Claude:
  the reset instant after a limit was hit, and the percent only if the operator saved a ``statusLine``
  snapshot (``usage_claude_statusline_file``). Copilot: AI credits used this month, the monthly reset (a
  documented rule) and a percent only if the operator set ``usage_copilot_monthly_credits``. Antigravity (agy):
  the five hour and weekly windows per model group and per model, from ``agy -p /usage`` (no model is spent;
  run without a shell, with a time limit, cached for a minute). Everything
  else is listed in ``not_available`` and is never estimated or shown as 0.
* ``tokens(since, until, apps, group_by)``: tokens per app, model, level (catalog) and role, measured inside
  an exact time window. ``role`` is ``primary`` (a top-level session or a workflow step), ``delegated`` (a
  subagent or spawned thread) or ``unknown`` (the source does not say); the core knows no orchestrator or
  worker role. Tokens a source did not report stay ``None``.
* ``costs(since, until, limit)``: estimated cost of the framework's own provider calls (the ``telemetry``
  table) per run, model, day and month, each group with the price evidence behind it. A cost that cannot
  be computed stays unknown and is counted apart (``cost_unknown_records`` and its reasons), never added as 0.

The apps' own sessions are priced by their plans, not per token, so no USD is computed for them. The tokens
of executor attempts are not added here because their app session already counts them.

Budgets (USO-T03) compare the framework's own estimated cost with limits the operator configured
(``usage_budgets`` in ``config``; no migration, the consumption is derived from the ``telemetry`` table):

* ``budgets(run_id)``: every budget with what it has used, what is left, the percent consumed, its state
  (``ok``, ``alert`` once an alert percent was reached, ``limit_reached`` at 100 %), the alerts reached and the
  next one, in the currency of the budget (only costs priced in that currency are counted; nothing is
  converted, and the calls whose cost is unknown are counted apart, so ``complete`` says whether the figure is
  the whole cost). It also lists the latest alert events, so a view needs no other call. Read-only.
* ``notify_budgets(run_id)``: the same answer, after appending one ``budget.threshold`` team event for each
  alert percent reached that was not announced before, idempotent per (budget, alert, period, amount, currency
  and project): a month budget alerts once per month, a project budget once, a run budget once per run, and a
  raised amount alerts again at its own percents.
* ``mode`` is ``warn`` (default) or ``cut``. Nothing here stops a call: ``cut`` is reported and the budget guard of the
  call chain (``budget_cut``, USO-T04) acts on it. ``attach(engine)`` plugs both in: the guard reads ``notify_budgets``
  before each call and every call the engine records announces the alerts it reached.
* ``reserving(run_id)`` and ``settle(reservation_id)`` (USO-T04) are what makes two calls against one budget safe: a write
  transaction (``BEGIN IMMEDIATE``) in which the guard reads ``budgets()`` plus the outstanding reservations and books the
  estimate of the call it lets through as a ``budget.reserved`` team event (no table of its own). The reservation ends with
  a ``budget.settled`` event when the real usage of that call is recorded (``attach`` hooks the recorder, so a failed call
  ends it too) or is ignored after ``RESERVATION_TTL``. These and ``notify_budgets`` are the only methods here that write.

Paths come from the settings the session viewer uses (``gui_claude_home``, ``gui_codex_home``,
``gui_copilot_home``) and ``gui_session_sources`` says which apps are shown. No working folder, title,
session id, prompt or credential file is read or returned. Errors (``ServiceError``): ``invalid_request``
(422) for a bad ``since``/``until``/``apps``/``group_by``/``limit``.
"""
from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
import tempfile
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..budget_cut import BudgetHold
from ..observability.usage import load_basis, totals_of
from ..sessions import SessionsConfig
from ..sessions._common import normalize_ts
from ..sessions import usage_sources as sources
from ..sessions.usage_sources import PlanObservation, RateWindow, TokenEvent
from .errors import INVALID_REQUEST, ServiceError, service_errors

if TYPE_CHECKING:
    from . import ApplicationServices

_logger = logging.getLogger(__name__)

APPS = ("claude", "codex", "copilot", "antigravity", "framework")
GROUP_DIMENSIONS = ("app", "model", "level", "role", "day", "month")
DEFAULT_GROUP_BY = ("app", "model", "level", "role")
DEFAULT_TOKEN_DAYS = 7
DEFAULT_COST_DAYS = 31
MAX_WINDOW_DAYS = 400
DEFAULT_RUN_LIMIT = 100
MAX_RUN_LIMIT = 500
MAX_EVIDENCE = 20
BUDGET_EVENT = "budget.threshold"    # team event (USO-T03): a budget reached one of its alert percents
BUDGET_ACTOR = "system:budgets"
RESERVED_EVENT = "budget.reserved"   # team event (USO-T04): the estimate of a call the budget guard let through
SETTLED_EVENT = "budget.settled"     # team event (USO-T04): that call's usage was recorded (or it never happened)
# A reservation nobody settled (the process died between the guard and the record) stops counting after this long. It must
# outlast any provider call (the timeouts are 120 s), because a reservation that ends early lets another call through.
RESERVATION_TTL = timedelta(minutes=15)
MAX_BUDGET_RUNS = 50                # run budgets: the most consumed runs shown for each budget
MAX_RECENT_ALERTS = 20
_RUN_CHUNK = 400                     # run ids per query (SQLite caps the number of parameters)
_PERCENT_EPS = 1e-9
LIMIT_LOOKBACK = timedelta(days=8)   # a plan reset is never believed to be farther away than this (CNT-T01)
TOKEN_FIELDS = ("input", "output", "cache_read", "cache_write", "reasoning", "total")

# How each source measures a window (so a reader of the numbers knows how exact they are).
MEASUREMENT = {
    "claude": "per_message",       # one record per assistant message, deduplicated by message id
    "codex": "counter_growth",     # growth of the session's cumulative counters inside the window
    "copilot": "per_call",         # one row per call in the usage database
    "antigravity": "not_recorded",  # its per-generation counts (gen_metadata) carry no time, so none fall in a window
    "framework": "per_call",       # one telemetry row per provider call
}
CONVENTIONS = {
    "claude": "total = input + output + cache_read + cache_write (input excludes the cache)",
    "codex": "total = the reported total_tokens (its input already counts the cached part)",
    "copilot": "total = input + output (cache and reasoning are reported apart)",
    "antigravity": "per session only (multiagent sessions): total = input + output + cache_read (input excludes the cache)",
    "framework": "total = input + output (input already counts the cached part; cache_read is shown apart)",
}
RESET_CONFIDENCE = {sources.RESET_APP_EPOCH: "high", sources.RESET_RULE: "medium", sources.RESET_NONE: "none"}
AGY_USAGE_ARGS = ("-p", "/usage", "--output-format", "text", "--print-timeout", "30s")  # /usage spends no model (GEM-T01)
AGY_TIMEOUT_SECONDS = 40.0
AGY_CACHE_SECONDS = 60.0   # plans() is polled; /usage is a network call, so the answer is reused this long
AgyRunner = Callable[[Sequence[str], float], tuple[int, str]]


def run_agy_usage(argv: Sequence[str], timeout: float) -> tuple[int, str]:
    """Run ``argv`` without a shell and never wait past ``timeout``; ``(exit code, stdout)`` (``(1, reason)`` on failure).

    Output goes to temporary files, not pipes, so a child that outlives its parent cannot hold the call.
    """
    try:
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            process = subprocess.Popen(list(argv), stdin=subprocess.DEVNULL, stdout=out, stderr=err, shell=False)
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                return 1, f"timed out after {timeout:g} s"
            out.seek(0)
            return code, out.read().decode("utf-8", errors="replace")
    except OSError as exc:
        return 1, type(exc).__name__


# What an app does not leave on disk, from USO-T01 (section 4); shown instead of a number.
NOT_AVAILABLE_ALWAYS = ("model_limits",)


def _invalid(message: str, **details: Any) -> ServiceError:
    return ServiceError(INVALID_REQUEST, message, status=422, details=details)


def _iso(moment: datetime | None) -> str | None:
    return normalize_ts(moment.isoformat()) if moment is not None else None


def _instant(value: Any, name: str) -> datetime | None:
    """An ISO-8601 text (a missing zone means UTC) or a datetime as an aware UTC datetime; ``None`` when empty."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            raise _invalid(f"{name} must be an ISO-8601 date or time", field=name) from None
    else:
        raise _invalid(f"{name} must be an ISO-8601 date or time", field=name)
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def _names(value: Any, name: str, allowed: Sequence[str]) -> tuple[str, ...]:
    """A list of names (or one comma separated text) checked against ``allowed``, in order and without repeats."""
    if value is None or value == "":
        return ()
    items = [value] if isinstance(value, str) else list(value) if isinstance(value, Iterable) else None
    if items is None or not all(isinstance(item, str) for item in items):
        raise _invalid(f"{name} must be a list of names", field=name)
    names = tuple(dict.fromkeys(part.strip() for item in items for part in item.split(",") if part.strip()))
    unknown = [item for item in names if item not in allowed]
    if unknown:
        raise _invalid(f"{name} accepts only {', '.join(allowed)}; got {', '.join(unknown)}", field=name)
    return names


def _add(first: int | None, second: int | None) -> int | None:
    return second if first is None else (first if second is None else first + second)


class _Sums:
    """Running token sums of a group; a field nobody reported stays ``None``."""

    __slots__ = ("events", *TOKEN_FIELDS)

    def __init__(self) -> None:
        self.events = 0
        for field in TOKEN_FIELDS:
            setattr(self, field, None)

    def add(self, event: TokenEvent) -> None:
        self.events += 1
        for field in TOKEN_FIELDS:
            setattr(self, field, _add(getattr(self, field), getattr(event, field)))

    def view(self) -> dict[str, Any]:
        return {"events": self.events, **{field: getattr(self, field) for field in TOKEN_FIELDS}}


def _sort_key(value: Any) -> tuple[bool, str]:
    return value is None, "" if value is None else str(value)


def _evidence(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The distinct price evidence behind a group's computed costs, in a stable order (at most ``MAX_EVIDENCE``)."""
    found: dict[str, dict[str, Any]] = {}
    for record in records:
        basis = record.get("cost_basis")
        if not isinstance(basis, dict):
            continue
        entry = {"model_id": basis.get("model_id"), "currency": basis.get("currency"),
                 "input": basis.get("input"), "output": basis.get("output")}
        found.setdefault(json.dumps(entry, sort_keys=True, default=str), entry)
    return [found[key] for key in sorted(found)][:MAX_EVIDENCE]


def _spent(record: dict[str, Any]) -> tuple[str, float] | None:
    """``(currency, amount)`` of a record's estimated cost, or ``None`` when it has none (never 0).

    A USD cost sits in ``estimated_cost_usd``; any other currency only in the price evidence (``cost_basis``).
    """
    if record["estimated_cost_usd"] is not None:
        return "USD", float(record["estimated_cost_usd"])
    basis = record.get("cost_basis") or {}
    cost, currency = basis.get("cost"), basis.get("currency")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool) and isinstance(currency, str) and currency.strip():
        return currency.strip().upper(), float(cost)
    return None


def _in_project(record: dict[str, Any], project: str | None) -> bool:
    """``project`` is a project's name or id; ``None`` accepts every record."""
    return project is None or project in (record.get("project"), record.get("project_id"))


def _decimal(value: Any) -> Decimal | None:
    """Exact decimal text (as the guard wrote it into a reservation) as a ``Decimal``; ``None`` when it is not a number."""
    if not isinstance(value, str):
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _number(value: float) -> str:
    """A number as the shortest text that reads back as the same float (``10``, ``87.5``, ``1000004``)."""
    text = repr(float(value))
    return text[:-2] if text.endswith(".0") else text


def _project_id_of(records: list[dict[str, Any]]) -> str | None:
    """The id of the project the records belong to: the system store's when it has one, else the first found."""
    found = [r for r in records if r.get("project_id") is not None]
    return next((r["project_id"] for r in found if r["bundle"] is None), found[0]["project_id"] if found else None)


def _percent_text(value: float) -> float:
    """A percent cut (not rounded) to two decimals, so a shown 80.0 never hides a state computed below 80."""
    return math.floor((value + _PERCENT_EPS) * 100) / 100


def _unknown_reason(record: dict[str, Any]) -> str | None:
    """Why a record has no USD cost: ``None`` when it has one or is priced in another currency."""
    if record["estimated_cost_usd"] is not None:
        return None
    basis = record.get("cost_basis") or {}
    if isinstance(basis.get("cost"), (int, float)) and isinstance(basis.get("currency"), str) and basis["currency"]:
        return None
    if record["provider"] == "human":
        return "human_step"
    if record["tokens_in"] is None or record["tokens_out"] is None:
        return "tokens_not_reported"
    return "no_price_evidence"


class UsageServices:
    def __init__(self, services: ApplicationServices, *, clock: Callable[[], datetime] | None = None):
        self._services = services
        # A test sets ``clock`` to move time; every window and "now" below reads it.
        self.clock: Callable[[], datetime] = clock or (lambda: datetime.now(timezone.utc))
        # Runs ``agy -p /usage``; a test replaces it with a fake so no real agy is ever started.
        self.agy_runner: AgyRunner = run_agy_usage
        self._agy_cache: tuple[float, dict[str, Any]] | None = None
        self._announced: set[str] = set()   # budget alerts already written as events (a cache: the events are the truth)

    # --- the engine (USO-T04) -------------------------------------------------------

    def attach(self, engine: Any) -> None:
        """Plug the budgets into a workflow engine: its budget guard (``budget_cut``) reads them before each model call
        and every call its recorder stores ends the reservation of that call and announces the alerts it reached
        (``notify_budgets``).

        ``ApplicationServices`` and the bundle runtimes call this for each engine. With no budget configured nothing is
        read, reserved or announced.
        """
        engine.budgets_source = self
        engine.usage_recorder.after_record = self._after_call

    def _after_call(self, record: Any) -> None:
        """After a call is recorded (success or failure): end its reservation, then announce the alerts it reached.

        Best effort and nothing without budgets. The usage is stored before this runs, so for an instant a call counts
        both as used and as reserved (a call checked right then is held, never let through); the other way round would
        under-count.
        """
        if not self._services.settings.usage_budgets:
            return
        try:
            self._settle_call(record.run_id, record.step_id, record.attempt)
        except Exception:
            _logger.warning("The reservation of a recorded call could not be ended; it expires by itself", exc_info=True)
        try:
            self.notify_budgets(run_id=record.run_id)
        except Exception:
            _logger.warning("Budget alerts could not be announced after a recorded call", exc_info=True)

    # --- reservations (USO-T04) -----------------------------------------------------

    @contextmanager
    def reserving(self, *, run_id: Any = None) -> Iterator[BudgetHold]:
        """The section in which the budget guard decides and books a call: one write transaction (``BEGIN IMMEDIATE``).

        Inside it ``hold.status`` is ``budgets(run_id)`` and ``hold.reserved`` the estimates of the calls that were let
        through and have not been recorded yet (by ``reservation_key``), so two calls against one budget cannot both
        read the same ``used`` and pass: the second waits for the first transaction and sees its reservation.
        ``hold.reserve(payload)`` appends the ``budget.reserved`` event (``run_id``, ``step_id``, ``attempt``,
        ``model_id``, ``currency``, ``amount`` as exact decimal text and the ``budgets`` keys; at most 2048 bytes) and
        returns its id; it is committed when the section ends without an error, and rolled back if it raises. Nothing
        else must be written to this database from inside (the lock is taken).
        """
        store = self._services.tasks.store
        with store.transaction() as conn:
            status = self.budgets(run_id=run_id)
            reserved: dict[str, Decimal] = {}
            for _cause, payload in self._outstanding(conn):
                amount = _decimal(payload.get("amount"))
                if amount is None:
                    continue
                for key in payload.get("budgets") or ():
                    reserved[key] = reserved.get(key, Decimal(0)) + amount

            def reserve(payload: Any) -> str:
                reservation_id = uuid.uuid4().hex
                store.append_event(conn, RESERVED_EVENT, actor=BUDGET_ACTOR, cause=reservation_id,
                                   ref=f"run:{payload.get('run_id')}", payload=dict(payload))
                return reservation_id

            yield BudgetHold(status=status, reserved=reserved, reserve=reserve)

    @staticmethod
    def _outstanding(conn: Any, *, run_id: str | None = None, expired: bool = False) -> list[tuple[str, dict[str, Any]]]:
        """``(reservation id, payload)`` of the reservations nobody settled, oldest first. Those older than
        ``RESERVATION_TTL`` are left out unless ``expired``; ``run_id`` keeps one run's."""
        sql = ("SELECT cause, payload_json FROM team_events WHERE type = ? AND created_at >= ? "
               "AND cause NOT IN (SELECT cause FROM team_events WHERE type = ?)")
        cutoff = "" if expired else (datetime.now(timezone.utc) - RESERVATION_TTL).isoformat()
        params: list[Any] = [RESERVED_EVENT, cutoff, SETTLED_EVENT]
        if run_id is not None:
            sql += " AND ref = ?"
            params.append(f"run:{run_id}")
        found: list[tuple[str, dict[str, Any]]] = []
        for row in conn.execute(sql + " ORDER BY seq", params):
            try:
                payload = json.loads(row["payload_json"])
            except ValueError:
                continue
            if isinstance(payload, dict):
                found.append((row["cause"], payload))
        return found

    def _settle_call(self, run_id: Any, step_id: Any, attempt: Any) -> None:
        """End the newest outstanding reservation of this run, step and attempt (one record ends one reservation).

        A run makes one call at a time, so the record belongs to the latest reservation; an older one that is still
        outstanding was left by a call that never recorded anything and expires by itself.
        """
        if not isinstance(run_id, str) or not run_id:
            return
        store = self._services.tasks.store
        with store.transaction() as conn:
            for cause, payload in reversed(self._outstanding(conn, run_id=run_id, expired=True)):
                if payload.get("step_id") == step_id and payload.get("attempt") == attempt:
                    store.append_event(conn, SETTLED_EVENT, actor=BUDGET_ACTOR, cause=cause, ref=f"run:{run_id}",
                                       payload={"run_id": run_id, "reason": "recorded"})
                    return

    def settle(self, reservation_id: str) -> bool:
        """End a reservation by hand (the guard uses it when a call it let through did not happen); ``False`` when there is
        no such outstanding reservation."""
        store = self._services.tasks.store
        with store.transaction() as conn:
            for cause, payload in self._outstanding(conn, expired=True):
                if cause == reservation_id:
                    store.append_event(conn, SETTLED_EVENT, actor=BUDGET_ACTOR, cause=cause,
                                       ref=f"run:{payload.get('run_id')}",
                                       payload={"run_id": payload.get("run_id"), "reason": "cancelled"})
                    return True
        return False

    # --- configuration ----------------------------------------------------------

    def _now(self) -> datetime:
        moment = self.clock()
        return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)

    def _enabled(self) -> tuple[str, ...]:
        shown = tuple(self._services.settings.gui_session_sources)
        return tuple(app for app in APPS if app in shown)

    def _config(self) -> SessionsConfig:
        """Where each app keeps its files: the viewer's ``gui_*_home`` settings, else the app's folder in the user's home."""
        settings, home = self._services.settings, Path.home()
        return SessionsConfig(claude_home=settings.gui_claude_home or home / ".claude",
                              codex_home=settings.gui_codex_home or home / ".codex",
                              copilot_home=settings.gui_copilot_home or home / ".copilot",
                              antigravity_home=settings.gui_antigravity_home or home / ".gemini" / "antigravity-cli")

    def _stores(self) -> list[tuple[str | None, Any]]:
        """``(bundle, store)`` of the system and of every bundle that has runs."""
        return [(rt.bundle, rt.store) for rt in self._services.runs._runtimes()]  # type: ignore[attr-defined]

    def _window(self, since: Any, until: Any, now: datetime, default_days: int) -> tuple[datetime, datetime]:
        end = _instant(until, "until") or now
        start = _instant(since, "since") or end - timedelta(days=default_days)
        if start >= end:
            raise _invalid("since must be before until", field="since")
        if end - start > timedelta(days=MAX_WINDOW_DAYS):
            raise _invalid(f"the window may not be longer than {MAX_WINDOW_DAYS} days", field="since")
        return start, end

    # --- plans ----------------------------------------------------------------------

    @service_errors
    def plans(self) -> dict[str, Any]:
        """Plan usage per app: windows with percent and reset when the app leaves them on disk, else ``not_available``.

        ``{generated_at, apps: [{app, status, plan, source, windows, credits, not_available, notes}]}``.
        ``status`` is ``ok`` (a window with a known percent), ``partial`` (only resets, hits or consumption),
        ``not_available`` (nothing found) or ``disabled``. A window has ``window`` (``five_hour``, ``weekly``,
        ``model_weekly``, ``monthly``, ``spend``), ``used_percent`` (``None`` when unknown), ``state``
        (``current``, ``limit_reached``, ``window_reset`` when the reset passed after the app wrote the number,
        so the real percent is unknown, or ``unknown``), ``resets_at`` (UTC), ``reset_source`` (``app_epoch`` exact,
        ``rule`` documented) with ``reset_confidence``, and ``elapsed_percent`` of the window already gone.
        """
        now = self._now()
        config, enabled = self._config(), self._enabled()
        apps = []
        for app, build in (("claude", self._plan_claude), ("codex", self._plan_codex), ("copilot", self._plan_copilot),
                           ("antigravity", self._plan_antigravity)):
            apps.append(build(config, now) if app in enabled else {"app": app, "status": "disabled"})
        return {"generated_at": _iso(now), "apps": apps}

    @staticmethod
    def _window_view(window: RateWindow, now: datetime, *, started: datetime | None = None,
                     last_hit_at: datetime | None = None, detail: str | None = None,
                     assume_current: bool = False) -> dict[str, Any]:
        resets, percent = window.resets_at, window.used_percent
        passed = resets is not None and now >= resets
        if passed:
            state, shown = "window_reset", None
        elif percent is not None:
            state, shown = ("limit_reached" if percent >= 100 else "current"), percent
        elif last_hit_at is not None and resets is not None:
            state, shown = "limit_reached", None
        else:
            state, shown = ("current" if assume_current else "unknown"), None
        elapsed = None
        if resets is not None and not passed:
            begin = started or (resets - timedelta(minutes=window.window_minutes) if window.window_minutes else None)
            if begin is not None and resets > begin:
                elapsed = round(min(100.0, max(0.0, (now - begin) / (resets - begin) * 100)), 1)
        return {"window": window.window, "used_percent": shown,
                "observed_used_percent": percent if passed else None, "state": state,
                "resets_at": _iso(resets), "reset_source": window.reset_source,
                "reset_confidence": RESET_CONFIDENCE.get(window.reset_source, "none"),
                "window_minutes": window.window_minutes, "elapsed_percent": elapsed,
                "last_hit_at": _iso(last_hit_at), "detail": detail}

    @staticmethod
    def _entry(app: str, observed: PlanObservation | None, windows: list[dict[str, Any]], not_available: list[str],
               notes: list[str], *, data: bool, credits: Any = None) -> dict[str, Any]:
        """One app of ``plans()``: ``ok`` with a known percent, ``partial`` with other data, else ``not_available``."""
        if any(window["used_percent"] is not None for window in windows):
            status = "ok"
        else:
            status = "partial" if data else "not_available"
        return {"app": app, "status": status, "plan": observed.plan if observed else None,
                "source": ({"kind": observed.source, "observed_at": _iso(observed.observed_at)}
                           if observed is not None else None),
                "windows": windows, "credits": credits, "not_available": not_available, "notes": notes}

    @staticmethod
    def _missing_percents(windows: list[dict[str, Any]]) -> list[str]:
        have = {window["window"] for window in windows if window["used_percent"] is not None}
        return [name for name, window in (("five_hour_percent", sources.FIVE_HOUR), ("weekly_percent", sources.WEEKLY))
                if window not in have]

    def _plan_claude(self, config: SessionsConfig, now: datetime) -> dict[str, Any]:
        windows: list[dict[str, Any]] = []
        notes: list[str] = []
        observed: PlanObservation | None = None
        snapshot = self._services.settings.usage_claude_statusline_file
        if snapshot is None:
            notes.append("statusline_not_configured")
        else:
            observed, found = sources.read_claude_statusline(snapshot)
            notes += found
            windows += [self._window_view(window, now) for window in observed.windows] if observed else []
        hits, found = (sources.read_claude_limit_hits(config.claude_home, now - LIMIT_LOOKBACK)
                       if config.claude_home else ([], []))
        notes += found
        known = {window["window"]: window for window in windows}
        for hit in hits:
            if hit.window in known:  # the snapshot knows this window's percent: the hit only says when it was rejected
                known[hit.window]["last_hit_at"] = _iso(hit.at)
                continue
            windows.append(self._window_view(RateWindow(hit.window, None, hit.resets_at, None, hit.reset_source), now,
                                             last_hit_at=hit.at, detail=hit.detail))
        missing = self._missing_percents(windows)
        if not any(window["resets_at"] for window in windows):
            missing.append("reset_time")
        return self._entry("claude", observed, windows, [*missing, *NOT_AVAILABLE_ALWAYS, "credits_balance", "plan_name"],
                           notes, data=bool(windows))

    def _plan_codex(self, config: SessionsConfig, now: datetime) -> dict[str, Any]:
        observed, found = sources.read_codex_plan(config.codex_home) if config.codex_home else (None, [])
        notes = list(found)
        windows = [self._window_view(window, now) for window in observed.windows] if observed else []
        missing = self._missing_percents(windows)
        if any(window["state"] == "window_reset" for window in windows):
            notes.append("window_reset_since_observation")
        if observed is None or observed.credits is None:
            missing.append("credits_balance")
        if observed is None or observed.plan is None:
            missing.append("plan_name")
        return self._entry("codex", observed, windows, [*missing, *NOT_AVAILABLE_ALWAYS], notes, data=bool(windows),
                           credits=dict(observed.credits) if observed and observed.credits else None)

    def _plan_copilot(self, config: SessionsConfig, now: datetime) -> dict[str, Any]:
        start, reset = sources.month_start(now), sources.next_month(now)
        events, notes = (sources.read_copilot_tokens(config.copilot_home, start, reset)
                         if config.copilot_home else ([], ["source_unavailable: copilot store not configured"]))
        hit, found = sources.read_copilot_limit_hit(config.copilot_home, start) if config.copilot_home else (None, [])
        notes = [*notes, *found]
        nano = [event.nano_aiu for event in events if event.nano_aiu is not None]
        used = round(sum(nano) / sources.NANO_AIU_PER_CREDIT, 6) if nano else None
        cap = self._services.settings.usage_copilot_monthly_credits
        percent = round(used / cap * 100, 2) if cap and used is not None else None
        view = self._window_view(RateWindow(sources.MONTHLY, percent, reset, None, sources.RESET_RULE), now, started=start,
                                 last_hit_at=hit.at if hit else None, detail=hit.detail if hit else None,
                                 assume_current=True)
        data = bool(events) or hit is not None
        if not data and not any(note.startswith("source_unavailable") for note in notes):
            notes.append("no_usage_this_month")
        credits = {"unit": "ai_credit", "used": used, "cap": cap, "cap_source": "configured" if cap else None,
                   "calls": len(events), "month_start": _iso(start), "unit_confidence": "medium"}
        missing = [name for name, absent in (("monthly_percent", percent is None), ("monthly_cap", cap is None)) if absent]
        observed = PlanObservation("copilot", "copilot_usage_events", max((e.at for e in events), default=None))
        entry = self._entry("copilot", observed if events else None, [view], [*missing, *NOT_AVAILABLE_ALWAYS, "plan_name"],
                            notes, data=data, credits=credits)
        if not data and any(note.startswith("source_unavailable") for note in notes):
            entry["credits"] = None  # the database could not be read: no consumption figure, not even a 0
        return entry

    def _plan_antigravity(self, config: SessionsConfig, now: datetime) -> dict[str, Any]:
        """Antigravity: the limits ``agy -p /usage`` prints (percent left and reset, per model group and per model).

        The command spends no model. Any failure (agy missing, a timeout, odd output) is a note, never an error.
        """
        if self._services.settings.mock_mode:
            return self._entry("antigravity", None, [], ["plan_name"],
                               ["source_unavailable: mock mode does not start agy"], data=False)
        cached = self._agy_cache
        if cached is not None and time.monotonic() - cached[0] < AGY_CACHE_SECONDS:
            entry = dict(cached[1])
        else:
            entry = self._read_agy_usage(now)
            self._agy_cache = (time.monotonic(), entry)
        return dict(entry)

    def _read_agy_usage(self, now: datetime) -> dict[str, Any]:
        missing = ["plan_name", "credits_balance"]  # the per-model limits are in the windows
        exe = shutil.which("agy")
        if exe is None:
            return self._entry("antigravity", None, [], missing, ["source_unavailable: agy not found in PATH"], data=False)
        try:
            code, text = self.agy_runner([exe, *AGY_USAGE_ARGS], AGY_TIMEOUT_SECONDS)
        except Exception as exc:  # a runner must never break the report
            return self._entry("antigravity", None, [], missing,
                               [f"source_unavailable: agy /usage failed ({type(exc).__name__})"], data=False)
        if code != 0:
            return self._entry("antigravity", None, [], missing,
                               [f"source_unavailable: agy /usage failed (exit {code})"], data=False)
        limits, notes = sources.parse_agy_usage(text)
        windows = []
        for limit in limits:
            view = self._window_view(limit.to_rate_window(), now)
            view["group"] = limit.name
            windows.append(view)
        if any(w["state"] == "window_reset" for w in windows):
            notes.append("window_reset_since_observation")
        observed = PlanObservation("antigravity", "agy_usage_command", now, windows=tuple(l.to_rate_window() for l in limits))
        return self._entry("antigravity", observed if windows else None, windows, missing, notes, data=bool(windows))

    # --- tokens ---------------------------------------------------------------------

    @service_errors
    def tokens(self, *, since: Any = None, until: Any = None, apps: Any = None,
               group_by: Any = None) -> dict[str, Any]:
        """Tokens per app, model, level and role inside ``[since, until)`` (default: the last 7 days).

        ``apps`` limits the sources (``claude``, ``codex``, ``copilot``, ``framework``; a list or comma
        separated text; default every app the session viewer shows). ``group_by`` picks the dimensions
        among ``app``, ``model``, ``level``, ``role``, ``day`` and ``month`` (UTC; default
        ``app,model,level,role``). Each row has ``events`` and ``input``, ``output``, ``cache_read``,
        ``cache_write``, ``reasoning`` and ``total``; a field no event reported is ``None``. ``apps`` in the
        answer says how each source measured the window and why one gave nothing; ``conventions`` says how
        ``total`` is built for each. Level is the catalog's level of the model (``None`` when unclassified).
        """
        now = self._now()
        start, end = self._window(since, until, now, DEFAULT_TOKEN_DAYS)
        dims = _names(group_by, "group_by", GROUP_DIMENSIONS) or DEFAULT_GROUP_BY
        enabled = self._enabled()
        wanted = _names(apps, "apps", APPS) or enabled
        config = self._config()
        events: list[TokenEvent] = []
        status: dict[str, Any] = {}
        for app in wanted:
            if app not in enabled:
                status[app] = {"status": "disabled", "measurement": MEASUREMENT[app], "events": 0, "warnings": []}
                continue
            found, warnings, extra = self._read_tokens(app, config, start, end)
            events += found
            state = "unavailable" if any(w.startswith("source_unavailable") for w in warnings) else "ok"
            status[app] = {"status": state, "measurement": MEASUREMENT[app], "events": len(found),
                           "warnings": warnings, **extra}
        level_of = self._services.sessions._level_of()  # type: ignore[attr-defined]
        groups: dict[tuple, _Sums] = defaultdict(_Sums)
        total = _Sums()
        for event in events:
            key = tuple(self._dimension(event, dim, level_of) for dim in dims)
            groups[key].add(event)
            total.add(event)
        rows = [{**dict(zip(dims, key)), **sums.view()}
                for key, sums in sorted(groups.items(), key=lambda item: [_sort_key(v) for v in item[0]])]
        return {"window": {"since": _iso(start), "until": _iso(end)}, "group_by": list(dims), "rows": rows,
                "totals": total.view(), "apps": status,
                "conventions": {app: CONVENTIONS[app] for app in wanted}}

    @staticmethod
    def _dimension(event: TokenEvent, dim: str, level_of: Callable[[str | None], str | None]) -> Any:
        if dim == "app":
            return event.app
        if dim == "model":
            return event.model
        if dim == "level":
            return level_of(event.model)
        if dim == "role":
            return event.role
        return event.at.strftime("%Y-%m-%d" if dim == "day" else "%Y-%m")

    def _read_tokens(self, app: str, config: SessionsConfig, start: datetime,
                     end: datetime) -> tuple[list[TokenEvent], list[str], dict[str, Any]]:
        if app == "claude":
            home, reader = config.claude_home, sources.read_claude_tokens
        elif app == "codex":
            home, reader = config.codex_home, sources.read_codex_tokens
        elif app == "copilot":
            home, reader = config.copilot_home, sources.read_copilot_tokens
        elif app == "antigravity":
            return [], ["tokens_not_recorded: agy's token counts carry no time; per-session totals are in multiagent sessions"], {}
        else:
            records = self._telemetry(start, end)
            events = [TokenEvent(app="framework", at=r["at"], model=r["model"] or None, role=sources.ROLE_PRIMARY,
                                 input=r["tokens_in"], output=r["tokens_out"],
                                 cache_read=r["cache_read_tokens"], cache_write=r["cache_write_tokens"],
                                 total=_add(r["tokens_in"], r["tokens_out"]))
                      for r in records if r["tokens_in"] is not None or r["tokens_out"] is not None]
            return events, [], {"records_without_usage": len(records) - len(events)}
        if home is None:
            return [], [f"source_unavailable: {app} store not configured"], {}
        events, warnings = reader(home, start, end)
        return events, warnings, {}

    # --- costs ----------------------------------------------------------------------

    def _telemetry(self, start: datetime | None, end: datetime | None, *, run_ids: Sequence[str] | None = None,
                   project: str | None = None, stores: list[tuple[str | None, Any]] | None = None) -> list[dict[str, Any]]:
        """Telemetry rows of every store created in ``[start, end)``, oldest first, with the cost evidence loaded.

        ``None`` for ``start`` or ``end`` leaves that side open. ``run_ids`` keeps only those runs, ``project`` (a
        name or an id) only the runs of that project and ``stores`` reads only those stores. Each record also carries
        the ``project`` name and ``project_id`` of its run (``None`` when the run is not in the store).
        """
        conditions: list[str] = []
        params: list[Any] = []
        if start is not None:   # a coarse text filter; the exact test is below
            conditions.append("t.created_at >= ?")
            params.append(start.date().isoformat())
        if end is not None:
            conditions.append("t.created_at < ?")
            params.append((end.date() + timedelta(days=1)).isoformat())
        if project is not None:
            conditions.append("(p.name = ? OR p.id = ?)")
            params += [project, project]
        chunks: list[Sequence[str] | None] = [None] if run_ids is None else [
            run_ids[i:i + _RUN_CHUNK] for i in range(0, len(run_ids), _RUN_CHUNK)]
        records: list[dict[str, Any]] = []
        for bundle, store in (self._stores() if stores is None else stores):
            rows: list[Any] = []
            for chunk in chunks:
                where = [*conditions, f"t.run_id IN ({','.join('?' * len(chunk))})"] if chunk is not None else conditions
                with store.db.connect() as conn:
                    rows += conn.execute(
                        "SELECT t.id,t.run_id,t.step_id,t.agent_id,t.provider,t.model,t.tokens_in,t.tokens_out,"
                        "t.cache_read_tokens,t.cache_write_tokens,t.duration_ms,t.estimated_cost_usd,t.cost_basis,t.success,t.created_at,"
                        "p.name AS project,p.id AS project_id FROM telemetry t "
                        "LEFT JOIN runs r ON r.id = t.run_id LEFT JOIN projects p ON p.id = r.project_id"
                        + (f" WHERE {' AND '.join(where)}" if where else "") + " ORDER BY t.id",
                        [*params, *(chunk or ())]).fetchall()
            for row in rows:
                at = sources.parse_instant(row["created_at"])
                if at is None or (start is not None and at < start) or (end is not None and at >= end):
                    continue
                record = {key: row[key] for key in row.keys()}
                reported = record["tokens_in"] is not None and record["tokens_out"] is not None
                record.update(at=at, bundle=bundle, success=bool(record["success"]),
                              cost_basis=load_basis(record["cost_basis"]),
                              tokens_total=record["tokens_in"] + record["tokens_out"] if reported else None)
                records.append(record)
        records.sort(key=lambda record: (record["at"], record["bundle"] or "", record["id"]))
        return records

    @staticmethod
    def _cost_group(records: list[dict[str, Any]], **identity: Any) -> dict[str, Any]:
        reasons: dict[str, int] = {}
        for record in records:
            reason = _unknown_reason(record)
            if reason is not None:
                reasons[reason] = reasons.get(reason, 0) + 1
        return {**identity, **totals_of(records), "cost_unknown_reasons": dict(sorted(reasons.items())),
                "evidence": _evidence(records)}

    @service_errors
    def costs(self, *, since: Any = None, until: Any = None, limit: Any = DEFAULT_RUN_LIMIT) -> dict[str, Any]:
        """Estimated cost of the framework's provider calls inside ``[since, until)`` (default: the last 31 days).

        Per run (most recent first, at most ``limit``), per model, per day and per month (UTC), each with the
        token sums of ``observability.usage`` and its cost fields: ``estimated_cost_usd`` (the USD subtotal
        that is known, ``None`` if none is), ``cost_known_records``, ``cost_unknown_records`` with
        ``cost_unknown_reasons`` (``no_price_evidence``, ``tokens_not_reported``, ``human_step``),
        ``cost_other_currencies`` and ``cost_complete``; show the subtotal together with the unknown count.
        ``evidence`` lists the distinct price evidence (model, currency, source, validity, provenance) the
        group's costs were computed from. Costs are estimates (``cost_is_estimate``), never billed amounts.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RUN_LIMIT:
            raise _invalid(f"limit must be an integer from 1 to {MAX_RUN_LIMIT}", field="limit")
        now = self._now()
        start, end = self._window(since, until, now, DEFAULT_COST_DAYS)
        records = self._telemetry(start, end)

        def grouped(key: Callable[[dict[str, Any]], Any]) -> dict[Any, list[dict[str, Any]]]:
            found: dict[Any, list[dict[str, Any]]] = {}
            for record in records:
                found.setdefault(key(record), []).append(record)
            return found

        runs = grouped(lambda r: (r["bundle"], r["run_id"]))
        by_run = [self._cost_group(group, run_id=run_id, bundle=bundle, first_at=_iso(group[0]["at"]),
                                   last_at=_iso(group[-1]["at"]))
                  for (bundle, run_id), group in sorted(runs.items(), key=lambda item: item[1][0]["at"], reverse=True)]
        return {
            "cost_is_estimate": True,
            "window": {"since": _iso(start), "until": _iso(end)},
            "totals": self._cost_group(records),
            "by_run": by_run[:limit], "runs": len(by_run), "runs_truncated": len(by_run) > limit,
            "by_model": [self._cost_group(group, model=model)
                         for model, group in sorted(grouped(lambda r: r["model"]).items(), key=lambda item: _sort_key(item[0]))],
            "by_day": [self._cost_group(group, day=day)
                       for day, group in sorted(grouped(lambda r: r["at"].strftime("%Y-%m-%d")).items())],
            "by_month": [self._cost_group(group, month=month)
                         for month, group in sorted(grouped(lambda r: r["at"].strftime("%Y-%m")).items())],
        }

    # --- budgets (USO-T03) ----------------------------------------------------------

    @service_errors
    def budgets(self, *, run_id: Any = None) -> dict[str, Any]:
        """Every configured budget with its consumption, percent, state and alerts (read-only).

        ``{generated_at, month, defaults, budgets, status, counts, worst_percent, runs_truncated, recent_alerts}``.
        ``budgets`` is the configuration as understood (``id``, ``scope``, ``project``, ``currency``, ``amount``,
        ``mode``, ``alerts``). ``status`` has one row for a ``month`` or ``project`` budget and, for a ``run``
        budget, one row for each run that spent this month (the most consumed ``MAX_BUDGET_RUNS``, most consumed
        first; ``runs_truncated`` says there are more), or only the row of ``run_id`` when it is given
        (``not_found`` if there is no such run; a run of another project has no row for a budget limited to a
        project). A row has:

        * ``budget_id``, ``scope``, ``project`` (as configured) with its ``project_id`` (taken from the runs counted;
          only for a budget tied to a project), ``run_id`` and ``bundle`` (run rows), ``period`` (``YYYY-MM`` for
          a month budget, ``all`` for a project one) with ``since``/``until`` (month only) and ``currency``;
        * ``amount``, ``used`` (the known cost in that currency, cut to six decimals) with ``used_exact`` (the same sum
          before cutting it: the budget guard decides on it, never on ``used``), ``remaining``, ``percent`` (cut to two decimals)
          and ``state``: ``ok``, ``alert`` (an alert percent was reached) or ``limit_reached`` (100 % or more);
        * ``mode`` (``warn`` or ``cut``; ``cut`` is applied by USO-T04, not here), ``alerts``, ``crossed`` (the alert
          percents reached) and ``next_alert`` (the first one not reached, ``None`` when there is none);
        * ``records`` (provider calls counted), ``cost_known_records``, ``cost_unknown_records`` with
          ``cost_unknown_reasons``, ``cost_other_currencies`` (spent in other currencies, never converted) and
          ``complete`` (no call has an unknown cost and none is priced in another currency): when it is false,
          ``used`` is only the known part.

        ``recent_alerts`` are the latest ``budget.threshold`` events (newest first, at most ``MAX_RECENT_ALERTS``).
        Without budgets everything is empty.
        """
        settings = self._services.settings
        configured = list(settings.usage_budgets)
        now = self._now()
        month_start, month_end = sources.month_start(now), sources.next_month(now)
        month = {"key": month_start.strftime("%Y-%m"), "since": _iso(month_start), "until": _iso(month_end)}
        defaults = {"mode": settings.usage_budget_mode, "alerts": list(settings.usage_budget_alerts)}
        run = self._budget_run(run_id)
        rows, truncated = self._budget_rows(configured, defaults, run, month_start, month_end, month["key"])
        counts = {state: sum(1 for row in rows if row["state"] == state) for state in ("ok", "alert", "limit_reached")}
        return {
            "generated_at": _iso(now), "month": month, "defaults": defaults,
            "budgets": [{"id": b.id, "scope": b.scope, "project": b.project, "currency": b.currency, "amount": b.amount,
                         "mode": b.mode or defaults["mode"],
                         "alerts": list(b.alerts if b.alerts is not None else defaults["alerts"])}
                        for b in configured],
            "status": rows, "counts": counts, "worst_percent": max((row["percent"] for row in rows), default=None),
            "runs_truncated": truncated, "recent_alerts": self._recent_budget_alerts(),
        }

    def _budget_run(self, run_id: Any) -> dict[str, Any] | None:
        """The run a ``budgets`` call is about (store, bundle and project), ``None`` when no run was given."""
        if run_id is None or run_id == "":
            return None
        if not isinstance(run_id, str) or len(run_id) > 128:
            raise _invalid("run_id must be the id of a run", field="run_id")
        runtime = self._services.runs._find(run_id)  # type: ignore[attr-defined]  # not_found when there is no such run
        with runtime.store.db.connect() as conn:
            row = conn.execute("SELECT p.name AS name, p.id AS id FROM runs r JOIN projects p ON p.id = r.project_id "
                               "WHERE r.id = ?", (run_id,)).fetchone()
        return {"run_id": run_id, "bundle": runtime.bundle, "store": runtime.store,
                "project": row["name"] if row else None, "project_id": row["id"] if row else None}

    def _budget_rows(self, configured: Sequence[Any], defaults: dict[str, Any], run: dict[str, Any] | None,
                     month_start: datetime, month_end: datetime, month_key: str) -> tuple[list[dict[str, Any]], bool]:
        """The status rows of the budgets, and whether a run budget showed fewer runs than spent this month."""
        if not configured:
            return [], False
        scopes = {budget.scope for budget in configured}
        in_month = self._telemetry(month_start, month_end) if "month" in scopes or ("run" in scopes and run is None) else []
        rows: list[dict[str, Any]] = []
        truncated = False
        by_project: dict[str, list[dict[str, Any]]] = {}
        runs: list[dict[str, Any]] | None = None
        for budget in configured:
            if budget.scope == "month":
                rows.append(self._budget_row(budget, defaults, [r for r in in_month if _in_project(r, budget.project)],
                                             period=month_key, since=month_start, until=month_end))
            elif budget.scope == "project":
                if budget.project not in by_project:
                    by_project[budget.project] = self._telemetry(None, None, project=budget.project)
                rows.append(self._budget_row(budget, defaults, by_project[budget.project], period="all"))
            else:
                if runs is None:
                    runs, cut = self._budget_runs(run, in_month)
                    truncated = truncated or cut
                found = [self._budget_row(budget, defaults, item["records"], run_id=item["run_id"], bundle=item["bundle"])
                         for item in runs
                         if budget.project is None or budget.project in (item["project"], item["project_id"])]
                found.sort(key=lambda row: (-row["percent"], row["run_id"]))
                truncated = truncated or len(found) > MAX_BUDGET_RUNS
                rows += found[:MAX_BUDGET_RUNS]
        return rows, truncated

    def _budget_runs(self, run: dict[str, Any] | None,
                     in_month: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
        """The runs a run budget looks at, each with every telemetry row it ever wrote.

        The given run, else those active this month (the most recent ``MAX_RUN_LIMIT``; the flag says more were active).
        """
        if run is not None:
            records = self._telemetry(None, None, run_ids=[run["run_id"]], stores=[(run["bundle"], run["store"])])
            return [{"run_id": run["run_id"], "bundle": run["bundle"], "project": run["project"],
                     "project_id": run["project_id"], "records": records}], False
        last: dict[tuple[str | None, str], datetime] = {}
        for record in in_month:   # oldest first, so the last write is the most recent activity
            last[(record["bundle"], record["run_id"])] = record["at"]
        newest = sorted(last, key=lambda key: last[key], reverse=True)[:MAX_RUN_LIMIT]
        grouped: dict[tuple[str | None, str], dict[str, Any]] = {
            key: {"run_id": key[1], "bundle": key[0], "project": None, "project_id": None, "records": []} for key in newest}
        for record in self._telemetry(None, None, run_ids=sorted({key[1] for key in newest})):
            item = grouped.get((record["bundle"], record["run_id"]))
            if item is not None:
                item["records"].append(record)
                item["project"], item["project_id"] = record["project"], record["project_id"]
        return list(grouped.values()), len(last) > len(newest)

    def _budget_row(self, budget: Any, defaults: dict[str, Any], records: list[dict[str, Any]], *, period: str | None = None,
                    since: datetime | None = None, until: datetime | None = None, run_id: str | None = None,
                    bundle: str | None = None) -> dict[str, Any]:
        """One budget measured on ``records``: only costs in the budget's currency are added up."""
        used, known = 0.0, 0
        other: dict[str, float] = {}
        reasons: dict[str, int] = {}
        for record in records:
            spent = _spent(record)
            if spent is None:
                reason = _unknown_reason(record) or "no_price_evidence"
                reasons[reason] = reasons.get(reason, 0) + 1
            elif spent[0] == budget.currency:
                used += spent[1]
                known += 1
            else:
                other[spent[0]] = other.get(spent[0], 0.0) + spent[1]
        exact_used = used           # what the budget guard compares; ``used`` below is cut to six decimals for display
        used = round(used, 6)
        percent = used / budget.amount * 100
        alerts = list(budget.alerts if budget.alerts is not None else defaults["alerts"])
        crossed = [alert for alert in alerts if percent >= alert - _PERCENT_EPS]
        unknown = sum(reasons.values())
        state = "limit_reached" if percent >= 100 - _PERCENT_EPS else ("alert" if crossed else "ok")
        return {
            "budget_id": budget.id, "scope": budget.scope, "project": budget.project,
            "project_id": _project_id_of(records) if budget.project is not None else None,
            "run_id": run_id, "bundle": bundle, "period": period, "since": _iso(since), "until": _iso(until), "currency": budget.currency,
            "amount": budget.amount, "used": used, "used_exact": exact_used,
            "remaining": round(max(0.0, budget.amount - used), 6),
            "percent": _percent_text(percent), "state": state,
            "mode": budget.mode or defaults["mode"], "alerts": alerts, "crossed": crossed,
            "next_alert": next((alert for alert in alerts if alert not in crossed), None),
            "records": len(records), "cost_known_records": known, "cost_unknown_records": unknown,
            "cost_unknown_reasons": dict(sorted(reasons.items())),
            "cost_other_currencies": {currency: round(total, 6) for currency, total in sorted(other.items())},
            "complete": unknown == 0 and not other,
        }

    def _recent_budget_alerts(self) -> list[dict[str, Any]]:
        """The latest ``budget.threshold`` events, newest first."""
        with self._services.tasks.store.db.connect() as conn:
            rows = conn.execute("SELECT seq,created_at,payload_json FROM team_events WHERE type = ? "
                                "ORDER BY seq DESC LIMIT ?", (BUDGET_EVENT, MAX_RECENT_ALERTS)).fetchall()
        alerts = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except ValueError:
                payload = {}
            alerts.append({**(payload if isinstance(payload, dict) else {}), "seq": row["seq"],
                           "at": _iso(sources.parse_instant(row["created_at"]))})
        return alerts

    @staticmethod
    def _alert_key(row: dict[str, Any], threshold: float) -> str:
        """What makes an alert the same alert: budget, percent, period (or run), amount, currency and project.

        Numbers are written in full (``_number``): two amounts that differ at the seventh digit are different keys.
        The project goes last because it is free text.
        """
        subject = f"run={row['run_id']}" if row["scope"] == "run" else row["period"]
        return (f"budget:{row['budget_id']}:{_number(threshold)}:{subject}:{_number(row['amount'])}{row['currency']}"
                f":{row['project'] or '*'}")

    @service_errors
    def notify_budgets(self, *, run_id: Any = None) -> dict[str, Any]:
        """``budgets()`` after announcing the alerts reached: one ``budget.threshold`` team event for each one.

        An alert is announced once per (budget, alert percent, period, amount, currency and project); a run budget's
        period is the run. The event's ``cause`` holds that key, its ``ref`` is ``project:<project id>`` (the id the
        project's runs have, which is not always its name) for a budget tied to a project, so it shows in the
        project's events, and ``budget:<id>`` otherwise. Its payload says the budget, scope, ``threshold``,
        ``period``, ``run_id``, ``project``, ``project_id``, ``currency``, ``amount``, ``used``, ``percent``,
        ``state``, ``mode`` and ``complete``. Calling it again announces nothing new, from any process.
        The answer is that of ``budgets()`` plus ``emitted`` (``seq``, ``budget_id``, ``threshold``, ``period``,
        ``run_id`` of each new event). Nothing is ever stopped here: ``mode: cut`` is for USO-T04.
        """
        status = self.budgets(run_id=run_id)
        store = self._services.tasks.store
        emitted: list[dict[str, Any]] = []
        for row in status["status"]:
            for threshold in row["crossed"]:
                key = self._alert_key(row, threshold)
                if key in self._announced:
                    continue
                payload = {"budget": row["budget_id"], "scope": row["scope"], "threshold": threshold,
                           "period": row["period"], "run_id": row["run_id"], "project": row["project"],
                           "project_id": row["project_id"], "currency": row["currency"], "amount": row["amount"], "used": row["used"],
                           "percent": row["percent"], "state": row["state"], "mode": row["mode"],
                           "complete": row["complete"]}
                with store.transaction() as conn:   # the check and the insert are one write: no duplicate from a race
                    if conn.execute("SELECT 1 FROM team_events WHERE type = ? AND cause = ? LIMIT 1",
                                    (BUDGET_EVENT, key)).fetchone() is None:
                        seq = store.append_event(
                            conn, BUDGET_EVENT, actor=BUDGET_ACTOR, cause=key, payload=payload,
                            ref=f"project:{row['project_id']}" if row["project_id"] else f"budget:{row['budget_id']}")
                        emitted.append({"seq": seq, "budget_id": row["budget_id"], "threshold": threshold,
                                        "period": row["period"], "run_id": row["run_id"]})
                self._announced.add(key)
        if emitted:
            status["recent_alerts"] = self._recent_budget_alerts()
        return {**status, "emitted": emitted}
