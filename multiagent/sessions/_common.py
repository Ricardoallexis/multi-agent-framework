# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Shared read-only helpers: streaming JSONL, truncation, timestamps, read-only SQLite."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import ItemsPage, SessionItem, decode_cursor, encode_cursor

MAX_LINE_BYTES = 1024 * 1024
HEAD_BYTES = 2048   # prefix of a transcript that is hashed to tell an append from a rewrite
ANCHOR_BYTES = 128  # bytes just before the resume point that are hashed for the same purpose
MAX_INDEX_ENTRIES = 5000  # bound of the per-file metadata index (least recently used goes first)
MAX_TEXT_CHARS = 20_000
MAX_TOOL_SUMMARY_CHARS = 500
ACTIVE_WINDOW_SECONDS = 120
_FILE_KEYS = ("file_path", "path", "filePath", "notebook_path", "filename")
_PATCH_FILE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", re.M)
_WIN_PREFIX = "\\\\?\\"


def clip(text: str, limit: int = MAX_TEXT_CHARS) -> tuple[str, bool]:
    return (text, False) if len(text) <= limit else (text[:limit], True)


def clean_cwd(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    return value[len(_WIN_PREFIX):] if value.startswith(_WIN_PREFIX) else value


def normalize_ts(value: object) -> str | None:
    """Epoch seconds/ms or ISO-8601 text -> ``YYYY-MM-DDTHH:MM:SS[.fff]Z`` (UTC)."""
    dt: datetime | None = None
    try:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, str) and re.fullmatch(r"-?\d+(\.\d+)?", value.strip()):
            value = float(value)
        if isinstance(value, (int, float)):
            seconds = value / 1000.0 if abs(value) >= 1e11 else float(value)
            dt = datetime.fromtimestamp(seconds, UTC)
        elif isinstance(value, str):
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    except (ValueError, OverflowError, OSError):
        return None
    if dt is None:
        return None
    text = dt.strftime("%Y-%m-%dT%H:%M:%S")
    if dt.microsecond:
        text += f".{dt.microsecond // 1000:03d}"
    return text + "Z"


def mtime_ts(path: Path) -> str | None:
    try:
        return normalize_ts(path.stat().st_mtime)
    except OSError:
        return None


def is_recent(ts: str | None) -> bool:
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (datetime.now(UTC) - dt).total_seconds() <= ACTIVE_WINDOW_SECONDS


def _short(value: Any, limit: int = 120) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text.replace("\n", " ")[:limit]


def summarize_args(args: Any) -> str:
    """Arguments of a tool call as a bounded one-line summary (never a whole file)."""
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except ValueError:
            return _short(args, MAX_TOOL_SUMMARY_CHARS)
        if not isinstance(parsed, dict):
            return _short(args, MAX_TOOL_SUMMARY_CHARS)
        args = parsed
    if isinstance(args, dict):
        text = ", ".join(f"{k}={_short(v)}" for k, v in args.items())
        return text[:MAX_TOOL_SUMMARY_CHARS]
    return _short(args, MAX_TOOL_SUMMARY_CHARS) if args is not None else ""


def files_from_args(args: Any) -> list[str]:
    if isinstance(args, str):
        found = _PATCH_FILE.findall(args)
        if found:
            return [f[:300] for f in found[:20]]
        try:
            args = json.loads(args)
        except ValueError:
            return []
    out: list[str] = []
    if isinstance(args, dict):
        for key in _FILE_KEYS:
            if isinstance(args.get(key), str):
                out.append(args[key][:300])
    return out[:20]


def text_item(item: SessionItem, text: str | None) -> SessionItem:
    if text is not None:
        item.text, item.truncated = clip(text)
    return item


def blocks_text(content: Any) -> str:
    """Plain text of a content value (string or list of blocks); images become a marker."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif block.get("type") in ("image", "input_image", "output_image"):
                    parts.append("[image]")
    elif isinstance(content, dict) and isinstance(content.get("text"), str):
        parts.append(content["text"])
    return "\n".join(parts)


def int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


# -------- streaming JSONL --------
def iter_records(path: Path, start: int = 0,
                 on_bytes: Callable[[int], None] | None = None) -> Iterator[tuple[dict | None, int, str]]:
    """Yield ``(record, end_offset, status)`` per line, never holding more than one line.

    ``status``: ``ok``, ``broken`` (not a JSON object, or nested too deeply to parse), ``oversized``
    (> 1 MB, skipped) and ``unavailable`` (the file cannot be opened: yielded once, with no path in it).
    A final line without newline that is not yet valid JSON (being written) stops the iteration
    without advancing past it. ``on_bytes`` (optional) is told how many bytes each read took.
    """
    try:
        fh = open(path, "rb")
        fh.seek(start)
    except OSError:  # PermissionError, a locked or vanished file: its text carries the local path
        yield None, start, "unavailable"
        return
    with fh:
        while True:
            line = fh.readline(MAX_LINE_BYTES + 1)
            if on_bytes is not None:
                on_bytes(len(line))
            if not line:
                return
            complete = line.endswith(b"\n")
            if not complete and len(line) > MAX_LINE_BYTES:
                while True:  # skip the rest of the oversized line in bounded chunks
                    rest = fh.readline(MAX_LINE_BYTES)
                    if on_bytes is not None:
                        on_bytes(len(rest))
                    if not rest or rest.endswith(b"\n"):
                        break
                yield None, fh.tell(), "oversized"
                continue
            if complete and len(line) > MAX_LINE_BYTES:
                yield None, fh.tell(), "oversized"
                continue
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped.decode("utf-8", "replace"))
            except (ValueError, RecursionError):  # not JSON, or nested deeper than the parser allows
                record = None
            if not isinstance(record, dict):
                if not complete:
                    return  # partial last line: try again on the next read
                yield None, fh.tell(), "broken"
                continue
            yield record, fh.tell(), "ok"


class WarningCounter:
    """Aggregates per-read warnings into short stable strings."""

    def __init__(self) -> None:
        self.broken = 0
        self.oversized = 0
        self.unavailable = False
        self.unknown: dict[str, int] = {}
        self.extra: list[str] = []

    def copy(self) -> WarningCounter:
        other = WarningCounter()
        other.broken, other.oversized, other.unavailable = self.broken, self.oversized, self.unavailable
        other.unknown, other.extra = dict(self.unknown), list(self.extra)
        return other

    def line(self, status: str) -> None:
        if status == "broken":
            self.broken += 1
        elif status == "oversized":
            self.oversized += 1
        elif status == "unavailable":
            self.unavailable = True

    def unknown_type(self, name: object) -> None:
        key = str(name)[:60]
        self.unknown[key] = self.unknown.get(key, 0) + 1

    def render(self) -> list[str]:
        out = list(self.extra)
        if self.unavailable:
            out.append("session_file_unavailable")
        if self.broken:
            out.append(f"broken_json_lines: {self.broken}")
        if self.oversized:
            out.append(f"oversized_lines_skipped: {self.oversized}")
        if self.unknown:
            names = ",".join(sorted(self.unknown)[:5])
            out.append(f"unknown_record_types: {sum(self.unknown.values())} ({names})")
        return out


ParseFn = Callable[[dict, WarningCounter], list[SessionItem]]


def read_page(path: Path, after: str | None, limit: int, parse: ParseFn) -> ItemsPage:
    """One page of items from a JSONL file; cursors are ``"0:<offset of the next line>"``."""
    _, offset = decode_cursor(after, 1)
    counter = WarningCounter()
    try:
        size = path.stat().st_size
    except OSError:
        return ItemsPage([], encode_cursor(0, 0), True, ["session_file_unavailable"])
    if offset > size:
        counter.extra.append("cursor_reset: file is shorter than the cursor; restarted from the beginning")
        offset = 0
    items: list[SessionItem] = []
    pos = offset
    for record, end, status in iter_records(path, offset):
        pos = end
        counter.line(status)
        if status == "unavailable":
            return ItemsPage([], encode_cursor(0, offset), True, counter.render())
        if record is not None:
            cursor = encode_cursor(0, end)
            for item in parse(record, counter):
                item.cursor = cursor
                items.append(item)
        if len(items) >= limit:
            break
    try:
        done = pos >= path.stat().st_size
    except OSError:
        done = True
    return ItemsPage(items, encode_cursor(0, pos), done, counter.render())


# -------- read-only SQLite --------
def open_ro(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=0.2)
    conn.row_factory = sqlite3.Row
    return conn


def table_names(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]


def select_rows(conn: sqlite3.Connection, table: str, wanted: tuple[str, ...]) -> list[dict[str, Any]]:
    """Rows of ``table`` restricted to the ``wanted`` columns that exist (schema tolerant)."""
    if table not in table_names(conn):
        return []
    have = table_columns(conn, table)
    present = [c for c in wanted if c in have]
    if not present:
        return []
    cols = ", ".join(f'"{c}"' for c in present)
    return [dict(row) for row in conn.execute(f'SELECT {cols} FROM "{table}"')]


def within_root(cwd: str | None, roots: tuple[str, ...]) -> bool:
    if not cwd or not roots or not os.path.isabs(cwd):  # a relative folder would resolve against our own cwd
        return False
    target = os.path.normcase(os.path.abspath(cwd))
    for root in roots:
        base = os.path.normcase(os.path.abspath(root)).rstrip("\\/")
        if target == base or target.startswith(base + os.sep):
            return True
    return False


class JsonlFold:
    """Accumulator over the records of one JSONL file that can continue where it stopped.

    ``add`` takes the next ``(record, status)`` of ``iter_records``; ``result`` gives what the reader
    needs (called once, when a read ends); ``copy`` makes an independent accumulator, because a published
    result is shared with other threads and must never change afterwards.
    """

    def add(self, rec: dict | None, status: str) -> None:
        raise NotImplementedError

    def copy(self) -> JsonlFold:
        raise NotImplementedError

    def result(self) -> Any:
        raise NotImplementedError


def _digest(data: bytes) -> bytes:
    return hashlib.blake2b(data, digest_size=8).digest()


@dataclass(frozen=True)
class _Entry:
    mtime_ns: int
    size: int
    value: Any
    # Only for JSONL scans that may continue: the accumulator as it was, the offset where the next read
    # starts (end of the last line folded) and short hashes of the head and of the bytes before ``offset``.
    fold: JsonlFold | None = None
    offset: int = 0
    head: bytes = b""
    anchor: bytes = b""


class MetaIndex:
    """In-memory metadata cache keyed by path+mtime+size (never content).

    One entry per ``(kind, absolute path)``, bounded to ``max_entries`` (least recently used first).
    Thread-safe: the lock only guards the table, never the file reads or the builders, so a slow read
    cannot block another thread that is polling; the result is published when the read ends.

    ``get`` caches whatever ``build`` returns. ``scan_jsonl`` is for transcripts that only grow: when the
    file grew and was not rewritten it reads just the appended bytes and continues the cached
    accumulator; otherwise (shrunk, same size but touched, head or bytes before the old end changed, or
    unreadable meanwhile) it reads the whole file again. ``on_read(path, nbytes, phase)`` is an optional
    probe told every read: ``"records"`` for the lines folded and ``"verify"`` for the few bytes hashed
    to tell an append from a rewrite.
    """

    def __init__(self, max_entries: int = MAX_INDEX_ENTRIES,
                 on_read: Callable[[Path, int, str], None] | None = None) -> None:
        self._data: OrderedDict[tuple[str, str], _Entry] = OrderedDict()
        self._lock = threading.RLock()
        self._max_entries = max_entries
        self._on_read = on_read

    # -- table --
    def _hit(self, slot: tuple[str, str], st: os.stat_result) -> tuple[_Entry | None, bool]:
        """The entry of ``slot`` and whether it still describes the file as ``st`` shows it."""
        with self._lock:
            entry = self._data.get(slot)
            if entry is not None and entry.mtime_ns == st.st_mtime_ns and entry.size == st.st_size:
                self._data.move_to_end(slot)
                return entry, True
            return entry, False

    def _publish(self, slot: tuple[str, str], entry: _Entry) -> Any:
        """Store ``entry`` and return the value to use: a concurrent equal one wins so all callers share it."""
        with self._lock:
            current = self._data.get(slot)
            if current is not None:
                if (current.mtime_ns, current.size) == (entry.mtime_ns, entry.size):
                    self._data.move_to_end(slot)
                    return current.value
            self._data[slot] = entry
            self._data.move_to_end(slot)
            while len(self._data) > self._max_entries:
                self._data.popitem(last=False)
        return entry.value

    def peek(self, kind: str, path: Path) -> Any | None:
        """The cached value if it is current, else ``None``; never reads the file."""
        try:
            st = path.stat()
        except OSError:
            return None
        entry, current = self._hit((kind, os.path.abspath(path)), st)
        return entry.value if current and entry is not None else None

    def get(self, kind: str, path: Path, build: Callable[[], Any]) -> Any:
        try:
            st = path.stat()
        except OSError:
            return build()
        slot = (kind, os.path.abspath(path))
        entry, current = self._hit(slot, st)
        if current and entry is not None:
            return entry.value
        return self._publish(slot, _Entry(st.st_mtime_ns, st.st_size, build()))

    # -- incremental JSONL --
    def _note(self, path: Path, nbytes: int, phase: str) -> None:
        if self._on_read is not None:
            self._on_read(path, nbytes, phase)

    def _marks(self, path: Path, offset: int) -> tuple[bytes, bytes] | None:
        """Hashes of the first bytes and of the bytes just before ``offset``; ``None`` if unreadable."""
        low = max(0, offset - ANCHOR_BYTES)
        try:
            with open(path, "rb") as fh:
                head = fh.read(min(offset, HEAD_BYTES))
                fh.seek(low)
                anchor = fh.read(offset - low)
        except OSError:
            return None
        self._note(path, len(head) + len(anchor), "verify")
        if len(head) < min(offset, HEAD_BYTES) or len(anchor) < offset - low:
            return None  # shorter than the point we claim to have read to: not an append
        return _digest(head), _digest(anchor)

    def _fold(self, path: Path, fold: JsonlFold, start: int) -> tuple[int, bool]:
        """Feed ``fold`` with the records from ``start``; return where the next read starts and whether the file was readable."""
        count = (lambda n: self._on_read(path, n, "records")) if self._on_read is not None else None
        offset, readable = start, True
        for rec, end, status in iter_records(path, start, count):
            fold.add(rec, status)
            if status == "unavailable":
                readable = False
            else:
                offset = end
        return offset, readable

    def scan_jsonl(self, kind: str, path: Path, new_fold: Callable[[], JsonlFold]) -> Any:
        """``new_fold()``'s result over the whole file, cached; reads only the appended bytes when possible."""
        try:
            st = path.stat()
        except OSError:
            fold = new_fold()
            self._fold(path, fold, 0)  # reports the file as unavailable, like any unreadable file
            return fold.result()
        slot = (kind, os.path.abspath(path))
        entry, current = self._hit(slot, st)
        if current and entry is not None:
            return entry.value
        fold, start = new_fold(), 0
        if (entry is not None and entry.fold is not None and st.st_size > entry.size
                and self._marks(path, entry.offset) == (entry.head, entry.anchor)):
            fold, start = entry.fold.copy(), entry.offset  # appended only: continue where the last read ended
        offset, readable = self._fold(path, fold, start)
        marks = self._marks(path, offset) if readable else None
        try:
            after = path.stat()
            stable = (after.st_mtime_ns, after.st_size) == (st.st_mtime_ns, st.st_size)
        except OSError:
            stable = False
        # A file that changed while it was read can not be continued later (the marks may describe
        # other bytes than the ones folded): the next change reads it whole, as it always did.
        resume = stable and marks is not None
        value = fold.result()
        return self._publish(slot, _Entry(
            st.st_mtime_ns, st.st_size, value, fold if resume else None, offset,
            marks[0] if resume and marks else b"", marks[1] if resume and marks else b""))
