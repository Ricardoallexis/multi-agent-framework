# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Local, bounded, read-only replay of completed interactive ACP turns."""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..egress_guard import findings, redact

SCHEMA = "maf.acp-history/1"
TEXT_LIMIT = 8000
MAX_BYTES = 2 * 1024 * 1024
MAX_FILES = 200
AGE = timedelta(days=30)
_ID = re.compile(r"[0-9a-f]{32}\Z")
_LOG = logging.getLogger(__name__)


def valid_id(identifier: str) -> bool:
    return isinstance(identifier, str) and _ID.fullmatch(identifier) is not None


def _cutoff() -> float:
    return (datetime.now(timezone.utc) - AGE).timestamp()


def _past_retention(info: os.stat_result, cutoff: float) -> bool:
    """Older than the retention or over the size limit: never shown, and deleted by the next purge."""
    return info.st_mtime < cutoff or info.st_size > MAX_BYTES


def should_keep_history(offices: Any, bundle: str, setting: dict[str, Any] | None) -> bool:
    """Only a known ordinary area saves by default; an explicit area choice wins."""
    if setting is not None and "keep_history" in setting:
        return setting["keep_history"] is True
    try:
        return offices.area_class(bundle) == "non_sensitive"
    except Exception:  # noqa: BLE001 - an unreadable classification must fail closed
        _LOG.warning("ACP area classification unavailable; history disabled")
        return False


def _instant() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(value: Any) -> tuple[Any, bool]:
    """Screen complete text fields, then clip to the same visible character bound as the GUI."""
    if isinstance(value, str):
        matches = findings(value, include_entropy=True)
        cleaned = redact(value, matches) if matches else value
        return (cleaned[:TEXT_LIMIT] + "…" if len(cleaned) > TEXT_LIMIT else cleaned), bool(matches)
    if isinstance(value, list):
        items = [_clean(item) for item in value]
        return [item for item, _ in items], any(flag for _, flag in items)
    if isinstance(value, dict):
        items = {key: _clean(item) for key, item in value.items() if isinstance(key, str)}
        return {key: item for key, (item, _) in items.items()}, any(flag for _, flag in items.values())
    if value is None or type(value) in (bool, int, float):
        return value, False
    return "", False


def _content(value: Any, depth: int = 0) -> Any:
    if depth > 3:
        return None
    if isinstance(value, list):
        return [_content(item, depth + 1) for item in value]
    if isinstance(value, dict):
        return {key: _content(item, depth + 1) if key == "content" else item
                for key, item in value.items()
                if key in ("type", "text", "content", "path", "oldText", "newText")}
    return value if isinstance(value, str) else None


def _visible_data(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "prompt": ("text",), "message": ("text", "content"), "thought": ("text", "content"),
        "tool": ("title", "kind", "text", "content", "locations"),
        "diff": ("title", "path", "oldText", "newText", "text", "content", "locations"),
        "plan": ("entries",), "usage": ("used", "size", "cost"),
        "end_turn": ("stop_reason",), "error": ("status", "message", "code", "text", "content")}
    result = {key: copy.deepcopy(data[key]) for key in fields.get(kind, ()) if key in data}
    if "content" in result:
        result["content"] = _content(result["content"])
    if "locations" in result:
        locations = result["locations"]
        result["locations"] = ([item if isinstance(item, str) else {"path": item["path"]}
                                for item in locations if isinstance(item, str)
                                or (isinstance(item, dict) and isinstance(item.get("path"), str))]
                               if isinstance(locations, list) else [])
    if "entries" in result:
        entries = result["entries"]
        result["entries"] = [{key: item[key] for key in ("content", "status", "priority") if key in item}
                             for item in entries if isinstance(item, dict)] if isinstance(entries, list) else []
    if "cost" in result:
        cost = result["cost"]
        result["cost"] = {key: cost[key] for key in ("amount", "currency") if key in cost} if isinstance(cost, dict) else {}
    return result


def _message_text(data: dict[str, Any]) -> str | None:
    """Read text in the same order as the thread, including lists of text blocks."""
    def blocks(value: Any) -> list[str] | None:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                piece = blocks(item)
                if piece is None:
                    return None
                parts.extend(piece)
            return parts
        if isinstance(value, dict):
            if isinstance(value.get("text"), str):
                return [value["text"]]
            if "content" in value:
                return blocks(value["content"])
        return None

    parts: list[str] = []
    for key in ("text", "content"):
        if key in data:
            piece = blocks(data[key])
            if piece is None:
                return None
            parts.extend(piece)
    return "\n".join(parts) if parts else None


def _turn(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    grouped: list[dict[str, Any]] = []
    for event in events:
        kind = event["kind"]
        data = _visible_data(kind, event.get("data") or {})
        message_text = _message_text(data) if kind in ("message", "thought") else None
        if (kind in ("message", "thought") and grouped and grouped[-1]["kind"] == kind
                and message_text is not None):
            prior = grouped[-1]["data"]
            prior_text = _message_text(prior)
            if prior_text is not None:
                grouped[-1]["data"] = {"text": prior_text + message_text}
                continue
        status = event.get("tool_status")
        if status not in ("pending", "in_progress", "completed", "failed", None):
            status = None
        grouped.append({"kind": kind, "tool_status": status, "data": copy.deepcopy(data)})
    cleaned = []
    redacted = False
    for event in grouped:
        data, found = _clean(event["data"])
        redacted |= found
        cleaned.append({**event, "data": data, "redacted": found})
    return cleaned, redacted


class AcpHistory:
    """Retention: the API purges at startup and ``create`` purges around each new file; reads hide what is past it."""

    def __init__(self, data_dir: Path):
        self.folder = Path(data_dir) / "acp-history"   # building it touches no disk
        self._lock = threading.RLock()

    def _path(self, identifier: str) -> Path | None:
        return self.folder / f"{identifier}.jsonl" if valid_id(identifier) else None

    def create(self, identifier: str, bundle: str, scope: str) -> None:
        path = self._path(identifier)
        if path is None:
            raise ValueError("Invalid ACP history id")
        self.purge()
        with self._lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            office, area = scope.split("/", 1)
            header = {"schema": SCHEMA, "id": identifier, "bundle": bundle, "scope": scope,
                      "office": office, "area": area, "started_at": _instant()}
            with path.open("x", encoding="utf-8") as output:
                output.write(json.dumps(header, ensure_ascii=False) + "\n")
        self.purge()

    def append_turn(self, identifier: str, events: list[dict[str, Any]]) -> dict[str, Any]:
        path = self._path(identifier)
        if path is None:
            raise ValueError("Invalid ACP history id")
        cleaned, redacted = _turn(events)
        with self._lock:
            _, previous, partial = self._read(path)
            count = len(previous)
            if partial:
                with path.open("rb+") as output:
                    lines_before = output.readlines()
                    output.truncate(sum(len(line) for line in lines_before[:count + 1]))
            lines = "".join(json.dumps({"cursor": count + index, **event}, ensure_ascii=False,
                                       separators=(",", ":")) + "\n"
                            for index, event in enumerate(cleaned, 1))
            if path.stat().st_size + len(lines.encode("utf-8")) > MAX_BYTES:
                raise OverflowError("ACP history size limit reached")
            with path.open("a", encoding="utf-8") as output:
                output.write(lines)
        return {"redacted": redacted, "count": len(cleaned)}

    @staticmethod
    def _read(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
        with path.open("rb") as source:
            lines = source.readlines()
        if not lines or not lines[0].endswith(b"\n"):
            raise ValueError("Unreadable ACP history")
        header = json.loads(lines[0])
        if (not isinstance(header, dict) or header.get("schema") != SCHEMA
                or not valid_id(header.get("id"))
                or any(not isinstance(header.get(key), str)
                       for key in ("bundle", "scope", "office", "area", "started_at"))):
            raise ValueError("Unreadable ACP history")
        events: list[dict[str, Any]] = []
        partial = False
        for line in lines[1:]:
            if not line.endswith(b"\n"):
                partial = True
                break
            try:
                event = json.loads(line)
            except (UnicodeError, ValueError):
                partial = True
                break
            if not isinstance(event, dict) or event.get("cursor") != len(events) + 1:
                partial = True
                break
            events.append(event)
        return header, events, partial

    def page(self, identifier: str, after: int = 0) -> dict[str, Any] | None:
        path = self._path(identifier)
        if path is None or not path.is_file():
            return None
        try:
            if _past_retention(path.stat(), _cutoff()):
                return None
            header, events, partial = self._read(path)
            if header["id"] != identifier:
                return None
        except (OSError, UnicodeError, ValueError, KeyError):
            return None
        selected = events[after:]
        return {"id": identifier, "events": selected,
                "next_cursor": selected[-1]["cursor"] if selected else after,
                "active": False, "saved": True, "consent_configured": True,
                "redacted": any(event.get("redacted") for event in events),
                "incomplete": partial or not events or events[-1]["kind"] not in ("end_turn", "error")}

    def list(self, active: set[str] | None = None) -> list[dict[str, Any]]:
        active = active or set()
        if not self.folder.is_dir():
            return []
        rows = []
        cutoff = _cutoff()
        for path in self.folder.glob("*.jsonl"):
            if not valid_id(path.stem):
                continue
            try:
                info = path.stat()
                if path.stem not in active and _past_retention(info, cutoff):
                    continue
                header, events, partial = self._read(path)
                if header["id"] != path.stem:
                    raise ValueError("History id mismatch")
                rows.append({"id": path.stem, "bundle": header["bundle"], "scope": header["scope"],
                             "office": header["office"], "area": header["area"],
                             "started_at": header["started_at"],
                             "last_message_at": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
                             "message_count": sum(event["kind"] in ("prompt", "message") for event in events),
                             "active": path.stem in active, "unreadable": partial,
                             "redacted": any(event.get("redacted") for event in events)})
            except (OSError, UnicodeError, ValueError, KeyError, TypeError):
                rows.append({"id": path.stem, "unreadable": True, "active": path.stem in active})
        return sorted(rows, key=lambda row: row.get("last_message_at", ""), reverse=True)[:MAX_FILES]

    def delete(self, identifier: str) -> bool:
        path = self._path(identifier)
        if path is None:
            return False
        with self._lock:
            try:
                path.unlink()
                return True
            except FileNotFoundError:
                return False

    def delete_all(self) -> int:
        if not self.folder.is_dir():
            return 0
        return sum(self.delete(path.stem) for path in self.folder.glob("*.jsonl") if valid_id(path.stem))

    def purge(self) -> None:
        if not self.folder.is_dir():
            return
        with self._lock:
            files = []
            for path in self.folder.glob("*.jsonl"):
                if not valid_id(path.stem):
                    continue
                try:
                    files.append((path, path.stat()))
                except OSError:
                    _LOG.warning("Could not inspect ACP history")
            files.sort(key=lambda item: item[1].st_mtime, reverse=True)
            cutoff = _cutoff()
            for index, (path, info) in enumerate(files):
                try:
                    if index >= MAX_FILES or _past_retention(info, cutoff):
                        path.unlink()
                except OSError:
                    _LOG.warning("Could not purge ACP history")
