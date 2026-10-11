"""ECO-T07: measuring the economic profile and the adoption criterion.

Three layers, none of which calls a real model:

* the criterion (``tools/evals/routing/adoption.py``) on synthetic comparator records: the 20-case minimum,
  ties, quality regressions, new critical errors, unknown and incomparable costs, unpaired cases;
* the exporter (``tools/evals/routing/export_runs.py``) on a real framework database: record shape, outcome
  rules, route checks, read-only access and privacy;
* a synthetic end to end: one small corpus run with fake providers under the legacy route and under the economic
  profile (fake priced catalog entries T1/T2/T3, some cases escalating), exported, compared and judged.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from multiagent.adapters.base import LLMAdapter, LLMResponse
from multiagent.bootstrap import build_system
from multiagent.catalog import ModelSpec
from multiagent.config import Settings
from multiagent.contracts import ContentOutput, RunStatus, SocialPostRequest
from multiagent.errors import StructuredOutputError
from tools.evals.routing import adoption, compare, export_runs
from tools.evals.routing.adoption import ADOPT, INSUFFICIENT, NOT_ADOPT, AdoptionError, evaluate

ROOT = Path(__file__).resolve().parents[1]

# --- the criterion on synthetic records ---------------------------------------------------------------------


def rec(case, route, *, category="extraction", accepted=True, critical=False, cost=0.01, unit="USD", escalations=0, **extra):
    value = {
        "case_id": case, "route": route, "category": category, "input_version": "v1", "input_hash": f"in-{case}",
        "rubric_version": "r1", "rubric_hash": "rub-1", "config_hash": "cfg-1", "surface": "fake-offline",
        "accepted": accepted and not critical, "critical_error": critical, "calls": 1, "input_tokens": 1000,
        "output_tokens": 500, "cost": cost, "cost_unit": unit if cost is not None else None, "duration_ms": 10,
        "rework": 0, "escalations": escalations,
    }
    value.update(extra)
    return value


def corpus(cases, *, base_cost=0.03, cand_cost=0.01, base_rejected=(), cand_rejected=(), base_critical=(),
           cand_critical=(), cand_unknown=(), cand_unit="USD", base_unit="USD", candidate="economic"):
    """Paired records for ``cases`` = {category: count}; case ids are ``<category>-<index>``."""
    rows = []
    for category, count in cases.items():
        for index in range(count):
            case = f"{category}-{index:02d}"
            rows.append(rec(case, "legacy", category=category, cost=base_cost, unit=base_unit,
                            accepted=case not in base_rejected, critical=case in base_critical))
            rows.append(rec(case, candidate, category=category, cost=None if case in cand_unknown else cand_cost,
                            unit=cand_unit, accepted=case not in cand_rejected, critical=case in cand_critical,
                            escalations=1 if index % 3 == 2 else 0))
    return rows


def codes(group):
    return {item["code"] for item in group["reasons"]}


def category(verdict, name):
    return next(group for group in verdict["categories"] if group["category"] == name)


TWO = {"extraction": 20, "classification": 20}


def test_every_criterion_met_with_the_minimum_cases_adopts():
    verdict = evaluate(corpus(TWO))
    assert verdict["overall"]["verdict"] == ADOPT and verdict["paired_cases"] == 40
    assert [group["verdict"] for group in verdict["categories"]] == [ADOPT, ADOPT]
    assert verdict["overall"]["categories"] == {ADOPT: 2, NOT_ADOPT: 0, INSUFFICIENT: 0}
    group = category(verdict, "extraction")
    assert group["checks"]["cost"]["status"] == "pass" and group["checks"]["cost"]["saving_pct"] == pytest.approx(66.666, abs=0.01)
    assert group["candidate"]["escalations"]["known_total"] == 6                      # reported, not a criterion
    assert verdict["criterion"]["min_cases_per_category"] == 20 and verdict["criterion"]["max_acceptance_drop_points"] == 2
    json.dumps(verdict)                                                               # serializable as is


def test_one_case_short_of_the_minimum_is_insufficient_data_and_the_overall_follows():
    verdict = evaluate(corpus({"extraction": 20, "classification": 19}))
    assert category(verdict, "extraction")["verdict"] == ADOPT
    short = category(verdict, "classification")
    assert short["verdict"] == INSUFFICIENT and "insufficient_cases" in codes(short)
    assert verdict["overall"]["verdict"] == INSUFFICIENT
    assert evaluate(corpus({"extraction": 19}), min_cases=19)["overall"]["verdict"] == ADOPT      # the minimum is a parameter


def test_a_failing_criterion_on_too_few_cases_stays_insufficient_but_says_so():
    verdict = evaluate(corpus({"extraction": 10}, cand_rejected={"extraction-00", "extraction-01"}))
    group = category(verdict, "extraction")
    assert group["verdict"] == INSUFFICIENT and group["preliminary_failing_checks"] == ["acceptance"]
    assert verdict["overall"]["verdict"] == INSUFFICIENT


def test_a_tie_is_not_a_saving():
    verdict = evaluate(corpus(TWO, base_cost=0.02, cand_cost=0.02))
    assert verdict["overall"]["verdict"] == NOT_ADOPT
    assert "cost_tie" in codes(category(verdict, "extraction"))


def test_a_tie_that_floating_point_would_miss_is_still_a_tie():
    verdict = evaluate(corpus(TWO, base_cost=0.1 + 0.2, cand_cost=0.3))
    assert category(verdict, "extraction")["checks"]["cost"]["reason"] == "cost_tie"
    assert verdict["overall"]["verdict"] == NOT_ADOPT


def test_a_higher_cost_is_not_adopt():
    verdict = evaluate(corpus(TWO, base_cost=0.01, cand_cost=0.02))
    assert verdict["overall"]["verdict"] == NOT_ADOPT and "cost_higher" in codes(category(verdict, "classification"))


def test_acceptance_may_drop_by_two_points_but_not_more():
    cases = {"extraction": 100}
    ok = evaluate(corpus(cases, cand_rejected={"extraction-00", "extraction-01"}))                    # 100% -> 98%
    group = category(ok, "extraction")
    assert group["checks"]["acceptance"]["drop_points"] == 2.0 and ok["overall"]["verdict"] == ADOPT
    bad = evaluate(corpus(cases, cand_rejected={"extraction-00", "extraction-01", "extraction-02"}))  # 97%
    assert category(bad, "extraction")["checks"]["acceptance"]["status"] == "fail"
    assert bad["overall"]["verdict"] == NOT_ADOPT and "acceptance_regression" in codes(category(bad, "extraction"))
    wide = evaluate(corpus(cases, cand_rejected={"extraction-00", "extraction-01", "extraction-02"}),
                    max_acceptance_drop_points=3)
    assert wide["overall"]["verdict"] == ADOPT


def test_a_quality_regression_beats_a_cheaper_cost():
    verdict = evaluate(corpus(TWO, cand_cost=0.0001, cand_rejected={"extraction-00"}))      # 1 of 20 = 5 points
    assert verdict["overall"]["verdict"] == NOT_ADOPT
    assert category(verdict, "extraction")["verdict"] == NOT_ADOPT and category(verdict, "classification")["verdict"] == ADOPT


def test_only_new_critical_errors_count():
    new = evaluate(corpus({"extraction": 100}, cand_critical={"extraction-05"}))              # 1 point of acceptance: inside the margin
    group = category(new, "extraction")
    assert group["checks"]["acceptance"]["status"] == "pass" and group["checks"]["critical_errors"]["new_cases"] == 1
    assert new["overall"]["verdict"] == NOT_ADOPT and "new_critical_errors" in codes(group)
    both = evaluate(corpus({"extraction": 100}, base_critical={"extraction-05"}, cand_critical={"extraction-05"}))
    assert both["overall"]["verdict"] == ADOPT and category(both, "extraction")["checks"]["critical_errors"]["new_cases"] == 0
    fixed = evaluate(corpus({"extraction": 100}, base_critical={"extraction-05"}))
    assert category(fixed, "extraction")["checks"]["critical_errors"]["fixed_cases"] == 1 and fixed["overall"]["verdict"] == ADOPT


def test_an_unknown_cost_is_insufficient_data_never_a_zero():
    verdict = evaluate(corpus(TWO, cand_unknown={"extraction-03"}))
    group = category(verdict, "extraction")
    assert group["verdict"] == INSUFFICIENT and group["checks"]["cost"]["reason"] == "cost_unknown"
    assert group["candidate"]["cost_per_accepted"]["status"] == "unknown"
    assert category(verdict, "classification")["verdict"] == ADOPT and verdict["overall"]["verdict"] == INSUFFICIENT


def test_a_quality_failure_decides_even_when_the_cost_is_unknown():
    verdict = evaluate(corpus(TWO, cand_unknown={"extraction-03"}, cand_rejected={"extraction-00"}))
    assert category(verdict, "extraction")["verdict"] == NOT_ADOPT and verdict["overall"]["verdict"] == NOT_ADOPT


def test_costs_in_different_units_are_never_compared():
    verdict = evaluate(corpus(TWO, cand_unit="EUR"))
    group = category(verdict, "extraction")
    assert group["verdict"] == INSUFFICIENT and group["checks"]["cost"]["reason"] == "cost_units_incomparable"


def test_categories_in_different_units_pass_alone_but_not_pooled():
    rows = corpus({"extraction": 20}) + corpus({"classification": 20}, base_unit="EUR", cand_unit="EUR")
    verdict = evaluate(rows)
    assert [group["verdict"] for group in verdict["categories"]] == [ADOPT, ADOPT]
    assert verdict["overall"]["verdict"] == INSUFFICIENT and "pooled_insufficient" in codes(verdict["overall"])


def test_no_accepted_run_leaves_the_cost_undefined():
    verdict = evaluate(corpus({"extraction": 20}, base_rejected={f"extraction-{i:02d}" for i in range(20)}))
    group = category(verdict, "extraction")
    assert group["checks"]["cost"]["reason"] == "cost_undefined" and group["verdict"] == INSUFFICIENT


def test_cases_that_do_not_pair_are_left_out_with_their_reason():
    rows = corpus({"extraction": 21})
    rows = [dict(row, rubric_hash="changed") if (row["route"] == "economic" and row["case_id"] == "extraction-00") else row
            for row in rows]
    rows = [row for row in rows if not (row["route"] == "economic" and row["case_id"] == "extraction-01")]
    verdict = evaluate(rows)
    assert verdict["excluded_cases"] == {"rubric_hash_mismatch": 1, "route_missing": 1}
    assert verdict["paired_cases"] == 19 and category(verdict, "extraction")["verdict"] == INSUFFICIENT
    rows = [dict(row, category="other") if (row["route"] == "economic" and row["case_id"] == "extraction-05") else row
            for row in corpus({"extraction": 22})]
    assert evaluate(rows)["excluded_cases"] == {"category_mismatch": 1}


def test_a_corpus_category_with_no_case_keeps_the_verdict_open():
    verdict = evaluate(corpus({"extraction": 20}), categories=["extraction", "classification"])
    empty = category(verdict, "classification")
    assert empty["paired_cases"] == 0 and empty["verdict"] == INSUFFICIENT
    assert verdict["overall"]["verdict"] == INSUFFICIENT


def test_cases_without_a_category_cannot_adopt():
    rows = [dict(row, category=None) for row in corpus({"extraction": 20})]
    verdict = evaluate(rows)
    assert verdict["overall"]["verdict"] == INSUFFICIENT and "cases_without_category" in codes(verdict["overall"])
    assert category(verdict, adoption.UNCATEGORIZED)["verdict"] == ADOPT


def test_a_failing_group_makes_the_overall_not_adopt_even_with_another_group_short():
    rows = corpus({"extraction": 20}, cand_cost=0.05) + corpus({"classification": 5})
    verdict = evaluate(rows)
    assert category(verdict, "classification")["verdict"] == INSUFFICIENT
    assert verdict["overall"]["verdict"] == NOT_ADOPT


def test_no_paired_case_at_all():
    rows = [rec("a", "legacy"), rec("b", "economic")]
    verdict = evaluate(rows)
    assert verdict["overall"]["verdict"] == INSUFFICIENT and "no_paired_cases" in codes(verdict["overall"])
    assert verdict["categories"] == [] and verdict["excluded_cases"] == {"route_missing": 2}


def test_routes_are_named_explicitly_when_they_are_ambiguous():
    rows = corpus({"extraction": 2}) + [dict(row, route="other") for row in corpus({"extraction": 2})[1::2]]
    with pytest.raises(AdoptionError, match="name the candidate"):
        evaluate(rows)
    assert evaluate(rows, candidate="economic", min_cases=2)["criterion"]["candidate_route"] == "economic"
    with pytest.raises(AdoptionError, match="baseline route 'old'"):
        evaluate(rows, baseline="old", candidate="economic")
    with pytest.raises(AdoptionError, match="must differ"):
        evaluate(rows, baseline="legacy", candidate="legacy")
    with pytest.raises(AdoptionError, match="candidate route 'ghost'"):
        evaluate(rows, candidate="ghost")


def test_bad_options_and_bad_records_are_rejected():
    with pytest.raises(AdoptionError, match="min_cases"):
        evaluate(corpus({"extraction": 2}), min_cases=0)
    with pytest.raises(AdoptionError, match="not be negative"):
        evaluate(corpus({"extraction": 2}), max_acceptance_drop_points=-1)
    with pytest.raises(AdoptionError, match="a number"):
        evaluate(corpus({"extraction": 2}), max_acceptance_drop_points="two")
    with pytest.raises(AdoptionError, match="no records"):
        evaluate([])
    with pytest.raises(AdoptionError, match="critical error cannot be accepted"):
        evaluate([dict(rec("a", "legacy"), critical_error=True)])


def test_markdown_and_json_reports_and_the_cli(tmp_path):
    rows = corpus({"extraction": 4, "classification": 4}, cand_rejected={"classification-00"})
    source = tmp_path / "records.jsonl"
    source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    script = ROOT / "tools" / "evals" / "routing" / "adoption.py"
    base = [sys.executable, str(script), str(source), "--candidate", "economic", "--min-cases", "4"]
    printed = subprocess.run(base, capture_output=True, text=True, check=False, cwd=tmp_path)
    assert printed.returncode == 0, printed.stderr
    verdict = json.loads(printed.stdout)
    assert verdict["overall"]["verdict"] == NOT_ADOPT                               # 4 cases: 25 points of acceptance lost
    assert verdict == evaluate(compare.load_records(source), candidate="economic", min_cases=4)
    out_md, out_json = tmp_path / "verdict.md", tmp_path / "verdict.json"
    for target in (out_md, out_json):
        done = subprocess.run([*base, "--output", str(target)], capture_output=True, text=True, check=False, cwd=tmp_path)
        assert done.returncode == 0, done.stderr
    assert json.loads(out_json.read_text(encoding="utf-8")) == verdict
    markdown = out_md.read_text(encoding="utf-8")
    assert "**Verdict: not adopt**" in markdown and "| classification | 4 | not adopt |" in markdown
    assert "acceptance_regression" in markdown and "it activates nothing" in markdown
    assert markdown == adoption.render_markdown(verdict)
    missing = subprocess.run([*base[:3], "--baseline", "nope"], capture_output=True, text=True, check=False, cwd=tmp_path)
    assert missing.returncode != 0 and "baseline route 'nope'" in missing.stderr
    stdout_md = subprocess.run([*base, "--format", "md"], capture_output=True, text=True, check=False, cwd=tmp_path)
    assert stdout_md.stdout == markdown


def test_the_comparator_report_and_every_record_format_feed_the_criterion(tmp_path):
    rows = corpus({"extraction": 3, "classification": 3})
    report = compare.summarize(rows)
    for name in ("report.json", "records.jsonl", "records.csv"):
        path = tmp_path / name
        compare.export_report(report, path)
        assert evaluate(compare.load_records(path), min_cases=3) == evaluate(rows, min_cases=3)
        assert adoption.evaluate_file(path, min_cases=3)["overall"]["verdict"] == ADOPT
    bad = tmp_path / "bad.txt"
    bad.write_text("x", encoding="utf-8")
    with pytest.raises(AdoptionError, match="extension"):
        adoption.evaluate_file(bad)


# --- a framework database with the two routes ---------------------------------------------------------------

SECRET = "confidential-launch-plan-7731"


def good() -> ContentOutput:
    return ContentOutput(title="T", hook="A hook long enough for contract validation",
                         body="A body long enough to validate the structured contract correctly.",
                         cta="CTA", hashtags=["#ok"], source_urls_used=[])


def weak() -> ContentOutput:
    """Valid for the contract, rejected by the step's own validation (a social post needs hashtags)."""
    return good().model_copy(update={"hashtags": []})


TOKENS = {"t1": (1200, 600), "t2": (1000, 500), "t3": (1000, 500)}   # the cheap model is wordier


class Fake(LLMAdapter):
    """Answers by model and by marker in the prompt: ``[hard]`` fails T1's validation, ``[unparsable]`` makes T1 return
    text that is not JSON (no usage reported), ``[impossible]`` fails every model but T3. No network, no key."""

    provider = "lab"

    def __init__(self):
        self.calls: list[str] = []

    def generate_structured(self, *, spec, prompt, output_schema, requires_web=False, effort=None):
        self.calls.append(spec.model)
        model = spec.model
        if model != "t3" and "[impossible]" in prompt:
            action = "semantic"
        elif model == "t1" and "[hard]" in prompt:
            action = "semantic"
        elif model == "t1" and "[unparsable]" in prompt:
            raise StructuredOutputError("The model returned text that is not the requested JSON")
        else:
            action = "ok"
        parsed = weak() if action == "semantic" else good()
        tokens_in, tokens_out = TOKENS[model]
        return LLMResponse(parsed, parsed.model_dump_json(), "lab", model, tokens_in=tokens_in, tokens_out=tokens_out,
                           latency_ms=10)

    def health(self):
        return {"provider": "lab", "available": True}


def priced(amount):
    """Fake rate card: input at ``amount`` per million tokens, output at twice that, valid for years."""
    def side(value):
        return {"amount": value, "denominator": 1_000_000, "unit": "token", "currency": "USD",
                "source": "synthetic rate card (test)", "provenance": "catalog_declared",
                "valid_from": "2025-01-01T00:00:00Z", "valid_until": "2035-01-01T00:00:00Z"}
    return {"input": side(amount), "output": side(amount * 2)}


def model(model_id, level, amount):
    return ModelSpec(
        id=model_id, provider="lab", model=model_id, tier="local", structured_output=True, web=False,
        capabilities=frozenset({"text_generation"}), roles=frozenset({"creator"}), permissions=frozenset(),
        permissions_required=frozenset(), allowed_data_classes=frozenset({"non_sensitive"}), level=level,
        classification="classified", pricing=priced(amount))


PRICES = {"t1": 1, "t2": 4, "t3": 15}                                  # USD per million input tokens
CALL_COST = {name: (TOKENS[name][0] * PRICES[name] + TOKENS[name][1] * PRICES[name] * 2) / 1_000_000 for name in PRICES}

ECONOMIC = {
    "version": 1, "enabled": True, "mode": "automatic",
    "levels": {"orchestration": {"min": "T3"}, "planning": {"min": "T3"}, "review": {"min": "T3"},
               "work": {"min": "T1", "max": "T1"}},
    "economic_selection": True, "required_capabilities": ["structured_output"], "required_roles": [],
    "required_permissions": [], "allowed_permissions": [], "allowed_data_classes": ["non_sensitive"],
    "execution_modes": ["auto", "local", "cloud"], "require_health": False, "max_retries_per_candidate": 0,
    "escalation": {"max_escalations_per_step": 1, "ceiling": "T3"}, "candidate_model_ids": ["t1", "t2", "t3"],
}


def build(tmp_path: Path):
    """A system with fake providers, models T1/T2/T3 (classified, priced) and a legacy binding to T3."""
    settings = Settings(data_dir=tmp_path / "data", db_path=tmp_path / "data" / "multiagent.db",
                        runs_dir=tmp_path / "data" / "runs", assets_dir=tmp_path / "data" / "assets",
                        worker_enabled=False, gemini_api_key="")
    fake = Fake()
    system = build_system(settings, dry_run=False, adapter_overrides={"lab": fake, "net": Fake()})
    directory = tmp_path / "workflows"
    directory.mkdir(parents=True, exist_ok=True)
    document = yaml.safe_load((Path(system.engine.workflows.workflows_dir) / "social_post.yaml").read_text(encoding="utf-8"))
    document["steps"][1].update({"preferred_model": "t3", "fallback_model": None, "checkpoint_after": True})
    (directory / "social_post.yaml").write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    system.engine.workflows.workflows_dir = directory
    system.catalog.models.update({name: model(name, f"T{index}", PRICES[name]) for index, name in enumerate(PRICES, 1)})
    system.catalog.routing_profiles["economic"] = dict(ECONOMIC)
    system.fake = fake
    return system


def run_case(system, route, topic):
    """Run one case under ``route`` ("legacy" = the workflow's own binding, else the profile id); return its id."""
    assignments = system.catalog.routing_assignments
    if route == export_runs.LEGACY_ROUTE:
        assignments.pop("social_post", None)
    else:
        assignments["social_post"] = {"create": route}
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="eco-t07", objective=f"Write about {SECRET}", topic=topic))
    system.worker.process_once()
    return run["id"]


def finish(system, run_id):
    """The human reviewer approves the draft waiting at the checkpoint (if the run got that far)."""
    if system.run_service.get(run_id)["status"] == RunStatus.WAITING_HUMAN.value:
        system.run_service.approve(run_id)


def label(run_id, case, route, cat, **extra):
    return {"run_id": run_id, "case_id": case, "route": route, "category": cat, "input_version": "v1",
            "rubric_version": "r1", "rubric_hash": "rub-1", "critical_error": False, **extra}


CASES = {  # category -> (cases, indexes that need the escalation)
    "extraction": (6, {2, 5}),
    "classification": (6, {2}),
}


class Measurement:
    """The synthetic corpus run under both routes in one database."""

    def __init__(self, tmp_path: Path):
        self.system = build(tmp_path)
        self.db = self.system.settings.db_path
        self.labels: list[dict] = []
        for cat, (count, hard) in CASES.items():
            for index in range(count):
                case = f"{cat}-{index}"
                topic = f"{SECRET} {case}" + (" [hard]" if index in hard else "")
                for route in (export_runs.LEGACY_ROUTE, "economic"):
                    run_id = run_case(self.system, route, topic)
                    finish(self.system, run_id)
                    self.labels.append(label(run_id, case, route, cat))
        # Edge cases in categories of their own: an unparsable T1 answer (no usage, so an unknown cost) and a case
        # nobody below T3 can answer (the economic run fails at its escalation limit).
        for cat, marker in (("unpriced", "[unparsable]"), ("failing", "[impossible]")):
            for route in (export_runs.LEGACY_ROUTE, "economic"):
                run_id = run_case(self.system, route, f"{SECRET} {cat}-0 {marker}")
                finish(self.system, run_id)
                self.labels.append(label(run_id, f"{cat}-0", route, cat))
        self.records = export_runs.export_records(self.db, self.labels)

    def only(self, *categories):
        return [row for row in self.records if row["category"] in categories]


@pytest.fixture(scope="module")
def measurement(tmp_path_factory):
    return Measurement(tmp_path_factory.mktemp("measurement"))


def by_case(records, route):
    return {row["case_id"]: row for row in records if row["route"] == route}


def test_the_synthetic_runs_behave_as_designed(measurement):
    system = measurement.system
    statuses = {}
    for item in measurement.labels:
        statuses[(item["case_id"], item["route"])] = system.run_service.get(item["run_id"])["status"]
    assert all(statuses[(f"extraction-{i}", route)] == RunStatus.COMPLETED.value
               for i in range(6) for route in ("legacy", "economic"))
    assert statuses[("failing-0", "legacy")] == RunStatus.COMPLETED.value
    assert statuses[("failing-0", "economic")] == RunStatus.FAILED.value            # escalation limit reached
    assert statuses[("unpriced-0", "economic")] == RunStatus.COMPLETED.value


def test_the_export_has_one_validated_record_per_run_in_the_comparator_format(measurement):
    records = measurement.records
    assert len(records) == len(measurement.labels) == 2 * (6 + 6 + 1 + 1)
    assert records == compare.summarize(records)["records"]                         # already normalized and valid
    assert {row["route"] for row in records} == {"legacy", "economic"}
    expected_keys = set(compare._TEXT_FIELDS) | set(compare._BOOL_FIELDS) | set(compare._NULLABLE_NUMBERS)
    assert all(set(row) == expected_keys for row in records)
    sample = by_case(records, "legacy")["extraction-0"]
    assert (sample["input_version"], sample["rubric_version"], sample["rubric_hash"], sample["surface"]) == (
        "v1", "r1", "rub-1", "framework-run")
    assert len(sample["input_hash"]) == len(sample["config_hash"]) == 64
    assert sample["accepted"] is True and sample["critical_error"] is False
    # Both routes ran the same request under the same configuration, so every case pairs.
    assert by_case(records, "legacy")["extraction-0"]["input_hash"] == by_case(records, "economic")["extraction-0"]["input_hash"]
    assert by_case(records, "legacy")["extraction-0"]["config_hash"] == by_case(records, "economic")["extraction-0"]["config_hash"]
    comparison = compare.summarize(records)["paired_comparisons"][0]
    assert comparison["comparable_cases"] == 14                                      # every case pairs; only the unpriced one has unknowns
    assert comparison["excluded_cases"] == {"cost_unknown": 1, "input_tokens_unknown": 1, "output_tokens_unknown": 1}


def test_legacy_runs_call_t3_once_and_economic_runs_escalate_only_the_hard_cases(measurement):
    legacy, economic = by_case(measurement.records, "legacy"), by_case(measurement.records, "economic")
    easy, hard = economic["extraction-0"], economic["extraction-2"]
    assert (legacy["extraction-0"]["calls"], legacy["extraction-0"]["escalations"], legacy["extraction-0"]["effective"]) == (1, 0, "t3")
    assert legacy["extraction-0"]["cost"] == pytest.approx(CALL_COST["t3"]) and legacy["extraction-0"]["cost_unit"] == "USD"
    assert legacy["extraction-0"]["input_tokens"] == 1000 and legacy["extraction-0"]["output_tokens"] == 500
    assert (easy["calls"], easy["escalations"], easy["effective"], easy["requested"]) == (1, 0, "t1", "t1")
    assert easy["cost"] == pytest.approx(CALL_COST["t1"]) and easy["input_tokens"] == 1200 and easy["output_tokens"] == 600
    assert (hard["calls"], hard["escalations"], hard["effective"], hard["requested"]) == (2, 1, "t1,t2", "t1,t2")
    assert hard["cost"] == pytest.approx(CALL_COST["t1"] + CALL_COST["t2"])          # the failed T1 call is part of the cost
    assert hard["input_tokens"] == 2200 and hard["accepted"] is True and hard["rework"] == 0
    assert hard["duration_ms"] == 20 and hard["cost_unit"] == "USD"


def test_unknown_usage_stays_unknown_and_a_failed_run_keeps_what_it_spent(measurement):
    economic = by_case(measurement.records, "economic")
    unpriced = economic["unpriced-0"]
    assert unpriced["accepted"] is True and unpriced["calls"] == 2 and unpriced["escalations"] == 1
    assert unpriced["cost"] is None and unpriced["cost_unit"] is None            # the unparsable call reported no usage
    assert unpriced["input_tokens"] is None and unpriced["output_tokens"] is None
    failed = economic["failing-0"]
    assert failed["accepted"] is False and failed["critical_error"] is False and failed["calls"] == 2
    assert failed["cost"] == pytest.approx(CALL_COST["t1"] + CALL_COST["t2"]) and failed["escalations"] == 1


def test_the_adoption_verdict_of_the_synthetic_corpus(measurement):
    rows = measurement.only("extraction", "classification")
    verdict = evaluate(rows, min_cases=6)
    assert verdict["overall"]["verdict"] == ADOPT and verdict["paired_cases"] == 12
    group = category(verdict, "extraction")
    legacy_cost, escalated = CALL_COST["t3"], 2
    cand_total = 4 * CALL_COST["t1"] + escalated * (CALL_COST["t1"] + CALL_COST["t2"])
    assert group["baseline"]["cost_per_accepted"]["value"] == pytest.approx(legacy_cost)
    assert group["candidate"]["cost_per_accepted"]["value"] == pytest.approx(cand_total / 6)
    assert group["candidate"]["escalations"]["known_total"] == escalated and group["baseline"]["escalations"]["known_total"] == 0
    assert group["candidate"]["calls"]["known_total"] == 6 + escalated
    assert group["checks"]["acceptance"]["drop_points"] == 0.0
    # The same evidence is not enough under the real minimum of 20 cases per category.
    default = evaluate(rows)
    assert default["overall"]["verdict"] == INSUFFICIENT
    assert all("insufficient_cases" in codes(item) for item in default["categories"])


def test_the_edge_categories_decide_the_way_the_criterion_says(measurement):
    unpriced = evaluate(measurement.only("unpriced"), min_cases=1)
    assert unpriced["overall"]["verdict"] == INSUFFICIENT
    assert category(unpriced, "unpriced")["checks"]["cost"]["reason"] == "cost_unknown"
    assert codes(category(unpriced, "unpriced")) == {"cost_unknown"}
    short = evaluate(measurement.only("unpriced"), min_cases=2)                       # a short group still shows what it knows
    assert codes(category(short, "unpriced")) == {"insufficient_cases", "cost_unknown"}
    failing = evaluate(measurement.only("failing"), min_cases=1)
    assert failing["overall"]["verdict"] == NOT_ADOPT
    assert "acceptance_regression" in codes(category(failing, "failing"))
    everything = evaluate(measurement.records, min_cases=1)
    assert everything["overall"]["verdict"] == NOT_ADOPT                            # one failing category is enough


def test_an_evaluator_who_rejects_candidate_outputs_turns_the_verdict(measurement):
    rejected = {"extraction-0", "extraction-1", "extraction-3"}
    labels = [dict(item, accepted=False) if item["route"] == "economic" and item["case_id"] in rejected else item
              for item in measurement.labels if item["category"] in ("extraction", "classification")]
    rows = export_runs.export_records(measurement.db, labels)
    assert by_case(rows, "economic")["extraction-0"]["accepted"] is False
    assert by_case(rows, "legacy")["extraction-0"]["accepted"] is True
    verdict = evaluate(rows, min_cases=6)
    assert category(verdict, "extraction")["verdict"] == NOT_ADOPT and verdict["overall"]["verdict"] == NOT_ADOPT
    assert category(verdict, "classification")["verdict"] == ADOPT
    critical = [dict(item, critical_error=True) if item["route"] == "economic" and item["case_id"] == "classification-4" else item
                for item in measurement.labels if item["category"] in ("extraction", "classification")]
    flagged = by_case(export_runs.export_records(measurement.db, critical), "economic")["classification-4"]
    assert flagged["critical_error"] is True and flagged["accepted"] is False        # a critical error is never accepted
    assert category(evaluate(export_runs.export_records(measurement.db, critical), min_cases=6),
                    "classification")["verdict"] == NOT_ADOPT


def test_the_export_is_deterministic_and_reads_without_writing(measurement):
    def snapshot():
        with sqlite3.connect(measurement.db) as conn:
            return [conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("runs", "run_events", "telemetry")]
    before = snapshot()
    again = export_runs.export_records(measurement.db, list(reversed(measurement.labels)))
    assert again == measurement.records and snapshot() == before
    with export_runs._read_only(measurement.db) as conn, pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM telemetry")


def test_the_export_carries_no_prompt_request_or_output_content(measurement, tmp_path):
    corpus_row = {"id": "RUT-X", "solver_input": f"Extract the code from {SECRET}", "category": "extraction"}
    first = measurement.labels[0]
    polluted = [dict(first, solver_input=corpus_row["solver_input"], prompt="TOP-SECRET-PROMPT", note=SECRET)]
    out = tmp_path / "records.jsonl"
    compare.export_report({"records": export_runs.export_records(measurement.db, polluted)}, out)
    text = out.read_text(encoding="utf-8") + json.dumps(measurement.records)
    for forbidden in (SECRET, "TOP-SECRET-PROMPT", "Write about", "hook long enough", "structured contract", "Create an educational"):
        assert forbidden not in text
    assert set(json.loads(text.splitlines()[0])) <= set(compare._TEXT_FIELDS) | set(compare._BOOL_FIELDS) | set(compare._NULLABLE_NUMBERS)
    with pytest.raises(export_runs.ExportError, match="identifiers, not content"):
        export_runs.export_records(measurement.db, [dict(first, case_id=SECRET * 10)])


def test_the_route_label_must_match_the_routing_the_run_used(measurement):
    legacy = next(item for item in measurement.labels if item["route"] == "legacy")
    economic = next(item for item in measurement.labels if item["route"] == "economic")
    with pytest.raises(export_runs.ExportError, match="labelled route 'economic' but .* none"):
        export_runs.export_records(measurement.db, [dict(legacy, route="economic")])
    with pytest.raises(export_runs.ExportError, match="labelled route 'legacy' but .* economic"):
        export_runs.export_records(measurement.db, [dict(economic, route="legacy")])
    with pytest.raises(export_runs.ExportError, match="labelled route 'standard'"):
        export_runs.export_records(measurement.db, [dict(economic, route="standard")])
    relabelled = export_runs.export_records(measurement.db, [dict(economic, route="standard")], check_route=False)
    assert relabelled[0]["route"] == "standard"


def test_labels_and_runs_that_cannot_be_exported_are_refused(measurement, tmp_path):
    system = measurement.system
    first = measurement.labels[0]
    queued = system.run_service.create_social_post(SocialPostRequest(project_name="eco-t07", objective="Not processed yet", topic="queued"))
    with pytest.raises(export_runs.ExportError, match="queued: only finished runs"):
        export_runs.export_records(measurement.db, [label(queued["id"], "q", "legacy", "x")])
    with pytest.raises(export_runs.ExportError, match="run not found"):
        export_runs.export_records(measurement.db, [label("no-such-run", "q", "legacy", "x")])
    with pytest.raises(export_runs.ExportError, match="more than one label"):
        export_runs.export_records(measurement.db, [first, dict(first, case_id="again")])
    failed = next(item for item in measurement.labels if item["case_id"] == "failing-0" and item["route"] == "economic")
    with pytest.raises(export_runs.ExportError, match="did not complete .* cannot be labelled accepted"):
        export_runs.export_records(measurement.db, [dict(failed, accepted=True)])
    with pytest.raises(export_runs.ExportError, match="critical error cannot be accepted"):
        export_runs.export_records(measurement.db, [dict(first, critical_error=True, accepted=True)])
    bare = {key: value for key, value in first.items() if key != "critical_error"}
    with pytest.raises(export_runs.ExportError, match="no critical_error adjudication"):
        export_runs.export_records(measurement.db, [bare])
    assumed = export_runs.export_records(measurement.db, [bare], assume_no_critical_errors=True)
    assert assumed[0]["critical_error"] is False
    for broken, message in ((dict(first, run_id=""), "missing required field run_id"),
                            (dict(first, rubric_hash=None), "missing required field rubric_hash"),
                            (dict(first, critical_error="no"), "critical_error must be true or false"),
                            ("not an object", "must be an object")):
        with pytest.raises(export_runs.ExportError, match=message):
            export_runs.export_records(measurement.db, [broken])
    with pytest.raises(export_runs.ExportError, match="database not found"):
        export_runs.export_records(tmp_path / "missing.db", [first])
    with pytest.raises(export_runs.ExportError, match="duplicate case_id/route"):
        export_runs.export_records(measurement.db, [first, dict(first, run_id=next(
            item["run_id"] for item in measurement.labels if item["route"] == "legacy" and item["run_id"] != first["run_id"]))])


def test_a_lost_telemetry_row_makes_usage_unknown_not_zero(measurement):
    target = next(item for item in measurement.labels if item["case_id"] == "extraction-0" and item["route"] == "economic")
    system = measurement.system
    system.store.db.log_event(target["run_id"], "telemetry_failed", {"step_id": "create", "attempt": 1, "error": "disk"})
    try:
        row = by_case(export_runs.export_records(measurement.db, [target]), "economic")["extraction-0"]
    finally:
        with system.db.connect() as conn:
            conn.execute("DELETE FROM run_events WHERE run_id=? AND event_type='telemetry_failed'", (target["run_id"],))
            conn.commit()
    assert row["calls"] is None and row["cost"] is None and row["input_tokens"] is None and row["duration_ms"] is None
    assert row["accepted"] is True


def test_labels_load_from_json_and_jsonl(tmp_path):
    items = [label("r1", "c1", "legacy", "x"), label("r2", "c1", "economic", "x")]
    as_json, as_object, as_jsonl = tmp_path / "a.json", tmp_path / "b.json", tmp_path / "c.jsonl"
    as_json.write_text(json.dumps(items), encoding="utf-8")
    as_object.write_text(json.dumps({"runs": items}), encoding="utf-8")
    as_jsonl.write_text("".join(json.dumps(item) + "\n" for item in items), encoding="utf-8")
    assert export_runs.load_labels(as_json) == export_runs.load_labels(as_object) == export_runs.load_labels(as_jsonl)
    assert export_runs.load_labels(as_json)[0]["run_id"] == "r1"
    broken = tmp_path / "d.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(export_runs.ExportError, match="invalid JSON"):
        export_runs.load_labels(broken)
    broken.write_text('{"nothing": 1}', encoding="utf-8")
    with pytest.raises(export_runs.ExportError, match="JSON array"):
        export_runs.load_labels(broken)
    with pytest.raises(export_runs.ExportError, match="cannot read"):
        export_runs.load_labels(tmp_path / "gone.json")


def test_export_compare_and_adopt_from_the_command_line(measurement, tmp_path):
    labels = tmp_path / "labels.jsonl"
    labels.write_text("".join(json.dumps(item) + "\n" for item in measurement.labels if item["category"] in ("extraction", "classification")),
                      encoding="utf-8")
    tools = ROOT / "tools" / "evals" / "routing"
    records = tmp_path / "records.jsonl"
    exported = subprocess.run([sys.executable, str(tools / "export_runs.py"), "--db", str(measurement.db), "--labels", str(labels),
                               "--output", str(records)], capture_output=True, text=True, check=False, cwd=tmp_path)
    assert exported.returncode == 0, exported.stderr
    assert compare.load_records(records) == measurement.only("extraction", "classification")
    report = tmp_path / "report.json"
    compared = subprocess.run([sys.executable, str(tools / "compare.py"), str(records), "--output", str(report)],
                              capture_output=True, text=True, check=False, cwd=tmp_path)
    assert compared.returncode == 0, compared.stderr
    verdict = tmp_path / "verdict.md"
    judged = subprocess.run([sys.executable, str(tools / "adoption.py"), str(report), "--min-cases", "6", "--output", str(verdict),
                             "--category", "extraction", "--category", "classification"],
                            capture_output=True, text=True, check=False, cwd=tmp_path)
    assert judged.returncode == 0, judged.stderr
    text = verdict.read_text(encoding="utf-8")
    assert "**Verdict: adopt**" in text and "| extraction | 6 | adopt |" in text
    stdout = subprocess.run([sys.executable, str(tools / "export_runs.py"), "--db", str(measurement.db), "--labels", str(labels)],
                            capture_output=True, text=True, check=False, cwd=tmp_path)
    assert stdout.returncode == 0 and len(stdout.stdout.splitlines()) == 24
    refused = subprocess.run([sys.executable, str(tools / "export_runs.py"), "--db", str(measurement.db), "--labels", str(labels),
                              "--assume-no-critical-errors", "--db", str(tmp_path / "missing.db")],
                             capture_output=True, text=True, check=False, cwd=tmp_path)
    assert refused.returncode != 0 and "database not found" in refused.stderr
