# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""AVA-T01: the opt-in availability policy (contract ``AVA-T01-contrato.md``).

``multiagent/services/availability.py`` is pure, so every case is a hand-made mapping, settings object or list of
synthetic messages: no database, no clock, no process and no model.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from multiagent.config import Settings
from multiagent.services import availability
from multiagent.services.availability import (
    DEFAULT_MAX_WAIT_SECONDS,
    DEFAULT_PRIORITIES,
    DISABLED,
    FOREGROUND_RUNNING,
    AvailabilityPolicy,
    AvailabilityPolicyError,
    MessageKind,
    UserMessage,
    next_message,
    order_messages,
    policy_from_mapping,
    policy_from_settings,
)

ON = policy_from_mapping({"enabled": True})


# --- defaults and opt-in -------------------------------------------------------------------------------------------

def test_default_policy_is_off_with_a_30_second_wait():
    policy = AvailabilityPolicy()
    assert policy == DISABLED
    assert policy.enabled is False
    assert policy.max_wait_seconds == 30 == DEFAULT_MAX_WAIT_SECONDS
    assert policy.heartbeat_seconds == 60
    assert policy.priorities == ("cancel", "interrupt", "adjust", "note") == DEFAULT_PRIORITIES
    assert policy.orchestrator_threshold is None


@pytest.mark.parametrize("data", [None, {}])
def test_no_configuration_means_the_policy_is_off(data):
    assert policy_from_mapping(data) == DISABLED


def test_off_policy_keeps_the_current_synchronous_wait():
    assert DISABLED.foreground_wait(1800) is None
    assert DISABLED.foreground_wait(None) is None
    assert DISABLED.foreground_outcome(finished=False) == "finished"
    assert DISABLED.foreground_outcome(finished=True) == "finished"


def test_on_policy_waits_at_most_max_wait_seconds():
    assert ON.foreground_wait(1800) == 30
    assert ON.foreground_wait(None) == 30
    assert ON.foreground_wait(10) == 10  # never longer than the attempt's own limit
    assert ON.foreground_outcome(finished=False) == FOREGROUND_RUNNING == "running"
    assert ON.foreground_outcome(finished=True) == "finished"


def test_settings_without_availability_fields_give_the_off_policy():
    assert policy_from_settings(Settings(_env_file=None)) == DISABLED
    assert policy_from_settings(SimpleNamespace()) == DISABLED


def test_settings_with_availability_fields_are_read():
    settings = SimpleNamespace(availability_enabled=True, availability_max_wait_seconds=12,
                               availability_heartbeat_seconds=20,
                               availability_priorities=["cancel", "adjust", "interrupt", "note"],
                               availability_orchestrator_threshold=0.55)
    policy = policy_from_settings(settings)
    assert policy.enabled and policy.max_wait_seconds == 12 and policy.heartbeat_seconds == 20
    assert policy.priorities == ("cancel", "adjust", "interrupt", "note")
    assert policy.orchestrator_threshold == 0.55


def test_invalid_settings_value_is_reported():
    with pytest.raises(AvailabilityPolicyError, match="max_wait_seconds"):
        policy_from_settings(SimpleNamespace(availability_enabled=True, availability_max_wait_seconds=0))


# --- limits --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("value", [1, 1.5, 30, 120])
def test_max_wait_seconds_accepts_its_range(value):
    assert policy_from_mapping({"enabled": True, "max_wait_seconds": value}).max_wait_seconds == value


@pytest.mark.parametrize("value", [0, 0.99, -5, 120.01, 600, True, "30", None, float("nan"), float("inf")])
def test_max_wait_seconds_rejects_values_out_of_range_or_of_the_wrong_type(value):
    with pytest.raises(AvailabilityPolicyError, match="max_wait_seconds"):
        policy_from_mapping({"enabled": True, "max_wait_seconds": value})


def test_direct_construction_is_validated_too():
    with pytest.raises(AvailabilityPolicyError, match="max_wait_seconds"):
        AvailabilityPolicy(enabled=True, max_wait_seconds=500)
    with pytest.raises(AvailabilityPolicyError, match="max_wait_seconds"):
        AvailabilityPolicy(max_wait_seconds=0)  # an off policy is still a valid one


@pytest.mark.parametrize("value", [5, 60, 300])
def test_heartbeat_accepts_its_range(value):
    assert policy_from_mapping({"heartbeat_seconds": value}).heartbeat_seconds == value


@pytest.mark.parametrize("value", [4.9, 301, 900, 0, False, "60"])
def test_heartbeat_rejects_values_out_of_range(value):
    with pytest.raises(AvailabilityPolicyError, match="heartbeat_seconds"):
        policy_from_mapping({"heartbeat_seconds": value})


def test_heartbeat_stays_below_the_15_minute_stall():
    assert availability.MAX_HEARTBEAT_SECONDS < availability.STALL_SECONDS == 900


@pytest.mark.parametrize("value", ["yes", 1, None])
def test_enabled_must_be_a_boolean(value):
    with pytest.raises(AvailabilityPolicyError, match="enabled"):
        policy_from_mapping({"enabled": value})


def test_unknown_keys_are_rejected():
    with pytest.raises(AvailabilityPolicyError, match="max_wait"):
        policy_from_mapping({"enabled": True, "max_wait": 30})


def test_a_mapping_is_required():
    with pytest.raises(AvailabilityPolicyError):
        policy_from_mapping(["enabled"])  # type: ignore[arg-type]


def test_error_is_a_value_error():
    assert issubclass(AvailabilityPolicyError, ValueError)


# --- priorities ----------------------------------------------------------------------------------------------------

def test_default_priority_order():
    assert [ON.priority_rank(kind) for kind in ("cancel", "interrupt", "adjust", "note")] == [0, 1, 2, 3]
    assert ON.priority_rank(MessageKind.CANCEL) == 0


def test_priorities_can_be_reordered_after_cancel():
    policy = policy_from_mapping({"priorities": ["cancel", "adjust", "interrupt", "note"]})
    assert policy.priority_rank("adjust") < policy.priority_rank("interrupt")


@pytest.mark.parametrize("value", [
    ["interrupt", "cancel", "adjust", "note"],          # cancel must come first
    ["cancel", "interrupt", "adjust"],                  # a kind is missing
    ["cancel", "interrupt", "adjust", "note", "note"],  # repeated
    ["cancel", "interrupt", "adjust", "approve"],       # not a message kind: nothing approves
    "cancel,interrupt,adjust,note",
])
def test_invalid_priorities_are_rejected(value):
    with pytest.raises(AvailabilityPolicyError, match="priorities"):
        policy_from_mapping({"priorities": value})


def test_unknown_kind_has_no_rank():
    with pytest.raises(AvailabilityPolicyError, match="kind"):
        ON.priority_rank("approve")


def msg(message_id: str, kind: str, seq: int) -> UserMessage:
    return UserMessage(message_id=message_id, kind=MessageKind(kind), seq=seq)


def test_messages_are_ordered_by_priority_then_by_seq():
    messages = [msg("m1", "note", 1), msg("m2", "adjust", 2), msg("m3", "interrupt", 3),
                msg("m4", "adjust", 4), msg("m5", "cancel", 5)]
    assert [m.message_id for m in order_messages(messages, ON)] == ["m5", "m3", "m2", "m4", "m1"]
    assert next_message(messages, ON).message_id == "m5"


def test_a_late_cancel_still_goes_first():
    messages = [msg(f"n{i}", "note", i) for i in range(1, 50)] + [msg("stop", "cancel", 99)]
    assert next_message(messages, ON).message_id == "stop"


def test_repeated_message_id_is_kept_once_with_its_first_seq():
    messages = [msg("m1", "adjust", 7), msg("m1", "adjust", 3), msg("m2", "note", 1)]
    ordered = order_messages(messages, ON)
    assert [(m.message_id, m.seq) for m in ordered] == [("m1", 3), ("m2", 1)]


def test_empty_inbox_has_no_next_message():
    assert order_messages([], ON) == []
    assert next_message([], ON) is None


def test_custom_order_is_applied_to_messages():
    policy = policy_from_mapping({"priorities": ["cancel", "note", "adjust", "interrupt"]})
    messages = [msg("i", "interrupt", 1), msg("n", "note", 2)]
    assert next_message(messages, policy).message_id == "n"


def test_order_works_with_the_off_policy_too():
    messages = [msg("n", "note", 1), msg("c", "cancel", 2)]
    assert next_message(messages, DISABLED).message_id == "c"


@pytest.mark.parametrize("kwargs, field", [
    ({"message_id": "", "kind": MessageKind.NOTE, "seq": 1}, "message_id"),
    ({"message_id": "m", "kind": "note", "seq": 1}, "kind"),
    ({"message_id": "m", "kind": MessageKind.NOTE, "seq": -1}, "seq"),
    ({"message_id": "m", "kind": MessageKind.NOTE, "seq": True}, "seq"),
])
def test_invalid_messages_are_rejected(kwargs, field):
    with pytest.raises(AvailabilityPolicyError, match=field):
        UserMessage(**kwargs)


# --- reserved orchestrator threshold -------------------------------------------------------------------------------

@pytest.mark.parametrize("value", [0.5, 0.55, 0.6])
def test_reserved_threshold_accepts_50_to_60_percent(value):
    assert policy_from_mapping({"orchestrator_threshold": value}).orchestrator_threshold == value


@pytest.mark.parametrize("value", [0.49, 0.61, 0.7, 55, True, "0.55"])
def test_reserved_threshold_rejects_values_outside_50_to_60_percent(value):
    with pytest.raises(AvailabilityPolicyError, match="orchestrator_threshold"):
        policy_from_mapping({"orchestrator_threshold": value})


def test_reserved_threshold_changes_no_decision():
    plain = policy_from_mapping({"enabled": True})
    reserved = policy_from_mapping({"enabled": True, "orchestrator_threshold": 0.5})
    messages = [msg("a", "note", 1), msg("b", "interrupt", 2), msg("c", "cancel", 3)]
    for timeout in (None, 5, 30, 1800):
        assert plain.foreground_wait(timeout) == reserved.foreground_wait(timeout)
    for finished in (True, False):
        assert plain.foreground_outcome(finished) == reserved.foreground_outcome(finished)
    assert order_messages(messages, plain) == order_messages(messages, reserved)
    off_reserved = policy_from_mapping({"orchestrator_threshold": 0.6})
    assert off_reserved.foreground_wait(1800) is None


def test_reserved_threshold_is_not_the_task_handoff_threshold():
    """The task hand-off uses the CMP context profile only (audit decision 6)."""
    from multiagent.catalog import DEFAULT_CONTEXT_PROFILE

    assert DEFAULT_CONTEXT_PROFILE["threshold"] == 0.70
    assert not hasattr(AvailabilityPolicy(), "threshold")


# --- serialization and purity --------------------------------------------------------------------------------------

def test_policy_serializes_to_json_and_back():
    policy = policy_from_mapping({"enabled": True, "max_wait_seconds": 20, "heartbeat_seconds": 30,
                                  "priorities": ["cancel", "adjust", "interrupt", "note"],
                                  "orchestrator_threshold": 0.55})
    data = json.loads(json.dumps(policy.to_dict()))
    assert set(data) == {"enabled", "max_wait_seconds", "heartbeat_seconds", "priorities", "orchestrator_threshold"}
    assert policy_from_mapping(data) == policy
    assert policy_from_mapping(DISABLED.to_dict()) == DISABLED


def test_user_message_carries_no_text():
    fields = set(UserMessage.__dataclass_fields__)
    assert fields == {"message_id", "kind", "seq"}


def test_shared_event_names():
    assert availability.HEARTBEAT_EVENT == "executor.heartbeat"
    assert availability.ATTEMPT_FINISHED_EVENT == "executor.attempt_finished"
    assert availability.USER_MESSAGE_EVENT == "user.message"


def test_module_is_pure_and_domain_neutral():
    source = Path(availability.__file__).read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0] if node.level == 0 else "." + (node.module or ""))
    assert not imported & {"sqlite3", "subprocess", "time", "threading", "asyncio", "requests", "httpx"}
    assert not any(name.startswith(".") for name in imported)  # depends on nothing else in the framework
    lowered = source.lower()
    for word in ("claude", "codex", "copilot", "openai", "anthropic"):
        assert word not in lowered
