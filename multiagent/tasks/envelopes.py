# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Pure task and result envelopes v1 (ORQ-T24): no database, no services.

Builds and validates the two envelopes of the ORQ-3 contract and the reference
grammar they use:

* a **task envelope** (``multiagent.task_envelope/1``) is the data an agent
  needs to start a task: objective, effective status, dependencies by
  reference, file scope, gate, base commit and the event sequence to read from;
* a **task result** (``multiagent.task_result/1``) is what the agent delivers:
  a bounded summary, decisions, contract changes, tests not run, requests and
  the patch as a blob by sha256.

Both are data only. No field carries a command, a credential or an absolute
path, and free text (``objective``, ``constraints``, ``summary``) is never
interpreted. Readers ignore unknown fields (forward compatibility) and writers
emit only the fields of version 1, so an unknown field such as ``command`` is
dropped, never kept.

References are text with a fixed grammar:

=========  =====================================  ======================
scheme     form                                   resolved by the core
=========  =====================================  ======================
result     ``result://<task_id>/<attempt>``       yes
blob       ``blob:sha256:<64 hex>``               yes
doc        ``doc://<relative path>[#anchor][@c]`` no (validated text)
report     ``report://<id>``                      no
rules      ``rules@sha256:<64 hex>``              no
=========  =====================================  ======================

Every error is an :class:`EnvelopeError` (a ``ValueError``) naming the
offending ``field``; the services map it to ``invalid_request``.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .graph import REQUIRES

TASK_ENVELOPE_SCHEMA = "multiagent.task_envelope/1"
TASK_RESULT_SCHEMA = "multiagent.task_result/1"
REFERENCE_SCHEMES = ("result", "blob", "doc", "report", "rules")
RESOLVABLE_SCHEMES = ("result", "blob")

MAX_REFERENCE_CHARS = 200
MAX_OBJECTIVE_CHARS = 4000
MAX_DEPENDENCIES = 50
MAX_ARTIFACT_REFS = 50
MAX_CONTEXT_REFS = 20
MAX_CONSTRAINTS = 20
MAX_CONSTRAINT_CHARS = 200
MAX_GATE_CHECKS = 10
MAX_FILE_SCOPE = 100
MAX_PATH_CHARS = 300
MAX_SUMMARY_CHARS = 1500
MAX_DECISIONS = 20
MAX_DECISION_CHARS = 300
MAX_CONTRACT_CHANGES_CHARS = 1500
MAX_TESTS_NOT_RUN = 20
MAX_TEST_NOT_RUN_CHARS = 200
MAX_REQUESTS = 10
MAX_REQUEST_TO_CHARS = 100
MAX_REQUEST_WHAT_CHARS = 200
MAX_PATCH_FILES = 200
MAX_RESULT_BYTES = 16 * 1024

# Stored states (ORQ-1) plus the two computed ones of an open task.
ENVELOPE_STATUSES = ("blocked", "ready", "claimed", "running", "paused", "submitted", "validating",
                     "needs_changes", "accepted", "failed", "cancelled")
TASK_ENVELOPE_FIELDS = ("schema", "task_id", "group_id", "revision", "attempt", "assignee", "status",
                        "objective", "depends_on", "artifact_refs", "context_refs", "file_scope",
                        "constraints", "gate", "base_commit", "events_after")
TASK_RESULT_FIELDS = ("schema", "task_id", "attempt", "agent", "base_commit", "patch", "summary",
                      "decisions", "contract_changes", "tests_not_run", "requests")

_GATE_ALIASES = {"tests": "command:test", "commit": "command:commit"}
# Same identifiers as ORQ-1 (services.tasks) and command names as ORQ-2 (tasks.commands), repeated
# here so that validating an envelope imports no database, service or process code.
_GROUP_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_COMMAND_NAME = re.compile(r"[a-z][a-z0-9_-]{0,39}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{7,40}")
_ANCHOR = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
_REPORT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
_RESULT_REF = re.compile(r"result://(?P<task>[^/]+)/(?P<attempt>[1-9][0-9]{0,8})")
_DOC_REF = re.compile(r"doc://(?P<path>[^#@]+)(?:#(?P<anchor>[^#@]+))?(?:@(?P<commit>[^#@]+))?")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class EnvelopeError(ValueError):
    """An envelope or reference is invalid; ``field`` names the offending field."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class Reference:
    """A parsed reference. Only the attributes of its scheme are set."""

    text: str
    scheme: str
    task_id: str | None = None
    attempt: int | None = None
    sha256: str | None = None
    path: str | None = None
    anchor: str | None = None
    commit: str | None = None
    report_id: str | None = None

    @property
    def resolvable(self) -> bool:
        """Whether the core resolves it (``result:`` and ``blob:``)."""
        return self.scheme in RESOLVABLE_SCHEMES


# -------- canonical JSON --------
def canonical_json(data: Any) -> bytes:
    """UTF-8 JSON with sorted keys and ``,``/``:`` separators: the bytes that are hashed and measured."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def envelope_sha256(data: Any) -> str:
    """sha256 (hex) of the canonical JSON of ``data``."""
    return hashlib.sha256(canonical_json(data)).hexdigest()


# -------- references --------
def result_ref(task_id: str, attempt: int) -> str:
    """``result://<task_id>/<attempt>``, validated."""
    return parse_reference(f"result://{task_id}/{attempt}", field="result_ref").text


def blob_ref(sha256: str) -> str:
    """``blob:sha256:<hex>`` for a sha256 in hex (or already prefixed with ``sha256:``)."""
    value = sha256[len("sha256:"):] if isinstance(sha256, str) and sha256.startswith("sha256:") else sha256
    return parse_reference(f"blob:sha256:{value}", field="blob").text


def parse_reference(value: Any, *, field: str = "reference") -> Reference:
    """Parse one reference of the fixed grammar; anything else raises :class:`EnvelopeError`."""
    if not isinstance(value, str):
        raise EnvelopeError(field, f"{field} must be text")
    if not value or len(value) > MAX_REFERENCE_CHARS:
        raise EnvelopeError(field, f"{field} must have 1 to {MAX_REFERENCE_CHARS} characters")
    if _CONTROL.search(value) or any(char.isspace() for char in value):
        raise EnvelopeError(field, f"{field} has control or blank characters: {value!r}")
    if value.startswith("result://"):
        match = _RESULT_REF.fullmatch(value)
        if not match or not _TASK_ID.fullmatch(match["task"]):
            raise EnvelopeError(field, f"Invalid result reference (result://<task_id>/<attempt>): {value!r}")
        return Reference(value, "result", task_id=match["task"], attempt=int(match["attempt"]))
    if value.startswith("blob:"):
        sha = value[len("blob:sha256:"):] if value.startswith("blob:sha256:") else ""
        if not _HEX64.fullmatch(sha):
            raise EnvelopeError(field, f"Invalid blob reference (blob:sha256:<64 lowercase hex>): {value!r}")
        return Reference(value, "blob", sha256=sha)
    if value.startswith("rules@"):
        sha = value[len("rules@sha256:"):] if value.startswith("rules@sha256:") else ""
        if not _HEX64.fullmatch(sha):
            raise EnvelopeError(field, f"Invalid rules reference (rules@sha256:<64 lowercase hex>): {value!r}")
        return Reference(value, "rules", sha256=sha)
    if value.startswith("report://"):
        report_id = value[len("report://"):]
        if not _REPORT_ID.fullmatch(report_id):
            raise EnvelopeError(field, f"Invalid report reference (report://<id>): {value!r}")
        return Reference(value, "report", report_id=report_id)
    if value.startswith("doc://"):
        match = _DOC_REF.fullmatch(value)
        if not match:
            raise EnvelopeError(field, f"Invalid doc reference (doc://<relative path>[#anchor][@commit]): {value!r}")
        path = _relative_path(field, match["path"])
        if match["anchor"] is not None and not _ANCHOR.fullmatch(match["anchor"]):
            raise EnvelopeError(field, f"Invalid anchor in {value!r}")
        if match["commit"] is not None and not _COMMIT.fullmatch(match["commit"]):
            raise EnvelopeError(field, f"Invalid commit in {value!r} (7 to 40 lowercase hex)")
        return Reference(value, "doc", path=path, anchor=match["anchor"], commit=match["commit"])
    raise EnvelopeError(field, f"Unknown reference scheme in {value!r}: use {', '.join(REFERENCE_SCHEMES)}")


def validate_reference(value: Any, *, field: str = "reference", schemes: Iterable[str] = REFERENCE_SCHEMES) -> str:
    """Return the reference text if it parses and its scheme is one of ``schemes``."""
    allowed = tuple(schemes)
    ref = parse_reference(value, field=field)
    if ref.scheme not in allowed:
        raise EnvelopeError(field, f"{field} must use {', '.join(allowed)}: {value!r}")
    return ref.text


# -------- field helpers --------
def _relative_path(field: str, raw: Any) -> str:
    """A repository path as ORQ-1 stores it: relative, ``/`` separators, no ``..`` nor drive."""
    if not isinstance(raw, str):
        raise EnvelopeError(field, f"{field} must hold text paths")
    path = raw.strip().rstrip("/")
    parts = path.split("/")
    if (not path or len(path) > MAX_PATH_CHARS or "\\" in path or path.startswith("/") or ":" in path
            or ".." in parts or "" in parts or _CONTROL.search(path)):
        raise EnvelopeError(field, f"Invalid path (relative, '/' separators, no '..' nor drive): {raw!r}")
    return path


def _text(field: str, value: Any, *, maximum: int, required: bool = True) -> str:
    if not isinstance(value, str):
        raise EnvelopeError(field, f"{field} must be text")
    value = value.strip()
    if required and not value:
        raise EnvelopeError(field, f"{field} is required")
    if len(value) > maximum:
        raise EnvelopeError(field, f"{field} is longer than {maximum} characters")
    return value


def _list(field: str, value: Any, *, maximum: int) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        raise EnvelopeError(field, f"{field} must be a list")
    items = list(value)
    if len(items) > maximum:
        raise EnvelopeError(field, f"{field} has more than {maximum} items")
    return items


def _texts(field: str, value: Any, *, maximum: int, chars: int) -> list[str]:
    return [_text(field, item, maximum=chars) for item in _list(field, value, maximum=maximum)]


def _integer(field: str, value: Any, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise EnvelopeError(field, f"{field} must be an integer >= {minimum}")
    return value


def _identifier(field: str, value: Any, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise EnvelopeError(field, f"Invalid {field}: {value!r}")
    return value


def _commit(field: str, value: Any) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or not _COMMIT.fullmatch(value):
        raise EnvelopeError(field, f"{field} must be empty or 7 to 40 lowercase hex characters")
    return value


def _paths(field: str, value: Any, *, maximum: int) -> list[str]:
    paths: list[str] = []
    for raw in _list(field, value, maximum=maximum):
        path = _relative_path(field, raw)
        if path not in paths:
            paths.append(path)
    return paths


def _refs(field: str, value: Any, *, maximum: int, schemes: Iterable[str] = REFERENCE_SCHEMES) -> list[str]:
    allowed = tuple(schemes)
    refs: list[str] = []
    for raw in _list(field, value, maximum=maximum):
        ref = validate_reference(raw, field=field, schemes=allowed)
        if ref not in refs:
            refs.append(ref)
    return refs


def _gate(value: Any) -> list[str]:
    checks: list[str] = []
    for raw in _list("gate", value, maximum=MAX_GATE_CHECKS):
        check = _GATE_ALIASES.get(raw.strip(), raw.strip()) if isinstance(raw, str) else raw
        known = isinstance(check, str) and (
            check == "review" or (check.startswith("command:") and bool(_COMMAND_NAME.fullmatch(check[8:]))))
        if not known:
            raise EnvelopeError("gate", f"Unknown gate check {raw!r}: use review, tests, commit or command:<name>")
        if check not in checks:
            checks.append(check)
    return checks


def _dependency(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise EnvelopeError("depends_on", "every dependency must be an object")
    dep: dict[str, Any] = {"task_id": _identifier("depends_on.task_id", raw.get("task_id"), _TASK_ID)}
    if raw.get("requires") not in REQUIRES:
        raise EnvelopeError("depends_on.requires", f"requires must be one of {', '.join(REQUIRES)}")
    dep["requires"] = raw["requires"]
    if raw.get("status") not in ENVELOPE_STATUSES:
        raise EnvelopeError("depends_on.status", f"Unknown dependency status: {raw.get('status')!r}")
    dep["status"] = raw["status"]
    if raw.get("result_ref") is not None:
        ref = parse_reference(raw["result_ref"], field="depends_on.result_ref")
        if ref.scheme != "result" or ref.task_id != dep["task_id"]:
            raise EnvelopeError("depends_on.result_ref", "result_ref must be a result reference of the same task")
        dep["result_ref"] = ref.text
    return dep


def _schema(data: Mapping[str, Any], expected: str) -> None:
    schema = data.get("schema", expected)
    if schema != expected:
        raise EnvelopeError("schema", f"Unsupported schema {schema!r}: expected {expected!r}")


# -------- task envelope --------
def build_task_envelope(*, task_id: str, group_id: str, revision: int, attempt: int, assignee: str | None,
                        status: str, objective: str, depends_on: Iterable[Mapping[str, Any]] = (),
                        artifact_refs: Iterable[str] = (), context_refs: Iterable[str] = (),
                        file_scope: Iterable[str] = (), constraints: Iterable[str] = (),
                        gate: Iterable[str] = (), base_commit: str = "", events_after: int = 0) -> dict[str, Any]:
    """Build and validate a task envelope v1 with exactly the fields of the contract, in its order."""
    return validate_task_envelope({
        "schema": TASK_ENVELOPE_SCHEMA, "task_id": task_id, "group_id": group_id, "revision": revision,
        "attempt": attempt, "assignee": assignee, "status": status, "objective": objective,
        "depends_on": list(depends_on), "artifact_refs": list(artifact_refs), "context_refs": list(context_refs),
        "file_scope": list(file_scope), "constraints": list(constraints), "gate": list(gate),
        "base_commit": base_commit, "events_after": events_after,
    })


def validate_task_envelope(data: Any) -> dict[str, Any]:
    """Validate a task envelope v1 and return it normalized, without unknown fields.

    A missing ``schema`` is taken as v1; another version raises
    :class:`EnvelopeError`. Optional lists default to ``[]``.
    """
    if not isinstance(data, Mapping):
        raise EnvelopeError("envelope", "A task envelope must be an object")
    _schema(data, TASK_ENVELOPE_SCHEMA)
    assignee = data.get("assignee")
    if assignee is not None:
        assignee = _text("assignee", assignee, maximum=100)
    status = data.get("status")
    if status not in ENVELOPE_STATUSES:
        raise EnvelopeError("status", f"Unknown status: {status!r}")
    deps = [_dependency(raw) for raw in _list("depends_on", data.get("depends_on"), maximum=MAX_DEPENDENCIES)]
    return {
        "schema": TASK_ENVELOPE_SCHEMA,
        "task_id": _identifier("task_id", data.get("task_id"), _TASK_ID),
        "group_id": _identifier("group_id", data.get("group_id"), _GROUP_ID),
        "revision": _integer("revision", data.get("revision"), minimum=1),
        "attempt": _integer("attempt", data.get("attempt", 0), minimum=0),
        "assignee": assignee,
        "status": status,
        "objective": _text("objective", data.get("objective"), maximum=MAX_OBJECTIVE_CHARS),
        "depends_on": deps,
        "artifact_refs": _refs("artifact_refs", data.get("artifact_refs"), maximum=MAX_ARTIFACT_REFS,
                               schemes=("blob",)),
        "context_refs": _refs("context_refs", data.get("context_refs"), maximum=MAX_CONTEXT_REFS),
        "file_scope": _paths("file_scope", data.get("file_scope"), maximum=MAX_FILE_SCOPE),
        "constraints": _texts("constraints", data.get("constraints"), maximum=MAX_CONSTRAINTS,
                              chars=MAX_CONSTRAINT_CHARS),
        "gate": _gate(data.get("gate")),
        "base_commit": _commit("base_commit", data.get("base_commit")),
        "events_after": _integer("events_after", data.get("events_after", 0), minimum=0),
    }


def unchanged_envelope(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """The short answer for a caller that already holds this envelope: ``{unchanged, hash, revision}``."""
    return {"unchanged": True, "hash": envelope_sha256(envelope), "revision": envelope["revision"]}


# -------- task result --------
def validate_task_result(data: Any, *, task_id: str, attempt: int, agent: str) -> dict[str, Any]:
    """Validate a submitted task result v1 and return it normalized and completed.

    ``schema``, ``task_id``, ``attempt`` and ``agent`` are filled from the
    task and the actor; a submitted value that differs raises
    :class:`EnvelopeError`. Unknown fields are dropped. The canonical JSON of
    the result must fit in :data:`MAX_RESULT_BYTES`. Whether the patch blob
    exists is the caller's check (it needs the store).
    """
    if not isinstance(data, Mapping):
        raise EnvelopeError("result", "A task result must be an object")
    _schema(data, TASK_RESULT_SCHEMA)
    expected = {
        "task_id": _identifier("task_id", task_id, _TASK_ID),
        "attempt": _integer("attempt", attempt, minimum=1),
        "agent": _text("agent", agent, maximum=100),
    }
    for field, value in expected.items():
        if field in data and data[field] != value:
            raise EnvelopeError(field, f"{field} {data[field]!r} does not match the task ({value!r})")
    result = {
        "schema": TASK_RESULT_SCHEMA, **expected,
        "base_commit": _commit("base_commit", data.get("base_commit")),
        "patch": _patch(data.get("patch")),
        "summary": _text("summary", data.get("summary"), maximum=MAX_SUMMARY_CHARS),
        "decisions": _texts("decisions", data.get("decisions"), maximum=MAX_DECISIONS, chars=MAX_DECISION_CHARS),
        "contract_changes": _text("contract_changes", data.get("contract_changes", ""),
                                  maximum=MAX_CONTRACT_CHANGES_CHARS, required=False),
        "tests_not_run": _texts("tests_not_run", data.get("tests_not_run"), maximum=MAX_TESTS_NOT_RUN,
                                chars=MAX_TEST_NOT_RUN_CHARS),
        "requests": [_request(raw) for raw in _list("requests", data.get("requests"), maximum=MAX_REQUESTS)],
    }
    size = len(canonical_json(result))
    if size > MAX_RESULT_BYTES:
        raise EnvelopeError("result", f"The result is {size} bytes; the limit is {MAX_RESULT_BYTES}")
    return result


def _patch(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise EnvelopeError("patch", "patch must be an object {blob, files} or null")
    blob = value.get("blob")
    sha = blob[len("sha256:"):] if isinstance(blob, str) and blob.startswith("sha256:") else ""
    if not _HEX64.fullmatch(sha):
        raise EnvelopeError("patch.blob", f"patch.blob must be sha256:<64 lowercase hex>: {blob!r}")
    return {"blob": blob, "files": _paths("patch.files", value.get("files"), maximum=MAX_PATCH_FILES)}


def _request(raw: Any) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        raise EnvelopeError("requests", "every request must be an object {to, what}")
    return {"to": _text("requests.to", raw.get("to"), maximum=MAX_REQUEST_TO_CHARS),
            "what": _text("requests.what", raw.get("what"), maximum=MAX_REQUEST_WHAT_CHARS)}


def patch_sha256(result: Mapping[str, Any]) -> str:
    """The patch sha256 (hex) of a validated result, or ``""`` without a patch."""
    patch = result.get("patch")
    return patch["blob"][len("sha256:"):] if patch else ""


def result_key(result: Mapping[str, Any]) -> tuple[str, int, str]:
    """Idempotency key of a validated result: ``(task_id, attempt, patch sha256 or "")``."""
    return result["task_id"], result["attempt"], patch_sha256(result)


def same_result(stored: Mapping[str, Any], submitted: Mapping[str, Any]) -> bool:
    """Whether two validated results have the same content (canonical JSON), i.e. a replay."""
    return canonical_json(dict(stored)) == canonical_json(dict(submitted))
