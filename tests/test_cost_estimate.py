# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Estimated cost in the usage telemetry (ECO-T03): tokens x price evidence, unknown stays null."""
from __future__ import annotations

import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.adapters.base import LLMResponse
from multiagent.adapters.fake import FakeAdapter
from multiagent.api import create_app
from multiagent.bootstrap import build_system
from multiagent.config import Settings
from multiagent.contracts import RunStatus, SocialPostRequest
from multiagent.db import Database
from multiagent.db.store import Store
from multiagent.errors import ProviderUnavailable
from multiagent.model_router import ModelRouter
from multiagent.observability import TokenUsageRecord, UsageRecorder, estimate_call_cost
from multiagent.observability.usage import read_usage, summarize_usage

AT = datetime(2026, 6, 1, tzinfo=timezone.utc)


# --- synthetic price evidence ------------------------------------------------------------------

def price_side(amount, **overrides):
    side = {
        "amount": amount, "denominator": 1_000_000, "unit": "token", "currency": "USD",
        "source": "unit-test rate card", "provenance": "catalog_declared",
        "valid_from": "2026-01-01T00:00:00Z", "valid_until": "2027-01-01T00:00:00Z",
    }
    side.update(overrides)
    return side


def pricing(input_amount=2.0, output_amount=6.0, **overrides):
    return {"input": price_side(input_amount, **overrides), "output": price_side(output_amount, **overrides)}


def spec_with(pricing_block, model_id="m1"):
    return SimpleNamespace(id=model_id, pricing=pricing_block)


# --- one call: estimate_call_cost --------------------------------------------------------------

def test_priced_model_costs_tokens_times_input_and_output_price():
    estimate = estimate_call_cost(spec_with(pricing()), tokens_in=1000, tokens_out=500, at=AT)

    assert estimate is not None
    assert estimate.amount == pytest.approx(1000 * 2.0 / 1e6 + 500 * 6.0 / 1e6)  # 0.005
    assert estimate.currency == "USD" and estimate.estimated_cost_usd == estimate.amount
    basis = estimate.basis
    assert basis["model_id"] == "m1" and basis["currency"] == "USD" and basis["cost"] == estimate.amount
    assert (basis["tokens_in"], basis["tokens_out"]) == (1000, 500)
    assert basis["input"]["source"] == "unit-test rate card"
    assert basis["input"]["valid_from"] == "2026-01-01T00:00:00Z"
    assert basis["output"]["valid_until"] == "2027-01-01T00:00:00Z"
    assert basis["input"]["provenance"] == "catalog_declared"


def test_denominator_scales_the_price():
    block = pricing(1.0, 1.0, denominator=1000)
    assert estimate_call_cost(spec_with(block), tokens_in=500, tokens_out=250, at=AT).amount == pytest.approx(0.75)


@pytest.mark.parametrize("unit", ["token", "tokens", "Tokens"])
def test_both_spellings_of_the_token_unit_are_the_same_unit(unit):
    estimate = estimate_call_cost(spec_with(pricing(unit=unit)), tokens_in=1_000_000, tokens_out=1, at=AT)
    assert estimate is not None and estimate.amount == pytest.approx(2.0 + 6.0 / 1e6)


def test_a_price_that_is_not_per_token_is_unknown():
    assert estimate_call_cost(spec_with(pricing(unit="character")), tokens_in=10, tokens_out=10, at=AT) is None


def test_model_without_pricing_or_with_incomplete_pricing_is_unknown_never_zero():
    assert estimate_call_cost(spec_with(None), tokens_in=10, tokens_out=10, at=AT) is None
    assert estimate_call_cost(object(), tokens_in=10, tokens_out=10, at=AT) is None
    no_source = pricing()
    del no_source["output"]["source"]
    assert estimate_call_cost(spec_with(no_source), tokens_in=10, tokens_out=10, at=AT) is None
    one_side = {"input": price_side(1.0)}
    assert estimate_call_cost(spec_with(one_side), tokens_in=10, tokens_out=10, at=AT) is None
    unknown = pricing(provenance="unknown")
    assert estimate_call_cost(spec_with(unknown), tokens_in=10, tokens_out=10, at=AT) is None
    mixed = {"input": price_side(1.0), "output": price_side(2.0, currency="EUR")}
    assert estimate_call_cost(spec_with(mixed), tokens_in=10, tokens_out=10, at=AT) is None


def test_the_evidence_must_be_in_force_at_the_time_of_the_call():
    block = pricing()
    assert estimate_call_cost(spec_with(block), tokens_in=10, tokens_out=10, at=AT) is not None
    expired = datetime(2027, 1, 1, tzinfo=timezone.utc)          # valid_until is exclusive
    early = datetime(2025, 12, 31, tzinfo=timezone.utc)
    assert estimate_call_cost(spec_with(block), tokens_in=10, tokens_out=10, at=expired) is None
    assert estimate_call_cost(spec_with(block), tokens_in=10, tokens_out=10, at=early) is None
    # The default moment is "now": a window that ended long ago prices nothing today.
    old = pricing(valid_from="2020-01-01T00:00:00Z", valid_until="2021-01-01T00:00:00Z")
    assert estimate_call_cost(spec_with(old), tokens_in=10, tokens_out=10) is None
    current = pricing(valid_from="2020-01-01T00:00:00Z", valid_until="2100-01-01T00:00:00Z")
    assert estimate_call_cost(spec_with(current), tokens_in=10, tokens_out=10) is not None


@pytest.mark.parametrize("tokens_in, tokens_out", [
    (None, 5), (5, None), (None, None), (0, 5), (5, 0), (-1, 5), (True, 5), (5.0, 5), ("5", 5),
])
def test_missing_or_unreported_token_counts_leave_the_cost_unknown(tokens_in, tokens_out):
    assert estimate_call_cost(spec_with(pricing()), tokens_in=tokens_in, tokens_out=tokens_out, at=AT) is None


def test_local_no_charge_is_a_real_zero_with_its_evidence():
    block = pricing(0, 0, provenance="local_no_charge", source="Local model: no per-token charge",
                    valid_until="2036-01-01T00:00:00Z")
    estimate = estimate_call_cost(spec_with(block, "local_default"), tokens_in=71, tokens_out=29, at=AT)

    assert estimate is not None and estimate.amount == 0.0 and estimate.estimated_cost_usd == 0.0
    assert estimate.basis["input"]["provenance"] == "local_no_charge"
    assert estimate.basis["input"]["source"] == "Local model: no per-token charge"
    # A local model whose tokens were not reported is still unknown, not a free call.
    assert estimate_call_cost(spec_with(block), tokens_in=None, tokens_out=None, at=AT) is None


def test_non_usd_currency_is_not_stored_as_usd_but_keeps_cost_and_currency():
    estimate = estimate_call_cost(spec_with(pricing(currency="EUR")), tokens_in=1000, tokens_out=500, at=AT)

    assert estimate is not None
    assert estimate.currency == "EUR" and estimate.estimated_cost_usd is None
    assert estimate.basis["currency"] == "EUR" and estimate.basis["cost"] == pytest.approx(0.005)


def test_usd_currency_is_matched_ignoring_case():
    estimate = estimate_call_cost(spec_with(pricing(currency="usd")), tokens_in=1000, tokens_out=500, at=AT)
    assert estimate is not None and estimate.estimated_cost_usd == pytest.approx(0.005)


def test_router_and_telemetry_share_the_same_validity_check():
    for block, at in ((pricing(), AT), (pricing(), datetime(2028, 1, 1, tzinfo=timezone.utc)),
                      (pricing(currency="EUR"), AT), (None, AT)):
        shared = ModelRouter._current_price(block, now=at)
        priced = estimate_call_cost(spec_with(block), tokens_in=10, tokens_out=10, at=at)
        assert (shared is not None) == (priced is not None)


# --- totals: known subtotal, unknown kept apart -------------------------------------------------

def record(model="m", cost=None, basis=None, tokens=(10, 20), step="s", agent="a", success=True):
    tin, tout = tokens
    return {
        "id": 1, "step_id": step, "agent_id": agent, "attempt": 1, "provider": "p", "model": model,
        "model_digest": "", "prompt_id": "p", "prompt_version": 1, "tokens_in": tin, "tokens_out": tout,
        "tokens_total": tin + tout if tin is not None and tout is not None else None, "num_ctx": None,
        "duration_ms": 1, "estimated_cost_usd": cost, "cost_basis": basis, "success": success,
        "error_type": "", "created_at": "2026-06-01T00:00:00+00:00",
    }


def test_totals_with_mixed_known_and_unknown_costs_never_add_unknown_as_zero():
    records = [
        record("priced", cost=0.25),
        record("priced", cost=0.5, step="t"),
        record("unpriced", cost=None, step="t", agent="b"),
        record("human", cost=None, tokens=(None, None), agent="b"),
    ]
    totals = summarize_usage(records)

    run = totals["run"]
    assert run["estimated_cost_usd"] == pytest.approx(0.75)
    assert run["cost_known_records"] == 2 and run["cost_unknown_records"] == 2
    assert run["cost_other_currencies"] == {} and run["cost_complete"] is False
    by_model = {item["model"]: item for item in totals["by_model"]}
    assert by_model["priced"]["estimated_cost_usd"] == pytest.approx(0.75)
    assert by_model["priced"]["cost_complete"] is True and by_model["priced"]["cost_unknown_records"] == 0
    assert by_model["unpriced"]["estimated_cost_usd"] is None  # unknown, not 0
    assert by_model["unpriced"]["cost_unknown_records"] == 1 and by_model["unpriced"]["cost_complete"] is False
    assert by_model["human"]["estimated_cost_usd"] is None
    by_step = {item["step_id"]: item for item in totals["by_step"]}
    assert by_step["t"]["estimated_cost_usd"] == pytest.approx(0.5) and by_step["t"]["cost_unknown_records"] == 1
    by_agent = {item["agent_id"]: item for item in totals["by_agent"]}
    assert by_agent["a"]["cost_complete"] is True and by_agent["b"]["cost_known_records"] == 0


def test_a_real_zero_is_a_known_cost_not_an_unknown_one():
    totals = summarize_usage([record("local", cost=0.0), record("local", cost=0.0)])["run"]
    assert totals["estimated_cost_usd"] == 0.0
    assert totals["cost_known_records"] == 2 and totals["cost_unknown_records"] == 0 and totals["cost_complete"] is True


def test_other_currencies_are_summed_apart_and_are_not_unknown():
    eur = {"currency": "EUR", "cost": 0.125}
    records = [record(cost=0.25), record(cost=None, basis=eur), record(cost=None, basis=eur),
               record(cost=None, basis={"currency": "GBP", "cost": 1})]
    run = summarize_usage(records)["run"]

    assert run["estimated_cost_usd"] == pytest.approx(0.25)
    assert run["cost_other_currencies"] == {"EUR": 0.25, "GBP": 1}
    assert run["cost_known_records"] == 1 and run["cost_unknown_records"] == 0
    assert run["cost_complete"] is False  # the USD subtotal is not the whole cost


def test_no_records_and_no_known_cost_are_null_not_zero():
    empty = summarize_usage([])
    assert empty["run"]["estimated_cost_usd"] is None and empty["run"]["cost_complete"] is False
    assert empty["by_model"] == []
    unknown = summarize_usage([record(cost=None)])["run"]
    assert unknown["estimated_cost_usd"] is None and unknown["cost_unknown_records"] == 1


# --- the engine writes the estimate -------------------------------------------------------------

def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data", db_path=tmp_path / "data" / "multiagent.db",
        runs_dir=tmp_path / "data" / "runs", assets_dir=tmp_path / "data" / "assets",
        worker_enabled=False, gemini_api_key="",
    )


class UsageAdapter(FakeAdapter):
    """An Ollama-looking provider that reports fixed token counts."""

    provider = "ollama"

    def __init__(self, tokens_in, tokens_out):
        super().__init__()
        self.tokens = (tokens_in, tokens_out)

    def generate_structured(self, **kwargs) -> LLMResponse:
        response = super().generate_structured(**kwargs)
        response.tokens_in, response.tokens_out = self.tokens
        return response


class Unavailable(FakeAdapter):
    provider = "ollama"

    def generate_structured(self, **kwargs):
        raise ProviderUnavailable("offline")


def run_with_pricing(tmp_path, adapter, block, *, gemini=None, model_id="local_default"):
    overrides = {"ollama": adapter}
    if gemini is not None:
        overrides["gemini"] = gemini
    system = build_system(settings_for(tmp_path), adapter_overrides=overrides)
    spec = system.catalog.models[model_id]
    system.catalog.models[model_id] = replace(spec, pricing=block)
    run = system.run_service.create_social_post(
        SocialPostRequest(project_name="cost", objective="Create an educational post", topic="KNX"))
    assert system.worker.process_once() is True
    assert system.run_service.get(run["id"])["status"] == RunStatus.WAITING_HUMAN.value
    return system, run["id"]


FOREVER = dict(valid_from="2020-01-01T00:00:00Z", valid_until="2100-01-01T00:00:00Z")


def test_engine_records_the_estimate_and_its_basis_for_a_priced_model(tmp_path):
    system, run_id = run_with_pricing(tmp_path, UsageAdapter(1000, 500), pricing(**FOREVER))

    [row] = read_usage(system.store, run_id)
    assert row["estimated_cost_usd"] == pytest.approx(0.005)
    basis = row["cost_basis"]
    assert basis["model_id"] == "local_default" and basis["currency"] == "USD"
    assert basis["input"]["source"] == "unit-test rate card"
    assert basis["input"]["valid_until"] == "2100-01-01T00:00:00Z" and basis["output"]["provenance"] == "catalog_declared"
    run_totals = summarize_usage([row])["run"]
    assert run_totals["estimated_cost_usd"] == pytest.approx(0.005) and run_totals["cost_complete"] is True


def test_engine_leaves_the_cost_unknown_without_usable_evidence_or_tokens(tmp_path):
    expired = pricing(valid_from="2020-01-01T00:00:00Z", valid_until="2021-01-01T00:00:00Z")
    cases = {
        "no pricing": (UsageAdapter(1000, 500), None),
        "expired pricing": (UsageAdapter(1000, 500), expired),
        "unreported tokens": (UsageAdapter(0, 0), pricing(**FOREVER)),
        "one count missing": (UsageAdapter(1000, 0), pricing(**FOREVER)),
    }
    for index, (label, (adapter, block)) in enumerate(cases.items()):
        system, run_id = run_with_pricing(tmp_path / f"case{index}", adapter, block)
        [row] = read_usage(system.store, run_id)
        assert row["estimated_cost_usd"] is None, label
        assert row["cost_basis"] is None, label
        run_totals = summarize_usage([row])["run"]
        assert run_totals["estimated_cost_usd"] is None and run_totals["cost_unknown_records"] == 1, label


def test_engine_stores_a_non_usd_cost_only_in_the_basis(tmp_path):
    system, run_id = run_with_pricing(tmp_path, UsageAdapter(1000, 500), pricing(currency="EUR", **FOREVER))

    [row] = read_usage(system.store, run_id)
    assert row["estimated_cost_usd"] is None
    assert row["cost_basis"]["currency"] == "EUR" and row["cost_basis"]["cost"] == pytest.approx(0.005)
    run_totals = summarize_usage([row])["run"]
    assert run_totals["estimated_cost_usd"] is None and run_totals["cost_other_currencies"] == {"EUR": 0.005}
    assert run_totals["cost_unknown_records"] == 0


def test_engine_gives_the_bundled_local_model_a_real_zero(tmp_path):
    system = build_system(settings_for(tmp_path), adapter_overrides={"ollama": UsageAdapter(300, 120)})
    run = system.run_service.create_social_post(
        SocialPostRequest(project_name="local-zero", objective="Create an educational post", topic="KNX"))
    system.worker.process_once()

    [row] = read_usage(system.store, run["id"])
    assert row["estimated_cost_usd"] == 0.0
    assert row["cost_basis"]["input"]["provenance"] == "local_no_charge"


def test_failed_priced_attempt_and_unpriced_fallback_make_a_mixed_run(tmp_path):
    class RejectedOnce(UsageAdapter):
        def generate_structured(self, **kwargs):
            response = super().generate_structured(**kwargs)
            response.parsed = response.parsed.model_copy(update={"title": " "})  # fails the semantic check
            return response

    system, run_id = run_with_pricing(
        tmp_path, RejectedOnce(71, 29), pricing(**FOREVER), gemini=FakeAdapter())

    rows = read_usage(system.store, run_id)
    assert [row["success"] for row in rows] == [False, True]
    assert rows[0]["estimated_cost_usd"] == pytest.approx(71 * 2.0 / 1e6 + 29 * 6.0 / 1e6)
    assert rows[1]["estimated_cost_usd"] is None and rows[1]["cost_basis"] is None  # fallback has no price
    totals = summarize_usage(rows)
    run_totals = totals["run"]
    assert run_totals["estimated_cost_usd"] == pytest.approx(rows[0]["estimated_cost_usd"])
    assert run_totals["cost_known_records"] == 1 and run_totals["cost_unknown_records"] == 1
    assert run_totals["cost_complete"] is False
    assert {item["model"] for item in totals["by_model"]} == {"fixture"} | {rows[0]["model"]}


def test_usage_endpoint_exposes_the_cost_fields_and_per_model_totals(tmp_path):
    system, run_id = run_with_pricing(tmp_path, UsageAdapter(1000, 500), pricing(**FOREVER))

    with TestClient(create_app(system), base_url="http://localhost") as client:
        body = client.get(f"/api/v1/runs/{run_id}/usage").json()

    assert body["cost_is_estimate"] is True
    assert body["records"][0]["estimated_cost_usd"] == pytest.approx(0.005)
    assert body["records"][0]["cost_basis"]["model_id"] == "local_default"
    run_totals = body["totals"]["run"]
    assert {"cost_known_records", "cost_unknown_records", "cost_other_currencies", "cost_complete"} <= run_totals.keys()
    assert [item["model"] for item in body["totals"]["by_model"]] == [body["records"][0]["model"]]


def test_dry_run_and_human_rows_stay_unknown(tmp_path):
    system = build_system(settings_for(tmp_path), dry_run=True)
    run = system.run_service.create_social_post(
        SocialPostRequest(project_name="dry", objective="Create an educational post", topic="KNX"))
    system.worker.process_once()

    [row] = read_usage(system.store, run["id"])
    assert row["tokens_in"] == 10 and row["estimated_cost_usd"] is None and row["cost_basis"] is None


# --- storage: the recorder and the migration ----------------------------------------------------

def usage_record(**overrides):
    values = dict(
        run_id="r1", step_id="s1", agent_id="a", attempt=1, provider="p", model="m", model_digest="",
        prompt_id="x", prompt_version=1, prompt_sha256="", brand_version=None, tokens_in=1, tokens_out=2,
        num_ctx=None, duration_ms=3, estimated_cost_usd=None, success=True,
    )
    values.update(overrides)
    return TokenUsageRecord(**values)


def test_recorder_round_trips_the_basis_and_a_record_without_one(tmp_path):
    db = Database(Settings(data_dir=tmp_path / "data", db_path=tmp_path / "data" / "multiagent.db"))
    db.migrate()
    store = Store(db)
    recorder = UsageRecorder(store)
    basis = {"model_id": "m1", "currency": "EUR", "cost": 0.5, "input": {"source": "são €"}}

    assert recorder.record(usage_record(cost_basis=basis)) is True
    assert recorder.record(usage_record(step_id="s2", estimated_cost_usd=0.0)) is True

    rows = read_usage(store, "r1")
    assert rows[0]["cost_basis"] == basis and rows[0]["estimated_cost_usd"] is None
    assert rows[1]["cost_basis"] is None and rows[1]["estimated_cost_usd"] == 0.0


def test_migration_adds_cost_basis_to_an_existing_database_and_keeps_rows(tmp_path):
    source = Path(__file__).parents[1] / "migrations"
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    for path in sorted(source.glob("*.sql")):
        if int(path.stem.split("_", 1)[0]) <= 9:
            shutil.copyfile(path, migrations_dir / path.name)
    db = Database(SimpleNamespace(data_dir=tmp_path / "data", db_path=tmp_path / "data" / "multiagent.db",
                                  migrations_dir=migrations_dir))
    db.migrate()
    assert db.schema_version() == 9
    with db.connect() as conn:
        assert "cost_basis" not in {row["name"] for row in conn.execute("PRAGMA table_info(telemetry)")}
        conn.execute(
            "INSERT INTO telemetry(run_id,step_id,provider,model,prompt_id,prompt_version,tokens_in,tokens_out,"
            "estimated_cost_usd,success,created_at) VALUES('old','s','p','m','x',1,5,6,NULL,1,'2026-01-01T00:00:00Z')")
        conn.commit()

    shutil.copyfile(source / "010_telemetry_cost_basis.sql", migrations_dir / "010_telemetry_cost_basis.sql")
    assert db.migrate() == ["010_telemetry_cost_basis.sql"]
    assert db.schema_version() == 10
    assert db.migrate() == []  # applied once

    # The store also writes the task link that migration 011 (PRY) adds to telemetry, so the schema moves on first.
    shutil.copyfile(source / "011_projects.sql", migrations_dir / "011_projects.sql")
    assert db.migrate() == ["011_projects.sql"]

    # And the cache token columns of migration 013 (CAP-T04), which the store writes too.
    shutil.copyfile(source / "013_telemetry_cache_tokens.sql", migrations_dir / "013_telemetry_cache_tokens.sql")
    assert db.migrate() == ["013_telemetry_cache_tokens.sql"]

    store = Store(db)
    [old] = read_usage(store, "old")
    assert (old["tokens_in"], old["tokens_out"], old["estimated_cost_usd"], old["cost_basis"]) == (5, 6, None, None)
    UsageRecorder(store).record(usage_record(run_id="new", estimated_cost_usd=0.1, cost_basis={"cost": 0.1}))
    [new] = read_usage(store, "new")
    assert new["estimated_cost_usd"] == 0.1 and new["cost_basis"] == {"cost": 0.1}
    totals = summarize_usage([old, new])["run"]
    assert totals["cost_known_records"] == 1 and totals["cost_unknown_records"] == 1
