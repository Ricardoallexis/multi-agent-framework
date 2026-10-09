# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Pure task and result envelopes v1 (ORQ-T24)."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from multiagent.tasks.envelopes import (
    MAX_RESULT_BYTES,
    TASK_ENVELOPE_FIELDS,
    TASK_ENVELOPE_SCHEMA,
    TASK_RESULT_FIELDS,
    TASK_RESULT_SCHEMA,
    EnvelopeError,
    blob_ref,
    build_task_envelope,
    canonical_json,
    envelope_sha256,
    parse_reference,
    patch_sha256,
    result_key,
    result_ref,
    same_result,
    unchanged_envelope,
    validate_reference,
    validate_task_envelope,
    validate_task_result,
)

SHA = "4de80d71" + "0" * 52 + "b9df"
RULES = "6c621d1a" + "1" * 52 + "1bb0"


def envelope(**changes):
    fields = {
        "task_id": "G5-T02", "group_id": "G5", "revision": 7, "attempt": 1, "assignee": "agent:a",
        "status": "ready",
        "objective": "Implement the retry policy for failed model calls. Keep the public API unchanged.",
        "depends_on": [{"task_id": "G5-T01", "requires": "accepted", "status": "accepted",
                        "result_ref": "result://G5-T01/1"}],
        "artifact_refs": [f"blob:sha256:{SHA}"],
        "context_refs": ["doc://docs/ARCHITECTURE.md#model-router@2263537", f"rules@sha256:{RULES}"],
        "file_scope": ["multiagent/model_router.py", "tests/test_model_router.py"],
        "constraints": ["no push"], "gate": ["tests"], "base_commit": "2263537", "events_after": 212,
    }
    fields.update(changes)
    return build_task_envelope(**fields)


def result(**changes):
    data = {
        "base_commit": "2263537",
        "patch": {"blob": f"sha256:{SHA}", "files": ["multiagent/model_router.py"]},
        "summary": "Added the retry policy with a cap of three attempts.",
        "decisions": ["Retry only on transient errors"],
        "tests_not_run": ["pytest"],
        "requests": [{"to": "user", "what": "apply the patch"}],
    }
    data.update(changes)
    return data


def submit(data, **key):
    return validate_task_result(data, **{"task_id": "G5-T01", "attempt": 1, "agent": "agent:b", **key})


# -------- references --------
@pytest.mark.parametrize(("text", "scheme", "attrs"), [
    ("result://G5-T01/3", "result", {"task_id": "G5-T01", "attempt": 3}),
    (f"blob:sha256:{SHA}", "blob", {"sha256": SHA}),
    ("doc://docs/TASKS.md", "doc", {"path": "docs/TASKS.md", "anchor": None, "commit": None}),
    ("doc://docs/TASKS.md#gates@90fb692", "doc", {"path": "docs/TASKS.md", "anchor": "gates", "commit": "90fb692"}),
    ("doc://README.md@" + "a" * 40, "doc", {"path": "README.md", "commit": "a" * 40}),
    ("report://2026-10-09_0430", "report", {"report_id": "2026-10-09_0430"}),
    (f"rules@sha256:{RULES}", "rules", {"sha256": RULES}),
])
def test_valid_references_parse_into_their_parts(text, scheme, attrs):
    ref = parse_reference(text)
    assert ref.text == text and ref.scheme == scheme
    assert ref.resolvable is (scheme in ("result", "blob"))
    for name, value in attrs.items():
        assert getattr(ref, name) == value


@pytest.mark.parametrize("text", [
    "", "x" * 201, 42, None,
    "result://G5-T01/0", "result://G5-T01/x", "result://G5-T01", "result:///1", "result://G5 T01/1",
    "blob:sha256:" + "A" * 64, "blob:sha256:abc", "blob:md5:" + "0" * 64, "blob:" + "0" * 64,
    "rules@sha1:" + "0" * 64,
    "doc:///etc/passwd", "doc://C:/Users/x/secret.txt", "doc://docs/../.env", "doc://docs\\TASKS.md",
    "doc://docs//TASKS.md", "doc://docs/TASKS.md@HEAD", "doc://docs/TASKS.md#a b",
    "report://", "report://user:secret@host", "report://a/b",
    "file:///etc/passwd", "http://user:pass@example.com/x", "https://example.com", "ftp://x",
    "doc://docs/TASKS.md\n", "result://G5-T01/1\x00",
])
def test_invalid_references_are_rejected(text):
    with pytest.raises(EnvelopeError) as raised:
        parse_reference(text, field="ref")
    assert raised.value.field == "ref"


def test_reference_helpers_and_scheme_filter():
    assert result_ref("G5-T01", 2) == "result://G5-T01/2"
    assert blob_ref(SHA) == blob_ref(f"sha256:{SHA}") == f"blob:sha256:{SHA}"
    assert validate_reference(f"blob:sha256:{SHA}", schemes=("blob",)) == f"blob:sha256:{SHA}"
    with pytest.raises(EnvelopeError, match="must use blob"):
        validate_reference("doc://README.md", schemes=("blob",))
    with pytest.raises(EnvelopeError):
        result_ref("G5-T01", 0)


# -------- canonical JSON --------
def test_canonical_json_sorts_keys_and_hash_ignores_key_order():
    assert canonical_json({"b": 1, "a": "é"}) == '{"a":"é","b":1}'.encode()
    assert envelope_sha256({"a": 1, "b": [1, 2]}) == envelope_sha256({"b": [1, 2], "a": 1})
    assert envelope_sha256({"a": 1}) != envelope_sha256({"a": 2})
    with pytest.raises(ValueError):
        canonical_json({"a": float("nan")})


# -------- task envelope --------
def test_task_envelope_has_exactly_the_contract_fields_in_order():
    env = envelope()
    assert tuple(env) == TASK_ENVELOPE_FIELDS
    assert env["schema"] == TASK_ENVELOPE_SCHEMA
    assert env["gate"] == ["command:test"]          # alias normalized as ORQ-2 does
    assert env["depends_on"][0]["result_ref"] == "result://G5-T01/1"
    assert len(canonical_json(env)) < 1200


def test_task_envelope_defaults_and_deduplication():
    env = envelope(depends_on=[], artifact_refs=[f"blob:sha256:{SHA}"] * 2, context_refs=[], file_scope=["a/", "a"],
                   constraints=[], gate=["review", "review"], base_commit="", assignee=None, status="blocked")
    assert env["artifact_refs"] == [f"blob:sha256:{SHA}"]
    assert env["file_scope"] == ["a"] and env["gate"] == ["review"]
    assert env["assignee"] is None and env["base_commit"] == "" and env["depends_on"] == []


def test_task_envelope_forward_compatibility_ignores_unknown_fields():
    received = dict(envelope(), future_field={"x": 1}, command="rm -rf /", api_key="secret")
    env = validate_task_envelope(received)
    assert tuple(env) == TASK_ENVELOPE_FIELDS
    assert "command" not in env and "api_key" not in env and "future_field" not in env
    without_schema = {k: v for k, v in envelope().items() if k != "schema"}
    assert validate_task_envelope(without_schema) == envelope()


def test_task_envelope_rejects_other_schema_versions():
    with pytest.raises(EnvelopeError) as raised:
        validate_task_envelope(dict(envelope(), schema="multiagent.task_envelope/2"))
    assert raised.value.field == "schema"


@pytest.mark.parametrize(("changes", "field"), [
    ({"task_id": "bad id"}, "task_id"),
    ({"group_id": "1G"}, "group_id"),
    ({"revision": 0}, "revision"),
    ({"revision": True}, "revision"),
    ({"attempt": -1}, "attempt"),
    ({"status": "open"}, "status"),
    ({"objective": " "}, "objective"),
    ({"objective": "x" * 4001}, "objective"),
    ({"depends_on": [{"task_id": "G5-T01", "requires": "done", "status": "accepted"}]}, "depends_on.requires"),
    ({"depends_on": [{"task_id": "G5-T01", "requires": "accepted", "status": "weird"}]}, "depends_on.status"),
    ({"depends_on": [{"task_id": "G5-T01", "requires": "accepted", "status": "accepted",
                      "result_ref": "result://OTHER/1"}]}, "depends_on.result_ref"),
    ({"depends_on": ["G5-T01"]}, "depends_on"),
    ({"depends_on": [{"task_id": f"T{i}", "requires": "accepted", "status": "ready"}
                     for i in range(51)]}, "depends_on"),
    ({"artifact_refs": ["doc://README.md"]}, "artifact_refs"),
    ({"context_refs": ["http://example.com"]}, "context_refs"),
    ({"context_refs": [f"doc://d{i}.md" for i in range(21)]}, "context_refs"),
    ({"context_refs": "doc://README.md"}, "context_refs"),
    ({"file_scope": ["/etc/passwd"]}, "file_scope"),
    ({"file_scope": ["C:/Users/x"]}, "file_scope"),
    ({"file_scope": ["src/../secret"]}, "file_scope"),
    ({"file_scope": ["src\\a.py"]}, "file_scope"),
    ({"constraints": ["x" * 201]}, "constraints"),
    ({"constraints": [f"c{i}" for i in range(21)]}, "constraints"),
    ({"gate": ["shell:rm -rf /"]}, "gate"),
    ({"gate": ["command:Bad Name"]}, "gate"),
    ({"gate": [f"command:c{i}" for i in range(11)]}, "gate"),
    ({"base_commit": "HEAD"}, "base_commit"),
    ({"base_commit": "abc12"}, "base_commit"),
    ({"events_after": -1}, "events_after"),
])
def test_task_envelope_rejects_invalid_fields(changes, field):
    with pytest.raises(EnvelopeError) as raised:
        envelope(**changes)
    assert raised.value.field == field


def test_unchanged_envelope_answer_is_short_and_carries_the_hash():
    env = envelope()
    answer = unchanged_envelope(env)
    assert answer == {"unchanged": True, "hash": envelope_sha256(env), "revision": 7}
    assert len(canonical_json(answer)) < 120
    assert envelope_sha256(envelope(events_after=213)) != answer["hash"]


# -------- task result --------
def test_task_result_is_completed_from_the_task_and_actor():
    res = submit(result())
    assert tuple(res) == TASK_RESULT_FIELDS
    assert res["schema"] == TASK_RESULT_SCHEMA
    assert (res["task_id"], res["attempt"], res["agent"]) == ("G5-T01", 1, "agent:b")
    assert res["contract_changes"] == "" and res["patch"]["blob"] == f"sha256:{SHA}"
    assert result_key(res) == ("G5-T01", 1, SHA) and patch_sha256(res) == SHA


def test_task_result_minimal_has_defaults_and_empty_patch_key():
    res = submit({"summary": "Analysis only."})
    assert res["patch"] is None and res["decisions"] == [] and res["requests"] == [] and res["base_commit"] == ""
    assert result_key(res) == ("G5-T01", 1, "")


def test_task_result_matching_identity_is_accepted_and_mismatch_rejected():
    submit(result(schema=TASK_RESULT_SCHEMA, task_id="G5-T01", attempt=1, agent="agent:b"))
    for field, value in (("task_id", "G5-T09"), ("attempt", 2), ("agent", "agent:c")):
        with pytest.raises(EnvelopeError) as raised:
            submit(result(**{field: value}))
        assert raised.value.field == field
    with pytest.raises(EnvelopeError) as raised:
        submit(result(schema="multiagent.task_result/2"))
    assert raised.value.field == "schema"


def test_task_result_forward_compatibility_drops_unknown_fields():
    res = submit(result(conversation="...long transcript...", command="git push", token="secret"))
    assert tuple(res) == TASK_RESULT_FIELDS


@pytest.mark.parametrize(("changes", "field"), [
    ({"summary": ""}, "summary"),
    ({"summary": "x" * 1501}, "summary"),
    ({"decisions": ["x" * 301]}, "decisions"),
    ({"decisions": [f"d{i}" for i in range(21)]}, "decisions"),
    ({"contract_changes": "x" * 1501}, "contract_changes"),
    ({"tests_not_run": [f"t{i}" for i in range(21)]}, "tests_not_run"),
    ({"requests": [{"to": "user"}]}, "requests.what"),
    ({"requests": [{"to": "u", "what": "w"}] * 11}, "requests"),
    ({"requests": ["apply"]}, "requests"),
    ({"base_commit": "main"}, "base_commit"),
    ({"patch": "diff --git a b"}, "patch"),
    ({"patch": {"blob": f"blob:sha256:{SHA}", "files": []}}, "patch.blob"),
    ({"patch": {"blob": "sha256:abc", "files": []}}, "patch.blob"),
    ({"patch": {"blob": f"sha256:{SHA}", "files": ["/abs/path.py"]}}, "patch.files"),
    ({"patch": {"blob": f"sha256:{SHA}", "files": [f"f{i}.py" for i in range(201)]}}, "patch.files"),
])
def test_task_result_rejects_invalid_fields(changes, field):
    with pytest.raises(EnvelopeError) as raised:
        submit(result(**changes))
    assert raised.value.field == field


def test_task_result_identity_arguments_are_validated():
    with pytest.raises(EnvelopeError):
        submit(result(), attempt=0)
    with pytest.raises(EnvelopeError):
        submit(result(), task_id="bad id")


def test_task_result_total_size_limit():
    big = result(summary="s" * 1500, decisions=["d" * 300] * 20, contract_changes="c" * 1500,
                 tests_not_run=["t" * 200] * 20, requests=[{"to": "u" * 100, "what": "w" * 200}] * 10)
    with pytest.raises(EnvelopeError) as raised:
        submit(big)
    assert raised.value.field == "result" and str(MAX_RESULT_BYTES) in str(raised.value)
    assert len(canonical_json(submit(result(summary="s" * 1500)))) <= MAX_RESULT_BYTES


def test_same_result_detects_a_replay_and_a_different_content():
    first = submit(result())
    assert same_result(first, submit(json.loads(json.dumps(result()))))
    assert not same_result(first, submit(result(summary="Other summary.")))


def test_the_envelope_module_imports_no_database_services_or_processes():
    path = Path(__file__).resolve().parents[1] / "multiagent" / "tasks" / "envelopes.py"
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                 else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
        for name in names:
            assert not any(part in name for part in ("db", "services", "sqlite3", "subprocess", "commands")), name
