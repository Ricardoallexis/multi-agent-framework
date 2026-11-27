# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Token-protected GUI routes for INS-T03 template status and INS-T04 guided consoles."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..config import BUNDLED_ROOT, IN_CHECKOUT, PROJECT_ROOT
from ..document_actions import ActionKind, Approval
from ..document_trust import Provenance, SourceKind
from ..executors import launch_safety
from ..executors.process import ProcessLaunchError
from ..services.errors import INVALID_REQUEST, ServiceError
from ..setup import guided

TOOL_TO_APP = {
    "claude_cli": "claude",
    "codex_cli": "codex",
    "copilot_cli": "copilot",
    "antigravity_cli": "agy",
}
APP_STEPS: dict[str, tuple[str, ...]] = {
    "claude_cli": ("install", "login"),
    "codex_cli": ("install", "login"),
    "copilot_cli": ("install", "login"),
    "antigravity_cli": ("login",),
}


class ConsoleStepRequest(BaseModel):
    """Strict request body: accepts only a tool identifier and a fixed step, never a command."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    step: Literal["install", "login"]


def _settings_of(services: Any) -> Any:
    return getattr(services, "settings", None) or services.system.settings


def _console_supported(services: Any) -> bool:
    system = getattr(services, "setup_system", None)
    if system is not None:
        return system in {"Windows", "win32"}
    return sys.platform == "win32"


def permission_status(root: Path | None = None) -> dict[str, dict[str, Any]]:
    """Read-only presence checks for the INS-T03 permission templates; never reads local overrides or secrets."""
    project_root = root if root is not None else (PROJECT_ROOT if IN_CHECKOUT else Path.cwd())
    docs_root = root if root is not None else (PROJECT_ROOT if IN_CHECKOUT else BUNDLED_ROOT)
    claude_template = "docs/permissions/claude-settings.template.json"
    codex_template = "docs/permissions/codex-config.template.toml"
    return {
        "claude_cli": {
            "mode": "template",
            "template_path": claude_template,
            "active_path": ".claude/settings.json",
            "local_path": ".claude/settings.local.json",
            "template_present": (docs_root / claude_template).is_file(),
            "active": (project_root / ".claude" / "settings.json").is_file(),
            "local_present": (project_root / ".claude" / "settings.local.json").is_file(),
            "guide": "docs/PERMISSIONS.md",
        },
        "codex_cli": {
            "mode": "template",
            "template_path": codex_template,
            "active_path": ".codex/config.toml",
            "template_present": (docs_root / codex_template).is_file(),
            "active": (project_root / ".codex" / "config.toml").is_file(),
            "guide": "docs/PERMISSIONS.md",
        },
        "copilot_cli": {
            "mode": "per_run",
            "template_path": None,
            "active_path": None,
            "active": None,
            "guide": "docs/PERMISSIONS.md",
        },
        "antigravity_cli": {
            "mode": "supervised_modes",
            "template_path": None,
            "active_path": None,
            "active": None,
            "guide": "docs/EXECUTORS.md",
        },
    }


def setup_plans() -> dict[str, dict[str, dict[str, Any]]]:
    """Fixed, dated install and login plans from INS-T04."""
    return {
        tool_id: {step: guided.step_plan(TOOL_TO_APP[tool_id], step) for step in steps}
        for tool_id, steps in APP_STEPS.items()
    }


def confirmed_setup_approval(app: str, step: str) -> tuple[Approval, ...]:
    """Build user approval evidence for the confirmed setup console request."""
    target = f"setup:{app}:{step}"
    provenance = Provenance(SourceKind.USER.value, ref=f"gui:setup-console:{app}:{step}")
    return (Approval(provenance, frozenset({ActionKind.OPEN_APPLICATION}), frozenset({target})),)


def add_setup_routes(api: APIRouter, services: Any) -> None:
    """Register token-protected GUI setup routes."""

    @api.get("/setup/status")
    def get_setup_status():
        settings = _settings_of(services)
        root = getattr(services, "setup_project_root", None)
        return {
            "allow_launch": bool(getattr(settings, "gui_allow_launch", True)),
            "console_supported": _console_supported(services),
            "permissions": permission_status(root),
            "plans": setup_plans(),
        }

    @api.get("/setup/console")
    def get_setup_console():
        """State of the latest console: none, open, closed (with exit code and recheck) or failed."""
        return guided.console_status()

    @api.post("/setup/console", status_code=202)
    def open_setup_console(body: ConsoleStepRequest):
        if body.id not in TOOL_TO_APP or body.step not in APP_STEPS[body.id]:
            raise ServiceError(INVALID_REQUEST, "Unsupported tool or setup step", status=400,
                               details={"id": body.id, "step": body.step})
        settings = _settings_of(services)
        if not getattr(settings, "gui_allow_launch", True):
            raise ServiceError("launch_disabled",
                               "Launching setup consoles is turned off (gui_allow_launch).", status=403)
        app = TOOL_TO_APP[body.id]
        policy = getattr(getattr(services, "executors", None), "document_policy", None)
        evidence = confirmed_setup_approval(app, body.step)
        guide_fn = getattr(services, "setup_guide", None) or guided.guide_step
        extra: dict[str, Any] = {}
        connections = getattr(services, "connections", None)
        if connections is not None and getattr(services, "setup_detector", None) is None:
            # EXA-T05: the measurement after the console closes is a search too: without the person's answer it does not run.
            def guarded_detector(**kwargs: Any) -> dict[str, Any]:
                if not connections.discovery_allowed(guided.detect):
                    return {"skipped": "consent_required"}
                return guided.detect(**kwargs)

            extra["detector"] = guarded_detector
        for attr, key in (("setup_launcher", "launcher"), ("setup_detector", "detector"), ("setup_system", "system")):
            value = getattr(services, attr, None)
            if value is not None:
                extra[key] = value
        try:
            result = guide_fn(app, body.step, policy=policy, evidence=evidence, context="packaged_process",
                              background=True, **extra)
        except launch_safety.EffectNotAuthorized as exc:
            raise ServiceError(launch_safety.EFFECT_NOT_AUTHORIZED,
                               f"Opening the setup console is not authorized: {exc.result.reason}", status=403) from None
        except ValueError as exc:
            raise ServiceError(INVALID_REQUEST, str(exc), status=400) from None
        except RuntimeError as exc:
            if "already open" in str(exc):
                raise ServiceError("console_already_open", "Another setup console is already open.",
                                   status=409) from None
            raise ServiceError("launch_unsupported", str(exc), status=409) from None
        except (OSError, ProcessLaunchError):
            raise ServiceError("launch_failed", "The setup console could not be started.", status=409) from None
        payload = {
            "ok": True,
            "id": body.id,
            "app": app,
            "step": body.step,
            "plan": result.get("plan"),
            "console_id": result.get("console_id"),
            "state": result.get("state"),
            "console_exit_code": result.get("console_exit_code"),
            "recheck": result.get("recheck"),
        }
        # 202 only when a console is open and still running; a step with nothing to open answers 200.
        return JSONResponse(payload, status_code=202 if result.get("console_id") else 200)
