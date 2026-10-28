"""BDI-T04: the policy reaches the bundle runs, and every door records who settled a content review.

Synthetic and offline (the throwaway bundle of the CMP tests registered as a bundle, scripted providers):

* ``RunServices`` builds the engine of a bundle runtime from the system engine's document policy (one source of
  truth: the screening of the context, the content guard and the gates of effects all read the same value), so a
  bundle run with the policy ``pause`` is screened and held like a system run;
* approving or rejecting a held run through the run API records ``channel`` ``api``; through the GUI router,
  ``gui``; ``by`` is what the caller declared (the channel when it declared nothing). Nothing authenticates it.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.document_trust import EVENT_REVIEW_REQUESTED, EVENT_REVIEW_RESOLVED, DetectionPolicy
from multiagent.gui.token import ensure_token
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices
from test_context_compaction import STEPS, TOPIC, WINDOW, Scripted, events, filler, write_bundle
from test_document_prompt_boundaries import ATTACK, MARK, hostile_note


def make_bundle_services(tmp_path, *, policy, runs=1):
    root = write_bundle(tmp_path / "bundle", window=WINDOW, assign=False)
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, gemini_api_key="",
                        openai_api_key="", openai_enabled=False, max_llm_calls=40, ollama_num_ctx=WINDOW,
                        gui_token_file=tmp_path / "gui-token")
    provider = Scripted(notes=[hostile_note("a")] * runs + [filler(step) for step in STEPS * 3])
    services = ApplicationServices.create(settings, bundles={"chain": root},
                                          adapter_overrides={"ollama": provider, "gemini": Scripted("gemini")})
    services.system.engine.document_policy = policy
    return services, provider


def start_and_process(services) -> str:
    run = services.runs.create(RunRequest(workflow_id="chain", project_name="Synthetic chain",
                                          inputs={"topic": TOPIC}), bundle="chain")
    assert services.runs._bundle_runtime("chain").worker.process_once()
    return run["id"]


@pytest.fixture
def held(tmp_path):
    services, provider = make_bundle_services(tmp_path, policy=DetectionPolicy.PAUSE, runs=2)
    yield SimpleNamespace(services=services, provider=provider, token=ensure_token(services.settings.gui_token_file))
    services.runs.close()


def test_a_bundle_run_gets_the_policy_of_the_system_engine_and_is_held_by_the_guard(held):
    services = held.services
    run_id = start_and_process(services)
    assert services.runs._bundle_runtime("chain").service.engine.document_policy is DetectionPolicy.PAUSE
    run = services.runs.get(run_id)
    assert (run["status"], run["waiting_reason"], run["waiting_step"]) == ("waiting_human", "content_review", "b")
    assert [p["part"] for p in events(run, EVENT_REVIEW_REQUESTED)] == ["a_output"]
    (paused,) = events(run, "call_paused")
    assert paused["guard"] == "untrusted_content" and paused["waiting_reason"] == "content_review"
    assert len(held.provider.of("note")) == 1                                   # step b never reached the model
    approved = services.runs.approve(run_id)
    assert approved["status"] == "queued"
    services.runs._bundle_runtime("chain").worker.process_once()
    prompt = held.provider.of("note")[1].prompt
    assert MARK in prompt and ATTACK not in prompt                              # the screened context, as in a system run


@pytest.mark.parametrize("policy", [None, DetectionPolicy.QUARANTINE_WARN])
def test_a_bundle_run_without_the_policy_pause_is_not_held(tmp_path, policy):
    services, provider = make_bundle_services(tmp_path, policy=policy)
    try:
        run_id = start_and_process(services)
        run = services.runs.get(run_id)
        assert run["status"] == "completed" and not events(run, "call_paused")
        assert not events(run, EVENT_REVIEW_REQUESTED)
        assert bool(events(run, "document_quarantined")) is (policy is not None)
        if policy is None:                                                       # the plain context, as always
            assert all(ATTACK in call.prompt for call in provider.of("note")[1:])
    finally:
        services.runs.close()


def resolutions(services, run_id):
    return events(services.runs.get(run_id), EVENT_REVIEW_RESOLVED)


def test_the_run_services_record_the_channel_and_who_as_declared(held):
    services = held.services
    first, second = start_and_process(services), start_and_process(services)
    services.runs.approve(first)                                                 # nothing declared
    assert [(p["by"], p["channel"]) for p in resolutions(services, first)] == [("api", "api")]
    services.runs.reject(second, "no", by="Ana", channel="gui")
    assert [(p["decision"], p["by"], p["channel"]) for p in resolutions(services, second)] == [
        ("rejected", "Ana", "gui")]


def test_the_api_records_the_api_and_the_gui_router_records_the_gui(held):
    services = held.services
    first, second = start_and_process(services), start_and_process(services)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        auth = {"Authorization": f"Bearer {held.token}"}
        assert client.post(f"/api/v1/runs/{first}/approve").status_code == 200
        assert client.post(f"/gui/api/runs/{second}/reject", json={"feedback": "no"}, headers=auth).status_code == 200
    assert [(p["decision"], p["by"], p["channel"]) for p in resolutions(services, first)] == [
        ("approved", "api", "api")]
    assert [(p["decision"], p["by"], p["channel"]) for p in resolutions(services, second)] == [
        ("rejected", "gui", "gui")]
