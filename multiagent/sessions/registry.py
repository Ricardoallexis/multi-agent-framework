# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Facade over the per-source readers: list, get and paginated items, restricted to allowed folders."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from ._common import MetaIndex, normalize_ts, within_root
from .children import MAX_CHILDREN, ChildAgent, LevelOf, no_level, sort_children
from .claude import ClaudeReader
from .codex import CodexReader
from .config import SessionsConfig
from .copilot import CopilotReader
from .models import (
    InvalidCursor,
    InvalidSessionId,
    ItemsPage,
    SessionFilters,
    SessionNotFound,
    SessionPage,
    SessionSummary,
    Status,
    decode_cursor,
    encode_cursor,
)

MAX_PAGE = 1000
SCAN_TTL_SECONDS = 2.0  # how long the per-source listing is reused before the folders are walked again
_READERS = {"claude": ClaudeReader, "codex": CodexReader, "copilot": CopilotReader}
_ROOTS = {"claude": ("claude_home", "claude_desktop_dir"), "codex": ("codex_home",), "copilot": ("copilot_home",)}


@dataclass(frozen=True)
class _Listing:
    at: float       # clock reading when the listing was built
    signature: tuple
    rows: tuple[list[SessionSummary], list[str]]


class SessionRegistry:
    """Readers plus the caches that keep repeated calls cheap.

    * ``MetaIndex``: per-file summaries keyed by path+mtime+size, bounded, transcripts continued from the
      bytes appended since the last read.
    * The listing of each source, reused for ``scan_ttl`` seconds (and only while its root folders keep
      their modification time), so a burst of calls walks the folders once. ``invalidate`` drops it.

    Nothing here holds a lock while it reads a file: a listing is built outside ``_scan_lock`` and only
    swapped in under it, so a slow rescan never blocks another thread that is polling.
    """

    def __init__(self, config: SessionsConfig, *, scan_ttl: float = SCAN_TTL_SECONDS,
                 clock: Callable[[], float] = time.monotonic, on_read: Callable[..., None] | None = None):
        self.config = config
        self._index = MetaIndex(on_read=on_read)  # metadata only; never content
        self._readers = {name: _READERS[name](config, self._index) for name in config.sources if name in _READERS}
        self._scan_ttl = scan_ttl
        self._clock = clock
        self._scan_lock = threading.RLock()
        self._scans: dict[str, _Listing] = {}

    def invalidate(self) -> None:
        """Forget the cached listings: the next call walks the folders again (the per-file index stays)."""
        with self._scan_lock:
            self._scans.clear()

    def _signature(self, name: str) -> tuple:
        """Modification time of the root folders of one source: a change there ends the listing early."""
        result = []
        for attr in _ROOTS[name]:
            path = getattr(self.config, attr)
            try:
                result.append((str(path.resolve()), path.stat().st_mtime_ns) if path is not None else None)
            except OSError:
                result.append((str(path), None))
        return tuple(result)

    def _listing(self, name: str) -> tuple[list[SessionSummary], list[str]]:
        """Summaries and warnings of one source, from the cache while it is fresh."""
        signature = self._signature(name)
        with self._scan_lock:
            cached = self._scans.get(name)
            if cached is not None and cached.signature == signature and self._clock() - cached.at < self._scan_ttl:
                return cached.rows
        rows = self._readers[name].list_summaries()  # folder walks and file reads happen outside the lock
        with self._scan_lock:
            self._scans[name] = _Listing(self._clock(), signature, rows)
        return rows

    def _allowed(self, summary: SessionSummary) -> bool:
        return within_root(summary.cwd, self.config.allowed_roots)

    def find_sessions(self, filters: SessionFilters | None = None) -> tuple[list[SessionSummary], list[str]]:
        """Every allowed session that matches the filters, newest first, plus warnings (``limit``/``after`` are ignored).

        One pass and one sort over the cached listing, for callers that merge sources and page themselves.
        """
        f = filters or SessionFilters()
        if not self.config.allowed_roots:
            return [], ["no_allowed_roots: configure gui_session_roots to list sessions"]
        since, until = normalize_ts(f.since) if f.since else None, normalize_ts(f.until) if f.until else None
        rows: list[SessionSummary] = []
        warnings: list[str] = []
        for name in self._readers:
            if f.source and f.source != name:
                continue
            summaries, warns = self._listing(name)
            warnings.extend(w for w in warns if w not in warnings)
            rows.extend(summaries)
        keep = []
        for s in rows:
            if not self._allowed(s):
                continue
            if f.surface and s.surface.value != f.surface:
                continue
            if f.cwd and not within_root(s.cwd, (f.cwd,)):
                continue
            if since and (s.updated_at or "") < since:
                continue
            if until and (s.updated_at or "") > until:
                continue
            if f.text and f.text.lower() not in (s.title or "").lower():
                continue
            keep.append(s)
        keep.sort(key=lambda s: (s.updated_at or "", s.id), reverse=True)
        return keep, warnings

    def list_sessions(self, filters: SessionFilters | None = None) -> SessionPage:
        f = filters or SessionFilters()
        if not self.config.allowed_roots:
            return SessionPage([], None, ["no_allowed_roots: configure gui_session_roots to list sessions"])
        _, start = decode_cursor(f.after, 1)
        keep, warnings = self.find_sessions(f)
        limit = max(1, min(int(f.limit), MAX_PAGE))
        page = keep[start:start + limit]
        nxt = encode_cursor(0, start + limit) if start + limit < len(keep) else None
        return SessionPage(page, nxt, warnings)

    def count_sessions(self) -> dict[str, dict[str, int]]:
        """Allowed sessions per source and status: ``{source: {"total": n, "active": n, ...}}``.

        One pass over the cached listing (no paging, no sorting, no row built per page), so it stays cheap
        with thousands of sessions. A source that is not configured is absent; the folder check is the
        one ``list_sessions`` applies.
        """
        if not self.config.allowed_roots:
            return {}
        counts: dict[str, dict[str, int]] = {}
        for name in self._readers:
            summaries, _ = self._listing(name)
            row = counts[name] = {"total": 0, **{status.value: 0 for status in Status}}
            for summary in summaries:
                if self._allowed(summary):
                    row["total"] += 1
                    row[summary.status.value] += 1
        return counts

    def _parse_id(self, session_id: str) -> tuple[str, str]:
        if not isinstance(session_id, str) or ":" not in session_id:
            raise InvalidSessionId("id must look like '<source>:<native_id>'")
        source, native = session_id.split(":", 1)
        if source not in _READERS or not native or len(native) > 200:
            raise InvalidSessionId("unknown source or empty native id")
        return source, native

    def get_session(self, session_id: str) -> SessionSummary:
        source, native = self._parse_id(session_id)
        reader = self._readers.get(source)
        if reader is None:
            raise SessionNotFound(session_id)
        summary = reader.get_summary(native)
        if not self._allowed(summary):
            raise SessionNotFound(session_id)
        return summary

    def activity(self, session_id: str) -> dict[str, object]:
        """Return only allowlisted activity metadata for an allowed session."""
        self.get_session(session_id)
        source, native = self._parse_id(session_id)
        reader = self._readers[source]
        data = reader.activity(native)
        result: dict[str, object] = {
            "last_output_at": data.get("last_output_at"),
            "source": {"claude": "claude_transcript", "codex": "codex_rollout_mtime",
                       "copilot": "copilot_events_mtime"}[source],
        }
        if source in ("claude", "codex", "copilot"):
            result["open_tests"] = bool(data.get("open_tests"))
        return result
    def read_items(self, session_id: str, after: str | None = None, limit: int = 200) -> ItemsPage:
        decode_cursor(after, 1)  # validate before touching any file
        self.get_session(session_id)  # existence + allowed folders
        source, native = self._parse_id(session_id)
        return self._readers[source].read_items(native, after, max(1, min(int(limit), MAX_PAGE)))

    def read_items_unchecked(self, session_id: str, after: str | None = None, limit: int = 200) -> ItemsPage:
        """Items without the folder and existence check, for live follow.

        The caller already ran ``get_session`` for this id; skipping it avoids re-scanning a growing
        file on every poll. The cursor and the id are still validated.
        """
        decode_cursor(after, 1)
        source, native = self._parse_id(session_id)
        reader = self._readers.get(source)
        if reader is None:
            raise SessionNotFound(session_id)
        return reader.read_items(native, after, max(1, min(int(limit), MAX_PAGE)))

    def list_children(self, session_id: str, *, now: datetime | None = None, level_of: LevelOf = no_level,
                      check: bool = True) -> tuple[list[ChildAgent], list[str]]:
        """Subagents / spawned threads of a session, most recent first, plus warnings.

        Same existence and allowed-folder check as ``get_session``. ``now`` is injectable for tests; metadata
        only (no message text). At most ``MAX_CHILDREN`` come back, with a warning when more exist.
        ``check=False`` skips the folder check for a caller that already ran ``get_session`` (live polling).
        """
        if check:
            self.get_session(session_id)  # existence + allowed folders
        source, native = self._parse_id(session_id)
        reader = self._readers.get(source)
        if reader is None:
            raise SessionNotFound(session_id)
        children, warnings = reader.list_children(native, now or datetime.now(UTC), level_of)
        children = sort_children(children)
        warnings = list(warnings)
        if len(children) > MAX_CHILDREN:
            children = children[:MAX_CHILDREN]
            warnings.append(f"children_truncated: only the {MAX_CHILDREN} most recent are listed")
        return children, warnings


def list_sessions(config: SessionsConfig, filters: SessionFilters | None = None) -> SessionPage:
    return SessionRegistry(config).list_sessions(filters)


def get_session(config: SessionsConfig, session_id: str) -> SessionSummary:
    return SessionRegistry(config).get_session(session_id)


def read_items(config: SessionsConfig, session_id: str, after: str | None = None, limit: int = 200) -> ItemsPage:
    return SessionRegistry(config).read_items(session_id, after, limit)


__all__ = ["InvalidCursor", "SessionRegistry", "get_session", "list_sessions", "read_items"]
