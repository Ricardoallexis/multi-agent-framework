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


def cmd_task_groups(args) -> None:
    pretty(_api_json("GET", "/api/v1/task-groups"))


def cmd_task_group_show(args) -> None:
    pretty(_api_json("GET", f"/api/v1/task-groups/{_segment(args.group_id)}"))


def cmd_task_group_create(args) -> None:
    pretty(_api_json("POST", "/api/v1/task-groups", json_body={
        "id": args.group_id, "title": args.title, "actor": args.actor,
        "authorized": args.authorized, "default_requires": args.requires,
    }))


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
        ("data_class", args.data_class),
    ) if value is not None}
    pretty(_api_json("POST", f"/api/v1/tasks/{_segment(args.task_id)}/assignment/preview", json_body=body))


def cmd_task_assignment_accept(args) -> None:
    pretty(_api_json("POST", f"/api/v1/tasks/{_segment(args.task_id)}/assignment/accept",
                     json_body={"preview_token": args.preview_token, "acknowledge": args.acknowledge}))


def cmd_task_create(args) -> None:
    pretty(_api_json("POST", "/api/v1/tasks", json_body={
        "group_id": args.group_id, "id": args.task_id, "title": args.title, "actor": args.actor,
        "objective": args.objective, "depends_on": _parse_depends_on(args.depends_on),
        "file_scope": args.file or [], "requirements": args.requirement or [], "preferred": args.preferred,
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
    pretty(_api_json("POST", f"/api/v1/tasks/{_segment(args.task_id)}/transitions", json_body=body))


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
    pretty(_api_json("GET", "/api/v1/team-events", params=params))


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


def cmd_approve(args) -> None:
    pretty(_api_json("POST", f"/api/v1/runs/{args.run_id}/approve"))


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

    import uvicorn

    from .api import create_app

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
    uvicorn.run(app, host=host, port=port, log_level="warning")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="multiagent", description=f"Multi-Agent Framework {__version__} — Hybrid Human/AI Orchestration")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--debug", action="store_true", help="Show the full traceback when an error occurs")
    sub = p.add_subparsers(dest="command", required=True)

    x = sub.add_parser("init"); x.set_defaults(func=cmd_init)
    x = sub.add_parser("doctor"); x.add_argument("--deep", action="store_true"); x.set_defaults(func=cmd_doctor)
    x = sub.add_parser("ui", help="Start the API with the Stage 0 UI (Mock mode unless --real) and open the browser")
    x.add_argument("--real", action="store_true", help="Use the configured model providers instead of Mock mode")
    x.add_argument("--port", type=int, help="Port to listen on (default: APP_PORT, 8000)")
    x.add_argument("--bundle", action="append", metavar="NAME=FOLDER", help="Register a trusted definition bundle; repeatable")
    x.add_argument("--no-browser", action="store_true", help="Do not open the browser")
    x.set_defaults(func=cmd_ui)

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
    x = task.add_parser("show"); x.add_argument("task_id"); x.set_defaults(func=cmd_task_show)
    assignment = task.add_parser("assignment", help="Preview or acknowledge a task assignment plan")
    assignment_sub = assignment.add_subparsers(dest="assignment_command", required=True)
    x = assignment_sub.add_parser("preview")
    x.add_argument("task_id"); x.add_argument("--profile-id"); x.add_argument("--executor")
    x.add_argument("--model"); x.add_argument("--effort"); x.add_argument("--context-isolation", choices=["fresh", "inherited", "unknown"])
    x.add_argument("--requirement", action="append"); x.add_argument("--permission", action="append")
    x.add_argument("--data-class"); x.set_defaults(func=cmd_task_assignment_preview)
    x = assignment_sub.add_parser("accept")
    x.add_argument("task_id"); x.add_argument("preview_token"); x.add_argument("--acknowledge", action="store_true")
    x.set_defaults(func=cmd_task_assignment_accept)
    x = task.add_parser("create"); x.add_argument("group_id"); x.add_argument("task_id"); x.add_argument("--title", required=True)
    x.add_argument("--objective", default=""); x.add_argument("--depends-on", action="append", metavar="ID[:requires]")
    x.add_argument("--file", action="append", help="A path or folder the task will change; repeatable")
    x.add_argument("--requirement", action="append"); x.add_argument("--preferred", default="")
    x.add_argument("--actor", required=True); x.set_defaults(func=cmd_task_create)
    x = task.add_parser("dep", help="Add or remove a dependency")
    x.add_argument("operation", choices=["add", "remove"]); x.add_argument("task_id"); x.add_argument("depends_on_id")
    x.add_argument("--requires", choices=requires_choices); x.add_argument("--actor", required=True); x.set_defaults(func=cmd_task_dep)
    x = task.add_parser("do", help="Apply one action: claim, unclaim, start, pause, resume, submit, accept, request_changes, fail, cancel")
    x.add_argument("task_id"); x.add_argument("action"); x.add_argument("--actor", required=True)
    x.add_argument("--cause", default=""); x.add_argument("--revision", type=int, help="Fail unless the task is still at this revision")
    x.set_defaults(func=cmd_task_do)
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
    x = sub.add_parser("project-commands", help="List the project commands configured on the server (names, timeouts, folders)")
    x.set_defaults(func=cmd_project_commands)
    x = sub.add_parser("team-events", help="List team events, optionally only those after a sequence number")
    x.add_argument("--after", type=int, default=0); x.add_argument("--group"); x.add_argument("--task")
    x.add_argument("--limit", type=int, default=500); x.set_defaults(func=cmd_team_events)

    x = sub.add_parser("status"); x.add_argument("run_id"); x.set_defaults(func=cmd_status)
    x = sub.add_parser("runs"); x.add_argument("--limit", type=int, default=20); x.add_argument("--status", default=""); x.add_argument("--project", default=""); x.set_defaults(func=cmd_runs)
    x = sub.add_parser("approve"); x.add_argument("run_id"); x.set_defaults(func=cmd_approve)
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
    return p


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
