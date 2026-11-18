# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""AXI-T07: pinned versions of the executor applications (contract ``AXI-T01-contrato.md`` sections 9 to 11).

An executor application (Claude, Codex, Copilot, Antigravity, Hermes) is only as trustworthy as the version a person
looked at. The **pin manifest** (``catalog/executor-pins.yaml``) records, per executor, the version and the SHA-256
digest that were accepted by a person, and (Hermes only, which ships no binary) its release tag and commit. This module
reads that manifest, compares it with the evidence of what is installed and says ``matched`` or ``drift``. It is
evidence for the autonomy policy, not a policy: a match enables nothing and grants no autonomy, and a drift only ever
blocks.

What it never does:

* run a program, ask an application for ``--version``, import a third-party package, open a socket or install or update
  anything. The only files it reads are the manifest and, when given, an explicit registry of observed evidence;
* accept a new pin by itself. A newer version is a **candidate** that a person accepts, by hand, in a maintenance
  window (:func:`accept_candidate`); the window is :data:`WINDOW` (7 days) from the first observation and, once it is
  over, the candidate stays blocked and the previous pin stays in force;
* fill in a default: a manifest that is invalid, an executor with no pin, evidence that is missing or malformed all
  block (``manifest_invalid``, ``unpinned``, ``missing_evidence``), never ``matched``.

What a digest covers is part of the pin (``artifact_kind``): ``package`` (the installed package as published),
``launcher`` (only the launcher or shim file that is resolved on the path: it does not identify the package behind it)
or, for Hermes, ``source_archive`` (the reproducible archive of the commit a tag points at; there is no binary to
verify and none is claimed). The person who hashes must hash the same kind of object each time.

Where the evidence comes from is the host's decision: a function ``executor_id -> Evidence | None`` (local package
metadata or a registry the operator writes in the maintenance window, :func:`load_observed`). The service
(``ExecutorServices.pin_guard``) asks a :class:`PinGuard` before a launch; with no guard set the check is off, as the
other barriers are, and a host that turns autonomy on sets one. On a ``drift`` the service records one
``executor.version_changed`` team event per observed change (:func:`event_payload`, :func:`should_record`) and refuses
the launch (``executor_pin_blocked``). The event carries versions, tag, commit and digests, never a local path, an
argument or task data; the autonomy policy (T18) reads it to put the executor back at level 2.

Pure and deterministic: time comes in as an argument (a clock a test can move) and values are compared as text.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from .errors import MultiAgentError

__all__ = [
    "ARTIFACT_KINDS", "CANDIDATE_NONE", "CANDIDATE_OTHER", "Candidate", "DRIFT", "EXECUTOR_IDS", "Evidence", "HERMES_ID", "MANIFEST_INVALID", "MATCHED",
    "MISSING_EVIDENCE", "PinAcceptanceError", "PinCheck", "PinGuard", "PinIssue", "PinManifest", "PinManifestError",
    "PinRecord", "Pin", "UNPINNED", "WINDOW", "accept_candidate", "compare", "default_manifest_path", "dump_manifest",
    "evaluate", "event_payload", "evidence_fields", "load_manifest", "load_observed", "parse_manifest",
    "parse_observed", "should_record", "window_state",
]

_logger = logging.getLogger(__name__)

MANIFEST_VERSION = 1
HERMES_ID = "hermes_cli"          # the working id until AXI-T06 fixes the adapter's; T06 must assert it matches
EXECUTOR_IDS = ("claude_cli", "codex_cli", "copilot_cli", "antigravity_cli", HERMES_ID)   # closed: nothing else is pinned
WINDOW = timedelta(days=7)        # to accept a candidate, counted from its first observation (half-open: see window_state)
MAX_FILE_BYTES = 64 * 1024
ARTIFACT_KINDS = ("package", "launcher", "source_archive")
NOT_ACCEPTORS = ("system:", "executor:", "agent:")     # a pin is accepted by a person, never in the name of the framework

MATCHED = "matched"
DRIFT = "drift"
UNPINNED = "unpinned"
MISSING_EVIDENCE = "missing_evidence"
MANIFEST_INVALID = "manifest_invalid"

WINDOW_OPEN = "open"
WINDOW_EXPIRED = "expired"
WINDOW_NOT_STARTED = "not_started"
CANDIDATE_NONE = "none"           # in ``PinCheck.candidate``: no candidate on record
CANDIDATE_OTHER = "other"         # a candidate is on record but it is not what was observed

_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")       # a version or a tag
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_ACTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,63}$")
_FORMATS = {"version": _TOKEN, "tag": _TOKEN, "sha256": _SHA256, "commit": _COMMIT}
_FORMAT_HINT = {"version": "text of up to 64 letters, digits and . _ + -", "tag": "text of up to 64 letters, digits and . _ + -",
                "sha256": "64 lowercase hexadecimal characters", "commit": "40 lowercase hexadecimal characters"}


# --------------------------------------------------------------------------- errors

@dataclass(frozen=True)
class PinIssue:
    """One problem in a manifest or in an observed registry: where (executor, field) and what, never the value."""
    executor_id: str
    field: str
    message: str

    def __str__(self) -> str:
        where = f"{self.executor_id}: " if self.executor_id else ""
        return f"{where}{self.field}: {self.message}" if self.field else f"{where}{self.message}"


class PinManifestError(MultiAgentError):
    """A pin manifest or an observed registry is not valid: ``issues`` lists every problem found."""

    def __init__(self, issues: list[PinIssue]):
        self.issues = list(issues)
        shown = "; ".join(str(issue) for issue in self.issues[:3])
        more = f" (+{len(self.issues) - 3} more)" if len(self.issues) > 3 else ""
        super().__init__(f"invalid executor pins: {shown}{more}")


class PinAcceptanceError(MultiAgentError):
    """A candidate cannot be accepted. ``code``: ``unknown_executor``, ``no_candidate``, ``invalid_acceptor``,
    ``window_expired`` or ``window_not_started``. The manifest is unchanged: the previous pin stays in force."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


# --------------------------------------------------------------------------- the data

def evidence_fields(executor_id: str) -> tuple[str, ...]:
    """The values that identify an executor: version and digest; Hermes also its tag and commit."""
    return ("version", "sha256", "tag", "commit") if executor_id == HERMES_ID else ("version", "sha256")


@dataclass(frozen=True)
class Evidence:
    """What identifies one build of an executor: pinned or observed. ``None`` is a value that is not known."""
    version: str | None = None
    sha256: str | None = None
    tag: str | None = None
    commit: str | None = None

    def view(self, names: tuple[str, ...] = ("version", "sha256", "tag", "commit")) -> dict[str, str]:
        return {name: getattr(self, name) for name in names if getattr(self, name) is not None}


@dataclass(frozen=True)
class Pin:
    """The evidence a person accepted: ``first_observed_at`` is when it was first seen, ``accepted_at`` when a person
    accepted it (inside the window), ``accepted_by`` who."""
    evidence: Evidence
    artifact_kind: str
    first_observed_at: datetime
    accepted_at: datetime
    accepted_by: str


@dataclass(frozen=True)
class Candidate:
    """Evidence seen that is not the pin and waits for a person (not valid evidence of anything until accepted)."""
    evidence: Evidence
    artifact_kind: str
    first_observed_at: datetime


@dataclass(frozen=True)
class PinRecord:
    executor_id: str
    pin: Pin | None = None
    candidate: Candidate | None = None


@dataclass(frozen=True)
class PinManifest:
    records: tuple[PinRecord, ...]

    def get(self, executor_id: str) -> PinRecord | None:
        return next((record for record in self.records if record.executor_id == executor_id), None)

    def replace_record(self, record: PinRecord) -> "PinManifest":
        return PinManifest(tuple(record if item.executor_id == record.executor_id else item for item in self.records))


@dataclass(frozen=True)
class PinCheck:
    """The result of comparing one executor with its pin.

    ``status`` is ``matched`` or one of the blocking ones (``drift``, ``unpinned``, ``missing_evidence``,
    ``manifest_invalid``). ``changed`` names the values that differ; ``candidate`` says whether what was observed is the
    candidate on record and its window (``open``, ``expired``, ``not_started``), ``other`` or ``none``. ``reason`` is
    plain text without values. ``fingerprint`` identifies a drift (executor, pinned and observed values).
    """
    executor_id: str
    status: str
    reason: str = ""
    changed: tuple[str, ...] = ()
    pinned: Evidence | None = None
    observed: Evidence | None = None
    candidate: str = CANDIDATE_NONE
    fingerprint: str = ""

    @property
    def blocked(self) -> bool:
        return self.status != MATCHED


# --------------------------------------------------------------------------- time

def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("a moment must carry a time zone")
    return moment.astimezone(timezone.utc)


def _iso(moment: datetime) -> str:
    return _aware(moment).isoformat().replace("+00:00", "Z")


def _time(value: Any) -> datetime | None:
    """A moment with a time zone, in UTC, from a datetime or an ISO 8601 text; None for anything else (a date alone too)."""
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    else:
        return None
    return moment.astimezone(timezone.utc) if moment.tzinfo is not None and moment.utcoffset() is not None else None


def window_state(first_observed_at: datetime, now: datetime) -> str:
    """``open`` from the first observation up to, not including, ``WINDOW`` later; ``expired`` from then on;
    ``not_started`` if ``now`` is before the first observation. The end instant itself is expired (fails closed)."""
    start, current = _aware(first_observed_at), _aware(now)
    if current < start:
        return WINDOW_NOT_STARTED
    return WINDOW_OPEN if current < start + WINDOW else WINDOW_EXPIRED


# --------------------------------------------------------------------------- reading files

class _StrictLoader(yaml.SafeLoader):
    """``safe_load`` that refuses a repeated key (the last one would win silently)."""


def _construct_unique_mapping(loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            repeated = key in seen
            seen.add(key)
        except TypeError:
            raise yaml.constructor.ConstructorError(None, None, "a key is not text", key_node.start_mark) from None
        if repeated:
            raise yaml.constructor.ConstructorError(None, None, f"the key {str(key)[:40]!r} is repeated", key_node.start_mark)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


def _read_yaml(path: Path | str) -> Any:
    path = Path(path)
    with path.open("rb") as handle:
        raw = handle.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise PinManifestError([PinIssue("", "", f"the file is larger than {MAX_FILE_BYTES} bytes")])
    try:
        return yaml.load(raw.decode("utf-8"), Loader=_StrictLoader)       # noqa: S506 - a safe loader subclass
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        detail = getattr(exc, "problem", None) or "not readable"
        raise PinManifestError([PinIssue("", "", f"not valid YAML ({str(detail)[:80]})")]) from None


def default_manifest_path() -> Path:
    """The manifest that ships with the framework (``catalog/executor-pins.yaml``)."""
    from .config import _resource_dir

    return _resource_dir("catalog") / "executor-pins.yaml"


# --------------------------------------------------------------------------- parsing

def _valid_acceptor(value: Any) -> bool:
    return isinstance(value, str) and bool(_ACTOR.fullmatch(value)) and not value.lower().startswith(NOT_ACCEPTORS)


def _text_value(name: str, value: Any) -> str | None:
    return value if isinstance(value, str) and _FORMATS[name].fullmatch(value) else None


def _parse_block(executor_id: str, section: str, block: Any, issues: list[PinIssue], *, accepted: bool):
    """A ``pin`` (``accepted``) or ``candidate`` block as a :class:`Pin` or :class:`Candidate`; None with issues if invalid."""
    start = len(issues)
    hermes = executor_id == HERMES_ID
    allowed = ["version", "sha256", "artifact_kind", "first_observed_at"] + (["tag", "commit"] if hermes else []) \
        + (["accepted_at", "accepted_by"] if accepted else [])
    if not isinstance(block, Mapping):
        issues.append(PinIssue(executor_id, section, "must be a mapping or null"))
        return None
    for key in block:
        if key not in allowed:
            issues.append(PinIssue(executor_id, f"{section}.{str(key)[:40]}", "is not a known field here"))
    values: dict[str, Any] = {}
    for name in allowed:
        if name not in block:
            issues.append(PinIssue(executor_id, f"{section}.{name}", "is required"))
            continue
        value = block[name]
        if name in _FORMATS:
            values[name] = _text_value(name, value)
            if values[name] is None:
                issues.append(PinIssue(executor_id, f"{section}.{name}",
                                       f"must be quoted text: {_FORMAT_HINT[name]}"))
        elif name == "artifact_kind":
            kinds = ("source_archive",) if hermes else ("package", "launcher")
            values[name] = value if value in kinds else None
            if values[name] is None:
                issues.append(PinIssue(executor_id, f"{section}.{name}", f"must be one of {', '.join(kinds)}"))
        elif name == "accepted_by":
            values[name] = value if _valid_acceptor(value) else None
            if values[name] is None:
                issues.append(PinIssue(executor_id, f"{section}.{name}", "must name a person (short text, not a "
                                       f"{'/'.join(NOT_ACCEPTORS)} identity)"))
        else:                                                              # a moment
            values[name] = _time(value)
            if values[name] is None:
                issues.append(PinIssue(executor_id, f"{section}.{name}", "must be an ISO 8601 moment with a time zone"))
    if len(issues) > start:
        return None
    evidence = Evidence(values["version"], values["sha256"], values.get("tag"), values.get("commit"))
    if accepted:
        first, taken = values["first_observed_at"], values["accepted_at"]
        if taken < first:
            issues.append(PinIssue(executor_id, f"{section}.accepted_at", "is before first_observed_at"))
        elif taken - first >= WINDOW:
            issues.append(PinIssue(executor_id, f"{section}.accepted_at",
                                   f"is {WINDOW.days} days or more after first_observed_at: the acceptance window was over"))
        if len(issues) > start:
            return None
        return Pin(evidence, values["artifact_kind"], first, taken, values["accepted_by"])
    return Candidate(evidence, values["artifact_kind"], values["first_observed_at"])


def parse_manifest(data: Any) -> PinManifest:
    """Validate a loaded manifest and return it, or raise :class:`PinManifestError` listing every problem.

    Shape: ``{version: 1, executors: {<id>: {pin: <block>|null, candidate: <block>|null}}}`` with exactly the executors
    of :data:`EXECUTOR_IDS`. A block has ``version``, ``sha256``, ``artifact_kind`` and ``first_observed_at`` (plus
    ``tag`` and ``commit`` for Hermes only, and ``accepted_at`` and ``accepted_by`` for a pin). Unknown or missing keys,
    values of the wrong form and an acceptance outside the window are errors; nothing is defaulted.
    """
    issues: list[PinIssue] = []
    if not isinstance(data, Mapping):
        raise PinManifestError([PinIssue("", "", "the manifest must be a mapping")])
    for key in data:
        if key not in ("version", "executors"):
            issues.append(PinIssue("", str(key)[:40], "is not a known field"))
    if data.get("version") != MANIFEST_VERSION or isinstance(data.get("version"), bool):
        issues.append(PinIssue("", "version", f"must be {MANIFEST_VERSION}"))
    declared = data.get("executors")
    if not isinstance(declared, Mapping):
        issues.append(PinIssue("", "executors", "must be a mapping"))
        raise PinManifestError(issues)
    for key in declared:
        if key not in EXECUTOR_IDS:
            issues.append(PinIssue(str(key)[:40], "", f"is not a pinned executor (known: {', '.join(EXECUTOR_IDS)})"))
    records: list[PinRecord] = []
    for executor_id in EXECUTOR_IDS:
        entry = declared.get(executor_id)
        if entry is None and executor_id not in declared:
            issues.append(PinIssue(executor_id, "", "has no record"))
            continue
        if not isinstance(entry, Mapping):
            issues.append(PinIssue(executor_id, "", "the record must be a mapping with pin and candidate"))
            continue
        for key in entry:
            if key not in ("pin", "candidate"):
                issues.append(PinIssue(executor_id, str(key)[:40], "is not a known field"))
        for name in ("pin", "candidate"):
            if name not in entry:
                issues.append(PinIssue(executor_id, name, "is required (use null when there is none)"))
        pin = _parse_block(executor_id, "pin", entry["pin"], issues, accepted=True) if entry.get("pin") is not None else None
        candidate = _parse_block(executor_id, "candidate", entry["candidate"], issues, accepted=False) \
            if entry.get("candidate") is not None else None
        if pin is not None and candidate is not None and pin.evidence == candidate.evidence:
            issues.append(PinIssue(executor_id, "candidate", "is the same as the pin"))
        records.append(PinRecord(executor_id, pin, candidate))
    if issues:
        raise PinManifestError(issues)
    return PinManifest(tuple(records))


def load_manifest(path: Path | str) -> PinManifest:
    """Read and validate the manifest at ``path`` (``OSError`` if it cannot be read, :class:`PinManifestError` if invalid)."""
    return parse_manifest(_read_yaml(path))


def parse_observed(data: Any) -> dict[str, Evidence]:
    """Validate an observed-evidence registry: ``{version: 1, executors: {<id>: {version, sha256[, tag, commit]}}}``.

    The registry is what an operator wrote after checking the installation in the maintenance window (or a host built
    from local package metadata). An executor that is absent has no evidence. Values are checked for form here: a
    malformed one is a registry error, not a drift.
    """
    issues: list[PinIssue] = []
    if not isinstance(data, Mapping):
        raise PinManifestError([PinIssue("", "", "the registry must be a mapping")])
    for key in data:
        if key not in ("version", "executors"):
            issues.append(PinIssue("", str(key)[:40], "is not a known field"))
    if data.get("version") != MANIFEST_VERSION or isinstance(data.get("version"), bool):
        issues.append(PinIssue("", "version", f"must be {MANIFEST_VERSION}"))
    declared = data.get("executors")
    if not isinstance(declared, Mapping):
        issues.append(PinIssue("", "executors", "must be a mapping"))
        raise PinManifestError(issues)
    found: dict[str, Evidence] = {}
    for key, entry in declared.items():
        if key not in EXECUTOR_IDS:
            issues.append(PinIssue(str(key)[:40], "", f"is not a pinned executor (known: {', '.join(EXECUTOR_IDS)})"))
            continue
        names = evidence_fields(key)
        if not isinstance(entry, Mapping):
            issues.append(PinIssue(key, "", "the evidence must be a mapping"))
            continue
        start = len(issues)
        for name in entry:
            if name not in names:
                issues.append(PinIssue(key, str(name)[:40], "is not a known field here"))
        values: dict[str, str | None] = {}
        for name in names:
            if name not in entry:
                issues.append(PinIssue(key, name, "is required"))
            else:
                values[name] = _text_value(name, entry[name])
                if values[name] is None:
                    issues.append(PinIssue(key, name, f"must be quoted text: {_FORMAT_HINT[name]}"))
        if len(issues) == start:
            found[key] = Evidence(**values)
    if issues:
        raise PinManifestError(issues)
    return found


def load_observed(path: Path | str) -> dict[str, Evidence]:
    """Read and validate an observed-evidence registry (see :func:`parse_observed`)."""
    return parse_observed(_read_yaml(path))


# --------------------------------------------------------------------------- writing

def _block_mapping(owner: Pin | Candidate) -> dict[str, Any]:
    out: dict[str, Any] = {**owner.evidence.view(("version", "sha256")), "artifact_kind": owner.artifact_kind}
    out.update(owner.evidence.view(("tag", "commit")))
    out["first_observed_at"] = _iso(owner.first_observed_at)
    if isinstance(owner, Pin):
        out["accepted_at"] = _iso(owner.accepted_at)
        out["accepted_by"] = owner.accepted_by
    return out


def dump_manifest(manifest: PinManifest) -> str:
    """The manifest as YAML text that :func:`load_manifest` reads back to an equal manifest."""
    executors = {record.executor_id: {
        "pin": _block_mapping(record.pin) if record.pin else None,
        "candidate": _block_mapping(record.candidate) if record.candidate else None,
    } for record in manifest.records}
    return yaml.safe_dump({"version": MANIFEST_VERSION, "executors": executors}, sort_keys=False,
                          default_flow_style=False, allow_unicode=True)


# --------------------------------------------------------------------------- comparing

def _fingerprint(executor_id: str, pinned: Mapping[str, str], observed: Mapping[str, str]) -> str:
    blob = json.dumps([executor_id, dict(pinned), dict(observed)], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def compare(executor_id: str, record: PinRecord | None, observed: Evidence | None, now: datetime) -> PinCheck:
    """Compare what was observed with the pin of ``executor_id``; never raises for bad evidence, it blocks.

    ``unpinned`` when there is no record or no pin; ``missing_evidence`` when nothing was observed or a needed value is
    absent or malformed; ``matched`` when every value that identifies the executor (``evidence_fields``) is equal;
    ``drift`` otherwise, with the names that differ in ``changed``. A drift never changes the pin; if what was observed
    is the candidate on record, ``candidate`` says where its window stands.
    """
    now = _aware(now)
    if record is None or record.pin is None:
        reason = "it is not one of the pinned executors" if record is None else "no pin has been accepted for it"
        return PinCheck(executor_id, UNPINNED, reason)
    names = evidence_fields(executor_id)
    pinned = record.pin.evidence
    if observed is None:
        return PinCheck(executor_id, MISSING_EVIDENCE, "there is no evidence of the installed version", pinned=pinned)
    absent = [name for name in names if getattr(observed, name) is None]
    malformed = [name for name in names if getattr(observed, name) is not None
                 and _text_value(name, getattr(observed, name)) is None]
    if absent or malformed:
        parts = ([f"missing {', '.join(absent)}"] if absent else []) + ([f"malformed {', '.join(malformed)}"] if malformed else [])
        return PinCheck(executor_id, MISSING_EVIDENCE, f"the evidence is incomplete ({'; '.join(parts)})", pinned=pinned)
    seen = Evidence(**observed.view(names))
    changed = tuple(name for name in names if getattr(seen, name) != getattr(pinned, name))
    if not changed:
        return PinCheck(executor_id, MATCHED, "", pinned=pinned, observed=seen)
    candidate = CANDIDATE_NONE
    if record.candidate is not None:
        candidate = (window_state(record.candidate.first_observed_at, now)
                     if Evidence(**record.candidate.evidence.view(names)) == seen else CANDIDATE_OTHER)
    return PinCheck(executor_id, DRIFT, f"differs from the pin in {', '.join(changed)}", changed, pinned, seen, candidate,
                    _fingerprint(executor_id, pinned.view(names), seen.view(names)))


def evaluate(manifest: PinManifest, observed: Mapping[str, Evidence], now: datetime,
             only: tuple[str, ...] | None = None) -> list[PinCheck]:
    """:func:`compare` for each executor of the closed list (or only ``only``), in order."""
    return [compare(executor_id, manifest.get(executor_id), observed.get(executor_id), now)
            for executor_id in EXECUTOR_IDS if only is None or executor_id in only]


# --------------------------------------------------------------------------- accepting

def accept_candidate(manifest: PinManifest, executor_id: str, *, accepted_by: str, now: datetime) -> PinManifest:
    """A new manifest in which the candidate of ``executor_id`` is the pin, accepted by ``accepted_by`` at ``now``.

    Only a person accepts (``accepted_by`` is not a ``system:``, ``executor:`` or ``agent:`` identity) and only while
    the candidate's window is open (:func:`window_state`). Otherwise :class:`PinAcceptanceError` is raised and the
    manifest, so the previous pin, stays as it is: an expired candidate is not accepted late, a new observation starts
    its own window. This changes only the manifest value; installing or updating the application is a separate manual act.
    """
    record = manifest.get(executor_id)
    if record is None:
        raise PinAcceptanceError("unknown_executor", f"{executor_id[:40]!r} is not a pinned executor")
    candidate = record.candidate
    if candidate is None:
        raise PinAcceptanceError("no_candidate", f"{executor_id} has no candidate to accept")
    if not _valid_acceptor(accepted_by):
        raise PinAcceptanceError("invalid_acceptor", "a pin is accepted by a person, named in short text")
    state = window_state(candidate.first_observed_at, now)
    if state != WINDOW_OPEN:
        raise PinAcceptanceError(f"window_{state}", f"the acceptance window of {executor_id} is {state.replace('_', ' ')}")
    pin = Pin(candidate.evidence, candidate.artifact_kind, candidate.first_observed_at, _aware(now), accepted_by)
    return manifest.replace_record(replace(record, pin=pin, candidate=None))


# --------------------------------------------------------------------------- the launch guard and its event

class PinGuard:
    """What the executor service asks before a launch: :meth:`check` of one executor against the manifest.

    ``manifest`` is a :class:`PinManifest` or a function that returns one (called at each check, so a manifest that a
    person edits is read again); ``evidence`` is a function ``executor_id -> Evidence | None`` or a mapping. The check
    never raises: a manifest or an evidence source that fails is ``manifest_invalid`` or ``missing_evidence``.
    """

    def __init__(self, manifest: PinManifest | Callable[[], PinManifest],
                 evidence: Callable[[str], Evidence | None] | Mapping[str, Evidence]):
        self._manifest = manifest
        self._evidence = evidence if callable(evidence) else evidence.get

    @classmethod
    def from_files(cls, manifest_path: Path | str, observed_path: Path | str) -> "PinGuard":
        """A guard that reads the manifest and an observed registry from files at each check."""
        return cls(lambda: load_manifest(manifest_path), lambda executor_id: load_observed(observed_path).get(executor_id))

    def check(self, executor_id: str, now: datetime) -> PinCheck:
        try:
            manifest = self._manifest() if callable(self._manifest) else self._manifest
        except Exception:  # noqa: BLE001 - a manifest that cannot be read proves nothing: block
            _logger.warning("the executor pin manifest could not be read", exc_info=True)
            return PinCheck(executor_id, MANIFEST_INVALID, "the pin manifest cannot be read or is not valid")
        record = manifest.get(executor_id)
        if record is None or record.pin is None:
            return compare(executor_id, record, None, now)
        try:
            observed = self._evidence(executor_id)
        except Exception:  # noqa: BLE001 - an evidence source that fails proves nothing: block
            _logger.warning("the evidence of executor %s could not be read", executor_id, exc_info=True)
            return PinCheck(executor_id, MISSING_EVIDENCE, "the evidence source failed", pinned=record.pin.evidence)
        return compare(executor_id, record, observed, now)


def event_payload(check: PinCheck, now: datetime) -> dict[str, Any]:
    """The payload of ``executor.version_changed`` for a drift: values and digests, no path, argument or task data."""
    if check.status != DRIFT or check.pinned is None or check.observed is None:
        raise ValueError("only a drift has an event")
    names = evidence_fields(check.executor_id)
    return {"executor_id": check.executor_id, "status": check.status, "changed": list(check.changed),
            "pinned": check.pinned.view(names), "observed": check.observed.view(names), "candidate": check.candidate,
            "fingerprint": check.fingerprint, "observed_at": _iso(now), "decision": "blocked"}


def should_record(previous: Mapping[str, Any] | None, check: PinCheck) -> bool:
    """True when ``check`` is a drift that is not the one the latest event of the executor already records.

    One event per transition: the same observation again writes nothing, a different one (including a return to an
    earlier one after another) writes a new event.
    """
    return check.status == DRIFT and (previous is None or previous.get("fingerprint") != check.fingerprint)
