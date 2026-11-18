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


def setup(tmp_path, *, services=None):
    if services is None:
        settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                            gemini_api_key="", openai_api_key="", openai_enabled=False)
        store = Store(Database(settings))
        store.db.migrate()
    else:
        settings, store = services.system.settings, services.system.store
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


# ---- CAP-T14: the person's routes (GUI token route, API, CLI) ----------------------------------------------------

def _other_call(request, text="x", ordinal=1):
    return request.model_copy(update={"request_id": request_id_for(request.context, ordinal),
                                      "arguments": {"target": "synthetic.txt", "text": text}})


def test_permission_request_id_is_stable_across_hash_seeds_and_binds_arguments(tmp_path):
    import os
    import subprocess
    import sys
    store, service, invoker, request, count = setup(tmp_path)
    one = invoker.invoke(request, grants={"tool:mock.delete"}).permission_request
    # same request_id and destination, other arguments: another id (B11)
    same_id_other_args = request.model_copy(update={"arguments": {"target": "synthetic.txt", "text": "y"}})
    other = invoker.invoke(same_id_other_args, grants={"tool:mock.delete"}).permission_request
    assert other.request_id == one.request_id and other.actions == one.actions
    assert other.permission_request_id != one.permission_request_id
    assert invoker.invoke(request, grants={"tool:mock.delete"}).permission_request.permission_request_id == one.permission_request_id
    code = ("from multiagent.tools.contracts import ActionRef, permission_request_id_for\n"
            "refs=(ActionRef(kind='destructive', target='synthetic.txt'),)\n"
            "print(permission_request_id_for('tr_fixed_request_id', 'mock.delete', 1, refs, 'a'*64, 'b'*64))")
    outputs = set()
    for seed in ("1", "2", "random"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": os.pathsep.join(sys.path)}
        outputs.add(subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                                   check=True, timeout=60).stdout.strip())
    # A fixed literal: a change in the canonical JSON, the ActionRef dump or the bound fields strands stored waits.
    assert outputs == {"tpr_45cc55ed6b0f9e41342750382ccfa567"}


@pytest.fixture
def route(tmp_path):
    """The whole app over a real Store: a run held on one pending tool request, the way S5.D1 will hold it."""
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from multiagent.api import create_app
    from multiagent.gui.token import ensure_token
    from multiagent.services import ApplicationServices

    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_token_file=tmp_path / "gui-token")
    services = ApplicationServices.create(settings, dry_run=True)
    token = ensure_token(settings.gui_token_file)
    store, _, invoker, request, count = setup(tmp_path / "unused", services=services)
    denied = invoker.invoke(request, grants={"tool:mock.delete"})
    store.hold_tool_approval(denied.permission_request)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, store=store, invoker=invoker, request=request,
                              permission=denied.permission_request, run_id=request.context.run_id, settings=settings,
                              headers={"Authorization": f"Bearer {token}"}, count=count)
    services.runs.close()


def _gui(route, action, body=None):
    return route.client.post(f"/gui/api/runs/{route.run_id}/{action}", json=body if body is not None else {},
                             headers=route.headers)


def test_gui_shows_the_pending_request_and_approves_only_with_its_id(route):
    view = route.client.get(f"/gui/api/runs/{route.run_id}", headers=route.headers).json()
    shown = view["tool_approval"]
    assert shown["permission_request_id"] == route.permission.permission_request_id
    assert shown["actions"] == [{"kind": "destructive", "target": "synthetic.txt"}] and shown["summary"]
    assert "arguments" not in shown
    # the read API shows the same bounded fields
    api = route.client.get(f"/api/v1/runs/{route.run_id}").json()["tool_approval"]
    assert api == shown
    for wrong in ({}, {"permission_request_id": "tpr_stale"}):
        refused = _gui(route, "approve", wrong)
        assert refused.status_code == 409 and refused.json()["detail"]["details"]["reason"] == "permission_request_mismatch"
    # a request for other arguments is another id, and neither it nor one with changed arguments approves this wait
    other = route.invoker.invoke(_other_call(route.request, "changed"), grants={"tool:mock.delete"}).permission_request
    assert other.permission_request_id != route.permission.permission_request_id
    assert _gui(route, "approve", {"permission_request_id": other.permission_request_id}).status_code == 409
    changed = route.invoker.invoke(route.request.model_copy(update={"arguments": {"target": "synthetic.txt", "text": "z"}}),
                                   grants={"tool:mock.delete"}).permission_request
    assert changed.permission_request_id != route.permission.permission_request_id
    assert _gui(route, "approve", {"permission_request_id": changed.permission_request_id}).status_code == 409
    assert route.store.get_run(route.run_id)["status"] == "waiting_human"
    assert "tool_approval_approved" not in _events(route.store, route.run_id)
    ok = _gui(route, "approve", {"permission_request_id": shown["permission_request_id"]})
    assert ok.status_code == 200 and ok.json()["status"] == "queued" and ok.json()["tool_approval"] is None
    # a double click with the same id: the wait is settled, nothing more is approved
    again = _gui(route, "approve", {"permission_request_id": shown["permission_request_id"]})
    assert again.status_code == 409
    assert _events(route.store, route.run_id).count("tool_approval_approved") == 1
    assert route.store.take_tool_approval(route.permission) is not None


def test_gui_reject_works_with_or_without_an_id(route):
    assert _gui(route, "reject", {"feedback": "no"}).json()["status"] == "failed"
    events = _events(route.store, route.run_id)
    assert "tool_approval_rejected" in events and "tool_approval_approved" not in events
    assert route.store.take_tool_approval(route.permission) is None


def test_double_post_cannot_approve_the_next_request(route):
    shown = route.permission.permission_request_id
    assert _gui(route, "approve", {"permission_request_id": shown}).status_code == 200
    # the run goes on and holds a second request; the repeated POST still carries the first id
    route.store.update_run(route.run_id, status="running")
    second = route.invoker.invoke(_other_call(route.request, "next"), grants={"tool:mock.delete"}).permission_request
    route.store.hold_tool_approval(second)
    assert _gui(route, "approve", {"permission_request_id": shown}).status_code == 409
    assert route.store.get_run(route.run_id)["status"] == "waiting_human"
    assert _gui(route, "approve", {"permission_request_id": second.permission_request_id}).status_code == 200


def test_unauthenticated_api_route_never_approves_a_tool_call(route):
    refused = route.client.post(f"/api/v1/runs/{route.run_id}/approve")
    assert refused.status_code == 409 and refused.json()["detail"]["details"]["reason"] == "person_required"
    assert route.store.get_run(route.run_id)["status"] == "waiting_human"
    assert "tool_approval_approved" not in _events(route.store, route.run_id)
    # the GUI route without a token is not a way around it either
    assert route.client.post(f"/gui/api/runs/{route.run_id}/approve",
                             json={"permission_request_id": route.permission.permission_request_id}).status_code in (401, 403)
    assert route.store.get_run(route.run_id)["status"] == "waiting_human"
    assert route.client.post(f"/api/v1/runs/{route.run_id}/reject", json={"feedback": ""}).status_code == 200


def test_cli_approves_only_with_the_permission_request_id(route, monkeypatch, capsys):
    from multiagent import cli

    posts = []

    class Proxy:
        def post(self, path, **kwargs):
            posts.append(path)
            return route.client.post(path, **kwargs)

        def __getattr__(self, name):
            return getattr(route.client, name)

    class Ctx:
        def __enter__(self):
            return Proxy()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(cli, "api_client", lambda: Ctx())
    monkeypatch.setattr(cli, "get_settings", lambda: route.settings)
    token = route.headers["Authorization"].split()[1]
    with pytest.raises(SystemExit) as shown_exit:
        cli.main(["approve", route.run_id])
    assert shown_exit.value.code == 2            # shown, not approved: a script must not take it for approved
    shown = capsys.readouterr().out
    assert route.permission.permission_request_id in shown and "--permission-request" in shown
    assert route.store.get_run(route.run_id)["status"] == "waiting_human"
    posts.clear()
    with pytest.raises(SystemExit):
        cli.main(["approve", route.run_id, "--permission-request", "tpr_stale"])
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err      # the token is never in a message
    assert posts == [f"/gui/api/runs/{route.run_id}/approve"]   # straight to the token route, never /api/v1
    assert route.store.get_run(route.run_id)["status"] == "waiting_human"
    cli.main(["approve", route.run_id, "--permission-request", route.permission.permission_request_id])
    assert route.store.get_run(route.run_id)["status"] == "queued"
    assert not any(path.startswith("/api/v1") for path in posts)


def test_cli_scrubs_the_token_from_a_server_error(route, monkeypatch, capsys):
    from multiagent import cli

    token = route.headers["Authorization"].split()[1]

    class Bad:
        status_code = 500
        text = "echo " + token

        def json(self):
            return {"detail": "echo " + token}

    class Client:
        def post(self, *a, **k):
            return Bad()

        def __enter__(self):
            return self

        def __exit__(self, *e):
            return False

    monkeypatch.setattr(cli, "api_client", lambda: Client())
    monkeypatch.setattr(cli, "get_settings", lambda: route.settings)
    with pytest.raises(SystemExit):
        cli.main(["approve", route.run_id, "--permission-request", "tpr_x"])
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err


@pytest.mark.parametrize("next_wait", ["budget_cut_approval", "user_message"])
def test_old_id_never_approves_a_wait_of_another_kind(route, next_wait):
    shown = route.permission.permission_request_id
    assert _gui(route, "approve", {"permission_request_id": shown}).status_code == 200
    route.store.update_run(route.run_id, status="waiting_human", waiting_reason=next_wait, waiting_step="s", waiting_attempt=1)
    refused = _gui(route, "approve", {"permission_request_id": shown})
    assert refused.status_code == 409 and refused.json()["detail"]["details"]["reason"] == "permission_request_mismatch"
    run = route.store.get_run(route.run_id)
    assert run["status"] == "waiting_human" and run["waiting_reason"] == next_wait
    approvals = [e for e in _events(route.store, route.run_id) if "approved" in e]
    assert approvals == ["tool_approval_approved"]


def test_view_strips_controls_and_bidi_from_summary_and_destinations(route):
    risky = route.request.model_copy(update={
        "request_id": request_id_for(route.request.context, 5),
        "arguments": {"target": "safe‮txt.exe\u009b​⁦", "text": "x"}})
    held = route.invoker.invoke(risky, grants={"tool:mock.delete"}).permission_request
    route.store.update_run(route.run_id, status="running")
    route.store.hold_tool_approval(held)
    shown = route.client.get(f"/gui/api/runs/{route.run_id}", headers=route.headers).json()["tool_approval"]
    assert shown["permission_request_id"] == held.permission_request_id
    text = str(shown)
    for bad in ("‮", "\u009b", "​", "⁦"):
        assert bad not in text
    assert shown["actions"][0]["target"] == "safetxt.exe"


def _view_run(route, events, **fields):
    run = {"id": route.run_id, "status": "waiting_human", "waiting_reason": "tool_approval",
           "waiting_step": route.request.context.step_id, "waiting_attempt": route.request.context.attempt,
           "events": [{"seq": i, "event_type": kind, "payload": payload} for i, (kind, payload) in enumerate(events, 1)]}
    run.update(fields)
    return run


def test_view_is_exactly_the_bounded_fields_and_follows_the_persisted_events(route):
    from multiagent.services.runs import pending_tool_approval

    payload = route.permission.model_dump(mode="json")
    requested = ("tool_approval_requested", payload)
    shown = pending_tool_approval(_view_run(route, [requested]))
    assert set(shown) == {"permission_request_id", "tool_id", "tool_version", "step_id", "attempt", "summary", "impacts",
                          "actions", "actions_total", "actions_omitted", "arguments_sha256"}
    # settled requests are no longer shown
    assert pending_tool_approval(_view_run(route, [requested, ("tool_approval_approved", {})])) is None
    assert pending_tool_approval(_view_run(route, [requested, ("tool_approval_rejected", {})])) is None
    # another step or attempt than the one waiting is not shown
    assert pending_tool_approval(_view_run(route, [requested], waiting_step="other")) is None
    assert pending_tool_approval(_view_run(route, [requested], waiting_attempt=9)) is None
    assert pending_tool_approval(_view_run(route, [requested], id="run_other")) is None
    # an unreadable newer event leaves nothing to show, never the old request
    assert pending_tool_approval(_view_run(route, [requested, ("tool_approval_requested", {})])) is None
    # not waiting on a tool approval: nothing
    assert pending_tool_approval(_view_run(route, [requested], waiting_reason="review")) is None


def test_view_says_when_actions_are_cut(route):
    from multiagent.services.runs import pending_tool_approval

    payload = route.permission.model_dump(mode="json")
    payload["actions"] = [{"kind": "destructive", "target": f"f{i}.txt"} for i in range(25)]
    shown = pending_tool_approval(_view_run(route, [("tool_approval_requested", payload)]))
    assert len(shown["actions"]) == 20 and shown["actions_total"] == 25
    assert shown["actions_omitted"] == "+5 más (total 25)"
