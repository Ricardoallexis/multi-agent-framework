# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Antigravity (agy) reader: ``conversation_summaries.db`` plus the ``conversations/<uuid>.db`` file names.

Read-only and metadata only. The folder (``<home>/.gemini/antigravity-cli``) holds:

* ``conversation_summaries.db`` (SQLite), table ``conversation_summaries``: one row per conversation with the
  id, title, step count, last modification, workspace URIs, run status and parent. Only the allow-listed
  columns below are selected; ``preview``, ``raw_summary`` and the full-text tables (``conversations_fts*``)
  hold conversation text and are never selected.
* ``conversations/<uuid>.db``: one SQLite per conversation. Only ``gen_metadata.data`` is read, and from each
  protobuf blob only four numeric varints (``decode_gen_usage``); ``steps`` and every other table hold
  content and are never selected. Their sum is the session's tokens; any doubt leaves the tokens unknown.
* ``brain/<uuid>/``: plans and artifacts. Never read.

Databases are opened ``mode=ro``. If the summary database is missing or unreadable the conversation file
names (a uuid and a modification time) are listed instead. If the folder does not exist, nothing is listed.
``read_items`` never returns conversation text: it answers an empty, finished page with a warning.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from ._common import MetaIndex, clean_cwd, is_recent, mtime_ts, normalize_ts, open_ro, select_rows, table_columns, table_names
from .children import MAX_NAME_CHARS, ChildAgent, LevelOf, derive_status
from .config import SessionsConfig
from .models import ItemsPage, SessionNotFound, SessionSummary, Source, Status, Surface, TokenUsage, is_uuid

READER = "antigravity-summaries/1"
SUMMARY_DB = "conversation_summaries.db"
MAX_TITLE_CHARS = 200
# The only columns ever selected (no ``preview``, no ``raw_summary``, no full-text tables).
COLUMNS = ("conversation_id", "title", "last_modified_time", "workspace_uris", "status", "parent_conversation_id",
           "agent_name", "killed", "not_fully_idle")
MAX_BLOB_BYTES = 4 * 1024 * 1024
MAX_GEN_ROWS = 20000
TOKENS_UNKNOWN = "tokens_not_recorded: antigravity usage could not be read for this conversation"
# gen_metadata.data = protobuf: field 1 -> message, its field 4 -> message with these varints (verified against the
# ``usage`` of ``agy --output-format json``): 2 input (cache NOT included; the cache can be larger), 3 output
# (reasoning included), 5 cache read. Like Claude: total = input + cache_read + output (agy's own total omits the cache).
_USAGE_PATH = (1, 4)
_USAGE_FIELDS = {2: "input", 3: "output", 5: "cache_read", 9: "thinking"}
NO_CONTENT = "content_not_read: antigravity conversations are not read, only their metadata"


def _varint(buf: bytes, pos: int) -> tuple[int, int] | None:
    value = shift = 0
    while pos < len(buf) and shift < 70:
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7
    return None


def _fields(buf: bytes):
    """``(field number, wire type, value)`` of a protobuf message; value is an int (varint) or the bytes (length-delimited).

    Fixed32/fixed64 are skipped, groups and anything malformed raise ``ValueError``. No other field is interpreted.
    """
    pos = 0
    while pos < len(buf):
        head = _varint(buf, pos)
        if head is None:
            raise ValueError("bad tag")
        tag, pos = head
        number, wire = tag >> 3, tag & 7
        if number == 0:
            raise ValueError("bad field number")
        if wire == 0:
            got = _varint(buf, pos)
            if got is None:
                raise ValueError("bad varint")
            value, pos = got
            yield number, wire, value
        elif wire == 2:
            got = _varint(buf, pos)
            if got is None:
                raise ValueError("bad length")
            size, pos = got
            if pos + size > len(buf):
                raise ValueError("truncated")
            yield number, wire, buf[pos:pos + size]
            pos += size
        elif wire in (1, 5):
            pos += 8 if wire == 1 else 4
            if pos > len(buf):
                raise ValueError("truncated")
        else:
            raise ValueError("unsupported wire type")


def decode_gen_usage(blob: object) -> dict[str, int] | None:
    """``{input, output, cache_read, thinking}`` (missing ones 0) of one ``gen_metadata.data`` blob, else ``None``.

    ``None`` for anything that is not the known shape (not bytes, too large, malformed, path or fields absent): the
    caller then reports the tokens as unknown instead of a number that may be wrong.
    """
    if not isinstance(blob, (bytes, bytearray, memoryview)) or len(blob) > MAX_BLOB_BYTES:
        return None
    node: bytes = bytes(blob)
    try:
        for number in _USAGE_PATH:
            node = next((v for n, w, v in _fields(node) if n == number and w == 2), None)  # type: ignore[assignment]
            if node is None:
                return None
        out = {name: 0 for name in _USAGE_FIELDS.values()}
        seen = set()
        for number, wire, value in _fields(node):
            if wire == 0 and number in _USAGE_FIELDS:
                out[_USAGE_FIELDS[number]] = value
                seen.add(number)
    except (ValueError, TypeError):
        return None
    return out if 2 in seen and 3 in seen else None


def read_gen_tokens(db: Path) -> TokenUsage | None:
    """Tokens of one conversation file: the sum over its ``gen_metadata`` rows; ``None`` if any row cannot be decoded."""
    try:
        conn = open_ro(db)
        try:
            if "gen_metadata" not in table_names(conn) or "data" not in table_columns(conn, "gen_metadata"):
                return None
            rows = conn.execute("SELECT data FROM gen_metadata LIMIT ?", (MAX_GEN_ROWS + 1,)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if not rows or len(rows) > MAX_GEN_ROWS:
        return None
    total = {"input": 0, "output": 0, "cache_read": 0}
    for (blob,) in rows:
        usage = decode_gen_usage(blob)
        if usage is None:
            return None
        for key in total:
            total[key] += usage[key]
    return TokenUsage(input=total["input"], output=total["output"], cache_read=total["cache_read"],
                      total=total["input"] + total["cache_read"] + total["output"])


def workspace_path(uris: object) -> str | None:
    """The first ``file:///`` workspace of the JSON list agy stores, as a local path (``None`` if there is none)."""
    try:
        values = json.loads(uris) if isinstance(uris, str) else uris
    except ValueError:
        return None
    if not isinstance(values, list):
        return None
    for value in values:
        if not isinstance(value, str):
            continue
        parsed = urlparse(value)
        if parsed.scheme != "file":
            continue
        path = unquote(parsed.path)
        if len(path) >= 3 and path[0] == "/" and path[2] == ":":  # /d:/x -> d:/x
            path = path[1:]
        if os.name == "nt":
            path = path.replace("/", "\\")
        return clean_cwd(path)
    return None


def _truthy(value: object) -> bool:
    return value in (1, True, "1", "true", "True")


def _running(row: dict[str, Any]) -> bool:
    return str(row.get("status") or "").endswith("_RUNNING") or _truthy(row.get("not_fully_idle"))


class AntigravityReader:
    source = Source.ANTIGRAVITY

    def __init__(self, config: SessionsConfig, index: MetaIndex):
        self.config = config
        self.index = index

    def _home(self) -> Path | None:
        home = self.config.antigravity_home
        return home if home is not None and home.is_dir() else None

    @staticmethod
    def _files(home: Path) -> dict[str, Path]:
        base = home / "conversations"
        if not base.is_dir():
            return {}
        return {p.stem.lower(): p for p in sorted(base.glob("*.db")) if is_uuid(p.stem)}

    @staticmethod
    def _rows(home: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
        """Allow-listed summary rows by lower-case conversation id."""
        db = home / SUMMARY_DB
        if not db.is_file():
            return {}, ["summaries_unavailable: antigravity summary database not found"]
        try:
            conn = open_ro(db)
            try:
                rows = select_rows(conn, "conversation_summaries", COLUMNS)
            finally:
                conn.close()
        except sqlite3.Error as exc:
            return {}, [f"summaries_unavailable: antigravity summary database ({type(exc).__name__})"]
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            cid = str(row.get("conversation_id") or "").lower()
            if is_uuid(cid):
                out[cid] = row
        return out, []

    @staticmethod
    def _created(path: Path | None) -> str | None:
        """Creation time of the conversation file where the platform records it (Windows, macOS); else ``None``."""
        if path is None:
            return None
        try:
            st = path.stat()
        except OSError:
            return None
        born = getattr(st, "st_birthtime", None) or (st.st_ctime if sys.platform == "win32" else None)
        return normalize_ts(born)

    def _tokens(self, file: Path | None) -> TokenUsage | None:
        if file is None:
            return None
        return self.index.get("antigravity-tokens", file, lambda: read_gen_tokens(file))

    def _load(self) -> tuple[Path | None, dict[str, dict[str, Any]], dict[str, Path], list[str]]:
        home = self._home()
        if home is None:
            return None, {}, {}, []
        rows, warns = self._rows(home)
        return home, rows, self._files(home), warns

    @staticmethod
    def _child_counts(rows: dict[str, dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in rows.values():
            parent = str(row.get("parent_conversation_id") or "").lower()
            if is_uuid(parent):
                counts[parent] = counts.get(parent, 0) + 1
        return counts

    def _summary(self, native: str, row: dict[str, Any] | None, files: dict[str, Path],
                 children: dict[str, int], extra: list[str]) -> SessionSummary:
        file = files.get(native)
        updated = normalize_ts((row or {}).get("last_modified_time")) or (mtime_ts(file) if file else None)
        row = row or {}
        parent = str(row.get("parent_conversation_id") or "").lower()
        title = row.get("title")
        warnings = list(extra)
        if not row:
            warnings.append("no_summary_row")
        tokens = self._tokens(file)
        if tokens is None and file is not None:
            warnings.append(TOKENS_UNKNOWN)
        return SessionSummary(
            id=f"antigravity:{native}", source=Source.ANTIGRAVITY, surface=Surface.CLI, native_id=native,
            title=title.strip()[:MAX_TITLE_CHARS] if isinstance(title, str) and title.strip() else None,
            cwd=workspace_path(row.get("workspace_uris")), created_at=self._created(file), updated_at=updated,
            models=[], status=Status.ACTIVE if _running(row) and is_recent(updated) else Status.IDLE,
            parent_id=f"antigravity:{parent}" if is_uuid(parent) else None, child_count=children.get(native, 0),
            tokens=tokens, open_in_app=None, reader=READER, warnings=warnings)

    # ---- public ----
    def list_summaries(self) -> tuple[list[SessionSummary], list[str]]:
        home, rows, files, warns = self._load()
        if home is None:
            return [], ["source_unavailable: antigravity store not found"]
        counts = self._child_counts(rows)
        # A conversation exists if its file does or its summary row does (a row may outlive a deleted file).
        return [self._summary(n, rows.get(n), files, counts, []) for n in sorted(set(files) | set(rows))], warns

    def get_summary(self, native_id: str) -> SessionSummary:
        if not is_uuid(native_id):
            raise SessionNotFound(native_id)
        native = native_id.lower()
        home, rows, files, _ = self._load()
        if home is None or (native not in rows and native not in files):
            raise SessionNotFound(native_id)
        return self._summary(native, rows.get(native), files, self._child_counts(rows), [])

    def activity(self, native_id: str) -> dict[str, Any]:
        summary = self.get_summary(native_id)
        home = self._home()
        file = self._files(home).get(native_id.lower()) if home else None
        return {"last_output_at": (mtime_ts(file) if file else None) or summary.updated_at, "open_tests": False}

    def read_items(self, native_id: str, after: str | None, limit: int) -> ItemsPage:
        self.get_summary(native_id)  # existence only: the conversation itself is never opened
        return ItemsPage([], "0:0", True, [NO_CONTENT])

    def list_children(self, native_id: str, now: datetime, level_of: LevelOf) -> tuple[list[ChildAgent], list[str]]:
        """Conversations whose ``parent_conversation_id`` is this one (agy subagents), metadata only."""
        if not is_uuid(native_id):
            raise SessionNotFound(native_id)
        native = native_id.lower()
        _, rows, files, _ = self._load()
        if native not in rows and native not in files:
            raise SessionNotFound(native_id)
        out: list[ChildAgent] = []
        for cid, row in rows.items():
            if str(row.get("parent_conversation_id") or "").lower() != native:
                continue
            last = normalize_ts(row.get("last_modified_time"))
            name = row.get("agent_name")
            out.append(ChildAgent(
                id=f"antigravity:{native}#{cid}", source="antigravity", kind="subagent",
                name=name[:MAX_NAME_CHARS] if isinstance(name, str) and name else None, label=None, model=None,
                level=level_of(None),
                status=derive_status(now=now, last_activity=last, finished=not _running(row),
                                     failed=_truthy(row.get("killed"))),
                started_at=self._created(files.get(cid)), last_activity_at=last, tokens=self._tokens(files.get(cid)),
                link_id=f"antigravity:{cid}"))
        return out, []
