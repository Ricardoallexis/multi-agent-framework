"""Run the existing three-step Mock workflow through the installed Python Core.

Install the checkout in editable mode first. No server or API key is needed.
By default the workspace is temporary; --local-dir retains the run for review.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from multiagent.bootstrap import build_system
from multiagent.config import Settings
from multiagent.contracts import PipelineMode, RunStatus, SocialPostRequest


def run_demo(local_dir: Path) -> dict[str, Any]:
    """Create and process only this demo's run, leaving final review to a human."""
    local_dir = local_dir.resolve()
    data_dir = local_dir / "data"
    settings = Settings(
        _env_file=None,
        local_dir=local_dir,
        data_dir=data_dir,
        db_path=data_dir / "multiagent.db",
        runs_dir=data_dir / "runs",
        assets_dir=data_dir / "assets",
        worker_enabled=False,
        gemini_api_key="",
        openai_api_key="",
        openai_enabled=False,
        max_llm_calls=4,
        max_run_seconds=300,
    )
    system = build_system(settings, dry_run=True)
    try:
        request = SocialPostRequest(
            project_name="Public Python demo",
            objective="Explain a configurable runtime through an educational post",
            topic="Collaboration through explicit contracts",
            platform="LinkedIn",
            pipeline_mode=PipelineMode.FULL,
            requires_web=False,
            use_brand_context=False,
        )
        run = system.run_service.create_social_post(request)
        # Process this run directly: no server, background thread, or unrelated queued work.
        state = system.engine.process_run(run["id"])
        steps = [artifact["step_id"] for artifact in state["artifacts"]]
        providers = sorted({
            event["payload"]["provider"]
            for event in state["events"]
            if event["event_type"] == "model_selected"
        })
        if (
            state["status"] != RunStatus.WAITING_HUMAN.value
            or state["waiting_reason"] != "review"
            or steps != ["strategy", "create", "design"]
            or providers != ["fake"]
        ):
            raise RuntimeError(f"Demo did not reach its expected review checkpoint: {state['status']}")
        return {
            "run_id": run["id"],
            "status": state["status"],
            "waiting_reason": state["waiting_reason"],
            "steps": steps,
            "artifact_count": len(state["artifacts"]),
            "providers": providers,
            "llm_calls": state["llm_calls"],
        }
    finally:
        # Bootstrap creates an Ollama HTTP client even when FakeAdapter executes.
        system.adapters["ollama"].client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-dir", type=Path, help="Private workspace in which to retain demo data")
    args = parser.parse_args()
    if args.local_dir is None:
        with TemporaryDirectory(prefix="multiagent-python-demo-") as temporary_dir:
            result = run_demo(Path(temporary_dir))
    else:
        result = run_demo(args.local_dir)
    result["artifacts_retained"] = args.local_dir is not None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
