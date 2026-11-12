"""Synthetic, offline checks of the outbound boundary."""
from __future__ import annotations

from dataclasses import replace
import re
import pytest

import multiagent.egress_guard as egress_guard
from cmp_harness import make_system, get
from multiagent.adapters.fake import FakeAdapter
from multiagent.call_guards import CallContext, GuardEnvironment, Pause, Stop, build_call_guards
from multiagent.catalog import ModelSpec
from multiagent.contracts import ContentOutput, ModelTier, SocialPostRequest, TaskRequirements
from multiagent.model_router import ModelBinding, ModelRouter
from multiagent.run_service import PersonRequired
from multiagent.egress_guard import (
    EVENT_APPROVED, EVENT_RELEASED, EgressGuard, approval_from_pause, findings, redact,
)
from multiagent.workflows import WorkflowStep
from multiagent.workflow_validation import structural_issues


TEXT = "Write to person@example.test using sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890."


def call(text=TEXT, *, tier="cloud", ordinal=1, replace_outgoing=None):
    spec = ModelSpec("synthetic", "lab", "synthetic", tier, True, False)
    return CallContext(model_id=spec.id, spec=spec, run_id="run", step_id="write", attempt=1,
                       ordinal=ordinal, outgoing_text=text, replace_outgoing=replace_outgoing)


def test_detectors_and_stable_redaction_without_reverse_map():
    text = TEXT + " Another person@example.test; Bearer ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    matches = findings(text)
    result = redact(text, matches)
    assert "person@example.test" not in result
    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890" not in result
    assert "[REDACTED:email:1]" in result and "[REDACTED:email:2]" in result
    assert "[REDACTED:secret_key:1]" in result and "[REDACTED:bearer:1]" in result


@pytest.mark.parametrize("value", [
    "AIzaABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abc", "sk_live_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234",
    "xoxb-123456789012-abcdefghijklmnopqrstuvwxyz", "github_pat_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
    "eyJabcdefghijk.eyJ1234567890.abcdefghijk", "Authorization: Basic YWxhZGRpbjpvcGVuc2VzYW1l",
    "aws_secret_access_key=syntheticSecretValue123456", "password=syntheticPassword123456",
    "-----BEGIN ENCRYPTED PRIVATE KEY-----\nsynthetic\n", "-----BEGIN PGP PRIVATE KEY-----\nsynthetic\n",
    "+34 612 345 678", "612345678", "123-45-6789", "ABCD010101XYZ", "ABCD010101HDFRRL09",
    "12345678Z", "ES9121000418450200051332", "4111 1111 1111 1111",
])
def test_synthetic_detector_families(value):
    matches = findings(value)
    assert matches, value
    assert value not in redact(value, matches)


def test_overlapping_findings_cover_the_union():
    value = "-----BEGIN PRIVATE KEY-----\nsynthetic\n-----END PRIVATE KEY-----person@example.test"
    assert value not in redact(value, findings(value))
    assert "person@example.test" not in redact(value, findings(value))
    assert redact("abcdefgh", [(0, 5, "first"), (3, 8, "second")]) == "[REDACTED:first:1]"


def test_long_token_ignores_framework_references_and_public_identifiers():
    values = [
        "blob:sha256:" + "a1" * 32,
        "[resumido · blob:sha256:" + "a1" * 32 + "]",
        "a1" * 20,
        "1" * 64,
        "123e4567-e89b-12d3-a456-426614174000",
        "https://example.test/articles/a-public-article-slug-with-many-lowercase-words",
    ]
    for value in values:
        assert not findings(value, include_entropy=True), value
        assert EgressGuard({"action": "block"}).check(call(value), {"events": []}).__class__.__name__ == "Proceed"


@pytest.mark.parametrize("value", [
    "https://api.example.test/v1/AbcdEFGHijklMNOP1234567890qrstuvwxyz",
    "https://hooks.example.test/api/webhooks/T0AB/B0CD/AbcdEFGHijklMNOP1234567890qrstuvwxyz",
    "https://files.example.test/s/AbcdEFGHijklMNOP1234567890qrstuvwxyz",
])
def test_synthetic_long_token_in_url_path_blocks(value):
    assert any(kind == "long_token" for _, _, kind in findings(value, include_entropy=True))
    assert isinstance(EgressGuard({"action": "block"}).check(call(value), {"events": []}), Stop)


def test_excluded_hex_and_uuid_do_not_hide_other_detectors(monkeypatch):
    for value in ("12345678901234567890123456789012", "123e4567-e89b-12d3-a456-426614174000"):
        monkeypatch.setattr(egress_guard, "_PATTERNS", egress_guard._PATTERNS +
                            (("synthetic_detector", re.compile(re.escape(value))),))
        matches = findings(value, include_entropy=True)
        assert any(kind == "synthetic_detector" for _, _, kind in matches), value


@pytest.mark.parametrize("value", [
    "A1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6",
    "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6",
    "QmFzZTY0U3ludGhldGljVG9rZW4xMjM0NTY3ODkw",
])
def test_obvious_synthetic_long_secrets_still_block(value):
    assert any(kind == "long_token" for _, _, kind in findings(value, include_entropy=True))
    assert isinstance(EgressGuard({"action": "block"}).check(call(value), {"events": []}), Stop)


def test_unknown_action_and_missing_text_fail_closed():
    for action in ("blokc", False):
        with pytest.raises(ValueError):
            EgressGuard({"action": action})
    for action in ("block", "escalate"):
        assert isinstance(EgressGuard({"action": action}).check(call(None), {"events": []}), Stop)
    assert findings("A1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6", include_entropy=True)
    assert not findings("A1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6")
    changed = {"action": "block"}
    guard = EgressGuard(changed)
    changed["action"] = "allow"
    assert isinstance(guard.check(call(), {"events": []}), Stop)
    assert TEXT not in repr(call())


def test_each_action_and_local_scope():
    emitted = []
    outgoing = []
    state = {"events": []}
    assert EgressGuard({"action": "redact"}, emit=lambda k, p: emitted.append((k, p))).check(
        call(replace_outgoing=outgoing.append), state).__class__.__name__ == "Proceed"
    assert TEXT not in outgoing and "person@example.test" not in outgoing[0]
    assert "person@example.test" not in repr(emitted)
    assert isinstance(EgressGuard({"action": "block"}).check(call(), state), Stop)
    assert isinstance(EgressGuard({"action": "escalate"}).check(call(), state), Pause)
    assert EgressGuard({"action": "block"}).check(call(tier="local"), state).__class__.__name__ == "Proceed"
    assert isinstance(EgressGuard({"action": "block", "include_local": True}).check(call(tier="local"), state), Stop)


def test_approval_is_for_same_text_and_one_call_only():
    emitted = []
    guard = EgressGuard({"action": "escalate"}, emit=lambda k, p: emitted.append((k, p)))
    paused = guard.check(call(), {"events": []})
    assert isinstance(paused, Pause)
    approval = approval_from_pause({"step_id": "write", "attempt": 1, "model_id": "synthetic",
                                    "request": paused.request}, approval_id="synthetic-id", by="human", channel="cli")
    state = {"events": [{"event_type": EVENT_APPROVED, "payload": approval}]}
    assert guard.check(call(), state).__class__.__name__ == "Proceed"
    assert emitted[0][0] == EVENT_RELEASED
    with pytest.raises(Exception, match="Cannot record egress approval use"):
        EgressGuard({"action": "escalate"}).check(call(), state)
    assert isinstance(guard.check(call(TEXT + " changed"), state), Pause)
    assert isinstance(guard.check(call(ordinal=2), state), Pause)
    state["events"].append({"event_type": EVENT_RELEASED, "payload": emitted[0][1]})
    assert isinstance(guard.check(call(), state), Pause)


def test_without_egress_or_default_chain_has_no_egress_guard():
    assert "egress" not in build_call_guards(GuardEnvironment()).names


def test_step_policy_validation():
    base = WorkflowStep("write", "writer", "writer/base", 1, [], "text", "local_default", None,
                        False, None, False)
    assert not [i for i in structural_issues(type("Definition", (), {"id": "x", "max_iterations": 0,
                       "description": None, "inputs": [], "steps": [base]})()) if i.location.endswith("egress")]
    invalid = replace(base, egress={"action": "allow"})
    assert any(i.location.endswith("egress.action") for i in structural_issues(type("Definition", (), {"id": "x",
                       "max_iterations": 0, "description": None, "inputs": [], "steps": [invalid]})()))


def test_real_run_escalation_approval_and_rejection(tmp_path):
    system = make_system(tmp_path)
    system.settings.egress_default = "escalate"
    # The scripted provider is local in the fixture; classify it as cloud to exercise the catalog boundary.
    system.catalog.models["local_default"] = replace(system.catalog.models["local_default"], tier="cloud")
    def begin(topic):
        run = system.run_service.create_social_post(SocialPostRequest(
            project_name="synthetic", objective="Write a sample", topic=topic))
        system.worker.process_once()
        return run["id"]

    run_id = begin("person@example.test")
    run = get(system, run_id)
    assert run["waiting_reason"] == "egress_review"
    assert not system.ollama.prompts
    assert "person@example.test" not in repr([e for e in run["events"] if e["event_type"] == "call_paused"])
    try:
        system.run_service.approve(run_id, recorded_only=True)
    except PersonRequired:
        pass
    else:
        raise AssertionError("Unauthenticated approval must be refused")
    assert get(system, run_id)["waiting_reason"] == "egress_review"
    system.run_service.approve(run_id, by="tester", channel="cli")
    system.worker.process_once()
    assert system.ollama.prompts
    assert "egress_released" in [e["event_type"] for e in get(system, run_id)["events"]]

    second = begin("second@example.test")
    assert get(system, second)["waiting_reason"] == "egress_review"
    system.run_service.reject(second, "no", by="tester", channel="cli")
    assert get(system, second)["status"] == "failed"
    assert any(e["payload"].get("type") == "EgressBlocked" and
               e["payload"].get("code") == "egress_blocked" for e in get(system, second)["events"])


def test_real_run_redacts_before_provider_and_blocks_without_invoking(tmp_path):
    system = make_system(tmp_path)
    system.catalog.models["local_default"] = replace(system.catalog.models["local_default"], tier="cloud")

    def begin(topic):
        run = system.run_service.create_social_post(SocialPostRequest(
            project_name="synthetic", objective="Write a sample", topic=topic))
        system.worker.process_once()
        return get(system, run["id"])

    system.settings.egress_default = "redact"
    redacted = begin("person@example.test")
    assert "person@example.test" not in system.ollama.prompts[0]
    assert "[REDACTED:email:1]" in system.ollama.prompts[0]
    assert any(e["event_type"] == "egress_redacted" for e in redacted["events"])

    system.settings.egress_default = "block"
    before = len(system.ollama.prompts)
    blocked = begin("other@example.test")
    assert blocked["status"] == "failed"
    assert len(system.ollama.prompts) == before
    assert any(e["event_type"] == "egress_blocked" for e in blocked["events"])
    assert any(e["payload"].get("type") == "EgressBlocked" and
               e["payload"].get("code") == "egress_blocked" for e in blocked["events"])


def test_without_policy_real_run_keeps_original_text(tmp_path):
    system = make_system(tmp_path)
    system.catalog.models["local_default"] = replace(system.catalog.models["local_default"], tier="cloud")
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="synthetic", objective="Write a sample", topic="person@example.test"))
    system.worker.process_once()
    assert "person@example.test" in system.ollama.prompts[0]
    assert not any(e["event_type"].startswith("egress_") for e in get(system, run["id"])["events"])


def test_dry_run_uses_the_replaced_outgoing_prompt():
    class CaptureFake(FakeAdapter):
        sent = None

        def generate_structured(self, *, spec, prompt, output_schema, requires_web=False, effort=None):
            self.sent = prompt
            return super().generate_structured(spec=spec, prompt=prompt, output_schema=output_schema,
                                               requires_web=requires_web, effort=effort)

    fake = CaptureFake()
    router = ModelRouter(object(), {"fake": fake}, dry_run=True)
    router.execute(binding=ModelBinding("unused"),
                   requirements=TaskRequirements(requires_web=False, output_schema="ContentOutput", tier=ModelTier.CLOUD),
                   prompt="person@example.test", output_schema=ContentOutput,
                   before_call=lambda ctx: ctx.replace_outgoing("[REDACTED:email:1]"))
    assert fake.sent == "[REDACTED:email:1]"
