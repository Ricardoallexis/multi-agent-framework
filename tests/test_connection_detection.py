"""Connection detection snapshots are cached in memory and refreshed per app on request."""
from __future__ import annotations

import importlib
import time
from pathlib import Path

from test_connections import fixture


OBSERVED_AT = "2026-11-25T17:00:00+00:00"


def no_requirements(cwd, steps):
    raise AssertionError("connection detection does not collect setup requirements")


def _report(app=None, *, version_result="observed"):
    names = (app,) if app else ("claude", "codex", "copilot", "agy")
    return {
        "observed_at": OBSERVED_AT,
        "contexts": {
            "packaged_process": {
                "apps": [
                    {
                        "app": name,
                        "context": "packaged_process",
                        "detection": "detected",
                        "executable_found": True,
                        "windows_package_path": False,
                        "version": "1.2.3" if version_result == "observed" else None,
                        "version_result": version_result,
                        "version_observed_at": OBSERVED_AT,
                        "session": {"state": "signed_in"},
                    }
                    for name in names
                ]
            }
        },
    }


def test_list_caches_snapshot_and_check_remeasures_only_requested_app():
    service = fixture()
    calls = []

    def detector(**kwargs):
        calls.append(kwargs)
        return _report(kwargs.get("app"))

    service.detector = detector
    first = service.list()
    assert len(calls) == 1 and calls[0].get("app") is None
    assert first["detection_cache"] == {"scope": "service_memory", "observed_at": OBSERVED_AT}
    codex = next(row for row in first["connections"] if row["id"] == "codex_cli")
    assert codex["detection_context"] == "packaged_process"
    assert codex["detection_observed_at"] == OBSERVED_AT

    service.list()
    assert len(calls) == 1

    refreshed = service.check("codex_cli")
    assert len(calls) == 2 and calls[1]["app"] == "codex"
    assert refreshed["version_result"] == "observed"
    assert refreshed["detection_observed_at"] == OBSERVED_AT


def test_version_failure_is_reported_without_marking_executor_available():
    service = fixture()
    service.detector = lambda **kwargs: _report(kwargs.get("app"), version_result="timed_out")

    row = service.check("claude_cli")

    assert row["executable_found"] is True and row["installed"] is True
    assert row["version"] is None and row["version_result"] == "timed_out"
    assert row["reason"] == "version_check_failed" and row["available"] is False


def test_list_measures_apps_in_parallel_and_matches_serial_results(monkeypatch):
    detection = importlib.import_module("multiagent.setup.detect")
    service = fixture()

    def fake_app(app, cwd, declared_path, steps):
        time.sleep(0.3)
        return {
            "app": app,
            "detection": "detected",
            "executable_found": True,
            "context": steps.context,
            "windows_package_path": False,
            "version": "1.2.3",
            "version_result": "observed",
            "session": {"state": "signed_in"},
        }

    monkeypatch.setattr(detection, "_app", fake_app)
    monkeypatch.setattr(detection, "_requirements", no_requirements)
    service.detector = lambda **kwargs: detection.detect(**kwargs, system="Windows")

    started = time.perf_counter()
    listed = service.list()
    elapsed = time.perf_counter() - started

    assert elapsed < 0.6
    serial = detection.detect(
        cwd=Path.cwd(),
        context="packaged_process",
        include_antigravity=True,
        include_requirements=False,
        system="Windows",
    )
    parallel = detection.detect(
        cwd=Path.cwd(),
        context="packaged_process",
        include_antigravity=True,
        include_requirements=False,
        parallel_apps=True,
        system="Windows",
    )
    assert parallel["contexts"]["packaged_process"]["apps"] == serial["contexts"]["packaged_process"]["apps"]
    assert all(row["installed"] is True for row in listed["connections"] if row["id"].endswith("_cli"))


def test_parallel_detection_isolates_one_app_failure(monkeypatch):
    detection = importlib.import_module("multiagent.setup.detect")
    service = fixture()

    def fake_app(app, cwd, declared_path, steps):
        if app == "codex":
            raise RuntimeError("synthetic failure")
        return {"app": app, "detection": "detected", "executable_found": True,
                "context": steps.context, "version": "1.2.3", "version_result": "observed",
                "session": {"state": "signed_in"}}

    monkeypatch.setattr(detection, "_app", fake_app)
    monkeypatch.setattr(detection, "_requirements", no_requirements)
    service.detector = lambda **kwargs: detection.detect(**kwargs, system="Windows")

    rows = {row["id"]: row for row in service.list()["connections"]}

    assert rows["claude_cli"]["installed"] is True
    assert rows["codex_cli"]["installed"] is None
    assert rows["codex_cli"]["version_result"] == "probe_failed"
    assert rows["copilot_cli"]["installed"] is True
    assert rows["antigravity_cli"]["installed"] is True
