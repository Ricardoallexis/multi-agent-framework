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
    assert service.approve(request.context.run_id, permission_request_id=denied.permission_request.permission_request_id, person=True)["status"] == "queued"
    store.update_run(request.context.run_id, status="running")     # take needs a RUNNING run (B7)
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
    service.approve(request.context.run_id, permission_request_id=denied.permission_request.permission_request_id, person=True)
    store.update_run(request.context.run_id, status="running")
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
    service.approve(request.context.run_id, permission_request_id=denied.permission_request.permission_request_id, person=True)
    store.update_run(request.context.run_id, status="running")
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
        return _approval_from_decision(permission, datetime.now(timezone.utc) + timedelta(minutes=5), datetime.now(timezone.utc) - timedelta(seconds=1))
    assert fresh().valid_for(spec, request)
    other_step = request.model_copy(update={"context": request.context.model_copy(update={"step_id": "other"})})
    other_attempt = request.model_copy(update={"context": request.context.model_copy(update={"attempt": 2})})
    for changed in (other_step, other_attempt, request.model_copy(update={"tool_id": "mock.other"})):
        assert not fresh().valid_for(spec, changed)
    assert not fresh().valid_for(spec.model_copy(update={"version": 2}), request)
    assert not fresh().valid_for(spec.model_copy(update={"title": "Changed"}), request)
    expired = _approval_from_decision(permission, datetime.now(timezone.utc) - timedelta(seconds=1), datetime.now(timezone.utc) - timedelta(minutes=1))
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
            service.approve(run_id, permission_request_id=wrong, person=True)
        assert err.value.code == "permission_request_mismatch"
    assert service.get(run_id)["status"] == "waiting_human"
    assert "tool_approval_approved" not in _events(store, run_id)
    assert service.approve(run_id, permission_request_id=permission.permission_request_id, person=True)["status"] == "queued"
    # A repeated click cannot approve the next request: the run is no longer waiting on this one.
    with pytest.raises(Exception):
        service.approve(run_id, permission_request_id=permission.permission_request_id, person=True)


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
            service.approve(run_id, permission_request_id=permission.permission_request_id, person=True)
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
    service.approve(run_id, permission_request_id=permission.permission_request_id, person=True)
    store.update_run(run_id, status="running")
    spec = invoker.registry.lookup("mock.delete").spec
    def fresh():
        return _approval_from_decision(permission, datetime.now(timezone.utc) + timedelta(minutes=5), datetime.now(timezone.utc) - timedelta(seconds=1))
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
    service2.approve(request2.context.run_id, permission_request_id=permission2.permission_request_id, person=True)
    store2.update_run(request2.context.run_id, status="running", cancel_requested=1)
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
    route.store.update_run(route.run_id, status="running")
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


def test_view_strips_controls_and_bidi_from_the_summary(route):
    # Such a destination can no longer be requested at all (B9 of CAP-T14, below); the view still cleans a summary.
    dirty = "safe\u202etxt.exe\u009b\u200b\u2066"
    held = route.permission.model_copy(update={"summary": dirty})
    route.store.update_run(route.run_id, status="running")
    route.store.hold_tool_approval(held)
    shown = route.client.get(f"/gui/api/runs/{route.run_id}", headers=route.headers).json()["tool_approval"]
    assert shown["permission_request_id"] == held.permission_request_id
    # N06: the exact shown string, not str(shown), which escapes what it prints
    assert shown["summary"] == "safetxt.exe"
    import unicodedata
    assert not any(unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp", "Co") for ch in shown["summary"])
    assert shown["actions"][0]["target"] == "synthetic.txt"


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
    assert shown["actions_omitted"] == "+5 more (total 25)"


# ---- CAP-T15: bajas B1-B9 de CAP-T09 y B9-B10 de CAP-T14 ---------------------------------------------------------

def _granted_run(tmp_path):
    """Held, approved by a person, run RUNNING again: the state in which a worker takes the grant."""
    store, service, invoker, request, count, permission = _held(tmp_path)
    service.approve(request.context.run_id, permission_request_id=permission.permission_request_id, person=True)
    store.update_run(request.context.run_id, status="running")
    return store, service, invoker, request, count, permission


def test_b1_facade_without_the_person_flag_never_approves(route):
    from multiagent.run_service import PersonRequired
    from multiagent.services.errors import ServiceError

    pid = route.permission.permission_request_id
    with pytest.raises(ServiceError):
        route.services.runs.approve(route.run_id, permission_request_id=pid)           # services facade, no flag
    with pytest.raises(PersonRequired):
        route.services.system.run_service.approve(route.run_id, permission_request_id=pid)   # run service, no flag
    with pytest.raises(PersonRequired):
        route.services.system.run_service.approve(route.run_id, permission_request_id=pid, person=False)
    assert route.store.get_run(route.run_id)["status"] == "waiting_human"
    assert "tool_approval_approved" not in _events(route.store, route.run_id)
    assert route.services.runs.approve(route.run_id, permission_request_id=pid, person=True)["status"] == "queued"


def test_b2_clock_bounds_and_ttl(tmp_path, monkeypatch):
    import json
    from datetime import datetime, timedelta, timezone

    from multiagent.tools import approvals

    def payload(store):
        with store.db.connect() as conn:
            row = conn.execute("SELECT id,payload_json FROM run_events WHERE event_type='tool_approval_approved'").fetchone()
        return row["id"], json.loads(row["payload_json"])

    def rewrite(store, **changes):
        event_id, data = payload(store)
        data.update(changes)
        with store.db.tx() as conn:
            conn.execute("UPDATE run_events SET payload_json=? WHERE id=?", (json.dumps(data), event_id))

    base = datetime(2030, 1, 1, tzinfo=timezone.utc)
    # the grant lasts exactly ten minutes from the approval (M30: not ten days)
    monkeypatch.setattr(approvals, "_now", lambda: base)
    store, service, invoker, request, count, permission = _granted_run(tmp_path / "a")
    _, data = payload(store)
    assert datetime.fromisoformat(data["expires_at"]) - datetime.fromisoformat(data["approved_at"]) == timedelta(minutes=10)
    # the clock went back before the approval (it was ahead when approving): refused
    monkeypatch.setattr(approvals, "_now", lambda: base - timedelta(hours=1))
    assert store.take_tool_approval(permission) is None
    # a recorded expiry further than ten minutes ahead of now is refused
    monkeypatch.setattr(approvals, "_now", lambda: base)
    rewrite(store, expires_at=(base + timedelta(hours=1)).isoformat())
    assert store.take_tool_approval(permission) is None
    rewrite(store, expires_at=(base + timedelta(minutes=10)).isoformat())
    monkeypatch.setattr(approvals, "_now", lambda: base + timedelta(minutes=10))        # the instant it expires
    assert store.take_tool_approval(permission) is None
    monkeypatch.setattr(approvals, "_now", lambda: base + timedelta(minutes=9))
    assert store.take_tool_approval(permission) is not None
    # a legacy event with no approved_at fails closed
    store2, _, _, _, _, permission2 = _granted_run(tmp_path / "b")
    event_id, data = payload(store2)
    data.pop("approved_at")
    with store2.db.tx() as conn:
        conn.execute("UPDATE run_events SET payload_json=? WHERE id=?", (json.dumps(data), event_id))
    assert store2.take_tool_approval(permission2) is None
    # the in-memory grant applies the same bounds with the injected clock
    monkeypatch.setattr(approvals, "_now", lambda: base)
    spec = invoker.registry.lookup("mock.delete").spec
    far = approvals._approval_from_decision(permission, base + timedelta(hours=2), base)
    assert not far.valid_for(spec, request)
    back = approvals._approval_from_decision(permission, base + timedelta(minutes=5), base + timedelta(minutes=1))
    assert not back.valid_for(spec, request)
    ok = approvals._approval_from_decision(permission, base + timedelta(minutes=5), base - timedelta(minutes=1))
    assert ok.valid_for(spec, request)


def test_b3_grant_is_immutable_single_use_and_exposes_no_approval(tmp_path):
    import copy
    import dataclasses
    import threading

    from multiagent.tools.approvals import ToolCallApproval

    store, service, invoker, request, count, permission = _granted_run(tmp_path)
    grant = store.take_tool_approval(permission)
    with pytest.raises(TypeError):
        dataclasses.replace(grant)                      # M19: replace() must not hand out a fresh use mark
    for name, value in (("_used", False), ("permission", None), ("_spend", None), ("approval", object())):
        with pytest.raises(AttributeError):
            setattr(grant, name, value)
    with pytest.raises(TypeError):
        copy.copy(grant)                                # no copy with a fresh use mark either
    assert not hasattr(grant, "approval") and not hasattr(grant, "__dict__")
    assert not any(isinstance(getattr(grant, name, None), Approval) for name in dir(grant) if not name.startswith("__"))
    assert invoker.invoke(request, grants={"tool:mock.delete"}, evidence=[grant]).ok
    for again in (grant,):
        assert invoker.invoke(request, grants={"tool:mock.delete"}, evidence=[again]).error.code == "approval_required"
    assert len(count) == 1
    assert type(grant) is ToolCallApproval

    # eight threads, one grant: one execution
    store2, _, invoker2, request2, count2, permission2 = _granted_run(tmp_path / "t")
    shared = store2.take_tool_approval(permission2)
    barrier = threading.Barrier(8)
    results = []

    def work():
        barrier.wait()
        results.append(invoker2.invoke(request2, grants={"tool:mock.delete"}, evidence=[shared]).ok)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1 and len(count2) == 1


def test_b3_mixed_write_and_destructive_grant_is_spent_once_and_is_no_write_evidence(tmp_path):
    from multiagent.document_actions import AuthorizationResult
    from multiagent.tools.permissions import actions_for, check_call

    store, service, invoker, request, count = setup(tmp_path)
    spec = invoker.registry.lookup("mock.delete").spec
    mixed = ToolSpec.model_validate({**spec.model_dump(mode="json"), "tool_id": "mock.mixed",
                                     "impacts": ["write", "destructive"],
                                     "effects": [{"kind": "write", "target_from": "/target"},
                                                 {"kind": "destructive", "target_from": "/target"}]})
    invoker.registry.register(mixed, lambda arguments, context, *, should_cancel: count.append(arguments) or {"text": "done"})
    call = request.model_copy(update={"tool_id": "mock.mixed"})
    denied = invoker.invoke(call, grants={"tool:mock.mixed"})
    assert denied.error.code == "approval_required"
    store.hold_tool_approval(denied.permission_request)
    service.approve(request.context.run_id, permission_request_id=denied.permission_request.permission_request_id, person=True)
    store.update_run(request.context.run_id, status="running")
    grant = store.take_tool_approval(denied.permission_request)
    assert grant is not None
    # nothing public or private hands out an Approval (no proof()), whatever it is called with
    assert not hasattr(grant, "proof")
    actions = actions_for(mixed, call.arguments)
    results = grant._decide(mixed, call, actions)         # really call it: it validates and spends
    assert results is not None and all(isinstance(r, AuthorizationResult) and r.allowed for r in results)
    assert not any(isinstance(r, Approval) for r in results)
    # spent once: neither the method nor check_call (as any evidence) lets it through again
    assert grant._decide(mixed, call, actions) is None
    for _ in range(3):
        code, _ = check_call(mixed, call, grants={"tool:mock.mixed"}, evidence=[grant])
        assert code == "approval_required"
    # and it is no WRITE evidence for a write-only tool of the same destination
    writer = ToolSpec.model_validate({**spec.model_dump(mode="json"), "tool_id": "mock.writer", "impacts": ["write"],
                                      "effects": [{"kind": "write", "target_from": "/target"}]})
    code, _ = check_call(writer, call.model_copy(update={"tool_id": "mock.writer"}), grants={"tool:mock.writer"},
                         evidence=[grant])
    assert code == "approval_required" and count == []


def test_approved_at_is_the_injected_clock_and_reaches_the_grant(tmp_path, monkeypatch):
    """Kills T12 (stamp from the wall clock) and T13 (grant built without approved_at)."""
    import json
    from datetime import datetime, timedelta, timezone

    from multiagent.tools import approvals

    fake = datetime(2031, 5, 6, 7, 8, 9, tzinfo=timezone.utc)
    monkeypatch.setattr(approvals, "_now", lambda: fake)
    store, service, invoker, request, count, permission = _granted_run(tmp_path)
    with store.db.connect() as conn:
        data = json.loads(conn.execute("SELECT payload_json FROM run_events WHERE event_type='tool_approval_approved'")
                          .fetchone()["payload_json"])
    assert datetime.fromisoformat(data["approved_at"]) == fake
    assert datetime.fromisoformat(data["expires_at"]) == fake + timedelta(minutes=10)
    monkeypatch.setattr(approvals, "_now", lambda: fake + timedelta(minutes=1))
    grant = store.take_tool_approval(permission)
    assert grant._approved_at == fake and grant._expires_at == fake + timedelta(minutes=10)
    with pytest.raises(TypeError):
        approvals._approval_from_decision(permission, fake + timedelta(minutes=5))     # approved_at is required


def test_b4_take_ignores_the_wording_but_not_the_context(tmp_path):
    from datetime import datetime, timedelta, timezone

    from multiagent.tools.approvals import _approval_from_decision

    store, service, invoker, request, count, permission = _granted_run(tmp_path / "a")
    reworded = permission.model_copy(update={"reason": "another wording", "summary": "other"})
    assert store.take_tool_approval(reworded) is not None
    store2, _, invoker2, request2, _, permission2 = _granted_run(tmp_path / "b")
    other_context = permission2.model_copy(update={"context": permission2.context.model_copy(update={"data_class": "secret"})})
    assert store2.take_tool_approval(other_context) is None
    assert store2.take_tool_approval(permission2) is not None
    spec = invoker.registry.lookup("mock.delete").spec

    def fresh():
        return _approval_from_decision(permission, datetime.now(timezone.utc) + timedelta(minutes=5), datetime.now(timezone.utc) - timedelta(seconds=1))

    assert fresh().valid_for(spec, request)
    for update in ({"data_class": "secret"}, {"ordinal": 3}, {"profile_id": "p"}, {"model_call_ordinal": 4}):
        changed = request.model_copy(update={"context": request.context.model_copy(update=update)})
        assert not fresh().valid_for(spec, changed), update
    assert not fresh().valid_for(spec, request.model_copy(update={"origin": ToolOrigin(kind="model_output", ref="model:2")}))
    assert not fresh().valid_for(spec, request.model_copy(update={"origin": ToolOrigin(kind="user")}))


def test_b6_check_call_rejects_non_collections_as_evidence(tmp_path):
    from multiagent.tools.permissions import check_call

    store, service, invoker, request, count, permission = _granted_run(tmp_path)
    grant = store.take_tool_approval(permission)
    spec = invoker.registry.lookup("mock.delete").spec
    proof = Approval(Provenance(SourceKind.USER, ref="u"), frozenset({ActionKind.DESTRUCTIVE}), frozenset({"synthetic.txt"}))
    for bad in ({proof: "x"}, "text", b"bytes", proof, 7, grant, object()):
        code, requested = check_call(spec, request, grants={"tool:mock.delete"}, evidence=bad)
        assert code == "approval_required" and requested is not None, bad
        assert requested.reason == "malformed approval evidence"
    assert grant.valid_for(spec, request)               # none of those calls spent the real grant


def test_b7_take_needs_a_running_run(tmp_path):
    store, service, invoker, request, count, permission = _held(tmp_path)
    run_id = request.context.run_id
    service.approve(run_id, permission_request_id=permission.permission_request_id, person=True)
    assert store.get_run(run_id)["status"] == "queued"
    assert store.take_tool_approval(permission) is None          # QUEUED is not enough
    store.update_run(run_id, status="running")
    assert store.take_tool_approval(permission) is not None


def test_b8_reject_follows_the_budget_cut_pattern(tmp_path):
    store, service, invoker, request, count, permission = _held(tmp_path)
    run_id = request.context.run_id
    store.finish_tool_approval(store.get_run(run_id), approve=False, feedback="no", by="x" * 500, channel="smtp")
    run = store.get_run(run_id)
    by_type = {e["event_type"]: e["payload"] for e in run["events"]}
    assert run["status"] == "failed"
    assert by_type["workflow_failed"]["code"] == "denied" and by_type["workflow_failed"]["type"] == "ToolApprovalRejected"
    rejected = by_type["tool_approval_rejected"]
    assert "expires_at" not in rejected and "approved_at" not in rejected
    assert rejected["by"] == "api" and rejected["channel"] == "api"        # normalized, as resolution_source does
    store2, service2, _, request2, _, permission2 = _held(tmp_path / "g")
    store2.finish_tool_approval(store2.get_run(request2.context.run_id), approve=True,
                                permission_request_id=permission2.permission_request_id, by="Ana", channel="gui")
    approved = [e["payload"] for e in store2.get_run(request2.context.run_id)["events"]
                if e["event_type"] == "tool_approval_approved"][0]
    assert approved["by"] == "Ana" and approved["channel"] == "gui"


def test_b9_finish_ignores_a_request_of_another_step_or_attempt(tmp_path):
    from multiagent.tools.approvals import ToolApprovalMismatch

    # M28: the only REQUESTED event belongs to another step
    store, service, invoker, request, count, permission = _held(tmp_path)
    run_id = request.context.run_id
    other = permission.model_copy(update={"context": permission.context.model_copy(update={"step_id": "elsewhere"})})
    with store.db.tx() as conn:
        conn.execute("UPDATE run_events SET payload_json=? WHERE event_type='tool_approval_requested'",
                     (other.model_dump_json(),))
    with pytest.raises(ToolApprovalMismatch) as err:
        service.approve(run_id, permission_request_id=other.permission_request_id, person=True)
    assert err.value.code == "no_pending_tool_approval"
    # same for another attempt
    other = permission.model_copy(update={"context": permission.context.model_copy(update={"attempt": 5})})
    with store.db.tx() as conn:
        conn.execute("UPDATE run_events SET payload_json=? WHERE event_type='tool_approval_requested'",
                     (other.model_dump_json(),))
    with pytest.raises(ToolApprovalMismatch):
        service.approve(run_id, permission_request_id=other.permission_request_id, person=True)
    assert store.get_run(run_id)["status"] == "waiting_human"


def test_b9_hold_needs_a_running_run_of_that_step(tmp_path):
    from multiagent.errors import InvalidStateTransition

    store, service, invoker, request, count = setup(tmp_path)
    denied = invoker.invoke(request, grants={"tool:mock.delete"})
    run_id = request.context.run_id
    for status in ("queued", "waiting_human", "failed", "completed"):      # M29: anything but RUNNING
        store.update_run(run_id, status=status)
        with pytest.raises(InvalidStateTransition):
            store.hold_tool_approval(denied.permission_request)
    store.update_run(run_id, status="running", cancel_requested=1)
    with pytest.raises(InvalidStateTransition):
        store.hold_tool_approval(denied.permission_request)
    store.update_run(run_id, status="running", cancel_requested=0)
    store.hold_tool_approval(denied.permission_request)
    assert store.get_run(run_id)["waiting_reason"] == "tool_approval"


def test_b9_a_write_grant_never_covers_a_destructive_action():
    """M11, directly on authorize: a WRITE approval does not authorize DESTRUCTIVE, whatever the target."""
    write = Approval(Provenance(SourceKind.USER, ref="u"), frozenset({ActionKind.WRITE}))
    scoped = Approval(Provenance(SourceKind.USER, ref="u"), frozenset({ActionKind.WRITE}), frozenset({"a.txt"}))
    for proof in (write, scoped):
        assert not authorize(Action(ActionKind.DESTRUCTIVE, "a.txt"), [proof]).allowed
        assert not authorize(Action(ActionKind.DESTRUCTIVE, None), [proof]).allowed
    assert authorize(Action(ActionKind.WRITE, "a.txt"), [scoped]).allowed


def test_b11_invisible_destinations_cannot_be_requested(tmp_path):
    store, service, invoker, request, count = setup(tmp_path)

    def call(target, ordinal):
        risky = request.model_copy(update={"request_id": request_id_for(request.context, ordinal),
                                           "arguments": {"target": target, "text": "x"}})
        return invoker.invoke(risky, grants={"tool:mock.delete"})

    bad = ["notes\u200b.txt", "notes\u3164.txt", "notes\u202e.txt", "notes\u2028.txt", "a\u0085b", "notes\u034f.txt",
           "notes\u2800.txt", "notes\ue000.txt", "notes\u0378.txt", "notes\uffa0.txt",
           "notes\u115f.txt", "notes\u1160.txt", "notes\u17b4.txt", "notes\u17b5.txt", "notes\u180e.txt", "notes\u2029.txt"]
    for n, target in enumerate(bad, 1):
        result = call(target, n)
        assert result.error.code == "invalid_arguments" and result.permission_request is None, ascii(target)
    legit = call("cafe\u0301.txt", 40)                                 # combining accent (Mn): fine
    assert legit.error.code == "approval_required"
    assert legit.permission_request.actions[0].target == "cafe\u0301.txt"
    assert call("\u65e5\u672c\u8a9e.txt", 41).error.code == "approval_required"       # CJK (Lo): fine
    assert call("plain.txt", 42).error.code == "approval_required"


def test_n11_spec_hash_is_wired_into_the_permission_request_id(tmp_path):
    store, service, invoker, request, count = setup(tmp_path)
    one = invoker.invoke(request, grants={"tool:mock.delete"}).permission_request
    spec = invoker.registry.lookup("mock.delete").spec
    other_registry = ToolRegistry()
    other_registry.register(spec.model_copy(update={"title": "A different title"}),
                            lambda arguments, context, *, should_cancel: {"text": "done"})
    two = ToolInvoker(other_registry).invoke(request, grants={"tool:mock.delete"}).permission_request
    assert one.spec_sha256 != two.spec_sha256 and one.permission_request_id != two.permission_request_id


def test_n08_cli_show_output_is_ascii_with_a_non_ascii_destination(route, monkeypatch, capsys):
    from multiagent import cli

    risky = route.request.model_copy(update={"request_id": request_id_for(route.request.context, 9),
                                             "arguments": {"target": "caf\u00e9-\u65e5\u672c.txt", "text": "x"}})
    held = route.invoker.invoke(risky, grants={"tool:mock.delete"}).permission_request
    route.store.update_run(route.run_id, status="running")
    route.store.hold_tool_approval(held)

    class Ctx:
        def __enter__(self):
            return route.client

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(cli, "api_client", lambda: Ctx())
    monkeypatch.setattr(cli, "get_settings", lambda: route.settings)
    with pytest.raises(SystemExit) as shown_exit:
        cli.main(["approve", route.run_id])
    assert shown_exit.value.code == 2
    out = capsys.readouterr().out
    assert out.isascii() and held.permission_request_id in out
    assert "caf\\u00e9" in out                       # shown escaped, never as raw text
    assert route.store.get_run(route.run_id)["status"] == "waiting_human"
