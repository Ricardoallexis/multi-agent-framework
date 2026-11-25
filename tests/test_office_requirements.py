# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Pure capability requirements for office areas (OFI-T06 phase 1). Hand-written candidates, no services, no network."""
from __future__ import annotations

from multiagent.catalog import ModelSpec
from multiagent.offices.requirements import (
    CAPABILITY_TEXT,
    KNOWN_REQUIREMENTS,
    coverage,
    executor_capabilities,
    mock_capabilities,
    model_capabilities,
    office_requirements,
)
from multiagent.offices.schema import OfficeDefinition


def _spec(**over) -> ModelSpec:
    base = dict(id="m", provider="p", model="m", tier="cloud_fast", structured_output=False, web=False,
                capabilities=frozenset({"text_generation"}))
    base.update(over)
    return ModelSpec(**base)


def _model(cid: str, label: str, caps, *, local: bool = False) -> dict:
    return {"id": cid, "kind": "model", "label": label, "capabilities": frozenset(caps), "local": local}


def _definition(areas: list[dict], **over) -> OfficeDefinition:
    data = {"schema": "maf.office/1", "id": "demo", "name": "Demo", "areas": areas}
    data.update(over)
    return OfficeDefinition.model_validate(data)


def _area(aid: str, **over) -> dict:
    return {"id": aid, "name": aid.title(), "path": f"areas/{aid}", **over}


def test_equivalences():
    local = _spec(tier="local", structured_output=True)
    assert model_capabilities(local) == {"model.text", "model.structured", "model.local"}
    with_tools = _spec(tier="local", structured_output=True, capabilities=frozenset({"text_generation", "model.tools"}))
    assert model_capabilities(with_tools) == {"model.text", "model.structured", "model.local", "model.tools"}
    assert model_capabilities(_spec()) == {"model.text"}
    assert executor_capabilities() == {"model.text", "executor.code", "executor.files"}
    assert set(KNOWN_REQUIREMENTS) == set(CAPABILITY_TEXT)


def test_cover_text():
    cands = [_model("b", "Model B", {"model.text"}), _model("a", "Model A", {"model.text"})]
    got = coverage(["model.text"], cands, local_only_candidates=False)
    assert got["covered"] is True and got["action"] is None and got["missing"] == []
    assert got["text"] == "You can cover this with Model A or with Model B"
    assert got["by"] == [{"id": "a", "kind": "model", "label": "Model A"}, {"id": "b", "kind": "model", "label": "Model B"}]


def test_cover_text_with_three_repeats_con():
    cands = [_model(i, f"Model {i.upper()}", {"model.text"}) for i in ("c", "a", "b")]
    got = coverage(["model.text"], cands, local_only_candidates=False)
    assert got["text"] == "You can cover this with Model A, with Model B or with Model C"


def test_mock_is_local_and_covers_a_sensitive_area():
    mock = {"id": "mock", "kind": "mock", "label": "Mock (example)", "capabilities": mock_capabilities()}
    assert "local" not in mock
    definition = _definition([_area("caja", sensitivity="sensitive", requires=["model.text"])])
    area = office_requirements(definition, [mock])["areas"][0]
    assert area["covered"] is True
    assert area["by"] == [{"id": "mock", "kind": "mock", "label": "Mock (example)"}]
    assert area["note"] == "This area is Sensitive: it can only use agents on your team today"


def test_missing_text():
    got = coverage(["model.text", "executor.code"], [_model("a", "Modelo A", {"model.text"})], local_only_candidates=False)
    assert got["covered"] is False and got["by"] == []
    assert got["missing"] == ["executor.code"]
    assert got["text"] == "You need an agent that can work with code"
    assert got["action"] == "connect"


def test_missing_with_no_candidates_lists_everything():
    got = coverage(["model.text", "executor.files"], [], local_only_candidates=False)
    assert got["missing"] == ["model.text", "executor.files"]
    assert got["text"] == "You need an agent that can write text and work with files"


def test_closest_candidate_decides_what_is_missing():
    cands = [_model("z", "Z", {"model.text", "model.tools"}), _model("a", "A", {"model.text"})]
    got = coverage(["model.text", "model.tools", "model.structured"], cands, local_only_candidates=False)
    assert got["missing"] == ["model.structured"]
    tie = [_model("b", "B", {"model.text"}), _model("a", "A", {"model.tools"})]
    got = coverage(["model.text", "model.tools", "executor.code"], tie, local_only_candidates=False)
    assert got["missing"] == ["model.text", "executor.code"]  # tie on one each: id "a" wins


def test_sensitive_area_counts_only_local():
    cloud = _model("cloud", "Cloud", {"model.text"})
    local = _model("loc", "Local", {"model.text", "model.local"}, local=True)
    definition = _definition([_area("caja", sensitivity="sensitive", requires=["model.text"])])
    only_cloud = office_requirements(definition, [cloud])["areas"][0]
    assert only_cloud["by"] == [] and only_cloud["covered"] is False
    assert only_cloud["note"] == "This area is Sensitive: it can only use agents on your team today"
    both = office_requirements(definition, [cloud, local])["areas"][0]
    assert [entry["id"] for entry in both["by"]] == ["loc"]
    assert both["note"] == "This area is Sensitive: it can only use agents on your team today"


def test_local_only_area_and_office():
    cloud = _model("cloud", "Cloud", {"model.text"})
    definition = _definition([_area("a", local_only=True, requires=["model.text"]), _area("b", requires=["model.text"])])
    first, second = office_requirements(definition, [cloud])["areas"]
    assert first["note"] == "This area is Local only: it never leaves your computer" and first["by"] == []
    assert second["note"] is None and [e["id"] for e in second["by"]] == ["cloud"]
    whole = _definition([_area("b", requires=["model.text"])], local_only=True)
    assert office_requirements(whole, [cloud])["areas"][0]["note"] == "This area is Local only: it never leaves your computer"


def test_mock_covers_model_requirements_only():
    mock = {"id": "mock", "kind": "mock", "label": "Mock (example)", "capabilities": mock_capabilities()}
    ok = coverage(["model.text", "model.tools"], [mock], local_only_candidates=False)
    assert ok["covered"] is True and ok["by"] == [{"id": "mock", "kind": "mock", "label": "Mock (example)"}]
    assert ok["text"] == "You can cover this with Mock (example)"
    bad = coverage(["model.text", "executor.code"], [mock], local_only_candidates=False)
    assert bad["missing"] == ["executor.code"] and bad["action"] == "connect"
    assert not any(item.startswith("executor.") for item in mock_capabilities())


def test_unknown_requirement_is_shown_as_is():
    got = coverage(["model.vision"], [_model("a", "A", {"model.text"})], local_only_candidates=False)
    assert got["missing"] == ["model.vision"]
    assert got["text"] == "You need an agent that can model.vision"


def test_area_without_requirements_needs_nothing():
    definition = _definition([_area("a")])
    area = office_requirements(definition, [])["areas"][0]
    assert area["covered"] is True and area["action"] is None and area["requires"] == []


def test_office_requirements_shape_one_area_each():
    definition = _definition([_area("a", requires=["model.text", "executor.code"]), _area("b", requires=["model.text"])])
    cands = [_model("m", "Model", {"model.text"})]
    out = office_requirements(definition, cands)
    assert out["office"] == "demo" and [a["id"] for a in out["areas"]] == ["a", "b"]
    assert set(out["areas"][0]) == {"id", "requires", "covered", "by", "missing", "text", "note", "action"}
    assert out["areas"][0]["missing"] == ["executor.code"] and out["areas"][1]["covered"] is True


# --- the route and the service (phase 2) -------------------------------------------------------------------------

def _install_demo(services, tmp_path, *, areas=("reception", "proposal"), overrides=None):
    from test_offices_install import write_office
    folder = write_office(tmp_path / "office", areas=areas, overrides=overrides)
    return services.offices.import_folder(folder, actor="user:local")


def test_requirements_route(tmp_path):
    from fastapi.testclient import TestClient
    from test_offices_install import make_settings
    from multiagent.api import create_app
    from multiagent.services import ApplicationServices

    services = ApplicationServices.create(make_settings(tmp_path / "local"), dry_run=True)
    try:
        services.connections.detector = lambda **kw: {}
        calls: list[str] = []

        class CountingAdapter:
            def __getattr__(self, name):
                calls.append(name)
                raise AssertionError(f"a provider was called: {name}")

        for name in list(services.system.adapters):
            if name != "ollama":
                services.system.adapters[name] = CountingAdapter()
        _install_demo(services, tmp_path, overrides={"proposal": {"requires": ["model.text", "executor.code"]}})
        client = TestClient(create_app(services=services), base_url="http://localhost")
        response = client.get("/api/v1/offices/demo/requirements")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["office"] == "demo"
        assert [a["id"] for a in body["areas"]] == ["reception", "proposal"]
        for area in body["areas"]:
            assert set(area) == {"id", "requires", "covered", "by", "missing", "text", "note", "action"}
        first, second = body["areas"]
        assert first["covered"] is True and first["by"] == []          # asks for nothing
        assert second["missing"] and second["action"] == "connect"     # no executor active
        assert calls == []
        gone = client.get("/api/v1/offices/nope/requirements")
        assert gone.status_code == 409 and gone.json()["detail"]["details"]["reason"] == "office_not_installed"
    finally:
        services.bundle_scans.close()
        services.runs.close()


def test_service_candidates_in_mock_cover_model_requirements_only(tmp_path):
    from test_offices_install import make_settings
    from multiagent.services import ApplicationServices

    services = ApplicationServices.create(make_settings(tmp_path / "local"), dry_run=True)
    try:
        services.connections.detector = lambda **kw: {}
        found = {c["id"]: c for c in services.offices.requirements_candidates()}
        assert found["mock"]["local"] is True and found["mock"]["label"] == "Mock (example)"
        assert not any(c["kind"] == "executor" for c in found.values())
        _install_demo(services, tmp_path, overrides={"reception": {"requires": ["model.text"], "sensitivity": "sensitive"},
                                                     "proposal": {"requires": ["model.text", "executor.code"]}})
        areas = {a["id"]: a for a in services.offices.requirements("demo")["areas"]}
        assert [e["id"] for e in areas["reception"]["by"]] == ["mock"] and areas["reception"]["note"].startswith("This area is Sensitive")
        assert areas["proposal"]["missing"] == ["executor.code"]
    finally:
        services.bundle_scans.close()
        services.runs.close()
