import json
import re
import tomllib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CLAUDE_TEMPLATE = ROOT / "docs" / "permissions" / "claude-settings.template.json"
CODEX_TEMPLATE = ROOT / "docs" / "permissions" / "codex-config.template.toml"
GUIDE = ROOT / "docs" / "PERMISSIONS.md"

PERSONAL_DATA = re.compile(
    r"[A-Za-z]:[\\/]|//[A-Za-z]/|/Users/|/home/|"
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)


def _assert_safe_allow_rules(rules):
    for rule in rules:
        assert not rule.startswith("*")
        assert rule not in {
            "Bash",
            "Bash(*)",
            "Bash(**)",
            "PowerShell",
            "PowerShell(*)",
        }
        assert not rule.startswith(("Bash(*", "PowerShell(*"))
        assert not rule.startswith(
            (
                "Bash(bash -c",
                "Bash(sh -c",
                "Bash(pwsh",
                "Bash(powershell",
                "Bash(cmd",
                "PowerShell(bash -c",
                "PowerShell(sh -c",
                "PowerShell(pwsh",
                "PowerShell(powershell",
                "PowerShell(cmd",
            )
        )


def _codex_strings(value):
    if isinstance(value, dict):
        for nested in value.values():
            yield from _codex_strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _codex_strings(nested)
    elif isinstance(value, str):
        yield value


def _assert_restricted_codex(codex):
    values = list(_codex_strings(codex))
    assert "never" not in values
    assert "danger-full-access" not in values


def test_permission_templates_parse_and_use_restricted_settings():
    claude = json.loads(CLAUDE_TEMPLATE.read_text(encoding="utf-8"))
    codex = tomllib.loads(CODEX_TEMPLATE.read_text(encoding="utf-8"))

    assert set(claude) == {"permissions"}
    permissions = claude["permissions"]
    assert set(permissions) == {
        "allow",
        "deny",
        "ask",
        "defaultMode",
        "disableBypassPermissionsMode",
        "blockReadsOutsideWorkingDirectories",
    }
    assert permissions["disableBypassPermissionsMode"] == "disable"
    # git diff and git log can write files (--output, --no-index): they ask, never auto-approve.
    assert not any(rule.startswith(("Bash(git diff", "Bash(git log")) for rule in permissions["allow"])
    assert permissions["defaultMode"] == "default"
    assert permissions["blockReadsOutsideWorkingDirectories"] is True
    assert any("git push" in rule for rule in permissions["deny"])
    assert any("~/.ssh/**" in rule for rule in permissions["deny"])
    assert not any(
        "bypassPermissions" in rule or "dangerously-skip-permissions" in rule
        for rule in permissions["allow"]
    )
    _assert_safe_allow_rules(permissions["allow"])

    assert codex["approval_policy"] == "on-request"
    assert codex["sandbox_mode"] == "workspace-write"
    assert codex["sandbox_workspace_write"]["network_access"] is False
    assert codex["sandbox_workspace_write"]["writable_roots"] == []
    assert codex["windows"]["sandbox"] == "elevated"
    _assert_restricted_codex(codex)


def test_templates_and_guide_have_no_personal_paths_or_emails():
    for path in (CLAUDE_TEMPLATE, CODEX_TEMPLATE, GUIDE):
        for line in path.read_text(encoding="utf-8").splitlines():
            assert not PERSONAL_DATA.search(line), f"Personal path or email in {path}"

            if "~/" in line:
                claude = json.loads(CLAUDE_TEMPLATE.read_text(encoding="utf-8"))
                home_denies = {
                    rule
                    for rule in claude["permissions"]["deny"]
                    if "~/" in rule
                }
                rule_on_line = line.strip().rstrip(",").strip('"')
                assert path == CLAUDE_TEMPLATE
                assert rule_on_line in home_denies


@pytest.mark.parametrize(
    "path",
    [
        r"C:\Users\person\file",
        "C:/Users/person/file",
        "D:/projects/file",
        "B:/projects/file",
        "//d/projects/file",
        "/Users/person/file",
        "/home/person/file",
        "person@example.com",
    ],
)
def test_personal_path_detector_rejects_machine_paths(path):
    assert PERSONAL_DATA.search(path)


@pytest.mark.parametrize("rule", ["Bash(* *)", "PowerShell"])
def test_broad_allow_mutations_are_rejected(rule):
    with pytest.raises(AssertionError):
        _assert_safe_allow_rules([rule])


def test_codex_profile_mutation_cannot_enable_danger_mode():
    mutated = {
        "profiles": {
            "x": {
                "sandbox_mode": "danger-full-access",
                "approval_policy": "never",
            }
        }
    }
    with pytest.raises(AssertionError):
        _assert_restricted_codex(mutated)


def test_never_allowed_flags_are_not_allowed_by_templates():
    guide = GUIDE.read_text(encoding="utf-8")
    never_allowed = guide.split("## Never allowed", maxsplit=1)[1].split(
        "\n## ", maxsplit=1
    )[0]
    flags = re.findall(r"`(--[^`]+)`", never_allowed)
    assert flags

    claude = json.loads(CLAUDE_TEMPLATE.read_text(encoding="utf-8"))
    codex = tomllib.loads(CODEX_TEMPLATE.read_text(encoding="utf-8"))
    allowed = " ".join(claude["permissions"]["allow"])
    allowed += " ".join(str(value) for value in codex.values())
    assert all(flag not in allowed for flag in flags)


def test_permission_profiles_remain_inactive():
    assert not (ROOT / ".claude" / "settings.json").exists()
    assert not (ROOT / ".codex" / "config.toml").exists()
