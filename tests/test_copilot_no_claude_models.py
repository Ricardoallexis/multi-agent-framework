"""MOD-T02: the Copilot CLI never declares a Claude model.

Claude models run through the Claude CLI executor only. Every catalog folder of the repository (any folder with a
``models.yaml``, hidden folders skipped) is loaded with the real ``Catalog`` loader, and the ``copilot_cli`` declaration
of each must list no model whose id starts with ``claude``. Nothing is launched: the adapter is only asked whether it
refuses a model.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from multiagent.catalog import Catalog
from multiagent.config import Settings
from multiagent.executors.copilot import CopilotAdapter

REPO_ROOT = Path(__file__).resolve().parents[1]


def _catalog_dirs() -> list[Path]:
    """Every folder under the repository root that holds a ``models.yaml`` (hidden folders are not searched)."""
    found: list[Path] = []
    for folder, subfolders, files in os.walk(REPO_ROOT):
        subfolders[:] = sorted(name for name in subfolders if not name.startswith("."))
        if "models.yaml" in files:
            found.append(Path(folder))
    return found


CATALOG_DIRS = _catalog_dirs()


def _settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, local_dir=tmp_path, worker_enabled=False, mock_mode=True,
                    gemini_api_key="", openai_api_key="", openai_enabled=False)


def _copilot_models(catalog: Catalog) -> list[str]:
    """The model ids the ``copilot_cli`` executor declares in ``model_support`` (empty when it is not declared)."""
    executors = (catalog.executor_routing or {}).get("executors") or {}
    declaration = executors.get("copilot_cli") or {}
    evidence = declaration.get("model_support") or {}
    return [str(model) for model in (evidence.get("values") or [])]


def test_the_repository_has_catalogs_to_check():
    assert REPO_ROOT / "catalog" in CATALOG_DIRS          # the bundled catalog is among them, so the check is not empty


@pytest.mark.parametrize("catalog_dir", CATALOG_DIRS, ids=lambda path: path.relative_to(REPO_ROOT).as_posix())
def test_copilot_cli_declares_no_claude_model(catalog_dir, tmp_path):
    catalog = Catalog(_settings(tmp_path), catalog_dir=catalog_dir)
    claude = [model for model in _copilot_models(catalog) if model.lower().startswith("claude")]
    assert claude == [], f"copilot_cli must not declare Claude models, found {claude}"


def test_the_bundled_catalog_still_declares_its_copilot_model(tmp_path):
    catalog = Catalog(_settings(tmp_path), catalog_dir=REPO_ROOT / "catalog")
    assert "gpt-6-luna" in _copilot_models(catalog)       # proves the reader sees the declaration


def test_the_copilot_adapter_still_refuses_a_claude_model():
    assert CopilotAdapter().refuses_model("claude-sonnet-5.5") == "claude_model_rejected"
