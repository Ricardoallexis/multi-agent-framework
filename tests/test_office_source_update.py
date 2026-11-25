# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""OFA-T02: remembered office sources, reviewed updates and local-only metadata."""
from __future__ import annotations

from test_gui_office_import import gui, post, decide, write_office


BASE = "/gui/api/offices"


def use_safe_scan(gui):
    def scan(office_id, _fingerprint, folders):
        areas = {area_id: {"state": "safe", "verdict": "safe", "findings": [], "coverage": {}}
                 for area_id in folders}
        return {"office": office_id, "state": "safe", "verdict": "safe", "approval_id": None, "areas": areas}

    gui.services.bundle_scans.scan_office = scan


def install_folder(gui, folder):
    staged = post(gui, "/stage-folder", {"folder": str(folder)})
    assert staged.status_code == 200, staged.text
    card = staged.json()
    assert card["scan"]["state"] == "safe"
    assert decide(gui, card["approval_id"], risk_ack=card["risk_ack_required"]).status_code == 200
    installed = post(gui, f"/stage/{card['stage_id']}/install", {"approval_id": card["approval_id"]})
    assert installed.status_code == 200, installed.text
    return card, installed.json()


def test_folder_origin_is_local_and_update_requires_a_review(gui, tmp_path):
    use_safe_scan(gui)
    gui.services.gui_settings.set_mode("advanced")
    source = write_office(tmp_path / "source")
    work_data = gui.services.settings.data_dir / "work-data.txt"
    work_data.write_text("keep this local\n", encoding="utf-8")

    initial_card, installed = install_folder(gui, source)
    record = gui.services.offices._read()["demo"]["source"]
    assert record["path"] == str(source.resolve())
    assert record["fingerprint"] == initial_card["fingerprint"]
    assert record["version"] == 1
    assert isinstance(record["approved_at"], str) and record["approved_at"]
    assert record["approved_by"] == "user:operator"
    assert record["approval_id"] == initial_card["approval_id"]
    event = next(event for event in gui.services.tasks.store.events(limit=100)
                 if event["type"] == "office.installed")
    assert event["actor"] == "user:operator"
    assert event["payload"]["version"] == 1
    assert event["payload"]["approval_id"] == initial_card["approval_id"]
    assert event["payload"]["source_fingerprint"] == initial_card["fingerprint"]

    detail = gui.client.get(BASE + "/demo", headers=gui.auth)
    assert detail.status_code == 200
    assert detail.json()["source_update"] == {"tracked": True, "available": True, "changed": False}
    assert str(source) not in detail.text
    assert "source" not in installed["office"]
    privacy = post(gui, "/demo/privacy/raise", {
        "area": "reception", "sensitivity": "sensitive", "local_only": True,
    })
    assert privacy.status_code == 200, privacy.text

    changed_file = source / "areas" / "reception" / "prompts" / "spec" / "extract_requirements.v1.md"
    changed_file.write_text(changed_file.read_text(encoding="utf-8") + "\nUpdated source.\n", encoding="utf-8")
    changed_detail = gui.client.get(BASE + "/demo", headers=gui.auth)
    assert changed_detail.json()["source_update"]["changed"] is True
    staged = post(gui, "/demo/stage-source-update")
    assert staged.status_code == 200, staged.text
    card = staged.json()
    assert [(area["id"], area["change"]) for area in card["areas"]] == [("reception", "changed")]
    assert {"area": "reception", "path": "prompts/spec/extract_requirements.v1.md", "change": "changed"} \
        in card["file_changes"]
    assert str(source) not in staged.text

    assert decide(gui, card["approval_id"], risk_ack=card["risk_ack_required"]).status_code == 200
    updated = post(gui, f"/stage/{card['stage_id']}/install", {"approval_id": card["approval_id"]})
    assert updated.status_code == 200, updated.text
    updated_office = updated.json()["office"]
    updated_record = gui.services.offices._read()["demo"]["source"]
    assert updated_office["version"] == updated_record["version"] == 2
    assert updated_record["fingerprint"] != record["fingerprint"]
    assert updated_record["approved_by"] == "user:operator"
    assert updated_record["approval_id"] == card["approval_id"]
    assert updated_office["areas"][0]["sensitivity"] == "sensitive"
    assert updated_office["areas"][0]["local_only"] is True
    assert work_data.read_text(encoding="utf-8") == "keep this local\n"
    assert gui.services.offices.source_update_status("demo") == {
        "tracked": True, "available": True, "changed": False,
    }

    exported = post(gui, "/demo/export")
    assert exported.status_code == 200
    assert str(source) not in exported.text


def test_missing_origin_is_reported_without_guessing_a_new_path(gui, tmp_path):
    use_safe_scan(gui)
    gui.services.gui_settings.set_mode("advanced")
    source = write_office(tmp_path / "source")
    install_folder(gui, source)
    source.rename(tmp_path / "moved")

    detail = gui.client.get(BASE + "/demo", headers=gui.auth)
    assert detail.status_code == 200
    assert detail.json()["source_update"] == {"tracked": True, "available": False, "changed": False}
    assert str(source) not in detail.text
    response = post(gui, "/demo/stage-source-update")
    assert response.status_code == 409
    assert response.json()["detail"]["details"]["reason"] == "office_source_unavailable"
    assert gui.services.offices.get("demo")["version"] == 1


def test_source_change_after_review_invalidates_the_approval(gui, tmp_path):
    use_safe_scan(gui)
    gui.services.gui_settings.set_mode("advanced")
    source = write_office(tmp_path / "source")
    install_folder(gui, source)
    changed_file = source / "areas" / "reception" / "prompts" / "spec" / "extract_requirements.v1.md"
    changed_file.write_text(changed_file.read_text(encoding="utf-8") + "\nFirst update.\n", encoding="utf-8")

    staged = post(gui, "/demo/stage-source-update")
    assert staged.status_code == 200, staged.text
    card = staged.json()
    assert decide(gui, card["approval_id"], risk_ack=card["risk_ack_required"]).status_code == 200
    changed_file.write_text(changed_file.read_text(encoding="utf-8") + "\nChanged after review.\n", encoding="utf-8")

    response = post(gui, f"/stage/{card['stage_id']}/install", {"approval_id": card["approval_id"]})
    assert response.status_code == 409
    assert response.json()["detail"]["details"]["reason"] == "office_source_changed"
    assert gui.services.offices.get("demo")["version"] == 1
    assert gui.services.user_approvals.status(card["approval_id"])["state"] == "approved"
    assert not gui.services.offices._stage_folder(card["stage_id"]).exists()
