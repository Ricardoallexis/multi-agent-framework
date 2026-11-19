# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Built-in file tools confined to one declared root folder (no git required)."""
from __future__ import annotations

import json
import ntpath
import os
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from multiagent.document_actions import ActionKind
from multiagent.tasks.envelopes import canonical_json, envelope_sha256
from .contracts import (DEFAULT_MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES_CEILING, EffectDecl, ImpactClass,
                        InvocationKind, SchemaRef, ToolCallContext, ToolExecutionError, ToolSpec)
from .registry import ToolRegistry

ALLOWED_FILE_EXTENSIONS = frozenset({".md", ".csv", ".json", ".txt"})
DEFAULT_LIST_MAX_DEPTH = 4
MAX_LIST_DEPTH = 10
DEFAULT_LIST_MAX_ENTRIES = 200
MAX_LIST_MAX_ENTRIES = 1000

ERR_OUTSIDE_ROOT = "outside_root"
ERR_UNSUPPORTED_FORMAT = "unsupported_format"
ERR_NOT_FOUND = "not_found"
ERR_ALREADY_EXISTS = "already_exists"
ERR_TOO_LARGE = "too_large"
ERR_INVALID_JSON = "invalid_json"


def _ref(name: str, schema: dict[str, Any]) -> SchemaRef:
    return SchemaRef(schema_id=name, json_schema=schema, sha256=envelope_sha256(schema))


def _is_link_or_junction(path: Path) -> bool:
    return path.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(path))


def _parse_relative(raw: str, *, allow_root: bool = False) -> tuple[str, ...]:
    if not isinstance(raw, str) or "\x00" in raw:
        raise ToolExecutionError(ERR_OUTSIDE_ROOT, "Path must be a relative string without null bytes")
    if raw.startswith(("/", "\\")):
        raise ToolExecutionError(ERR_OUTSIDE_ROOT, "Absolute and UNC paths are not allowed")
    drive, _ = ntpath.splitdrive(raw)
    if drive or PureWindowsPath(raw).is_absolute() or PurePosixPath(raw).is_absolute():
        raise ToolExecutionError(ERR_OUTSIDE_ROOT, "Absolute, drive, and UNC paths are not allowed")
    parts = raw.replace("\\", "/").split("/")
    if ".." in parts:
        raise ToolExecutionError(ERR_OUTSIDE_ROOT, "Parent directory references ('..') are not allowed")
    clean = tuple(part for part in parts if part not in ("", "."))
    if not clean and not allow_root:
        raise ToolExecutionError(ERR_UNSUPPORTED_FORMAT, "A file path with a supported extension is required")
    return clean


def _validate_extension(parts: tuple[str, ...]) -> str:
    if not parts:
        raise ToolExecutionError(ERR_UNSUPPORTED_FORMAT, "A file path with a supported extension is required")
    ext = PurePosixPath(parts[-1]).suffix.lower()
    if ext not in ALLOWED_FILE_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_FILE_EXTENSIONS))
        raise ToolExecutionError(ERR_UNSUPPORTED_FORMAT, f"Unsupported file format {ext or '(none)'}; allowed: {allowed}")
    return ext


def _resolve_inside(root: Path, parts: tuple[str, ...]) -> tuple[Path, str]:
    candidate = root.joinpath(*parts)
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise ToolExecutionError(ERR_OUTSIDE_ROOT, "Path could not be resolved inside root") from exc
    if not resolved.is_relative_to(root):
        raise ToolExecutionError(ERR_OUTSIDE_ROOT, "Resolved path escapes the declared root folder")
    return resolved, "/".join(parts)


def _positive_bound(value: Any, default: int, maximum: int, name: str) -> int:
    if value is None:
        return default
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return min(value, maximum)


def _list_executor(root: Path, max_bytes: int):
    def execute(arguments: Mapping[str, Any], context: ToolCallContext, *, should_cancel: Callable[[], bool]) -> dict[str, Any]:
        parts = _parse_relative(arguments.get("path", ""), allow_root=True)
        recursive = bool(arguments.get("recursive", False))
        depth_limit = _positive_bound(arguments.get("max_depth"), DEFAULT_LIST_MAX_DEPTH, MAX_LIST_DEPTH, "max_depth")
        entry_limit = _positive_bound(arguments.get("max_entries"), DEFAULT_LIST_MAX_ENTRIES, MAX_LIST_MAX_ENTRIES, "max_entries")
        target_dir, rel_posix = _resolve_inside(root, parts)
        if not target_dir.exists() or not target_dir.is_dir():
            raise ToolExecutionError(ERR_NOT_FOUND, "Directory not found")

        entries: list[dict[str, Any]] = []
        truncated = False

        def walk(current: Path, depth: int) -> None:
            nonlocal truncated
            try:
                children = sorted(current.iterdir(), key=lambda p: p.name)
            except OSError as exc:
                raise ToolExecutionError(ERR_NOT_FOUND, "Directory cannot be read") from exc
            for child in children:
                if should_cancel():
                    raise RuntimeError("Tool call cancelled by host")
                is_link = _is_link_or_junction(child)
                if is_link:
                    try:
                        resolved_child = child.resolve()
                    except OSError:
                        continue
                    if not resolved_child.is_relative_to(root):
                        continue
                    is_dir = resolved_child.is_dir()
                    size = 0 if is_dir else resolved_child.stat().st_size
                else:
                    is_dir = child.is_dir()
                    size = 0 if is_dir else child.lstat().st_size
                if len(entries) >= entry_limit:
                    truncated = True
                    return
                entries.append({
                    "name": child.relative_to(target_dir).as_posix(),
                    "type": "dir" if is_dir else "file",
                    "size": size,
                })
                if is_dir and recursive and not is_link:
                    if depth < depth_limit:
                        walk(child, depth + 1)
                        if len(entries) >= entry_limit:
                            return
                    else:
                        try:
                            if any(child.iterdir()):
                                truncated = True
                        except OSError:
                            pass

        walk(target_dir, 1)
        result = {"path": rel_posix, "entries": entries, "truncated": truncated}
        while len(canonical_json(result)) > max_bytes:
            if not entries:
                raise ToolExecutionError(ERR_TOO_LARGE, "Listing output exceeds max_output_bytes")
            entries.pop()
            result["truncated"] = True
        return result

    return execute


def _read_executor(root: Path, max_bytes: int):
    def execute(arguments: Mapping[str, Any], context: ToolCallContext, *, should_cancel: Callable[[], bool]) -> dict[str, Any]:
        parts = _parse_relative(arguments["path"])
        _validate_extension(parts)
        target, rel_posix = _resolve_inside(root, parts)
        if not target.exists() or not target.is_file():
            raise ToolExecutionError(ERR_NOT_FOUND, "File not found")
        if target.stat().st_size > max_bytes:
            raise ToolExecutionError(ERR_TOO_LARGE, "File exceeds max_output_bytes")
        raw = target.read_bytes()
        if len(raw) > max_bytes:
            raise ToolExecutionError(ERR_TOO_LARGE, "File exceeds max_output_bytes")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolExecutionError(ERR_UNSUPPORTED_FORMAT, "File is not valid UTF-8") from exc
        result = {"path": rel_posix, "content": text, "size": len(raw)}
        if len(canonical_json(result)) > max_bytes:
            raise ToolExecutionError(ERR_TOO_LARGE, "Read result exceeds max_output_bytes")
        return result

    return execute


def _write_executor(root: Path, max_bytes: int):
    def execute(arguments: Mapping[str, Any], context: ToolCallContext, *, should_cancel: Callable[[], bool]) -> dict[str, Any]:
        parts = _parse_relative(arguments["path"])
        ext = _validate_extension(parts)
        content = arguments["content"]
        try:
            data = content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ToolExecutionError(ERR_UNSUPPORTED_FORMAT, "Content is not valid UTF-8") from exc
        if len(data) > max_bytes:
            raise ToolExecutionError(ERR_TOO_LARGE, "Content exceeds max_output_bytes")
        if ext == ".json":
            try:
                json.loads(content)
            except ValueError as exc:
                raise ToolExecutionError(ERR_INVALID_JSON, "Content is not valid JSON") from exc
        if root.exists() and not root.is_dir():
            raise ToolExecutionError(ERR_NOT_FOUND, "Configured root is not a directory")
        target, rel_posix = _resolve_inside(root, parts)
        if target.exists() and target.is_dir():
            raise ToolExecutionError(ERR_ALREADY_EXISTS, "Target path is an existing directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return {"path": rel_posix, "size": len(data)}

    return execute


def _move_executor(root: Path):
    def execute(arguments: Mapping[str, Any], context: ToolCallContext, *, should_cancel: Callable[[], bool]) -> dict[str, Any]:
        src_parts = _parse_relative(arguments["source"])
        dst_parts = _parse_relative(arguments["destination"])
        _validate_extension(src_parts)
        _validate_extension(dst_parts)
        src_path, src_posix = _resolve_inside(root, src_parts)
        dst_path, dst_posix = _resolve_inside(root, dst_parts)
        if not src_path.exists() or not src_path.is_file():
            raise ToolExecutionError(ERR_NOT_FOUND, "Source file not found")
        if dst_path.exists() or dst_path.is_symlink():
            raise ToolExecutionError(ERR_ALREADY_EXISTS, "Destination already exists")
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            src_path.rename(dst_path)
        except FileExistsError as exc:
            raise ToolExecutionError(ERR_ALREADY_EXISTS, "Destination already exists") from exc
        return {"source": src_posix, "destination": dst_posix}

    return execute


def register_file_tools(registry: ToolRegistry, root: Path | str, *, max_bytes: int = DEFAULT_MAX_OUTPUT_BYTES) -> None:
    """Register the four built-in ``file.*`` tools confined to ``root``."""
    if isinstance(root, str) and not root.strip():
        raise ValueError("file_tools_root must be a non-empty path")
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_OUTPUT_BYTES_CEILING:
        raise ValueError(f"max_bytes must be between 1 and {MAX_OUTPUT_BYTES_CEILING}")
    root_path = Path(root).expanduser().resolve()

    list_in = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "recursive": {"type": "boolean"},
            "max_depth": {"type": "integer"},
            "max_entries": {"type": "integer"},
        },
        "additionalProperties": False,
    }
    list_out = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "entries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "type": {"type": "string", "enum": ["file", "dir"]},
                        "size": {"type": "integer"},
                    },
                    "required": ["name", "type", "size"],
                    "additionalProperties": False,
                },
            },
            "truncated": {"type": "boolean"},
        },
        "required": ["path", "entries", "truncated"],
        "additionalProperties": False,
    }
    read_in = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    }
    read_out = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
            "size": {"type": "integer"},
        },
        "required": ["path", "content", "size"],
        "additionalProperties": False,
    }
    write_in = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
        "additionalProperties": False,
    }
    write_out = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "size": {"type": "integer"}},
        "required": ["path", "size"],
        "additionalProperties": False,
    }
    move_in = {
        "type": "object",
        "properties": {"source": {"type": "string"}, "destination": {"type": "string"}},
        "required": ["source", "destination"],
        "additionalProperties": False,
    }
    move_out = {
        "type": "object",
        "properties": {"source": {"type": "string"}, "destination": {"type": "string"}},
        "required": ["source", "destination"],
        "additionalProperties": False,
    }

    registry.register(
        ToolSpec(
            tool_id="file.list",
            version=1,
            title="List files",
            description="List files and subfolders inside a relative folder within the configured root.",
            invocation=InvocationKind.LOCAL,
            capabilities=frozenset({"file.list"}),
            impacts=frozenset({ImpactClass.READ}),
            input_schema=_ref("file.list.input/1", list_in),
            output_schema=_ref("file.list.output/1", list_out),
            max_output_bytes=max_bytes,
            declared_by="builtin",
        ),
        _list_executor(root_path, max_bytes),
    )
    registry.register(
        ToolSpec(
            tool_id="file.read",
            version=1,
            title="Read file",
            description="Read a UTF-8 .md, .csv, .json, or .txt file within the configured root.",
            invocation=InvocationKind.LOCAL,
            capabilities=frozenset({"file.read"}),
            impacts=frozenset({ImpactClass.READ}),
            input_schema=_ref("file.read.input/1", read_in),
            output_schema=_ref("file.read.output/1", read_out),
            max_output_bytes=max_bytes,
            declared_by="builtin",
        ),
        _read_executor(root_path, max_bytes),
    )
    registry.register(
        ToolSpec(
            tool_id="file.write",
            version=1,
            title="Write file",
            description="Create or replace a UTF-8 .md, .csv, .json, or .txt file within the configured root.",
            invocation=InvocationKind.LOCAL,
            capabilities=frozenset({"file.write"}),
            impacts=frozenset({ImpactClass.WRITE}),
            effects=(EffectDecl(kind=ActionKind.WRITE, target_from="/path"),),
            input_schema=_ref("file.write.input/1", write_in),
            output_schema=_ref("file.write.output/1", write_out),
            max_output_bytes=max_bytes,
            declared_by="builtin",
        ),
        _write_executor(root_path, max_bytes),
    )
    registry.register(
        ToolSpec(
            tool_id="file.move",
            version=1,
            title="Move file",
            description="Move or rename a .md, .csv, .json, or .txt file within the configured root without overwriting.",
            invocation=InvocationKind.LOCAL,
            capabilities=frozenset({"file.move"}),
            impacts=frozenset({ImpactClass.WRITE}),
            effects=(
                EffectDecl(kind=ActionKind.WRITE, target_from="/source"),
                EffectDecl(kind=ActionKind.WRITE, target_from="/destination"),
            ),
            input_schema=_ref("file.move.input/1", move_in),
            output_schema=_ref("file.move.output/1", move_out),
            max_output_bytes=max_bytes,
            declared_by="builtin",
        ),
        _move_executor(root_path),
    )
