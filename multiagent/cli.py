from __future__ import annotations

import argparse
import base64
import json
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

import httpx

from .asset_service import AssetService
from .bootstrap import build_system
from .config import Settings, get_settings
from .contracts import (
    AssetMetadata,
    AssetSource,
    AssetStatus,
    BrandProfilePayload,
    ExecutionMode,
    MetricSnapshotInput,
    PipelineMode,
    PublicationStatus,
    ResearchMode,
    SocialPostRequest,
)
from .doctor import run_doctor
from .version import __version__


def pretty(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def api_client() -> httpx.Client:
    s = get_settings()
    return httpx.Client(base_url=s.api_base_url, timeout=30)


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
        if isinstance(body, dict):
            detail = body.get("detail") or body.get("error") or body
            if isinstance(detail, dict) and "message" in detail:
                message = str(detail["message"])
                if detail.get("code"):
                    message = f"{detail['code']}: {message}"
                if detail.get("details"):
                    message += "\n" + json.dumps(detail["details"], ensure_ascii=False, indent=2, default=str)
                return message
            return str(detail)
        return str(body)
    except Exception:
        return response.text.strip() or response.reason_phrase


_DEFAULT_TIMEOUT = object()


def _api_json(method: str, path: str, *, json_body=None, params=None, timeout=_DEFAULT_TIMEOUT, content=None):
    # ``timeout=None`` waits without limit (a task gate may run long project commands).
    # ``content`` sends raw bytes instead of JSON (a blob).
    options = {} if timeout is _DEFAULT_TIMEOUT else {"timeout": timeout}
    if content is not None:
        options["content"] = content
    try:
        with api_client() as client:
            response = client.request(method, path, json=json_body, params=params, **options)
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise RuntimeError("Backend unavailable at 127.0.0.1:8000. Start .\\run_backend.bat") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Backend communication error: {exc}") from exc
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {_detail(response)}")
    return response.json()


def _parse_step_modes(values: list[str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in values or []:
        if "=" not in item:
            raise ValueError("--step-mode uses the format step=auto|local|cloud|human_guided")
        step, mode = item.split("=", 1)
        ExecutionMode(mode)
        result[step.strip()] = mode
    return result


def cmd_init(args) -> None:
    s = get_settings(); s.ensure_directories(); system = build_system(s)
    print(f"Multi-Agent Framework {__version__} initialized. DB={s.db_path} schema={system.db.schema_version()}")


def cmd_doctor(args) -> None:
    ok, report = run_doctor(get_settings(), deep=args.deep)
    pretty(report)
    raise SystemExit(0 if ok else 1)


def _parse_inputs(values: list[str] | None) -> dict[str, str]:
    inputs: dict[str, str] = {}
    for item in values or []:
        if "=" not in item:
            raise ValueError("--input uses the format name=value")
        name, value = item.split("=", 1)
        name = name.strip()
        if not name or name in inputs:
            raise ValueError("--input names must be nonempty and unique")
        inputs[name] = value
    return inputs


def _bundle_params(args) -> dict[str, str]:
    return {"bundle": args.bundle} if args.bundle else {}


def cmd_workflows(args) -> None:
    path = "/api/v1/workflows"
    if args.workflow_id:
        path += "/" + quote(args.workflow_id, safe="")
    pretty(_api_json("GET", path, params=_bundle_params(args)))


def cmd_agents(args) -> None:
    pretty(_api_json("GET", "/api/v1/agents", params=_bundle_params(args)))


def cmd_bundle_validate(args) -> None:
    name = quote(args.name, safe="")
    pretty(_api_json("POST", f"/api/v1/bundles/{name}/validate"))


def cmd_artifacts(args) -> None:
    run_id = quote(args.run_id, safe="")
    pretty(_api_json("GET", f"/api/v1/runs/{run_id}/artifacts"))


def cmd_events(args) -> None:
    run_id = quote(args.run_id, safe="")
    pretty(_api_json("GET", f"/api/v1/runs/{run_id}/events", params={"after": args.after}))


# --- tasks (ORQ-1): every command calls the task routes of the HTTP API ---------

def _segment(value: str) -> str:
    return quote(value, safe="")


def _parse_depends_on(values: list[str] | None) -> list:
    result: list = []
    for item in values or []:
        task_id, _, requires = item.partition(":")
        result.append({"task_id": task_id, "requires": requires} if requires else task_id)
    return result


def _parse_release_dependencies(values: list[str] | None) -> list[dict[str, str]]:
    result = []
    for item in values or []:
        depends_on_id, separator, source_ref = item.partition("=")
        if not separator or not depends_on_id or not source_ref:
            raise ValueError("--release-dep must use DEPENDENCY_ID=SOURCE_REF")
        result.append({"depends_on_id": depends_on_id, "source_ref": source_ref})
    return result


def cmd_task_groups(args) -> None:
    pretty(_api_json("GET", "/api/v1/task-groups"))


def cmd_task_group_show(args) -> None:
    pretty(_api_json("GET", f"/api/v1/task-groups/{_segment(args.group_id)}"))


def cmd_task_group_create(args) -> None:
    pretty(_api_json("POST", "/api/v1/task-groups", json_body={
        "id": args.group_id, "title": args.title, "actor": args.actor,
        "authorized": args.authorized, "default_requires": args.requires,
    }))


def cmd_task_group_state(args) -> None:
    pretty(_api_json("POST", f"/api/v1/task-groups/{_segment(args.group_id)}/state", json_body={
        "state": args.state, "actor": args.actor, "cause": args.cause,
        "expected_revision": args.revision,
    }))


def cmd_task_wait(args) -> None:
    body = {"actor": args.actor, "expected_revision": args.revision}
    path = f"/api/v1/tasks/{_segment(args.task_id)}/waiting"
    if args.wait_action == "set":
        body.update(who=args.who, question=args.question, target=args.target,
                    reference=args.reference, reset_at=args.reset_at)
        method = "PUT"
    else:
        body["wait_id"] = args.wait_id
        path += "/clear" if args.wait_action == "clear" else "/decision"
        if args.wait_action == "resolve":
            body["decision"] = args.decision
        method = "POST"
    pretty(_api_json(method, path, json_body=body))


def cmd_task_scope(args) -> None:
    policy = None
    if args.shared is not None or args.workspace is not None:
        policy = {"shared_scope": args.shared or [], "workspace_scope": args.workspace or []}
    body = {"file_scope": args.file or [], "actor": args.actor,
            "expected_revision": args.revision, "workspace_domain": args.domain, "lock_policy": policy}
    pretty(_api_json("POST", f"/api/v1/tasks/{_segment(args.task_id)}/scope", json_body=body))


def cmd_task_group_update(args) -> None:
    body = {"actor": args.actor}
    if args.title is not None:
        body["title"] = args.title
    if args.authorized is not None:
        body["authorized"] = args.authorized
    if args.requires is not None:
        body["default_requires"] = args.requires
    pretty(_api_json("PATCH", f"/api/v1/task-groups/{_segment(args.group_id)}", json_body=body))


def cmd_tasks(args) -> None:
    if args.ready:
        params = {"group": args.group} if args.group else None
        pretty(_api_json("GET", "/api/v1/tasks/ready", params=params))
        return
    params = {key: value for key, value in (("group", args.group), ("status", args.status), ("assignee", args.assignee)) if value}
    pretty(_api_json("GET", "/api/v1/tasks", params=params or None))


def cmd_task_show(args) -> None:
    pretty(_api_json("GET", f"/api/v1/tasks/{_segment(args.task_id)}"))


def cmd_task_assignment_preview(args) -> None:
    body = {key: value for key, value in (
        ("profile_id", args.profile_id), ("requested_executor", args.executor),
        ("requested_model", args.model), ("requested_effort", args.effort),
        ("required_context_isolation", args.context_isolation),
        ("additional_requirements", args.requirement), ("permission_scope", args.permission),
        ("data_class", args.data_class), ("step_kind", args.step_kind),
    ) if value is not None}
    pretty(_api_json("POST", f"/api/v1/tasks/{_segment(args.task_id)}/assignment/preview", json_body=body))


def cmd_task_assignment_accept(args) -> None:
    pretty(_api_json("POST", f"/api/v1/tasks/{_segment(args.task_id)}/assignment/accept",
                     json_body={"preview_token": args.preview_token, "acknowledge": args.acknowledge}))


def cmd_executors(args) -> None:
    """Run, resume or open a task in an executor application, or show the executors (ORQ-T51)."""
    if args.executors_command == "status":
        pretty(_api_json("GET", "/api/v1/executors/status", params={"task": args.task} if args.task else None))
        return
    if args.executors_command == "resume-due":
        body = {key: value for key, value in (("task_id", args.task), ("limit", args.limit),
                                              ("timeout_seconds", args.timeout)) if value is not None}
        body["dry_run"] = bool(args.dry_run)
        pretty(_api_json("POST", "/api/v1/executors/resume-due", json_body=body, timeout=None))
        return
    if args.executors_command == "decide-holds":
        body = {key: value for key, value in (("task_id", args.task), ("limit", args.limit),
                                              ("timeout_seconds", args.timeout)) if value is not None}
        body["dry_run"] = bool(args.dry_run)
        body["run"] = not args.no_run
        pretty(_api_json("POST", "/api/v1/executors/decide-holds", json_body=body, timeout=None))
        return
    if args.executors_command == "handoff":
        body = {"task_id": args.task_id}
        body.update({key: value for key, value in (
            ("executor_id", args.executor), ("decision_id", args.decision_id),
            ("expected_revision", args.expected_revision), ("actor", args.actor),
            ("timeout_seconds", args.timeout)) if value is not None})
        body["run"] = not args.no_run
        pretty(_api_json("POST", "/api/v1/executors/handoff", json_body=body, timeout=None))
        return
    if args.executors_command == "lift-pause":
        body = {"executor_id": args.executor_id, "reason": args.reason or ""}
        if args.actor:
            body["actor"] = args.actor
        pretty(_api_json("POST", "/api/v1/executors/lift-pause", json_body=body))
        return
    body = {"task_id": args.task_id}
    if args.executors_command == "open":
        if args.actor:
            body["actor"] = args.actor
        pretty(_api_json("POST", "/api/v1/executors/open", json_body=body))
        return
    body.update({key: value for key, value in (("model", args.model), ("effort", args.effort), ("actor", args.actor),
                                               ("timeout_seconds", args.timeout)) if value is not None})
    if args.executors_command == "run":
        body.update({key: value for key, value in (("executor_id", args.executor), ("profile_id", args.profile_id),
                                                    ("step_kind", args.step_kind)) if value is not None})
    # ``timeout=None``: the attempt can take minutes and the server answers when it ends.
    pretty(_api_json("POST", f"/api/v1/executors/{args.executors_command}", json_body=body, timeout=None))


def cmd_task_create(args) -> None:
    pretty(_api_json("POST", "/api/v1/tasks", json_body={
        "group_id": args.group_id, "id": args.task_id, "title": args.title, "actor": args.actor,
        "objective": args.objective, "depends_on": _parse_depends_on(args.depends_on),
        "release_dependencies": _parse_release_dependencies(args.release_dep),
        "file_scope": args.file or [], "requirements": args.requirement or [], "preferred": args.preferred,
        "context_refs": args.context_ref or [],
        "reopen_group": args.reopen_group, "expected_group_revision": args.group_revision, "cause": args.cause,
    }))


def cmd_task_dep(args) -> None:
    task = _segment(args.task_id)
    if args.operation == "add":
        body = {"depends_on_id": args.depends_on_id, "actor": args.actor}
        if args.requires:
            body["requires"] = args.requires
        pretty(_api_json("POST", f"/api/v1/tasks/{task}/dependencies", json_body=body))
    else:
        pretty(_api_json("DELETE", f"/api/v1/tasks/{task}/dependencies/{_segment(args.depends_on_id)}",
                         params={"actor": args.actor}))


def cmd_task_do(args) -> None:
    body = {"action": args.action, "actor": args.actor, "cause": args.cause}
    if args.revision is not None:
        body["expected_revision"] = args.revision
    if args.actor_domain is not None:
        body["actor_domain"] = args.actor_domain
    pretty(_api_json("POST", f"/api/v1/tasks/{_segment(args.task_id)}/transitions", json_body=body))


def cmd_task_snapshot(args) -> None:
    if args.operation == "import":
        try:
            body = json.loads(Path(args.file).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Could not read snapshot JSON file '{args.file}': {exc}") from exc
        if not isinstance(body, dict):
            raise ValueError("Snapshot file must contain a JSON object.")
        required = {"snapshot", "source_key", "source_ref", "snapshot_sha256"}
        missing = sorted(required - body.keys())
        extra = sorted(body.keys() - required)
        if missing or extra:
            details = []
            if missing:
                details.append("missing fields: " + ", ".join(missing))
            if extra:
                details.append("unsupported fields: " + ", ".join(extra))
            raise ValueError("Invalid snapshot file (" + "; ".join(details) + ").")
        if not isinstance(body["snapshot"], dict):
            raise ValueError("Snapshot file field 'snapshot' must be a JSON object.")
        body["actor"] = args.actor
        if args.what_if:
            body["what_if"] = True
        pretty(_api_json("POST", "/api/v1/task-snapshots/import", json_body=body))
    elif args.operation == "show":
        pretty(_api_json("GET", f"/api/v1/task-snapshots/{_segment(args.source_key)}"))
    else:
        pretty(_api_json("POST", f"/api/v1/task-snapshots/{_segment(args.source_key)}/rollback",
                         json_body={"actor": args.actor}))


def cmd_task_gate(args) -> None:
    task = _segment(args.task_id)
    if args.operation == "show":
        pretty(_api_json("GET", f"/api/v1/tasks/{task}/gate"))
    elif args.operation == "results":
        params = {"attempt": args.attempt} if args.attempt is not None else None
        pretty(_api_json("GET", f"/api/v1/tasks/{task}/gate/results", params=params))
    else:
        if not args.actor:
            raise ValueError(f"task gate {args.operation} needs --actor")
        if args.operation == "set":
            pretty(_api_json("PUT", f"/api/v1/tasks/{task}/gate", json_body={"checks": args.check or [], "actor": args.actor}))
        elif args.operation == "run":
            pretty(_api_json("POST", f"/api/v1/tasks/{task}/gate/run", json_body={"actor": args.actor, "cause": args.cause},
                             timeout=None))
        else:  # abort
            pretty(_api_json("POST", f"/api/v1/tasks/{task}/gate/abort", json_body={"actor": args.actor, "cause": args.cause}))


def cmd_task_review(args) -> None:
    pretty(_api_json("POST", f"/api/v1/tasks/{_segment(args.task_id)}/reviews",
                     json_body={"actor": args.actor, "approved": args.approve, "comment": args.comment}))


def cmd_task_group_gate(args) -> None:
    pretty(_api_json("PUT", f"/api/v1/task-groups/{_segment(args.group_id)}/gate",
                     json_body={"checks": args.check or [], "actor": args.actor}))


def cmd_project_commands(args) -> None:
    pretty(_api_json("GET", "/api/v1/project-commands"))


def cmd_task_context(args) -> None:
    body = {"actor": args.actor}
    if args.base_commit is not None:
        body["base_commit"] = args.base_commit
    if args.context_ref is not None or args.clear_context_refs:
        body["context_refs"] = args.context_ref or []
    if args.constraint is not None or args.clear_constraints:
        body["constraints"] = args.constraint or []
    pretty(_api_json("PUT", f"/api/v1/tasks/{_segment(args.task_id)}/context", json_body=body))


def cmd_task_envelope(args) -> None:
    params = {"known_hash": args.known_hash} if args.known_hash else None
    pretty(_api_json("GET", f"/api/v1/tasks/{_segment(args.task_id)}/envelope", params=params))


def cmd_task_context_v2(args) -> None:
    params = {"after": args.after}
    if args.known_fingerprint:
        params["known_fingerprint"] = args.known_fingerprint
    pretty(_api_json("GET", f"/api/v2/tasks/{_segment(args.task_id)}/context", params=params))


def cmd_task_release(args) -> None:
    if args.release_command == "mode":
        pretty(_api_json("PUT", f"/api/v1/task-groups/{_segment(args.group_id)}/release-mode",
                         json_body={"mode": args.mode, "actor": args.actor}))
    elif args.release_command == "dep":
        path = f"/api/v1/tasks/{_segment(args.task_id)}/release-dependencies"
        if args.operation == "add":
            pretty(_api_json("POST", path, json_body={"depends_on_id": args.depends_on_id,
                                                      "source_ref": args.source_ref, "actor": args.actor}))
        else:
            pretty(_api_json("DELETE", f"{path}/{_segment(args.depends_on_id)}",
                             params={"actor": args.actor}))
    elif args.facts_command == "show":
        pretty(_api_json("GET", f"/api/v1/tasks/{_segment(args.task_id)}/release-facts"))
    elif args.facts_command == "delivery":
        body = {"delivery_ref": args.delivery_ref, "delivery_sha256": args.delivery_sha256,
                "has_patch": args.has_patch, "patchset_sha256": args.patchset_sha256 or "",
                "actor": args.actor}
        pretty(_api_json("POST", f"/api/v1/tasks/{_segment(args.task_id)}/release-facts/delivery", json_body=body))
    elif args.facts_command == "test":
        body = {"test_verdict": args.test_verdict, "test_patchset_sha256": args.test_patchset_sha256,
                "test_ref": args.test_ref, "actor": args.actor}
        pretty(_api_json("POST", f"/api/v1/tasks/{_segment(args.task_id)}/release-facts/test", json_body=body))
    elif args.facts_command == "integration":
        body = {"integration_commit": args.integration_commit, "integration_ref": args.integration_ref,
                "actor": args.actor}
        pretty(_api_json("POST", f"/api/v1/tasks/{_segment(args.task_id)}/release-facts/integration",
                         json_body=body))
    else:  # reset
        pretty(_api_json("POST", f"/api/v1/tasks/{_segment(args.task_id)}/release-facts/reset",
                         json_body={"actor": args.actor, "reason": args.reason}))


def cmd_task_result(args) -> None:
    if args.operation == "submit":
        if not args.actor or not args.file:
            raise ValueError("task result submit needs --actor and --file")
        result = json.loads(Path(args.file).read_text(encoding="utf-8"))
        pretty(_api_json("POST", f"/api/v1/tasks/{_segment(args.target)}/results",
                         json_body={"actor": args.actor, "result": result}))
    elif args.operation == "list":
        pretty(_api_json("GET", f"/api/v1/tasks/{_segment(args.target)}/results"))
    elif args.target.startswith("result://"):  # show by reference
        pretty(_api_json("GET", "/api/v1/results", params={"ref": args.target}))
    else:  # show by task id: the latest attempt unless --attempt
        if args.attempt is None:
            results = _api_json("GET", f"/api/v1/tasks/{_segment(args.target)}/results")
            if not results:
                raise RuntimeError(f"Task {args.target} has no result")
            pretty(_api_json("GET", "/api/v1/results", params={"ref": results[0]["ref"]}))
        else:
            pretty(_api_json("GET", f"/api/v1/tasks/{_segment(args.target)}/results", params={"attempt": args.attempt}))


def cmd_blob_put(args) -> None:
    params = {"actor": args.actor, "media_type": args.media_type}
    pretty(_api_json("POST", "/api/v1/blobs", params=params, content=Path(args.file).read_bytes()))


def cmd_artifact_get(args) -> None:
    piece = _api_json("GET", "/api/v1/artifacts", params={"ref": args.ref, "offset": args.offset, "length": args.length})
    if not args.output:
        pretty(piece)
        return
    data = piece["content"].encode("utf-8") if piece["encoding"] == "utf-8" else base64.b64decode(piece["content"])
    Path(args.output).write_bytes(data)
    print(f"{len(data)} bytes from offset {piece['offset']} of {piece['size']} written to {args.output}"
          f"{'' if piece['eof'] else ' (more remain: use --offset)'}")


def cmd_cursor(args) -> None:
    consumer = _segment(args.consumer)
    if args.operation == "show":
        pretty(_api_json("GET", f"/api/v1/cursors/{consumer}", params={"scope": args.scope}))
    elif args.operation == "events":
        params = {"scope": args.scope, "mine": args.mine, "limit": args.limit}
        if args.type:
            params["type"] = args.type
        pretty(_api_json("GET", f"/api/v1/cursors/{consumer}/events", params=params))
    else:  # ack
        if args.seq is None:
            raise ValueError("cursor ack needs --seq")
        pretty(_api_json("POST", f"/api/v1/cursors/{consumer}/ack", json_body={"seq": args.seq, "scope": args.scope}))


def cmd_team_events(args) -> None:
    params = {"after": args.after, "limit": args.limit}
    if args.group:
        params["group"] = args.group
    if args.task:
        params["task"] = args.task
    if args.project:
        params["project"] = args.project
    pretty(_api_json("GET", "/api/v1/team-events", params=params))


# --- projects (PRY): every command calls the project routes of the HTTP API -----------

MAX_PLAN_FILE_BYTES = 256 * 1024


def _plan_file(path: str) -> dict:
    """A plan or template file (JSON or YAML) as a plain dict; the server validates it against the schema."""
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise ValueError(f"Could not read plan file '{path}': {exc}") from exc
    if len(raw) > MAX_PLAN_FILE_BYTES:
        raise ValueError(f"Plan file '{path}' is larger than {MAX_PLAN_FILE_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            import yaml
            # An unquoted date in YAML is a date object: give it back as AAAA-MM-DD text.
            data = json.loads(json.dumps(yaml.safe_load(text), default=lambda value: value.isoformat()))
    except Exception as exc:  # undecodable bytes, invalid JSON or YAML, a value that is not data
        raise ValueError(f"Plan file '{path}' is not valid JSON or YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Plan file '{path}' must contain an object")
    return data


def _plan_body(args) -> dict:
    body = {"doc": _plan_file(args.file), "key": args.key}
    if args.title is not None:
        body["title"] = args.title
    if args.visibility is not None:
        body["visibility"] = args.visibility
    return body


def cmd_projects(args) -> None:
    pretty(_api_json("GET", "/api/v1/projects", params={"archived": "true"} if args.archived else None))


def cmd_project_show(args) -> None:
    pretty(_api_json("GET", f"/api/v1/projects/{_segment(args.project_id)}"))


def cmd_project_tasks(args) -> None:
    params = {name: value for name, value in (("owner", args.owner), ("state", args.state), ("due_from", args.due_from),
                                              ("due_to", args.due_to)) if value}
    pretty(_api_json("GET", f"/api/v1/projects/{_segment(args.project_id)}/tasks", params=params or None))


def cmd_project_plan(args) -> None:
    pretty(_api_json("POST", "/api/v1/projects/plan", json_body=_plan_body(args)))


def cmd_project_create(args) -> None:
    body = _plan_body(args)
    # Without --plan-sha256 the file is planned first and its hash used: the command itself is the confirmation.
    body["plan_sha256"] = args.plan_sha256 or _api_json("POST", "/api/v1/projects/plan", json_body=dict(body))["plan_sha256"]
    body["actor"] = args.actor
    pretty(_api_json("POST", "/api/v1/projects", json_body=body))


def cmd_project_update(args) -> None:
    body = {"actor": args.actor, "expected_revision": args.revision}
    if args.title is not None:
        body["title"] = args.title
    if args.visibility is not None:
        body["visibility"] = args.visibility
    pretty(_api_json("PATCH", f"/api/v1/projects/{_segment(args.project_id)}", json_body=body))


def cmd_project_archive(args) -> None:
    pretty(_api_json("POST", f"/api/v1/projects/{_segment(args.project_id)}/{args.project_command}",
                     json_body={"actor": args.actor, "expected_revision": args.revision}))


def cmd_project_stage(args) -> None:
    project = _segment(args.project_id)
    base = {"actor": args.actor, "expected_revision": args.revision}
    if args.stage_command == "attach":
        pretty(_api_json("POST", f"/api/v1/projects/{project}/stages", json_body={**base, "group_id": args.group_id}))
    elif args.stage_command == "order":
        pretty(_api_json("PUT", f"/api/v1/projects/{project}/stages", json_body={**base, "group_ids": args.group_ids}))
    else:  # close
        pretty(_api_json("POST", f"/api/v1/projects/{project}/stages/{_segment(args.group_id)}/close", json_body=base))


def cmd_activity(args) -> None:
    if args.activity_command == "start":
        body = {"title": args.title, "actor": args.actor}
        for name in ("executor", "idempotency_key", "objective"):
            if getattr(args, name) is not None:
                body[name] = getattr(args, name)
        pretty(_api_json("POST", f"/api/v1/projects/{_segment(args.project_id)}/activities", json_body=body))
    elif args.activity_command == "close":
        pretty(_api_json("POST", f"/api/v1/activities/{_segment(args.activity_id)}/close",
                         json_body={"summary": args.summary, "actor": args.actor}))
    else:  # list
        params = {name: getattr(args, name) for name in ("status", "since") if getattr(args, name) is not None}
        pretty(_api_json("GET", f"/api/v1/projects/{_segment(args.project_id)}/activities", params=params or None))


def cmd_task_session(args) -> None:
    task = _segment(args.task_id)
    if args.session_command == "link":
        pretty(_api_json("POST", f"/api/v1/tasks/{task}/sessions",
                         json_body={"session_id": args.session_id, "actor": args.actor}))
    else:
        pretty(_api_json("DELETE", f"/api/v1/tasks/{task}/sessions",
                         params={"session_id": args.session_id, "actor": args.actor}))


def cmd_task_labels(args) -> None:
    pretty(_api_json("GET", "/api/v1/task-labels"))


def cmd_usage(args) -> None:
    run_id = quote(args.run_id, safe="")
    usage = _api_json("GET", f"/api/v1/runs/{run_id}/usage")
    if not args.jsonl:
        pretty(usage)
        return
    for record in usage["records"]:
        print(json.dumps({"run_id": usage["run_id"], **record}, ensure_ascii=False, default=str))


def cmd_run(args) -> None:
    if args.workflow:
        if (args.objective is not None or args.topic is not None or args.web
                or args.no_brand or args.audience or args.instructions
                or args.platform != "Instagram" or args.pipeline != "quick"
                or args.research_mode != "human_bridge"):
            raise ValueError("Generic workflows use --input name=value for domain inputs")
        payload = {
            "workflow_id": args.workflow,
            "project_name": args.project,
            "inputs": _parse_inputs(args.input),
            "idempotency_key": args.idempotency_key,
            "execution_mode": args.mode,
            "step_modes": _parse_step_modes(args.step_mode),
            "sensitive": args.sensitive,
        }
        pretty(_api_json("POST", "/api/v1/runs", json_body=payload, params=_bundle_params(args)))
        return
    if args.bundle or args.input:
        raise ValueError("--bundle and --input require --workflow")
    if args.objective is None or args.topic is None:
        raise ValueError("Social-post runs require --objective and --topic")
    payload = {
        "project_name": args.project,
        "objective": args.objective,
        "topic": args.topic,
        "platform": args.platform,
        "audience": args.audience,
        "instructions": args.instructions,
        "requires_web": args.web,
        "use_brand_context": not args.no_brand,
        "idempotency_key": args.idempotency_key,
        "execution_mode": args.mode,
        "research_mode": args.research_mode if args.web else "none",
        "pipeline_mode": args.pipeline,
        "step_modes": _parse_step_modes(args.step_mode),
        "sensitive": args.sensitive,
    }
    pretty(_api_json("POST", "/api/v1/runs", json_body=payload))


def cmd_status(args) -> None:
    pretty(_api_json("GET", f"/api/v1/runs/{args.run_id}"))


def cmd_runs(args) -> None:
    result = _api_json("GET", "/api/v1/runs", params={"limit": args.limit, "status": args.status, "project": args.project})
    pretty(result)


def cmd_run_report(args) -> None:
    run_id = quote(args.run_id, safe="")
    result = _api_json("GET", f"/api/v1/runs/{run_id}/report",
                       params={"format": "json", "include_raw": str(bool(args.include_raw)).lower()})
    if args.format == "markdown":
        from .services.reports import render_run_report_markdown

        print(render_run_report_markdown(result))
    else:
        pretty(result)


def _approve_with_token(run_id: str, permission_request_id: str | None = None):
    """Approve a run through the token route of the GUI (``/gui/api``), with the token this user keeps in their profile."""
    from .gui.token import read_token

    settings = get_settings()
    token = read_token(settings.gui_token_file)
    if token is None:
        raise RuntimeError(f"person_required: this approval needs the GUI token and none was found at {settings.gui_token_file}; "
                           "start `multiagent gui` once or approve it in the GUI")
    with api_client() as client:
        body = {"permission_request_id": permission_request_id} if permission_request_id else {}
        response = client.post(f"/gui/api/runs/{run_id}/approve", json=body, headers={"Authorization": f"Bearer {token}"})
    if response.status_code >= 400:
        # Whatever the server answered, the token never goes into the message.
        raise RuntimeError(f"HTTP {response.status_code}: {_detail(response)}".replace(token, "[token]"))
    return response.json()


def cmd_approve(args) -> None:
    permission_id = getattr(args, "permission_request", None)
    if permission_id:
        # Naming a tool request goes straight to the route with the token, never through the unauthenticated one.
        pretty(_approve_with_token(args.run_id, permission_id))
        return
    try:
        pretty(_api_json("POST", f"/api/v1/runs/{args.run_id}/approve"))
    except RuntimeError as exc:
        if "person_required" not in str(exc):
            raise
        # A budget cut spends money (USO-T04) and a tool call acts on the world (CAP-T14): /api/v1 has no token and refuses
        # them, so the person at this terminal goes through the route that has one.
        run = _api_json("GET", f"/api/v1/runs/{args.run_id}")
        if run.get("waiting_reason") == "tool_approval" and run.get("status") == "waiting_human" and not permission_id:
            # Show the request and approve nothing: the person approves by repeating the id they read here.
            pending = run.get("tool_approval")
            # ASCII-only output: what the request carries is shown escaped, never as raw Unicode controls.
            print(json.dumps({"approved": False, "reason": "permission_request_required", "tool_approval": pending,
                              "next": ("repeat with --permission-request " + pending["permission_request_id"]) if pending
                                      else "no readable pending request: reject the run"},
                             ensure_ascii=True, indent=2, default=str))
            raise SystemExit(2)
        pretty(_approve_with_token(args.run_id))


def cmd_changes(args) -> None:
    pretty(_api_json("POST", f"/api/v1/runs/{args.run_id}/changes", json_body={"feedback": args.feedback}))


def cmd_reject(args) -> None:
    pretty(_api_json("POST", f"/api/v1/runs/{args.run_id}/reject", json_body={"feedback": args.feedback}))


def cmd_regenerate(args) -> None:
    pretty(_api_json("POST", f"/api/v1/runs/{args.run_id}/regenerate"))


def cmd_cancel(args) -> None:
    pretty(_api_json("POST", f"/api/v1/runs/{args.run_id}/cancel", params={"force": str(bool(args.force)).lower()}))


def cmd_human_next(args) -> None:
    result = _api_json("GET", f"/api/v1/runs/{args.run_id}/human-next")
    if args.prompt_only:
        print(result["prompt"])
    else:
        pretty(result)


def cmd_human_submit(args) -> None:
    raw = Path(args.file).read_text(encoding="utf-8")
    prompt_used = Path(args.prompt_used_file).read_text(encoding="utf-8") if args.prompt_used_file else ""
    payload = {"raw_response": raw, "provider": args.provider, "model": args.model, "prompt_used": prompt_used, "notes": args.notes}
    pretty(_api_json("POST", f"/api/v1/runs/{args.run_id}/human-submit", json_body=payload))


def cmd_asset_add(args) -> None:
    system = build_system(get_settings())
    if not args.project_id and not args.project:
        raise ValueError("asset-add requires --project or --project-id")
    project_id = args.project_id or system.store.get_or_create_project(args.project)
    metadata = AssetMetadata(
        project_id=project_id,
        source=AssetSource(args.source),
        status=AssetStatus(args.status),
        title=args.title,
        notes=args.notes,
        tags=args.tag or [],
        rejection_reason=args.rejection_reason,
        prompt_id=args.prompt_id,
        prompt_version=args.prompt_version,
        model_id=args.model_id,
        run_id=args.run_id,
        step_id=args.step_id,
        attempt=args.attempt,
    )
    pretty(AssetService(system.settings, system.store).ingest(args.file, metadata))


def cmd_publication_add(args) -> None:
    system = build_system(get_settings())
    if not args.project_id and not args.project:
        raise ValueError("publication-add requires --project or --project-id")
    project_id = args.project_id or system.store.get_or_create_project(args.project)
    pid = system.store.add_publication(
        project_id=project_id, platform=args.platform, asset_id=args.asset_id or None,
        artifact_id=args.artifact_id or None, external_url=args.url, objective=args.objective,
        audience=args.audience, brand_version=args.brand_version, status=args.status,
    )
    pretty({"publication_id": pid, "project_id": project_id, "status": args.status})


def cmd_metrics_add(args) -> None:
    system = build_system(get_settings())
    metrics = json.loads(args.metrics_json)
    raw = json.loads(Path(args.raw_file).read_text(encoding="utf-8")) if args.raw_file else {}
    mid = system.store.add_metric_snapshot(MetricSnapshotInput(publication_id=args.publication_id, platform=args.platform, metrics=metrics, raw_payload=raw))
    pretty({"metric_snapshot_id": mid})


def cmd_brand_generate(args) -> None:
    answers = Path(args.answers).read_text(encoding="utf-8")
    if not args.dry_run:
        pretty(build_system(get_settings()).brand_service.generate_from_answers(answers, model_id=args.model)); return
    with tempfile.TemporaryDirectory(prefix="multiagent-brand-dryrun-") as tmp:
        settings = Settings(data_dir=Path(tmp), db_path=None, runs_dir=None, assets_dir=None, worker_enabled=False)
        pretty(build_system(settings, dry_run=True).brand_service.generate_from_answers(answers, model_id=args.model))


def cmd_brand_activate(args) -> None:
    system = build_system(get_settings())
    if args.version is not None:
        pretty(system.brand_service.activate_version(args.version))
    elif args.profile_id:
        pretty(system.brand_service.activate(args.profile_id))
    else:
        raise ValueError("brand-activate requires profile_id or --version")


def cmd_brand_import(args) -> None:
    system = build_system(get_settings())
    payload = BrandProfilePayload.model_validate_json(Path(args.file).read_text(encoding="utf-8"))
    pretty(system.store.save_brand_profile(payload, created_by=args.created_by, activate=args.activate))


def cmd_brand_show(args) -> None:
    system = build_system(get_settings()); profile = system.store.active_brand_profile()
    pretty({"active_id": system.store.active_brand_id(), "active_version": system.store.active_brand_version(), "profile": profile.model_dump(mode="json") if profile else None})


def cmd_brand_list(args) -> None:
    pretty(build_system(get_settings()).store.list_brand_profiles(args.limit))


def _run_dry_run(args, settings: Settings) -> dict:
    system = build_system(settings, dry_run=True)
    request = SocialPostRequest(
        project_name=args.project, objective=args.objective, topic=args.topic, platform=args.platform,
        audience=args.audience, instructions=args.instructions, requires_web=args.web,
        research_mode=ResearchMode.GEMINI_GROUNDED if args.web else ResearchMode.NONE,
        pipeline_mode=PipelineMode(args.pipeline), execution_mode=ExecutionMode.AUTO,
    )
    run = system.run_service.create_social_post(request)
    while system.worker.process_once():
        state = system.run_service.get(run["id"])
        if state["status"] != "queued":
            break
    return system.run_service.get(run["id"])


def cmd_dry_run(args) -> None:
    if args.persist:
        pretty(_run_dry_run(args, get_settings())); return
    with tempfile.TemporaryDirectory(prefix="multiagent-dryrun-") as tmp:
        settings = Settings(data_dir=Path(tmp), db_path=None, runs_dir=None, assets_dir=None, worker_enabled=False)
        pretty(_run_dry_run(args, settings))


def _parse_ui_bundles(values: list[str] | None) -> dict[str, Path]:
    bundles: dict[str, Path] = {}
    for value in values or []:
        name, separator, folder = value.partition("=")
        if not separator or not name.strip() or not folder.strip():
            raise ValueError("--bundle uses the format name=folder")
        path = Path(folder.strip()).expanduser()
        if not path.is_dir():
            raise ValueError(f"Bundle folder not found: {path}")
        bundles[name.strip()] = path
    return bundles


def _add_bundle_argument(parser: argparse.ArgumentParser) -> None:
    """The ``--bundle NAME=FOLDER`` option of ``multiagent ui`` and ``multiagent gui`` (defined once)."""
    parser.add_argument("--bundle", action="append", metavar="NAME=FOLDER",
                        help="Register a trusted definition bundle (its contracts.py is imported); repeatable")


def _check_port_free(host: str, port: int) -> None:
    import socket

    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError as exc:
            raise RuntimeError(
                f"Port {port} on {host} is already in use. Stop the other server or choose another port with --port."
            ) from exc


def cmd_ui(args) -> None:
    """Start the API with the Stage 0 UI, in Mock mode unless --real, and open the browser."""
    import os
    import threading
    import webbrowser

    from .api import create_app
    from .gui.__main__ import serve

    if not args.real:
        os.environ["MOCK_MODE"] = "true"  # synthetic fixtures, no model calls or API keys
    settings = Settings()
    settings.ensure_directories()
    host = settings.app_host
    port = args.port or settings.app_port
    bundles = _parse_ui_bundles(args.bundle)
    _check_port_free(host, port)
    app = create_app(build_system(settings), bundles=bundles)

    url = f"http://{f'[{host}]' if ':' in host else host}:{port}/ui/"
    mode = "REAL models (provider calls may cost money)" if args.real else "Mock (synthetic results, no API keys)"
    print(f"Multi-Agent Framework UI: {url}")
    print(f"Mode: {mode}")
    if bundles:
        print(f"Bundles: {', '.join(sorted(bundles))}")
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    serve(app, host=host, port=port, stopped="UI detenida.")


def add_gui_arguments(parser: argparse.ArgumentParser) -> None:
    """Options of ``multiagent gui`` and ``python -m multiagent.gui`` (defined once)."""
    parser.add_argument("--port", type=int, default=0, help="Port (default: app_port from settings)")
    parser.add_argument("--real", action="store_true", help="Use real models (default: Mock mode)")
    parser.add_argument("--no-browser", action="store_true", help="Print the URL without opening the browser")
    _add_bundle_argument(parser)


def cmd_gui(args) -> None:
    """Start the local server with the new GUI; same as ``python -m multiagent.gui``."""
    from .gui import __main__ as gui_main

    gui_main.run(port=args.port, real=args.real, no_browser=args.no_browser, bundles=_parse_ui_bundles(args.bundle))


def _session_services():
    """The session service the GUI routes use, built in-process: session data is private and local,
    so the CLI reads it directly instead of going through the (token-protected) HTTP routes."""
    from .services import ApplicationServices

    return ApplicationServices.create(get_settings()).sessions


def cmd_sessions(args) -> None:
    """List, show or read the items of Claude, Codex, Copilot and framework sessions (read-only)."""
    from .services import ServiceError

    service = _session_services()
    try:
        if args.sessions_command == "list":
            result = service.list_sessions(source=args.source, surface=args.surface, cwd=args.cwd, since=args.since,
                                           until=args.until, text=args.text, limit=args.limit, after=args.after)
        elif args.sessions_command == "show":
            result = service.get_session(args.session_id)
        else:
            result = service.read_items(args.session_id, after=args.after, limit=args.limit)
    except ServiceError as exc:
        raise RuntimeError(f"{exc.code}: {exc.message}") from exc
    if getattr(args, "jsonl", False):
        if result.get("hint"):
            print(result["hint"], file=sys.stderr)
        for row in result.get("sessions", result.get("items", [])):
            print(json.dumps(row, ensure_ascii=False, default=str))
    else:
        pretty(result)


def _usage_services():
    """The usage service the GUI routes use, built in-process: plan usage and the apps' tokens are private local
    data, so the CLI reads them directly instead of going through the (token-protected) HTTP routes."""
    from .services import ApplicationServices

    return ApplicationServices.create(get_settings()).usage


def cmd_usage_report(args) -> None:
    """Plan usage per app, tokens per app/model/level/role and the estimated cost of provider calls (read-only)."""
    from .services import ServiceError

    service = _usage_services()
    try:
        if args.usage_report_command == "plans":
            result, rows = service.plans(), "apps"
        elif args.usage_report_command == "tokens":
            result = service.tokens(since=args.since, until=args.until, apps=args.apps, group_by=args.group_by)
            rows = "rows"
        else:
            result, rows = service.costs(since=args.since, until=args.until, limit=args.limit), "by_run"
    except ServiceError as exc:
        raise RuntimeError(f"{exc.code}: {exc.message}") from exc
    if getattr(args, "jsonl", False):
        for row in result[rows]:
            print(json.dumps(row, ensure_ascii=False, default=str))
    else:
        pretty(result)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="multiagent", description=f"Multi-Agent Framework {__version__} — Hybrid Human/AI Orchestration")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--debug", action="store_true", help="Show the full traceback when an error occurs")
    sub = p.add_subparsers(dest="command", required=True)

    x = sub.add_parser("init"); x.set_defaults(func=cmd_init)
    x = sub.add_parser("doctor"); x.add_argument("--deep", action="store_true"); x.set_defaults(func=cmd_doctor)
    try:
        from .setup.cli import add_parser as add_setup_parser
    except Exception:
        x = sub.add_parser("setup", help="CLI detection unavailable")
        def unavailable_setup(_args):
            raise RuntimeError("setup is unavailable: its module could not be imported")
        x.set_defaults(func=unavailable_setup)
    else:
        add_setup_parser(sub)
    x = sub.add_parser("ui", help="Start the API with the Stage 0 UI (Mock mode unless --real) and open the browser")
    x.add_argument("--real", action="store_true", help="Use the configured model providers instead of Mock mode")
    x.add_argument("--port", type=int, help="Port to listen on (default: APP_PORT, 8000)")
    _add_bundle_argument(x)
    x.add_argument("--no-browser", action="store_true", help="Do not open the browser")
    x.set_defaults(func=cmd_ui)

    x = sub.add_parser("gui", help="Start the local server with the new GUI (Mock mode unless --real) and open the browser")
    add_gui_arguments(x); x.set_defaults(func=cmd_gui)
    sess = sub.add_parser("sessions", help="Read Claude, Codex, Copilot and framework sessions (local, read-only)")
    sess = sess.add_subparsers(dest="sessions_command", required=True)
    x = sess.add_parser("list", help="List sessions, newest first (empty until GUI_SESSION_ROOTS is set)")
    x.add_argument("--source", help="claude, codex, copilot or framework"); x.add_argument("--surface", help="desktop, cli, framework or unknown")
    x.add_argument("--cwd", help="Only sessions whose folder is under this one"); x.add_argument("--since", help="ISO-8601, compared with the last update")
    x.add_argument("--until", help="ISO-8601, compared with the last update"); x.add_argument("--text", help="Part of the title")
    x.add_argument("--limit", type=int, default=100, help="1 to 500"); x.add_argument("--after", help="next_cursor of the previous page")
    x.add_argument("--jsonl", action="store_true", help="One JSON line per session"); x.set_defaults(func=cmd_sessions)
    x = sess.add_parser("show", help="Show one session's summary"); x.add_argument("session_id", help="<source>:<id>"); x.set_defaults(func=cmd_sessions)
    x = sess.add_parser("items", help="Read a session's items after a cursor")
    x.add_argument("session_id", help="<source>:<id>"); x.add_argument("--after", help="next_cursor of the previous page")
    x.add_argument("--limit", type=int, default=200, help="1 to 500")
    x.add_argument("--jsonl", action="store_true", help="One JSON line per item"); x.set_defaults(func=cmd_sessions)

    x = sub.add_parser("run")
    x.add_argument("--project", required=True); x.add_argument("--objective"); x.add_argument("--topic")
    x.add_argument("--workflow", help="Workflow ID for a generic run")
    x.add_argument("--bundle", help="Bundle name registered on the server; requires --workflow")
    x.add_argument("--input", action="append", help="Generic workflow input name=value; repeat for each input")
    x.add_argument("--platform", default="Instagram"); x.add_argument("--audience", default=""); x.add_argument("--instructions", default="")
    x.add_argument("--web", action="store_true"); x.add_argument("--no-brand", action="store_true"); x.add_argument("--idempotency-key", default="")
    x.add_argument("--mode", choices=[m.value for m in ExecutionMode], default="auto")
    x.add_argument("--research-mode", choices=[m.value for m in ResearchMode], default="human_bridge")
    x.add_argument("--pipeline", choices=[m.value for m in PipelineMode], default="quick")
    x.add_argument("--step-mode", action="append", help="Override per step, e.g. strategy=human_guided")
    x.add_argument("--sensitive", action="store_true", help="Force local routing in AUTO and add a warning to Human Guided prompts")
    x.set_defaults(func=cmd_run)

    x = sub.add_parser("workflows", help="List workflows or inspect one workflow")
    x.add_argument("workflow_id", nargs="?"); x.add_argument("--bundle"); x.set_defaults(func=cmd_workflows)
    x = sub.add_parser("agents", help="List agent definitions")
    x.add_argument("--bundle"); x.set_defaults(func=cmd_agents)
    x = sub.add_parser("bundle-validate", help="Validate a bundle registered on the server")
    x.add_argument("name"); x.set_defaults(func=cmd_bundle_validate)
    x = sub.add_parser("artifacts", help="List artifacts for a run")
    x.add_argument("run_id"); x.set_defaults(func=cmd_artifacts)
    x = sub.add_parser("events", help="List a run's events, optionally only those after a sequence number")
    x.add_argument("run_id"); x.add_argument("--after", type=int, default=0, help="Only events with seq greater than this")
    x.set_defaults(func=cmd_events)
    x = sub.add_parser("usage", help="Show a run's token usage with totals per run, agent and step (cost is an estimate)")
    x.add_argument("run_id"); x.add_argument("--jsonl", action="store_true", help="One JSON line per usage record")
    x.set_defaults(func=cmd_usage)
    x = sub.add_parser("run-report", help="Export a run report")
    x.add_argument("run_id"); x.add_argument("--format", choices=("json", "markdown"), default="json")
    x.add_argument("--include-raw", action="store_true", help="Include complete run and event payloads")
    x.set_defaults(func=cmd_run_report)
    report = sub.add_parser("usage-report", help="Plan usage per app, tokens per app/model/level/role and estimated API cost "
                                                   "(local, read-only)")
    report = report.add_subparsers(dest="usage_report_command", required=True)
    x = report.add_parser("plans", help="What each app's plan allows and how much is used; 'not_available' where the app leaves no number")
    x.add_argument("--jsonl", action="store_true", help="One JSON line per app"); x.set_defaults(func=cmd_usage_report)
    x = report.add_parser("tokens", help="Tokens per app, model, level and role inside a time window (default: the last 7 days)")
    x.add_argument("--since", help="ISO-8601 start of the window"); x.add_argument("--until", help="ISO-8601 end of the window (exclusive)")
    x.add_argument("--app", dest="apps", action="append", help="claude, codex, copilot or framework; repeat or comma separate")
    x.add_argument("--group-by", help="Comma separated: app, model, level, role, day, month (default app,model,level,role)")
    x.add_argument("--jsonl", action="store_true", help="One JSON line per row"); x.set_defaults(func=cmd_usage_report)
    x = report.add_parser("costs", help="Estimated cost of the framework's provider calls per run, model, day and month, with evidence")
    x.add_argument("--since", help="ISO-8601 start of the window (default: 31 days before --until)")
    x.add_argument("--until", help="ISO-8601 end of the window (exclusive)")
    x.add_argument("--limit", type=int, default=100, help="Runs listed, 1 to 500")
    x.add_argument("--jsonl", action="store_true", help="One JSON line per run"); x.set_defaults(func=cmd_usage_report)

    # Tasks, dependencies and the team event log (ORQ-1).
    requires_choices = ["accepted", "submitted"]
    x = sub.add_parser("task-groups", help="List task groups with their task counts")
    x.set_defaults(func=cmd_task_groups)
    group = sub.add_parser("task-group", help="Show, create or update a task group").add_subparsers(dest="group_command", required=True)
    x = group.add_parser("show"); x.add_argument("group_id"); x.set_defaults(func=cmd_task_group_show)
    x = group.add_parser("create"); x.add_argument("group_id"); x.add_argument("--title", required=True)
    x.add_argument("--authorized", action="store_true"); x.add_argument("--requires", choices=requires_choices, default="accepted")
    x.add_argument("--actor", required=True); x.set_defaults(func=cmd_task_group_create)
    x = group.add_parser("update"); x.add_argument("group_id"); x.add_argument("--title")
    x.add_argument("--authorized", dest="authorized", action="store_true", default=None)
    x.add_argument("--not-authorized", dest="authorized", action="store_false")
    x.add_argument("--requires", choices=requires_choices); x.add_argument("--actor", required=True)
    x.set_defaults(func=cmd_task_group_update)
    x = group.add_parser("gate", help="Set the group's default gate; no --check clears it")
    x.add_argument("group_id"); x.add_argument("--check", action="append", help="tests, commit, review or command:<name>; repeatable")
    x.add_argument("--actor", required=True); x.set_defaults(func=cmd_task_group_gate)
    x = sub.add_parser("tasks", help="List tasks, or only ready tasks of authorized groups with --ready")
    x.add_argument("--group"); x.add_argument("--status"); x.add_argument("--assignee"); x.add_argument("--ready", action="store_true")
    x.set_defaults(func=cmd_tasks)
    task = sub.add_parser("task", help="Show, create, link or change a task").add_subparsers(dest="task_command", required=True)
    # Explicit lifecycle transition: task group state set ID STATE --actor A --cause C --revision N
    task_group = task.add_parser("group", help="Change task group lifecycle").add_subparsers(dest="group_action", required=True)
    state_sub = task_group.add_parser("state").add_subparsers(dest="state_action", required=True)
    x = state_sub.add_parser("set"); x.add_argument("group_id"); x.add_argument("state", choices=["open", "closed"])
    x.add_argument("--actor", required=True); x.add_argument("--cause", required=True); x.add_argument("--revision", type=int, required=True)
    x.set_defaults(func=cmd_task_group_state)
    x = task.add_parser("show"); x.add_argument("task_id"); x.set_defaults(func=cmd_task_show)
    x = task.add_parser("labels", help="Show the shared map of task and stage labels"); x.set_defaults(func=cmd_task_labels)
    session = task.add_parser("session", help="Link a chat of another app to a task, or remove the link")
    session = session.add_subparsers(dest="session_command", required=True)
    for name in ("link", "unlink"):
        x = session.add_parser(name); x.add_argument("task_id"); x.add_argument("session_id", help="<source>:<id>, never framework:")
        x.add_argument("--actor", required=True); x.set_defaults(func=cmd_task_session)
    assignment = task.add_parser("assignment", help="Preview or acknowledge a task assignment plan")
    assignment_sub = assignment.add_subparsers(dest="assignment_command", required=True)
    x = assignment_sub.add_parser("preview")
    x.add_argument("task_id"); x.add_argument("--profile-id"); x.add_argument("--executor")
    x.add_argument("--model"); x.add_argument("--effort"); x.add_argument("--context-isolation", choices=["fresh", "inherited", "unknown"])
    x.add_argument("--requirement", action="append"); x.add_argument("--permission", action="append")
    x.add_argument("--data-class")
    x.add_argument("--step-kind", choices=["orchestration", "planning", "review", "work"],
                   help="Kind of step the model routing profile selects for (default work)")
    x.set_defaults(func=cmd_task_assignment_preview)
    x = assignment_sub.add_parser("accept")
    x.add_argument("task_id"); x.add_argument("preview_token"); x.add_argument("--acknowledge", action="store_true")
    x.set_defaults(func=cmd_task_assignment_accept)
    x = task.add_parser("create"); x.add_argument("group_id"); x.add_argument("task_id"); x.add_argument("--title", required=True)
    x.add_argument("--objective", default=""); x.add_argument("--depends-on", action="append", metavar="ID[:requires]")
    x.add_argument("--release-dep", action="append", metavar="ID=SOURCE_REF",
                   help="Add an atomic legacy release dependency; repeatable")
    x.add_argument("--file", action="append", help="A path or folder the task will change; repeatable")
    x.add_argument("--requirement", action="append"); x.add_argument("--preferred", default="")
    x.add_argument("--context-ref", action="append", help="Reference to immutable context; repeatable")
    x.add_argument("--actor", required=True); x.add_argument("--reopen-group", action="store_true")
    x.add_argument("--group-revision", type=int); x.add_argument("--cause", default=""); x.set_defaults(func=cmd_task_create)
    wait = task.add_parser("wait", help="Set, withdraw or resolve a task decision wait")
    wait_sub = wait.add_subparsers(dest="wait_action", required=True)
    for operation in ("set", "clear", "resolve"):
        x = wait_sub.add_parser(operation)
        x.add_argument("task_id"); x.add_argument("--actor", required=True)
        x.add_argument("--revision", type=int, required=True)
        if operation == "set":
            x.add_argument("--who", choices=["user", "agent", "task", "quota", "window"], required=True)
            x.add_argument("--question", required=True); x.add_argument("--target", default="")
            x.add_argument("--reference", default=""); x.add_argument("--reset-at")
        else:
            x.add_argument("--wait-id", required=True)
            if operation == "resolve":
                x.add_argument("--decision", required=True)
        x.set_defaults(func=cmd_task_wait)
    scope = task.add_parser("scope", help="Set a task file scope and lock partition").add_subparsers(dest="scope_action", required=True)
    x = scope.add_parser("set"); x.add_argument("task_id"); x.add_argument("--actor", required=True); x.add_argument("--revision", type=int, required=True)
    x.add_argument("--file", action="append", help="A path or folder in file_scope; repeatable")
    x.add_argument("--shared", action="append", help="Shared lock path; repeatable")
    x.add_argument("--workspace", action="append", help="Workspace lock path; repeatable")
    x.add_argument("--domain"); x.set_defaults(func=cmd_task_scope)
    x = task.add_parser("dep", help="Add or remove a dependency")
    x.add_argument("operation", choices=["add", "remove"]); x.add_argument("task_id"); x.add_argument("depends_on_id")
    x.add_argument("--requires", choices=requires_choices); x.add_argument("--actor", required=True); x.set_defaults(func=cmd_task_dep)
    x = task.add_parser("do", help="Apply one action: claim, unclaim, start, pause, resume, submit, accept, request_changes, fail, cancel")
    x.add_argument("task_id"); x.add_argument("action"); x.add_argument("--actor", required=True)
    x.add_argument("--cause", default=""); x.add_argument("--revision", type=int, help="Fail unless the task is still at this revision")
    x.add_argument("--actor-domain", help="Workspace domain for a pending task's workspace-scoped locks")
    x.set_defaults(func=cmd_task_do)
    release = task.add_parser("release", help="Manage legacy release modes, dependencies and evidence")
    release_sub = release.add_subparsers(dest="release_command", required=True)
    x = release_sub.add_parser("mode", help="Set a group's legacy release mode")
    x.add_argument("group_id"); x.add_argument("mode", choices=["terminada", "probada", "integrada"])
    x.add_argument("--actor", required=True); x.set_defaults(func=cmd_task_release)
    dep = release_sub.add_parser("dep", help="Add or remove a legacy release dependency")
    dep_sub = dep.add_subparsers(dest="operation", required=True)
    x = dep_sub.add_parser("add"); x.add_argument("task_id"); x.add_argument("depends_on_id")
    x.add_argument("--source-ref", required=True); x.add_argument("--actor", required=True)
    x.set_defaults(func=cmd_task_release)
    x = dep_sub.add_parser("remove"); x.add_argument("task_id"); x.add_argument("depends_on_id")
    x.add_argument("--actor", required=True); x.set_defaults(func=cmd_task_release)
    facts = release_sub.add_parser("facts", help="Inspect or record current-round release evidence")
    facts_sub = facts.add_subparsers(dest="facts_command", required=True)
    x = facts_sub.add_parser("show"); x.add_argument("task_id"); x.set_defaults(func=cmd_task_release)
    x = facts_sub.add_parser("delivery"); x.add_argument("task_id")
    x.add_argument("--delivery-ref", required=True); x.add_argument("--delivery-sha256", required=True)
    patch_presence = x.add_mutually_exclusive_group(required=True)
    patch_presence.add_argument("--has-patch", dest="has_patch", action="store_true")
    patch_presence.add_argument("--no-patch", dest="has_patch", action="store_false")
    x.add_argument("--patchset-sha256", default=""); x.add_argument("--actor", required=True)
    x.set_defaults(func=cmd_task_release)
    x = facts_sub.add_parser("test"); x.add_argument("task_id")
    x.add_argument("--verdict", dest="test_verdict", choices=["passed", "failed"], required=True)
    x.add_argument("--patchset-sha256", dest="test_patchset_sha256", required=True)
    x.add_argument("--test-ref", required=True); x.add_argument("--actor", required=True)
    x.set_defaults(func=cmd_task_release)
    x = facts_sub.add_parser("integration"); x.add_argument("task_id")
    x.add_argument("--commit", dest="integration_commit", required=True)
    x.add_argument("--integration-ref", required=True); x.add_argument("--actor", required=True)
    x.set_defaults(func=cmd_task_release)
    x = facts_sub.add_parser("reset"); x.add_argument("task_id"); x.add_argument("--actor", required=True)
    x.add_argument("--reason", required=True); x.set_defaults(func=cmd_task_release)
    snapshot = task.add_parser("snapshot", help="Import, inspect or roll back an imported task snapshot")
    snapshot_sub = snapshot.add_subparsers(dest="operation", required=True)
    x = snapshot_sub.add_parser("import", help="Import a snapshot JSON file; --actor is the current importer")
    x.add_argument("file", help="JSON object with snapshot, source_key, source_ref and snapshot_sha256")
    x.add_argument("--actor", required=True); x.add_argument("--what-if", action="store_true")
    x.set_defaults(func=cmd_task_snapshot)
    x = snapshot_sub.add_parser("show"); x.add_argument("source_key"); x.set_defaults(func=cmd_task_snapshot)
    x = snapshot_sub.add_parser("rollback"); x.add_argument("source_key"); x.add_argument("--actor", required=True)
    x.set_defaults(func=cmd_task_snapshot)
    x = task.add_parser("gate", help="Show, set, run or abort a task's gate, or list its results")
    x.add_argument("operation", choices=["show", "set", "run", "abort", "results"]); x.add_argument("task_id")
    x.add_argument("--check", action="append", help="For set: tests, commit, review or command:<name>; repeatable; none clears")
    x.add_argument("--actor"); x.add_argument("--cause", default=""); x.add_argument("--attempt", type=int)
    x.set_defaults(func=cmd_task_gate)
    x = task.add_parser("review", help="Approve or reject the current attempt of a submitted task")
    x.add_argument("task_id"); decision = x.add_mutually_exclusive_group(required=True)
    decision.add_argument("--approve", dest="approve", action="store_true")
    decision.add_argument("--reject", dest="approve", action="store_false")
    x.add_argument("--actor", required=True); x.add_argument("--comment", default=""); x.set_defaults(func=cmd_task_review)
    x = task.add_parser("context", help="Set a task's base commit, context references or constraints")
    x.add_argument("task_id"); x.add_argument("--actor", required=True); x.add_argument("--base-commit")
    x.add_argument("--context-ref", action="append", help="doc://, report://, rules@sha256:, result:// or blob:sha256:; repeatable")
    x.add_argument("--constraint", action="append", help="Plain text; repeatable")
    x.add_argument("--clear-context-refs", action="store_true"); x.add_argument("--clear-constraints", action="store_true")
    x.set_defaults(func=cmd_task_context)
    x = task.add_parser("envelope", help="Show the task envelope (references only), or 'unchanged' for a known hash")
    x.add_argument("task_id"); x.add_argument("--known-hash"); x.set_defaults(func=cmd_task_envelope)
    x = task.add_parser("context-v2", help="Show release-aware task context, or 'unchanged' for a known fingerprint")
    x.add_argument("task_id"); x.add_argument("--known-fingerprint"); x.add_argument("--after", type=int, default=0)
    x.set_defaults(func=cmd_task_context_v2)
    x = task.add_parser("result", help="Submit a result (and the task), show one, or list a task's results")
    x.add_argument("operation", choices=["submit", "show", "list"])
    x.add_argument("target", help="Task id, or result://<task>/<attempt> for show")
    x.add_argument("--actor"); x.add_argument("--file", help="For submit: the result JSON"); x.add_argument("--attempt", type=int)
    x.set_defaults(func=cmd_task_result)
    blob = sub.add_parser("blob", help="Store content by its sha256").add_subparsers(dest="blob_command", required=True)
    x = blob.add_parser("put"); x.add_argument("file"); x.add_argument("--actor", required=True)
    x.add_argument("--media-type", default="application/octet-stream"); x.set_defaults(func=cmd_blob_put)
    artifact = sub.add_parser("artifact", help="Read a blob by reference, by ranges").add_subparsers(dest="artifact_command", required=True)
    x = artifact.add_parser("get"); x.add_argument("ref"); x.add_argument("--offset", type=int, default=0)
    x.add_argument("--length", type=int, default=64 * 1024); x.add_argument("--output", help="Write the bytes to this file")
    x.set_defaults(func=cmd_artifact_get)
    x = sub.add_parser("cursor", help="Show, read or acknowledge a consumer's event cursor")
    x.add_argument("operation", choices=["show", "events", "ack"]); x.add_argument("consumer")
    x.add_argument("--scope", default="", help="'' (team), group:<id> or task:<id>"); x.add_argument("--seq", type=int)
    x.add_argument("--type", action="append", help="For events: keep only this event type; repeatable")
    x.add_argument("--mine", action="store_true", help="For events: only tasks assigned to the consumer")
    x.add_argument("--limit", type=int, default=100); x.set_defaults(func=cmd_cursor)
    executors = sub.add_parser("executors", help="Run a task in an executor application (Codex, Claude, Copilot), "
                               "resume or open its session, or show the executors").add_subparsers(dest="executors_command", required=True)
    x = executors.add_parser("run", help="Launch a ready task in a new session and submit its result (the gate decides)")
    x.add_argument("task_id"); x.add_argument("--executor", help="Executor id, e.g. codex_cli; without it --profile-id chooses")
    x.add_argument("--profile-id", help="Assignment profile whose preview selects the executor (needs a verified operator)")
    x.add_argument("--model"); x.add_argument("--effort"); x.add_argument("--actor", help="Default: executor:<executor id>")
    x.add_argument("--timeout", type=int, help="Seconds before the attempt is stopped")
    x.add_argument("--step-kind", choices=["orchestration", "planning", "review", "work"],
                   help="Kind of step the model routing profile selects for (default work)")
    x.set_defaults(func=cmd_executors)
    x = executors.add_parser("resume", help="Continue the session the framework created for the task")
    x.add_argument("task_id"); x.add_argument("--model"); x.add_argument("--effort")
    x.add_argument("--actor", help="Default: the task's assignee"); x.add_argument("--timeout", type=int)
    x.set_defaults(func=cmd_executors)
    x = executors.add_parser("open", help="Show the target that opens the task's session in its app (it records the open)")
    x.add_argument("task_id"); x.add_argument("--actor"); x.set_defaults(func=cmd_executors)
    x = executors.add_parser("status", help="Show the executors with an adapter and, with --task, its sessions and attempts")
    x.add_argument("--task"); x.set_defaults(func=cmd_executors)
    x = executors.add_parser("resume-due", help="Resume the tasks held by a usage limit whose time has come "
                             "(only if still owned by the same agent; relaunches from the checkpoint on config drift)")
    x.add_argument("--task", help="Only this task"); x.add_argument("--limit", type=int, help="At most this many resumptions")
    x.add_argument("--dry-run", action="store_true", help="Only report what is due"); x.add_argument("--timeout", type=int)
    x.set_defaults(func=cmd_executors)
    x = executors.add_parser("handoff", help="Carry out the hand-off of a task held by a usage limit that you chose on the "
                             "decision card (the original session is retired; the delegate starts from the saved checkpoint)")
    x.add_argument("task_id"); x.add_argument("--executor", help="Executor to hand it to; it must be the one chosen on the "
                                                                 "card (without it, that one): choose it on the card")
    x.add_argument("--decision-id", help="Makes a repeated call return the first result")
    x.add_argument("--expected-revision", type=int, help="The task revision you saw; a different one is refused")
    x.add_argument("--actor", help="Who decides (default: user)"); x.add_argument("--timeout", type=int)
    x.add_argument("--no-run", action="store_true", help="Hand it over without launching the delegate")
    x.set_defaults(func=cmd_executors)
    x = executors.add_parser("decide-holds", help="Apply the continuity policy to the tasks held by a usage limit: wait, "
                             "hand over to a candidate without asking (semi-automatic only) or ask you")
    x.add_argument("--task", help="Only this task"); x.add_argument("--limit", type=int, help="At most this many hand-offs")
    x.add_argument("--dry-run", action="store_true", help="Only report what would happen")
    x.add_argument("--no-run", action="store_true", help="Hand over without launching the delegate")
    x.add_argument("--timeout", type=int); x.set_defaults(func=cmd_executors)
    x = executors.add_parser("lift-pause", help="Lift the pause of an executor (the only end of a credits_exhausted pause)")
    x.add_argument("executor_id"); x.add_argument("--actor", help="Default: user"); x.add_argument("--reason")
    x.set_defaults(func=cmd_executors)
    x = sub.add_parser("project-commands", help="List the project commands configured on the server (names, timeouts, folders)")
    x.set_defaults(func=cmd_project_commands)
    x = sub.add_parser("team-events", help="List team events, optionally only those after a sequence number")
    x.add_argument("--after", type=int, default=0); x.add_argument("--group"); x.add_argument("--task")
    x.add_argument("--project", help="Only the events about this project and its stages")
    x.add_argument("--limit", type=int, default=500); x.set_defaults(func=cmd_team_events)

    # Projects, stages and the links of tasks to chats (PRY).
    x = sub.add_parser("projects", help="List projects (the active ones, or only the archived ones with --archived)")
    x.add_argument("--archived", action="store_true"); x.set_defaults(func=cmd_projects)
    project = sub.add_parser("project", help="Show, plan, create or change a project and its stages").add_subparsers(
        dest="project_command", required=True)
    x = project.add_parser("show", help="The aggregated view: progress, stages, what needs you, who is working")
    x.add_argument("project_id"); x.set_defaults(func=cmd_project_show)
    x = project.add_parser("tasks", help="The flat list of the tasks, optionally by owner, state (label key) and due date")
    x.add_argument("project_id"); x.add_argument("--owner"); x.add_argument("--state")
    x.add_argument("--due-from", dest="due_from", help="AAAA-MM-DD"); x.add_argument("--due-to", dest="due_to", help="AAAA-MM-DD")
    x.set_defaults(func=cmd_project_tasks)
    for name, help_text in (("plan", "Validate a plan or template file (JSON or YAML) without writing"),
                            ("create", "Create the project from a plan or template file, in one transaction")):
        x = project.add_parser(name, help=help_text)
        x.add_argument("file"); x.add_argument("--key", required=True, help="Project id and prefix of its stages and tasks")
        x.add_argument("--title"); x.add_argument("--visibility", choices=["normal", "private"],
                                                  help="Default: the file's, else private")
        if name == "create":
            x.add_argument("--actor", required=True, help="Must be user:...")
            x.add_argument("--plan-sha256", help="The hash 'project plan' gave; without it the file is planned first")
            x.set_defaults(func=cmd_project_create)
        else:
            x.set_defaults(func=cmd_project_plan)
    x = project.add_parser("update", help="Change the title or the visibility")
    x.add_argument("project_id"); x.add_argument("--actor", required=True); x.add_argument("--revision", type=int, required=True)
    x.add_argument("--title"); x.add_argument("--visibility", choices=["normal", "private"],
                                              help="private always; a private project is made normal in the GUI (it needs "
                                                   "the token), not from here")
    x.set_defaults(func=cmd_project_update)
    for name in ("archive", "unarchive"):
        x = project.add_parser(name, help=f"{name.capitalize()} the project (nothing is deleted)")
        x.add_argument("project_id"); x.add_argument("--actor", required=True)
        x.add_argument("--revision", type=int, required=True); x.set_defaults(func=cmd_project_archive)
    stage = project.add_parser("stage", help="Attach, order or close the stages of a project").add_subparsers(
        dest="stage_command", required=True)
    x = stage.add_parser("attach", help="Add an existing task group as the last stage")
    x.add_argument("project_id"); x.add_argument("group_id")
    x.add_argument("--actor", required=True); x.add_argument("--revision", type=int, required=True, help="The project's revision")
    x.set_defaults(func=cmd_project_stage)
    x = stage.add_parser("order", help="Put all the stages in this order")
    x.add_argument("project_id"); x.add_argument("group_ids", nargs="+")
    x.add_argument("--actor", required=True); x.add_argument("--revision", type=int, required=True, help="The project's revision")
    x.set_defaults(func=cmd_project_stage)
    x = stage.add_parser("close", help="Verify and close a stage that waits for verification")
    x.add_argument("project_id"); x.add_argument("group_id")
    x.add_argument("--actor", required=True); x.add_argument("--revision", type=int, required=True, help="The stage's revision")
    x.set_defaults(func=cmd_project_stage)

    # Activities: lightweight tasks of a project, outside its stages (PRY-T09).
    activity = sub.add_parser("activity", help="Start, close or list the activities of a project").add_subparsers(
        dest="activity_command", required=True)
    x = activity.add_parser("start", help="Start an activity (a person, an agent or an executor works on it now)")
    x.add_argument("project_id"); x.add_argument("--title", required=True); x.add_argument("--actor", required=True)
    x.add_argument("--executor", help="Leave it ready for this executor instead of starting it as --actor")
    x.add_argument("--idempotency-key", dest="idempotency_key", help="A repeated key gives back the same activity")
    x.add_argument("--objective"); x.set_defaults(func=cmd_activity)
    x = activity.add_parser("close", help="Close a running activity with a short result")
    x.add_argument("activity_id"); x.add_argument("--summary", required=True)
    x.add_argument("--actor", required=True, help="Must be the one who started it"); x.set_defaults(func=cmd_activity)
    x = activity.add_parser("list", help="The activities of a project, optionally of one label or changed since a day")
    x.add_argument("project_id"); x.add_argument("--status", help="todo, ready, doing, done, failed or cancelled")
    x.add_argument("--since", help="A day AAAA-MM-DD or an ISO date and time"); x.set_defaults(func=cmd_activity)

    x = sub.add_parser("status"); x.add_argument("run_id"); x.set_defaults(func=cmd_status)
    x = sub.add_parser("runs"); x.add_argument("--limit", type=int, default=20); x.add_argument("--status", default=""); x.add_argument("--project", default=""); x.set_defaults(func=cmd_runs)
    x = sub.add_parser("approve"); x.add_argument("run_id")
    x.add_argument("--permission-request", dest="permission_request", default=None,
                   help="id of the pending tool request you were shown (needed to approve a tool call)")
    x.set_defaults(func=cmd_approve)
    x = sub.add_parser("changes"); x.add_argument("run_id"); x.add_argument("feedback"); x.set_defaults(func=cmd_changes)
    x = sub.add_parser("reject"); x.add_argument("run_id"); x.add_argument("--feedback", default=""); x.set_defaults(func=cmd_reject)
    x = sub.add_parser("regenerate"); x.add_argument("run_id"); x.set_defaults(func=cmd_regenerate)
    x = sub.add_parser("cancel"); x.add_argument("run_id"); x.add_argument("--force", action="store_true"); x.set_defaults(func=cmd_cancel)

    x = sub.add_parser("human-next", help="Show the next human_guided prompt")
    x.add_argument("run_id"); x.add_argument("--prompt-only", action="store_true"); x.set_defaults(func=cmd_human_next)
    x = sub.add_parser("human-submit", help="Submit a response from an external model or human")
    x.add_argument("run_id"); x.add_argument("--file", required=True); x.add_argument("--provider", default="human")
    x.add_argument("--model", default=""); x.add_argument("--prompt-used-file", default=""); x.add_argument("--notes", default="")
    x.set_defaults(func=cmd_human_submit)

    x = sub.add_parser("research-submit", help="Specialized human-submit alias for external research")
    x.add_argument("run_id"); x.add_argument("--file", required=True); x.add_argument("--provider", default="human_web")
    x.add_argument("--model", default=""); x.add_argument("--prompt-used-file", default=""); x.add_argument("--notes", default="")
    x.set_defaults(func=cmd_human_submit)

    x = sub.add_parser("dry-run")
    x.add_argument("--project", default="demo"); x.add_argument("--objective", default="Create a demonstration post")
    x.add_argument("--topic", default="home automation"); x.add_argument("--platform", default="Instagram")
    x.add_argument("--audience", default=""); x.add_argument("--instructions", default=""); x.add_argument("--web", action="store_true")
    x.add_argument("--pipeline", choices=[m.value for m in PipelineMode], default="quick")
    x.add_argument("--persist", action="store_true"); x.set_defaults(func=cmd_dry_run)

    asset = sub.add_parser("asset-add")
    asset.add_argument("file"); asset.add_argument("--project-id", default=""); asset.add_argument("--project", default="")
    asset.add_argument("--source", choices=[x.value for x in AssetSource], required=True); asset.add_argument("--status", choices=[x.value for x in AssetStatus], default="draft")
    asset.add_argument("--title", default=""); asset.add_argument("--notes", default=""); asset.add_argument("--tag", action="append")
    asset.add_argument("--rejection-reason", default=""); asset.add_argument("--prompt-id", default=""); asset.add_argument("--prompt-version", type=int)
    asset.add_argument("--model-id", default=""); asset.add_argument("--run-id", default=""); asset.add_argument("--step-id", default=""); asset.add_argument("--attempt", type=int)
    asset.set_defaults(func=cmd_asset_add)

    pub = sub.add_parser("publication-add")
    pub.add_argument("--project-id", default=""); pub.add_argument("--project", default=""); pub.add_argument("--platform", required=True)
    pub.add_argument("--asset-id", default=""); pub.add_argument("--artifact-id", default=""); pub.add_argument("--url", default="")
    pub.add_argument("--objective", default=""); pub.add_argument("--audience", default=""); pub.add_argument("--brand-version", type=int)
    pub.add_argument("--status", choices=[x.value for x in PublicationStatus], default="draft"); pub.set_defaults(func=cmd_publication_add)

    met = sub.add_parser("metrics-add")
    met.add_argument("--publication-id", required=True); met.add_argument("--platform", required=True); met.add_argument("--metrics-json", required=True)
    met.add_argument("--raw-file", default=""); met.set_defaults(func=cmd_metrics_add)

    bg = sub.add_parser("brand-generate"); bg.add_argument("--answers", required=True); bg.add_argument("--model", default="cloud_fast"); bg.add_argument("--dry-run", action="store_true"); bg.set_defaults(func=cmd_brand_generate)
    ba = sub.add_parser("brand-activate"); ba.add_argument("profile_id", nargs="?"); ba.add_argument("--version", type=int); ba.set_defaults(func=cmd_brand_activate)
    bi = sub.add_parser("brand-import"); bi.add_argument("file"); bi.add_argument("--created-by", default="human"); bi.add_argument("--activate", action="store_true"); bi.set_defaults(func=cmd_brand_import)
    bs = sub.add_parser("brand-show"); bs.set_defaults(func=cmd_brand_show)
    bl = sub.add_parser("brand-list"); bl.add_argument("--limit", type=int, default=20); bl.set_defaults(func=cmd_brand_list)
    watch = sub.add_parser("watcher", help="Watch one condition hold without an AI model")
    watch_sub = watch.add_subparsers(dest="watcher_action", required=True)
    for action in ("run", "sweep"):
        command = watch_sub.add_parser(action)
        command.add_argument("--state-dir", help="Local watcher state folder (default: data_dir/watchers); the integration "
                             "marker is not in it unless that is also where the installation's marker is")
        command.add_argument("--integration-marker", required=action == "run",
                             help="Shared exclusive marker; it must be the installation's (Settings.integration_marker, "
                                  "INTEGRATION_MARKER); sweep only removes gates owned by proven-dead watchers")
        command.set_defaults(func=cmd_watcher)
        if action == "run":
            command.add_argument("--task", required=True)
            command.add_argument("--hold", required=True)
            command.add_argument("--attempt", required=True, type=int)
            command.add_argument("--max-hours", type=float, default=12)
    return p


def cmd_watcher(args) -> None:
    """Run a deterministic watcher, or sweep dead leases; never construct a shell command."""
    from .services import ApplicationServices
    from .services.watcher import WatcherProcess

    settings = get_settings()
    root = Path(args.state_dir) if args.state_dir else settings.data_dir / "watchers"
    # One marker for the whole installation (CNT-T05): the hand-off of a held task reads ``Settings.integration_marker``,
    # so neither ``--state-dir`` nor ``--integration-marker`` may point the watcher to another file and hide it.
    marker = settings.integration_marker
    if args.integration_marker and Path(args.integration_marker).expanduser().resolve() != marker:
        raise ValueError(f"--integration-marker must be the marker of this installation, {marker}: the hand-off of held "
                         "tasks reads that one. Set INTEGRATION_MARKER to move it for every process.")
    if args.watcher_action == "sweep":
        pretty({"lost": WatcherProcess(root, None, integration_marker=marker, integration_active=lambda: True).sweep()})
        return
    services = ApplicationServices.create(settings)
    try:
        watcher = WatcherProcess(root, services.executors, integration_marker=marker, integration_active=lambda: marker.exists())
        watcher.claim(args.task, args.hold, args.attempt, max_hours=args.max_hours)
        pretty(watcher.run())
    finally:
        services.runs.close()


def main(argv=None) -> None:
    parser = build_parser(); args = parser.parse_args(argv)
    try:
        args.func(args)
    except SystemExit:
        raise
    except Exception as exc:
        if getattr(args, "debug", False):
            raise
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
