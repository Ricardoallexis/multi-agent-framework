"""Synthetic, local coverage for one-call destructive tool approval."""
import pytest
from pydantic import ValidationError

from multiagent.config import Settings
from multiagent.contracts import RunStatus, WaitingReason
from multiagent.db.database import Database
from multiagent.db.store import Store
from multiagent.document_actions import Action, ActionKind, Approval, authorize
from multiagent.document_trust import Provenance, SourceKind
from multiagent.run_request import RunRequest
from multiagent.run_service import PersonRequired, RunService
from multiagent.tools.approvals import pause_for_result
from multiagent.tools.contracts import EffectDecl, ImpactClass, ToolCallContext, ToolOrigin, ToolRequest, ToolSpec, request_id_for
from multiagent.tools.invoker import ToolInvoker
from multiagent.tools.mock import register_mock_tools
from multiagent.tools.registry import ToolRegistry


def setup(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    store = Store(Database(settings))
    store.db.migrate()
    run = store.create_run(RunRequest(workflow_id="synthetic", project_name="Synthetic tool approval"), 4, 300)
    store.update_run(run["id"], status=RunStatus.RUNNING.value)
    registry = ToolRegistry()
    register_mock_tools(registry)
    write = registry.lookup("mock.note_write")
    spec = ToolSpec.model_validate({**write.spec.model_dump(mode="json"), "tool_id": "mock.delete",
                                   "impacts": ["destructive"],
                                   "effects": [{"kind": "destructive", "target_from": "/target"}]})
    count = []
    registry.register(spec, lambda arguments, context, *, should_cancel: count.append(arguments) or {"text": "done"})
    context = ToolCallContext(scope="run_step", run_id=run["id"], step_id="delete", attempt=1)
    request = ToolRequest(request_id=request_id_for(context, 0), tool_id="mock.delete",
                          arguments={"target": "synthetic.txt", "text": "x"}, context=context,
                          origin=ToolOrigin(kind="model_output", ref="model:1"))
    return store, RunService(settings, store), ToolInvoker(registry), request, count


def test_destructive_requires_one_call_person_approval(tmp_path):
    store, service, invoker, request, count = setup(tmp_path)
    write = Approval(Provenance(SourceKind.USER, ref="user:1"), frozenset({ActionKind.WRITE}),
                     frozenset({"synthetic.txt"}))
    denied = invoker.invoke(request, grants={"tool:mock.delete"}, evidence=[write])
    assert denied.error.code == "approval_required" and count == []
    pause = pause_for_result(denied)
    assert pause.waiting_reason == WaitingReason.TOOL_APPROVAL.value
    store.hold_tool_approval(denied.permission_request)
    assert service.get(request.context.run_id)["waiting_reason"] == "tool_approval"
    with pytest.raises(PersonRequired):
        service.approve(request.context.run_id, recorded_only=True)
    assert service.get(request.context.run_id)["status"] == "waiting_human"
    assert service.approve(request.context.run_id, permission_request_id=denied.permission_request.permission_request_id)["status"] == "queued"
    grant = store.take_tool_approval(denied.permission_request)
    assert grant is not None
    assert store.take_tool_approval(denied.permission_request) is None
    assert invoker.invoke(request, grants={"tool:mock.delete"}, evidence=[grant]).ok
    assert len(count) == 1
    assert invoker.invoke(request, grants={"tool:mock.delete"}, evidence=[grant]).error.code == "approval_required"


def test_other_arguments_or_call_denied_and_reject_fails_run(tmp_path):
    store, service, invoker, request, count = setup(tmp_path)
    denied = invoker.invoke(request, grants={"tool:mock.delete"})
    store.hold_tool_approval(denied.permission_request)
    service.approve(request.context.run_id, permission_request_id=denied.permission_request.permission_request_id)
    grant = store.take_tool_approval(denied.permission_request)
    altered = request.model_copy(update={"arguments": {"target": "other.txt", "text": "x"}})
    assert invoker.invoke(altered, grants={"tool:mock.delete"}, evidence=[grant]).error.code == "approval_required"
    assert count == []
    other = request.model_copy(update={"request_id": "tr_other_call_123456"})
    assert invoker.invoke(other, grants={"tool:mock.delete"}, evidence=[grant]).error.code == "approval_required"
    store.update_run(request.context.run_id, status="running")
    denied = invoker.invoke(request, grants={"tool:mock.delete"})
    store.hold_tool_approval(denied.permission_request)
    assert service.reject(request.context.run_id)["status"] == "failed"
    assert store.take_tool_approval(denied.permission_request) is None


def test_unknown_action_kind_fails_closed(tmp_path):
    store, service, invoker, request, count = setup(tmp_path)
    with pytest.raises(ValidationError):
        EffectDecl.model_validate({"kind": "erase", "target": "synthetic.txt"})
    with pytest.raises(ValidationError):
        ToolSpec.model_validate({**invoker.registry.lookup("mock.delete").spec.model_dump(mode="json"),
                                 "effects": [{"kind": "erase", "target": "synthetic.txt"}]})
    bad = Action("erase", "synthetic.txt")
    assert not authorize(bad, [Approval(Provenance(SourceKind.USER, ref="u"),
                                        frozenset({ActionKind.DESTRUCTIVE}))]).allowed


def _granted(tmp_path):
    store, service, invoker, request, count = setup(tmp_path)
    denied = invoker.invoke(request, grants={"tool:mock.delete"})
    store.hold_tool_approval(denied.permission_request)
    service.approve(request.context.run_id, permission_request_id=denied.permission_request.permission_request_id)
    return store, invoker, request, count, denied.permission_request


def test_generic_approval_enumerating_destructive_never_releases(tmp_path):
    store, service, invoker, request, count = setup(tmp_path)
    broad = Approval(Provenance(SourceKind.USER, ref="user:1"),
                     frozenset({ActionKind.WRITE, ActionKind.DESTRUCTIVE}))
    assert invoker.invoke(request, grants={"tool:mock.delete"}, evidence=[broad]).error.code == "approval_required"
    assert count == []


def test_grant_expires(tmp_path):
    store, invoker, request, count, permission = _granted(tmp_path)
    with store.db.tx() as conn:
        row = conn.execute("SELECT id,payload_json FROM run_events WHERE event_type='tool_approval_approved'").fetchone()
        import json
        payload = json.loads(row["payload_json"])
        payload["expires_at"] = "2000-01-01T00:00:00+00:00"
        conn.execute("UPDATE run_events SET payload_json=? WHERE id=?", (json.dumps(payload), row["id"]))
    assert store.take_tool_approval(permission) is None
    assert count == []


def test_grant_bound_to_step_attempt_version_and_spec(tmp_path):
    store, invoker, request, count, permission = _granted(tmp_path)
    spec = invoker.registry.lookup("mock.delete").spec
    base = store.take_tool_approval(permission)
    assert base is not None
    from multiagent.tools.approvals import _approval_from_decision
    from datetime import datetime, timedelta, timezone
    def fresh():
        return _approval_from_decision(permission, datetime.now(timezone.utc) + timedelta(minutes=5))
    assert fresh().valid_for(spec, request)
    other_step = request.model_copy(update={"context": request.context.model_copy(update={"step_id": "other"})})
    other_attempt = request.model_copy(update={"context": request.context.model_copy(update={"attempt": 2})})
    for changed in (other_step, other_attempt, request.model_copy(update={"tool_id": "mock.other"})):
        assert not fresh().valid_for(spec, changed)
    assert not fresh().valid_for(spec.model_copy(update={"version": 2}), request)
    assert not fresh().valid_for(spec.model_copy(update={"title": "Changed"}), request)
    expired = _approval_from_decision(permission, datetime.now(timezone.utc) - timedelta(seconds=1))
    assert not expired.valid_for(spec, request)
    assert count == []


def _held(tmp_path):
    store, service, invoker, request, count = setup(tmp_path)
    denied = invoker.invoke(request, grants={"tool:mock.delete"})
    store.hold_tool_approval(denied.permission_request)
    return store, service, invoker, request, count, denied.permission_request


def _events(store, run_id):
    with store.db.connect() as conn:
        return [r["event_type"] for r in conn.execute("SELECT event_type FROM run_events WHERE run_id=? ORDER BY id", (run_id,))]


def test_approve_must_name_the_pending_request(tmp_path):
    from multiagent.tools.approvals import ToolApprovalMismatch
    store, service, invoker, request, count, permission = _held(tmp_path)
    run_id = request.context.run_id
    for wrong in (None, "pr_other"):
        with pytest.raises(ToolApprovalMismatch) as err:
            service.approve(run_id, permission_request_id=wrong)
        assert err.value.code == "permission_request_mismatch"
    assert service.get(run_id)["status"] == "waiting_human"
    assert "tool_approval_approved" not in _events(store, run_id)
    assert service.approve(run_id, permission_request_id=permission.permission_request_id)["status"] == "queued"
    # A repeated click cannot approve the next request: the run is no longer waiting on this one.
    with pytest.raises(Exception):
        service.approve(run_id, permission_request_id=permission.permission_request_id)


def test_missing_or_unreadable_request_event_can_always_be_rejected(tmp_path):
    from multiagent.tools.approvals import ToolApprovalMismatch
    for mode in ("missing", "unreadable"):
        store, service, invoker, request, count, permission = _held(tmp_path / mode)
        run_id = request.context.run_id
        with store.db.tx() as conn:
            if mode == "missing":
                conn.execute("DELETE FROM run_events WHERE event_type='tool_approval_requested'")
            else:
                conn.execute("UPDATE run_events SET payload_json='{}' WHERE event_type='tool_approval_requested'")
        with pytest.raises(ToolApprovalMismatch) as err:
            service.approve(run_id, permission_request_id=permission.permission_request_id)
        assert err.value.code == "no_pending_tool_approval"
        assert service.get(run_id)["status"] == "waiting_human"
        assert service.reject(run_id)["status"] == "failed"
        events = _events(store, run_id)
        assert "tool_approval_rejected" in events and "tool_approval_approved" not in events
        assert store.take_tool_approval(permission) is None


def test_binding_mutations_are_caught(tmp_path):
    from multiagent.tools.approvals import _approval_from_decision
    from datetime import datetime, timedelta, timezone
    store, service, invoker, request, count, permission = _held(tmp_path)
    run_id = request.context.run_id
    service.approve(run_id, permission_request_id=permission.permission_request_id)
    spec = invoker.registry.lookup("mock.delete").spec
    def fresh():
        return _approval_from_decision(permission, datetime.now(timezone.utc) + timedelta(minutes=5))
    assert fresh().valid_for(spec, request)
    # same target, other non-target argument
    assert not fresh().valid_for(spec, request.model_copy(update={"arguments": {"target": "synthetic.txt", "text": "y"}}))
    assert not fresh().valid_for(spec, request.model_copy(update={"request_id": "tr_other_call_123456"}))
    other_run = request.model_copy(update={"context": request.context.model_copy(update={"run_id": "run_other"})})
    assert not fresh().valid_for(spec, other_run)
    # same id, other arguments hash: take must not hand out a grant for the changed request
    changed = permission.model_copy(update={"arguments_sha256": "0" * 64})
    assert store.take_tool_approval(changed) is None
    # the grant is built from the persisted request, not from the caller's object
    taken = store.take_tool_approval(permission)
    assert taken is not None and taken.permission == permission
    # a cancelled run yields nothing
    store2, service2, _, request2, _, permission2 = _held(tmp_path / "c")
    service2.approve(request2.context.run_id, permission_request_id=permission2.permission_request_id)
    store2.update_run(request2.context.run_id, cancel_requested=1)
    assert store2.take_tool_approval(permission2) is None


def test_reject_records_rejected_never_approved_and_releases_nothing(tmp_path):
    store, service, invoker, request, count, permission = _held(tmp_path)
    run_id = request.context.run_id
    assert service.reject(run_id)["status"] == "failed"
    events = _events(store, run_id)
    assert "tool_approval_rejected" in events and "tool_approval_approved" not in events
    store.update_run(run_id, status="running")
    assert store.take_tool_approval(permission) is None


def test_finish_with_stale_run_dict_is_refused(tmp_path):
    from multiagent.errors import InvalidStateTransition
    store, service, invoker, request, count, permission = _held(tmp_path)
    stale = dict(store.get_run(request.context.run_id), waiting_step="other")
    with pytest.raises(InvalidStateTransition):
        store.finish_tool_approval(stale, approve=True, permission_request_id=permission.permission_request_id)
