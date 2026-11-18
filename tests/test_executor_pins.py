# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""AXI-T07: pinned versions of the executor applications.

Everything is synthetic: versions are ``0.test``, digests are ``"0" * 64`` and ``"f" * 64``, files live in ``tmp_path``,
time is a value the test passes in, and no executor is ever started, asked for its version or looked up. The service
tests reuse the world of ``test_executor_services`` (fake programs in ``tmp_path``); the pin check refuses a launch before
the adapter is reached, so no program runs in the drift cases.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import socket
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from multiagent import executor_pins as pins
from multiagent.executor_pins import Evidence, PinGuard
from multiagent.executors import process
from multiagent.services import ServiceError
from multiagent.services import executors as executor_module
from test_executor_services import (  # noqa: F401  (the fixtures are used by name)
    NOW, event_types, make_world, never_a_real_cli, refused, world,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_executor_pins.py"
spec = importlib.util.spec_from_file_location("check_executor_pins", SCRIPT)
checker = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = checker
spec.loader.exec_module(checker)

SHA_A, SHA_B = "0" * 64, "f" * 64
COMMIT_A, COMMIT_B = "a" * 40, "b" * 40
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
DAY = timedelta(days=1)
HERMES = pins.HERMES_ID


def iso(moment: datetime) -> str:
    return moment.isoformat()


# --------------------------------------------------------------------------- builders

def pin_block(executor_id, *, version="0.test", sha=SHA_A, first=T0, taken=None, by="user", commit=COMMIT_A):
    block = {"version": version, "sha256": sha, "artifact_kind": "source_archive" if executor_id == HERMES else "package",
             "first_observed_at": iso(first), "accepted_at": iso(taken or first), "accepted_by": by}
    if executor_id == HERMES:
        block.update(tag="v0.test", commit=commit)
    return block


def candidate_block(executor_id, *, version="1.test", sha=SHA_B, first=T0, commit=COMMIT_B):
    block = pin_block(executor_id, version=version, sha=sha, first=first, commit=commit)
    del block["accepted_at"], block["accepted_by"]
    if executor_id == HERMES:
        block["tag"] = "v1.test"
    return block


def manifest_data(*, pinned=pins.EXECUTOR_IDS, candidates=None, **blocks):
    """A valid manifest as a mapping; ``blocks`` replace the ``pin`` of an executor, ``candidates`` add candidates."""
    executors = {}
    for executor_id in pins.EXECUTOR_IDS:
        executors[executor_id] = {
            "pin": blocks.get(executor_id, pin_block(executor_id)) if executor_id in pinned else None,
            "candidate": (candidates or {}).get(executor_id)}
    return {"version": 1, "executors": executors}


def observed_evidence(executor_id, *, version="0.test", sha=SHA_A, tag="v0.test", commit=COMMIT_A) -> Evidence:
    if executor_id == HERMES:
        return Evidence(version, sha, tag, commit)
    return Evidence(version, sha)


def observed_all(**changes) -> dict[str, Evidence]:
    return {executor_id: observed_evidence(executor_id, **changes.get(executor_id, {})) for executor_id in pins.EXECUTOR_IDS}


def write_yaml(path: Path, data) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def issues_of(data) -> list[str]:
    with pytest.raises(pins.PinManifestError) as caught:
        pins.parse_manifest(data)
    return [str(issue) for issue in caught.value.issues]


# =========================================================================== the manifest

def test_pin_manifest_accepts_the_five_executor_records():
    manifest = pins.parse_manifest(manifest_data())
    assert [record.executor_id for record in manifest.records] == [
        "claude_cli", "codex_cli", "copilot_cli", "antigravity_cli", "hermes_cli"]
    for record in manifest.records:
        assert record.pin.evidence.version == "0.test" and record.pin.evidence.sha256 == SHA_A
        assert record.pin.accepted_by == "user" and record.pin.accepted_at.tzinfo is not None and record.candidate is None
    assert manifest.get("claude_cli").pin.artifact_kind == "package"
    assert manifest.get(HERMES).pin.artifact_kind == "source_archive"
    assert manifest.get("claude_cli").pin.evidence.tag is None            # tag and commit are Hermes only


def test_a_manifest_written_and_read_back_is_the_same_manifest(tmp_path):
    manifest = pins.parse_manifest(manifest_data(candidates={"codex_cli": candidate_block("codex_cli")},
                                                 pinned=("claude_cli", HERMES)))
    path = tmp_path / "pins.yaml"
    path.write_text(pins.dump_manifest(manifest), encoding="utf-8")
    assert pins.load_manifest(path) == manifest


def test_the_shipped_manifest_is_valid_and_carries_no_versions_or_digests():
    path = pins.default_manifest_path()
    assert path == ROOT / "catalog" / "executor-pins.yaml"
    manifest = pins.load_manifest(path)
    assert [(record.executor_id, record.pin, record.candidate) for record in manifest.records] == [
        (executor_id, None, None) for executor_id in pins.EXECUTOR_IDS]
    assert not re.search(r"[0-9a-f]{40}", path.read_text(encoding="utf-8"))      # no digest and no commit
    # An executor with no pin is blocked, never matched.
    guard = PinGuard(lambda: pins.load_manifest(path), observed_all())
    assert {guard.check(executor_id, NOW).status for executor_id in pins.EXECUTOR_IDS} == {pins.UNPINNED}


@pytest.mark.parametrize("missing", ["tag", "commit", "sha256", "version"])
def test_hermes_pin_requires_tag_commit_and_hash(missing):
    block = pin_block(HERMES)
    del block[missing]
    assert issues_of(manifest_data(hermes_cli=block)) == [f"hermes_cli: pin.{missing}: is required"]


def test_only_hermes_carries_a_tag_and_a_commit_and_a_source_archive():
    block = pin_block("claude_cli")
    block.update(tag="v0.test", commit=COMMIT_A)
    assert issues_of(manifest_data(claude_cli=block)) == ["claude_cli: pin.tag: is not a known field here",
                                                          "claude_cli: pin.commit: is not a known field here"]
    assert issues_of(manifest_data(claude_cli={**pin_block("claude_cli"), "artifact_kind": "source_archive"})) == [
        "claude_cli: pin.artifact_kind: must be one of package, launcher"]
    assert issues_of(manifest_data(hermes_cli={**pin_block(HERMES), "artifact_kind": "package"})) == [
        "hermes_cli: pin.artifact_kind: must be one of source_archive"]


def test_unknown_executor_id_is_rejected():
    data = manifest_data()
    data["executors"]["unknown_cli"] = {"pin": None, "candidate": None}
    assert any("unknown_cli" in item and "not a pinned executor" in item for item in issues_of(data))
    # And what is outside the closed list is not pinned either.
    guard = PinGuard(pins.parse_manifest(manifest_data()), {"unknown_cli": Evidence("0.test", SHA_A)})
    assert guard.check("unknown_cli", NOW).status == pins.UNPINNED


def test_a_missing_record_or_pin_key_is_an_error_not_a_default():
    data = manifest_data()
    del data["executors"]["codex_cli"]
    assert issues_of(data) == ["codex_cli: has no record"]
    data = manifest_data()
    del data["executors"]["claude_cli"]["pin"]
    assert issues_of(data) == ["claude_cli: pin: is required (use null when there is none)"]


@pytest.mark.parametrize("field, value", [
    ("sha256", "not-a-digest"), ("sha256", "F" * 64), ("sha256", "0" * 63), ("sha256", 12), ("version", ""),
    ("version", 1.5), ("version", "has space"), ("artifact_kind", "binary"),
    ("first_observed_at", "2026-10-01"), ("first_observed_at", "2026-10-01T12:00:00"), ("accepted_at", None)])
def test_a_malformed_value_is_rejected_without_echoing_it(field, value):
    problems = issues_of(manifest_data(claude_cli={**pin_block("claude_cli"), field: value}))
    assert len(problems) == 1 and problems[0].startswith(f"claude_cli: pin.{field}: ")
    assert str(value) not in problems[0] or str(value) in ("", "None")


def test_an_unknown_field_and_a_wrong_manifest_version_are_rejected():
    data = manifest_data()
    data["executors"]["claude_cli"]["pin"]["extra"] = "x"
    data["colour"] = "red"
    data["version"] = 2
    assert issues_of(data) == ["colour: is not a known field", "version: must be 1",
                               "claude_cli: pin.extra: is not a known field here"]


def test_an_acceptance_outside_the_window_or_before_the_observation_is_rejected_in_the_file():
    assert issues_of(manifest_data(claude_cli=pin_block("claude_cli", taken=T0 + 7 * DAY))) == [
        "claude_cli: pin.accepted_at: is 7 days or more after first_observed_at: the acceptance window was over"]
    assert issues_of(manifest_data(claude_cli=pin_block("claude_cli", taken=T0 - timedelta(seconds=1)))) == [
        "claude_cli: pin.accepted_at: is before first_observed_at"]
    pins.parse_manifest(manifest_data(claude_cli=pin_block("claude_cli", taken=T0 + 7 * DAY - timedelta(seconds=1))))


@pytest.mark.parametrize("by", ["system:watcher", "executor:claude_cli", "Agent:claude", "", "two words"])
def test_a_pin_is_accepted_by_a_person_never_by_the_framework_or_an_agent(by):
    assert len(issues_of(manifest_data(claude_cli=pin_block("claude_cli", by=by)))) == 1


def test_a_candidate_equal_to_the_pin_is_rejected():
    same = candidate_block("claude_cli", version="0.test", sha=SHA_A)
    assert issues_of(manifest_data(candidates={"claude_cli": same})) == ["claude_cli: candidate: is the same as the pin"]


def test_yaml_with_a_repeated_key_or_an_unquoted_number_is_refused(tmp_path):
    text = yaml.safe_dump(manifest_data(), sort_keys=False)
    repeated = tmp_path / "repeated.yaml"
    repeated.write_text(text + "version: 1\n", encoding="utf-8")
    with pytest.raises(pins.PinManifestError, match="repeated"):
        pins.load_manifest(repeated)
    number = tmp_path / "number.yaml"
    number.write_text(text.replace("version: 0.test", "version: 1.5", 1), encoding="utf-8")
    with pytest.raises(pins.PinManifestError):
        pins.load_manifest(number)
    huge = tmp_path / "huge.yaml"
    huge.write_bytes(b"#" * (pins.MAX_FILE_BYTES + 1))
    with pytest.raises(pins.PinManifestError, match="larger than"):
        pins.load_manifest(huge)


def test_a_timestamp_written_as_yaml_with_a_zone_is_read_and_one_without_is_not(tmp_path):
    path = tmp_path / "pins.yaml"
    text = pins.dump_manifest(pins.parse_manifest(manifest_data()))
    path.write_text(text.replace("'2026-10-01T12:00:00Z'", "2026-10-01T12:00:00Z"), encoding="utf-8")
    assert pins.load_manifest(path).get("claude_cli").pin.first_observed_at == T0
    path.write_text(text.replace("'2026-10-01T12:00:00Z'", "2026-10-01 12:00:00"), encoding="utf-8")
    with pytest.raises(pins.PinManifestError):
        pins.load_manifest(path)


# =========================================================================== comparing

def record_of(executor_id="claude_cli", **kwargs) -> pins.PinRecord:
    return pins.parse_manifest(manifest_data(**kwargs)).get(executor_id)


@pytest.mark.parametrize("changes, changed", [
    ({"version": "0.other"}, ("version",)),
    ({"sha": SHA_B}, ("sha256",)),
    ({"version": "0.other", "sha": SHA_B}, ("version", "sha256")),
])
def test_a_different_version_or_digest_is_a_drift_and_names_what_differs(changes, changed):
    check = pins.compare("claude_cli", record_of(), observed_evidence("claude_cli", **changes), NOW)
    assert (check.status, check.changed, check.blocked) == (pins.DRIFT, changed, True)
    assert check.pinned == Evidence("0.test", SHA_A) and check.fingerprint and check.candidate == pins.CANDIDATE_NONE


@pytest.mark.parametrize("changes, changed", [({"tag": "v0.other"}, ("tag",)), ({"commit": COMMIT_B}, ("commit",)),
                                              ({"sha": SHA_B}, ("sha256",))])
def test_hermes_is_pinned_by_tag_commit_and_digest(changes, changed):
    check = pins.compare(HERMES, record_of(HERMES), observed_evidence(HERMES, **changes), NOW)
    assert (check.status, check.changed) == (pins.DRIFT, changed)
    assert pins.compare(HERMES, record_of(HERMES), observed_evidence(HERMES), NOW).status == pins.MATCHED


def test_an_exact_match_is_matched_and_not_blocked():
    check = pins.compare("claude_cli", record_of(), observed_evidence("claude_cli"), NOW)
    assert (check.status, check.changed, check.blocked, check.fingerprint) == (pins.MATCHED, (), False, "")


@pytest.mark.parametrize("observed", [None, Evidence(), Evidence("0.test"), Evidence(None, SHA_A),
                                      Evidence("0.test", "not-a-digest"), Evidence("0.test", SHA_B.upper()),
                                      Evidence(123, SHA_A)])
def test_missing_or_malformed_evidence_blocks_and_is_never_a_match(observed):
    check = pins.compare("claude_cli", record_of(), observed, NOW)
    assert (check.status, check.blocked) == (pins.MISSING_EVIDENCE, True)
    assert SHA_B.upper() not in check.reason and "not-a-digest" not in check.reason


def test_hermes_needs_its_tag_and_commit_as_evidence_too():
    assert pins.compare(HERMES, record_of(HERMES), Evidence("0.test", SHA_A), NOW).status == pins.MISSING_EVIDENCE


def test_extra_observed_values_that_do_not_identify_the_executor_are_ignored():
    check = pins.compare("claude_cli", record_of(), Evidence("0.test", SHA_A, "v9", COMMIT_B), NOW)
    assert check.status == pins.MATCHED


def test_no_record_or_no_pin_is_unpinned():
    assert pins.compare("claude_cli", record_of(pinned=()), observed_evidence("claude_cli"), NOW).status == pins.UNPINNED
    assert pins.compare("other_cli", None, observed_evidence("claude_cli"), NOW).status == pins.UNPINNED


def test_a_naive_moment_is_refused():
    with pytest.raises(ValueError):
        pins.compare("claude_cli", record_of(), observed_evidence("claude_cli"), datetime(2026, 10, 1))


def test_evaluate_goes_through_the_closed_list_in_order_and_can_be_limited():
    manifest = pins.parse_manifest(manifest_data())
    observed = observed_all(codex_cli={"sha": SHA_B})
    del observed["copilot_cli"]
    checks = pins.evaluate(manifest, observed, NOW)
    assert [(c.executor_id, c.status) for c in checks] == [
        ("claude_cli", pins.MATCHED), ("codex_cli", pins.DRIFT), ("copilot_cli", pins.MISSING_EVIDENCE),
        ("antigravity_cli", pins.MATCHED), (HERMES, pins.MATCHED)]
    assert [c.executor_id for c in pins.evaluate(manifest, observed, NOW, ("codex_cli",))] == ["codex_cli"]


# =========================================================================== the window

def with_candidate(**kwargs) -> pins.PinManifest:
    return pins.parse_manifest(manifest_data(candidates={"claude_cli": candidate_block("claude_cli", **kwargs)}))


def test_new_pin_acceptance_obeys_seven_day_window():
    manifest = with_candidate()
    candidate = manifest.get("claude_cli").candidate
    just_before = T0 + 7 * DAY - timedelta(microseconds=1)
    for moment in (T0, T0 + 6 * DAY, just_before):
        assert pins.window_state(T0, moment) == "open"
        accepted = pins.accept_candidate(manifest, "claude_cli", accepted_by="user", now=moment)
        record = accepted.get("claude_cli")
        assert record.candidate is None and record.pin.evidence == candidate.evidence
        assert record.pin.first_observed_at == T0 and record.pin.accepted_at == moment and record.pin.accepted_by == "user"
        assert accepted.get("codex_cli") == manifest.get("codex_cli")            # the others are untouched
        assert pins.parse_manifest(yaml.safe_load(pins.dump_manifest(accepted))) == accepted    # still a valid manifest
    # The 7 days exactly and later are over: the window is half open, which fails closed.
    for moment, code in ((T0 + 7 * DAY, "window_expired"), (T0 + 8 * DAY, "window_expired"),
                         (T0 - timedelta(seconds=1), "window_not_started")):
        with pytest.raises(pins.PinAcceptanceError) as caught:
            pins.accept_candidate(manifest, "claude_cli", accepted_by="user", now=moment)
        assert caught.value.code == code


def test_expired_acceptance_window_preserves_current_pin():
    manifest = with_candidate()
    before = manifest.get("claude_cli").pin
    late = T0 + 8 * DAY
    with pytest.raises(pins.PinAcceptanceError):
        pins.accept_candidate(manifest, "claude_cli", accepted_by="user", now=late)
    assert manifest.get("claude_cli").pin == before                       # the manifest value is immutable
    # What was observed is the candidate, its window is over: it stays blocked.
    check = pins.compare("claude_cli", manifest.get("claude_cli"), Evidence("1.test", SHA_B), late)
    assert (check.status, check.candidate, check.blocked) == (pins.DRIFT, "expired", True)
    # The old pin keeps matching the old build.
    assert pins.compare("claude_cli", manifest.get("claude_cli"), observed_evidence("claude_cli"), late).status == pins.MATCHED


def test_unaccepted_drift_keeps_the_old_pin_and_blocks():
    manifest = with_candidate()
    seen = Evidence("1.test", SHA_B)
    guard = PinGuard(manifest, {"claude_cli": seen})
    for moment in (T0, T0 + DAY):
        check = guard.check("claude_cli", moment)
        assert (check.status, check.candidate, check.blocked) == (pins.DRIFT, "open", True)
        assert check.pinned == Evidence("0.test", SHA_A)                 # the observation did not become the pin
    other = PinGuard(manifest, {"claude_cli": Evidence("2.test", SHA_B)}).check("claude_cli", T0)
    assert (other.status, other.candidate) == (pins.DRIFT, pins.CANDIDATE_OTHER)


def test_only_a_person_accepts_and_only_what_is_a_candidate():
    manifest = with_candidate()
    for who in ("system:watcher", "executor:claude_cli", "agent:claude", "", None):
        with pytest.raises(pins.PinAcceptanceError) as caught:
            pins.accept_candidate(manifest, "claude_cli", accepted_by=who, now=T0)
        assert caught.value.code == "invalid_acceptor"
    for executor_id, code in (("codex_cli", "no_candidate"), ("nope", "unknown_executor")):
        with pytest.raises(pins.PinAcceptanceError) as caught:
            pins.accept_candidate(manifest, executor_id, accepted_by="user", now=T0)
        assert caught.value.code == code


# =========================================================================== the event

def test_the_event_payload_has_the_values_and_nothing_else_and_fits_the_event_budget():
    check = pins.compare(HERMES, record_of(HERMES), observed_evidence(HERMES, version="9.test", sha=SHA_B, tag="v9",
                                                                      commit=COMMIT_B), NOW)
    payload = pins.event_payload(check, NOW)
    assert set(payload) == {"executor_id", "status", "changed", "pinned", "observed", "candidate", "fingerprint",
                            "observed_at", "decision"}
    assert payload["pinned"] == {"version": "0.test", "sha256": SHA_A, "tag": "v0.test", "commit": COMMIT_A}
    assert payload["observed"] == {"version": "9.test", "sha256": SHA_B, "tag": "v9", "commit": COMMIT_B}
    assert payload["changed"] == ["version", "sha256", "tag", "commit"] and payload["decision"] == "blocked"
    assert payload["observed_at"] == "2026-10-11T12:00:00Z"
    assert len(json.dumps(payload).encode()) < executor_module.EVENT_PAYLOAD_BUDGET
    with pytest.raises(ValueError):
        pins.event_payload(pins.compare(HERMES, record_of(HERMES), observed_evidence(HERMES), NOW), NOW)


def test_one_event_per_transition_not_per_observation():
    manifest = pins.parse_manifest(manifest_data())
    first = pins.compare("claude_cli", manifest.get("claude_cli"), Evidence("1.test", SHA_A), NOW)
    again = pins.compare("claude_cli", manifest.get("claude_cli"), Evidence("1.test", SHA_A), NOW + DAY)
    other = pins.compare("claude_cli", manifest.get("claude_cli"), Evidence("2.test", SHA_A), NOW)
    assert pins.should_record(None, first)
    assert not pins.should_record(pins.event_payload(first, NOW), again)       # the same observation: nothing new
    assert pins.should_record(pins.event_payload(first, NOW), other)           # a different one: a new event
    assert pins.should_record(pins.event_payload(other, NOW), first)           # and back to the first: a new transition
    assert not pins.should_record(None, pins.compare("claude_cli", manifest.get("claude_cli"), None, NOW))


# =========================================================================== the guard

def test_a_manifest_or_an_evidence_source_that_fails_blocks():
    def broken_manifest():
        raise OSError("gone")

    def broken_evidence(executor_id):
        raise RuntimeError("no metadata")

    manifest = pins.parse_manifest(manifest_data())
    assert PinGuard(broken_manifest, observed_all()).check("claude_cli", NOW).status == pins.MANIFEST_INVALID
    assert PinGuard(manifest, broken_evidence).check("claude_cli", NOW).status == pins.MISSING_EVIDENCE
    assert PinGuard(manifest, {}).check("claude_cli", NOW).status == pins.MISSING_EVIDENCE


def test_a_guard_built_from_files_reads_them_again_at_each_check(tmp_path):
    manifest_file = write_yaml(tmp_path / "pins.yaml", manifest_data())
    observed_file = tmp_path / "observed.yaml"
    guard = PinGuard.from_files(manifest_file, observed_file)
    assert guard.check("claude_cli", NOW).status == pins.MISSING_EVIDENCE            # no registry yet
    write_yaml(observed_file, {"version": 1, "executors": {"claude_cli": {"version": "0.test", "sha256": SHA_A}}})
    assert guard.check("claude_cli", NOW).status == pins.MATCHED
    write_yaml(observed_file, {"version": 1, "executors": {"claude_cli": {"version": "0.test", "sha256": SHA_B}}})
    assert guard.check("claude_cli", NOW).status == pins.DRIFT
    manifest_file.write_text("version: 1\nexecutors: {}\n", encoding="utf-8")
    assert guard.check("claude_cli", NOW).status == pins.MANIFEST_INVALID


def test_the_observed_registry_is_validated_like_the_manifest(tmp_path):
    good = {"version": 1, "executors": {HERMES: {"version": "0.test", "sha256": SHA_A, "tag": "v0.test", "commit": COMMIT_A}}}
    assert pins.parse_observed(good) == {HERMES: Evidence("0.test", SHA_A, "v0.test", COMMIT_A)}
    bad = {"version": 1, "executors": {"claude_cli": {"version": "0.test", "sha256": "nope", "path": "C:/x"},
                                       "other_cli": {}}}
    with pytest.raises(pins.PinManifestError) as caught:
        pins.parse_observed(bad)
    assert [str(i) for i in caught.value.issues] == [
        "claude_cli: path: is not a known field here",
        "claude_cli: sha256: must be quoted text: 64 lowercase hexadecimal characters",
        "other_cli: is not a pinned executor (known: claude_cli, codex_cli, copilot_cli, antigravity_cli, hermes_cli)"]


# =========================================================================== the service

def pinned_world(world, evidence=None, **blocks):
    """The world of the executor service tests with a guard whose manifest pins the four executors."""
    seen = {"claude_cli": observed_evidence("claude_cli")} if evidence is None else dict(evidence)
    world.services.executors.pin_guard = PinGuard(pins.parse_manifest(manifest_data(**blocks)), seen)
    return seen


def pin_events(services):
    """Every ``executor.version_changed`` of the team (the event is about the executor, so it has no task to filter by)."""
    return [e for e in services.tasks.events() if e["type"] == "executor.version_changed"]


def assert_no_task_data(event, *task_ids):
    """The whole stored record, outer fields included, says nothing about a task or a group."""
    assert event["task_id"] == "" and event["group_id"] == "" and event["ref"] != ""
    text = json.dumps(event)
    for word in (*task_ids, "ORQ"):
        assert json.dumps(word) not in text                               # as a whole value (T1 is inside a timestamp)


def no_program(monkeypatch):
    """Fails the test if the process boundary is reached at all."""
    def boom(*args, **kwargs):
        raise AssertionError("the pin check must refuse before any program is started")
    monkeypatch.setattr(process, "start", boom)
    monkeypatch.setattr(process, "run", boom)


def test_the_event_type_is_registered_and_the_check_is_off_by_default(world):
    assert "executor.version_changed" in executor_module.EVENT_TYPES
    assert world.services.executors.pin_guard is None
    assert world.services.executors.run_task("T1", "claude_cli")["outcome"] == "submitted"
    assert pin_events(world.services) == []


@pytest.mark.parametrize("changes, changed", [
    ({"version": "0.other"}, ["version"]), ({"sha": SHA_B}, ["sha256"]),
    ({"version": "0.other", "sha": SHA_B}, ["version", "sha256"])])
def test_version_or_hash_drift_is_marked_and_emits_event(world, monkeypatch, changes, changed):
    pinned_world(world, {"claude_cli": observed_evidence("claude_cli", **changes)})
    no_program(monkeypatch)
    services = world.services

    error = refused(lambda: services.executors.run_task("T1", "claude_cli"), "invalid_state", "executor_pin_blocked")

    assert error.details["status"] == "drift" and error.details["changed"] == changed
    assert error.details["executor_id"] == "claude_cli" and error.details["event_recorded"] is True
    (event,) = pin_events(services)
    assert event["ref"] == "claude_cli" and event["actor"] == "system:executor-pins"
    assert_no_task_data(event, "T1")                                      # not even task_id / group_id outside the payload
    assert error.details["task_id"] == "T1"                               # the refusal keeps the task; the event does not
    payload = event["payload"]
    assert payload["changed"] == changed and payload["decision"] == "blocked" and payload["status"] == "drift"
    assert payload["pinned"] == {"version": "0.test", "sha256": SHA_A}
    assert payload["observed"] == {"version": changes.get("version", "0.test"), "sha256": changes.get("sha", SHA_A)}
    assert payload["observed_at"] == "2026-10-11T12:00:00Z"
    assert str(world.root) not in json.dumps(event)                       # no local path anywhere in the record


def test_executor_pin_drift_blocks_before_adapter_call(world, monkeypatch):
    pinned_world(world, {"claude_cli": observed_evidence("claude_cli", sha=SHA_B)})
    no_program(monkeypatch)
    services = world.services
    before = services.tasks.get_task("T1")

    refused(lambda: services.executors.run_task("T1", "claude_cli"), "invalid_state", "executor_pin_blocked")

    assert world.fake.calls() == []                                       # the adapter was never reached
    task = services.tasks.get_task("T1")
    assert (task["status"], task["state"], task["revision"], task["attempt"]) == (
        before["status"], before["state"], before["revision"], before["attempt"]) and task["status"] == "ready"
    assert event_types(services, "T1") == ["task.created"]                # nothing was started or recorded for the task
    assert len(pin_events(services)) == 1                                 # the change is on the executor, not on the task
    assert not (services.settings.executor_workdir_root / "task-T1").exists()            # not even the work folder


def test_a_matching_pin_runs_and_emits_no_change_event(world):
    pinned_world(world)
    out = world.services.executors.run_task("T1", "claude_cli")
    assert out["outcome"] == "submitted" and len(world.fake.calls("claude")) == 1
    assert pin_events(world.services) == []


def test_matching_pin_does_not_emit_change_event_for_other_tasks_either(world):
    pinned_world(world)
    for task_id in ("T1", "T3"):
        assert world.services.executors.run_task(task_id, "claude_cli")["outcome"] == "submitted"
    assert [e for e in world.services.tasks.events() if e["type"] == "executor.version_changed"] == []


def test_change_event_is_idempotent_for_the_same_observation(world, monkeypatch):
    seen = pinned_world(world, {"claude_cli": observed_evidence("claude_cli", version="1.test")})
    no_program(monkeypatch)
    services = world.services
    for task_id in ("T1", "T1", "T3"):                                    # the same drift, seen three times
        refused(lambda: services.executors.run_task(task_id, "claude_cli"), "invalid_state", "executor_pin_blocked")
    events = pin_events(services)
    assert len(events) == 1 and events[0]["ref"] == "claude_cli"
    assert_no_task_data(events[0], "T1", "T3")                            # whichever task saw it first, the event has none

    seen["claude_cli"] = observed_evidence("claude_cli", version="2.test")        # the evidence changes again
    refused(lambda: services.executors.run_task("T3", "claude_cli"), "invalid_state", "executor_pin_blocked")
    assert [e["payload"]["observed"]["version"] for e in pin_events(services)] == ["1.test", "2.test"]

    seen["claude_cli"] = observed_evidence("claude_cli", version="1.test")        # back to the first one: A -> B -> A
    for task_id in ("T1", "T3"):
        refused(lambda: services.executors.run_task(task_id, "claude_cli"), "invalid_state", "executor_pin_blocked")
    events = pin_events(services)                                         # one event per transition, so a third one
    assert [e["payload"]["observed"]["version"] for e in events] == ["1.test", "2.test", "1.test"]
    for event in events:
        assert_no_task_data(event, "T1", "T3")


def test_two_launches_that_see_the_same_drift_at_once_record_one_event(world, monkeypatch):
    """The read of the latest event, the decision and the write are one write transaction (BEGIN IMMEDIATE).

    Deterministic: each launch (T1 and T3, each on its own connection) stops right after reading the latest event until
    the other one has read it too. Were the read outside the transaction both would read "no event" and both would
    write; inside it the second launch cannot even read until the first has committed, the rendezvous is never
    completed (it times out), and the second one then finds the first one's event.
    """
    pinned_world(world, {"claude_cli": observed_evidence("claude_cli", sha=SHA_B)})
    no_program(monkeypatch)
    services = world.services
    rendezvous, seen_by, connections = threading.Barrier(2, timeout=1.0), [], []
    original = executor_module.ExecutorServices._latest

    def latest_then_wait(conn, event_type, **kwargs):
        found = original(conn, event_type, **kwargs)
        if event_type == "executor.version_changed":
            connections.append(conn)
            seen_by.append(found)
            try:
                rendezvous.wait()
            except threading.BrokenBarrierError:
                pass
        return found
    monkeypatch.setattr(executor_module.ExecutorServices, "_latest", staticmethod(latest_then_wait))

    errors: list[ServiceError] = []

    def launch(task_id):
        try:
            services.executors.run_task(task_id, "claude_cli")
        except ServiceError as error:
            errors.append(error)
    threads = [threading.Thread(target=launch, args=(task_id,)) for task_id in ("T1", "T3")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads)

    assert len(errors) == 2 and all(e.code == "invalid_state" and e.details["reason"] == "executor_pin_blocked"
                                    for e in errors)                      # both launches were refused
    assert all(e.details["event_recorded"] is True for e in errors)       # and the answer is the same for both
    assert len(connections) == 2 and connections[0] is not connections[1]     # two reads, each on its own connection
    (event,) = pin_events(services)                                       # but the observation was recorded once
    assert event["payload"]["observed"]["sha256"] == SHA_B and event["ref"] == "claude_cli"
    assert_no_task_data(event, "T1", "T3")
    assert [item is None for item in seen_by].count(True) == 1            # only the first reader saw an empty history
    assert world.fake.calls() == []


def test_disabled_executor_stays_disabled_with_a_matching_pin(tmp_path, monkeypatch):
    world = make_world(tmp_path, tmp_path / "bin", enabled=("codex_cli", "copilot_cli"))       # claude_cli is disabled
    pinned_world(world)
    no_program(monkeypatch)
    assert world.services.executors.pin_guard.check("claude_cli", NOW).status == pins.MATCHED

    refused(lambda: world.services.executors.run_task("T1", "claude_cli"), "invalid_state", "executor_disabled")

    assert world.fake.calls() == [] and pin_events(world.services) == []
    assert world.services.tasks.get_task("T1")["status"] == "ready"


@pytest.mark.parametrize("evidence, status", [({}, "missing_evidence"), (None, "unpinned")])
def test_missing_evidence_and_an_executor_with_no_pin_block_without_an_event(world, monkeypatch, evidence, status):
    pinned_world(world, evidence, **({} if evidence is not None else {"pinned": ("codex_cli",)}))
    no_program(monkeypatch)
    error = refused(lambda: world.services.executors.run_task("T1", "claude_cli"), "invalid_state", "executor_pin_blocked")
    assert error.details["status"] == status and "event_recorded" not in error.details
    assert pin_events(world.services) == [] and world.fake.calls() == []


def test_a_manifest_that_cannot_be_read_blocks_the_launch(world, monkeypatch, tmp_path):
    world.services.executors.pin_guard = PinGuard.from_files(tmp_path / "missing.yaml", tmp_path / "observed.yaml")
    no_program(monkeypatch)
    error = refused(lambda: world.services.executors.run_task("T1", "claude_cli"), "invalid_state", "executor_pin_blocked")
    assert error.details["status"] == "manifest_invalid" and world.fake.calls() == []


def test_if_the_change_cannot_be_recorded_the_launch_is_still_refused(world, monkeypatch):
    pinned_world(world, {"claude_cli": observed_evidence("claude_cli", sha=SHA_B)})
    no_program(monkeypatch)
    services = world.services

    def failing_append(*args, **kwargs):
        raise RuntimeError("the store is unavailable")
    monkeypatch.setattr(services.executors._store, "append_event", failing_append)

    error = refused(lambda: services.executors.run_task("T1", "claude_cli"), "invalid_state", "executor_pin_blocked")

    assert error.details["event_recorded"] is False and world.fake.calls() == []
    assert services.tasks.get_task("T1")["status"] == "ready"


def test_a_candidate_waiting_for_a_person_does_not_let_the_launch_through(world, monkeypatch):
    candidates = {"claude_cli": candidate_block("claude_cli", first=NOW - DAY)}
    world.services.executors.pin_guard = PinGuard(
        pins.parse_manifest(manifest_data(candidates=candidates, claude_cli=pin_block("claude_cli", first=NOW - 30 * DAY))),
        {"claude_cli": Evidence("1.test", SHA_B)})
    no_program(monkeypatch)
    error = refused(lambda: world.services.executors.run_task("T1", "claude_cli"), "invalid_state", "executor_pin_blocked")
    assert error.details["candidate"] == "open" and world.fake.calls() == []
    (event,) = pin_events(world.services)
    assert event["payload"]["candidate"] == "open"


# =========================================================================== the checker script

def run_checker(capsys, *args):
    code = checker.main(list(args))
    out = capsys.readouterr().out
    return code, out


NOW_ARG = ("--now", iso(T0 + DAY))


def test_checker_passes_with_a_temporary_test_catalog(tmp_path, capsys):
    manifest = write_yaml(tmp_path / "pins.yaml", manifest_data())
    observed = write_yaml(tmp_path / "observed.yaml", {"version": 1, "executors": {
        executor_id: observed_evidence(executor_id).view(pins.evidence_fields(executor_id))
        for executor_id in pins.EXECUTOR_IDS}})
    code, out = run_checker(capsys, "--manifest", str(manifest), "--observed", str(observed), *NOW_ARG)
    assert code == 0 and "Executor pins OK." in out and out.count("matches its pin") == len(pins.EXECUTOR_IDS) == 5
    code, out = run_checker(capsys, "--manifest", str(manifest), "--require-pins", *NOW_ARG)
    assert code == 0 and "pinned (accepted by user)" in out


def test_checker_passes_on_the_shipped_manifest_until_pins_are_required(capsys):
    code, out = run_checker(capsys, *NOW_ARG)
    assert code == 0 and out.count("no pin has been accepted yet") == 5
    code, out = run_checker(capsys, "--require-pins", *NOW_ARG)
    assert code == 1 and out.count("ERROR: ") == 5


def test_checker_rejects_missing_pin_and_malformed_hash(tmp_path, capsys):
    data = manifest_data()
    data["executors"]["claude_cli"]["pin"]["sha256"] = "not-a-digest"
    del data["executors"]["codex_cli"]
    data["executors"]["copilot_cli"]["pin"]["extra"] = 1
    data["executors"]["unknown_cli"] = {"pin": None, "candidate": None}
    manifest = write_yaml(tmp_path / "pins.yaml", data)
    code, out = run_checker(capsys, "--manifest", str(manifest), *NOW_ARG)
    assert code == 1
    assert "ERROR: manifest: claude_cli: pin.sha256: must be quoted text: 64 lowercase hexadecimal characters" in out
    assert "ERROR: manifest: codex_cli: has no record" in out
    assert "ERROR: manifest: copilot_cli: pin.extra: is not a known field here" in out
    assert "ERROR: manifest: unknown_cli: is not a pinned executor" in out
    assert "not-a-digest" not in out                                     # the value is never echoed
    code, out = run_checker(capsys, "--manifest", str(tmp_path / "nope.yaml"))
    assert code == 1 and "manifest: cannot be read (FileNotFoundError)" in out
    assert str(tmp_path) not in out


def test_checker_reports_drift_missing_evidence_and_unpinned_executors(tmp_path, capsys):
    manifest = write_yaml(tmp_path / "pins.yaml", manifest_data(
        pinned=("claude_cli", "codex_cli", HERMES),
        candidates={"claude_cli": candidate_block("claude_cli", first=T0)}))
    observed = write_yaml(tmp_path / "observed.yaml", {"version": 1, "executors": {
        "claude_cli": {"version": "1.test", "sha256": SHA_B},
        "copilot_cli": {"version": "0.test", "sha256": SHA_A},
        HERMES: {"version": "0.test", "sha256": SHA_A, "tag": "v0.test", "commit": COMMIT_B}}})
    code, out = run_checker(capsys, "--manifest", str(manifest), "--observed", str(observed), *NOW_ARG)
    assert code == 1
    assert "ERROR: claude_cli: drift: differs from the pin in version, sha256; it is the candidate on record (window open)" in out
    assert "ERROR: codex_cli: missing_evidence: " in out
    assert "ERROR: copilot_cli: unpinned: " in out
    assert "ERROR: hermes_cli: drift: differs from the pin in commit" in out
    code, out = run_checker(capsys, "--manifest", str(manifest), "--observed", str(observed), "--only", "copilot_cli", *NOW_ARG)
    assert code == 1 and out.count("ERROR: ") == 1


def test_checker_notes_a_candidate_and_its_window_but_accepts_nothing(tmp_path, capsys):
    manifest = write_yaml(tmp_path / "pins.yaml", manifest_data(
        candidates={"claude_cli": candidate_block("claude_cli", first=T0)}))
    before = manifest.read_bytes()
    code, out = run_checker(capsys, "--manifest", str(manifest), "--now", iso(T0 + 8 * DAY))
    assert code == 0 and "a candidate waits for a person (acceptance window expired)" in out
    assert manifest.read_bytes() == before                                # read-only


def test_checker_refuses_a_bad_registry_and_a_bad_moment(tmp_path, capsys):
    manifest = write_yaml(tmp_path / "pins.yaml", manifest_data())
    observed = write_yaml(tmp_path / "observed.yaml", {"version": 1, "executors": {"claude_cli": {"version": "0.test"}}})
    code, out = run_checker(capsys, "--manifest", str(manifest), "--observed", str(observed), *NOW_ARG)
    assert code == 1 and "ERROR: observed: claude_cli: sha256: is required" in out
    with pytest.raises(SystemExit) as caught:
        checker.main(["--now", "2026-10-01T12:00:00"])
    assert caught.value.code == 2


def test_checker_never_invokes_network_or_executor(tmp_path, capsys, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("the pin check must not run a program or use the network")
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(process, "start", boom)
    monkeypatch.setattr(process, "run", boom)
    monkeypatch.setattr(process, "find_executable", boom)
    manifest = write_yaml(tmp_path / "pins.yaml", manifest_data())
    observed = write_yaml(tmp_path / "observed.yaml", {"version": 1, "executors": {
        "claude_cli": {"version": "0.test", "sha256": SHA_A}}})
    code, _ = run_checker(capsys, "--manifest", str(manifest), "--observed", str(observed), "--only", "claude_cli", *NOW_ARG)
    assert code == 0
    pins.PinGuard.from_files(manifest, observed).check("claude_cli", NOW)


@pytest.mark.parametrize("source", [ROOT / "multiagent" / "executor_pins.py", SCRIPT])
def test_the_pin_code_cannot_run_a_program_or_reach_the_network(source):
    imported = set()
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"subprocess", "socket", "urllib", "http", "requests", "shutil", "ctypes", "importlib", "os",
                           "asyncio", "multiprocessing"}, imported
