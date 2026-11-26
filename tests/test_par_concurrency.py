# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Fixed-clock quota calculations and persisted concurrency policy."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from multiagent.services.concurrency import active_mode, calculate_auto, resolve_limit
from multiagent.services.gui_settings import GuiSettingsServices

NOW = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)


def plans(*windows):
    return {"apps": [{"app": name, "windows": [{"window": "five_hour", "used_percent": used,
                                                  "resets_at": (NOW + timedelta(minutes=minutes)).isoformat()}]}
                     for name, used, minutes in windows]}


def test_eco_margin_and_tightest_window():
    data = plans(("codex", 40, 120), ("claude", 70, 120))
    eco = calculate_auto(data, mode="eco", margin=30, cost_per_step={"codex": 2, "claude": 2},
                         manual_limit=20, machine_limit=20, now=NOW)
    # Claude has 70 used + 30 margin: nothing is free, so the limit is 0 (was 1 before the reserve rule).
    assert eco["limit"] == 0 and eco["app"] == "claude" and eco["code"] == "quota_margin_reached"
    lean = calculate_auto(data, mode="eco", margin=3, cost_per_step={"codex": 2, "claude": 2},
                          manual_limit=20, machine_limit=20, now=NOW)
    assert lean["limit"] == 1  # the tighter Claude window still governs
    codex_only = calculate_auto(plans(("codex", 40, 120)), mode="eco", margin=3,
                                cost_per_step={"codex": 2}, manual_limit=20, machine_limit=20, now=NOW)
    assert codex_only["limit"] == 3  # floor(floor(57 / 2) / 8)


def test_normal_spreads_and_performance_does_not():
    data = plans(("codex", 50, 120))
    normal = calculate_auto(data, mode="normal", margin=10, cost_per_step={"codex": 2},
                            manual_limit=12, machine_limit=8, now=NOW)
    fast = calculate_auto(data, mode="performance", margin=3, cost_per_step={"codex": 2},
                          manual_limit=12, machine_limit=8, now=NOW)
    assert normal["limit"] == 2 and fast["limit"] == 8


def test_no_quota_data_and_temporary_modes():
    assert calculate_auto({"apps": []}, mode="maximum", margin=3, cost_per_step={},
                          manual_limit=12, machine_limit=8, now=NOW) == {
        "limit": 1, "reason": "no_quota_data", "code": "no_quota_data"}
    assert active_mode({"mode": "maximum", "expires_at": (NOW - timedelta(seconds=1)).isoformat()}, now=NOW) == "normal"
    assert active_mode({"mode": "performance", "expires_at": (NOW + timedelta(hours=1)).isoformat()}, now=NOW) == "performance"


def test_precedence_and_smallest_target_cap():
    assert resolve_limit(gui=9, environment=7, cli=5, app=4, model=3) == (3, "model_limit")
    assert resolve_limit(gui=9, environment=7, cli=5) == (5, "total_limit")
    assert resolve_limit(gui=9, environment=7, cli=None) == (7, "total_limit")


def test_each_mode_remembers_its_margin(tmp_path):
    service = GuiSettingsServices(SimpleNamespace(settings=SimpleNamespace(local_dir=tmp_path)))
    service.set_concurrency({"mode": "eco", "margin": 3})
    assert service.set_concurrency({"mode": "normal"})["margin"] == 10
    assert service.set_concurrency({"mode": "eco"})["margin"] == 3
    with pytest.raises(Exception):
        service.set_concurrency({"margin": 2})


def test_gui_api_reports_effective_setting_and_rejects_coercion(tmp_path):
    from fastapi.testclient import TestClient
    from multiagent.api import create_app
    from multiagent.config import Settings
    from multiagent.gui.token import ensure_token
    from multiagent.services import ApplicationServices

    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        mock_mode=True, gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_token_file=tmp_path / "gui-token",
                        gui_claude_home=tmp_path / "h" / "claude", gui_codex_home=tmp_path / "h" / "codex",
                        gui_copilot_home=tmp_path / "h" / "copilot",
                        gui_antigravity_home=tmp_path / "h" / "agy")  # never the real quota files
    services = ApplicationServices.create(settings, dry_run=True)
    auth = {"Authorization": f"Bearer {ensure_token(settings.gui_token_file)}"}
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        initial = client.get("/gui/api/settings/concurrency", headers=auth)
        assert initial.status_code == 200 and "limit" in initial.json() and "reason" in initial.json()
        saved = client.put("/gui/api/settings/concurrency", json={"mode": "eco", "margin": 3}, headers=auth)
        assert saved.status_code == 200 and saved.json()["mode"] == "eco" and saved.json()["margin"] == 3
        invalid = client.put("/gui/api/settings/concurrency", json={"manual": "4"}, headers=auth)
        assert invalid.status_code == 422
        assert client.get("/gui/api/settings/concurrency").status_code == 401
    services.runs.close()


# --- Round 3 ---------------------------------------------------------------------------------------

import math

from multiagent.services.concurrency import ConcurrencyServices, economic_measure


@pytest.mark.parametrize("mode", ["eco", "normal", "performance", "maximum"])
def test_margin_reached_gives_zero_in_every_mode(mode):
    data = plans(("codex", 90, 120))  # 90 used + 10 margin: nothing is free
    result = calculate_auto(data, mode=mode, margin=10, cost_per_step={"codex": 2},
                            manual_limit=12, machine_limit=8, now=NOW)
    assert result["limit"] == 0 and result["code"] == "quota_margin_reached"
    just_above = calculate_auto(plans(("codex", 80, 120)), mode=mode, margin=10, cost_per_step={"codex": 2},
                                manual_limit=12, machine_limit=8, now=NOW)
    assert just_above["limit"] >= 1 and just_above["code"] == "quota_window"
    # one whole step is free but fewer than the blocks left: the floor of 1 only applies with steps > 0
    one_step = calculate_auto(plans(("codex", 87, 600)), mode="eco", margin=10, cost_per_step={"codex": 3},
                              manual_limit=12, machine_limit=8, now=NOW)
    assert one_step["limit"] == 1


def test_past_reset_is_ignored_and_means_no_data():
    past = plans(("codex", 10, 0))
    assert calculate_auto(past, mode="eco", margin=10, cost_per_step={"codex": 2},
                          manual_limit=12, machine_limit=8, now=NOW)["code"] == "no_quota_data"
    earlier = plans(("codex", 10, -30))
    assert calculate_auto(earlier, mode="normal", margin=10, cost_per_step={"codex": 2},
                          manual_limit=12, machine_limit=8, now=NOW)["limit"] == 1


@pytest.mark.parametrize("bad", [True, False, float("nan"), float("inf"), -1, 0, "5"])
def test_bad_cost_is_ignored(bad):
    result = calculate_auto(plans(("codex", 10, 120)), mode="performance", margin=3, cost_per_step={"codex": bad},
                            manual_limit=12, machine_limit=8, now=NOW)
    assert result["code"] == "no_quota_data"


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf")])
def test_bad_observations_are_not_measurements(bad):
    def spec(provider):
        return SimpleNamespace(id="m", provider=provider, tier="cloud", level="T2", pricing=None)
    assert economic_measure(spec("copilot_cli"), now=NOW, observations={"m": {"premium_interaction": bad}}) is None
    assert economic_measure(spec("agy"), now=NOW, observations={"m": {"bag_free_percent": bad}}) is None
    assert economic_measure(spec("codex_cli"), now=NOW,
                            observations={"m": {"quota_percent_per_step": bad}}) == ("quota_percent_per_step", 2.0)


def test_expired_temporary_mode_is_normal_before_a_partial_change(tmp_path):
    service = GuiSettingsServices(SimpleNamespace(settings=SimpleNamespace(local_dir=tmp_path),
                                                  usage=SimpleNamespace(plans=lambda: {"apps": []})))
    service.set_concurrency({"mode": "maximum", "duration": "two_hours"})
    saved = service.read()["concurrency"]
    assert saved["mode"] == "maximum" and saved["expires_at"] is not None
    soon = datetime.now(timezone.utc) + timedelta(hours=1, minutes=55)
    assert soon < datetime.fromisoformat(saved["expires_at"]) <= datetime.now(timezone.utc) + timedelta(hours=2, minutes=1)
    # Age it by hand, the way a clock past the deadline would see it.
    import json
    path = tmp_path / "gui-settings.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["concurrency"]["expires_at"] = "2020-01-01T00:00:00+00:00"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert service.concurrency()["mode"] == "normal"
    changed = service.set_concurrency({"margin": 15})
    assert changed["mode"] == "normal" and changed["margin"] == 15
    stored = service.read()["concurrency"]
    assert stored["mode"] == "normal" and stored["duration"] is None and stored["expires_at"] is None
    assert stored["margins"]["maximum"] == 3  # the expired mode's own margin was not touched


def test_invalid_or_null_mode_is_rejected_cleanly(tmp_path):
    service = GuiSettingsServices(SimpleNamespace(settings=SimpleNamespace(local_dir=tmp_path)))
    for bad in (None, "turbo", 3):
        with pytest.raises(Exception) as caught:
            service.set_concurrency({"mode": bad})
        assert getattr(caught.value, "status", None) == 422


def test_next_five_hour_expiry_and_tasks_completion(tmp_path):
    reset = datetime.now(timezone.utc) + timedelta(minutes=90)
    usage = SimpleNamespace(plans=lambda: {"apps": [{"app": "codex", "windows": [
        {"window": "five_hour", "used_percent": 10, "resets_at": reset.isoformat()}]}]})
    service = GuiSettingsServices(SimpleNamespace(settings=SimpleNamespace(local_dir=tmp_path), usage=usage))
    result = service.set_concurrency({"mode": "performance"})
    assert datetime.fromisoformat(result["expires_at"]) == reset.replace(microsecond=0)
    assert result["duration"] == "next_five_hour"
    service.set_concurrency({"mode": "maximum", "duration": "tasks"})
    assert service.concurrency()["mode"] == "maximum" and service.concurrency()["expires_at"] is None
    assert service.complete_temporary_tasks()["mode"] == "normal"
    assert service.concurrency()["duration"] is None
    assert service.complete_temporary_tasks()["mode"] == "normal"  # nothing temporary left: no change


def _effective_services(gui_manual, **fields):
    from multiagent.config import Settings
    settings = Settings(_env_file=None, **fields)
    config = {"mode": "normal", "margin": 10, "margins": {}, "manual": gui_manual, "per_app": {"codex": 6},
              "per_model": {}, "allow_t4": False, "duration": None, "expires_at": None, "selection": "manual",
              "reason": "saved_setting"}
    return SimpleNamespace(settings=settings, gui_settings=SimpleNamespace(concurrency=lambda now=None: dict(config)),
                           usage=SimpleNamespace(plans=lambda: {"apps": []}))


def test_real_precedence_uses_explicitly_set_fields_only():
    cpu = max(1, __import__("os").cpu_count() or 1)
    # The default worker_concurrency of 1 is not a choice: the GUI cap stands.
    gui_only = ConcurrencyServices(_effective_services(max(7, cpu + 5))).effective(app="codex")
    assert gui_only["limit"] == min(cpu, 6) and gui_only["reason"] == "app_limit"
    env = ConcurrencyServices(_effective_services(cpu + 5, worker_concurrency=2)).effective()
    assert env["limit"] == min(2, cpu) and env["reason"] == "total_limit"
    cli = ConcurrencyServices(_effective_services(cpu + 5, worker_concurrency=3)).effective(cli_limit=1)
    assert cli["limit"] == 1
    capped = ConcurrencyServices(_effective_services(cpu + 5, worker_concurrency=9,
                                                      worker_max_active_per_app=1)).effective(app="codex")
    assert capped["limit"] == 1 and capped["reason"] == "app_limit"


def test_model_level_api(tmp_path):
    from fastapi.testclient import TestClient
    from multiagent.api import create_app
    from multiagent.config import Settings
    from multiagent.gui.token import ensure_token
    from multiagent.services import ApplicationServices

    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        mock_mode=True, gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_token_file=tmp_path / "gui-token",
                        gui_claude_home=tmp_path / "h" / "claude", gui_codex_home=tmp_path / "h" / "codex",
                        gui_copilot_home=tmp_path / "h" / "copilot", gui_antigravity_home=tmp_path / "h" / "agy")
    services = ApplicationServices.create(settings, dry_run=True)
    auth = {"Authorization": f"Bearer {ensure_token(settings.gui_token_file)}"}
    known = next(iter(services.system.catalog.models))
    url = "/gui/api/settings/model-level"
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        ok = client.put(url, json={"model_id": known, "level": "T2", "source": "model card"}, headers=auth)
        assert ok.status_code == 200 and ok.json()["level"] == "T2" and ok.json()["confirmed_by"]
        assert client.put(url, json={"model_id": known, "level": "T2", "source": "x", "extra": 1},
                          headers=auth).status_code == 422
        assert client.put(url, json={"model_id": "no-such-model", "level": "T2", "source": "x"},
                          headers=auth).status_code == 422
        assert client.put(url, json={"model_id": known, "level": "T9", "source": "x"},
                          headers=auth).status_code == 422
        assert client.put(url, json={"model_id": known, "level": "T2", "source": "x"}).status_code == 401
        for bad in (None, "turbo"):
            response = client.put("/gui/api/settings/concurrency", json={"mode": bad}, headers=auth)
            assert response.status_code == 422 and "invalid_request" in response.text
    services.runs.close()


def test_expired_temporary_mode_is_not_inherited_when_a_new_mode_is_set(tmp_path):
    import json
    service = GuiSettingsServices(SimpleNamespace(settings=SimpleNamespace(local_dir=tmp_path),
                                                  usage=SimpleNamespace(plans=lambda: {"apps": []})))
    service.set_concurrency({"mode": "maximum", "duration": "two_hours"})
    path = tmp_path / "gui-settings.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["concurrency"]["expires_at"] = "2020-01-01T00:00:00+00:00"
    path.write_text(json.dumps(raw), encoding="utf-8")
    result = service.set_concurrency({"mode": "performance"})  # no duration given: the old two_hours is not reused
    assert result["mode"] == "performance" and result["duration"] == "next_five_hour"
    assert datetime.fromisoformat(result["expires_at"]) > datetime.now(timezone.utc)
