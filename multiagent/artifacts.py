from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import Settings


class ArtifactWriter:
    def __init__(self, settings: Settings):
        self.settings = settings

    def run_dir(self, run_id: str) -> Path:
        path = self.settings.runs_dir / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write(self, *, run_id: str, step_index: int, step_id: str, attempt: int, data: dict[str, Any]) -> Path:
        run_dir = self.run_dir(run_id)
        path = run_dir / f"{step_index+1:02d}_{step_id}_attempt{attempt}.md"
        if path.exists():
            return path
        content = [
            f"# {step_id}",
            "",
            f"- Run: `{run_id}`",
            f"- Attempt: `{attempt}`",
            "",
            "## Structured result",
            "",
            "```json",
            json.dumps(data, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
        path.write_text("\n".join(content), encoding="utf-8")
        return path

    def write_human_request(
        self,
        *,
        run_id: str,
        step_index: int,
        step_id: str,
        attempt: int,
        prompt: str,
        expected_contract: str,
    ) -> Path:
        path = self.run_dir(run_id) / f"{step_index+1:02d}_{step_id}_attempt{attempt}_HUMAN_REQUEST.md"
        path.write_text(
            "\n".join([
                f"# Human Step Request — {step_id}",
                "",
                f"- Run: `{run_id}`",
                f"- Attempt: `{attempt}`",
                f"- Expected contract: `{expected_contract}`",
                "",
                "## Prompt to copy",
                "",
                prompt,
                "",
            ]),
            encoding="utf-8",
        )
        return path

    def write_human_raw(
        self,
        *,
        run_id: str,
        step_index: int,
        step_id: str,
        attempt: int,
        raw_response: str,
        provider: str,
        model: str,
        prompt_used: str = "",
    ) -> Path:
        run_dir = self.run_dir(run_id)
        prefix = f"{step_index+1:02d}_{step_id}_attempt{attempt}_HUMAN_RAW"
        existing = sorted(run_dir.glob(prefix + "_*.md"))
        sequence = len(existing) + 1
        path = run_dir / f"{prefix}_{sequence:03d}.md"
        body = [
            f"# Human External Response — {step_id}",
            "",
            f"- Run: `{run_id}`",
            f"- Attempt: `{attempt}`",
            f"- Provider (human_reported): `{provider}`",
            f"- Model (human_reported): `{model or 'unspecified'}`",
            "",
        ]
        if prompt_used:
            body.extend(["## Prompt actually used", "", prompt_used, ""])
        body.extend(["## Raw response", "", raw_response, ""])
        path.write_text("\n".join(body), encoding="utf-8")
        return path

    def write_summary(self, run: dict[str, Any]) -> Path:
        run_id = run["id"]
        path = self.run_dir(run_id) / "00_RUN.md"
        artifacts = run.get("artifacts", [])
        events = run.get("events", [])
        last_revision = next((e for e in reversed(events) if e["event_type"] == "human_revision"), None)
        content = [
            "# Multi-Agent Run",
            "",
            f"- Run ID: `{run_id}`",
            f"- Workflow: `{run.get('workflow_id','')}`",
            f"- Status: `{run.get('status','')}`",
            f"- Waiting reason: `{run.get('waiting_reason','') or '-'}`",
            f"- Waiting step: `{run.get('waiting_step','') or '-'}`",
            f"- Current step index: `{run.get('current_step',0)}`",
            f"- Execution mode: `{run.get('execution_mode','auto')}`",
            f"- Research mode: `{run.get('research_mode','none')}`",
            f"- Pipeline mode: `{run.get('pipeline_mode','quick')}`",
            f"- Sensitive: `{bool((run.get('request') or {}).get('sensitive', False))}`",
            f"- Request fingerprint: `{run.get('request_fingerprint') or '-'}`",
            f"- Active seconds: `{float(run.get('active_seconds') or 0):.3f}`",
            f"- Brand ID: `{run.get('brand_profile_id') or '-'}`",
            f"- Brand version: `{run.get('brand_version')}`",
            f"- LLM attempts: `{run.get('llm_calls',0)}/{run.get('max_llm_calls',0)}`",
            f"- Successful LLM calls: `{run.get('successful_llm_calls',0)}`",
            f"- Budget exhausted: `{bool(run.get('budget_exhausted',0))}`",
            "",
            "## Request",
            "",
            "```json",
            json.dumps(run.get("request", {}), ensure_ascii=False, indent=2),
            "```",
            "",
            "## Artifacts",
            "",
        ]
        if artifacts:
            for item in artifacts:
                content.append(
                    f"- `{item.get('step_id')}` attempt `{item.get('attempt')}` — `{item.get('kind')}` — `{item.get('path')}`"
                )
        else:
            content.append("- None")
        if last_revision:
            content.extend([
                "",
                "## Latest human review",
                "",
                f"- Date: `{last_revision.get('ts','')}`",
                f"- Mode: `{last_revision.get('payload',{}).get('mode','')}`",
                f"- Feedback: {last_revision.get('payload',{}).get('feedback','')}",
            ])
        path.write_text("\n".join(content) + "\n", encoding="utf-8")
        return path
