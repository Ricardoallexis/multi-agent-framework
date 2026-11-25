# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Session viewer services (GUI-T04): one read-only, unified list of work sessions.

Wraps ``multiagent.sessions`` (Claude, Codex, Copilot and Antigravity readers, GUI-T03) and maps the framework's own
runs to the same ``SessionSummary``/``SessionItem`` model with ``source = "framework"``, so every
interface (the token-protected ``/gui/api/sessions`` routes, ``multiagent sessions`` and Python) sees
the same data. Nothing is written or stored: the readers open the apps' files read-only.

Configuration comes from ``Settings``: ``gui_session_roots`` (operator-approved folders whose sessions may be shown)
plus ``executor_workdir_root`` (framework-created executor folders), ``gui_session_sources`` and the four store
overrides ``gui_claude_home``, ``gui_codex_home``, ``gui_copilot_home``, ``gui_antigravity_home`` and ``gui_claude_desktop_dir``.

Cursors are opaque to callers. File sessions use the reader's ``"0:<offset>"``; a framework run uses
the id of its last ``run_events`` row (the same number the GUI event stream sends as ``id:``), so a
client can read items and then follow the live stream from the last cursor it holds.

Children (GUI2-T04): ``list_children`` gives the subagents, spawned threads and workers of one session in a
single model (Claude ``subagents/``, Codex child threads, Copilot ``subagent.*`` events and the steps of a
framework run), with status derived from recent activity; see ``sessions.children``. It never returns message
text, and ``version`` changes whenever a child appears or changes status (the GUI event stream sends it).

Errors (``ServiceError``): ``invalid_session_id`` (400), ``invalid_cursor`` (400), ``invalid_request``
(422, bad filter or limit) and ``not_found`` (404, also for a session outside the allowed folders).
The framework's runs are not tied to a folder (contract section 3), so ``get_session``/``read_items``
of a ``framework:<run_id>`` id do not depend on ``gui_session_roots``. Listing framework runs still
requires an operator root; the dedicated executor root only admits file sessions.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ..config import SESSION_SOURCES
from ..sessions import (
    ChildAgent,
    ChildStatus,
    InvalidCursor,
    InvalidSessionId,
    ItemsPage,
    Kind,
    OpenInApp,
    SessionFilters,
    SessionItem,
    SessionNotFound,
    SessionRegistry,
    SessionsConfig,
    SessionSummary,
    Source,
    Status,
    Surface,
    TokenUsage,
    decode_cursor,
)
from ..sessions._common import clip, normalize_ts
from ..sessions.children import GROUPS, LevelOf, derive_status, group_of, sort_children
from .errors import INVALID_CURSOR, INVALID_REQUEST, INVALID_SESSION_ID, NOT_FOUND, ServiceError

if TYPE_CHECKING:
    from . import ApplicationServices

MAX_LIMIT = 500
DEFAULT_LIST_LIMIT = 100
DEFAULT_ITEMS_LIMIT = 200
MAX_FRAMEWORK_RUNS = 2000  # most recent runs considered for the unified list
ROOTS_HINT = ("configure_roots: set gui_session_roots (environment GUI_SESSION_ROOTS, a JSON list or folders "
              "separated by ';') to the folders whose sessions may be shown")

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_EXTERNAL_ID = re.compile(rf"(?P<source>claude|codex|copilot|antigravity):(?P<native>{_UUID})(?P<agent>:agent-[A-Za-z0-9_-]{{1,64}})?")
_FRAMEWORK_ID = re.compile(r"framework:(?P<native>[A-Za-z0-9_-]{1,64})")
_INT_CURSOR = re.compile(r"[0-9]{1,15}")
_RUN_STATUS = {
    "queued": Status.ACTIVE, "running": Status.ACTIVE, "waiting_human": Status.ACTIVE,
    "paused": Status.IDLE, "interrupted": Status.IDLE, "completed": Status.IDLE, "failed": Status.IDLE,
    "cancelled": Status.IDLE, "rejected": Status.IDLE,
}
FRAMEWORK_READER = "framework-runs/1"
_STEP_EVENTS = ("step_started", "step_completed", "step_skipped", "human_step_requested", "human_step_submitted",
                "model_selected")
_RUN_ENDED_BADLY = frozenset({"failed", "cancelled", "rejected"})


def _invalid(message: str, **details: Any) -> ServiceError:
    return ServiceError(INVALID_REQUEST, message, status=422, details=details)


@contextmanager
def _session_errors() -> Iterator[None]:
    """Reader exceptions leave as ``ServiceError`` with the codes of the module docstring."""
    try:
        yield
    except InvalidCursor as exc:
        raise ServiceError(INVALID_CURSOR, str(exc), status=400) from exc
    except InvalidSessionId as exc:
        raise ServiceError(INVALID_SESSION_ID, str(exc), status=400) from exc
    except SessionNotFound as exc:
        raise ServiceError(NOT_FOUND, f"Session not found: {exc}", status=404) from exc


def _parse_id(session_id: object) -> tuple[str, str]:
    """``(source, native)`` of a well-formed id, else ``invalid_session_id``."""
    if isinstance(session_id, str) and len(session_id) <= 200:
        match = _EXTERNAL_ID.fullmatch(session_id)
        if match and (match["agent"] is None or match["source"] == "claude"):
            return match["source"], session_id.split(":", 1)[1]
        match = _FRAMEWORK_ID.fullmatch(session_id)
        if match:
            return "framework", match["native"]
    raise ServiceError(INVALID_SESSION_ID, "Session id must look like '<source>:<id>' with a valid id", status=400)


def _check_limit(limit: object) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise _invalid(f"limit must be an integer from 1 to {MAX_LIMIT}", field="limit")
    return limit


def _framework_cursor(after: object) -> int:
    if after is None or after == "":
        return 0
    if not isinstance(after, str) or _INT_CURSOR.fullmatch(after) is None:
        raise ServiceError(INVALID_CURSOR, "Framework cursor must be a non-negative integer", status=400)
    return int(after)


def _file_cursor(after: object) -> str | None:
    if after is None or after == "":
        return None
    with _session_errors():
        decode_cursor(after if isinstance(after, str) else "?", 1)
    return after  # type: ignore[return-value]


def _add(a: int | None, b: int | None) -> int | None:
    return b if a is None else (a if b is None else a + b)


def _offset(cursor: str | None) -> int:
    return decode_cursor(cursor, 1)[1]


@dataclass(frozen=True)
class Follow:
    """A validated subscription to one session's growth (see ``SessionServices.open_follow``)."""

    session_id: str
    cursor: str                  # file cursor ("0:<offset>") or the decimal run_events id
    run_id: str | None = None    # set for framework sessions
    store: Any = None            # store that holds the run (framework sessions only)

    @property
    def is_framework(self) -> bool:
        return self.run_id is not None


class SessionServices:
    def __init__(self, services: ApplicationServices):
        self._services = services
        # Guards only the creation of the registry. File reads never run under it (the registry and its
        # metadata index have their own short locks), so a slow listing cannot block the live stream's polls.
        self._lock = threading.RLock()
        self._registry: SessionRegistry | None = None
        self._registry_key: tuple | None = None

    # --- configuration ----------------------------------------------------------

    def _settings(self):
        return self._services.system.settings

    def _sources(self) -> tuple[str, ...]:
        return tuple(self._settings().gui_session_sources)

    def _roots(self) -> tuple[str, ...]:
        settings = self._settings()
        executor_roots = (str(settings.executor_workdir_root),) if settings.executor_workdir_root.is_dir() else ()
        return tuple(dict.fromkeys((*settings.gui_session_roots, *executor_roots)))

    def _has_viewer_roots(self) -> bool:
        settings = self._settings()
        return bool(settings.gui_session_roots) or settings.executor_workdir_root.is_dir()

    def _get_registry(self) -> SessionRegistry:
        s = self._settings()
        overrides = {name: getattr(s, f"gui_{name}") for name in
                     ("claude_home", "codex_home", "copilot_home", "antigravity_home", "claude_desktop_dir")
                     if getattr(s, f"gui_{name}") is not None}
        external = tuple(x for x in self._sources() if x != "framework")
        key = (self._roots(), external, tuple(sorted((k, str(v)) for k, v in overrides.items())))
        with self._lock:
            if self._registry is None or key != self._registry_key:
                config = SessionsConfig.defaults(allowed_roots=self._roots(), sources=external, **overrides)
                self._registry, self._registry_key = SessionRegistry(config), key
            return self._registry

    # --- framework runs -----------------------------------------------------------

    def _stores(self) -> list[tuple[str | None, Any]]:
        """``(bundle, store)`` of the system and of every bundle that has runs."""
        return [(rt.bundle, rt.store) for rt in self._services.runs._runtimes()]  # type: ignore[attr-defined]

    def _framework_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for _, store in self._stores():
            with store.db.connect() as conn:
                found = conn.execute(
                    "SELECT id,workflow_id,status,created_at,updated_at,task_id FROM runs ORDER BY created_at DESC LIMIT ?",
                    (MAX_FRAMEWORK_RUNS,)).fetchall()
            rows.extend(dict(r) | {"_store": store} for r in found)
        return rows

    def _find_run(self, run_id: str) -> tuple[Any, dict[str, Any]]:
        for _, store in self._stores():
            with store.db.connect() as conn:
                row = conn.execute("SELECT id,workflow_id,status,created_at,updated_at,task_id FROM runs WHERE id=?",
                                   (run_id,)).fetchone()
            if row is not None:
                return store, dict(row)
        raise ServiceError(NOT_FOUND, f"Session not found: framework:{run_id}", status=404)

    @staticmethod
    def _usage_by_run(store: Any, run_ids: list[str]) -> dict[str, tuple[list[str], TokenUsage | None]]:
        """Models (in order of first use) and summed tokens per run; unreported tokens stay ``None``."""
        out: dict[str, tuple[list[str], TokenUsage | None]] = {}
        with store.db.connect() as conn:
            for start in range(0, len(run_ids), 500):
                chunk = run_ids[start:start + 500]
                marks = ",".join("?" * len(chunk))
                rows = conn.execute(
                    f"SELECT run_id,model,SUM(tokens_in) AS tin,SUM(tokens_out) AS tout FROM telemetry "
                    f"WHERE run_id IN ({marks}) GROUP BY run_id,model ORDER BY MIN(id)", chunk).fetchall()
                for row in rows:
                    models, tokens = out.get(row["run_id"], ([], None))
                    if row["model"] and row["model"] not in models:
                        models.append(row["model"])
                    if row["tin"] is not None or row["tout"] is not None:
                        before = tokens or TokenUsage()
                        tokens = TokenUsage(input=_add(before.input, row["tin"]), output=_add(before.output, row["tout"]))
                    out[row["run_id"]] = (models, tokens)
        return out

    @staticmethod
    def _framework_summary(row: dict[str, Any], usage: tuple[list[str], TokenUsage | None] | None = None) -> SessionSummary:
        models, tokens = usage or ([], None)
        return SessionSummary(
            id=f"framework:{row['id']}", source=Source.FRAMEWORK, surface=Surface.FRAMEWORK, native_id=row["id"],
            title=row["workflow_id"], cwd=None, created_at=normalize_ts(row["created_at"]),
            updated_at=normalize_ts(row["updated_at"]), models=list(models),
            status=_RUN_STATUS.get(row["status"], Status.UNKNOWN), tokens=tokens,
            open_in_app=OpenInApp("url", f"/gui/#run={row['id']}", False), reader=FRAMEWORK_READER,
            task_ids=[row["task_id"]] if row.get("task_id") else [])

    def _framework_matches(self, filters: SessionFilters) -> list[tuple[SessionSummary, Any]]:
        """``(summary, store)`` of the runs matching the filters, without usage (added for the page only)."""
        if filters.surface not in (None, "framework") or filters.cwd:  # runs have no working folder
            return []
        since = normalize_ts(filters.since) if filters.since else None
        until = normalize_ts(filters.until) if filters.until else None
        keep = []
        for row in self._framework_rows():
            summary = self._framework_summary(row)
            if since and (summary.updated_at or "") < since:
                continue
            if until and (summary.updated_at or "") > until:
                continue
            if filters.text and filters.text.lower() not in (summary.title or "").lower():
                continue
            keep.append((summary, row["_store"]))
        return keep

    def _framework_store(self, run_id: str) -> Any:
        if "framework" not in self._sources():
            raise ServiceError(NOT_FOUND, f"Session not found: framework:{run_id}", status=404)
        return self._find_run(run_id)[0]

    def _with_usage(self, summaries: list[SessionSummary], stores: dict[str, Any]) -> None:
        """Fill models and tokens of framework summaries in place (one query per store)."""
        by_store: dict[int, tuple[Any, list[SessionSummary]]] = {}
        for summary in summaries:
            if summary.source is Source.FRAMEWORK:
                store = stores[summary.native_id]
                by_store.setdefault(id(store), (store, []))[1].append(summary)
        for store, group in by_store.values():
            usage = self._usage_by_run(store, [s.native_id for s in group])
            for summary in group:
                if summary.native_id in usage:
                    summary.models, summary.tokens = usage[summary.native_id]

    def _with_task_ids(self, summaries: list[SessionSummary]) -> None:
        """Fill ``task_ids`` of the chats of other apps from their links to tasks (a run's own comes from the run)."""
        external = [s for s in summaries if s.source is not Source.FRAMEWORK]
        if not external:
            return
        linked = self._services.projects.store.task_ids_by_session([s.id for s in external])
        for summary in external:
            summary.task_ids = list(linked.get(summary.id, []))

    def _framework_event_items(self, store: Any, run_id: str, after: int, limit: int) -> ItemsPage:
        from ..gui.runs import _clean  # lazy: gui imports this module; same cleaning as the run view

        with store.db.connect() as conn:
            rows = conn.execute(
                "SELECT id,ts,event_type,payload_json FROM run_events WHERE run_id=? AND id>? ORDER BY id LIMIT ?",
                (run_id, after, limit + 1)).fetchall()
        items = []
        for row in rows[:limit]:
            try:
                payload = json.loads(row["payload_json"])
            except ValueError:
                payload = {}
            payload = _clean(payload)  # no *_path keys (local paths), bounded text
            body = json.dumps(payload, ensure_ascii=False, sort_keys=True) if payload else ""
            text, truncated = clip(f"{row['event_type']} {body}".strip())
            model = payload.get("model") if isinstance(payload, dict) and isinstance(payload.get("model"), str) else None
            items.append(SessionItem(Kind.SYSTEM, cursor=str(row["id"]), timestamp=normalize_ts(row["ts"]),
                                     model=model, text=text, truncated=truncated))
        done = len(rows) <= limit
        return ItemsPage(items, items[-1].cursor if items else str(after), done)

    # --- public: list / get / items ----------------------------------------------

    def list_sessions(self, *, source: str | None = None, surface: str | None = None, cwd: str | None = None,
                      since: str | None = None, until: str | None = None, text: str | None = None,
                      limit: int = DEFAULT_LIST_LIMIT, after: str | None = None) -> dict[str, Any]:
        """One page of sessions, newest first: ``{sessions, next_cursor, warnings, hint}``.

        Filters: ``source`` (claude, codex, copilot, antigravity, framework), ``surface`` (desktop, cli, framework,
        unknown), ``cwd`` (only sessions whose folder is under it), ``since``/``until`` (ISO-8601,
        compared with ``updated_at``) and ``text`` (part of the title). Without operator roots or a created
        executor root, the page is empty and ``hint`` says how to configure roots (``None`` otherwise).
        """
        # An empty value (a blank form field, ``?since=``) means "no filter".
        source, surface, cwd, since, until, text, after = (
            None if value == "" else value for value in (source, surface, cwd, since, until, text, after))
        filters = self._check_filters(source, surface, cwd, since, until, text, limit, after)
        with _session_errors():
            _, start = decode_cursor(after, 1)
        if not self._has_viewer_roots():
            return {"sessions": [], "next_cursor": None, "hint": ROOTS_HINT,
                    "warnings": ["no_allowed_roots: configure gui_session_roots to list sessions"]}
        warnings: list[str] = []
        rows: list[SessionSummary] = []
        run_stores: dict[str, Any] = {}
        if source != "framework":
            with _session_errors():  # no lock held: the registry reads files, and the stream polls this service
                found, found_warnings = self._get_registry().find_sessions(filters)
            rows += found
            warnings += [w for w in found_warnings if w not in warnings]
        if (source in (None, "framework") and "framework" in self._sources()
                and self._settings().gui_session_roots):
            for summary, store in self._framework_matches(filters):
                rows.append(summary)
                run_stores[summary.native_id] = store
        rows.sort(key=lambda s: (s.updated_at or "", s.id), reverse=True)
        page_rows = [replace(s) for s in rows[start:start + limit]]
        self._with_usage(page_rows, run_stores)
        self._with_task_ids(page_rows)
        nxt = f"0:{start + limit}" if start + limit < len(rows) else None
        return {"sessions": [s.to_dict() for s in page_rows], "next_cursor": nxt, "warnings": warnings, "hint": None}

    def counts(self) -> dict[str, Any]:
        """Counts by source and status of the same allowed sessions the list shows: ``{counts, hint}``.

        ``counts[source]`` is ``{total, active, idle, archived, unknown}`` for every known source (zeros when
        it is off or empty). Without operator roots or a created executor root, everything is zero and
        ``hint`` says why.
        """
        counts = {source: {"total": 0, "active": 0, "idle": 0, "archived": 0, "unknown": 0}
                  for source in SESSION_SOURCES}
        if not self._has_viewer_roots():
            return {"counts": counts, "hint": ROOTS_HINT}
        with _session_errors():
            for source, row in self._get_registry().count_sessions().items():
                counts[source].update(row)
        if "framework" in self._sources() and self._settings().gui_session_roots:
            for summary, _ in self._framework_matches(SessionFilters(source="framework")):
                row = counts["framework"]
                row["total"] += 1
                row[summary.status.value if summary.status.value in row else "unknown"] += 1
        return {"counts": counts, "hint": None}

    def get_session(self, session_id: str) -> dict[str, Any]:
        """Summary of one session. ``not_found`` also covers a session outside the allowed folders."""
        return self._get_summary(session_id).to_dict()

    def _get_summary(self, session_id: str) -> SessionSummary:
        source, native = _parse_id(session_id)
        if source == "framework":
            if "framework" not in self._sources():
                raise ServiceError(NOT_FOUND, f"Session not found: {session_id}", status=404)
            store, row = self._find_run(native)
            return self._framework_summary(row, self._usage_by_run(store, [native]).get(native))
        with _session_errors():
            summary = replace(self._get_registry().get_session(session_id))
        self._with_task_ids([summary])
        return summary

    def activity(self, session_id: str) -> dict[str, Any]:
        """Minimal activity metadata for a session the configured registry allows."""
        source, _ = _parse_id(session_id)
        if source == "framework":
            raise ServiceError(NOT_FOUND, f"Session not found: {session_id}", status=404)
        with _session_errors():
            return self._get_registry().activity(session_id)
    def read_items(self, session_id: str, *, after: str | None = None, limit: int = DEFAULT_ITEMS_LIMIT) -> dict[str, Any]:
        """A page of items after the cursor: ``{items, next_cursor, done, warnings}``.

        ``next_cursor`` is the ``after`` of the following page; ``done`` is true when the reader reached
        the current end (a live session may grow: ask again with ``next_cursor``).
        """
        source, native = _parse_id(session_id)
        _check_limit(limit)
        if source == "framework":
            seq = _framework_cursor(after)
            return self._framework_event_items(self._framework_store(native), native, seq, limit).to_dict()
        _file_cursor(after)
        with _session_errors():
            return self._get_registry().read_items(session_id, after, limit).to_dict()

    # --- children: subagents, spawned threads and workers (GUI2-T04) ---------------

    def _level_of(self) -> LevelOf:
        """Model name or catalog id -> level (T1..T4) when the catalog classifies it, else ``None``."""
        models = getattr(getattr(self._services.system, "catalog", None), "models", None) or {}
        table: dict[str, str] = {}
        for model_id, spec in models.items():
            level = getattr(spec, "level", None)
            if not level:
                continue
            table[str(model_id).casefold()] = level
            name = getattr(spec, "model", None)
            if isinstance(name, str) and name:
                table.setdefault(name.casefold(), level)

        def level_of(model: str | None) -> str | None:
            return table.get(model.casefold()) if isinstance(model, str) and model else None

        return level_of

    @staticmethod
    def _framework_children(store: Any, run_id: str, now: datetime, level_of: LevelOf) -> list[ChildAgent]:
        """Each step of a run that started (skipped ones never ran) as a child, from events and telemetry."""
        marks = ",".join("?" * len(_STEP_EVENTS))
        with store.db.connect() as conn:
            run = conn.execute("SELECT status,waiting_step FROM runs WHERE id=?", (run_id,)).fetchone()
            events = conn.execute(
                f"SELECT ts,event_type,payload_json FROM run_events WHERE run_id=? AND event_type IN ({marks}) "
                "ORDER BY id", (run_id, *_STEP_EVENTS)).fetchall()
            usage = conn.execute(
                "SELECT step_id,model,SUM(tokens_in) AS tin,SUM(tokens_out) AS tout FROM telemetry "
                "WHERE run_id=? GROUP BY step_id,model ORDER BY MIN(id)", (run_id,)).fetchall()
            catalog_ids = conn.execute(
                "SELECT step_id,model_id FROM run_steps WHERE run_id=? AND model_id<>'' ORDER BY attempt",
                (run_id,)).fetchall()
        steps: dict[str, dict[str, Any]] = {}
        for event in events:
            try:
                payload = json.loads(event["payload_json"])
            except ValueError:
                continue
            step_id = payload.get("step_id") if isinstance(payload, dict) else None
            if not isinstance(step_id, str) or not step_id:
                continue
            step = steps.setdefault(step_id, {"state": "open", "started": None, "last": None, "model": None})
            ts, kind = normalize_ts(event["ts"]), event["event_type"]
            step["last"] = ts or step["last"]
            if kind in ("step_started", "human_step_requested"):
                step["state"] = "open"
                step["started"] = step["started"] or ts
            elif kind == "step_completed":
                step["state"] = "done"
            elif kind == "step_skipped":
                step["state"] = "skipped"
            if kind in ("model_selected", "human_step_submitted") and isinstance(payload.get("model"), str):
                step["model"] = payload["model"] or step["model"]
        tokens: dict[str, TokenUsage | None] = {}
        models: dict[str, str] = {}
        for row in usage:
            step_id = row["step_id"]
            if row["model"]:
                models.setdefault(step_id, row["model"])
            if row["tin"] is not None or row["tout"] is not None:
                before = tokens.get(step_id) or TokenUsage()
                tokens[step_id] = TokenUsage(input=_add(before.input, row["tin"]),
                                             output=_add(before.output, row["tout"]))
        catalog_of = {row["step_id"]: row["model_id"] for row in catalog_ids}
        run_status, waiting_step = (run["status"], run["waiting_step"]) if run is not None else ("", "")
        children: list[ChildAgent] = []
        for step_id, step in steps.items():
            if step["state"] == "skipped":
                continue
            note = None
            if step["state"] == "done" or run_status == "completed":
                status = ChildStatus.DONE
            elif run_status in _RUN_ENDED_BADLY:
                status = derive_status(now=now, last_activity=step["last"], failed=True)
            elif run_status == "waiting_human" and waiting_step == step_id:
                status, note = ChildStatus.IDLE, "waiting_human"
            else:
                status = derive_status(now=now, last_activity=step["last"])
            model = models.get(step_id) or step["model"] or catalog_of.get(step_id)
            children.append(ChildAgent(
                id=f"framework:{run_id}#{step_id}", source="framework", kind="step", name=step_id, label=None,
                model=model, level=level_of(model) or level_of(catalog_of.get(step_id)), status=status, note=note,
                started_at=step["started"], last_activity_at=step["last"], tokens=tokens.get(step_id),
                link_id=None))
        return children

    def list_children(self, session_id: str, *, status: str | None = None,
                      now: datetime | None = None) -> dict[str, Any]:
        """Subagents, spawned threads and workers of one session, most recent activity first.

        ``{session_id, children, counts, version, warnings}``. ``status`` filters the list: ``active``
        (running, testing, idle) or ``done`` (done, stopped, unknown); ``counts`` always covers all the
        children so both tabs can show their number. Each child has ``id``, ``source``, ``kind``, ``name``,
        ``label``, ``model``, ``level`` (T1..T4 when the catalog maps the model), ``status``, ``note``,
        ``started_at``, ``last_activity_at``, ``tokens`` (``None`` = unknown) and ``link_id`` (the session that
        opens its conversation, ``None`` when it has none). No message text is ever returned. Errors:
        ``invalid_session_id``, ``not_found`` and ``invalid_request`` (a ``status`` that is not active/done).
        """
        return self._children(session_id, status, now, check=True)

    def poll_children(self, session_id: str) -> dict[str, Any]:
        """``list_children`` for a session the caller already validated (the GUI stream polls it every few seconds)."""
        return self._children(session_id, None, None, check=False)

    def _children(self, session_id: str, status: str | None, now: datetime | None, *, check: bool) -> dict[str, Any]:
        source, native = _parse_id(session_id)
        if status in (None, ""):
            group = None
        elif status in GROUPS:
            group = status
        else:
            raise _invalid("status must be 'active' or 'done'", field="status")
        when = now if isinstance(now, datetime) else datetime.now(UTC)
        level_of = self._level_of()
        warnings: list[str] = []
        if source == "framework":
            if "framework" not in self._sources():
                raise ServiceError(NOT_FOUND, f"Session not found: {session_id}", status=404)
            store = self._find_run(native)[0]
            children = sort_children(self._framework_children(store, native, when, level_of))
        else:
            with _session_errors():
                children, warnings = self._get_registry().list_children(
                    session_id, now=when, level_of=level_of, check=check)
        counts = {name: sum(1 for c in children if group_of(c.status.value) == name) for name in GROUPS}
        counts["total"] = len(children)
        digest = hashlib.sha1("|".join(f"{c.id}:{c.status.value}" for c in children).encode("utf-8")).hexdigest()[:12]
        shown = [c for c in children if group is None or group_of(c.status.value) == group]
        return {"session_id": session_id, "children": [c.to_dict() for c in shown], "counts": counts,
                "version": digest, "warnings": warnings}

    # --- live follow (GUI event stream) ------------------------------------------

    def open_follow(self, session_id: str, after: str | None = None, last_event_id: str | None = None) -> Follow:
        """Validate a subscription: the id, the cursor and that the session exists and may be shown.

        ``last_event_id`` (the SSE reconnect header) wins over ``after`` when it is a valid, later cursor;
        an invalid ``last_event_id`` is ignored, an invalid ``after`` is ``invalid_cursor``.
        """
        source, native = _parse_id(session_id)
        self._get_summary(session_id)  # existence, allowed folders, enabled source
        if source == "framework":
            cursor = _framework_cursor(after)
            if isinstance(last_event_id, str) and _INT_CURSOR.fullmatch(last_event_id.strip()):
                cursor = max(cursor, int(last_event_id))
            return Follow(session_id, str(cursor), run_id=native, store=self._framework_store(native))
        cursor_text = _file_cursor(after)
        if isinstance(last_event_id, str):
            try:
                if _offset(last_event_id.strip()) > _offset(cursor_text):
                    cursor_text = last_event_id.strip()
            except InvalidCursor:
                pass
        return Follow(session_id, cursor_text or "0:0")

    def poll_items(self, session_id: str, after: str | None, limit: int = MAX_LIMIT) -> dict[str, Any]:
        """Items after ``after`` for a followed file session, without the folder re-check.

        Cheap at the end of the file (one seek and read), so the stream can call it every poll.
        """
        _parse_id(session_id)
        with _session_errors():
            return self._get_registry().read_items_unchecked(session_id, after, _check_limit(limit)).to_dict()

    # --- filters ---------------------------------------------------------------------

    @staticmethod
    def _check_filters(source: Any, surface: Any, cwd: Any, since: Any, until: Any, text: Any,
                       limit: Any, after: Any) -> SessionFilters:
        if source is not None and source not in SESSION_SOURCES:
            raise _invalid(f"source must be one of {', '.join(SESSION_SOURCES)}", field="source")
        if surface is not None and surface not in tuple(s.value for s in Surface):
            raise _invalid(f"surface must be one of {', '.join(s.value for s in Surface)}", field="surface")
        for name, value, size in (("cwd", cwd, 1000), ("text", text, 200)):
            if value is not None and (not isinstance(value, str) or len(value) > size):
                raise _invalid(f"{name} must be text of at most {size} characters", field=name)
        stamps: dict[str, str | None] = {}
        for name, value in (("since", since), ("until", until)):
            if value is not None and (not isinstance(value, str) or normalize_ts(value) is None):
                raise _invalid(f"{name} must be an ISO-8601 date or time", field=name)
            stamps[name] = normalize_ts(value) if value else None
        return SessionFilters(source=source, surface=surface, cwd=cwd or None, since=stamps["since"],
                              until=stamps["until"], text=text or None, limit=_check_limit(limit), after=after or None)
