# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""INI-T01: the double-click launchers (``Iniciar.cmd``, ``iniciar.sh``) and ``gui --launcher``.

Every launcher runs in a copy inside ``tmp_path``, with fake tools first on the ``PATH``: a fake Python (it logs its
arguments and creates a fake ``.venv``) and a fake ``explorer``, ``xdg-open`` or ``open``. Nothing is installed,
nothing reaches the network and no browser opens. Each test checks that the fake received the call.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from multiagent.services.gui_settings import ALLOWED_VALUES

ROOT = Path(__file__).resolve().parents[1]
DOWNLOAD_URL = "https://www.python.org/downloads/"
SH = shutil.which("sh")
IS_WINDOWS = sys.platform == "win32"
needs_sh = pytest.mark.skipif(SH is None, reason="needs sh")
needs_cmd = pytest.mark.skipif(not IS_WINDOWS, reason="Iniciar.cmd only runs on Windows")

FAKE_PYTHON_SH = """#!/bin/sh
name=$(basename "$0")
echo "$name: $*" >> fake.log
if [ "$1" = "-c" ]; then
  case " $FAKE_PY_OK " in *" $name "*) exit 0 ;; esac
  exit 1
fi
if [ "$1 $2 $3" = "-m venv .venv" ]; then
  mkdir -p .venv/bin
  cp "$(dirname "$0")/venv-python" .venv/bin/python
  chmod +x .venv/bin/python
fi
exit 0
"""
FAKE_VENV_PYTHON_SH = """#!/bin/sh
echo "venv: $*" >> fake.log
exit 0
"""
FAKE_OPENER_SH = """#!/bin/sh
echo "$(basename "$0"): $*" >> fake.log
exit 0
"""
FAKE_PY_CMD = """@echo off
>>fake.log echo py: %*
if "%~2"=="-c" (
  if "%~1"=="-%FAKE_PY_VERSION%" (exit /b 0) else (exit /b 1)
)
if "%~2 %~3 %~4"=="-m venv .venv" (
  mkdir .venv\\Scripts
  copy "%~dp0venv-python.cmd" .venv\\Scripts\\python.cmd >nul
)
exit /b 0
"""
FAKE_VENV_PYTHON_CMD = """@echo off
>>fake.log echo venv: %*
exit /b 0
"""
FAKE_EXPLORER_CMD = """@echo off
>>fake.log echo explorer: %*
exit /b 0
"""


def _write(path: Path, text: str, *, newline: str) -> None:
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))
    path.chmod(0o755)


def _copy_product(dest: Path, launcher: str) -> Path:
    """The launcher, a requirements.txt and a minimal package, in a folder of its own."""
    dest.mkdir()
    shutil.copy(ROOT / launcher, dest / launcher)
    shutil.copy(ROOT / "requirements.txt", dest / "requirements.txt")
    (dest / "multiagent").mkdir()
    (dest / "multiagent" / "__init__.py").write_text("", encoding="utf-8")
    return dest


def _sh_world(tmp_path: Path, *, python_ok: str = "python3.13") -> dict:
    """Fakes for every Python the launcher may look for (so a real one is never found) and for both openers."""
    fake = tmp_path / "fakebin"
    fake.mkdir()
    for name in ("python3.14", "python3.13", "python3.12", "python3"):
        _write(fake / name, FAKE_PYTHON_SH, newline="\n")
    _write(fake / "venv-python", FAKE_VENV_PYTHON_SH, newline="\n")
    for name in ("xdg-open", "open"):
        _write(fake / name, FAKE_OPENER_SH, newline="\n")
    home = _copy_product(tmp_path / "product", "iniciar.sh")
    env = dict(os.environ, PATH=str(fake) + os.pathsep + os.environ["PATH"], FAKE_PY_OK=python_ok)
    env.pop("LOCAL_DIR", None)
    return {"home": home, "env": env}


def _run_sh(world: dict, **env_extra) -> subprocess.CompletedProcess:
    env = dict(world["env"], **env_extra)
    return subprocess.run([SH, "iniciar.sh"], cwd=world["home"], env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)


def _cmd_world(tmp_path: Path, *, version: str = "3.13") -> dict:
    fake = tmp_path / "fakebin"
    fake.mkdir()
    _write(fake / "py.cmd", FAKE_PY_CMD, newline="\r\n")
    _write(fake / "venv-python.cmd", FAKE_VENV_PYTHON_CMD, newline="\r\n")
    _write(fake / "explorer.cmd", FAKE_EXPLORER_CMD, newline="\r\n")
    home = _copy_product(tmp_path / "product", "Iniciar.cmd")
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    env = {"SystemRoot": system_root, "ComSpec": os.environ.get("ComSpec", system_root + r"\System32\cmd.exe"),
           "PATH": str(fake) + os.pathsep + system_root + r"\System32", "FAKE_PY_VERSION": version,
           "PATHEXT": ".COM;.EXE;.BAT;.CMD"}
    return {"home": home, "env": env}


def _run_cmd(world: dict, **env_extra) -> subprocess.CompletedProcess:
    env = dict(world["env"], **env_extra)
    return subprocess.run(["cmd", "/c", "Iniciar.cmd"], cwd=world["home"], env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8", timeout=120, check=False)


def _calls(home: Path) -> list[tuple[str, str]]:
    """(tool, arguments) for every logged call, in order."""
    log = home / "fake.log"
    rows = []
    for line in (log.read_text(encoding="utf-8").splitlines() if log.exists() else []):
        tool, _, args = line.partition(": ")
        rows.append((tool.strip(), args.strip()))
    return rows


def _module_calls(home: Path) -> list[tuple[str, str]]:
    return [(tool, args) for tool, args in _calls(home) if re.match(r"(-\S+ )?-m ", args)]


def _sha(home: Path) -> str:
    return hashlib.sha256((home / "requirements.txt").read_bytes()).hexdigest()


def _assert_iso_hour(text: str) -> None:
    value = text.strip()
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?[+-]\d\d:\d\d", value), value
    assert datetime.fromisoformat(value).tzinfo is not None


FIRST_RUN_SH = [("python3.13", "-m venv .venv"), ("venv", "-m pip install -r requirements.txt"),
                ("venv", "-m multiagent init"), ("venv", "-m multiagent gui --launcher")]
FIRST_RUN_CMD = [("py", "-3.13 -m venv .venv"), ("venv", "-m pip install -r requirements.txt"),
                 ("venv", "-m multiagent init"), ("venv", "-m multiagent gui --launcher")]


# ---------------------------------------------------------------- iniciar.sh
@needs_sh
def test_sh_first_run_order(tmp_path):
    world = _sh_world(tmp_path)
    result = _run_sh(world)
    home = world["home"]
    assert result.returncode == 0, result.stdout + result.stderr
    assert _module_calls(home) == FIRST_RUN_SH
    assert (home / ".venv" / ".maf-ready").read_text(encoding="utf-8").strip() == _sha(home)
    _assert_iso_hour((home / ".local" / "primer-recorrido-inicio.txt").read_text(encoding="utf-8"))


@needs_sh
def test_sh_uses_local_dir_when_defined(tmp_path):
    world = _sh_world(tmp_path)
    result = _run_sh(world, LOCAL_DIR="otra-carpeta")
    home = world["home"]
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_iso_hour((home / "otra-carpeta" / "primer-recorrido-inicio.txt").read_text(encoding="utf-8"))
    assert not (home / ".local").exists()


@needs_sh
def test_sh_second_run_only_starts(tmp_path):
    world = _sh_world(tmp_path)
    home = world["home"]
    assert _run_sh(world).returncode == 0
    hour = (home / ".local" / "primer-recorrido-inicio.txt").read_text(encoding="utf-8")
    (home / "fake.log").unlink()
    result = _run_sh(world)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _module_calls(home) == [("venv", "-m multiagent gui --launcher")]
    assert (home / ".local" / "primer-recorrido-inicio.txt").read_text(encoding="utf-8") == hour


@needs_sh
def test_sh_requirements_changed_reinstalls(tmp_path):
    world = _sh_world(tmp_path)
    home = world["home"]
    assert _run_sh(world).returncode == 0
    (home / "fake.log").unlink()
    with (home / "requirements.txt").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n# changed\n")
    assert _run_sh(world).returncode == 0
    assert _module_calls(home) == [("venv", "-m pip install -r requirements.txt"), ("venv", "-m multiagent init"),
                                   ("venv", "-m multiagent gui --launcher")]
    assert (home / ".venv" / ".maf-ready").read_text(encoding="utf-8").strip() == _sha(home)


@needs_sh
def test_sh_no_python_opens_download_page(tmp_path):
    world = _sh_world(tmp_path, python_ok="")
    result = _run_sh(world)
    home = world["home"]
    assert result.returncode != 0
    openers = [call for call in _calls(home) if call[0] in ("xdg-open", "open")]
    assert len(openers) == 1 and openers[0][1] == DOWNLOAD_URL  # the fake received it: no real browser opened
    assert "Python 3.13" in result.stdout and "No encontré Python" in result.stdout
    assert not (home / ".venv").exists() and not (home / ".local").exists()
    assert _module_calls(home) == []


@needs_sh
def test_sh_rejects_python_311(tmp_path):
    world = _sh_world(tmp_path, python_ok="")  # python3 exists but its version check fails: it says 3.11
    result = _run_sh(world)
    home = world["home"]
    assert result.returncode != 0
    assert ("python3", "-c import sys; sys.exit(0 if (3, 12) <= sys.version_info[:2] <= (3, 14) else 1)") in _calls(home)
    assert any(call[1] == DOWNLOAD_URL for call in _calls(home) if call[0] in ("xdg-open", "open"))
    assert not (home / ".venv").exists()


@needs_sh
def test_sh_accepts_plain_python3_when_its_version_fits(tmp_path):
    world = _sh_world(tmp_path, python_ok="python3")
    result = _run_sh(world)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _module_calls(world["home"])[0] == ("python3", "-m venv .venv")


@needs_sh
def test_sh_reports_the_failed_step_in_one_line(tmp_path):
    world = _sh_world(tmp_path)
    (tmp_path / "fakebin" / "venv-python").write_text(
        '#!/bin/sh\necho "venv: $*" >> fake.log\n[ "$3" = "install" ] && exit 1\nexit 0\n', encoding="utf-8", newline="\n")
    result = _run_sh(world)
    home = world["home"]
    assert result.returncode != 0
    assert "paso 2 de 4, instalar las dependencias" in result.stdout
    assert not (home / ".venv" / ".maf-ready").exists()  # a failed install is not marked as ready
    assert ("venv", "-m multiagent init") not in _calls(home)


# ---------------------------------------------------------------- Iniciar.cmd
@needs_cmd
def test_cmd_first_run_order(tmp_path):
    world = _cmd_world(tmp_path)
    result = _run_cmd(world)
    home = world["home"]
    assert result.returncode == 0, result.stdout + result.stderr
    assert _module_calls(home) == FIRST_RUN_CMD
    assert (home / ".venv" / ".maf-ready").read_text(encoding="utf-8").strip() == _sha(home)
    _assert_iso_hour((home / ".local" / "primer-recorrido-inicio.txt").read_text(encoding="utf-8-sig"))


@needs_cmd
def test_cmd_second_run_only_starts(tmp_path):
    world = _cmd_world(tmp_path)
    home = world["home"]
    assert _run_cmd(world).returncode == 0
    hour = (home / ".local" / "primer-recorrido-inicio.txt").read_bytes()
    (home / "fake.log").unlink()
    assert _run_cmd(world).returncode == 0
    assert _module_calls(home) == [("venv", "-m multiagent gui --launcher")]
    assert (home / ".local" / "primer-recorrido-inicio.txt").read_bytes() == hour


@needs_cmd
def test_cmd_no_python(tmp_path):
    world = _cmd_world(tmp_path, version="")
    result = _run_cmd(world)
    home = world["home"]
    assert result.returncode != 0
    assert ("explorer", DOWNLOAD_URL) in _calls(home)  # the fake received it: no real browser opened
    assert "Python 3.13" in result.stdout and "py launcher" in result.stdout and "No encontré Python" in result.stdout
    assert not (home / ".venv").exists() and not (home / ".local").exists()
    assert _module_calls(home) == []


# ---------------------------------------------------------------- both launchers
def test_never_runs_generated_exe():
    for name in ("Iniciar.cmd", "iniciar.sh"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for forbidden in ("multiagent.exe", "pip.exe", "pip install -e", "requirements-dev.txt"):
            assert forbidden not in text, (name, forbidden)
        assert re.search(r"\buv\b", text) is None, name
        assert "-m pip install -r requirements.txt" in text and "-m multiagent init" in text
        assert "-m multiagent gui --launcher" in text


def test_line_endings():
    cmd = (ROOT / "Iniciar.cmd").read_bytes()
    sh = (ROOT / "iniciar.sh").read_bytes()
    assert cmd.count(b"\r\n") == cmd.count(b"\n") > 0 and b"chcp 65001" in cmd
    assert b"\r" not in sh and sh.startswith(b"#!/bin/sh\n")
    rules = (ROOT / ".gitattributes").read_text(encoding="utf-8").split()
    assert "*.cmd text eol=crlf".split() == rules[rules.index("*.cmd"):rules.index("*.cmd") + 3]
    assert "*.sh text eol=lf".split() == rules[rules.index("*.sh"):rules.index("*.sh") + 3]


def test_launcher_touches_nothing_global():
    for name in ("Iniciar.cmd", "iniciar.sh"):
        text = (ROOT / name).read_text(encoding="utf-8").lower()
        for forbidden in ("setx", "reg add", "sudo", "--user", "export path", "set path"):
            assert forbidden not in text, (name, forbidden)


# ---------------------------------------------------------------- gui --launcher
class _Stop(Exception):
    pass


@pytest.fixture
def launcher_env(tmp_path, monkeypatch):
    import multiagent.cli

    home = tmp_path / "home"
    home.mkdir()
    local = tmp_path / "local"
    local.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(multiagent.cli, "_check_port_free", lambda host, port: None)
    for name, value in (("APP_HOST", "127.0.0.1"), ("LOCAL_DIR", str(local)), ("WORKER_ENABLED", "false"),
                        ("GUI_TOKEN_FILE", str(home / ".multiagent" / "gui-token")), ("MOCK_MODE", "false")):
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("MULTIAGENT_REAL", raising=False)
    return local


def _models_seen_at_build(monkeypatch, **run_kwargs) -> str | None:
    """MOCK_MODE as ``run`` leaves it when it builds the system; stops there, so no real adapter is ever built."""
    import multiagent.bootstrap
    from multiagent.gui.__main__ import run

    seen: list[str | None] = []
    monkeypatch.setenv("MOCK_MODE", "false")  # run() writes it: start every call from the same value

    def stop(settings):
        seen.append(os.environ.get("MOCK_MODE"))
        raise _Stop

    monkeypatch.setattr(multiagent.bootstrap, "build_system", stop)
    with pytest.raises(_Stop):
        run(no_browser=True, **run_kwargs)
    return seen[0]


def test_launcher_reads_models_setting(launcher_env, monkeypatch):
    # no file: Mock
    assert _models_seen_at_build(monkeypatch, launcher=True) == "true"
    # {"models": "real"}: the launcher does not force Mock
    (launcher_env / "gui-settings.json").write_text('{"models": "real"}', encoding="utf-8")
    assert _models_seen_at_build(monkeypatch, launcher=True) == "false"
    # without --launcher the file is ignored: Mock as always
    assert _models_seen_at_build(monkeypatch) == "true"
    # {"models": "mock"} and a bad value read as Mock
    for body in ('{"models": "mock"}', '{"models": "otro"}', "no es json"):
        (launcher_env / "gui-settings.json").write_text(body, encoding="utf-8")
        assert _models_seen_at_build(monkeypatch, launcher=True) == "true", body


def test_real_flag_wins_over_the_models_setting(launcher_env, monkeypatch):
    (launcher_env / "gui-settings.json").write_text('{"models": "mock"}', encoding="utf-8")
    assert _models_seen_at_build(monkeypatch, launcher=True, real=True) == "false"


def test_launcher_in_mock_says_how_to_use_real_models(launcher_env, monkeypatch, capsys):
    import multiagent.gui.__main__ as gui_main

    started = []
    monkeypatch.setattr(gui_main, "serve", lambda app, **kwargs: started.append(kwargs))
    gui_main.run(port=8123, no_browser=True, launcher=True)
    out = capsys.readouterr().out
    assert started and "Mock (synthetic results)" in out
    assert "Para usar modelos reales: Inicio › Pasar a modelos reales" in out
    gui_main.run(port=8123, no_browser=True)  # without --launcher the line is not printed
    assert "Para usar modelos reales" not in capsys.readouterr().out


def test_launcher_flag_reaches_run_from_the_cli_and_the_module(monkeypatch):
    import multiagent.cli as cli
    import multiagent.gui.__main__ as gui_main

    seen = []
    monkeypatch.setattr(gui_main, "run", lambda **kwargs: seen.append(kwargs))
    cli.main(["gui", "--launcher", "--no-browser"])
    gui_main.main(["--launcher", "--no-browser"])
    cli.main(["gui", "--no-browser"])
    assert [row["launcher"] for row in seen] == [True, True, False]
    assert ALLOWED_VALUES["models"] == ("mock", "real")
