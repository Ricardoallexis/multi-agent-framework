from __future__ import annotations

import importlib.metadata
import os
import platform
import shutil
import subprocess
from typing import Any, Literal

from pydantic import BaseModel

from .bootstrap import build_system
from .version import __version__
from .config import Settings
from .contracts import GroundingStatus, ResearchOutput


class _DoctorStructuredProbe(BaseModel):
    status: Literal["ok"]
    message: str


def _cmd(args: list[str], timeout: int = 8) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or p.stderr).strip()
    except Exception as exc:
        return 1, str(exc)


def _gpu_report() -> dict[str, Any]:
    nvidia = shutil.which("nvidia-smi")
    if not nvidia:
        return {"available": False, "reason": "nvidia-smi not found"}
    code, out = _cmd([nvidia, "--query-gpu=name,memory.total,memory.free,driver_version", "--format=csv,noheader,nounits"])
    if code != 0:
        return {"available": False, "error": out}
    first = out.splitlines()[0] if out else ""
    parts = [x.strip() for x in first.split(",")]
    result: dict[str, Any] = {"available": True, "raw": first}
    if len(parts) >= 4:
        result.update({"name": parts[0], "memory_total_mb": int(float(parts[1])),
                       "memory_free_mb": int(float(parts[2])), "driver_version": parts[3]})
    return result


def _gemini_model_discovery(adapter) -> dict[str, Any]:
    try:
        client = adapter._client_or_raise()  # diagnostic-only introspection
        models_api = getattr(client, "models", None)
        if models_api is None or not hasattr(models_api, "list"):
            return {"ok": False, "reason": "models.list is not available in this client"}
        found: list[str] = []
        for item in models_api.list():
            name = getattr(item, "name", None) or getattr(item, "model", None)
            if name:
                found.append(str(name).removeprefix("models/"))
            if len(found) >= 100:
                break
        return {"ok": True, "models": found}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def run_doctor(settings: Settings, *, deep: bool = False) -> tuple[bool, dict[str, Any]]:
    report: dict[str, Any] = {"system": {}, "python": {}, "database": {}, "gpu": {}, "ollama": {}, "cloud": {}, "paths": {}, "recommendations": []}
    report["system"] = {"platform": platform.platform(), "machine": platform.machine(), "release_version": __version__}
    version = tuple(os.sys.version_info[:2])
    report["python"] = {"version": platform.python_version(), "supported": version in {(3, 12), (3, 13)},
                        "executable": os.sys.executable, "venv": os.sys.prefix != getattr(os.sys, "base_prefix", os.sys.prefix)}
    for package in ["pydantic", "pydantic-settings", "fastapi", "uvicorn", "httpx", "PyYAML", "google-genai", "openai"]:
        try:
            report["python"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            report["python"][package] = "missing"

    system = build_system(settings)
    report["database"] = {"path": str(settings.db_path), "schema_version": system.db.schema_version(),
                          "writable": os.access(settings.db_path.parent, os.W_OK)}
    report["gpu"] = _gpu_report()
    report["ollama"] = system.adapters["ollama"].health()
    code, out = _cmd(["ollama", "ps"])
    if code == 0:
        report["ollama"]["ps"] = out
    installed = set(report["ollama"].get("installed_models") or [])
    report["ollama"].update({
        "expected_model": settings.ollama_model,
        "expected_model_installed": settings.ollama_model in installed,
        "num_ctx": settings.ollama_num_ctx,
        "context_soft_limit": settings.ollama_context_soft_limit,
        "keep_alive": settings.ollama_keep_alive,
        "OLLAMA_NUM_PARALLEL": os.environ.get("OLLAMA_NUM_PARALLEL", "not_visible_in_process"),
        "OLLAMA_MAX_LOADED_MODELS": os.environ.get("OLLAMA_MAX_LOADED_MODELS", "not_visible_in_process"),
    })

    report["cloud"] = {
        "gemini_key_configured": bool(settings.gemini_api_key),
        "cloud_fast_model": settings.gemini_model,
        "cloud_advanced_model": settings.gemini_advanced_model,
        "gemini_grounded_model": settings.gemini_grounded_model,
        "openai_key_configured": bool(settings.openai_api_key),
        "openai_enabled": settings.openai_enabled,
        "openai_model": settings.openai_model,
    }

    if deep:
        if report["ollama"].get("available"):
            try:
                probe = system.adapters["ollama"].generate_structured(
                    spec=system.catalog.model("local_default"),
                    prompt='Respond in JSON with status="ok" and message="local probe".',
                    output_schema=_DoctorStructuredProbe,
                    requires_web=False,
                )
                report["ollama"]["structured_probe"] = {"ok": probe.parsed.status == "ok", "model": probe.model,
                    "model_digest": probe.model_digest, "tokens_in": probe.tokens_in, "tokens_out": probe.tokens_out,
                    "latency_ms": probe.latency_ms, "num_ctx": probe.metadata.get("num_ctx"),
                    "estimated_input_tokens": probe.metadata.get("estimated_input_tokens")}
            except Exception as exc:
                report["ollama"]["structured_probe"] = {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
        else:
            report["ollama"]["structured_probe"] = {"ok": False, "error": "Ollama unavailable"}

        if settings.gemini_api_key:
            report["cloud"]["model_discovery"] = _gemini_model_discovery(system.adapters["gemini"])
            try:
                probe = system.adapters["gemini"].generate_structured(
                    spec=system.catalog.model("cloud_fast"),
                    prompt='Respond in JSON with status="ok" and message="cloud probe".',
                    output_schema=_DoctorStructuredProbe,
                    requires_web=False,
                )
                report["cloud"]["fast_structured_probe"] = {"ok": True, "model": probe.model,
                    "tokens_in": probe.tokens_in, "tokens_out": probe.tokens_out, "latency_ms": probe.latency_ms}
            except Exception as exc:
                report["cloud"]["fast_structured_probe"] = {"ok": False, "error": str(exc), "error_type": type(exc).__name__}

            try:
                research_probe = system.adapters["gemini"].generate_structured(
                    spec=system.catalog.model("gemini_grounded"),
                    prompt=("Find a public source and return ResearchOutput about the HTTP standard. "
                            "Include at least one key_finding and source_urls when the tool returns citations."),
                    output_schema=ResearchOutput,
                    requires_web=True,
                )
                report["cloud"]["grounded_search_probe"] = {
                    "ok": bool(research_probe.sources or research_probe.parsed.source_urls),
                    "model": research_probe.model,
                    "sources": research_probe.sources,
                    "latency_ms": research_probe.latency_ms,
                }
            except Exception as exc:
                report["cloud"]["grounded_search_probe"] = {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
        else:
            report["cloud"]["fast_structured_probe"] = {"ok": False, "skipped": "gemini_api_key_missing"}
            report["cloud"]["grounded_search_probe"] = {"ok": False, "skipped": "gemini_api_key_missing"}

    grounded_ok = bool(report["cloud"].get("grounded_search_probe", {}).get("ok"))
    report["cloud"]["research_mode_recommended"] = "gemini_grounded" if grounded_ok else "human_bridge"
    if not grounded_ok:
        report["recommendations"].append("Use research_mode=human_bridge as the default web-research path")

    for name, path in {"data": settings.data_dir, "runs": settings.runs_dir, "assets": settings.assets_dir}.items():
        path.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(path)
        report["paths"][name] = {"path": str(path), "writable": os.access(path, os.W_OK), "free_gb": round(usage.free / 1024**3, 2)}

    required_missing = any(v == "missing" for k, v in report["python"].items() if k not in {"version", "supported", "executable", "venv"})
    hard_fail = required_missing or not report["python"]["supported"] or not report["database"]["writable"]
    # Human-guided execution can operate without model providers; provider capabilities are reported separately.
    return (not hard_fail), report
