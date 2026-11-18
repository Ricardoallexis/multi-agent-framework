# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Read-only step attempt history and attempt comparison."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..attempt_compare import compare_outputs
from ..observability.usage import read_usage, totals_of
from .errors import INVALID_REQUEST, NOT_FOUND, ServiceError

def event_actor(payload: Mapping[str, Any] | None, *, default: str) -> str:
    """Actor declared in an event payload, or the role default when absent."""
    if isinstance(payload, Mapping):
        for key in ("by", "actor"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return default


def _read_markdown(data_dir: Path, relative_path: Any) -> str | None:
    if not isinstance(relative_path, str) or not relative_path.strip():
        return None
    try:
        root = data_dir.resolve()
        candidate = (root / relative_path).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            return None
        return candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError):
        return None


def _human_submissions(store, run_id: str, step_id: str) -> dict[int, dict[str, Any]]:
    with store.db.connect() as conn:
        rows = conn.execute(
            "SELECT attempt, provider, model, provenance, prompt_used, notes, created_at "
            "FROM human_step_submissions WHERE run_id=? AND step_id=? ORDER BY attempt, id",
            (run_id, step_id),
        ).fetchall()
    return {int(row["attempt"]): dict(row) for row in rows}


def _known_steps(definitions, run: Mapping[str, Any], bundle: str | None) -> set[str]:
    steps: set[str] = set()
    try:
        definition = definitions.read_workflow(run["workflow_id"], bundle=bundle)
    except ServiceError:
        definition = None
    if isinstance(definition, Mapping):
        for step in definition.get("steps") or ():
            if isinstance(step, Mapping) and isinstance(step.get("id"), str):
                steps.add(step["id"])
    for artifact in run.get("artifacts") or ():
        if isinstance(artifact, Mapping) and isinstance(artifact.get("step_id"), str):
            steps.add(artifact["step_id"])
    for event in run.get("events") or ():
        payload = event.get("payload") if isinstance(event, Mapping) else None
        if isinstance(payload, Mapping) and isinstance(payload.get("step_id"), str):
            steps.add(payload["step_id"])
    return steps


def _step_events_index(
    events: list[dict[str, Any]],
    step_id: str,
) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, str]]]:
    """Review outcome and selected model/provider per attempt of ``step_id`` from run events."""
    reviews: dict[int, dict[str, Any]] = {}
    selected: dict[int, dict[str, str]] = {}
    latest_model_selected: dict[str, dict[str, str]] = {}
    open_checkpoint: tuple[str, int] | None = None

    for event in events:
        if not isinstance(event, Mapping):
            continue
        kind = event.get("event_type")
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            payload = {}
        ts = event.get("ts")
        ev_step = payload.get("step_id")
        ev_attempt = payload.get("attempt")
        has_attempt = isinstance(ev_attempt, int) and not isinstance(ev_attempt, bool)

        if kind == "model_selected" and isinstance(ev_step, str):
            latest_model_selected[ev_step] = {
                "provider": str(payload.get("provider") or ""),
                "model": str(payload.get("model") or ""),
            }
        elif kind == "step_completed" and ev_step == step_id and has_attempt:
            if step_id in latest_model_selected:
                selected[ev_attempt] = latest_model_selected[step_id]
        elif kind == "checkpoint":
            if isinstance(ev_step, str) and has_attempt:
                open_checkpoint = (ev_step, ev_attempt)
                if ev_step == step_id:
                    reviews[ev_attempt] = {
                        "status": "pending",
                        "checkpoint_at": ts,
                        "reviewed_at": None,
                        "actor": None,
                        "feedback": "",
                    }
        elif kind in ("human_approved", "human_revision", "human_rejected"):
            if isinstance(ev_step, str) and has_attempt:
                target_step, target_attempt = ev_step, ev_attempt
            elif open_checkpoint is not None:
                target_step, target_attempt = open_checkpoint
            else:
                target_step, target_attempt = None, None
            open_checkpoint = None
            if target_step == step_id and target_attempt is not None:
                actor = event_actor(payload, default="operator")
                if kind == "human_approved":
                    status, feedback = "approved", ""
                elif kind == "human_revision":
                    status = "regenerated" if payload.get("mode") == "regenerate" else "changes_requested"
                    feedback = str(payload.get("feedback") or "")
                else:
                    status = "rejected"
                    feedback = str(payload.get("feedback") or "")
                previous = reviews.get(target_attempt, {})
                reviews[target_attempt] = {
                    "status": status,
                    "checkpoint_at": previous.get("checkpoint_at"),
                    "reviewed_at": ts,
                    "actor": actor,
                    "feedback": feedback,
                }
                if kind == "human_revision":
                    if "comments" in payload:
                        reviews[target_attempt]["comments"] = payload["comments"]
                    if "channel" in payload:
                        reviews[target_attempt]["channel"] = payload["channel"]

    return reviews, selected


def _producer_for(
    row: Mapping[str, Any],
    submission: Mapping[str, Any] | None,
    usage_records: list[dict[str, Any]],
    selected_info: Mapping[str, str] | None,
) -> dict[str, Any]:
    if submission is not None:
        return {
            "kind": "human",
            "provider": str(submission.get("provider") or "human"),
            "model": str(submission.get("model") or ""),
            "provenance": str(submission.get("provenance") or "human_reported"),
            "submitted_at": submission.get("created_at"),
        }
    model_id = str(row.get("model_id") or "")
    if row.get("function"):
        return {"kind": "function", "function": row["function"]}
    if model_id.startswith("human:"):
        rest = model_id[len("human:"):]
        provider, _, model = rest.partition(":")
        return {
            "kind": "human",
            "provider": provider or "human",
            "model": "" if model == "unspecified" else model,
            "provenance": "human_reported",
            "submitted_at": row.get("created_at"),
        }
    chosen = next((r for r in reversed(usage_records) if r.get("success")), None)
    if chosen is None and usage_records:
        chosen = usage_records[-1]
    model = (chosen.get("model") if chosen else "") or (selected_info.get("model") if selected_info else "") or model_id
    provider = (chosen.get("provider") if chosen else "") or (selected_info.get("provider") if selected_info else "")
    return {
        "kind": "model",
        "model": model,
        "provider": provider,
    }


def _tokens_for(usage_records: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not usage_records:
        return None
    totals = totals_of(usage_records)
    if totals["tokens_in"] is None and totals["tokens_out"] is None:
        return None
    return {
        "tokens_in": totals["tokens_in"],
        "tokens_out": totals["tokens_out"],
        "tokens_total": totals["tokens_total"],
        "duration_ms": totals["duration_ms"],
        "estimated_cost_usd": totals["estimated_cost_usd"],
    }


def build_step_attempts(runtime, definitions, run_id: str, step_id: str) -> dict[str, Any]:
    """Return completed attempts of ``step_id`` in ``run_id`` with producer, input, output, review and tokens."""
    run = runtime.store.get_run(run_id)
    rows = runtime.store.list_step_attempts(run_id, step_id)
    known = _known_steps(definitions, run, runtime.bundle)
    if not step_id or (step_id not in known and not rows):
        raise ServiceError(
            NOT_FOUND,
            f"Step not found: {step_id}",
            status=404,
            details={"run_id": run_id, "step_id": step_id},
        )

    completed_rows = [row for row in rows if row.get("status") == "completed"]
    artifacts_by_attempt = {
        int(item["attempt"]): item
        for item in (run.get("artifacts") or ())
        if item.get("step_id") == step_id
    }
    submissions = _human_submissions(runtime.store, run_id, step_id)
    usage_by_attempt: dict[int, list[dict[str, Any]]] = {}
    for record in read_usage(runtime.store, run_id):
        if record.get("step_id") == step_id:
            usage_by_attempt.setdefault(int(record["attempt"]), []).append(record)
    reviews, selected_models = _step_events_index(run.get("events") or [], step_id)
    functions_by_attempt = {
        event["payload"]["attempt"]: event["payload"]["function"]
        for event in run.get("events") or []
        if event.get("event_type") == "step_started" and isinstance(event.get("payload"), Mapping)
        and event["payload"].get("step_id") == step_id and event["payload"].get("function")
    }
    data_dir = runtime.service.settings.data_dir

    attempts: list[dict[str, Any]] = []
    for row in completed_rows:
        attempt = int(row["attempt"])
        artifact = artifacts_by_attempt.get(attempt)
        submission = submissions.get(attempt)
        usage_records = usage_by_attempt.get(attempt, [])
        input_data = row.get("input") if isinstance(row.get("input"), dict) else {}
        output_data = row.get("output") if isinstance(row.get("output"), dict) else {}
        if not output_data and artifact and isinstance(artifact.get("data"), dict):
            output_data = artifact["data"]
        markdown = _read_markdown(data_dir, artifact.get("path")) if artifact else None
        human_meta = None
        if submission is not None:
            human_meta = {
                "provider": str(submission.get("provider") or "human"),
                "model": str(submission.get("model") or ""),
                "provenance": str(submission.get("provenance") or "human_reported"),
                "prompt_used": str(submission.get("prompt_used") or ""),
                "notes": str(submission.get("notes") or ""),
                "submitted_at": submission.get("created_at"),
            }
        attempts.append({
            "attempt": attempt,
            "status": str(row.get("status") or ""),
            "producer": _producer_for({**row, "function": functions_by_attempt.get(attempt)}, submission,
                                      usage_records, selected_models.get(attempt)),
            "input": {
                "revision_feedback": str(input_data.get("revision_feedback") or ""),
                "has_previous_output": bool(input_data.get("previous_output")),
            },
            "output": output_data,
            "kind": artifact.get("kind") if artifact else None,
            "approved": bool(artifact.get("approved")) if artifact else False,
            "markdown": markdown,
            "review": reviews.get(attempt),
            "human": human_meta,
            "tokens": _tokens_for(usage_records),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        })

    return {
        "run_id": run_id,
        "step_id": step_id,
        "bundle": runtime.bundle,
        "attempts": attempts,
    }


def build_attempt_comparison(runtime, definitions, run_id: str, step_id: str, a: int, b: int) -> dict[str, Any]:
    """Compare two completed attempts ``a`` and ``b`` of ``step_id`` in ``run_id``."""
    if isinstance(a, bool) or not isinstance(a, int) or a < 1 or isinstance(b, bool) or not isinstance(b, int) or b < 1:
        raise ServiceError(
            INVALID_REQUEST,
            "Attempt numbers must be positive integers",
            status=422,
            details={"a": a, "b": b},
        )
    history = build_step_attempts(runtime, definitions, run_id, step_id)
    by_number = {item["attempt"]: item for item in history["attempts"]}
    for number in (a, b):
        if number not in by_number:
            raise ServiceError(
                NOT_FOUND,
                f"Attempt {number} not found for step {step_id}",
                status=404,
                details={"run_id": run_id, "step_id": step_id, "attempt": number},
            )
    attempt_a = by_number[a]
    attempt_b = by_number[b]
    return {
        "run_id": run_id,
        "step_id": step_id,
        "bundle": runtime.bundle,
        "a": attempt_a,
        "b": attempt_b,
        "changes": compare_outputs(attempt_a["output"], attempt_b["output"]),
    }
