# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Claude Code reader (CLI jsonl + Desktop local_*.json metadata), read-only."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._common import (
    ACTIVE_WINDOW_SECONDS,
    JsonlFold,
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
    read_page,
    summarize_args,
    text_item,
)
from .children import (
    MAX_LABEL_CHARS,
    MAX_NAME_CHARS,
    ChildAgent,
    LevelOf,
    age_seconds,
    clean_text,
    derive_status,
    is_test_call,
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

READER = "claude-jsonl/1"
SEEN_VERSIONS = ("2.x",)  # `version` field of the CLI records
_SUBAGENT_RE = re.compile(r"agent-[A-Za-z0-9_-]{1,64}")
MAX_CHILD_META_BYTES = 64 * 1024  # agent-<id>.meta.json is a few lines
# Internal bookkeeping records: ignored without a warning (contract section 3).
_IGNORED = {"file-history-snapshot", "queue-operation", "cost-state", "custom-title", "agent-name",
            "last-prompt", "permission-mode", "mode", "pr-link", "progress", "attachment", "ai-title",
            "tag", "summary-meta"}


def _split_native(native_id: str) -> tuple[str, str | None]:
    """``<uuid>`` or ``<uuid>:agent-<id>`` -> (session uuid, subagent id | None)."""
    session, _, agent = native_id.partition(":")
    if not is_uuid(session) or (agent and not _SUBAGENT_RE.fullmatch(agent)):
        raise SessionNotFound(native_id)
    return session, agent or None


def _usage_from(message: dict) -> TokenUsage | None:
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    vals = (int_or_none(usage.get("input_tokens")), int_or_none(usage.get("output_tokens")),
            int_or_none(usage.get("cache_read_input_tokens")), int_or_none(usage.get("cache_creation_input_tokens")))
    return TokenUsage(*vals) if any(v is not None for v in vals) else None


class _SessionFold(JsonlFold):
    """Row fields of one transcript: head (cwd, entrypoint, first time), tail (last time, titles) and the
    models, usage and warnings counted over every record.

    The whole file is read once because the row shows the models, the summed usage and the warning counters,
    and those come from records anywhere in it; after that only the bytes appended since are folded in
    (``MetaIndex.scan_jsonl``), so a growing transcript is never read twice.
    """

    def __init__(self) -> None:
        self.info: dict[str, Any] = {"cwd": None, "entrypoint": None, "models": [], "first": None, "last": None,
                                     "custom": None, "agent": None, "usage": {}, "version": None}
        self.counter = WarningCounter()

    def copy(self) -> _SessionFold:
        other = _SessionFold()
        other.info = {**self.info, "models": list(self.info["models"]), "usage": dict(self.info["usage"])}
        other.counter = self.counter.copy()
        return other

    def add(self, rec: dict | None, status: str) -> None:
        info = self.info
        self.counter.line(status)
        if rec is None:
            return
        rtype = rec.get("type")
        ts = normalize_ts(rec.get("timestamp"))
        if ts:
            info["first"] = info["first"] or ts
            info["last"] = ts
        if info["cwd"] is None:
            info["cwd"] = clean_cwd(rec.get("cwd"))
        info["entrypoint"] = info["entrypoint"] or rec.get("entrypoint")
        info["version"] = info["version"] or rec.get("version")
        if rtype == "custom-title" and isinstance(rec.get("customTitle"), str):
            info["custom"] = rec["customTitle"]
        elif rtype == "agent-name" and isinstance(rec.get("agentName"), str):
            info["agent"] = rec["agentName"]
        elif rtype == "assistant" and isinstance(rec.get("message"), dict):
            msg = rec["message"]
            model = msg.get("model")
            if isinstance(model, str) and model != "<synthetic>" and model not in info["models"]:
                info["models"].append(model)
            usage = _usage_from(msg)
            if usage is not None:
                info["usage"][msg.get("id") or f"anon{len(info['usage'])}"] = usage
        elif rtype not in ("user", "system", "summary") and rtype not in _IGNORED:
            self.counter.unknown_type(rtype)

    def result(self) -> dict[str, Any]:
        self.info["warnings"] = self.counter.render()
        return self.info


class _ChildFold(JsonlFold):
    """Times, models, tokens and in-flight test calls of one subagent transcript.

    Whole file once, then only the appended bytes: the models, the summed usage and the test calls still
    open are state built from every record, not from the head or the tail alone.
    """

    def __init__(self) -> None:
        self.info: dict[str, Any] = {"first": None, "last": None, "models": [], "usage": {}, "open_tests": set(),
                                     "last_kind": None, "last_stop": None}

    def copy(self) -> _ChildFold:
        other = _ChildFold()
        other.info = {**self.info, "models": list(self.info["models"]), "usage": dict(self.info["usage"]),
                      "open_tests": set(self.info["open_tests"])}
        return other

    def add(self, rec: dict | None, status: str) -> None:
        if rec is None or status != "ok":
            return
        info = self.info
        ts = normalize_ts(rec.get("timestamp"))
        if ts:
            info["first"] = info["first"] or ts
            info["last"] = ts
        msg = rec.get("message") if isinstance(rec.get("message"), dict) else None
        if msg is None:
            return
        parts = msg.get("content") if isinstance(msg.get("content"), list) else []
        if rec.get("type") == "assistant":
            model = msg.get("model")
            if isinstance(model, str) and model != "<synthetic>" and model not in info["models"]:
                info["models"].append(model)
            usage = _usage_from(msg)
            if usage is not None:
                info["usage"][msg.get("id") or f"anon{len(info['usage'])}"] = usage
            kinds = {p.get("type") for p in parts if isinstance(p, dict)}
            for part in parts:
                if (isinstance(part, dict) and part.get("type") == "tool_use"
                        and isinstance(part.get("id"), str) and is_test_call(part.get("input"))):
                    info["open_tests"].add(part["id"])
            if "tool_use" in kinds:
                info["last_kind"] = "assistant_tool"
            elif "text" in kinds or isinstance(msg.get("content"), str):
                info["last_kind"] = "assistant_text"
            else:
                info["last_kind"] = "assistant_other"
            info["last_stop"] = msg.get("stop_reason")
        elif rec.get("type") == "user":
            for part in parts:
                if (isinstance(part, dict) and part.get("type") == "tool_result"
                        and isinstance(part.get("tool_use_id"), str)):
                    info["open_tests"].discard(part["tool_use_id"])
            info["last_kind"] = "user"

    def result(self) -> dict[str, Any]:
        return self.info


def _first_cwd(path: Path) -> str | None:
    """Working folder of a transcript: the first record that has one. Reads from the head and stops there."""
    records = iter_records(path)
    try:
        for rec, _, _ in records:
            if rec is not None and (cwd := clean_cwd(rec.get("cwd"))) is not None:
                return cwd
    finally:
        records.close()
    return None


class ClaudeReader:
    source = Source.CLAUDE

    def __init__(self, config: SessionsConfig, index: MetaIndex):
        self.config = config
        self.index = index

    # ---- discovery ----
    def _project_files(self) -> dict[str, Path]:
        home = self.config.claude_home
        out: dict[str, Path] = {}
        if home is None or not (home / "projects").is_dir():
            return out
        for pdir in sorted((home / "projects").iterdir()):
            if not pdir.is_dir():
                continue
            for f in pdir.glob("*.jsonl"):
                if is_uuid(f.stem):
                    out.setdefault(f.stem.lower(), f)
        return out

    def _subagent_files(self, main: Path) -> dict[str, Path]:
        sub = main.parent / main.stem / "subagents"
        if not sub.is_dir():
            return {}
        return {f.stem: f for f in sorted(sub.glob("agent-*.jsonl")) if _SUBAGENT_RE.fullmatch(f.stem)}

    def _desktop_meta(self) -> dict[str, dict]:
        base = self.config.claude_desktop_dir
        out: dict[str, dict] = {}
        if base is None or not base.is_dir():
            return out
        for f in base.rglob("local_*.json"):
            def build(path: Path = f) -> dict:
                try:
                    if path.stat().st_size > 2_000_000:
                        return {}
                    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
                    return data if isinstance(data, dict) else {}
                except (OSError, ValueError):
                    return {}
            data = self.index.get("claude-desktop", f, build)
            if not isinstance(data, dict):
                continue
            ids = [data.get("cliSessionId")] + list(data.get("priorCliSessionIds") or [])
            for sid in ids:
                if is_uuid(sid):
                    out.setdefault(sid.lower(), data)
        return out

    # ---- scan one jsonl (metadata only, cached by path+mtime+size, continued when the file only grew) ----
    def _scan(self, path: Path) -> dict[str, Any]:
        return self.index.scan_jsonl("claude", path, _SessionFold)

    @staticmethod
    def _tokens(info: dict[str, Any]) -> TokenUsage | None:
        usages = list(info["usage"].values())
        if not usages:
            return None

        def total(attr: str) -> int | None:
            vals = [getattr(u, attr) for u in usages if getattr(u, attr) is not None]
            return sum(vals) if vals else None

        return TokenUsage(total("input"), total("output"), total("cache_read"), total("cache_write"))

    def _summary(self, session: str, path: Path, desktop: dict | None, children: int,
                 parent_info: dict | None = None, agent: str | None = None) -> SessionSummary:
        info = self._scan(path)
        entry = info["entrypoint"] or ""
        if desktop is not None or entry == "claude-desktop":
            surface = Surface.DESKTOP
        elif entry in ("sdk-cli", "cli"):
            surface = Surface.CLI
        else:
            surface = Surface.UNKNOWN
        title = None
        models = list(info["models"])
        if desktop is not None:
            title = desktop.get("title") if isinstance(desktop.get("title"), str) else None
            if isinstance(desktop.get("model"), str) and desktop["model"] not in models:
                models.append(desktop["model"])
        title = title or info["custom"] or info["agent"]
        updated = info["last"] or mtime_ts(path)
        if desktop is not None and desktop.get("isArchived") is True:
            status = Status.ARCHIVED
        else:
            status = Status.ACTIVE if is_recent(updated) else Status.IDLE
        native = session if agent is None else f"{session}:{agent}"
        cwd = info["cwd"] or (parent_info or {}).get("cwd")
        open_in = None
        if agent is None and is_uuid(session):
            open_in = OpenInApp("command", ["claude", "--desktop", "--resume", session], False)
        return SessionSummary(
            id=f"claude:{native}", source=Source.CLAUDE, surface=surface, native_id=native, title=title,
            cwd=cwd, created_at=info["first"] or mtime_ts(path), updated_at=updated, models=models,
            status=status, parent_id=None if agent is None else f"claude:{session}", child_count=children,
            tokens=self._tokens(info), open_in_app=open_in, reader=READER, warnings=list(info["warnings"]))

    # ---- public ----
    def list_summaries(self) -> tuple[list[SessionSummary], list[str]]:
        files = self._project_files()
        if self.config.claude_home is None or not (self.config.claude_home / "projects").is_dir():
            return [], ["source_unavailable: claude store not found"]
        desktop = self._desktop_meta()
        out: list[SessionSummary] = []
        for session, path in files.items():
            subs = self._subagent_files(path)
            main = self._summary(session, path, desktop.get(session), len(subs))
            out.append(main)
            parent_info = self._scan(path)
            for agent, apath in subs.items():
                out.append(self._summary(session, apath, desktop.get(session), 0, parent_info, agent))
        return out, []

    def _resolve(self, native_id: str) -> tuple[str, str | None, Path, Path]:
        """``(session, subagent id | None, file of the id, main transcript of the session)``."""
        session, agent = _split_native(native_id)
        main = self._project_files().get(session.lower())
        if main is None:
            raise SessionNotFound(native_id)
        if agent is None:
            return session, None, main, main
        sub = self._subagent_files(main).get(agent)
        if sub is None:
            raise SessionNotFound(native_id)
        return session, agent, sub, main

    def _locate(self, native_id: str) -> tuple[str, str | None, Path]:
        session, agent, path, _ = self._resolve(native_id)
        return session, agent, path

    def _parent_cwd(self, main: Path) -> str | None:
        """Working folder a subagent inherits from its session: it sits in the first records, so the head is enough."""
        scanned = self.index.peek("claude", main)
        if scanned is not None:  # the whole-file scan is already cached: free
            return scanned["cwd"]
        return self.index.get("claude-cwd", main, lambda: _first_cwd(main))

    def get_summary(self, native_id: str) -> SessionSummary:
        session, agent, path, main = self._resolve(native_id)
        desktop = self._desktop_meta().get(session.lower())
        if agent is None:
            return self._summary(session, path, desktop, len(self._subagent_files(main)))
        return self._summary(session, path, desktop, 0, {"cwd": self._parent_cwd(main)}, agent)


    def activity(self, native_id: str) -> dict[str, Any]:
        """Allowlisted live activity; never return transcript contents or paths."""
        _, _, path, _ = self._resolve(native_id)
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        except OSError:
            modified = None
        info = self.index.scan_jsonl("claude-activity", path, _ChildFold)
        return {"last_output_at": modified, "open_tests": bool(info.get("open_tests")),
                "source": "claude_transcript"}
    def read_items(self, native_id: str, after: str | None, limit: int) -> ItemsPage:
        _, _, path = self._locate(native_id)
        return read_page(path, after, limit, self._parse)

    # ---- record -> items ----
    def _parse(self, rec: dict, counter: WarningCounter) -> list[SessionItem]:
        rtype = rec.get("type")
        ts = normalize_ts(rec.get("timestamp"))
        if rtype in _IGNORED:
            return []
        if rtype == "summary":
            return [text_item(SessionItem(Kind.COMPACTION, timestamp=ts), rec.get("summary") if isinstance(rec.get("summary"), str) else None)]
        if rtype == "system":
            content = rec.get("content")
            kind = Kind.COMPACTION if rec.get("subtype") == "compact_boundary" else Kind.SYSTEM
            return [text_item(SessionItem(kind, timestamp=ts), content if isinstance(content, str) else None)]
        if rtype not in ("user", "assistant"):
            counter.unknown_type(rtype)
            return [SessionItem(Kind.UNKNOWN, timestamp=ts)]
        msg = rec.get("message")
        if not isinstance(msg, dict):
            counter.unknown_type(f"{rtype}:no-message")
            return [SessionItem(Kind.UNKNOWN, timestamp=ts)]
        model = msg.get("model") if isinstance(msg.get("model"), str) and msg.get("model") != "<synthetic>" else None
        content = msg.get("content")
        role_kind = Kind.USER_MESSAGE if rtype == "user" else Kind.ASSISTANT_MESSAGE
        if rec.get("isCompactSummary") is True:
            role_kind = Kind.COMPACTION
        parts = [{"type": "text", "text": content}] if isinstance(content, str) else (content if isinstance(content, list) else [])
        items: list[SessionItem] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype == "text":
                items.append(text_item(SessionItem(role_kind, timestamp=ts, model=model), part.get("text") if isinstance(part.get("text"), str) else ""))
            elif ptype in ("thinking", "redacted_thinking"):
                items.append(SessionItem(Kind.REASONING_OMITTED, timestamp=ts, model=model))
            elif ptype == "tool_use":
                args = part.get("input")
                items.append(SessionItem(
                    Kind.TOOL_CALL, timestamp=ts, model=model,
                    tool={"name": str(part.get("name"))[:100], "call_id": part.get("id"), "summary": summarize_args(args)},
                    files=files_from_args(args)))
            elif ptype == "tool_result":
                item = text_item(SessionItem(Kind.TOOL_RESULT, timestamp=ts, tool={
                    "name": None, "call_id": part.get("tool_use_id"), "summary": ""}), blocks_text(part.get("content")))
                items.append(item)
                result = rec.get("toolUseResult")
                agent_id = result.get("agentId") if isinstance(result, dict) else None
                if isinstance(agent_id, str) and _SUBAGENT_RE.fullmatch(f"agent-{agent_id}" if not agent_id.startswith("agent-") else agent_id):
                    aid = agent_id if agent_id.startswith("agent-") else f"agent-{agent_id}"
                    sess = rec.get("sessionId")
                    child = f"claude:{sess}:{aid}" if is_uuid(sess) else None
                    items.append(SessionItem(Kind.SUBAGENT, timestamp=ts, child_id=child, text=aid))
            elif ptype in ("image", "document"):
                items.append(SessionItem(role_kind, timestamp=ts, model=model, text=f"[{ptype}]"))
            else:
                counter.unknown_type(f"content:{ptype}")
                items.append(SessionItem(Kind.UNKNOWN, timestamp=ts, model=model))
        usage = _usage_from(msg) if rtype == "assistant" else None
        if usage is not None and items:
            items[0].usage = usage
        return items

    # ---- children (GUI2-T04): metadata only, never the messages ----
    def _child_scan(self, path: Path) -> dict[str, Any]:
        """Times, models, tokens and in-flight test calls of one subagent file (cached by path+mtime+size)."""
        return self.index.scan_jsonl("claude-child", path, _ChildFold)

    def _child_meta(self, path: Path) -> dict[str, str | None]:
        """Agent type, one-line description and model alias from ``agent-<id>.meta.json`` (all optional).

        Cached by path+mtime+size like the transcripts: the children list is polled, and this file never
        changes once written.
        """
        meta = path.with_suffix(".meta.json")

        def build() -> dict[str, str | None]:
            try:
                if meta.stat().st_size > MAX_CHILD_META_BYTES:
                    return {}
                data = json.loads(meta.read_text(encoding="utf-8", errors="replace"))
            except (OSError, ValueError):
                return {}
            if not isinstance(data, dict):
                return {}
            return {"name": clean_text(data.get("agentType"), MAX_NAME_CHARS),
                    "label": clean_text(data.get("description"), MAX_LABEL_CHARS),
                    "model": clean_text(data.get("model"), MAX_NAME_CHARS)}

        return self.index.get("claude-child-meta", meta, build)

    def list_children(self, native_id: str, now: datetime, level_of: LevelOf) -> tuple[list[ChildAgent], list[str]]:
        """The subagents of a session (``agent-*.jsonl`` files). A subagent has no children of its own."""
        session, agent, main = self._locate(native_id)
        if agent is not None:
            return [], []
        out: list[ChildAgent] = []
        for aid, path in self._subagent_files(main).items():
            info = self._child_scan(path)
            meta = self._child_meta(path)
            last = info["last"] or mtime_ts(path)
            quiet = age_seconds(last, now)
            # The final answer is a text-only assistant record with nothing after it; a streamed record that
            # is still being completed has the same shape, so a quiet moment (or end_turn) confirms it.
            finished = info["last_kind"] == "assistant_text" and (
                info["last_stop"] == "end_turn" or (quiet is not None and quiet >= ACTIVE_WINDOW_SECONDS))
            status = derive_status(now=now, last_activity=last, finished=finished, testing=bool(info["open_tests"]))
            model = info["models"][-1] if info["models"] else meta.get("model")
            out.append(ChildAgent(
                id=f"claude:{session}:{aid}", source="claude", kind="subagent", name=meta.get("name"),
                label=meta.get("label"), model=model, level=level_of(model), status=status,
                started_at=info["first"] or mtime_ts(path), last_activity_at=last, tokens=self._tokens(info),
                link_id=f"claude:{session}:{aid}"))
        return out, []
