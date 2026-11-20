"""Synthetic, offline checks for the always-on model secret gate."""
from __future__ import annotations

from dataclasses import replace

import pytest

from cmp_harness import get, make_system
from multiagent.call_guards import CallContext, GuardEnvironment, Pause, build_call_guards
from multiagent.catalog import ModelSpec
from multiagent.contracts import SocialPostRequest
from multiagent.errors import InvalidStateTransition
from multiagent.secret_gate import SecretGuard, scan_text
from multiagent.services import ApplicationServices


SECRET = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"
STATE = {"llm_calls": 0, "max_llm_calls": 2, "events": []}


def _call(text: str, tier: str = "cloud") -> CallContext:
    spec = ModelSpec("synthetic", "lab", "synthetic", tier, True, False)
    return CallContext(model_id=spec.id, spec=spec, step_id="write", ordinal=1, outgoing_text=text)


@pytest.mark.parametrize("secret", [
    SECRET, "password=syntheticPassword123456", "-----BEGIN PRIVATE KEY-----\nsynthetic\n-----END PRIVATE KEY-----",
    "Bearer ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
])
def test_secret_types_pause_for_cloud_and_pass_for_local(secret):
    assert scan_text(secret)
    verdict = SecretGuard().check(_call(secret), STATE)
    assert isinstance(verdict, Pause)
    assert verdict.waiting_reason == "secret_review"
    assert secret not in repr(verdict.request)
    assert SecretGuard().check(_call(secret, "local"), STATE).__class__.__name__ == "Proceed"


def test_public_ids_and_clean_text_pass():
    for text in ("A plain request", "123e4567-e89b-12d3-a456-426614174000",
                 "a1" * 32, "blob:sha256:" + "a1" * 32, "sha256=" + "a1" * 32):
        assert not scan_text(text)
        assert SecretGuard().check(_call(text), STATE).__class__.__name__ == "Proceed"


def test_chain_runs_secret_before_egress_and_respects_redact_block():
    chain = build_call_guards(GuardEnvironment(egress={"action": "escalate"}))
    assert chain.names.index("secret") < chain.names.index("egress")
    assert chain.check(_call(SECRET), STATE).guard == "secret"
    assert chain.check(_call(SECRET), STATE).waiting_reason == "secret_review"
    assert chain.check(_call(SECRET), STATE, skip=()).guard == "secret"
    local_egress = build_call_guards(GuardEnvironment(egress={"action": "escalate", "include_local": True}))
    assert local_egress.check(_call(SECRET, "local"), STATE).guard == "egress"
    assert build_call_guards(GuardEnvironment(egress={"action": "block"})).check(_call(SECRET), STATE).guard == "egress"
    replaced = []
    redact_chain = build_call_guards(GuardEnvironment(egress={"action": "redact"}))
    assert redact_chain.check(_call(SECRET, "local"), STATE).__class__.__name__ == "Proceed"
    assert redact_chain.check(_call(SECRET).with_fields(replace_outgoing=replaced.append), STATE).__class__.__name__ == "Proceed"
    assert SECRET not in replaced[0]
    token = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
    replaced.clear()
    assert redact_chain.check(_call(token).with_fields(replace_outgoing=replaced.append), STATE).__class__.__name__ == "Proceed"
    assert token not in replaced[0]


def test_run_approval_releases_exactly_one_call_without_secret_events(tmp_path, caplog):
    system = make_system(tmp_path)
    system.catalog.models["local_default"] = replace(system.catalog.models["local_default"], tier="cloud")
    services = ApplicationServices(system)
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="synthetic", objective="Write a sample", topic=SECRET))
    system.worker.process_once()
    held = get(system, run["id"])
    assert held["waiting_reason"] == "secret_review"
    assert not system.ollama.prompts
    pending = [item for item in services.user_approvals.pending()
               if item["location"] == {"source": "run", "run_id": run["id"]}]
    assert len(pending) == 1
    approval = pending[0]
    assert approval["state"] == "pending"
    assert SECRET not in repr(approval)
    services.user_approvals.decide(approval["id"], "approve")
    assert services.user_approvals.status(approval["id"])["state"] == "consumed"
    system.worker.process_once()
    assert len(system.ollama.prompts) == 1          # the approval releases exactly one call
    system.worker.process_once()
    assert len(system.ollama.prompts) == 1          # and never a second one
    resumed = get(system, run["id"])
    assert "secret_released" in [event["event_type"] for event in resumed["events"]]
    assert SECRET not in repr(resumed["events"])
    with system.store.db.connect() as conn:
        team_events = [row["payload_json"] for row in conn.execute("SELECT payload_json FROM team_events")]
        run_events = [row["payload_json"] for row in conn.execute("SELECT payload_json FROM run_events")]
    assert SECRET not in repr(team_events + run_events)
    assert SECRET not in caplog.text


def test_rejected_run_fails_without_invoking_provider(tmp_path):
    system = make_system(tmp_path)
    system.catalog.models["local_default"] = replace(system.catalog.models["local_default"], tier="cloud")
    services = ApplicationServices(system)
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="synthetic", objective="Write a sample", topic=SECRET))
    system.worker.process_once()
    approval = services.user_approvals.pending()[0]
    services.user_approvals.decide(approval["id"], "reject")
    assert get(system, run["id"])["status"] == "failed"
    assert not system.ollama.prompts


def test_run_resolution_checks_the_approved_digest(tmp_path):
    system = make_system(tmp_path)
    system.catalog.models["local_default"] = replace(system.catalog.models["local_default"], tier="cloud")
    services = ApplicationServices(system)
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="synthetic", objective="Write a sample", topic=SECRET))
    system.worker.process_once()
    held = get(system, run["id"])
    request = next(event["payload"]["request"] for event in held["events"]
                   if event["event_type"] == "call_paused" and event["payload"].get("guard") == "secret")
    services.user_approvals.request(kind="secret_send", destination=request["destination"],
                                    location={"source": "run", "run_id": run["id"]},
                                    secret_types=["secret_key"], masked_sample="fake***",
                                    sha256="0" * 64, approval_id=request["approval_id"])
    with pytest.raises(InvalidStateTransition):
        services.user_approvals.decide(request["approval_id"], "approve")
    assert get(system, run["id"])["waiting_reason"] == "secret_review"
    assert not system.ollama.prompts
