# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""PAQ-T02: installing a package from a .zip and the example by reference, with archives built in memory."""
from __future__ import annotations

import io
import json
import struct
import zipfile
from pathlib import Path

import pytest

import test_installed_packages as base
from multiagent.services import ServiceError, installed
from multiagent.services.installed import (SHIPPED_EXAMPLES, ZIP_EXTRACT_MAX_BYTES, ZIP_MAX_FILES, ZIP_UPLOAD_MAX_BYTES,
                                           extract_zip_safely)

world = base.world
ACTOR = base.ACTOR


def make_zip(entries: dict[str, bytes | str], *, top: str = "", symlinks: dict[str, str] | None = None,
             stored: bool = False) -> bytes:
    """A .zip in memory. A name with a backslash is written as is (ZipInfo would turn it into "/" on Windows)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            info = zipfile.ZipInfo(top + name)
            info.filename = top + name
            info.orig_filename = top + name
            info.compress_type = archive.compression
            archive.writestr(info, content)
        for name, target in (symlinks or {}).items():
            info = zipfile.ZipInfo(name)
            info.external_attr = 0o120777 << 16
            archive.writestr(info, target)
    return buffer.getvalue()


def data_package(tmp: Path) -> dict[str, bytes]:
    """The files of a valid package made of data only, as zip entries."""
    folder = base.make_data_bundle(tmp / "origin")
    return {path.relative_to(folder).as_posix(): path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file()}


def code_package() -> dict[str, bytes]:
    folder = base.FIXTURE
    return {path.relative_to(folder).as_posix(): path.read_bytes() for path in sorted(folder.rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts}


def staging_of(services) -> Path:
    return base.installed_dir(services) / ".staging"


def left_in_staging(services) -> list[str]:
    folder = staging_of(services)
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def assert_error(call, status: int, reason: str) -> ServiceError:
    with pytest.raises(ServiceError) as error:
        call()
    assert (error.value.status, error.value.details["reason"]) == (status, reason), error.value.details
    return error.value


def test_zip_with_single_top_folder_installs(world):
    start, tmp = world
    services = start()
    data = make_zip(data_package(tmp), top="demo/")
    entry = services.installed.install_zip("demo", data, actor=ACTOR, channel="gui", filename="Mi paquete.zip")
    assert (entry["name"], entry["level"], entry["version"], entry["state"]) == ("demo", "data", 1, "ok")
    assert entry["source_label"] == "Mi paquete.zip" and entry["channel"] == "gui" and "path" not in entry
    copy = services.bundle_path("demo")
    assert (copy / "workflows" / "spec_review.yaml").is_file() and not (copy / "demo").exists()  # the folder was unwrapped
    assert left_in_staging(services) == []
    assert "demo" in start().bundle_names()  # it survives a restart


def test_zip_without_top_folder_installs(world):
    start, tmp = world
    services = start()
    services.installed.install_zip("flat", make_zip(data_package(tmp)), actor=ACTOR)
    assert (services.bundle_path("flat") / "workflows" / "spec_review.yaml").is_file()


def test_macos_resource_forks_are_ignored(world):
    start, tmp = world
    services = start()
    entries = {**{f"demo/{k}": v for k, v in data_package(tmp).items()}, "__MACOSX/demo/._x": b"fork"}
    services.installed.install_zip("demo", make_zip(entries), actor=ACTOR)
    assert not (services.bundle_path("demo") / "__MACOSX").exists()


UNSAFE = {
    "dots": {"../x": b"x"},
    "absolute": {"/x": b"x"},
    "drive": {"C:/x": b"x"},
    "drive_relative": {"C:x": b"x"},
    "backslash": {"a\\b.yaml": b"x"},
    "inner_dots": {"a/../../x": b"x"},
    "stream": {"a/b:c": b"x"},
}


@pytest.mark.parametrize("case", sorted(UNSAFE))
def test_zip_unsafe_entries_rejected(world, case):
    start, tmp = world
    services = start()
    data = make_zip({"workflows/ok.yaml": b"id: ok\n", **UNSAFE[case]})
    assert_error(lambda: services.installed.install_zip("demo", data, actor=ACTOR), 422, "zip_unsafe_entry")
    assert left_in_staging(services) == [] and services.installed.list() == []
    outside = tmp / "x"
    assert not outside.exists() and not (tmp / "local" / "x").exists()


def test_zip_symlink_and_repeated_name_rejected(world):
    start, tmp = world
    services = start()
    link = make_zip({"workflows/ok.yaml": b"id: ok\n"}, symlinks={"workflows/link": "/etc/passwd"})
    assert_error(lambda: services.installed.install_zip("demo", link, actor=ACTOR), 422, "zip_unsafe_entry")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.yaml", "one")
        with pytest.warns(UserWarning):
            archive.writestr("a.yaml", "two")
    assert_error(lambda: services.installed.install_zip("demo", buffer.getvalue(), actor=ACTOR), 422, "zip_unsafe_entry")
    upper = io.BytesIO()
    with zipfile.ZipFile(upper, "w") as archive:  # a name that differs only by case is the same file on Windows
        archive.writestr("a.yaml", "one")
        archive.writestr("A.yaml", "two")
    assert_error(lambda: services.installed.install_zip("demo", upper.getvalue(), actor=ACTOR), 422, "zip_unsafe_entry")
    assert left_in_staging(services) == [] and services.installed.list() == []


def test_zip_file_that_is_also_a_folder_rejected(world):
    start, _ = world
    services = start()
    data = make_zip({"a": b"x", "a/b.yaml": b"y"})
    assert_error(lambda: services.installed.install_zip("demo", data, actor=ACTOR), 422, "zip_unsafe_entry")
    assert left_in_staging(services) == []


def patch_declared_size(data: bytes, declared: int) -> bytes:
    """Make the central directory and the local header of the first entry declare ``declared`` bytes of content."""
    patched = bytearray(data)
    central = patched.index(b"PK\x01\x02")
    struct.pack_into("<I", patched, central + 24, declared)
    struct.pack_into("<I", patched, 22, declared)
    return bytes(patched)


def test_zip_too_large(world):
    start, tmp = world
    services = start()
    many = make_zip({f"workflows/f{i}.yaml": b"" for i in range(ZIP_MAX_FILES + 1)})
    assert_error(lambda: services.installed.install_zip("demo", many, actor=ACTOR), 422, "zip_too_large")
    assert left_in_staging(services) == []
    # A small bomb: 51 MB of zeros in a few KB, whose declared size is 100 bytes. The real bytes are the ones counted.
    bomb = patch_declared_size(make_zip({"workflows/big.yaml": b"0" * (ZIP_EXTRACT_MAX_BYTES + 2**20)}), 100)
    assert len(bomb) < 200_000
    assert_error(lambda: services.installed.install_zip("demo", bomb, actor=ACTOR), 422, "zip_too_large")
    assert left_in_staging(services) == [] and services.installed.list() == []
    # The same, with the cap passed in: nothing is written beyond it.
    destination = tmp / "extract-here"
    with pytest.raises(ServiceError) as error:
        extract_zip_safely(patch_declared_size(make_zip({"a.yaml": b"0" * 5000}), 10), destination, max_bytes=1000)
    assert error.value.details["reason"] == "zip_too_large" and not destination.exists()
    # Declared over the cap is refused before any byte is written.
    honest = make_zip({"a.yaml": b"0" * 5000})
    with pytest.raises(ServiceError) as error:
        extract_zip_safely(honest, destination, max_bytes=1000)
    assert error.value.details["reason"] == "zip_too_large" and not destination.exists()
    with pytest.raises(ServiceError) as error:
        extract_zip_safely(b"x" * (ZIP_UPLOAD_MAX_BYTES + 1), destination)
    assert error.value.details["reason"] == "zip_too_large"


def test_zip_understating_a_small_file_is_invalid_not_trusted(world):
    start, tmp = world
    services = start()
    data = patch_declared_size(make_zip({"workflows/a.yaml": b"id: a\n" * 50}), 3)
    assert_error(lambda: services.installed.install_zip("demo", data, actor=ACTOR), 422, "zip_invalid")
    assert left_in_staging(services) == []


def test_zip_invalid(world):
    start, tmp = world
    services = start()
    for data in (b"not a zip at all", b"", make_zip({"a.yaml": b"x"})[:-30]):
        assert_error(lambda: services.installed.install_zip("demo", data, actor=ACTOR), 422, "zip_invalid")
    damaged = bytearray(make_zip({"workflows/a.yaml": b"id: a\n" * 100}))
    damaged[60] ^= 0xFF  # inside the compressed bytes of the first entry
    assert_error(lambda: services.installed.install_zip("demo", bytes(damaged), actor=ACTOR), 422, "zip_invalid")
    empty = io.BytesIO()
    zipfile.ZipFile(empty, "w").close()
    assert_error(lambda: services.installed.install_zip("demo", empty.getvalue(), actor=ACTOR), 422, "zip_invalid")
    assert left_in_staging(services) == [] and services.installed.list() == []


def test_zip_stored_entries_extract(world):
    start, tmp = world
    services = start()
    services.installed.install_zip("demo", make_zip(data_package(tmp), top="x/", stored=True), actor=ACTOR)
    assert services.installed.get("demo")["state"] == "ok"


def test_zip_install_checks_name_and_actor_and_busy_names(world):
    start, tmp = world
    services = start()
    data = make_zip(data_package(tmp))
    with pytest.raises(ServiceError) as error:
        services.installed.install_zip("bad name!", data, actor=ACTOR)
    assert error.value.status == 422
    with pytest.raises(ServiceError) as other:
        services.installed.install_zip("demo", data, actor="agent:codex")
    assert other.value.details["reason"] == "confirmation_required" and left_in_staging(services) == []
    services.installed.install_zip("demo", data, actor=ACTOR)
    assert_error(lambda: services.installed.install_zip("demo", data, actor=ACTOR), 409, "bundle_name_in_use")
    assert left_in_staging(services) == []


def test_reinstall_zip_replaces_a_changed_copy(world):
    start, tmp = world
    services = start()
    package = data_package(tmp)
    first = services.installed.install_zip("demo", make_zip(package, top="demo/"), actor=ACTOR)
    (services.bundle_path("demo") / "workflows" / "spec_review.yaml").write_text("id: spec_review\n# edited\n",
                                                                               encoding="utf-8")
    again = start()
    assert again.installed.get("demo")["state"] == "changed"
    entry = again.installed.reinstall_zip("demo", make_zip(package, top="demo/"), actor=ACTOR, channel="gui")
    assert (entry["version"], entry["state"], entry["channel"]) == (2, "ok", "gui") and entry["fingerprint"] == first["fingerprint"]
    assert_error(lambda: again.installed.reinstall_zip("nope", make_zip(package), actor=ACTOR), 409, "bundle_not_installed")
    assert left_in_staging(again) == []


def test_code_zip_needs_advanced_mode(world, tmp_path):
    start, tmp = world
    services = start()
    data = make_zip(code_package(), top="team/")
    assert services.gui_settings.mode() == "simple"
    error = assert_error(lambda: services.installed.install_zip("team", data, actor=ACTOR, require_advanced_for_code=True),
                         409, "advanced_mode_required")
    assert error.details["action"] == "install_code"
    assert left_in_staging(services) == [] and services.installed.list() == [] and "team" not in services.bundle_names()
    services.gui_settings.set_mode("advanced")
    entry = services.installed.install_zip("team", data, actor=ACTOR, require_advanced_for_code=True)
    assert entry["level"] == "code" and services.bundle_scans._external(services.bundle_path("team")) is True
    # Installing never ran the package, and its first load goes through the scan gate (a fake SkillSpector).
    fake_python = tmp / "tool" / "Scripts" / "python.exe"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_bytes(b"fake")
    services.settings.skillspector_python = str(fake_python)
    services.bundle_scans.verifier = lambda path: True
    services.bundle_scans.env = {}
    with pytest.raises(ServiceError) as blocked:
        services.definitions.validate_bundle("team")
    assert blocked.value.status == 409 and blocked.value.code.startswith("bundle_scan")


def test_data_zip_installs_in_simple_mode(world):
    start, tmp = world
    services = start()
    assert services.gui_settings.mode() == "simple"
    entry = services.installed.install_zip("demo", make_zip(data_package(tmp)), actor=ACTOR, require_advanced_for_code=True)
    assert entry["level"] == "data"


def test_example_install_by_reference(world, monkeypatch):
    start, tmp = world
    services = start()
    assert SHIPPED_EXAMPLES == {"general": "examples/bundles/general"}
    assert services.installed.example_available() is True
    entry = services.installed.install_shipped(actor=ACTOR, channel="gui")
    assert (entry["source"], entry["level"], entry["name"], entry["state"]) == ("shipped", "code", "general", "ok")
    assert entry["source_label"] and "path" not in entry and str(installed.PROJECT_ROOT) not in json.dumps(entry)
    assert "general" in services.bundle_names()
    assert not (base.installed_dir(services) / "general").exists()  # no copy
    assert services.bundle_path("general") == (installed.PROJECT_ROOT / "examples/bundles/general").resolve()
    assert services.bundle_scans._external(services.bundle_path("general")) is False  # product code stays exempt
    assert services.installed.example_available() is False
    assert_error(lambda: services.installed.install_shipped(actor=ACTOR), 409, "bundle_name_in_use")
    again = start()  # a restart registers it again, from the name and never from the stored path
    assert "general" in again.bundle_names() and again.installed.get("general")["source"] == "shipped"
    registry = base.installed_dir(again) / "registry.json"
    document = json.loads(registry.read_text(encoding="utf-8"))
    document["bundles"]["general"]["path"] = "../../somewhere/else"
    registry.write_text(json.dumps(document), encoding="utf-8")
    assert start().bundle_path("general") == (installed.PROJECT_ROOT / "examples/bundles/general").resolve()
    removed = again.installed.remove("general", actor=ACTOR)
    assert removed["removed"] is True and "general" not in again.bundle_names()
    assert (installed.PROJECT_ROOT / "examples/bundles/general").is_dir()  # the product's folder is never touched
    with pytest.raises(ServiceError) as unknown:
        services.installed.install_shipped("other", actor=ACTOR)
    assert unknown.value.status == 422
    monkeypatch.setattr(installed, "IN_CHECKOUT", False)
    assert services.installed.example_available() is False
    assert_error(lambda: services.installed.install_shipped(actor=ACTOR), 409, "example_not_available")


def test_shipped_entry_outside_a_checkout_shows_missing(world, monkeypatch):
    start, _ = world
    services = start()
    services.installed.install_shipped(actor=ACTOR)
    monkeypatch.setattr(installed, "IN_CHECKOUT", False)
    again = start()
    assert again.installed.get("general")["state"] == "missing" and "general" not in again.bundle_names()
