# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Deterministic screening of portable office package text."""
from __future__ import annotations

import re
import string
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from ..egress_guard import findings, redact


@dataclass(frozen=True)
class PackageCheck:
    found: list[dict[str, str | int]]
    redacted: dict[str, str]
    unreadable: list[str]


def excluded_path(path: str) -> bool:
    """Exclude private workspace material regardless of its suffix or location."""
    parts = PurePosixPath(path.replace("\\", "/")).parts
    blocked = {"runs", "conversations", "inputs", "logs", ".local", ".env", "file_tools", "file-tools"}
    return (any(part.casefold() in blocked or part.casefold().startswith(".env.") for part in parts)
            or any(part.casefold().endswith((".db", ".sqlite", ".sqlite3", ".log",
                                             "-wal", "-shm", "-journal")) for part in parts))


def in_file_tools_root(path: Path, file_tools_root: Path | None) -> bool:
    """Identify a configured tools directory even when it has a custom name."""
    if file_tools_root is None:
        return False
    try:
        return path.resolve().is_relative_to(file_tools_root.resolve())
    except (OSError, RuntimeError):
        return False


def check_files(files: dict[str, bytes]) -> PackageCheck:
    """Redact recognized secrets and personal data; never attach unreadable files."""
    found: list[dict[str, str | int]] = []
    redacted: dict[str, str] = {}
    unreadable: list[str] = []
    for path, content in sorted(files.items()):
        path_matches = findings(path, include_entropy=True)
        if path_matches:
            found.extend({"path": path, "line": 1, "type": kind} for _, _, kind in path_matches)
            unreadable.append(path)
            continue
        if excluded_path(path) or any(byte < 32 and byte not in (9, 10, 13) for byte in content):
            unreadable.append(path)
            continue
        try:
            source = content.decode("utf-8")
        except UnicodeDecodeError:
            unreadable.append(path)
            continue
        matches = findings(source, include_entropy=True)
        found.extend({"path": path, "line": source.count("\n", 0, start) + 1, "type": kind}
                     for start, _, kind in matches)
        redacted[path] = redact(source, matches)
    return PackageCheck(found, redacted, unreadable)


# --- OFA-T06: what a package declares, read without running anything -------------------------------------------------

_RUNTIME_NAMES = frozenset({"previous_output", "revision_feedback"})
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PATH_ARGUMENTS = ("path", "source", "destination")


@dataclass(frozen=True)
class FlowDocument:
    """One workflow file of an area, as plain data. ``invalid`` means it could not be read as a workflow."""

    area: str
    base: str
    workflow: str
    steps: list[dict[str, Any]]
    declared: list[str]
    invalid: bool = False
    items: dict[str, dict[str, Any]] | None = None
    description: str = ""


def _placeholders(template: str) -> set[str]:
    names: set[str] = set()
    try:
        for _, field, _, _ in string.Formatter().parse(template):
            match = _IDENTIFIER.match(field) if field else None
            if match is not None:
                names.add(match.group(0))
    except ValueError:
        pass  # malformed braces: the engine reports them when it renders
    return {name for name in names if name not in _RUNTIME_NAMES and not name.endswith("_output")}


def _input_refs(value: Any) -> set[str]:
    """Names a declared tool or function argument takes from the run, as the engine reads them (``${name}``)."""
    if isinstance(value, str):
        return {value[2:-1]} if value.startswith("${") and value.endswith("}") else set()
    if isinstance(value, dict):
        return set().union(*(_input_refs(item) for item in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_input_refs(item) for item in value)) if value else set()
    return set()


def flow_documents(files: Mapping[str, bytes], areas: Iterable[tuple[str, str]]) -> list[FlowDocument]:
    """Parse each ``workflows/*.yaml`` of the given ``(area id, area path)`` pairs. Nothing is imported or run."""
    documents: list[FlowDocument] = []
    for area_id, path in areas:
        base = path.rstrip("/")
        prefix = f"{base}/workflows/"
        for name in sorted(files):
            if not name.startswith(prefix) or not name.endswith(".yaml") or "/" in name[len(prefix):]:
                continue
            workflow = name[len(prefix):-len(".yaml")]
            try:
                raw = yaml.safe_load(files[name].decode("utf-8"))
            except (UnicodeDecodeError, yaml.YAMLError):
                raw = None
            steps = raw.get("steps") if isinstance(raw, dict) else None
            if not isinstance(steps, list) or not all(isinstance(step, dict) for step in steps):
                documents.append(FlowDocument(area_id, base, workflow, [], [], invalid=True))
                continue
            items = {item["name"]: item for item in (raw.get("inputs") or [])
                     if isinstance(item, dict) and isinstance(item.get("name"), str)}
            description = raw.get("description") if isinstance(raw.get("description"), str) else ""
            documents.append(FlowDocument(area_id, base, workflow, steps, list(items), items=items,
                                          description=description))
    return documents


def flow_inputs(files: Mapping[str, bytes], document: FlowDocument) -> dict[str, Any]:
    """What a flow's steps use: ``placeholders`` of the step prompts, ``referenced`` names of tool and function
    arguments, whether a step is a function (``has_function``) and whether every prompt is inside the package."""
    placeholders: set[str] = set()
    referenced: set[str] = set()
    has_function = False
    prompts_known = True
    for step in document.steps:
        if step.get("function") is not None:
            has_function = True
            referenced |= _input_refs(step.get("arguments"))
            continue
        for tool in step.get("tools") or []:
            if isinstance(tool, dict):
                referenced |= _input_refs(tool.get("arguments"))
        prompt = files.get(f"{document.base}/prompts/{step.get('prompt_id')}.v{step.get('prompt_version')}.md")
        if prompt is None:
            prompts_known = False
            continue
        placeholders |= _placeholders(prompt.decode("utf-8", errors="replace"))
    return {"placeholders": placeholders, "referenced": referenced, "has_function": has_function,
            "prompts_known": prompts_known}


def cross_check_inputs(files: Mapping[str, bytes], documents: Iterable[FlowDocument]) -> list[dict[str, Any]]:
    """For each flow, the inputs it declares that nothing asks for and the data its tools need that nobody asks for.

    ``unasked``: a declared input that no step prompt and no tool argument uses, so the person is never asked for it.
    ``missing``: a name a tool or function argument takes from the run (``${name}``) that the flow neither declares nor
    uses as a prompt placeholder, so nobody asks for it and the step would fail. A flow with a function step cannot be
    judged (its code may read any input): it comes back ``unverified`` with ``unasked`` empty. A flow whose step prompt
    is not inside the package is ``unverified`` too, because the placeholders are unknown. The contracts only name step
    outputs, so they add no inputs here.
    """
    rows: list[dict[str, Any]] = []
    for document in documents:
        row: dict[str, Any] = {"area": document.area, "workflow": document.workflow, "declared": list(document.declared),
                               "unasked": [], "missing": [], "unverified": False, "reason": ""}
        if document.invalid:
            row.update(unverified=True, reason="workflow_unreadable")
            rows.append(row)
            continue
        used = flow_inputs(files, document)
        placeholders, referenced = used["placeholders"], used["referenced"]
        declared = set(document.declared)
        if used["has_function"]:
            row.update(unverified=True, reason="function_step")
        elif not used["prompts_known"]:
            row.update(unverified=True, reason="prompt_not_in_package")
        else:
            row["unasked"] = sorted(declared - placeholders - referenced)
        row["missing"] = sorted(referenced - declared - placeholders)
        rows.append(row)
    return rows


def _argument_target(arguments: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = arguments.get(name)
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return {"argument": name, "path": None, "from_input": value[2:-1]}
    return {"argument": name, "path": value if isinstance(value, str) else None, "from_input": None}


def _target_words(targets: list[dict[str, Any]]) -> str:
    parts = [f"the input «{item['from_input']}»" if item["from_input"] else item["path"] or "a file"
             for item in targets]
    return ", ".join(parts)


def describe_tools(documents: Iterable[FlowDocument], specs: Mapping[str, Any]) -> list[dict[str, Any]]:
    """What each declared tool reads, what it writes and what permission it asks, from the declarations alone.

    ``specs`` maps a tool id to its registered spec (``impacts``, ``effects``, ``capabilities``, ``title``,
    ``description``). A tool the host does not register comes back ``known: false``: it would fail when run. The
    ``sentence`` is fixed text; a model may only rephrase it.
    """
    by_capability = {capability: spec for spec in specs.values() for capability in spec.capabilities}
    rows: list[dict[str, Any]] = []
    for document in documents:
        for step in document.steps:
            for tool in step.get("tools") or []:
                tool_id = (tool.get("tool") or tool.get("capability")) if isinstance(tool, dict) else None
                if not isinstance(tool_id, str):
                    continue
                arguments = tool.get("arguments") if isinstance(tool.get("arguments"), dict) else {}
                spec = specs.get(tool_id) or by_capability.get(tool_id)
                row: dict[str, Any] = {"area": document.area, "workflow": document.workflow,
                                       "step": step.get("id") if isinstance(step.get("id"), str) else "",
                                       "tool": tool_id, "title": getattr(spec, "title", tool_id),
                                       "known": spec is not None, "impact": "unknown", "reads": [], "writes": [],
                                       "permission": "unknown", "uses_file_tools_root": tool_id.startswith("file.")}
                if spec is None:
                    row["sentence"] = f"{tool_id}: this tool is not available here, so the step would fail."
                    rows.append(row)
                    continue
                impacts = sorted(getattr(impact, "value", str(impact)) for impact in spec.impacts)
                row["impact"] = impacts[0] if len(impacts) == 1 else "+".join(impacts)
                effect_args = [effect.target_from[1:] for effect in spec.effects
                               if getattr(effect, "target_from", None) and effect.target_from.startswith("/")]
                inside = " inside the work folder" if row["uses_file_tools_root"] else ""
                if impacts == ["read"]:
                    row["reads"] = [_argument_target(arguments, name) for name in _PATH_ARGUMENTS if name in arguments]
                    row["permission"] = "none"
                    row["sentence"] = (f"{spec.title}: reads {_target_words(row['reads']) or 'its input'}{inside}. "
                                       "It changes nothing.")
                else:
                    row["writes"] = [_argument_target(arguments, name) for name in effect_args]
                    if "source" in arguments and "source" in effect_args:
                        row["reads"] = [_argument_target(arguments, "source")]
                    row["permission"] = "each_call"
                    row["sentence"] = (f"{spec.title}: writes {_target_words(row['writes']) or 'a file'}{inside}. "
                                       "It asks for your approval each time.")
                rows.append(row)
    return rows


def writes_files(tool_rows: Iterable[Mapping[str, Any]]) -> bool:
    """Whether any declared tool changes files: the rule behind the Mock rehearsal that installing then requires."""
    return any(row["known"] and ("write" in row["impact"] or "destructive" in row["impact"]) for row in tool_rows)


def read_package_files(root: Path) -> dict[str, bytes]:
    """Every regular file of a staged package by its relative posix path (a staged copy has no links)."""
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.relative_to(root).parts:
            files[path.relative_to(root).as_posix()] = path.read_bytes()
    return files
