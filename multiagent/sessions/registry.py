# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Facade over the per-source readers: list, get and paginated items, restricted to allowed folders."""
from __future__ import annotations

from ._common import MetaIndex, normalize_ts, within_root
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
    decode_cursor,
    encode_cursor,
)

MAX_PAGE = 1000
_READERS = {"claude": ClaudeReader, "codex": CodexReader, "copilot": CopilotReader}


class SessionRegistry:
    def __init__(self, config: SessionsConfig):
        self.config = config
        self._index = MetaIndex()  # metadata only; never content
        self._readers = {name: _READERS[name](config, self._index) for name in config.sources if name in _READERS}

    def _allowed(self, summary: SessionSummary) -> bool:
        return within_root(summary.cwd, self.config.allowed_roots)

    def list_sessions(self, filters: SessionFilters | None = None) -> SessionPage:
        f = filters or SessionFilters()
        if not self.config.allowed_roots:
            return SessionPage([], None, ["no_allowed_roots: configure gui_session_roots to list sessions"])
        _, start = decode_cursor(f.after, 1)
        since, until = normalize_ts(f.since) if f.since else None, normalize_ts(f.until) if f.until else None
        rows: list[SessionSummary] = []
        warnings: list[str] = []
        for name, reader in self._readers.items():
            if f.source and f.source != name:
                continue
            summaries, warns = reader.list_summaries()
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
        limit = max(1, min(int(f.limit), MAX_PAGE))
        page = keep[start:start + limit]
        nxt = encode_cursor(0, start + limit) if start + limit < len(keep) else None
        return SessionPage(page, nxt, warnings)

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

    def read_items(self, session_id: str, after: str | None = None, limit: int = 200) -> ItemsPage:
        decode_cursor(after, 1)  # validate before touching any file
        self.get_session(session_id)  # existence + allowed folders
        source, native = self._parse_id(session_id)
        return self._readers[source].read_items(native, after, max(1, min(int(limit), MAX_PAGE)))


def list_sessions(config: SessionsConfig, filters: SessionFilters | None = None) -> SessionPage:
    return SessionRegistry(config).list_sessions(filters)


def get_session(config: SessionsConfig, session_id: str) -> SessionSummary:
    return SessionRegistry(config).get_session(session_id)


def read_items(config: SessionsConfig, session_id: str, after: str | None = None, limit: int = 200) -> ItemsPage:
    return SessionRegistry(config).read_items(session_id, after, limit)


__all__ = ["InvalidCursor", "SessionRegistry", "get_session", "list_sessions", "read_items"]
