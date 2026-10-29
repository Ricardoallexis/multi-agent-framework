# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""CNT-T13: fake clock, conditions, processes and resumes only; no executor CLI."""
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from multiagent.cli import build_parser
from multiagent.services.watcher import INTERVAL_SECONDS, WatcherProcess


@pytest.fixture
def world(tmp_path):
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    calls = []
    wait = {"task_id": "T1", "hold_id": "h1", "attempt": 1, "executor_id": "fake", "due": False, "state": "waiting"}
    result = {"resumed": [{"task_id": "T1"}]}
    service = SimpleNamespace(check_waits=lambda **kw: {"waits": [dict(wait)]},
                              resume_met=lambda **kw: calls.append(kw) or result)
    blocked = [False]
    watcher = WatcherProcess(tmp_path, service, integration_marker=tmp_path / "integration.lock", integration_active=lambda: blocked[0], clock=lambda: now[0],
                             alive=lambda pid: pid == 123, pid=123)
    return SimpleNamespace(w=watcher, now=now, calls=calls, wait=wait, result=result, service=service, blocked=blocked,
                           root=tmp_path)


def claim(world):
    return world.w.claim("T1", "h1", 1)


def test_lease_is_exclusive_and_finished_state_cannot_reset_budget(world):
    claim(world)
    other = WatcherProcess(world.root, world.service, integration_marker=world.root / "integration.lock", integration_active=lambda: False)
    with pytest.raises(FileExistsError):
        other.claim("T1", "h1", 1)
    world.wait["due"] = True
    assert world.w.tick()["state"] == "resumed"
    with pytest.raises(FileExistsError):
        other.claim("T1", "h1", 2)
    assert len(world.calls) == 1
    assert world.w.tick()["state"] == "resumed" and len(world.calls) == 1


@pytest.mark.parametrize("task,hold,attempt", [("../bad", "h", 1), ("T1", "../bad", 1), ("T1", "h", 0),
                                               ("T1", "h", True)])
def test_identifiers_never_become_paths_or_commands(world, task, hold, attempt):
    with pytest.raises(ValueError):
        world.w.claim(task, hold, attempt)
    assert not list(world.root.iterdir())


def test_heartbeat_advances_even_when_reader_fails(world):
    claim(world)
    world.now[0] += timedelta(seconds=120)
    world.service.check_waits = lambda **kw: (_ for _ in ()).throw(OSError("synthetic"))
    state = world.w.tick()
    assert state["checked_at"] == world.now[0].isoformat()
    assert state["error"] == "OSError" and state["failed_restarts"] == 0
    assert not world.calls


@pytest.mark.parametrize("change", [{"hold_id": "h2"}, {"attempt": 2}])
def test_changed_hold_or_attempt_never_resumes(world, change):
    claim(world)
    world.wait.update(due=True, **change)
    assert world.w.tick()["state"] == "stopped"
    assert not world.calls


def test_task_no_longer_held_is_terminal(world):
    claim(world)
    world.service.check_waits = lambda **kw: {"waits": []}
    assert world.w.tick()["reason"] == "task_not_held"
    assert not world.calls


@pytest.mark.parametrize("reason", ["assignee_changed", "blocked:resume_failed", "session_open_in_app"])
def test_refusals_are_attention_and_never_retried(world, reason):
    claim(world)
    world.wait.update(state="skipped", why=reason, due=True)
    assert world.w.tick()["state"] == "attention"
    assert not world.calls


def test_integration_blocks_and_is_checked_twice(world):
    claim(world)
    world.wait["due"] = True
    world.blocked[0] = True
    assert world.w.tick()["reason"] == "integration_active" and not world.calls
    checks = iter([False, True])
    world.w.integration_active = lambda: next(checks)
    assert world.w.tick()["reason"] == "integration_active" and not world.calls
    assert not list(world.root.glob("executor-*.lock"))


def test_broken_integration_guard_fails_closed(world):
    claim(world)
    world.wait["due"] = True
    world.w.integration_active = lambda: (_ for _ in ()).throw(OSError())
    assert world.w.tick()["reason"] == "integration_active" and not world.calls


def test_resume_passes_exact_identity_and_system_actor(world):
    state = claim(world)
    world.wait["due"] = True
    assert world.w.tick()["state"] == "resumed"
    assert world.calls == [{"task_id": "T1", "hold_id": "h1", "attempt": 1, "limit": 1, "actor": "system:watcher"}]
    assert state["actor"] == "system:watcher"


def test_three_failed_resumes_have_exponential_backoff_then_attention(world):
    claim(world)
    world.wait["due"] = True
    def fail(**kw):
        world.calls.append(kw)
        raise OSError("private synthetic exception text must not enter state")
    world.service.resume_met = fail
    first = world.w.tick()
    assert first["failed_restarts"] == 1 and first["state"] == "waiting"
    assert datetime.fromisoformat(first["retry_at"]) == world.now[0] + timedelta(seconds=120)
    world.w.tick()
    assert len(world.calls) == 1
    world.now[0] += timedelta(seconds=120)
    second = world.w.tick()
    assert datetime.fromisoformat(second["retry_at"]) == world.now[0] + timedelta(seconds=240)
    world.now[0] += timedelta(seconds=240)
    assert world.w.tick()["reason"] == "restart_limit"
    assert len(world.calls) == 3
    world.w.tick()
    assert len(world.calls) == 3
    assert "private synthetic" not in world.w.path.read_text()


def test_refused_session_after_recheck_does_not_loop(world):
    claim(world)
    world.wait["due"] = True
    world.result.clear()
    world.result["skipped"] = [{"why": "session_open_in_app"}]
    assert world.w.tick()["reason"] == "session_open_in_app"
    world.w.tick()
    assert len(world.calls) == 1


def test_sweeper_requires_dead_process_and_stale_heartbeat(world):
    claim(world)
    world.now[0] += timedelta(seconds=2 * INTERVAL_SECONDS + 1)
    assert world.w.sweep() == []  # stale but still alive
    world.w.alive = lambda pid: False
    assert world.w.sweep() == [world.w.state["id"]]
    assert json.loads(world.w.path.read_text())["state"] == "lost"
    assert not world.calls
    with pytest.raises(RuntimeError, match="lost"):
        world.w.tick()


def test_dead_process_with_recent_heartbeat_is_not_swept(world):
    claim(world)
    world.w.alive = lambda pid: False
    assert world.w.sweep() == []


def test_executor_lease_serializes_watchers_and_sweeper_releases_dead_owner(world):
    claim(world)
    world.wait["due"] = True
    from multiagent.services.watcher import _key
    lock = world.root / ("executor-" + _key("fake") + ".lock")
    lock.write_text(json.dumps({"pid": 123, "owner": "another"}))
    assert world.w.tick()["reason"] == "executor_busy" and not world.calls
    world.w.sweep()
    assert lock.exists()
    lock.write_text(json.dumps({"pid": 456, "owner": "dead"}))
    world.w.sweep()
    assert not lock.exists()
    assert world.w.tick()["state"] == "resumed"


def test_local_deadline_expires_even_with_failed_condition_reader(world):
    world.w.claim("T1", "h1", 1, max_hours=1)
    world.now[0] += timedelta(hours=1)
    assert world.w.tick()["reason"] == "watcher_deadline" and not world.calls


def test_ctrl_c_stops_only_watcher(world):
    claim(world)
    def interrupt(seconds):
        raise KeyboardInterrupt
    assert world.w.run(sleep=interrupt)["reason"] == "operator_stop"
    assert not world.calls


def test_ctrl_c_during_resume_reports_unknown_and_never_retries(world):
    claim(world)
    world.wait["due"] = True
    def interrupt(**kw):
        world.calls.append(kw)
        raise KeyboardInterrupt
    world.service.resume_met = interrupt
    state = world.w.run()
    assert (state["state"], state["reason"]) == ("attention", "resume_outcome_unknown")
    assert json.loads(world.w.path.read_text())["reason"] == "resume_outcome_unknown"
    world.w.tick()
    assert len(world.calls) == 1


def test_missing_marker_parent_does_not_attempt_resume(world):
    claim(world)
    world.wait["due"] = True
    world.w.integration_marker = world.root / "missing" / "integration.lock"
    assert world.w.tick()["reason"] == "integration_marker_unavailable"
    assert not world.calls and world.w.state["failed_restarts"] == 0


@pytest.mark.parametrize("operation", ["replace", "unlink"])
def test_transient_windows_permission_errors_are_retried(world, monkeypatch, operation):
    claim(world)
    world.wait["due"] = True
    calls = []
    target = os if operation == "replace" else Path
    original = getattr(target, operation)
    def busy(*args, **kwargs):
        calls.append(1)
        if len(calls) <= 2:
            raise PermissionError("synthetic reader")
        return original(*args, **kwargs)
    monkeypatch.setattr(target, operation, busy)
    monkeypatch.setattr("multiagent.services.watcher.time.sleep", lambda _: None)
    assert world.w.tick()["state"] == "resumed"
    assert len(calls) >= 3 and len(world.calls) == 1


def test_permanent_permission_error_is_bounded_and_run_returns_attention(world, monkeypatch):
    from multiagent.services.watcher import IO_ATTEMPTS
    claim(world)
    calls = []
    def busy(*args):
        calls.append(1)
        raise PermissionError("synthetic permanent failure")
    monkeypatch.setattr(os, "replace", busy)
    monkeypatch.setattr("multiagent.services.watcher.time.sleep", lambda _: None)
    state = world.w.run()
    assert state["state"] == "attention" and state["reason"] == "state_io_error"
    assert state["persistence_error"] is True
    assert len(calls) == 2 * IO_ATTEMPTS  # heartbeat and best-effort terminal write
    assert not world.calls
    assert not list(world.root.glob("*.tmp"))


def test_run_catches_sweep_oserror_and_persists_attention(world, monkeypatch):
    claim(world)
    def fail():
        raise OSError("synthetic")
    monkeypatch.setattr(world.w, "sweep", fail)
    assert world.w.run()["reason"] == "state_io_error"
    assert json.loads(world.w.path.read_text())["state"] == "attention"
    assert not world.calls


def test_cli_requires_identity_and_explicit_integration_marker():
    parser = build_parser()
    args = parser.parse_args(["watcher", "run", "--task", "T1", "--hold", "h1", "--attempt", "1",
                              "--integration-marker", "synthetic.lock"])
    assert args.watcher_action == "run" and args.attempt == 1
    with pytest.raises(SystemExit):
        parser.parse_args(["watcher", "run", "--task", "T1", "--hold", "h1", "--attempt", "1"])


def test_integration_marker_is_held_exclusively_during_resume(world):
    claim(world)
    world.wait["due"] = True
    def resume(**kw):
        with pytest.raises(FileExistsError):
            world.w.integration_marker.open("x")
        return {"resumed": [{}]}
    world.service.resume_met = resume
    assert world.w.tick()["state"] == "resumed"
    assert not world.w.integration_marker.exists()


def test_sweeper_clears_only_own_dead_watcher_integration_gate(world):
    state = claim(world)
    marker = world.w.integration_marker
    marker.write_text(json.dumps({"schema": "multiagent.watcher_gate/1", "owner": state["owner"], "pid": 123}))
    world.now[0] += timedelta(seconds=241)
    world.w.sweep()
    assert marker.exists()  # owner still alive
    world.w.alive = lambda pid: False
    world.w.sweep()
    assert not marker.exists()
    marker.write_text(json.dumps({"schema": "integration-controller/1", "owner": state["owner"], "pid": 123}))
    world.w.sweep()
    assert marker.exists()  # controller-owned markers are never removed


def test_cleanup_failure_after_resume_never_makes_another_resumption(world, monkeypatch):
    claim(world)
    world.wait["due"] = True
    unlink = Path.unlink
    def fail_marker(path, *args, **kwargs):
        if path == world.w.integration_marker:
            raise OSError("synthetic cleanup failure")
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_marker)
    assert world.w.tick()["reason"] == "resume_outcome_unknown"
    world.w.tick()
    assert len(world.calls) == 1


@pytest.fixture
def channel(tmp_path):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 is needed for the channel watcher")
    script = tmp_path / "esperar-condicion.ps1"
    shutil.copyfile(Path(__file__).resolve().parents[1] / "tools/seguimiento/esperar-condicion.ps1", script)
    (tmp_path / "bloqueo.ps1").write_text('''
$script:lockFile = Join-Path $PSScriptRoot '.canal.lock'
function Enter-ChannelLock($owner, $seconds) {
  $script:handle = [IO.File]::Open($script:lockFile, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
}
function Exit-ChannelLock {
  $script:handle.Dispose()
  Remove-Item -LiteralPath $script:lockFile
}
''', encoding="utf-8")
    cards = tmp_path / "tareas/AAA"
    cards.mkdir(parents=True)
    card = cards / "T01-synthetic.md"
    card.write_text("| Estado | pendiente |\n| Agente | Synthetic-Agent |\n| Espera | tarea AAA-T02 — condición sintética |\n", encoding="utf-8")
    args = [pwsh, "-NoProfile", "-File", str(script)]
    watch = ["-Tipo", "hora", "-Valor", "2000-01-01T00:00:00", "-Accion", "avisar", "-Motivo", "synthetic",
             "-Tarea", "AAA-T01", "-Agente", "Synthetic-Agent", "-IntervaloSegundos", "1", "-MaximoHoras", "0.001"]
    return SimpleNamespace(root=tmp_path, script=script, args=args, watch=watch, card=card)


def invoke_channel(channel, args):
    return subprocess.run([*channel.args, *args], text=True, capture_output=True, timeout=15, encoding="utf-8")


def test_channel_probe_is_read_only_and_arbitrary_scripts_are_rejected(channel):
    before = sorted(str(p.relative_to(channel.root)) for p in channel.root.rglob("*"))
    answer = invoke_channel(channel, [*channel.watch, "-Probar"])
    assert answer.returncode == 0, answer.stderr
    assert json.loads(answer.stdout)["accion"] == "avisar"
    assert sorted(str(p.relative_to(channel.root)) for p in channel.root.rglob("*")) == before
    bad = list(channel.watch)
    bad[bad.index("avisar")] = "untrusted.ps1"
    assert invoke_channel(channel, [*bad, "-Probar"]).returncode != 0


def test_channel_ready_notice_is_fixed_and_duplicate_hold_is_refused(channel):
    answer = invoke_channel(channel, channel.watch)
    assert answer.returncode == 0, answer.stderr
    ready = list((channel.root / "orquestacion/esperas/listas").glob("*.json"))
    assert len(ready) == 1
    payload = json.loads(ready[0].read_text(encoding="utf-8-sig"))
    assert payload["accion"] == "avisar" and payload["agente"] == "Synthetic-Agent"
    assert invoke_channel(channel, channel.watch).returncode != 0


def test_channel_integration_round_blocks_even_when_condition_holds(channel):
    rounds = channel.root / "parches"
    rounds.mkdir()
    (rounds / "ronda.txt").write_text("synthetic.patch", encoding="utf-8")
    answer = invoke_channel(channel, channel.watch)
    assert answer.returncode == 2, answer.stderr
    assert not (channel.root / "orquestacion/esperas/listas").exists()


def test_channel_sweeper_needs_stale_heartbeat_and_missing_process(channel):
    active = channel.root / "orquestacion/esperas/activas"
    active.mkdir(parents=True)
    sample = {"id": "synthetic", "estado": "vigilando", "ultima_comprobacion": "2000-01-01T00:00:00",
              "intervalo_segundos": 1, "pid": os.getpid()}
    live = active / "live.json"
    dead = active / "dead.json"
    live.write_text(json.dumps(sample), encoding="utf-8")
    dead.write_text(json.dumps({**sample, "pid": 99999999}), encoding="utf-8")
    answer = invoke_channel(channel, ["-Barrido"])
    assert answer.returncode == 0, answer.stderr
    assert json.loads(live.read_text())["estado"] == "vigilando"
    assert json.loads(dead.read_text())["estado"] == "perdida"


def test_channel_changed_wait_stops_before_notification(channel):
    marker = channel.root / "condition.ready"
    args = list(channel.watch)
    args[args.index("hora")] = "archivo"
    args[args.index("2000-01-01T00:00:00")] = str(marker)
    process = subprocess.Popen([*channel.args, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        end = time.monotonic() + 5
        while not list((channel.root / "orquestacion/esperas/activas").glob("*.json")) and time.monotonic() < end:
            time.sleep(0.05)
        channel.card.write_text(channel.card.read_text(encoding="utf-8").replace("AAA-T02", "AAA-T03"), encoding="utf-8")
        marker.touch()
        out, error = process.communicate(timeout=10)
        assert process.returncode == 3, (out, error)
        assert not (channel.root / "orquestacion/esperas/listas").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_channel_rejects_empty_conditions(channel):
    answer = invoke_channel(channel, ["-Tipo", ",,", "-Valor", ";;", "-Accion", "avisar", "-Motivo", "synthetic", "-Probar"])
    assert answer.returncode != 0


def test_channel_rechecks_conditions_inside_lock(channel):
    marker = channel.root / "condition.ready"
    marker.touch()
    lock_script = channel.root / "bloqueo.ps1"
    lock_script.write_text(lock_script.read_text() + "\nfunction Enter-ChannelLock($owner, $seconds) { Remove-Item -LiteralPath '" +
                           str(marker).replace("'", "''") + "' -ErrorAction SilentlyContinue }\nfunction Exit-ChannelLock {}\n")
    args = list(channel.watch)
    args[args.index("hora")] = "archivo"
    args[args.index("2000-01-01T00:00:00")] = str(marker)
    answer = invoke_channel(channel, args)
    assert answer.returncode == 2, answer.stderr
    assert not (channel.root / "orquestacion/esperas/listas").exists()


def test_channel_checks_deadline_again_after_lock_wait(channel):
    lock_script = channel.root / "bloqueo.ps1"
    lock_script.write_text(lock_script.read_text() + "\nfunction Enter-ChannelLock($owner, $seconds) { Start-Sleep -Seconds 4 }\nfunction Exit-ChannelLock {}\n")
    answer = invoke_channel(channel, channel.watch)
    assert answer.returncode == 2, answer.stderr
    assert not (channel.root / "orquestacion/esperas/listas").exists()


def test_channel_post_close_failure_never_republishes_or_reopens_state(channel):
    import hashlib
    notice_id = "ESP-" + hashlib.sha256(b"AAA-T01/channel").hexdigest()[:24]
    ready = channel.root / "orquestacion/esperas/listas"
    ready.mkdir(parents=True)
    notice = ready / (notice_id + ".json")
    notice.write_text("preexisting synthetic marker")
    answer = invoke_channel(channel, channel.watch)
    assert answer.returncode == 3, answer.stderr
    assert notice.read_text() == "preexisting synthetic marker"
    closed = channel.root / "orquestacion/esperas/terminadas" / (notice_id + ".json")
    assert json.loads(closed.read_text(encoding="utf-8-sig"))["estado"] == "atencion"
    assert not list((channel.root / "orquestacion/esperas/activas").glob("*.json"))
