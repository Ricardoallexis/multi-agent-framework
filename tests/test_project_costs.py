# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""PRY-T07: tokens and API cost of one project, per stage and per task, summed from the stored telemetry rows.

Synthetic projects and rows in ``tmp_path`` only; every amount, model and id is invented.
"""
from __future__ import annotations

import pytest

from multiagent.run_request import RunRequest
from multiagent.services import ServiceError
from test_gui_project import auth, env, make_project  # noqa: F401  (env is a fixture)
from usage_fixtures import add_telemetry, priced

WHEN = "2026-11-10T08:00:00Z"
INV = "snt-INV-T01"
INV2 = "snt-INV-T02"
CON = "snt-CON-T01"


def run_for(env, project: str, task_id: str | None = None) -> str:
    request = RunRequest(workflow_id="w", project_name=project, task_id=task_id)
    return env.services.system.store.create_run(request, 4, 300)["id"]


def call(env, run_id: str, tokens_in, tokens_out, *, cost=None, provider: str = "gemini", model: str = "m-priced"):
    add_telemetry(env.services.system.store, run_id=run_id, when=WHEN, model=model, tokens_in=tokens_in,
                  tokens_out=tokens_out, cost=cost, provider=provider)


def stage_row(costs: dict, group_id: str) -> dict:
    return next(row for row in costs["stages"] if row["group_id"] == group_id)


def task_row(costs: dict, task_id: str) -> dict:
    return next(row for row in costs["tasks"] if row["task_id"] == task_id)


def test_sums_per_project_stage_and_task_and_adds_up(env):
    make_project(env, submitted=False)
    first, second, other = run_for(env, "snt", INV), run_for(env, "snt", INV2), run_for(env, "snt", CON)
    call(env, first, 1000, 500, cost=priced(1000, 500))
    call(env, first, 2000, 100, cost=priced(2000, 100))
    call(env, second, 10, 20, cost=priced(10, 20))
    call(env, other, 300, 200, cost=priced(300, 200))
    costs = env.services.usage.project_costs("snt")
    assert costs["project_id"] == "snt" and costs["cost_is_estimate"] is True
    assert [row["group_id"] for row in costs["stages"]] == ["snt-INV", "snt-CON"]
    inv, con = stage_row(costs, "snt-INV"), stage_row(costs, "snt-CON")
    assert (inv["tokens_in"], inv["tokens_out"], inv["tokens_total"], inv["records"]) == (3010, 620, 3630, 3)
    assert (con["tokens_total"], con["records"]) == (500, 1)
    assert task_row(costs, INV)["tokens_total"] == 3600 and task_row(costs, INV2)["tokens_total"] == 30
    assert task_row(costs, INV)["group_id"] == "snt-INV" and task_row(costs, INV)["title"] == "Estudio de audiencia"
    assert len(costs["tasks"]) == 3                                  # only the tasks that have calls
    assert costs["totals"]["tokens_total"] == 4130 and costs["totals"]["records"] == 4
    # The stages, the activities and the calls with no task add up to the project.
    parts = [*costs["stages"], costs["activities"], costs["unassigned"]]
    assert sum(p["tokens_total"] or 0 for p in parts) == costs["totals"]["tokens_total"]
    assert sum(p["estimated_cost_usd"] or 0 for p in parts) == pytest.approx(costs["totals"]["estimated_cost_usd"])
    assert costs["totals"]["cost_state"] == "complete" and costs["totals"]["tokens_partial"] is False
    assert costs["totals"]["evidence"]                                # the same price evidence «Uso» shows


def test_records_without_a_task_go_to_unassigned(env):
    make_project(env, submitted=False)
    call(env, run_for(env, "snt"), 40, 10, cost=priced(40, 10))                  # no task at all
    call(env, run_for(env, "snt", "snt-INV-T99"), 5, 5, cost=priced(5, 5))        # a task that is not in the project
    call(env, run_for(env, "snt", CON), 7, 3, cost=priced(7, 3))
    costs = env.services.usage.project_costs("snt")
    assert costs["unassigned"]["records"] == 2 and costs["unassigned"]["tokens_total"] == 60
    assert stage_row(costs, "snt-CON")["tokens_total"] == 10 and stage_row(costs, "snt-INV")["cost_state"] == "none"
    assert [row["task_id"] for row in costs["tasks"]] == [CON]
    assert costs["totals"]["tokens_total"] == 70


def test_the_task_of_the_telemetry_row_wins_over_the_one_of_the_run(env):
    make_project(env, submitted=False)
    run_id = run_for(env, "snt", INV)
    call(env, run_id, 10, 10)
    call(env, run_id, 20, 20)
    with env.services.system.store.db.connect() as conn:
        conn.execute("UPDATE telemetry SET task_id=? WHERE tokens_in=20", (CON,))
        conn.commit()
    costs = env.services.usage.project_costs("snt")
    assert stage_row(costs, "snt-INV")["tokens_total"] == 20 and stage_row(costs, "snt-CON")["tokens_total"] == 40


def test_calls_of_an_activity_are_counted_apart(env):
    make_project(env, submitted=False)
    activity = env.services.activities.start("snt", "Preparar fuentes", actor="user:operator")
    call(env, run_for(env, "snt", activity["id"]), 9, 1)
    call(env, run_for(env, "snt", INV), 4, 1)
    costs = env.services.usage.project_costs("snt")
    assert costs["activities"]["tokens_total"] == 10 and costs["unassigned"]["records"] == 0
    assert costs["totals"]["tokens_total"] == 15
    assert sum(p["tokens_total"] or 0 for p in [*costs["stages"], costs["activities"], costs["unassigned"]]) == 15


def test_a_project_without_records_has_no_numbers_not_zeros(env):
    make_project(env, submitted=False)
    costs = env.services.usage.project_costs("snt")
    totals = costs["totals"]
    assert totals["records"] == 0 and totals["tokens_total"] is None and totals["estimated_cost_usd"] is None
    assert totals["cost_state"] == "none" and costs["tasks"] == []
    assert [row["cost_state"] for row in costs["stages"]] == ["none", "none"]
    assert costs["unassigned"]["records"] == 0 and costs["activities"]["cost_state"] == "none"


def test_records_without_a_price_or_tokens_are_partial_never_zero(env):
    make_project(env, submitted=False)
    run_id = run_for(env, "snt", INV)
    call(env, run_id, 1000, 500, cost=priced(1000, 500))
    call(env, run_id, 100, 50, provider="ollama", model="local")                    # tokens but no price
    call(env, run_id, None, None, provider="human", model="")                        # no tokens, human step
    call(env, run_id, 70, None, model="m-partial")                                   # tokens in only
    totals = env.services.usage.project_costs("snt")["totals"]
    assert totals["cost_state"] == "partial" and totals["tokens_partial"] is True
    assert totals["cost_known_records"] == 1 and totals["cost_unknown_records"] == 3
    assert totals["tokens_total"] == 1650                                            # reported ones only
    assert totals["records_without_usage"] == 2
    assert totals["cost_unknown_reasons"] == {"human_step": 1, "no_price_evidence": 1, "tokens_not_reported": 1}
    only = env.services.usage.project_costs("snt")["stages"][0]
    assert only["estimated_cost_usd"] == pytest.approx(totals["estimated_cost_usd"])


def test_no_price_at_all_is_unknown_and_another_currency_is_not_converted(env):
    make_project(env, submitted=False)
    unpriced = run_for(env, "snt", INV)
    call(env, unpriced, 100, 50, provider="ollama", model="local")
    costs = env.services.usage.project_costs("snt")
    assert costs["totals"]["cost_state"] == "unknown" and costs["totals"]["estimated_cost_usd"] is None
    assert costs["totals"]["tokens_total"] == 150                                    # shown in tokens
    call(env, unpriced, 100, 100, cost=priced(100, 100, currency="EUR"))
    totals = env.services.usage.project_costs("snt")["totals"]
    assert totals["estimated_cost_usd"] is None and list(totals["cost_other_currencies"]) == ["EUR"]
    assert totals["cost_state"] == "partial"


def test_only_the_project_own_records_count(env):
    make_project(env, submitted=False)
    call(env, run_for(env, "snt", INV), 10, 10)
    call(env, run_for(env, "other-project"), 999, 1)
    assert env.services.usage.project_costs("snt")["totals"]["tokens_total"] == 20


def test_a_project_that_does_not_exist_or_cannot_be_seen_is_not_found(env):
    make_project(env, submitted=False)
    call(env, run_for(env, "plain-name"), 5, 5)                                      # a run created a project by name only
    with pytest.raises(ServiceError) as plain:
        env.services.usage.project_costs("plain-name")
    assert plain.value.code == "not_found"
    with pytest.raises(ServiceError) as missing:
        env.services.usage.project_costs("nope")
    assert missing.value.code == "not_found"
    with pytest.raises(ServiceError) as by_name:                                     # a name that is not an id
        env.services.usage.project_costs("Campaña Sintética")
    assert by_name.value.code == "not_found"


def test_project_costs_only_reads(env):
    make_project(env, submitted=False)
    call(env, run_for(env, "snt", INV), 10, 10)
    store = env.services.system.store

    def snapshot():
        """Row counts of the tables the method reads: a write to any of them would change one."""
        with store.db.connect() as conn:
            return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in ("telemetry", "runs", "projects", "tasks", "task_groups", "team_events")}

    before = snapshot()
    assert before["telemetry"] == 1
    env.services.usage.project_costs("snt")
    assert snapshot() == before


def test_calls_of_a_bundle_store_count_for_the_stage_and_the_task_they_belong_to(env, tmp_path):
    from multiagent.config import Settings
    from multiagent.db import Database
    from multiagent.db.store import Store

    make_project(env, submitted=False)
    call(env, run_for(env, "snt", INV), 100, 50, cost=priced(100, 50))               # the system store
    settings = Settings(_env_file=None, local_dir=tmp_path / "bundle-local")
    settings.ensure_directories()
    database = Database(settings)
    database.migrate()
    bundle_store = Store(database)
    # A run of a bundle lives in the bundle's store, whose own ``projects`` row has another id but the project's name.
    run_id = bundle_store.create_run(RunRequest(workflow_id="w", project_name="snt", task_id=CON), 4, 300)["id"]
    other = bundle_store.create_run(RunRequest(workflow_id="w", project_name="somebody-else", task_id=CON), 4, 300)["id"]
    add_telemetry(bundle_store, run_id=run_id, when=WHEN, model="m-priced", tokens_in=300, tokens_out=200, cost=priced(300, 200))
    add_telemetry(bundle_store, run_id=run_id, when=WHEN, model="m-priced", tokens_in=7, tokens_out=3, cost=None)
    add_telemetry(bundle_store, run_id=other, when=WHEN, model="m-priced", tokens_in=999, tokens_out=1)     # another project
    with bundle_store.db.connect() as conn:
        assert conn.execute("SELECT id FROM projects WHERE name='snt'").fetchone()["id"] != "snt"
    original = env.services.usage._stores
    env.services.usage._stores = lambda: [*original(), ("my_bundle", bundle_store)]
    costs = env.services.usage.project_costs("snt")
    assert stage_row(costs, "snt-CON")["tokens_total"] == 510 and stage_row(costs, "snt-CON")["records"] == 2
    assert stage_row(costs, "snt-INV")["tokens_total"] == 150
    assert task_row(costs, CON)["tokens_total"] == 510 and task_row(costs, INV)["tokens_total"] == 150
    assert costs["totals"]["tokens_total"] == 660 and costs["totals"]["records"] == 3
    parts = [*costs["stages"], costs["activities"], costs["unassigned"]]
    assert sum(p["tokens_total"] or 0 for p in parts) == costs["totals"]["tokens_total"]
    assert costs["unassigned"]["records"] == 0
