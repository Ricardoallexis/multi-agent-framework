# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Usage services (USO-T02): plan usage per app, tokens per app/model/level/role and estimated API cost.

Read-only and neutral of domain. Three questions, one method each, with the same answer from Python, the
token-protected ``/gui/api/usage/*`` routes, ``/api/v1/usage/costs`` and ``multiagent usage-report``:

* ``plans()``: what each app's plan allows and how much is used, **only where the app leaves the number on
  disk or documents it** (USO-T01). Codex: the percent and reset of its 5 hour and weekly windows. Claude:
  the reset instant after a limit was hit, and the percent only if the operator saved a ``statusLine``
  snapshot (``usage_claude_statusline_file``). Copilot: AI credits used this month, the monthly reset (a
  documented rule) and a percent only if the operator set ``usage_copilot_monthly_credits``. Everything
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

Paths come from the settings the session viewer uses (``gui_claude_home``, ``gui_codex_home``,
``gui_copilot_home``) and ``gui_session_sources`` says which apps are shown. No working folder, title,
session id, prompt or credential file is read or returned. Errors (``ServiceError``): ``invalid_request``
(422) for a bad ``since``/``until``/``apps``/``group_by``/``limit``.
"""
from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..observability.usage import load_basis, totals_of
from ..sessions import SessionsConfig
from ..sessions._common import normalize_ts
from ..sessions import usage_sources as sources
from ..sessions.usage_sources import PlanObservation, RateWindow, TokenEvent
from .errors import INVALID_REQUEST, ServiceError, service_errors

if TYPE_CHECKING:
    from . import ApplicationServices

APPS = ("claude", "codex", "copilot", "framework")
GROUP_DIMENSIONS = ("app", "model", "level", "role", "day", "month")
DEFAULT_GROUP_BY = ("app", "model", "level", "role")
DEFAULT_TOKEN_DAYS = 7
DEFAULT_COST_DAYS = 31
MAX_WINDOW_DAYS = 400
DEFAULT_RUN_LIMIT = 100
MAX_RUN_LIMIT = 500
MAX_EVIDENCE = 20
LIMIT_LOOKBACK = timedelta(days=8)   # a plan reset is never believed to be farther away than this (CNT-T01)
TOKEN_FIELDS = ("input", "output", "cache_read", "cache_write", "reasoning", "total")

# How each source measures a window (so a reader of the numbers knows how exact they are).
MEASUREMENT = {
    "claude": "per_message",       # one record per assistant message, deduplicated by message id
    "codex": "counter_growth",     # growth of the session's cumulative counters inside the window
    "copilot": "per_call",         # one row per call in the usage database
    "framework": "per_call",       # one telemetry row per provider call
}
CONVENTIONS = {
    "claude": "total = input + output + cache_read + cache_write (input excludes the cache)",
    "codex": "total = the reported total_tokens (its input already counts the cached part)",
    "copilot": "total = input + output (cache and reasoning are reported apart)",
    "framework": "total = input + output",
}
RESET_CONFIDENCE = {sources.RESET_APP_EPOCH: "high", sources.RESET_RULE: "medium", sources.RESET_NONE: "none"}
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
                              copilot_home=settings.gui_copilot_home or home / ".copilot")

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
        for app, build in (("claude", self._plan_claude), ("codex", self._plan_codex), ("copilot", self._plan_copilot)):
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
        else:
            records = self._telemetry(start, end)
            events = [TokenEvent(app="framework", at=r["at"], model=r["model"] or None, role=sources.ROLE_PRIMARY,
                                 input=r["tokens_in"], output=r["tokens_out"],
                                 total=_add(r["tokens_in"], r["tokens_out"]))
                      for r in records if r["tokens_in"] is not None or r["tokens_out"] is not None]
            return events, [], {"records_without_usage": len(records) - len(events)}
        if home is None:
            return [], [f"source_unavailable: {app} store not configured"], {}
        events, warnings = reader(home, start, end)
        return events, warnings, {}

    # --- costs ----------------------------------------------------------------------

    def _telemetry(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Telemetry rows of every store created in ``[start, end)``, oldest first, with the cost evidence loaded."""
        low = start.date().isoformat()
        high = (end.date() + timedelta(days=1)).isoformat()   # a coarse text filter; the exact test is below
        records: list[dict[str, Any]] = []
        for bundle, store in self._stores():
            with store.db.connect() as conn:
                rows = conn.execute(
                    "SELECT id,run_id,step_id,agent_id,provider,model,tokens_in,tokens_out,duration_ms,"
                    "estimated_cost_usd,cost_basis,success,created_at FROM telemetry "
                    "WHERE created_at >= ? AND created_at < ? ORDER BY id", (low, high)).fetchall()
            for row in rows:
                at = sources.parse_instant(row["created_at"])
                if at is None or not start <= at < end:
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
