from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

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
            return str(body.get("detail") or body.get("error") or body)
        return str(body)
    except Exception:
        return response.text.strip() or response.reason_phrase


def _api_json(method: str, path: str, *, json_body=None, params=None):
    try:
        with api_client() as client:
            response = client.request(method, path, json=json_body, params=params)
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


def cmd_run(args) -> None:
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="multiagent", description=f"Multi-Agent Framework {__version__} — Hybrid Human/AI Orchestration")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--debug", action="store_true", help="Show the full traceback when an error occurs")
    sub = p.add_subparsers(dest="command", required=True)

    x = sub.add_parser("init"); x.set_defaults(func=cmd_init)
    x = sub.add_parser("doctor"); x.add_argument("--deep", action="store_true"); x.set_defaults(func=cmd_doctor)

    x = sub.add_parser("run")
    x.add_argument("--project", required=True); x.add_argument("--objective", required=True); x.add_argument("--topic", required=True)
    x.add_argument("--platform", default="Instagram"); x.add_argument("--audience", default=""); x.add_argument("--instructions", default="")
    x.add_argument("--web", action="store_true"); x.add_argument("--no-brand", action="store_true"); x.add_argument("--idempotency-key", default="")
    x.add_argument("--mode", choices=[m.value for m in ExecutionMode], default="auto")
    x.add_argument("--research-mode", choices=[m.value for m in ResearchMode], default="human_bridge")
    x.add_argument("--pipeline", choices=[m.value for m in PipelineMode], default="quick")
    x.add_argument("--step-mode", action="append", help="Override per step, e.g. strategy=human_guided")
    x.add_argument("--sensitive", action="store_true", help="Force local routing in AUTO and add a warning to Human Guided prompts")
    x.set_defaults(func=cmd_run)

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
