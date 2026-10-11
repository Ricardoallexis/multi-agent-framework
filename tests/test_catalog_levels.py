"""Model levels, classification and price evidence in the catalog (ECO-T02)."""
from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from multiagent.catalog import (
    Catalog, CatalogError, ModelSpec, MODEL_CLASSIFICATIONS, MODEL_LEVELS, validate_pricing,
)
from multiagent.config import Settings
from multiagent.model_router import ModelRouter
from multiagent.routing_policy import RoutingEvidence, RoutingRequest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CATALOG = ROOT / "examples" / "teams" / "software_development" / "catalog"
NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, local_dir=tmp_path / "local")


def price_side(amount=0.5, **overrides):
    side = {
        "amount": amount, "denominator": 1_000_000, "unit": "token", "currency": "USD",
        "source": "unit-test rate card", "provenance": "catalog_declared",
        "valid_from": "2026-01-01T00:00:00Z", "valid_until": "2027-01-01T00:00:00Z",
    }
    side.update(overrides)
    return side


def pricing(amount=0.5, **overrides):
    return {"input": price_side(amount, **overrides), "output": price_side(amount * 2, **overrides)}


def local_no_charge():
    return pricing(0, source="Local model: no per-token charge", provenance="local_no_charge",
                   valid_until="2036-01-01T00:00:00Z")


def model(**overrides):
    values = {
        "provider": "ollama", "model": "m", "tier": "local", "structured_output": True, "web": False,
        "capabilities": ["text_generation"], "roles": ["assistant"], "permissions": [],
        "permissions_required": [], "allowed_data_classes": ["non_sensitive"],
    }
    values.update(overrides)
    return values


def load(tmp_path: Path, models: dict, **document) -> Catalog:
    folder = tmp_path / "catalog"
    folder.mkdir(exist_ok=True)
    (folder / "agents.yaml").write_text("agents: {}\n", encoding="utf-8")
    (folder / "models.yaml").write_text(yaml.safe_dump({"models": models, **document}), encoding="utf-8")
    return Catalog(settings(tmp_path), catalog_dir=folder)


# --- defaults and valid declarations ----------------------------------------------------------

def test_model_without_the_new_fields_keeps_working_and_is_unclassified(tmp_path):
    catalog = load(tmp_path, {"plain": model(input_cost_per_million=0.0, output_cost_per_million=0.0)})
    spec = catalog.model("plain")
    assert spec.level is None
    assert spec.classification == "unclassified"
    assert spec.pricing is None


@pytest.mark.parametrize("level", MODEL_LEVELS)
def test_every_level_can_be_classified_with_price_evidence(tmp_path, level):
    catalog = load(tmp_path, {"m": model(level=level, classification="classified", pricing=pricing())})
    spec = catalog.model("m")
    assert (spec.level, spec.classification) == (level, "classified")
    assert spec.pricing["input"]["amount"] == 0.5 and spec.pricing["output"]["amount"] == 1.0


def test_a_level_without_classification_stays_unclassified(tmp_path):
    spec = load(tmp_path, {"m": model(level="T2")}).model("m")
    assert spec.level == "T2" and spec.classification == "unclassified"


def test_classification_vocabulary_is_closed():
    assert MODEL_LEVELS == ("T1", "T2", "T3", "T4")
    assert MODEL_CLASSIFICATIONS == ("classified", "unclassified")


def test_pricing_block_is_normalized_and_the_source_dict_is_not_shared(tmp_path):
    declared = pricing(0.25, source="  rate card  ")
    spec = load(tmp_path, {"m": model(pricing=declared)}).model("m")
    assert spec.pricing["input"]["source"] == "rate card"
    assert spec.pricing is not declared


def test_unquoted_yaml_timestamps_are_accepted_and_still_usable_by_the_router(tmp_path):
    folder = tmp_path / "catalog"
    folder.mkdir()
    (folder / "agents.yaml").write_text("agents: {}\n", encoding="utf-8")
    side = (
        "        amount: 0.5\n        denominator: 1000000\n        unit: token\n        currency: USD\n"
        "        source: rate card\n        provenance: catalog_declared\n"
        "        valid_from: 2026-01-01T00:00:00Z\n        valid_until: 2027-01-01T00:00:00Z\n"
    )
    (folder / "models.yaml").write_text(
        "models:\n  m:\n    provider: ollama\n    model: m\n    tier: local\n    structured_output: true\n"
        "    web: false\n    pricing:\n      input:\n" + side + "      output:\n" + side,
        encoding="utf-8",
    )
    spec = Catalog(settings(tmp_path), catalog_dir=folder).model("m")
    assert ModelRouter._current_price(spec.pricing, now=NOW) is not None


# --- invalid declarations ---------------------------------------------------------------------

@pytest.mark.parametrize("level", ["T0", "T5", "t1", "high", 1, ""])
def test_invalid_level_is_rejected_at_load_with_the_model_id(tmp_path, level):
    with pytest.raises(CatalogError, match=r"Model 'm': level must be one of T1, T2, T3, T4"):
        load(tmp_path, {"m": model(level=level)})


def test_invalid_classification_is_rejected(tmp_path):
    with pytest.raises(CatalogError, match=r"Model 'm': classification must be one of classified, unclassified"):
        load(tmp_path, {"m": model(level="T1", classification="measured")})


def test_classified_requires_a_level(tmp_path):
    with pytest.raises(CatalogError, match=r"Model 'm': classification 'classified' requires a level"):
        load(tmp_path, {"m": model(classification="classified", pricing=pricing())})


def test_model_spec_enforces_level_rules_when_built_in_code():
    with pytest.raises(CatalogError, match="requires a level"):
        ModelSpec("m", "p", "x", "local", True, False, classification="classified")
    with pytest.raises(CatalogError, match="level must be one of"):
        ModelSpec("m", "p", "x", "local", True, False, level="T9")
    assert ModelSpec("m", "p", "x", "local", True, False, level="T3", classification="classified").level == "T3"


def test_catalog_error_is_a_value_error_and_a_framework_error():
    from multiagent.errors import MultiAgentError
    assert issubclass(CatalogError, ValueError) and issubclass(CatalogError, MultiAgentError)


@pytest.mark.parametrize("missing", ["input", "output"])
def test_pricing_needs_both_components(tmp_path, missing):
    block = pricing()
    del block[missing]
    with pytest.raises(CatalogError, match=rf"pricing\.{missing} is missing"):
        load(tmp_path, {"m": model(pricing=block)})


@pytest.mark.parametrize("field", ["amount", "denominator", "unit", "currency", "source",
                                   "valid_from", "valid_until", "provenance"])
def test_incomplete_price_names_the_missing_field(tmp_path, field):
    block = pricing()
    del block["output"][field]
    with pytest.raises(CatalogError, match=rf"pricing\.output is incomplete: missing {field}"):
        load(tmp_path, {"m": model(pricing=block)})


@pytest.mark.parametrize("override, message", [
    ({"amount": -1}, "amount must be a finite number >= 0"),
    ({"amount": True}, "amount must be a finite number >= 0"),
    ({"amount": "0.5"}, "amount must be a finite number >= 0"),
    ({"amount": float("inf")}, "amount must be a finite number >= 0"),
    ({"amount": float("nan")}, "amount must be a finite number >= 0"),
    ({"denominator": 0}, "denominator must be a positive integer"),
    ({"denominator": 1e6}, "denominator must be a positive integer"),
    ({"denominator": True}, "denominator must be a positive integer"),
    ({"unit": "  "}, "unit must be a non-empty string"),
    ({"currency": 5}, "currency must be a non-empty string"),
    ({"source": ""}, "source must be a non-empty string"),
    ({"provenance": "unknown"}, "provenance cannot be 'unknown'"),
    ({"valid_from": "last spring"}, "valid_from 'last spring' is not an ISO 8601 timestamp"),
    ({"valid_from": "2026-01-01T00:00:00"}, "valid_from '2026-01-01T00:00:00' needs a timezone"),
    ({"valid_until": "2025-12-31T00:00:00Z"}, "valid_until must be later than valid_from"),
    ({"valid_until": "2026-01-01T00:00:00Z"}, "valid_until must be later than valid_from"),
])
def test_invalid_price_values_are_rejected(tmp_path, override, message):
    block = pricing()
    block["input"].update(override)
    with pytest.raises(CatalogError, match=message):
        load(tmp_path, {"m": model(pricing=block)})


def test_bare_yaml_dates_are_not_timestamps(tmp_path):
    folder = tmp_path / "catalog"
    folder.mkdir()
    (folder / "agents.yaml").write_text("agents: {}\n", encoding="utf-8")
    side = ("        amount: 1\n        denominator: 1000000\n        unit: token\n        currency: USD\n"
            "        source: rate card\n        provenance: catalog_declared\n"
            "        valid_from: 2026-01-01\n        valid_until: 2027-01-01\n")
    (folder / "models.yaml").write_text(
        "models:\n  m:\n    provider: p\n    model: m\n    tier: cloud\n    structured_output: true\n"
        "    web: false\n    pricing:\n      input:\n" + side + "      output:\n" + side, encoding="utf-8")
    with pytest.raises(CatalogError, match="bare date"):
        Catalog(settings(tmp_path), catalog_dir=folder)


@pytest.mark.parametrize("field, other", [("currency", "EUR"), ("unit", "character"), ("denominator", 1000)])
def test_input_and_output_must_agree_on_currency_unit_and_denominator(tmp_path, field, other):
    block = pricing()
    block["output"][field] = other
    with pytest.raises(CatalogError, match=rf"input and output must use the same {field}"):
        load(tmp_path, {"m": model(pricing=block)})


@pytest.mark.parametrize("block", [[], "free", 3, {}, {"input": "x", "output": "y"}])
def test_pricing_must_be_a_mapping_of_prices(tmp_path, block):
    with pytest.raises(CatalogError):
        load(tmp_path, {"m": model(pricing=block)})


def test_unknown_pricing_keys_are_rejected_to_catch_typos(tmp_path):
    with pytest.raises(CatalogError, match=r"pricing has unknown keys \['outpt'\]"):
        load(tmp_path, {"m": model(pricing={**pricing(), "outpt": price_side()})})
    block = pricing()
    block["input"]["curency"] = "USD"
    with pytest.raises(CatalogError, match=r"unknown keys \['curency'\]"):
        load(tmp_path, {"m": model(pricing=block)})


def test_an_empty_pricing_block_is_incomplete_but_an_absent_one_is_fine(tmp_path):
    with pytest.raises(CatalogError, match="pricing.input is missing"):
        load(tmp_path, {"m": model(pricing={})})
    assert load(tmp_path, {"m": model(pricing=None)}).model("m").pricing is None


def test_validate_pricing_is_usable_on_its_own():
    assert validate_pricing("x", pricing())["input"]["currency"] == "USD"
    with pytest.raises(CatalogError, match="Model 'x': pricing"):
        validate_pricing("x", None)


# --- local, no charge -------------------------------------------------------------------------

def test_local_no_charge_with_amount_zero_is_accepted_and_is_a_usable_price(tmp_path):
    spec = load(tmp_path, {"m": model(level="T1", classification="classified", pricing=local_no_charge())}).model("m")
    assert spec.pricing["input"]["amount"] == 0 and spec.pricing["input"]["provenance"] == "local_no_charge"
    price = ModelRouter._current_price(spec.pricing, now=NOW)
    assert price == [(0.0, "USD", 1_000_000, "token"), (0.0, "USD", 1_000_000, "token")]


def test_local_no_charge_requires_zero_and_a_local_model(tmp_path):
    charged = local_no_charge()
    charged["input"]["amount"] = 0.1
    with pytest.raises(CatalogError, match="'local_no_charge' requires amount 0"):
        load(tmp_path, {"m": model(pricing=charged)})
    with pytest.raises(CatalogError, match="only valid for tier 'local'"):
        load(tmp_path, {"m": model(tier="cloud", provider="gemini", pricing=local_no_charge())})


def test_a_zero_amount_with_a_real_source_is_still_explicit_evidence(tmp_path):
    spec = load(tmp_path, {"m": model(tier="cloud", provider="gemini", pricing=pricing(0, source="free tier rate card"))}).model("m")
    assert spec.pricing["input"]["amount"] == 0


# --- legacy cost fields -----------------------------------------------------------------------

def test_nonzero_legacy_cost_without_pricing_warns(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="multiagent.catalog"):
        spec = load(tmp_path, {"m": model(input_cost_per_million=0.3, output_cost_per_million=1.2)}).model("m")
    assert spec.pricing is None and spec.input_cost_per_million == 0.3
    warnings = [r for r in caplog.records if r.name == "multiagent.catalog" and r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "'m'" in warnings[0].getMessage() and "not price evidence" in warnings[0].getMessage()


def test_only_one_nonzero_legacy_value_is_enough_to_warn(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="multiagent.catalog"):
        load(tmp_path, {"m": model(output_cost_per_million=2)})
    assert len(caplog.records) == 1


def test_zero_legacy_cost_or_real_pricing_does_not_warn(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="multiagent.catalog"):
        load(tmp_path, {
            "zero": model(input_cost_per_million=0.0, output_cost_per_million=0.0),
            "absent": model(),
            "evidence": model(input_cost_per_million=0.3, output_cost_per_million=1.2, pricing=pricing()),
        })
    assert caplog.records == []


def test_legacy_costs_are_never_turned_into_price_evidence(tmp_path):
    spec = load(tmp_path, {"m": model(input_cost_per_million=0.3, output_cost_per_million=1.2)}).model("m")
    assert spec.pricing is None
    assert ModelRouter._current_price(spec.pricing, now=NOW) is None


# --- the router reads pricing from the model spec -------------------------------------------------

def test_expired_or_future_pricing_loads_but_the_router_ignores_it(tmp_path):
    catalog = load(tmp_path, {
        "current": model(pricing=pricing()),
        "expired": model(pricing=pricing(valid_from="2024-01-01T00:00:00Z", valid_until="2025-01-01T00:00:00Z")),
        "future": model(pricing=pricing(valid_from="2030-01-01T00:00:00Z", valid_until="2031-01-01T00:00:00Z")),
    })
    assert ModelRouter._current_price(catalog.model("current").pricing, now=NOW) is not None
    assert ModelRouter._current_price(catalog.model("expired").pricing, now=NOW) is None
    assert ModelRouter._current_price(catalog.model("future").pricing, now=NOW) is None


def test_every_accepted_pricing_block_is_one_the_router_accepts_while_current(tmp_path):
    blocks = {"a": pricing(0.1), "b": pricing(3), "c": local_no_charge(),
              "d": {"input": price_side(7, denominator=1000, unit="character"),
                    "output": price_side(9, denominator=1000, unit="character")}}
    catalog = load(tmp_path, {key: model(pricing=copy.deepcopy(value)) for key, value in blocks.items()})
    for key in blocks:
        assert ModelRouter._current_price(catalog.model(key).pricing, now=NOW) is not None, key


def _economic_catalog(tmp_path: Path, models: dict) -> Catalog:
    return load(tmp_path, models, routing_profiles={"profiles": {"cheap": {
        "version": 1, "enabled": True, "candidate_model_ids": list(models), "max_candidates": len(models),
        "required_capabilities": ["structured_output"], "required_roles": [], "required_permissions": [],
        "allowed_permissions": [], "allowed_data_classes": ["non_sensitive"],
        "execution_modes": ["auto", "local", "cloud"], "economic_selection": True,
        "max_retries_per_candidate": 0, "max_provider_invocations": 2,
    }}, "assignments": {}})


def _select(catalog: Catalog):
    request = RoutingRequest(
        profile_id="cheap", workflow_id="w", step_id="s", requested_model=None,
        required_capabilities=frozenset({"structured_output"}), required_permissions=frozenset(),
        effective_permissions=frozenset(), authorized_data_classes=frozenset({"non_sensitive"}),
        authorization_provenance="caller_request", data_class="non_sensitive", execution_mode="auto",
        output_schema="Payload", estimated_input_tokens=1000, estimated_output_tokens=200,
    )
    return ModelRouter(catalog, {}).select(request, RoutingEvidence(scope="run-1", provenance="run_snapshot"))


def test_router_uses_the_pricing_loaded_from_the_catalog_file(tmp_path):
    catalog = _economic_catalog(tmp_path, {
        "paid": model(pricing=pricing(1.0)),
        "free": model(level="T1", classification="classified", pricing=local_no_charge()),
    })
    decision = _select(catalog)
    assert decision.status == "selected" and decision.selected_model == "free"
    assert decision.fallback_models == ("paid",)
    by_id = {candidate.model_id: candidate for candidate in decision.candidates}
    assert by_id["free"].pricing["input"]["provenance"] == "local_no_charge"


def test_router_behavior_without_evidence_is_unchanged_it_blocks(tmp_path):
    catalog = _economic_catalog(tmp_path, {
        "priced": model(pricing=pricing(1.0)),
        "legacy_only": model(input_cost_per_million=0.0, output_cost_per_million=0.0),
    })
    decision = _select(catalog)
    assert decision.status == "blocked" and decision.reason_codes == ("comparable_price_unknown",)


def test_router_blocks_when_loaded_pricing_is_out_of_date(tmp_path):
    catalog = _economic_catalog(tmp_path, {
        "stale": model(pricing=pricing(valid_from="2024-01-01T00:00:00Z", valid_until="2025-01-01T00:00:00Z")),
        "fresh": model(pricing=pricing(1.0)),
    })
    decision = _select(catalog)
    assert decision.status == "blocked" and decision.reason_codes == ("comparable_price_unknown",)


# --- the catalogs that ship with the project ----------------------------------------------------

LOCAL_MODELS = ("local_default", "local_reasoning")


def test_bundled_catalog_marks_only_the_local_models_as_classified_t1_with_no_charge_pricing(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="multiagent.catalog"):
        catalog = Catalog(settings(tmp_path))
    assert caplog.records == []
    for model_id, spec in catalog.models.items():
        if model_id in LOCAL_MODELS:
            assert spec.provider == "ollama" and spec.tier == "local"
            assert (spec.level, spec.classification) == ("T1", "classified")
            for side in ("input", "output"):
                assert spec.pricing[side]["provenance"] == "local_no_charge"
                assert spec.pricing[side]["amount"] == 0 and spec.pricing[side]["source"]
            assert ModelRouter._current_price(spec.pricing, now=datetime.now(timezone.utc)) is not None
        else:
            assert spec.tier == "cloud", model_id
            assert (spec.level, spec.classification, spec.pricing) == (None, "unclassified", None), model_id


def test_example_bundle_catalog_loads_with_the_same_treatment(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="multiagent.catalog"):
        catalog = Catalog(settings(tmp_path), catalog_dir=EXAMPLE_CATALOG)
    assert caplog.records == []
    spec = catalog.model("local_default")
    assert (spec.level, spec.classification) == ("T1", "classified")
    assert spec.pricing["input"]["provenance"] == "local_no_charge"
    assert ModelRouter._current_price(spec.pricing, now=datetime.now(timezone.utc)) is not None
    assert set(catalog.models) == {"local_default"}


def test_example_cloud_pricing_is_only_a_comment_clearly_marked_as_an_example():
    text = (EXAMPLE_CATALOG / "models.yaml").read_text(encoding="utf-8")
    commented = [line for line in text.splitlines() if line.lstrip().startswith("#")]
    assert any("EXAMPLE ONLY" in line and "not a real price" in line for line in commented)
    assert any("example_cloud_model:" in line for line in commented)
    assert "example_cloud_model" not in yaml.safe_load(text)["models"]
