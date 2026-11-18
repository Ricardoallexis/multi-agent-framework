"""Versioned, provider-neutral tool contracts."""
from __future__ import annotations

import hashlib
import re
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (BaseModel, ConfigDict, Field, PlainSerializer, field_validator,
                      model_validator)

from multiagent.document_actions import Action, ActionKind
from multiagent.tasks.envelopes import canonical_json, envelope_sha256

JsonValue = Any
MAX_ARGUMENT_BYTES = 262_144
DEFAULT_MAX_OUTPUT_BYTES = 262_144
MAX_OUTPUT_BYTES_CEILING = 4_194_304
MAX_TIMEOUT_S = 600
MAX_DETAILS_BYTES = 4_096
MAX_SUMMARY_CHARS = 300
_ID = re.compile(r"[a-z][a-z0-9_]{0,39}(?:\.[a-z][a-z0-9_]{0,39}){1,3}\Z")
_SCHEMA_ID = re.compile(r"[a-z][a-z0-9_.]*/[1-9][0-9]*\Z")
_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{8,64}\Z")
_TARGET_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _sorted_values(values: Any) -> list[Any]:
    return sorted(getattr(item, "value", item) for item in values)


# A set serializes as a sorted list so that hashes do not depend on PYTHONHASHSEED.
_SortedSet = PlainSerializer(_sorted_values, return_type=list[str], when_used="json")


class ToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_by_name=True, validate_by_alias=True,
                              serialize_by_alias=True)


class ImpactClass(str, Enum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"
    EXTERNAL = "external"


class InvocationKind(str, Enum):
    LOCAL = "local"
    REMOTE = "remote"
    PROVIDER_HOSTED = "provider_hosted"


class ToolErrorCode(str, Enum):
    UNKNOWN_TOOL = "unknown_tool"
    VERSION_NOT_FOUND = "version_not_found"
    INVALID_REQUEST = "invalid_request"
    INVALID_ARGUMENTS = "invalid_arguments"
    NOT_GRANTED = "not_granted"
    DATA_CLASS_NOT_ALLOWED = "data_class_not_allowed"
    APPROVAL_REQUIRED = "approval_required"
    DENIED = "denied"
    NOT_INVOCABLE = "not_invocable"
    GUARD_STOPPED = "guard_stopped"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    EXECUTION_FAILED = "execution_failed"
    INVALID_OUTPUT = "invalid_output"
    OUTPUT_TOO_LARGE = "output_too_large"


RETRYABLE_CODES = frozenset({ToolErrorCode.TIMEOUT, ToolErrorCode.EXECUTION_FAILED})


class SchemaRef(ToolModel):
    schema_id: str
    json_schema: dict[str, JsonValue]
    sha256: str

    @model_validator(mode="after")
    def valid(self) -> "SchemaRef":
        if not _SCHEMA_ID.fullmatch(self.schema_id) or self.sha256 != envelope_sha256(self.json_schema):
            raise ValueError("Invalid schema reference")
        return self


class EffectDecl(ToolModel):
    kind: ActionKind
    target_from: str | None = None
    target: str | None = None

    @model_validator(mode="after")
    def valid(self) -> "EffectDecl":
        if self.target_from is not None and (not self.target_from.startswith("/") or self.target is not None):
            raise ValueError("Effect target must use one source")
        if self.target is not None and not _valid_target(self.target):
            raise ValueError("Invalid effect target")
        return self


def _valid_target(value: Any) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 200 and _TARGET_CONTROL.search(value) is None


class ToolSpec(ToolModel):
    schema_: Literal["multiagent.tool_spec/1"] = Field(default="multiagent.tool_spec/1", alias="schema")
    tool_id: str
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)
    invocation: InvocationKind
    capabilities: Annotated[frozenset[str], _SortedSet] = frozenset()
    impacts: Annotated[frozenset[ImpactClass], _SortedSet]
    effects: tuple[EffectDecl, ...] = ()
    permissions_required: Annotated[frozenset[str], _SortedSet] = frozenset()
    allowed_data_classes: Annotated[frozenset[str], _SortedSet] | None = None
    input_schema: SchemaRef
    output_schema: SchemaRef
    deterministic: bool = False
    max_output_bytes: int = Field(default=DEFAULT_MAX_OUTPUT_BYTES, ge=1, le=MAX_OUTPUT_BYTES_CEILING)
    timeout_s: float = Field(default=30.0, gt=0, le=MAX_TIMEOUT_S, allow_inf_nan=False)
    declared_by: Literal["builtin", "preset", "adapter"]
    source_ref: str | None = None

    @model_validator(mode="after")
    def valid(self) -> "ToolSpec":
        if len(self.tool_id) > 120 or not _ID.fullmatch(self.tool_id):
            raise ValueError("Invalid tool id")
        if any(len(x) > 120 or not _ID.fullmatch(x) for x in self.capabilities):
            raise ValueError("Invalid capability")
        if not self.impacts:
            raise ValueError("Impacts required")
        kinds = {effect.kind for effect in self.effects}
        if ImpactClass.READ in self.impacts and (len(self.impacts) != 1 or self.effects):
            raise ValueError("Read is exclusive and effect-free")
        if ImpactClass.WRITE in self.impacts and ActionKind.WRITE not in kinds:
            raise ValueError("Write needs a write effect")
        if ImpactClass.DESTRUCTIVE in self.impacts and ActionKind.DESTRUCTIVE not in kinds:
            raise ValueError("Destructive needs a destructive effect")
        if ImpactClass.DESTRUCTIVE in self.impacts and any(
                effect.kind == ActionKind.DESTRUCTIVE and effect.target is None and effect.target_from is None
                for effect in self.effects):
            raise ValueError("Destructive effects need a target")
        external = {ActionKind.NETWORK, ActionKind.LAUNCH_EXECUTOR, ActionKind.OPEN_APPLICATION}
        if ImpactClass.EXTERNAL in self.impacts and not kinds.intersection(external):
            raise ValueError("External needs an external effect")
        if ActionKind.WRITE in kinds and ImpactClass.WRITE not in self.impacts:
            raise ValueError("Write effect needs write impact")
        if ActionKind.DESTRUCTIVE in kinds and ImpactClass.DESTRUCTIVE not in self.impacts:
            raise ValueError("Destructive effect needs destructive impact")
        if kinds.intersection(external) and ImpactClass.EXTERNAL not in self.impacts:
            raise ValueError("External effect needs external impact")
        return self


def spec_sha256(spec: ToolSpec) -> str:
    return envelope_sha256(spec.model_dump(mode="json"))


class ToolCallContext(ToolModel):
    scope: Literal["run_step", "task"]
    run_id: str | None = None
    task_id: str | None = None
    step_id: str | None = None
    attempt: int = Field(default=1, ge=1)
    ordinal: int = Field(default=0, ge=0)
    model_call_ordinal: int | None = None
    profile_id: str | None = None
    data_class: str | None = None

    @model_validator(mode="after")
    def valid(self) -> "ToolCallContext":
        if self.scope == "run_step" and not (self.run_id and self.step_id):
            raise ValueError("run_step needs run_id and step_id")
        if self.scope == "task" and not self.task_id:
            raise ValueError("task needs task_id")
        return self


class ToolOrigin(ToolModel):
    kind: str
    ref: str | None = None


def request_id_for(context: ToolCallContext, index: int) -> str:
    parts = (context.scope, context.run_id, context.task_id, context.step_id,
             context.attempt, context.model_call_ordinal, index)
    return "tr_" + hashlib.sha256("|".join("" if x is None else str(x) for x in parts).encode()).hexdigest()[:24]


class ToolRequest(ToolModel):
    schema_: Literal["multiagent.tool_request/1"] = Field(default="multiagent.tool_request/1", alias="schema")
    request_id: str
    tool_id: str
    tool_version: int | None = Field(default=None, ge=1)
    arguments: dict[str, JsonValue]
    context: ToolCallContext
    origin: ToolOrigin
    timeout_s: float | None = Field(default=None, gt=0, le=MAX_TIMEOUT_S, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid(self) -> "ToolRequest":
        if not _REQUEST_ID.fullmatch(self.request_id) or len(self.tool_id) > 120 or not _ID.fullmatch(self.tool_id):
            raise ValueError("Invalid request id or tool id")
        if len(canonical_json(self.arguments)) > MAX_ARGUMENT_BYTES:
            raise ValueError("Arguments too large")
        return self


class ActionRef(ToolModel):
    kind: ActionKind
    target: str | None = None

    @field_validator("target")
    @classmethod
    def target_valid(cls, value: str | None) -> str | None:
        if value is not None and not _valid_target(value):
            raise ValueError("Invalid action target")
        return value

    @classmethod
    def from_action(cls, action: Action) -> "ActionRef":
        return cls(kind=action.kind, target=action.target)

    def to_action(self) -> Action:
        return Action(self.kind, self.target)


def permission_request_id_for(request_id: str, tool_id: str, version: int, actions: "tuple[ActionRef, ...]",
                              arguments_sha256: str, spec_sha256: str) -> str:
    """Stable id of one pending call: it binds the arguments hash too (CAP-T14, B11), so a new request with the same
    request_id and destinations but other arguments has another id and an approval of the old one is refused. The spec
    hash is bound too: another spec (for example another title, which is part of the summary shown) is another id."""
    actions_sha = envelope_sha256([ref.model_dump(mode="json") for ref in actions])
    source = f"{request_id}|{tool_id}|{version}|{actions_sha}|{arguments_sha256}|{spec_sha256}"
    return "tpr_" + hashlib.sha256(source.encode()).hexdigest()[:32]


class PermissionRequest(ToolModel):
    schema_: Literal["multiagent.tool_permission_request/1"] = Field(default="multiagent.tool_permission_request/1", alias="schema")
    permission_request_id: str
    request_id: str
    tool_id: str
    tool_version: int
    spec_sha256: str
    context: ToolCallContext
    impacts: Annotated[frozenset[ImpactClass], _SortedSet]
    actions: tuple[ActionRef, ...]
    arguments_sha256: str
    summary: str = Field(max_length=MAX_SUMMARY_CHARS)
    reason: str
    origin: ToolOrigin


class ToolError(ToolModel):
    schema_: Literal["multiagent.tool_error/1"] = Field(default="multiagent.tool_error/1", alias="schema")
    code: ToolErrorCode
    message: str = Field(max_length=500)
    retryable: bool
    details: dict[str, JsonValue] = Field(default_factory=dict)
    tool_message_sha256: str | None = None

    @model_validator(mode="after")
    def valid(self) -> "ToolError":
        if self.retryable != (self.code in RETRYABLE_CODES) or len(canonical_json(self.details)) > MAX_DETAILS_BYTES:
            raise ValueError("Invalid error metadata")
        return self


class ToolResult(ToolModel):
    schema_: Literal["multiagent.tool_result/1"] = Field(default="multiagent.tool_result/1", alias="schema")
    request_id: str
    tool_id: str
    tool_version: int | None
    spec_sha256: str | None
    ok: bool
    output: JsonValue | None = None
    output_sha256: str | None = None
    output_bytes: int = 0
    error: ToolError | None = None
    permission_request: PermissionRequest | None = None
    duration_ms: int | None = None
    provenance_ref: str
    blob_refs: dict[Literal["arguments", "output", "tool_message"], str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid(self) -> "ToolResult":
        if self.ok != (self.error is None) or (self.ok and (self.output_sha256 is None or self.tool_version is None
                                                             or self.spec_sha256 is None)):
            raise ValueError("Inconsistent result")
        if self.provenance_ref != f"tool:{self.request_id}" or (self.error is not None and self.output is not None):
            raise ValueError("Inconsistent result")
        if (self.permission_request is not None) != (self.error is not None and self.error.code == ToolErrorCode.APPROVAL_REQUIRED):
            raise ValueError("Inconsistent permission request")
        if self.error and self.error.code not in {ToolErrorCode.UNKNOWN_TOOL, ToolErrorCode.VERSION_NOT_FOUND, ToolErrorCode.INVALID_REQUEST} and (self.tool_version is None or self.spec_sha256 is None):
            raise ValueError("Resolved tool metadata required")
        return self
