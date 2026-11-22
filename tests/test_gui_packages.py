# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""PAQ-T02: the «Paquetes» routes (TestClient) and its pure model (Node, skipped without it). Nobody opens a browser here."""
from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import test_installed_packages as base
import test_installed_zip as zips
from multiagent.api import create_app
from multiagent.gui import STATIC_DIR
from multiagent.gui.packages_api import BODY_MAX_BYTES
from multiagent.gui.token import ensure_token
from multiagent.services import installed

JS = STATIC_DIR / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
PATH = "/gui/api/packages"


def read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def run_node(source: str):
    result = subprocess.run([NODE, "--input-type=module", "-e", source], capture_output=True, text=True, encoding="utf-8",
                            cwd=STATIC_DIR, timeout=30, check=True)
    return json.loads(result.stdout)


def uri(name: str) -> str:
    return json.dumps((JS / name).as_uri())


@pytest.fixture
def gui(tmp_path):
    services = base.ApplicationServices.create(base.make_settings(tmp_path / "local"), dry_run=True)
    token = ensure_token(services.settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, tmp=tmp_path, auth={"Authorization": f"Bearer {token}"})
    services.bundle_scans.close()
    services.runs.close()


def zip_json(content: bytes, **extra) -> dict:
    return {"zip_base64": base64.b64encode(content).decode("ascii"), "filename": "mi.zip", **extra}


def staging(gui):
    folder = gui.services.settings.data_dir / "installed" / ".staging"
    return sorted(folder.iterdir()) if folder.is_dir() else []


def test_routes_need_token_and_json(gui):
    for method, path in (("get", PATH), ("post", PATH + "/zip"), ("post", PATH + "/folder"), ("post", PATH + "/example"),
                         ("post", PATH + "/demo/remove"), ("post", PATH + "/demo/reinstall-zip")):
        response = getattr(gui.client, method)(path) if method == "get" else gui.client.post(path, json={})
        assert response.status_code == 401, path
    wrong = gui.client.post(PATH + "/zip", json={}, headers={"Authorization": "Bearer wrong-token-value"})
    assert wrong.status_code == 401
    for content_type in ("multipart/form-data; boundary=x", "application/zip", "text/plain"):
        response = gui.client.post(PATH + "/zip", content=b"x", headers={**gui.auth, "Content-Type": content_type})
        assert response.status_code == 415, content_type


def test_upload_over_limit_is_413_before_reading(gui):
    declared = {**gui.auth, "Content-Type": "application/json", "Content-Length": str(BODY_MAX_BYTES + 1)}
    for path in (PATH + "/zip", PATH + "/demo/reinstall-zip"):
        response = gui.client.post(path, content=b"{}", headers=declared)
        assert response.status_code == 413, path
        assert response.json()["detail"]["details"]["reason"] == "zip_too_large"
    # No declared length (a chunked body): the stream is cut at the cap as well.
    response = gui.client.post(PATH + "/zip", content=iter([b"x" * 1_000_000] * 40), headers={**gui.auth, "Content-Type": "application/json"})
    assert response.status_code == 413 and staging(gui) == []
    # A .zip over 25 MB inside a body that fits is refused after decoding.
    big = base64.b64encode(b"\0" * (installed.ZIP_UPLOAD_MAX_BYTES + 1)).decode("ascii")
    response = gui.client.post(PATH + "/zip", json={"name": "demo", "zip_base64": big}, headers=gui.auth)
    assert response.status_code == 413 and response.json()["detail"]["details"]["reason"] == "zip_too_large"


def test_bad_bodies_are_422(gui):
    for body in ({}, {"name": "demo"}, {"name": "demo", "zip_base64": "%%%"}, {"name": "demo", "zip_base64": "QQ==", "extra": 1}):
        response = gui.client.post(PATH + "/zip", json=body, headers=gui.auth)
        assert response.status_code == 422, body
    assert response.json()["detail"]["code"] == "invalid_request"
    not_zip = gui.client.post(PATH + "/zip", json={"name": "demo", **zip_json(b"not a zip")}, headers=gui.auth)
    assert (not_zip.status_code, not_zip.json()["detail"]["details"]["reason"]) == (422, "zip_invalid")
    garbage = gui.client.post(PATH + "/zip", json={"name": "demo", "zip_base64": "%%%"}, headers=gui.auth)
    assert garbage.json()["detail"]["details"]["reason"] == "zip_invalid"


def test_zip_install_list_reinstall_and_remove(gui):
    package = zips.data_package(gui.tmp)
    data = zips.make_zip(package, top="demo/")
    created = gui.client.post(PATH + "/zip", json={"name": "demo", **zip_json(data)}, headers=gui.auth)
    assert created.status_code == 201, created.text
    entry = created.json()
    assert (entry["name"], entry["level"], entry["source_label"], entry["channel"], entry["installed_by"]) == (
        "demo", "data", "mi.zip", "gui", "user:gui")
    listing = gui.client.get(PATH, headers=gui.auth).json()
    assert listing["mode"] == "simple" and [row["name"] for row in listing["packages"]] == ["demo"]
    assert listing["example_available"] is True
    assert "path" not in json.dumps(listing) and str(gui.tmp) not in json.dumps(listing)
    again = gui.client.post(PATH + "/zip", json={"name": "demo", **zip_json(data)}, headers=gui.auth)
    assert (again.status_code, again.json()["detail"]["details"]["reason"]) == (409, "bundle_name_in_use")
    (gui.services.bundle_path("demo") / "workflows" / "spec_review.yaml").write_text("# edited\n", encoding="utf-8")
    assert gui.services.installed.verify("demo")["state"] == "changed"
    replaced = gui.client.post(PATH + "/demo/reinstall-zip", json=zip_json(data), headers=gui.auth)
    assert replaced.status_code == 200 and (replaced.json()["version"], replaced.json()["state"]) == (2, "ok")
    missing = gui.client.post(PATH + "/nope/reinstall-zip", json=zip_json(data), headers=gui.auth)
    assert (missing.status_code, missing.json()["detail"]["details"]["reason"]) == (409, "bundle_not_installed")
    removed = gui.client.post(PATH + "/demo/remove", json={}, headers=gui.auth)
    assert removed.status_code == 200 and removed.json()["kept_data"] is True
    assert gui.client.get(PATH, headers=gui.auth).json()["packages"] == []
    assert staging(gui) == []


def test_unsafe_zip_is_422_and_leaves_nothing(gui):
    data = zips.make_zip({"workflows/a.yaml": b"id: a\n", "../evil": b"x"})
    response = gui.client.post(PATH + "/zip", json={"name": "demo", **zip_json(data)}, headers=gui.auth)
    assert (response.status_code, response.json()["detail"]["details"]["reason"]) == (422, "zip_unsafe_entry")
    assert staging(gui) == [] and gui.services.installed.list() == []


def test_code_zip_and_trust_follow_the_mode_in_order(gui):
    code = zips.make_zip(zips.code_package(), top="team/")
    response = gui.client.post(PATH + "/zip", json={"name": "team", **zip_json(code)}, headers=gui.auth)
    assert response.status_code == 409
    assert response.json()["detail"]["details"] == {"reason": "advanced_mode_required", "action": "install_code"}
    assert staging(gui) == []
    # trust_functions is asked first, whatever the package is
    data = zips.make_zip(zips.data_package(gui.tmp))
    both = gui.client.post(PATH + "/zip", json={"name": "demo", **zip_json(code, trust_functions=True)}, headers=gui.auth)
    assert both.json()["detail"]["details"]["action"] == "trust_functions"
    trusted_data = gui.client.post(PATH + "/zip", json={"name": "demo", **zip_json(data, trust_functions=True)}, headers=gui.auth)
    assert trusted_data.json()["detail"]["details"]["action"] == "trust_functions"
    assert gui.services.installed.list() == []
    gui.services.gui_settings.set_mode("advanced")
    ok = gui.client.post(PATH + "/zip", json={"name": "team", **zip_json(code, trust_functions=True)}, headers=gui.auth)
    assert ok.status_code == 201 and (ok.json()["level"], ok.json()["trust_functions"]) == ("code", True)
    swapped = gui.client.post(PATH + "/team/reinstall-zip", json=zip_json(code), headers=gui.auth)
    assert swapped.status_code == 200 and swapped.json()["trust_functions"] is False  # a new copy is never trusted at once
    gui.services.gui_settings.set_mode("simple")
    blocked = gui.client.post(PATH + "/team/reinstall-zip", json=zip_json(code), headers=gui.auth)
    assert blocked.json()["detail"]["details"]["action"] == "install_code"


def test_folder_route_advanced_only(gui):
    origin = base.make_data_bundle(gui.tmp / "origin")
    body = {"name": "demo", "folder": str(origin)}
    simple = gui.client.post(PATH + "/folder", json=body, headers=gui.auth)
    assert simple.status_code == 409
    assert simple.json()["detail"]["details"] == {"reason": "advanced_mode_required", "action": "install_folder"}
    trusted = gui.client.post(PATH + "/folder", json={**body, "trust_functions": True}, headers=gui.auth)
    assert trusted.json()["detail"]["details"]["action"] == "trust_functions"
    assert gui.services.installed.list() == []
    gui.services.gui_settings.set_mode("advanced")
    done = gui.client.post(PATH + "/folder", json=body, headers=gui.auth)
    assert done.status_code == 201 and done.json()["source_label"] == "origin" and str(gui.tmp) not in done.text
    missing = gui.client.post(PATH + "/folder", json={"name": "other", "folder": str(gui.tmp / "nowhere")}, headers=gui.auth)
    assert missing.status_code == 422


def test_example_route_works_in_simple_mode(gui):
    assert gui.services.gui_settings.mode() == "simple"
    created = gui.client.post(PATH + "/example", json={}, headers=gui.auth)
    assert created.status_code == 201, created.text
    assert (created.json()["source"], created.json()["level"], created.json()["name"]) == ("shipped", "code", "general")
    listing = gui.client.get(PATH, headers=gui.auth).json()
    assert listing["example_available"] is False and listing["packages"][0]["source"] == "shipped"
    assert gui.client.post(PATH + "/example", json={}, headers=gui.auth).status_code == 409
    assert gui.client.post(PATH + "/general/remove", json={}, headers=gui.auth).status_code == 200
    assert gui.client.post(PATH + "/example", json={"x": 1}, headers=gui.auth).status_code == 422


def test_example_outside_a_checkout_is_409(gui, monkeypatch):
    monkeypatch.setattr(installed, "IN_CHECKOUT", False)
    assert gui.client.get(PATH, headers=gui.auth).json()["example_available"] is False
    response = gui.client.post(PATH + "/example", json={}, headers=gui.auth)
    assert (response.status_code, response.json()["detail"]["details"]["reason"]) == (409, "example_not_available")


def test_api_still_refuses_trust_functions_and_multipart(gui):
    folder = base.make_data_bundle(gui.tmp / "origin")
    response = gui.client.post("/api/v1/installed", json={"name": "demo", "folder": str(folder), "actor": "user:ana",
                                                          "trust_functions": True})
    assert response.status_code == 422  # PAQ-T03: the API takes no trust_functions


# --- the pure model ------------------------------------------------------------------------------------------------

@needs_node
def test_rows_hide_paths_and_show_state():
    out = run_node("""
      import { packageRows, STATE_TEXT, REMOVE_LABEL, REINSTALL_LABEL } from %s;
      const payload = { packages: [
        { name: "ok", level: "data", version: 2, source: "copy", source_label: "mi.zip", state: "ok", path: "C:/secret/x", functions: { present: false } },
        { name: "bad", level: "data", version: 1, source: "copy", source_label: "viejo.zip", state: "changed", reason: "installed_bundle_changed" },
        { name: "gone", level: "code", version: 1, source: "copy", source_label: "", state: "missing" },
        { name: "general", level: "code", version: null, source: "shipped", source_label: "", state: "ok" },
      ] };
      const rows = packageRows(payload, "simple");
      console.log(JSON.stringify({ rows, STATE_TEXT, REMOVE_LABEL, REINSTALL_LABEL, empty: packageRows(null, "simple"), none: packageRows({}, "advanced") }));
    """ % uri("packages-model.js"))
    rows = {row["name"]: row for row in out["rows"]}
    assert "path" not in json.dumps(out["rows"]) and "secret" not in json.dumps(out["rows"])
    assert out["STATE_TEXT"] == {"ok": "Listo", "changed": "Cambió desde que se instaló", "missing": "Falta la copia instalada"}
    assert (rows["ok"]["stateText"], rows["ok"]["degraded"], rows["ok"]["canReinstall"]) == ("Listo", False, False)
    assert rows["ok"]["origin"] == "mi.zip" and rows["ok"]["versionText"] == "v2"
    bad = rows["bad"]
    assert bad["degraded"] is True and bad["stateText"] == out["STATE_TEXT"]["changed"]
    assert (bad["reinstallLabel"], bad["removeLabel"], bad["canReinstall"]) == ("Reinstalar desde un archivo", "Quitar", True)
    assert out["REINSTALL_LABEL"] == "Reinstalar desde un archivo" and out["REMOVE_LABEL"] == "Quitar"
    assert rows["gone"]["stateText"] == out["STATE_TEXT"]["missing"] and rows["gone"]["origin"] == "Sin origen"
    assert rows["general"]["canReinstall"] is False and rows["general"]["origin"] == "Paquete de ejemplo del producto"
    assert out["empty"] == [] and out["none"] == []


@needs_node
def test_trust_checkbox_only_advanced_code():
    out = run_node("""
      import { packageRows, trustBoxVisible } from %s;
      const payload = { packages: [
        { name: "data", level: "data", state: "ok", source: "copy" },
        { name: "code", level: "code", state: "ok", source: "copy", functions: { present: true, trusted: false } },
        { name: "trusted", level: "code", state: "ok", source: "copy", functions: { present: true, trusted: true } },
      ] };
      const show = (mode) => packageRows(payload, mode).map((row) => [row.name, row.showTrust, row.functionsText]);
      console.log(JSON.stringify({ simple: show("simple"), advanced: show("advanced"),
        boxes: [trustBoxVisible("advanced", "code"), trustBoxVisible("advanced", "data"), trustBoxVisible("simple", "code"), trustBoxVisible("advanced", undefined)] }));
    """ % uri("packages-model.js"))
    assert all(show is False and text == "" for _, show, text in out["simple"])
    assert [(name, show) for name, show, _ in out["advanced"]] == [("data", False), ("code", True), ("trusted", True)]
    assert out["advanced"][1][2] == "Sus funciones no están confiadas" and out["advanced"][2][2] == "Sus funciones son de confianza"
    assert out["boxes"] == [True, False, False, False]
    source = read("packages.js")
    assert "advanced && html`<label class=\"check-inline\">" in source and "Confiar en las funciones de este paquete" in source


@needs_node
def test_package_name_from_file():
    out = run_node("""
      import { packageNameFromFile } from %s;
      const names = ["Mi paquete (1).zip", "C:\\\\Users\\\\x\\\\oficina.ZIP", "(solo).zip", ".zip", "ñandú.zip", "a".repeat(300) + ".zip", "ok_name-1.zip"];
      console.log(JSON.stringify(names.map(packageNameFromFile)));
    """ % uri("packages-model.js"))
    assert out[0] == "Mi-paquete--1-"
    assert (out[1], out[2], out[3], out[4]) == ("oficina", "solo-", "paquete", "and-")
    assert len(out[5]) == 120 and out[6] == "ok_name-1"
    pattern = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,119}$")  # the server's _BUNDLE_NAME
    assert all(pattern.fullmatch(name) for name in out)


@needs_node
def test_zip_body_and_base64():
    out = run_node("""
      import { zipBody, folderBody, bytesToBase64 } from %s;
      const big = new Uint8Array(100000).map((_, i) => i %% 251);
      const text = bytesToBase64(big);
      console.log(JSON.stringify({ body: zipBody("demo", "C:\\\\x\\\\mi.zip", "QQ==", 1), plain: zipBody("a", "b.zip", "QQ==", false),
        folder: folderBody("d", " C:/x ", true), same: Buffer.from(text, "base64").equals(Buffer.from(big)) }));
    """ % uri("packages-model.js"))
    assert out["body"] == {"name": "demo", "filename": "mi.zip", "zip_base64": "QQ==", "trust_functions": True}
    assert out["plain"]["trust_functions"] is False and out["same"] is True
    assert out["folder"] == {"name": "d", "folder": "C:/x", "trust_functions": True}


@needs_node
def test_remove_notice():
    out = run_node("""
      import { REMOVE_NOTICE, packageErrorText } from %s;
      console.log(JSON.stringify({ notice: REMOVE_NOTICE,
        code: packageErrorText({ details: { reason: "advanced_mode_required", action: "install_code" } }),
        unsafe: packageErrorText({ details: { reason: "zip_unsafe_entry" } }), big: packageErrorText({ status: 413, details: {} }),
        fallback: packageErrorText(new Error("x")) }));
    """ % uri("packages-model.js"))
    assert out["notice"] == "Se quita de la lista. Se conservan sus datos"
    assert "modo avanzado" in out["code"] and "ruta" in out["unsafe"] and "25 MB" in out["big"] and out["fallback"]
    source = read("packages.js")
    assert "REMOVE_NOTICE" in source and "${REMOVE_NOTICE}" in source   # the confirmation shows it before anything is removed
    assert source.index("setRemoving(row.name)") < source.index("remove(row.name)")   # the first click only asks


def test_view_is_wired_and_plain_text():
    main, route, extensions = read("main.js"), read("route.js"), read("extensions.js")
    assert 'import { Packages } from "./packages.js"' in main
    assert '{ id: "packages", label: "Paquetes", icon: "file", mode: "simple" }' in main
    assert '<${Packages} token=${token} />' in main
    assert '"packages"' in re.search(r"const SECTION_IDS = \[(.*?)\]", route)[1]
    assert '"packages"' in re.search(r"RESERVED_SECTIONS = \[(.*?)\]", extensions)[1]
    for name in ("packages.js", "packages-model.js"):
        source = read(name)
        assert source.startswith("// Copyright 2026 Ricardoallexis and contributors\n") or source.startswith("// Copyright 2026 Ricardoallexis and contributors\r\n")
        for bad in ("innerHTML", "dangerouslySetInnerHTML", "__html", "eval(", "style=", "localStorage", "document.write"):
            assert bad not in source, f"{name}: {bad}"
    gui_init = (STATIC_DIR.parent / "__init__.py").read_text(encoding="utf-8")
    assert "add_package_routes(api, services)" in gui_init
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    assert "/* PAQ-T02 */" in css and ".package-row.degraded" in css and "style=" not in read("packages.js")
