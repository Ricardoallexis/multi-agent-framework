# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Codex reader: rollout jsonl files plus the state_5.sqlite index (opened mode=ro)."""
from __future__ import annotations

import re
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
    parse_ts,
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

READER = "codex-rollout/1"
SEEN_VERSIONS = ("cli_version 0.x",)
_ROLLOUT_UUID = re.compile(r"rollout-.*?([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl")
_IGNORED_TOP = {"session_meta", "turn_context", "event_msg"}
_THREAD_COLS = ("id", "title", "cwd", "archived", "created_at", "updated_at", "tokens_used", "source",
                "rollout_path", "model", "agent_nickname", "agent_role")
_TURN_STARTS = {"task_started", "turn_started"}
_TURN_ENDS = {"task_complete", "turn_complete", "turn_aborted", "shutdown_complete"}
_CALLS = {"function_call", "custom_tool_call", "local_shell_call"}
_OUTPUTS = {"function_call_output", "custom_tool_call_output", "local_shell_call_output"}


def _surface(*hints: object) -> Surface:
    text = " ".join(str(h).lower() for h in hints if h)
    if "desktop" in text or "vscode" in text:
        return Surface.DESKTOP
    if "cli" in text or "exec" in text:
        return Surface.CLI
    return Surface.UNKNOWN


class CodexReader:
    source = Source.CODEX

    def __init__(self, config: SessionsConfig, index: MetaIndex):
        self.config = config
        self.index = index

    # ---- discovery ----
    def _rollouts(self) -> dict[str, Path]:
        home = self.config.codex_home
        out: dict[str, Path] = {}
        if home is None:
            return out
        for sub in ("sessions", "archived_sessions"):
            base = home / sub
            if base.is_dir():
                for f in base.rglob("rollout-*.jsonl"):
                    m = _ROLLOUT_UUID.fullmatch(f.name)
                    if m:
                        out.setdefault(m.group(1).lower(), f)
        return out

    def _threads(self) -> tuple[dict[str, dict], dict[str, list[str]], list[str]]:
        """Rows of ``threads`` by id, parent -> children, and warnings (locked/missing db)."""
        db = self.config.codex_home / "state_5.sqlite" if self.config.codex_home else None
        if db is None or not db.is_file():
            return {}, {}, []
        try:
            conn = open_ro(db)
            try:
                rows = select_rows(conn, "threads", _THREAD_COLS)
                edges = select_rows(conn, "thread_spawn_edges", ("parent_thread_id", "child_thread_id"))
            finally:
                conn.close()
        except sqlite3.Error as exc:
            return {}, {}, [f"source_unavailable: codex index ({type(exc).__name__}: {str(exc)[:80]})"]
        threads = {str(r["id"]).lower(): r for r in rows if is_uuid(r.get("id"))}
        children: dict[str, list[str]] = {}
        for e in edges:
            if is_uuid(e.get("parent_thread_id")) and is_uuid(e.get("child_thread_id")):
                children.setdefault(str(e["parent_thread_id"]).lower(), []).append(str(e["child_thread_id"]).lower())
        return threads, children, []

    # ---- scan a rollout ----
    def _scan(self, path: Path) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            info: dict[str, Any] = {"meta": {}, "models": [], "first": None, "last": None, "tokens": None}
            counter = WarningCounter()
            for rec, _, status in iter_records(path):
                counter.line(status)
                if rec is None:
                    continue
                ts = normalize_ts(rec.get("timestamp"))
                if ts:
                    info["first"] = info["first"] or ts
                    info["last"] = ts
                rtype = rec.get("type")
                payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
                if rtype == "session_meta" and not info["meta"]:
                    info["meta"] = payload
                elif rtype == "turn_context":
                    model = payload.get("model")
                    if isinstance(model, str) and model not in info["models"]:
                        info["models"].append(model)
                elif rtype == "event_msg" and payload.get("type") == "token_count":
                    usage = (payload.get("info") or {}).get("total_token_usage") if isinstance(payload.get("info"), dict) else None
                    if isinstance(usage, dict):
                        info["tokens"] = _tokens(usage)
                elif rtype not in ("response_item", "compacted", *_IGNORED_TOP):
                    counter.unknown_type(rtype)
            info["warnings"] = counter.render()
            return info

        return self.index.get("codex", path, build)

    def _summary(self, native: str, row: dict | None, path: Path | None,
                 children: dict[str, list[str]], parent: str | None, extra: list[str]) -> SessionSummary:
        info = self._scan(path) if path is not None else {"meta": {}, "models": [], "first": None,
                                                           "last": None, "tokens": None, "warnings": []}
        meta = info["meta"]
        row = row or {}
        models = list(info["models"])
        if isinstance(row.get("model"), str) and row["model"] not in models:
            models.append(row["model"])
        tokens = info["tokens"]
        if tokens is None and int_or_none(row.get("tokens_used")) is not None:
            tokens = TokenUsage(total=row["tokens_used"])
        created = normalize_ts(row.get("created_at")) or normalize_ts(meta.get("timestamp")) or info["first"]
        updated = normalize_ts(row.get("updated_at")) or info["last"] or (mtime_ts(path) if path else None)
        archived = bool(row.get("archived")) or (path is not None and "archived_sessions" in path.parts)
        status = Status.ARCHIVED if archived else (Status.ACTIVE if is_recent(updated) else Status.IDLE)
        title = row.get("title") if isinstance(row.get("title"), str) and row.get("title") else None
        warnings = list(extra) + list(info["warnings"])
        if path is None:
            warnings.append("no_local_history")
        return SessionSummary(
            id=f"codex:{native}", source=Source.CODEX,
            surface=_surface(row.get("source"), meta.get("originator"), meta.get("source")),
            native_id=native, title=title, cwd=clean_cwd(row.get("cwd")) or clean_cwd(meta.get("cwd")),
            created_at=created, updated_at=updated, models=models, status=status,
            parent_id=f"codex:{parent}" if parent else None, child_count=len(children.get(native, [])),
            tokens=tokens, open_in_app=OpenInApp("url", f"codex://threads/{native}", False) if is_uuid(native) else None,
            reader=READER, warnings=warnings)

    # ---- public ----
    def list_summaries(self) -> tuple[list[SessionSummary], list[str]]:
        home = self.config.codex_home
        if home is None or not home.is_dir():
            return [], ["source_unavailable: codex store not found"]
        threads, children, warns = self._threads()
        parents = {c: p for p, kids in children.items() for c in kids}
        rollouts = self._rollouts()
        out = [self._summary(n, threads.get(n), rollouts.get(n), children, parents.get(n), warns)
               for n in sorted(set(threads) | set(rollouts))]
        return out, warns

    def get_summary(self, native_id: str) -> SessionSummary:
        if not is_uuid(native_id):
            raise SessionNotFound(native_id)
        native = native_id.lower()
        threads, children, warns = self._threads()
        path = self._rollouts().get(native)
        if native not in threads and path is None:
            raise SessionNotFound(native_id)
        parents = {c: p for p, kids in children.items() for c in kids}
        return self._summary(native, threads.get(native), path, children, parents.get(native), warns)

    def read_items(self, native_id: str, after: str | None, limit: int) -> ItemsPage:
        if not is_uuid(native_id):
            raise SessionNotFound(native_id)
        path = self._rollouts().get(native_id.lower())
        if path is None:
            self.get_summary(native_id)  # raises SessionNotFound when the thread is unknown too
            return ItemsPage([], "0:0", True, ["no_local_history"])
        state = {"model": None}

        def parse(rec: dict, counter: WarningCounter) -> list[SessionItem]:
            return self._parse(rec, counter, state)

        return read_page(path, after, limit, parse)

    # ---- record -> items ----
    def _parse(self, rec: dict, counter: WarningCounter, state: dict) -> list[SessionItem]:
        rtype = rec.get("type")
        ts = normalize_ts(rec.get("timestamp"))
        payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
        if rtype == "turn_context":
            if isinstance(payload.get("model"), str):
                state["model"] = payload["model"]
            return []
        if rtype == "session_meta":
            return []
        if rtype == "event_msg":
            if payload.get("type") == "token_count" and isinstance(payload.get("info"), dict):
                usage = payload["info"].get("total_token_usage")
                if isinstance(usage, dict):
                    return [SessionItem(Kind.USAGE, timestamp=ts, model=state["model"], usage=_tokens(usage))]
            return []
        if rtype == "compacted":
            msg = payload.get("message")
            return [text_item(SessionItem(Kind.COMPACTION, timestamp=ts), msg if isinstance(msg, str) else None)]
        if rtype != "response_item":
            counter.unknown_type(rtype)
            return [SessionItem(Kind.UNKNOWN, timestamp=ts)]
        ptype = payload.get("type")
        model = state["model"]
        if ptype == "message":
            role = payload.get("role")
            kind = {"user": Kind.USER_MESSAGE, "assistant": Kind.ASSISTANT_MESSAGE}.get(role, Kind.SYSTEM)
            return [text_item(SessionItem(kind, timestamp=ts, model=model), blocks_text(payload.get("content")))]
        if ptype == "reasoning":
            return [SessionItem(Kind.REASONING_OMITTED, timestamp=ts, model=model)]
        if ptype in ("function_call", "custom_tool_call", "local_shell_call", "web_search_call"):
            args = payload.get("arguments", payload.get("input", payload.get("action")))
            name = payload.get("name") or ptype
            return [SessionItem(Kind.TOOL_CALL, timestamp=ts, model=model,
                                tool={"name": str(name)[:100], "call_id": payload.get("call_id"),
                                      "summary": summarize_args(args)}, files=files_from_args(args))]
        if ptype in ("function_call_output", "custom_tool_call_output"):
            out = payload.get("output")
            text = out if isinstance(out, str) else (out.get("output") if isinstance(out, dict) and isinstance(out.get("output"), str) else blocks_text(out))
            return [text_item(SessionItem(Kind.TOOL_RESULT, timestamp=ts, tool={
                "name": None, "call_id": payload.get("call_id"), "summary": ""}), text)]
        counter.unknown_type(f"response_item:{ptype}")
        return [SessionItem(Kind.UNKNOWN, timestamp=ts, model=model)]


    # ---- children (GUI2-T04): metadata only, never the messages ----
    def _edge_status(self) -> dict[str, str]:
        """``child thread id -> edge status`` ('open'/'closed'); empty when the index has no such column."""
        db = self.config.codex_home / "state_5.sqlite" if self.config.codex_home else None
        if db is None or not db.is_file():
            return {}
        try:
            conn = open_ro(db)
            try:
                rows = select_rows(conn, "thread_spawn_edges", ("child_thread_id", "status"))
            finally:
                conn.close()
        except sqlite3.Error:
            return {}
        return {str(r["child_thread_id"]).lower(): str(r["status"]).lower() for r in rows
                if is_uuid(r.get("child_thread_id")) and r.get("status") is not None}

    def _child_scan(self, path: Path) -> dict[str, Any]:
        """Times, models, tokens, in-flight test calls and whether the last turn ended (cached)."""
        def build() -> dict[str, Any]:
            info: dict[str, Any] = {"first": None, "last": None, "models": [], "tokens": None,
                                    "open_tests": set(), "turn_ended": False}
            for rec, _, status in iter_records(path):
                if rec is None or status != "ok":
                    continue
                ts = normalize_ts(rec.get("timestamp"))
                if ts:
                    info["first"] = info["first"] or ts
                    info["last"] = ts
                rtype = rec.get("type")
                payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
                ptype = payload.get("type")
                if rtype == "turn_context" and isinstance(payload.get("model"), str):
                    if payload["model"] not in info["models"]:
                        info["models"].append(payload["model"])
                elif rtype == "event_msg":
                    if ptype == "token_count" and isinstance(payload.get("info"), dict):
                        usage = payload["info"].get("total_token_usage")
                        if isinstance(usage, dict):
                            info["tokens"] = _tokens(usage)
                    elif ptype in _TURN_STARTS:
                        info["turn_ended"] = False
                    elif ptype in _TURN_ENDS:
                        info["turn_ended"] = True
                elif rtype == "response_item":
                    call_id = payload.get("call_id")
                    if not isinstance(call_id, str):
                        continue
                    if ptype in _CALLS and is_test_call(payload.get("arguments", payload.get("input", payload.get("action")))):
                        info["open_tests"].add(call_id)
                    elif ptype in _OUTPUTS:
                        info["open_tests"].discard(call_id)
            return info

        return self.index.get("codex-child", path, build)

    def list_children(self, native_id: str, now: datetime, level_of: LevelOf) -> tuple[list[ChildAgent], list[str]]:
        """The threads spawned by a thread (``thread_spawn_edges``), with their rollout activity."""
        if not is_uuid(native_id):
            raise SessionNotFound(native_id)
        native = native_id.lower()
        threads, children, warns = self._threads()
        rollouts = self._rollouts()
        if native not in threads and native not in rollouts:
            raise SessionNotFound(native_id)
        edges = self._edge_status()
        out: list[ChildAgent] = []
        for child in dict.fromkeys(children.get(native, [])):
            row = threads.get(child) or {}
            path = rollouts.get(child)
            info = self._child_scan(path) if path is not None else {
                "first": None, "last": None, "models": [], "tokens": None, "open_tests": set(), "turn_ended": False}
            row_updated = normalize_ts(row.get("updated_at"))
            last = max(filter(None, (info["last"], row_updated)), key=parse_ts, default=None)
            started = normalize_ts(row.get("created_at")) or info["first"]
            closed = edges.get(child) == "closed" or bool(row.get("archived"))
            status = derive_status(now=now, last_activity=last, finished=closed or info["turn_ended"],
                                   testing=bool(info["open_tests"]))
            model = clean_text(row.get("model"), MAX_NAME_CHARS) or (info["models"][-1] if info["models"] else None)
            # The nickname and role Codex gave the thread name it; its title comes from the first message, so
            # it is only a last resort when the thread has neither.
            who = [text for text in (clean_text(row.get("agent_nickname"), MAX_NAME_CHARS),
                                     clean_text(row.get("agent_role"), MAX_NAME_CHARS)) if text]
            tokens = info["tokens"]
            if tokens is None and int_or_none(row.get("tokens_used")) is not None:
                tokens = TokenUsage(total=row["tokens_used"])
            out.append(ChildAgent(
                id=f"codex:{child}", source="codex", kind="thread", name=" · ".join(who) or None,
                label=None if who else clean_text(row.get("title"), MAX_LABEL_CHARS), model=model,
                level=level_of(model), status=status, started_at=started, last_activity_at=last, tokens=tokens,
                link_id=f"codex:{child}"))
        return out, list(warns)


def _tokens(usage: dict) -> TokenUsage:
    return TokenUsage(input=int_or_none(usage.get("input_tokens")), output=int_or_none(usage.get("output_tokens")),
                      cache_read=int_or_none(usage.get("cached_input_tokens")), cache_write=None,
                      total=int_or_none(usage.get("total_tokens")))
