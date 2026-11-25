# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""ACP initialize probing stays bounded and never sends a session or prompt request."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from multiagent.executors import process
from multiagent.executors import acp_probe
from multiagent.executors.acp_probe import (
    PROBE_OUTPUT_CAP_BYTES,
    PROBE_TIMEOUT_SECONDS,
    probe_codex_acp,
    resolve_codex_acp,
)

EXECUTABLE = r"C:\synthetic\codex-acp.exe"


def outcome(stdout="", *, timed_out=False, cancelled=False, stdout_truncated=False, stderr_truncated=False):
    return SimpleNamespace(stdout_tail=stdout, stderr_tail="", timed_out=timed_out, cancelled=cancelled,
                           stdout_truncated=stdout_truncated, stderr_truncated=stderr_truncated, exit_code=0)


def response(*, protocol_version=1, request_id=1):
    return json.dumps({
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "protocolVersion": protocol_version,
            "agentCapabilities": {"loadSession": True, "promptCapabilities": {"image": False}},
            "authMethods": [{"id": "test-auth", "name": "Synthetic"}],
            "agentInfo": {"name": "Synthetic ACP", "version": "1.2.3"},
            "untrustedExtra": "must not pass through",
        },
    }) + "\n"


def test_initialize_sends_only_one_fixed_request_and_returns_announced_facts(tmp_path):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return outcome(response())

    result = probe_codex_acp(EXECUTABLE, cwd=tmp_path, run=run)
    assert result == {
        "status": "compatible",
        "protocol_version": 1,
        "capabilities": {"loadSession": True, "promptCapabilities": {"image": False}},
        "auth_methods": [{"id": "test-auth", "name": "Synthetic"}],
        "agent_info": {"name": "Synthetic ACP", "version": "1.2.3"},
    }
    argv, kwargs = calls[0]
    request = json.loads(kwargs["stdin_text"])
    assert argv == [EXECUTABLE]
    assert request == {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": 1, "clientCapabilities": {}},
    }
    assert kwargs["timeout_seconds"] == PROBE_TIMEOUT_SECONDS
    assert kwargs["output_cap_bytes"] == PROBE_OUTPUT_CAP_BYTES
    assert kwargs["env_extra"]["PATH"] == os.pathsep.join(acp_probe._safe_path_entries(tmp_path))
    assert len(calls) == 1


def test_unsupported_protocol_is_incompatible_and_does_not_claim_capabilities(tmp_path):
    result = probe_codex_acp(EXECUTABLE, cwd=tmp_path, run=lambda *args, **kwargs: outcome(response(protocol_version=2)))
    assert result == {"status": "incompatible", "protocol_version": 2}


@pytest.mark.parametrize(("stdout", "expected"), [
    ("not json\n", "invalid_json"),
    (response(request_id=2), "unmatched_id"),
    ('{"jsonrpc":"1.0","id":1,"result":{}}\n', "invalid_jsonrpc"),
    ('{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":1}}\n', "invalid_capabilities"),
])
def test_malformed_or_uncorrelated_responses_are_rejected(tmp_path, stdout, expected):
    result = probe_codex_acp(EXECUTABLE, cwd=tmp_path, run=lambda *args, **kwargs: outcome(stdout))
    assert result == {"status": "invalid_response", "reason": expected}
    assert stdout.strip() not in json.dumps(result)


def test_timeout_and_excess_output_are_distinct_failures(tmp_path):
    timeout = probe_codex_acp(
        EXECUTABLE, cwd=tmp_path, run=lambda *args, **kwargs: outcome(timed_out=True))
    response_before_timeout = probe_codex_acp(
        EXECUTABLE, cwd=tmp_path,
        run=lambda *args, **kwargs: outcome(response(), timed_out=True))
    oversized = probe_codex_acp(
        EXECUTABLE, cwd=tmp_path,
        run=lambda *args, **kwargs: outcome("x" * (PROBE_OUTPUT_CAP_BYTES + 1)))
    truncated = probe_codex_acp(
        EXECUTABLE, cwd=tmp_path, run=lambda *args, **kwargs: outcome("{}", stdout_truncated=True))
    assert timeout == {"status": "no_response", "reason": "timeout"}
    assert response_before_timeout["status"] == "compatible"
    assert oversized == truncated == {"status": "invalid_response", "reason": "output_too_large"}


def test_missing_executable_is_not_launched(tmp_path):
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("an absent executable must not be launched")

    assert probe_codex_acp(None, cwd=tmp_path, run=forbidden) == {"status": "not_installed"}
    assert called is False


def test_executable_detection_skips_working_folder_and_worktree_before_installed_path(tmp_path, monkeypatch):
    working = tmp_path / "working"
    installed = tmp_path / "installed"
    working.mkdir()
    installed.mkdir()
    worktree = Path(acp_probe.__file__).resolve().parents[2]
    monkeypatch.setenv("PATH", os.pathsep.join((str(working), str(worktree), str(installed))))
    searched = []

    def which(name, *, path):
        assert name == "codex-acp"
        searched.append(path)
        return str(installed / "codex-acp.exe")

    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(process, "resolve_executable", lambda name, cwd: name)
    assert resolve_codex_acp(working) == str(installed / "codex-acp.exe")
    assert searched == [str(installed)]


def test_probe_clears_inherited_secret_values_and_untrusted_path(tmp_path, monkeypatch):
    untrusted = tmp_path / "working"
    installed = tmp_path / "installed"
    untrusted.mkdir()
    installed.mkdir()
    monkeypatch.setenv("PATH", os.pathsep.join((str(untrusted), str(installed))))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret")
    monkeypatch.setenv("CUSTOM_PRIVATE_CONTEXT", "synthetic-secret")
    monkeypatch.setenv("CLAUDE_API_KEY", "synthetic-secret")
    captured = {}

    def run(argv, **kwargs):
        captured.update(kwargs)
        return outcome(response())

    assert probe_codex_acp(EXECUTABLE, cwd=untrusted, run=run)["status"] == "compatible"
    assert captured["env_extra"]["OPENAI_API_KEY"] == ""
    assert captured["env_extra"]["CUSTOM_PRIVATE_CONTEXT"] == ""
    assert "CLAUDE_API_KEY" not in captured["env_extra"]  # process.build_environment removes it
    assert captured["env_extra"]["PATH"] == str(installed)
