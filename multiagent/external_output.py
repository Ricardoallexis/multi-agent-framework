# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Validation and backend persistence for externally produced step output."""
from __future__ import annotations

import json
import hashlib
from pathlib import PurePath
from typing import Any

from pydantic import ValidationError

from .contracts import HumanStepSubmission
from .errors import HumanSubmissionError, InvalidStateTransition


EVIDENCE_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".mp4": "video/mp4", ".webm": "video/webm",
    ".txt": "text/plain", ".log": "text/plain", ".json": "application/json",
    ".jsonl": "application/x-ndjson",
}


class EvidenceRejected(ValueError):
    pass


def check_evidence(name: str, content: bytes, *, file_limit: int) -> str:
    if (not name or name in {".", ".."} or "/" in name or "\\" in name or ":" in name
            or PurePath(name).name != name or any(ord(c) < 32 for c in name)):
        raise EvidenceRejected("Evidence filename must contain no path")
    kind = EVIDENCE_TYPES.get(PurePath(name).suffix.lower())
    if kind is None:
        raise EvidenceRejected("Evidence type is not allowed")
    if len(content) > file_limit:
        raise EvidenceRejected("Evidence file exceeds the size limit")
    valid = {
        "image/png": lambda: content.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": lambda: content.startswith(b"\xff\xd8\xff"),
        "image/webp": lambda: content.startswith(b"RIFF") and content[8:12] == b"WEBP",
        "video/mp4": lambda: content[4:8] == b"ftyp",
        "video/webm": lambda: content.startswith(b"\x1a\x45\xdf\xa3"),
    }
    if kind in valid:
        if not valid[kind]():
            raise EvidenceRejected("Evidence signature does not match its extension")
    else:
        try:
            decoded = content.decode("utf-8")
            if "\x00" in decoded:
                raise ValueError("NUL byte")
            if kind == "application/json":
                json.loads(decoded)
            elif kind == "application/x-ndjson":
                for line in decoded.splitlines():
                    if line.strip():
                        json.loads(line)
        except (UnicodeError, ValueError) as exc:
            raise EvidenceRejected("Evidence is not valid UTF-8 text or JSON") from exc
    return kind


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def external_step(backend, run_id: str):
    run = backend.store.get_run(run_id)
    if run["status"] != "waiting_human" or run.get("cancel_requested"):
        raise InvalidStateTransition("The run is not waiting for external output")
    reason = run.get("waiting_reason")
    if reason in {"execute_step", "research"}:
        try:
            run, pending, index, step, schema = backend._human_step_context(run_id)
        except HumanSubmissionError as exc:
            raise InvalidStateTransition(str(exc)) from exc
        return run, pending, index, step, schema
    if reason != "review":
        raise InvalidStateTransition("The run is not waiting for external output")
    definition = backend.preflight(run["workflow_id"])
    index = int(run["current_step"])
    if index >= len(definition.steps):
        raise InvalidStateTransition("The review step is missing")
    step = definition.steps[index]
    attempt = run.get("waiting_attempt")
    if (step.id != run.get("waiting_step") or type(attempt) is not int or attempt < 1
            or backend.store.latest_step_attempt(run_id, step.id) != attempt):
        raise InvalidStateTransition("The review attempt has changed")
    old = next((item for item in backend.store.list_step_attempts(run_id, step.id)
                if item["attempt"] == attempt and item["status"] == "completed"), None)
    if old is None:
        raise InvalidStateTransition("The review attempt is missing")
    backend.store.review_artifact(run_id, step_id=step.id, attempt=attempt)
    return run, old, index, step, backend.output_schemas[step.contract]


def external_output(backend, run_id: str, output: dict[str, Any], *, commit: bool = False):
    run, source, index, step, schema = external_step(backend, run_id)
    try:
        raw = json.dumps(output, ensure_ascii=False)
        if getattr(backend, "name", "native") == "native":
            parsed = backend._validate_human_response(run, step, schema, raw)
        else:
            parsed = backend._validate_human_response(schema, raw)
    except (HumanSubmissionError, ValidationError, ValueError, TypeError) as exc:
        raise HumanSubmissionError(f"External output does not satisfy {step.contract}: {exc}") from exc
    data = parsed.model_dump(mode="json")
    if not commit:
        return data
    if run["waiting_reason"] in {"execute_step", "research"}:
        return backend.submit_human_step(run_id, HumanStepSubmission(
            raw_response=raw, provider="external_output", model="external"))
    attempt = int(run["waiting_attempt"]) + 1
    backend.store.record_step(
        run_id=run_id, step_id=step.id, attempt=attempt, status="completed",
        input_data=source["input"], output_data=data, model_id="external:output",
        prompt_id=source["prompt_id"], prompt_version=int(source["prompt_version"]),
    )
    path = backend.artifacts.write(run_id=run_id, step_index=index, step_id=step.id,
                                   attempt=attempt, data=data)
    relative = str(path.relative_to(backend.settings.data_dir))
    backend.store.add_artifact(run_id=run_id, step_id=step.id, attempt=attempt,
                               kind=step.contract, path=relative, data=data)
    backend.store.db.log_event(run_id, "step_completed", {
        "step_id": step.id, "attempt": attempt, "artifact_path": relative,
        "execution_mode": "external_output"})
    backend.store.update_run(run_id, waiting_attempt=attempt)
    backend.store.db.log_event(run_id, "checkpoint", {"step_id": step.id, "attempt": attempt})
    return backend.store.get_run(run_id)
