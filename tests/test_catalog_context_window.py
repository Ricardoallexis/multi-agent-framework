"""Context window with evidence in the catalog (CMP-T08, contract CMP-T01 section 2)."""
from __future__ import annotations

import copy
import logging
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import yaml

from multiagent.catalog import (
    CONTEXT_WINDOW_PROVENANCES, Catalog, CatalogError, ModelSpec, usable_context_window, validate_context_window,
)
from multiagent.config import Settings

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CATALOG = ROOT / "examples" / "teams" / "software_development" / "catalog"
GENERAL_CATALOG = ROOT / "examples" / "bundles" / "general" / "catalog"
LOCAL_MODELS = ("local_default", "local_reasoning")
BUNDLED_GEMINI = ("cloud_fast", "gemini_fast", "cloud_advanced", "gemini_grounded")


def settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(_env_file=None, local_dir=tmp_path / "local", **overrides)


def window(**overrides):
    values = {
        "tokens": 131072, "source": "https://example.test/docs/models/m (Input token limit 131,072)",
        "checked_at": "2026-10-11", "provenance": "provider_docs", "model": "m-cloud",
    }
    values.update(overrides)
    return {key: value for key, value in values.items() if value is not ...}


def model(**overrides):
    values = {
        "provider": "gemini", "model": "m-cloud", "tier": "cloud", "structured_output": True, "web": False,
        "capabilities": ["text_generation"], "roles": ["assistant"], "permissions": [],
        "permissions_required": [], "allowed_data_classes": ["non_sensitive"],
    }
    values.update(overrides)
    return values


def load(tmp_path: Path, models: dict, **overrides) -> Catalog:
    folder = tmp_path / "catalog"
    folder.mkdir(exist_ok=True)
    (folder / "agents.yaml").write_text("agents: {}\n", encoding="utf-8")
    (folder / "models.yaml").write_text(yaml.safe_dump({"models": models}), encoding="utf-8")
    return Catalog(settings(tmp_path, **overrides), catalog_dir=folder)


# --- local models: the window is the num_ctx in force ------------------------------------------

@pytest.mark.parametrize("model_id", LOCAL_MODELS)
def test_bundled_local_models_take_the_window_from_num_ctx(tmp_path, model_id):
    catalog = Catalog(settings(tmp_path))
    found = catalog.context_window(model_id)
    assert found["tokens"] == catalog.model(model_id).num_ctx == 8192
    assert found["provenance"] == "runtime_setting"
    assert "num_ctx" in found["source"]
    assert found["checked_at"] is None  # read from the live setting, so it has no separate date
    assert found["model"] == catalog.model(model_id).model


def test_bundled_local_models_follow_the_ollama_num_ctx_setting(tmp_path):
    catalog = Catalog(settings(tmp_path, ollama_num_ctx=16384, ollama_model="other:7b"))
    for model_id in LOCAL_MODELS:
        found = catalog.context_window(model_id)
        assert (found["tokens"], found["provenance"], found["model"]) == (16384, "runtime_setting", "other:7b")


def test_an_ollama_model_uses_its_own_num_ctx_else_the_setting(tmp_path):
    catalog = load(tmp_path, {
        "own": model(provider="ollama", tier="local", num_ctx=32768),
        "default": model(provider="ollama", tier="local"),
    }, ollama_num_ctx=8192)
    assert catalog.context_window("own")["tokens"] == 32768
    assert catalog.context_window("default")["tokens"] == 8192


@pytest.mark.parametrize("bad", [True, "abc", -1])
def test_an_unusable_num_ctx_leaves_the_window_unknown(tmp_path, bad):
    catalog = load(tmp_path, {"odd": model(provider="ollama", tier="local", num_ctx=bad)})
    assert catalog.context_window("odd") is None


def test_an_ollama_model_cannot_declare_a_window(tmp_path):
    with pytest.raises(CatalogError, match=r"'local'.*cannot be declared.*num_ctx"):
        load(tmp_path, {"local": model(provider="ollama", tier="local", context_window=window())})


# --- cloud models: only declared evidence ------------------------------------------------------

def test_bundled_cloud_models_carry_documentation_evidence(tmp_path):
    catalog = Catalog(settings(tmp_path))
    for model_id in BUNDLED_GEMINI:
        found = catalog.context_window(model_id)
        spec = catalog.model(model_id)
        assert found["tokens"] == 1_048_576, model_id
        assert found["provenance"] == "provider_docs"
        assert found["source"].startswith("https://ai.google.dev/gemini-api/docs/models/" + spec.model), model_id
        assert date.fromisoformat(found["checked_at"]) >= date(2026, 10, 11)
        assert found["model"] == spec.model


def test_bundled_cloud_model_without_a_configured_model_stays_unknown(tmp_path):
    catalog = Catalog(settings(tmp_path))
    assert catalog.model("openai_optional").model == "configure-me"
    assert catalog.model("openai_optional").context_window is None
    assert catalog.context_window("openai_optional") is None


def test_every_bundled_window_is_a_number_with_evidence_or_unknown(tmp_path):
    catalog = Catalog(settings(tmp_path))
    for model_id, spec in catalog.models.items():
        found = catalog.context_window(model_id)
        if found is None:
            assert spec.tier == "cloud", f"{model_id}: a local model always has a window"
            continue
        assert type(found["tokens"]) is int and found["tokens"] > 0
        assert found["provenance"] in CONTEXT_WINDOW_PROVENANCES and found["source"].strip()
        if found["provenance"] == "provider_docs":
            date.fromisoformat(found["checked_at"])


def test_a_changed_provider_model_name_drops_the_evidence_and_warns(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="multiagent.catalog"):
        catalog = Catalog(settings(tmp_path, gemini_model="gemini-other"))
    # the two ids that read GEMINI_MODEL no longer match the evidence; the others are untouched
    assert catalog.context_window("cloud_fast") is None
    assert catalog.context_window("gemini_fast") is None
    assert catalog.context_window("cloud_advanced")["tokens"] == 1_048_576
    assert catalog.context_window("gemini_grounded")["tokens"] == 1_048_576
    warned = [r.getMessage() for r in caplog.records]
    assert len(warned) == 2 and all("gemini-other" in text and "unknown" in text for text in warned)


def test_the_bundled_catalog_loads_without_warnings(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="multiagent.catalog"):
        Catalog(settings(tmp_path))
    assert caplog.records == []


def test_a_declared_window_is_loaded_and_normalized(tmp_path):
    catalog = load(tmp_path, {"m": model(context_window=window(note="  caveat  "))})
    found = catalog.context_window("m")
    assert found == {**window(), "note": "caveat"}
    assert list(found) == ["tokens", "source", "checked_at", "provenance", "model", "note"]
    assert catalog.model("m").context_window == found


def test_the_model_name_in_the_evidence_must_match_ignoring_case(tmp_path):
    catalog = load(tmp_path, {"m": model(context_window=window(model="M-Cloud"))})
    assert catalog.context_window("m")["tokens"] == 131072


def test_evidence_for_another_model_name_is_unknown_with_a_warning(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="multiagent.catalog"):
        catalog = load(tmp_path, {"m": model(context_window=window(model="another-model"))})
    assert catalog.context_window("m") is None
    assert catalog.model("m").context_window is None
    assert any("another-model" in r.getMessage() and "m-cloud" in r.getMessage() for r in caplog.records)


def test_a_model_without_a_window_block_is_unknown_not_guessed(tmp_path):
    catalog = load(tmp_path, {"plain": model(), "local_like": model(provider="llama_cpp", tier="local", num_ctx=2048)})
    for model_id in ("plain", "local_like"):
        assert catalog.model(model_id).context_window is None
        assert catalog.context_window(model_id) is None


def test_a_model_that_is_not_in_the_catalog_has_no_window(tmp_path):
    catalog = Catalog(settings(tmp_path))
    assert catalog.context_window("no-such-model") is None
    assert catalog.context_window("fixture") is None


def test_the_returned_window_is_a_copy(tmp_path):
    catalog = Catalog(settings(tmp_path))
    first = catalog.context_window("cloud_fast")
    first["tokens"] = 1
    assert catalog.context_window("cloud_fast")["tokens"] == 1_048_576
    assert catalog.model("cloud_fast").context_window["tokens"] == 1_048_576


def test_yaml_dates_are_accepted_as_the_checked_date(tmp_path):
    folder = tmp_path / "catalog"
    folder.mkdir()
    (folder / "agents.yaml").write_text("agents: {}\n", encoding="utf-8")
    (folder / "models.yaml").write_text(
        "models:\n"
        "  m:\n"
        "    provider: gemini\n    model: m-cloud\n    tier: cloud\n    structured_output: true\n    web: false\n"
        "    context_window: {tokens: 1000, source: 'docs page', checked_at: 2026-10-11, provenance: provider_docs, model: m-cloud}\n",
        encoding="utf-8")
    catalog = Catalog(settings(tmp_path), catalog_dir=folder)
    assert catalog.context_window("m")["checked_at"] == "2026-10-11"


# --- validation of a declared block ------------------------------------------------------------

@pytest.mark.parametrize("checked_at, expected", [
    ("2026-10-11", "2026-10-11"),
    (date(2026, 10, 11), "2026-10-11"),
    (datetime(2026, 10, 11, 23, 30, tzinfo=timezone.utc), "2026-10-11"),
    ("2026-10-11T08:00:00Z", "2026-10-11"),
])
def test_checked_at_accepts_a_date_or_a_timestamp(checked_at, expected):
    assert validate_context_window("m", window(checked_at=checked_at))["checked_at"] == expected


@pytest.mark.parametrize("block, message", [
    ("not a mapping", "must be a mapping"),
    ([1, 2], "must be a mapping"),
    (window(extra=1), "unknown keys"),
    (window(tokens=...), "missing tokens"),
    (window(source=...), "missing source"),
    (window(checked_at=...), "missing checked_at"),
    (window(provenance=...), "missing provenance"),
    (window(model=...), "missing model"),
    (window(tokens=None), "missing tokens"),
    (window(tokens=0), "tokens must be a positive integer"),
    (window(tokens=-5), "tokens must be a positive integer"),
    (window(tokens=True), "tokens must be a positive integer"),
    (window(tokens="1000"), "tokens must be a positive integer"),
    (window(tokens=1000.5), "tokens must be a positive integer"),
    (window(source="   "), "source must be a non-empty string"),
    (window(source=7), "source must be a non-empty string"),
    (window(model=""), "model must be a non-empty string"),
    (window(provenance="runtime_setting"), "provenance must be 'provider_docs'"),
    (window(provenance="unknown"), "provenance must be 'provider_docs'"),
    (window(provenance="guess"), "provenance must be 'provider_docs'"),
    (window(checked_at="yesterday"), "checked_at"),
    (window(checked_at=20261011), "checked_at"),
    (window(note=""), "note must be a non-empty string"),
    (window(note=3), "note must be a non-empty string"),
])
def test_an_invalid_declaration_is_rejected_naming_the_model(tmp_path, block, message):
    with pytest.raises(CatalogError, match="Model 'm'.*context_window") as caught:
        validate_context_window("m", block)
    assert message in str(caught.value)
    with pytest.raises(CatalogError, match="Model 'm'.*context_window"):
        load(tmp_path, {"m": model(context_window=block)})


def test_a_valid_declaration_does_not_alias_the_input():
    original = window(note="n")
    snapshot = copy.deepcopy(original)
    result = validate_context_window("m", original)
    result["tokens"] = 1
    assert original == snapshot


# --- the single check consumers use ------------------------------------------------------------

@pytest.mark.parametrize("value", [
    None, "128000", 128000, [], {},
    window(tokens=0), window(tokens=True), window(tokens="128000"), window(tokens=-1),
    window(source=""), window(source=None), window(provenance="unknown"), window(provenance=None),
    window(provenance="provider_docs", checked_at=None), window(provenance="provider_docs", checked_at=""),
])
def test_usable_context_window_treats_anything_incomplete_as_unknown(value):
    assert usable_context_window(value) is None


def test_usable_context_window_keeps_well_formed_evidence():
    docs = window()
    assert usable_context_window(docs) == docs and usable_context_window(docs) is not docs
    runtime = {"tokens": 4096, "source": "num_ctx", "checked_at": None, "provenance": "runtime_setting", "model": "q"}
    assert usable_context_window(runtime) == runtime


def test_a_spec_built_by_hand_defaults_to_unknown_and_is_never_trusted_blindly(tmp_path):
    spec = ModelSpec(id="h", provider="p", model="m", tier="cloud", structured_output=True, web=False)
    assert spec.context_window is None
    broken = ModelSpec(id="b", provider="p", model="m", tier="cloud", structured_output=True, web=False,
                       context_window={"tokens": 0, "source": "x", "provenance": "provider_docs"})
    catalog = Catalog(settings(tmp_path))
    catalog.models["h"], catalog.models["b"] = spec, broken
    assert catalog.context_window("h") is None and catalog.context_window("b") is None


# --- the catalogs that ship with the project ---------------------------------------------------

@pytest.mark.parametrize("catalog_dir", [EXAMPLE_CATALOG, GENERAL_CATALOG])
def test_example_catalogs_load_and_give_their_local_model_a_window(tmp_path, caplog, catalog_dir):
    with caplog.at_level(logging.WARNING, logger="multiagent.catalog"):
        catalog = Catalog(settings(tmp_path), catalog_dir=catalog_dir)
    assert caplog.records == []
    assert catalog.context_window("local_default")["tokens"] == catalog.model("local_default").num_ctx


def test_the_bundled_file_documents_the_field():
    text = (ROOT / "catalog" / "models.yaml").read_text(encoding="utf-8")
    assert "context_window" in text.split("models:", 1)[0]  # explained in the header comment
    declared = [model_id for model_id, raw in yaml.safe_load(text)["models"].items() if "context_window" in raw]
    assert set(declared) == set(BUNDLED_GEMINI)
