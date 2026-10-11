# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Copilot reader: session-state/<uuid>/{workspace.yaml,events.jsonl} plus read-only databases.

The Copilot desktop app data under ``%LOCALAPPDATA%\\copilot-desktop-*`` is not read: its layout
does not match this model (GUI-T02 section 3 allows using it only when it does).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from ._common import (
    MetaIndex,
    WarningCounter,
    blocks_text,
    clean_cwd,
    files_from_args,
    int_or_none,
    is_recent,
    iter_records,
    mtime_ts,
    normalize_ts,
    open_ro,
    read_page,
    select_rows,
    summarize_args,
    text_item,
)
from .children import (
    MAX_LABEL_CHARS,
    MAX_NAME_CHARS,
    ChildAgent,
    LevelOf,
    clean_text,
    derive_status,
    is_test_call,
    safe_id,
)
from .config import SessionsConfig
from .models import (
    ItemsPage,
    Kind,
    OpenInApp,
    SessionItem,
    SessionNotFound,
    SessionSummary,
    Source,
    Status,
    Surface,
    TokenUsage,
    is_uuid,
)

READER = "copilot-events/1"
MAX_WORKSPACE_YAML_BYTES = 1024 * 1024  # a workspace.yaml is a few lines; a larger file is not read
SEEN_VERSIONS = ("copilotVersion 0.x",)
_IGNORED = {"session.start", "session.resume", "session.info", "session.idle", "session.model_change",
            "session.truncation", "session.snapshot_rewind", "session.error", "session.warning",
            "assistant.turn_start", "assistant.turn_end", "assistant.message_delta",
            "assistant.reasoning_delta", "tool.execution_partial_result", "tool.execution_progress",
            "tool.user_requested", "user.message_delta", "abort", "hook.start", "hook.end",
            "session.compaction_start", "session.task_complete", "skill.invoked", "pending_messages.modified",
            "session.mode_changed", "session.plan_changed", "session.workspace_file_changed"}


def parse_flat_yaml(text: str) -> dict[str, str]:
    """Top-level ``key: value`` pairs of a flat workspace.yaml (no nesting, no anchors, no tags)."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#") or raw[0] in " \t-":
            continue
        key, sep, value = raw.partition(":")
        if not sep or not key.strip() or " " in key.strip():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        out[key.strip()] = value
    return out


class CopilotReader:
    source = Source.COPILOT

    def __init__(self, config: SessionsConfig, index: MetaIndex):
        self.config = config
        self.index = index

    def _dirs(self) -> dict[str, Path]:
        home = self.config.copilot_home
        base = home / "session-state" if home else None
        if base is None or not base.is_dir():
            return {}
        return {d.name.lower(): d for d in sorted(base.iterdir()) if d.is_dir() and is_uuid(d.name)}

    def _db_info(self) -> tuple[dict[str, dict], dict[str, TokenUsage], list[str]]:
        """Titles/types from data.db + session-store.db, usage from session-store.db."""
        home = self.config.copilot_home
        meta: dict[str, dict] = {}
        usage: dict[str, TokenUsage] = {}
        warns: list[str] = []
        if home is None:
            return meta, usage, warns
        for name in ("session-store.db", "data.db"):
            db = home / name
            if not db.is_file():
                continue
            try:
                conn = open_ro(db)
                try:
                    for row in select_rows(conn, "sessions", ("id", "title", "summary", "session_type", "cwd")):
                        sid = str(row.get("id", "")).lower()
                        if is_uuid(sid):
                            cur = meta.setdefault(sid, {})
                            for k in ("title", "summary", "session_type", "cwd"):
                                if row.get(k) and not cur.get(k):
                                    cur[k] = row[k]
                    if name == "session-store.db":
                        for row in select_rows(conn, "assistant_usage_events",
                                               ("session_id", "input_tokens", "output_tokens",
                                                "cache_read_tokens", "cache_write_tokens")):
                            sid = str(row.get("session_id", "")).lower()
                            if not is_uuid(sid):
                                continue
                            prev = usage.get(sid, TokenUsage())
                            usage[sid] = TokenUsage(*(_add(getattr(prev, a), int_or_none(row.get(c))) for a, c in (
                                ("input", "input_tokens"), ("output", "output_tokens"),
                                ("cache_read", "cache_read_tokens"), ("cache_write", "cache_write_tokens"))))
                finally:
                    conn.close()
            except sqlite3.Error as exc:
                warns.append(f"source_unavailable: copilot {name} ({type(exc).__name__}: {str(exc)[:80]})")
        return meta, usage, warns

    def _scan(self, events: Path | None) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            info: dict[str, Any] = {"models": [], "first": None, "last": None, "tokens": None,
                                    "subagents": 0, "version": None, "warnings": []}
            if events is None:
                return info
            counter = WarningCounter()
            tokens = TokenUsage()
            have = False
            for rec, _, status in iter_records(events):
                counter.line(status)
                if rec is None:
                    continue
                ts = normalize_ts(rec.get("timestamp"))
                if ts:
                    info["first"] = info["first"] or ts
                    info["last"] = ts
                rtype = rec.get("type")
                data = rec.get("data") if isinstance(rec.get("data"), dict) else {}
                if rtype == "session.start":
                    info["version"] = data.get("copilotVersion")
                model = data.get("newModel") or data.get("selectedModel") or data.get("model")
                if isinstance(model, str) and model not in info["models"] and rtype in (
                        "session.model_change", "session.start", "assistant.message"):
                    info["models"].append(model)
                if rtype == "subagent.started":
                    info["subagents"] += 1
                elif rtype == "session.usage_record":
                    u = _usage(data)
                    if u is not None:
                        have = True
                        tokens = TokenUsage(*(_add(getattr(tokens, a), getattr(u, a))
                                              for a in ("input", "output", "cache_read", "cache_write")))
                elif not _known(rtype):
                    counter.unknown_type(rtype)
            info["tokens"] = tokens if have else None
            info["warnings"] = counter.render()
            return info

        if events is None:
            return build()
        return self.index.get("copilot", events, build)

    def _summary(self, native: str, d: Path, dbmeta: dict[str, dict], usage: dict[str, TokenUsage],
                 extra: list[str]) -> SessionSummary:
        ws: dict[str, str] = {}
        extra = list(extra)
        try:
            with open(d / "workspace.yaml", "rb") as fh:
                raw = fh.read(MAX_WORKSPACE_YAML_BYTES + 1)
            if len(raw) > MAX_WORKSPACE_YAML_BYTES:
                extra.append("workspace_yaml_skipped: file is larger than 1 MB")
            else:
                ws = parse_flat_yaml(raw.decode("utf-8", errors="replace"))
        except OSError:
            pass
        events = d / "events.jsonl"
        has_events = events.is_file()
        info = self._scan(events if has_events else None)
        meta = dbmeta.get(native, {})
        stype = meta.get("session_type")
        surface = Surface.UNKNOWN if not stype else (Surface.CLI if "cli" in str(stype).lower() else Surface.DESKTOP)
        title = meta.get("title") or meta.get("summary") or ws.get("name") or ws.get("summary") or None
        updated = normalize_ts(ws.get("updated_at")) or info["last"] or mtime_ts(events if has_events else d)
        warnings = list(extra) + list(info["warnings"])
        if not has_events:
            warnings.append("no_local_history")
        return SessionSummary(
            id=f"copilot:{native}", source=Source.COPILOT, surface=surface, native_id=native, title=title,
            cwd=clean_cwd(ws.get("cwd")) or clean_cwd(meta.get("cwd")),
            created_at=normalize_ts(ws.get("created_at")) or info["first"] or mtime_ts(d), updated_at=updated,
            models=list(info["models"]), status=Status.ACTIVE if is_recent(updated) else Status.IDLE,
            child_count=info["subagents"], tokens=usage.get(native) or info["tokens"],
            open_in_app=OpenInApp("url", f"ghapp://sessions/{native}", True) if is_uuid(native) else None,
            reader=READER, warnings=warnings)

    # ---- public ----
    def list_summaries(self) -> tuple[list[SessionSummary], list[str]]:
        home = self.config.copilot_home
        if home is None or not (home / "session-state").is_dir():
            return [], ["source_unavailable: copilot store not found"]
        dbmeta, usage, warns = self._db_info()
        return [self._summary(n, d, dbmeta, usage, warns) for n, d in self._dirs().items()], warns

    def get_summary(self, native_id: str) -> SessionSummary:
        d = self._dirs().get(native_id.lower()) if is_uuid(native_id) else None
        if d is None:
            raise SessionNotFound(native_id)
        dbmeta, usage, warns = self._db_info()
        return self._summary(native_id.lower(), d, dbmeta, usage, warns)

    def read_items(self, native_id: str, after: str | None, limit: int) -> ItemsPage:
        d = self._dirs().get(native_id.lower()) if is_uuid(native_id) else None
        if d is None:
            raise SessionNotFound(native_id)
        events = d / "events.jsonl"
        if not events.is_file():
            return ItemsPage([], "0:0", True, ["no_local_history"])
        state = {"model": None}
        return read_page(events, after, limit, lambda rec, c: self._parse(rec, c, state))

    def _parse(self, rec: dict, counter: WarningCounter, state: dict) -> list[SessionItem]:
        rtype = rec.get("type")
        ts = normalize_ts(rec.get("timestamp"))
        data = rec.get("data") if isinstance(rec.get("data"), dict) else {}
        if rtype in ("session.model_change", "session.start"):
            m = data.get("newModel") or data.get("selectedModel")
            if isinstance(m, str):
                state["model"] = m
            return []
        model = state["model"]
        if rtype in _IGNORED:
            return []
        if rtype == "user.message":
            return [text_item(SessionItem(Kind.USER_MESSAGE, timestamp=ts), blocks_text(data.get("content")))]
        if rtype == "assistant.message":
            m = data.get("model") if isinstance(data.get("model"), str) else model
            return [text_item(SessionItem(Kind.ASSISTANT_MESSAGE, timestamp=ts, model=m), blocks_text(data.get("content")))]
        if rtype in ("assistant.reasoning", "assistant.thinking"):
            return [SessionItem(Kind.REASONING_OMITTED, timestamp=ts, model=model)]
        if rtype == "tool.execution_start":
            args = data.get("arguments")
            return [SessionItem(Kind.TOOL_CALL, timestamp=ts, model=model, tool={
                "name": str(data.get("toolName"))[:100], "call_id": data.get("toolCallId"),
                "summary": summarize_args(args)}, files=files_from_args(args))]
        if rtype == "tool.execution_complete":
            result = data.get("result")
            text = blocks_text(result.get("content")) if isinstance(result, dict) else (result if isinstance(result, str) else "")
            return [text_item(SessionItem(Kind.TOOL_RESULT, timestamp=ts, tool={
                "name": None, "call_id": data.get("toolCallId"), "summary": ""}), text)]
        if rtype in ("subagent.started", "subagent.completed", "subagent.failed"):
            child = data.get("toolCallId") or data.get("agentId")
            label = f"{data.get('agentName') or data.get('agentDisplayName') or 'subagent'} ({rtype.split('.')[1]})"
            return [SessionItem(Kind.SUBAGENT, timestamp=ts, text=label[:200], child_id=str(child) if child else None)]
        if rtype == "session.compaction_complete":
            summary = data.get("summaryContent")
            return [text_item(SessionItem(Kind.COMPACTION, timestamp=ts), summary if isinstance(summary, str) else None)]
        if rtype == "system.message":
            return [text_item(SessionItem(Kind.SYSTEM, timestamp=ts), blocks_text(data.get("content")))]
        if rtype == "session.usage_record":
            return [SessionItem(Kind.USAGE, timestamp=ts, model=model, usage=_usage(data))]
        counter.unknown_type(rtype)
        return [SessionItem(Kind.UNKNOWN, timestamp=ts, model=model)]


    # ---- children (GUI2-T04): the subagent.* events, metadata only ----
    def _children_scan(self, events: Path) -> dict[str, dict[str, Any]]:
        """One entry per subagent (by ``toolCallId``/``agentId``) with its times, model, tokens and test calls."""
        def build() -> dict[str, dict[str, Any]]:
            kids: dict[str, dict[str, Any]] = {}
            tool_owner: dict[str, str] = {}

            def kid(child_id: str) -> dict[str, Any]:
                return kids.setdefault(child_id, {
                    "name": None, "label": None, "model": None, "started": None, "last": None, "ended": False,
                    "failed": False, "tokens": None, "open_tests": set()})

            for rec, _, status in iter_records(events):
                if rec is None or status != "ok":
                    continue
                ts = normalize_ts(rec.get("timestamp"))
                rtype = rec.get("type")
                data = rec.get("data") if isinstance(rec.get("data"), dict) else {}
                if isinstance(rtype, str) and rtype.startswith("subagent."):
                    child_id = safe_id(data.get("toolCallId")) or safe_id(data.get("agentId"))
                    if child_id is None:
                        continue
                    entry = kid(child_id)
                    entry["name"] = entry["name"] or clean_text(data.get("agentName"), MAX_NAME_CHARS)
                    entry["label"] = entry["label"] or clean_text(
                        data.get("agentDisplayName") or data.get("agentDescription"), MAX_LABEL_CHARS)
                    if isinstance(data.get("model"), str):
                        entry["model"] = clean_text(data["model"], MAX_NAME_CHARS) or entry["model"]
                    if ts:
                        entry["started"] = entry["started"] or ts
                        entry["last"] = ts
                    if rtype in ("subagent.completed", "subagent.failed"):
                        entry["ended"] = True
                        entry["failed"] = rtype == "subagent.failed"
                        total = int_or_none(data.get("totalTokens"))
                        if total is not None:
                            entry["tokens"] = TokenUsage(total=total)
                    continue
                owner = safe_id(data.get("parentToolCallId"))
                call_id = data.get("toolCallId")
                if rtype == "tool.execution_complete" and isinstance(call_id, str) and call_id in tool_owner:
                    kids[tool_owner.pop(call_id)]["open_tests"].discard(call_id)
                if owner in kids:
                    entry = kids[owner]
                    entry["last"] = ts or entry["last"]
                    if rtype == "tool.execution_start" and isinstance(call_id, str):
                        if is_test_call(data.get("arguments")):
                            entry["open_tests"].add(call_id)
                            tool_owner[call_id] = owner
                    elif rtype == "assistant.message" and isinstance(data.get("model"), str) and not entry["model"]:
                        entry["model"] = clean_text(data["model"], MAX_NAME_CHARS)
            return kids

        return self.index.get("copilot-children", events, build)

    def list_children(self, native_id: str, now: datetime, level_of: LevelOf) -> tuple[list[ChildAgent], list[str]]:
        """The subagents a session started. They have no conversation of their own to open (``link_id`` is None)."""
        d = self._dirs().get(native_id.lower()) if is_uuid(native_id) else None
        if d is None:
            raise SessionNotFound(native_id)
        events = d / "events.jsonl"
        if not events.is_file():
            return [], ["no_local_history"]
        out: list[ChildAgent] = []
        for child_id, entry in self._children_scan(events).items():
            last = entry["last"]
            status = derive_status(now=now, last_activity=last, finished=entry["ended"], failed=entry["failed"],
                                   testing=bool(entry["open_tests"]))
            out.append(ChildAgent(
                id=f"copilot:{native_id.lower()}#{child_id}", source="copilot", kind="subagent",
                name=entry["name"], label=entry["label"], model=entry["model"], level=level_of(entry["model"]),
                status=status, started_at=entry["started"], last_activity_at=last, tokens=entry["tokens"],
                link_id=None))
        return out, []


_KNOWN = {"user.message", "assistant.message", "assistant.reasoning", "assistant.thinking", "tool.execution_start",
          "tool.execution_complete", "subagent.started", "subagent.completed", "subagent.failed",
          "session.compaction_complete", "system.message", "session.usage_record"}


def _known(rtype: object) -> bool:
    return rtype in _KNOWN or rtype in _IGNORED


def _add(a: int | None, b: int | None) -> int | None:
    return b if a is None else (a if b is None else a + b)


def _usage(data: dict) -> TokenUsage | None:
    vals = (int_or_none(data.get("inputTokens")), int_or_none(data.get("outputTokens")),
            int_or_none(data.get("cacheReadTokens")), int_or_none(data.get("cacheWriteTokens")))
    return TokenUsage(*vals) if any(v is not None for v in vals) else None
