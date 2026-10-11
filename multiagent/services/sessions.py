# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Session viewer services (GUI-T04): one read-only, unified list of work sessions.

Wraps ``multiagent.sessions`` (Claude, Codex and Copilot readers, GUI-T03) and maps the framework's own
runs to the same ``SessionSummary``/``SessionItem`` model with ``source = "framework"``, so every
interface (the token-protected ``/gui/api/sessions`` routes, ``multiagent sessions`` and Python) sees
the same data. Nothing is written or stored: the readers open the apps' files read-only.

Configuration comes from ``Settings``: ``gui_session_roots`` (folders whose sessions may be shown;
empty shows nothing and the list says how to configure it), ``gui_session_sources`` and the four store
overrides ``gui_claude_home``, ``gui_codex_home``, ``gui_copilot_home`` and ``gui_claude_desktop_dir``.

Cursors are opaque to callers. File sessions use the reader's ``"0:<offset>"``; a framework run uses
the id of its last ``run_events`` row (the same number the GUI event stream sends as ``id:``), so a
client can read items and then follow the live stream from the last cursor it holds.

Errors (``ServiceError``): ``invalid_session_id`` (400), ``invalid_cursor`` (400), ``invalid_request``
(422, bad filter or limit) and ``not_found`` (404, also for a session outside the allowed folders).
The framework's runs are not tied to a folder (contract section 3), so ``get_session``/``read_items``
of a ``framework:<run_id>`` id do not depend on ``gui_session_roots``; the list is empty until roots
are configured, like every other source.
"""
from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from ..config import SESSION_SOURCES
from ..sessions import (
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
_EXTERNAL_ID = re.compile(rf"(?P<source>claude|codex|copilot):(?P<native>{_UUID})(?P<agent>:agent-[A-Za-z0-9_-]{{1,64}})?")
_FRAMEWORK_ID = re.compile(r"framework:(?P<native>[A-Za-z0-9_-]{1,64})")
_INT_CURSOR = re.compile(r"[0-9]{1,15}")
_RUN_STATUS = {
    "queued": Status.ACTIVE, "running": Status.ACTIVE, "waiting_human": Status.ACTIVE,
    "paused": Status.IDLE, "interrupted": Status.IDLE, "completed": Status.IDLE, "failed": Status.IDLE,
    "cancelled": Status.IDLE, "rejected": Status.IDLE,
}
FRAMEWORK_READER = "framework-runs/1"


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
        self._lock = threading.RLock()  # the readers' metadata index is not thread-safe
        self._registry: SessionRegistry | None = None
        self._registry_key: tuple | None = None

    # --- configuration ----------------------------------------------------------

    def _settings(self):
        return self._services.system.settings

    def _sources(self) -> tuple[str, ...]:
        return tuple(self._settings().gui_session_sources)

    def _roots(self) -> tuple[str, ...]:
        return tuple(self._settings().gui_session_roots)

    def _get_registry(self) -> SessionRegistry:
        s = self._settings()
        overrides = {name: getattr(s, f"gui_{name}") for name in
                     ("claude_home", "codex_home", "copilot_home", "claude_desktop_dir")
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
                    "SELECT id,workflow_id,status,created_at,updated_at FROM runs ORDER BY created_at DESC LIMIT ?",
                    (MAX_FRAMEWORK_RUNS,)).fetchall()
            rows.extend(dict(r) | {"_store": store} for r in found)
        return rows

    def _find_run(self, run_id: str) -> tuple[Any, dict[str, Any]]:
        for _, store in self._stores():
            with store.db.connect() as conn:
                row = conn.execute("SELECT id,workflow_id,status,created_at,updated_at FROM runs WHERE id=?",
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
            open_in_app=OpenInApp("url", f"/gui/#run={row['id']}", False), reader=FRAMEWORK_READER)

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

    def _framework_event_items(self, store: Any, run_id: str, after: int, limit: int) -> ItemsPage:
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

        Filters: ``source`` (claude, codex, copilot, framework), ``surface`` (desktop, cli, framework,
        unknown), ``cwd`` (only sessions whose folder is under it), ``since``/``until`` (ISO-8601,
        compared with ``updated_at``) and ``text`` (part of the title). With no ``gui_session_roots``
        the page is empty and ``hint`` says how to configure them (``None`` otherwise).
        """
        # An empty value (a blank form field, ``?since=``) means "no filter".
        source, surface, cwd, since, until, text, after = (
            None if value == "" else value for value in (source, surface, cwd, since, until, text, after))
        filters = self._check_filters(source, surface, cwd, since, until, text, limit, after)
        with _session_errors():
            _, start = decode_cursor(after, 1)
        if not self._roots():
            return {"sessions": [], "next_cursor": None, "hint": ROOTS_HINT,
                    "warnings": ["no_allowed_roots: configure gui_session_roots to list sessions"]}
        warnings: list[str] = []
        rows: list[SessionSummary] = []
        run_stores: dict[str, Any] = {}
        with self._lock:
            if source != "framework":
                with _session_errors():
                    registry = self._get_registry()
                    cursor: str | None = None
                    while True:
                        page = registry.list_sessions(replace(filters, limit=1000, after=cursor))
                        rows += page.sessions
                        warnings += [w for w in page.warnings if w not in warnings]
                        if page.next_cursor is None:
                            break
                        cursor = page.next_cursor
            if source in (None, "framework") and "framework" in self._sources():
                for summary, store in self._framework_matches(filters):
                    rows.append(summary)
                    run_stores[summary.native_id] = store
            rows.sort(key=lambda s: (s.updated_at or "", s.id), reverse=True)
            page_rows = rows[start:start + limit]
            self._with_usage(page_rows, run_stores)
        nxt = f"0:{start + limit}" if start + limit < len(rows) else None
        return {"sessions": [s.to_dict() for s in page_rows], "next_cursor": nxt, "warnings": warnings, "hint": None}

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
        with self._lock, _session_errors():
            return self._get_registry().get_session(session_id)

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
        with self._lock, _session_errors():
            return self._get_registry().read_items(session_id, after, limit).to_dict()

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
        with self._lock, _session_errors():
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
