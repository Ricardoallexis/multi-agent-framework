# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Portable office package checks."""
from multiagent.offices.package_checks import check_files, excluded_path, in_file_tools_root


def test_secret_and_personal_data_are_located_and_redacted():
    files = {"areas/demo/README.md": b"Hello\ncontact@example.test\npassword=samplevalue\n"}
    result = check_files(files)
    assert {item["type"] for item in result.found} == {"email", "secret_key"}
    assert {item["line"] for item in result.found} == {2, 3}
    assert all(item["path"] == "areas/demo/README.md" for item in result.found)
    assert "contact@example.test" not in result.redacted["areas/demo/README.md"]
    assert "samplevalue" not in result.redacted["areas/demo/README.md"]


def test_unreadable_content_fails_closed():
    result = check_files({"bad.bin": b"a\x00b", "bad.md": b"\xff"})
    assert result.redacted == {}
    assert result.unreadable == ["bad.bin", "bad.md"]


def test_personal_data_in_a_filename_is_never_attached():
    path = "areas/demo/samples/contact@example.test.txt"
    result = check_files({path: b"safe text"})
    assert result.redacted == {}
    assert result.unreadable == [path]
    assert result.found == [{"path": path, "line": 1, "type": "email"}]


def test_fixed_exclusions_cover_private_directories_and_databases():
    for path in ("runs/x.txt", "prompts/conversations/a.md", "samples/inputs/a.txt",
                 "logs/a.txt", ".env", ".local/x.md", "file_tools/a.txt", "state.sqlite3"):
        assert excluded_path(path)


def test_env_variants_and_sqlite_sidecars_are_excluded():
    for path in (".env.local", "samples/.env.prod", "prompts/.env.txt",
                 "state.sqlite-wal", "state.db-shm", "state.sqlite3-journal"):
        assert excluded_path(path)
        assert path in check_files({path: b"safe"}).unreadable


def test_configured_file_tools_root_can_have_any_name(tmp_path):
    root = tmp_path / "custom_vault"
    assert in_file_tools_root(root / "nested" / "sample.txt", root)
    assert not in_file_tools_root(tmp_path / "other" / "sample.txt", root)
