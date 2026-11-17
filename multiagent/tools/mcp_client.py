"""MCP client behind the tool registry (CAP-T12). Needs the optional extra ``mcp`` only when a server is started.

Rules (decided by the orchestrator, not configurable from the server side):

* stdio only, and only servers the operator configures in code or config (command, arguments, alias, environment).
  Nothing a model, a tool or a document says can add a server.
* The subprocess gets the SDK's minimal default environment plus what the operator declares. Starting, listing
  and every call have a time limit, and the process is closed on ``close()``.
* The client offers no capabilities: no sampling, no roots, no elicitation. The server cannot ask for our models or
  our files.
* Tools are registered with ``registry.scoped(alias)``, ``declared_by="adapter"`` and ``invocation=remote``. The
  impact starts at the maximum (destructive). The server's hints can only raise it; lowering needs the operator's
  per-tool entry in ``McpServerConfig.impact_overrides``. The server's description is untrusted data (bounded, never
  read for permissions).
* Output goes back through ``ToolInvoker`` (so it is untrusted data for ``trust.py``) with a size cap. A failure of
  the server becomes an executor error whose message is exactly a stable code (``mcp_timeout``, ...): the invoker
  records only its hash, so no local path or server text is kept.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from multiagent.document_actions import ActionKind
from multiagent.document_trust import SourceKind, derive, wrap_untrusted
from multiagent.tasks.envelopes import canonical_json, envelope_sha256
from .contracts import EffectDecl, ImpactClass, InvocationKind, SchemaRef, ToolCallContext, ToolSpec
from .registry import ToolRegistrationError, ToolRegistry

log = logging.getLogger("multiagent.tools.mcp_client")

MAX_TOOLS = 50
MAX_DESCRIPTION_CHARS = 500
MAX_SCHEMA_BYTES = 16_384
MAX_OUTPUT_CHARS = 32_000
MAX_LIST_PAGES = 5
_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}\Z")
_ALIAS = re.compile(r"[a-z][a-z0-9_]{0,39}\Z")
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2066-\u2069\ufeff\U000e0000-\U000e007f]+")
_PROP = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}\Z")
MAX_SCHEMA_DEPTH = 8
MAX_PROPERTIES = 64
MAX_ENUM = 50
_LEVELS = {"read": 0, "write": 1, "destructive": 2}
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"text": {"type": "string"}, "truncated": {"type": "boolean"}},
    "required": ["text", "truncated"], "additionalProperties": False}


class McpClientError(RuntimeError):
    """Stable failure. ``str(error)`` is exactly ``code``; nothing from the server or the machine is added."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class McpServerConfig:
    """A server chosen by the operator. Build it in code or with :meth:`from_mapping`, never from model text."""
    alias: str
    command: str
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: str | None = None
    # Server tool name -> "read" | "write" (the only way to lower the impact; hints can still raise it back).
    impact_overrides: Mapping[str, str] = field(default_factory=dict)
    # Exact server tool name -> description written by the operator; otherwise the server text goes in as untrusted.
    descriptions: Mapping[str, str] = field(default_factory=dict)
    # The server reaches outside the machine: its tools are external (needs allowed_data_classes to be callable).
    external: bool = False
    allowed_data_classes: frozenset[str] | None = None
    max_tools: int = MAX_TOOLS
    startup_timeout_s: float = 20.0
    list_timeout_s: float = 20.0
    call_timeout_s: float = 30.0
    max_output_chars: int = MAX_OUTPUT_CHARS

    def __post_init__(self) -> None:
        if not isinstance(self.alias, str) or not _ALIAS.fullmatch(self.alias):
            raise ValueError("Invalid MCP alias")
        if not isinstance(self.command, str) or not self.command.strip():
            raise ValueError("MCP command required")
        if not all(isinstance(a, str) for a in self.args):
            raise ValueError("MCP arguments must be strings")
        if not all(isinstance(k, str) and isinstance(v, str) and k and "=" not in k for k, v in self.env.items()):
            raise ValueError("MCP environment must map strings to strings")
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))
        object.__setattr__(self, "impact_overrides", MappingProxyType(dict(self.impact_overrides)))
        object.__setattr__(self, "descriptions", MappingProxyType(dict(self.descriptions)))
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in self.descriptions.items()):
            raise ValueError("MCP descriptions must map strings to strings")
        if not isinstance(self.external, bool):
            raise ValueError("external must be a bool")
        for level in self.impact_overrides.values():
            if level not in ("read", "write"):
                raise ValueError("An override can only lower the impact to read or write")
        if not 1 <= self.max_tools <= MAX_TOOLS or not 1 <= self.max_output_chars <= 40_000:
            raise ValueError("MCP limits out of range")
        for seconds in (self.startup_timeout_s, self.list_timeout_s, self.call_timeout_s):
            if not 0 < seconds <= 600:
                raise ValueError("MCP time limits out of range")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "McpServerConfig":
        allowed = {"alias", "command", "args", "env", "cwd", "impact_overrides", "descriptions", "external",
                   "allowed_data_classes", "max_tools", "startup_timeout_s", "list_timeout_s",
                   "call_timeout_s", "max_output_chars"}
        extra = set(data) - allowed
        if extra:
            raise ValueError("Unknown MCP config keys")
        values = dict(data)
        if isinstance(values.get("args", ()), str) or isinstance(values.get("allowed_data_classes"), str):
            raise ValueError("args and allowed_data_classes must be lists")
        if "args" in values:
            values["args"] = tuple(values["args"])
        if values.get("allowed_data_classes") is not None:
            values["allowed_data_classes"] = frozenset(values["allowed_data_classes"])
        return cls(**values)


@dataclass(frozen=True)
class McpRegistration:
    registered: tuple[str, ...]
    rejected: tuple[tuple[str, str], ...]   # (sanitized server name, stable reason)


def _load_sdk() -> Any:
    # importlib, so the source never spells an import of the SDK (the core must not import it, test_mcp_server).
    try:
        sdk = importlib.import_module("mcp")
        stdio = importlib.import_module("mcp.client.stdio")
    except ImportError as exc:
        raise McpClientError("mcp_sdk_missing") from exc
    return sdk, stdio.StdioServerParameters, stdio.stdio_client


def _sdk_error() -> type[BaseException]:
    return importlib.import_module("mcp").MCPError


def _clean(text: Any, limit: int) -> str:
    return _CONTROL.sub(" ", text if isinstance(text, str) else "")[:limit].strip()


def _hint_level(annotations: Any) -> tuple[int, bool]:
    """Impact level the server's hints imply, and whether it says it reaches the open world. Only ever raises."""
    read_only = getattr(annotations, "read_only_hint", None)
    destructive = getattr(annotations, "destructive_hint", None)
    open_world = getattr(annotations, "open_world_hint", None) is True
    if destructive is True:
        level = 2
    elif read_only is False and destructive is not False:
        level = 2   # MCP: destructiveHint defaults to true when readOnlyHint is false
    elif read_only is False:
        level = 1
    else:
        level = 0
    return level, open_world


def _safe_schema(node: Any, depth: int = 0) -> dict[str, Any]:
    """Structural keys only (allowlist). Titles, descriptions, examples, defaults and any other free text are dropped;
    property names must be bounded identifiers. ``ValueError`` rejects the tool."""
    if depth > MAX_SCHEMA_DEPTH or not isinstance(node, dict):
        raise ValueError("schema")
    out: dict[str, Any] = {}
    kind = node.get("type")
    if isinstance(kind, str) and kind in ("object", "array", "string", "integer", "number", "boolean", "null"):
        out["type"] = kind
    props = node.get("properties")
    if isinstance(props, dict):
        if len(props) > MAX_PROPERTIES or not all(isinstance(k, str) and _PROP.fullmatch(k) for k in props):
            raise ValueError("schema")
        out["properties"] = {k: _safe_schema(v, depth + 1) for k, v in props.items()}
    required = node.get("required")
    if isinstance(required, list):
        if len(required) > MAX_PROPERTIES or not all(isinstance(k, str) and _PROP.fullmatch(k) for k in required):
            raise ValueError("schema")
        out["required"] = list(required)
    if isinstance(node.get("items"), dict):
        out["items"] = _safe_schema(node["items"], depth + 1)
    enum = node.get("enum")
    if isinstance(enum, list):
        if len(enum) > MAX_ENUM or not all(
                (isinstance(v, str) and len(v) <= 64 and _CONTROL.search(v) is None)
                or isinstance(v, (int, float, bool)) for v in enum):
            raise ValueError("schema")
        out["enum"] = list(enum)
    for key in ("minimum", "maximum"):
        if isinstance(node.get(key), (int, float)) and not isinstance(node[key], bool):
            out[key] = node[key]
    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        if isinstance(node.get(key), int) and not isinstance(node[key], bool) and 0 <= node[key] <= 1_000_000:
            out[key] = node[key]
    if isinstance(node.get("additionalProperties"), bool):
        out["additionalProperties"] = node["additionalProperties"]
    return out


class McpClient:
    """One stdio server, a background event loop, synchronous calls."""

    def __init__(self, config: McpServerConfig) -> None:
        self.config = config
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None
        self._session: Any = None
        self._ready = threading.Event()
        self._failure: str | None = None
        self._broken = False
        self._errlog: Any = None

    # -- lifecycle -----------------------------------------------------------------------------------------

    def __enter__(self) -> "McpClient":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def start(self) -> None:
        if self._thread is not None:
            raise McpClientError("mcp_already_started")
        mcp, params_cls, stdio_client = _load_sdk()
        params = params_cls(command=self.config.command, args=list(self.config.args),
                            env=dict(self.config.env), cwd=self.config.cwd)
        self._errlog = open(os.devnull, "w")
        self._thread = threading.Thread(target=self._run, args=(mcp, params, stdio_client),
                                        name=f"mcp-{self.config.alias}", daemon=True)
        self._thread.start()
        if not self._ready.wait(self.config.startup_timeout_s):
            self._abort()
            raise McpClientError("mcp_startup_timeout")
        if self._failure or self._session is None:
            self._failure = self._failure or "mcp_start_failed"
            self.close()
            raise McpClientError(self._failure)

    def _run(self, mcp: Any, params: Any, stdio_client: Any) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            self._task = loop.create_task(self._main(mcp, params, stdio_client))
            loop.run_until_complete(self._task)
        except BaseException:   # the failure is recorded by _main; nothing else may escape the thread
            pass
        finally:
            self._session = None
            self._ready.set()
            try:
                loop.close()
            except Exception:
                pass

    async def _main(self, mcp: Any, params: Any, stdio_client: Any) -> None:
        self._stop = asyncio.Event()
        try:
            # ClientSession without callbacks advertises no sampling, roots or elicitation.
            async with stdio_client(params, errlog=self._errlog) as (read, write):
                async with mcp.ClientSession(read, write) as session:
                    await asyncio.wait_for(session.initialize(), self.config.startup_timeout_s)
                    self._session = session
                    self._ready.set()
                    await self._stop.wait()
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            self._failure = "mcp_startup_timeout"
        except Exception:
            self._failure = "mcp_start_failed" if not self._ready.is_set() else None
            self._broken = True
        finally:
            self._session = None
            self._ready.set()

    def _abort(self) -> None:
        loop, task = self._loop, self._task
        if loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass
        if self._thread is not None:
            self._thread.join(5)
        self._cleanup()

    def close(self) -> None:
        loop, stop = self._loop, self._stop
        if loop is not None and stop is not None and self._thread is not None and self._thread.is_alive():
            try:
                loop.call_soon_threadsafe(stop.set)
            except RuntimeError:
                pass
            self._thread.join(5)
            if self._thread.is_alive():
                self._abort()
        elif self._thread is not None and self._thread.is_alive():
            self._abort()
        self._cleanup()

    def _cleanup(self) -> None:
        self._session = None
        if self._errlog is not None:
            try:
                self._errlog.close()
            except Exception:
                pass
            self._errlog = None

    # -- requests ------------------------------------------------------------------------------------------

    def _submit(self, make: Callable[[Any], Any], timeout: float, should_cancel: Callable[[], bool]) -> Any:
        session, loop = self._session, self._loop
        if session is None or loop is None or self._broken or self._thread is None or not self._thread.is_alive():
            raise McpClientError("mcp_not_connected")

        async def guarded() -> Any:
            try:
                return await asyncio.wait_for(make(session), timeout)
            except asyncio.TimeoutError:
                raise McpClientError("mcp_timeout") from None

        future = asyncio.run_coroutine_threadsafe(guarded(), loop)
        deadline = time.monotonic() + timeout + 2.0
        try:
            while True:
                try:
                    return future.result(0.1)
                except TimeoutError:   # only the 0.1 s poll: the call's own timeout is raised as McpClientError
                    pass
                except McpClientError:
                    raise
                except _sdk_error() as exc:
                    if getattr(exc, "code", None) != importlib.import_module("mcp_types").CONNECTION_CLOSED:
                        raise McpClientError("mcp_tool_error") from None   # one JSON-RPC error: the session is fine
                    self._broken = True
                    raise McpClientError("mcp_server_failed") from None
                except Exception:
                    self._broken = True
                    raise McpClientError("mcp_server_failed") from None
                if should_cancel():
                    raise McpClientError("mcp_cancelled")
                if time.monotonic() > deadline:
                    raise McpClientError("mcp_timeout")
        finally:
            if not future.done():
                future.cancel()

    def list_tools(self) -> list[Any]:
        types = importlib.import_module("mcp.types")   # the SDK is present: start() already loaded it
        tools: list[Any] = []
        cursor: str | None = None
        for _ in range(MAX_LIST_PAGES):
            params = types.PaginatedRequestParams(cursor=cursor) if cursor else None
            page = self._submit(lambda s, p=params: s.list_tools(params=p), self.config.list_timeout_s,
                                lambda: False)
            tools.extend(page.tools)
            cursor = page.next_cursor
            if not cursor or len(tools) > self.config.max_tools:
                break
        return tools

    def call_tool(self, name: str, arguments: Mapping[str, Any], *,
                  should_cancel: Callable[[], bool] = lambda: False) -> dict[str, Any]:
        result = self._submit(lambda s: s.call_tool(name, dict(arguments)), self.config.call_timeout_s,
                              should_cancel)
        if getattr(result, "is_error", False):
            raise McpClientError("mcp_tool_error")
        return self._output(result)

    def _output(self, result: Any) -> dict[str, Any]:
        parts: list[str] = []
        for block in getattr(result, "content", None) or []:
            text = getattr(block, "text", None)
            parts.append(text if isinstance(text, str) else f"[{_clean(getattr(block, 'type', 'unknown'), 20)} content omitted]")
        structured = getattr(result, "structured_content", None)
        if not parts and structured is not None:
            try:
                parts.append(canonical_json(structured).decode("utf-8"))
            except (TypeError, ValueError):
                parts.append("[structured content omitted]")
        text = "\n".join(parts)
        limit = self.config.max_output_chars
        return {"text": text[:limit], "truncated": len(text) > limit}

    # -- registry ------------------------------------------------------------------------------------------

    def register(self, registry: ToolRegistry, *, tools: list[Any] | None = None) -> McpRegistration:
        """List the server's tools and register them as ``<alias>.<name>``. A bad tool is rejected and logged."""
        cfg = self.config
        scoped = registry.scoped(cfg.alias)
        listed = self.list_tools() if tools is None else list(tools)
        registered: list[str] = []
        rejected: list[tuple[str, str]] = []

        def reject(raw: Any, reason: str) -> None:
            shown = _clean(raw, 60)
            rejected.append((shown, reason))
            log.warning("mcp %s: tool %r rejected (%s)", cfg.alias, shown, reason)

        if len(listed) > cfg.max_tools:
            rejected.append(("*", "too_many_tools"))
            log.warning("mcp %s: %d tools beyond the cap of %d were dropped", cfg.alias,
                        len(listed) - cfg.max_tools, cfg.max_tools)
            listed = listed[:cfg.max_tools]

        def normalized(raw: Any) -> str:
            return raw.lower().replace("-", "_") if isinstance(raw, str) and raw.isascii() else ""

        counts: dict[str, int] = {}
        for tool in listed:
            key = normalized(getattr(tool, "name", ""))
            counts[key] = counts.get(key, 0) + 1
        for tool in listed:
            raw = getattr(tool, "name", "")
            name = normalized(raw)
            if not _NAME.fullmatch(name):
                reject(raw, "invalid_name")
                continue
            if counts[name] > 1:   # every variant is rejected: none inherits another's operator override
                reject(raw, "name_conflict")
                continue
            schema = getattr(tool, "input_schema", None)
            try:
                if not isinstance(schema, dict) or len(canonical_json(schema)) > MAX_SCHEMA_BYTES:
                    reject(raw, "invalid_schema")
                    continue
                input_schema = {**_safe_schema(schema), "type": "object", "additionalProperties": False}
                input_ref = SchemaRef(schema_id=f"mcp.{cfg.alias}.{name}.input/1", json_schema=input_schema,
                                      sha256=envelope_sha256(input_schema))
                output_ref = SchemaRef(schema_id="mcp.tool.output/1", json_schema=OUTPUT_SCHEMA,
                                       sha256=envelope_sha256(OUTPUT_SCHEMA))
                spec = self._spec(tool, raw, name, input_ref, output_ref)
                scoped.register(spec, self._executor(raw))
            except (ToolRegistrationError, ValueError, TypeError, KeyError) as exc:
                reject(raw, getattr(exc, "code", "invalid_spec"))
                continue
            registered.append(spec.tool_id)
        return McpRegistration(tuple(registered), tuple(rejected))

    def _spec(self, tool: Any, raw: str, name: str, input_ref: SchemaRef, output_ref: SchemaRef) -> ToolSpec:
        cfg = self.config
        tool_id = f"{cfg.alias}.{name}"
        # Start at the maximum; only the operator can lower it, and the hints raise it back.
        override = cfg.impact_overrides.get(raw)   # exact server name only
        level = _LEVELS[override] if override else 2
        hint, open_world = _hint_level(getattr(tool, "annotations", None))
        level = max(level, hint)
        external = cfg.external or open_world   # an operator override never drops it
        impacts: set[ImpactClass] = set()
        effects: list[EffectDecl] = []
        if level == 0 and not external:
            impacts.add(ImpactClass.READ)
        elif level == 0:
            pass   # read is exclusive: an external reader is declared as external only
        elif level == 1:
            impacts.add(ImpactClass.WRITE)
            effects.append(EffectDecl(kind=ActionKind.WRITE, target=f"mcp:{tool_id}"))
        else:
            impacts.add(ImpactClass.DESTRUCTIVE)
            effects.append(EffectDecl(kind=ActionKind.DESTRUCTIVE, target=f"mcp:{tool_id}"))
        if external:
            impacts.add(ImpactClass.EXTERNAL)
            effects.append(EffectDecl(kind=ActionKind.NETWORK, target=f"mcp:{cfg.alias}"))
        return ToolSpec(
            tool_id=tool_id, version=1, title=name[:80], description=self._description(tool, raw, tool_id),
            invocation=InvocationKind.REMOTE, impacts=frozenset(impacts), effects=tuple(effects),
            allowed_data_classes=cfg.allowed_data_classes if external else None,
            input_schema=input_ref, output_schema=output_ref, timeout_s=cfg.call_timeout_s,
            declared_by="adapter", source_ref=f"mcp:{cfg.alias}")

    def _description(self, tool: Any, raw: str, tool_id: str) -> str:
        if raw in self.config.descriptions:
            return _clean(self.config.descriptions[raw], MAX_DESCRIPTION_CHARS)
        text = _clean(getattr(tool, "description", ""), MAX_DESCRIPTION_CHARS)
        return wrap_untrusted(text, derive(SourceKind.TOOL_OUTPUT, ref=f"mcp:{tool_id}", label="mcp tool description"))

    def _executor(self, server_name: str) -> Callable[..., Any]:
        def run(arguments: Mapping[str, Any], context: ToolCallContext, *,
                should_cancel: Callable[[], bool]) -> dict[str, Any]:
            return self.call_tool(server_name, arguments, should_cancel=should_cancel)
        return run
