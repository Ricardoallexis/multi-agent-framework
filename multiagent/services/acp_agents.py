# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The person's own ACP agents (EXA-T06): add, list, edit and remove, kept in ``<local_dir>/acp-agents.json``.

An agent here is a program that speaks ACP: an absolute executable and a list of fixed, literal arguments. Adding one
does not turn it on: it starts disabled, and the person probes and activates it from the GUI like Codex ACP (the
fingerprint, the note and the one-use approval live in ``executor_activations``). Only the person (actor ``user:gui``)
adds, edits or removes; no ``/api/v1`` route, agent or CLI command reaches this service. An own agent serves
interactive ACP conversations of the GUI, never workflow steps, batches or scheduled resumptions.

The task text never reaches the command line. It travels in ``session/prompt`` over the standard input, so a field of
the declaration that could carry it (a placeholder, a shell character, a wildcard) is refused.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from ..executors import process
from .errors import INVALID_REQUEST, INVALID_STATE, NOT_FOUND, ServiceError

if TYPE_CHECKING:
    from . import ApplicationServices

AGENTS_FILE = "acp-agents.json"
AGENTS_SCHEMA = "maf.acp-agents/1"
USER_ACTOR = "user:gui"
ADDED_EVENT = "acp_agent.added"
EDITED_EVENT = "acp_agent.edited"
REMOVED_EVENT = "acp_agent.removed"
MAX_AGENTS = 16
MAX_ARGS = 16
MAX_ARG_CHARS = 256
MAX_NAME_CHARS = 60
_ID = re.compile(r"[a-z][a-z0-9-]{1,31}\Z")
# ids of the house and of the known applications: an own agent never takes one (underscores read as hyphens)
_RESERVED = frozenset({"acp", "codex", "claude", "copilot", "agy", "antigravity", "gemini", "openai", "ollama", "mock",
                       "hermes", "codex_acp", "codex_cli", "claude_cli", "copilot_cli", "antigravity_cli"})
_WILDCARDS = frozenset("*?[]")
_SHELL_PATH_CHARS = frozenset(';&|<>"`$%^!\n\r\0')
_SHELL_SCRIPTS = (".cmd", ".bat", ".ps1", ".vbs", ".sh")
# command interpreters (round 2): refused by base name, with and without ".exe", for the declared and the resolved path.
# Program interpreters (node, python, deno, bun) stay allowed: their script argument obeys the same folder rules.
_SHELLS = frozenset({"cmd", "powershell", "pwsh", "wscript", "cscript", "mshta", "bash", "sh", "zsh", "ksh", "fish",
                     "csh", "tcsh", "dash", "wsl", "busybox"})
_SCRIPT_EXTENSIONS = (".js", ".mjs", ".cjs", ".ts", ".py", ".pyw", ".rb", ".pl", ".php", ".ps1", ".bat", ".cmd", ".sh")
_PLACEHOLDER_CHARS = frozenset("{}%$`<>")
_ARG_SHELL_CHARS = frozenset(";&|\n\r\0")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _reject(reason: str, message: str, *, status: int = 422, code: str = INVALID_REQUEST, **details: Any) -> ServiceError:
    return ServiceError(code, message, status=status, details={"reason": reason, **details})


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32].strip("-")


def _normal(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


class AcpAgentServices:
    """``services.acp_agents``: the declarations of the person's own ACP agents."""

    def __init__(self, services: ApplicationServices):
        self._services = services
        self._lock = threading.RLock()

    # -------- the local file --------
    @property
    def _dir(self) -> Path:
        return Path(self._services.settings.local_dir)

    @property
    def _file(self) -> Path:
        return self._dir / AGENTS_FILE

    def _entries(self) -> dict[str, dict[str, Any]]:
        """Every well-shaped entry of the file, in file order, as the person's file has it (no validation).

        A missing or broken file reads as no entries, and a broken file is not overwritten until the person's next add.
        Writes start from this, so an entry the rules refuse today is kept as it is, never lost.
        """
        try:
            raw = json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        entries = raw.get("agents") if isinstance(raw, dict) else None
        if not isinstance(entries, dict):
            return {}
        return {identifier: dict(entry) for identifier, entry in entries.items()
                if isinstance(identifier, str) and isinstance(entry, dict)
                and isinstance(entry.get("name"), str) and isinstance(entry.get("executable"), str)
                and isinstance(entry.get("args"), list) and all(isinstance(item, str) for item in entry["args"])}

    def _read(self) -> dict[str, dict[str, Any]]:
        """The listed agents with ``id``, ``valid`` and ``reason``, checked now with the rules of the add.

        An entry whose id is malformed or reserved is not listed anywhere. One whose name, executable or arguments do not
        pass is listed with ``valid: false`` and the ``reason`` the add would have given; so is every entry after the
        first ``MAX_AGENTS`` (``too_many_agents``). An invalid agent cannot be probed, activated or opened.
        """
        reserved = self._house_ids()
        result: dict[str, dict[str, Any]] = {}
        counted = 0
        for identifier, entry in self._entries().items():
            if not _ID.match(identifier) or identifier.replace("_", "-") in reserved:
                continue
            counted += 1
            reason = "too_many_agents" if counted > MAX_AGENTS else self._invalid_reason(entry)
            result[identifier] = {**entry, "id": identifier, "valid": reason is None, "reason": reason}
        return result

    def _invalid_reason(self, record: Mapping[str, Any]) -> str | None:
        try:
            self._validate_record(record)
        except ServiceError as exc:
            return exc.details.get("reason")
        return None

    def _validate_record(self, record: Mapping[str, Any]) -> tuple[str, str, list[str]]:
        """The whole declaration, with the rules of the add: ``(name, executable, args)`` normalized, or the refusal."""
        name = self._valid_name(record.get("name"))
        executable = self.validate_executable(record.get("executable"))
        return name, executable, self.validate_args(record.get("args"))

    def _write(self, entries: Mapping[str, Mapping[str, Any]]) -> None:
        """Atomic: a temporary file in the same folder, then ``os.replace``. No ``.tmp`` is left behind."""
        folder = self._dir
        folder.mkdir(parents=True, exist_ok=True)
        content = {"schema": AGENTS_SCHEMA,
                   "agents": {key: {k: v for k, v in value.items() if k not in {"id", "valid", "reason"}}
                              for key, value in entries.items()}}
        handle, name = tempfile.mkstemp(prefix=AGENTS_FILE + ".", suffix=".tmp", dir=folder)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(content, stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.replace(name, self._file)
        except BaseException:
            try:
                os.unlink(name)
            except OSError:
                pass
            raise

    def _event(self, event_type: str, agent_id: str, *, actor: str) -> None:
        store = self._services.tasks.store
        with store.transaction() as conn:
            store.append_event(conn, event_type, actor=actor, ref=agent_id,
                               payload={"agent_id": agent_id, "channel": "gui"})

    # -------- rules --------
    @staticmethod
    def _person(actor: str) -> None:
        """Only the person, from the GUI, declares, edits or removes an agent."""
        if actor != USER_ACTOR:
            raise _reject("person_required", "Adding or changing an ACP agent needs a person: use the GUI",
                          status=409, code=INVALID_STATE)

    def _house_ids(self) -> set[str]:
        from .connections import IDS
        from .executor_activations import EXECUTOR_IDS
        reserved = {*_RESERVED, *IDS, *EXECUTOR_IDS}
        cached = self.__dict__.get("_house_cache")
        if cached is not None:
            return cached | {item.replace("_", "-") for item in reserved}
        if not self.__dict__.get("_house_busy"):     # the catalog may itself ask for the activations: do not recurse
            self._house_busy = True
            try:
                self._house_cache = {item["executor_id"].replace("_", "-")
                                     for item in self._services.executors.status()["executors"]}
            except Exception:  # noqa: BLE001 - the fixed list above already covers the house
                pass
            finally:
                self._house_busy = False
        return {item.replace("_", "-") for item in reserved} | self.__dict__.get("_house_cache", set())

    def _valid_id(self, identifier: Any) -> str:
        if not isinstance(identifier, str) or not _ID.match(identifier):
            raise _reject("invalid_agent_id", "The agent id is 2 to 32 characters: lowercase letters, digits and hyphens, "
                          "starting with a letter")
        if identifier.replace("_", "-") in self._house_ids():
            raise _reject("agent_id_reserved", "That id belongs to an application of the framework")
        return identifier

    @staticmethod
    def _valid_name(name: Any) -> str:
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > MAX_NAME_CHARS or any(
                ord(char) < 32 for char in name):
            raise _reject("name_invalid", f"The name is 1 to {MAX_NAME_CHARS} characters, on one line")
        return name.strip()

    def _forbidden_folders(self) -> list[Path]:
        """The folders an agent may write or that hold the framework: the executable never sits in one of them.

        Besides the working folder, the framework copy, ``local_dir`` and the areas, every writable folder the settings can
        configure: ``data_dir``, ``file_tools_root`` (where the ``file.*`` tools write), ``executor_workdir_root``,
        ``runs_dir`` and ``assets_dir``.
        """
        settings = self._services.settings
        folders = [Path.cwd(), Path(__file__).resolve().parents[2], self._dir]
        for name in ("data_dir", "file_tools_root", "executor_workdir_root", "runs_dir", "assets_dir"):
            value = getattr(settings, name, None)
            if value:
                folders.append(Path(value))
        folders.extend(self._services._bundles.values())
        return folders

    def validate_executable(self, value: Any) -> str:
        """The executable as it will be launched: absolute, a real file, no shell, no wildcard, outside the writable folders."""
        if not isinstance(value, str) or not value.strip() or len(value) > 1024:
            raise _reject("executable_not_absolute", "The executable must be an absolute path")
        path = value.strip()
        if any(char in _WILDCARDS for char in path):
            raise _reject("executable_has_wildcard", "The executable path cannot contain wildcards")
        if any(char in _SHELL_PATH_CHARS for char in path):
            raise _reject("executable_has_shell_characters", "The executable path cannot contain shell characters")
        if not Path(path).is_absolute() or (os.name == "nt" and not os.path.splitdrive(path)[0]
                                            and not path.startswith("\\\\")):
            raise _reject("executable_not_absolute", "The executable must be an absolute path")
        if path.lower().endswith(_SHELL_SCRIPTS):
            raise _reject("executable_is_shell_script", "A script that needs a shell cannot be launched: declare the program itself")
        resolved = os.path.realpath(path)
        for candidate in (path, resolved):
            base = os.path.basename(candidate.replace("\\", "/")).lower()
            if base in _SHELLS or (base.endswith(".exe") and base[:-4] in _SHELLS):
                raise _reject("executable_is_shell", "A command interpreter cannot be an agent: declare the agent program itself")
        if not os.path.isfile(resolved) or not os.access(resolved, os.X_OK):
            raise _reject("executable_not_found", "The executable does not exist, is not a file, or cannot be run")
        if resolved.lower().endswith(_SHELL_SCRIPTS):
            raise _reject("executable_is_shell_script", "A script that needs a shell cannot be launched: declare the program itself")
        for folder in self._forbidden_folders():
            if process.is_within(resolved, folder) or process.is_within(path, folder):
                raise _reject("executable_in_writable_folder",
                              "The executable cannot sit in the working folder, an area, the framework or its data")
        return os.path.abspath(path)

    def validate_args(self, value: Any) -> list[str]:
        """Literal arguments. Nothing in them is filled in later: the task text is never an argument.

        An argument that is a path (or ``--flag=path``) obeys the folder rules of the executable: a program interpreter such as
        ``node`` or ``python`` runs the script it is given, so that script cannot sit in a folder the agent may write. A
        relative path would resolve inside the area it works in, so it is refused too.
        """
        if value is None:
            return []
        if not isinstance(value, (list, tuple)):
            raise _reject("argument_invalid", "The arguments are a list of text values")
        if len(value) > MAX_ARGS:
            raise _reject("too_many_arguments", f"At most {MAX_ARGS} arguments")
        result: list[str] = []
        for index, item in enumerate(value):
            if not isinstance(item, str) or not item or len(item) > MAX_ARG_CHARS or item != item.strip():
                raise _reject("argument_invalid", f"Argument {index + 1} is empty, too long or has spaces around it",
                              argument=index + 1)
            if any(char in _PLACEHOLDER_CHARS for char in item):
                raise _reject("argument_has_placeholder",
                              f"Argument {index + 1} has a placeholder or substitution character: arguments are literal",
                              argument=index + 1)
            if any(char in _ARG_SHELL_CHARS for char in item):
                raise _reject("argument_has_shell_syntax", f"Argument {index + 1} has shell syntax", argument=index + 1)
            result.append(item)
        self._check_argument_paths(result)
        return result

    def _check_argument_paths(self, args: list[str]) -> None:
        folders = self._forbidden_folders()
        for index, item in enumerate(args, start=1):
            candidates = [item] if not item.startswith("-") else []
            if "=" in item:
                candidates.append(item.split("=", 1)[1])
            for candidate in candidates:
                if not candidate or "://" in candidate:
                    continue
                absolute = Path(candidate).is_absolute() and (os.name != "nt" or bool(os.path.splitdrive(candidate)[0])
                                                              or candidate.startswith("\\\\"))
                if absolute:
                    if any(process.is_within(candidate, folder) for folder in folders):
                        raise _reject("argument_in_writable_folder",
                                      f"Argument {index} is a path inside a folder the agent may write", argument=index)
                elif "/" in candidate or "\\" in candidate or candidate.lower().endswith(_SCRIPT_EXTENSIONS):
                    raise _reject("argument_relative_path",
                                  f"Argument {index} is a relative path: it would resolve inside the area. Use an absolute path",
                                  argument=index)

    # -------- reading --------
    def get(self, agent_id: str) -> dict[str, Any]:
        record = self._read().get(agent_id) if isinstance(agent_id, str) else None
        if record is None:
            raise ServiceError(NOT_FOUND, "ACP agent not found", status=404,
                               details={"reason": "acp_agent_not_found", "agent": agent_id if isinstance(agent_id, str) else None})
        return record

    def exists(self, agent_id: Any) -> bool:
        return isinstance(agent_id, str) and agent_id in self._read()

    def ids(self) -> list[str]:
        """Every listed agent, valid or not (an invalid one fails visibly when used); never a reserved or malformed id."""
        return sorted(self._read())

    def is_valid(self, agent_id: str) -> bool:
        record = self._read().get(agent_id)
        return bool(record and record["valid"])

    def require_valid(self, agent_id: str) -> dict[str, Any]:
        """The record, or the refusal with the ``reason`` the add would have given (probe, activation and opening use it)."""
        record = self.get(agent_id)
        if not record["valid"]:
            raise ServiceError(INVALID_STATE, "This ACP agent's declaration is not valid", status=409,
                               details={"reason": record["reason"], "agent": agent_id})
        return record

    def name_of(self, agent_id: str) -> str:
        record = self._read().get(agent_id)
        return record["name"] if record else agent_id

    def launch_target(self, agent_id: str) -> tuple[str, list[str]]:
        """``(executable, args)`` to launch now. The declaration is checked again, with the rules of the add.

        A file that is gone is ``acp_unavailable`` (as for Codex ACP); any other refusal keeps its own ``reason``.
        """
        record = self.get(agent_id)
        if not record["valid"]:
            reason = record["reason"]
            raise ServiceError(INVALID_STATE, "ACP unavailable", status=409,
                               details={"reason": "acp_unavailable" if reason == "executable_not_found" else reason,
                                        "agent": agent_id, "cause": reason})
        return os.path.abspath(record["executable"].strip()), list(record["args"])

    def list(self) -> dict[str, Any]:
        return {"agents": [self.row(record) for record in self._read().values()]}

    def row(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """What a card needs: the declaration, ``valid`` and ``reason``, ``state`` (``disabled``, ``active`` or ``paused``) and the probe."""
        agent_id = record["id"]
        activations = self._services.executor_activations
        entry = activations._read().get(agent_id)
        connections = self._services.connections
        path, fresh, probe = connections.acp_agent_probe_state(agent_id)
        paused = entry["paused"] if entry else None
        return {"id": agent_id, "name": record["name"], "executable": record["executable"], "args": list(record["args"]),
                "valid": bool(record.get("valid", True)), "reason": record.get("reason"),
                "state": ("paused" if paused else "active") if entry else "disabled",
                "installed": path is not None, "probed": bool(fresh and probe.get("status") == "compatible"),
                "probe": probe or None,
                "probe_required": bool(path is not None and not (fresh and probe.get("status") == "compatible")),
                "activated_at": entry.get("at") if entry else None, "activated_by": entry.get("by") if entry else None,
                "paused": paused, "changes": list(paused.get("changes", [])) if paused else [],
                "scope": "acp_conversations",
                "created_at": record.get("created_at"), "created_by": record.get("created_by"),
                "updated_at": record.get("updated_at"), "updated_by": record.get("updated_by")}

    # -------- add, edit, remove --------
    def add(self, name: Any, executable: Any, args: Any = None, *, agent_id: Any = None,
            actor: str = USER_ACTOR) -> dict[str, Any]:
        """Declare an agent. It starts disabled, with no probe: the person checks and activates it afterwards."""
        self._person(actor)
        clean_name = self._valid_name(name)
        identifier = self._valid_id(agent_id if agent_id is not None else _slug(clean_name))
        path = self.validate_executable(executable)
        arguments = self.validate_args(args)
        with self._lock:
            entries = self._entries()
            if identifier in entries:
                raise _reject("agent_exists", "An agent with that id already exists", status=409, code=INVALID_STATE)
            if len(self._read()) >= MAX_AGENTS:
                raise _reject("too_many_agents", f"At most {MAX_AGENTS} agents", status=409, code=INVALID_STATE)
            now = _now()
            entries[identifier] = {"name": clean_name, "executable": path, "args": arguments,
                                   "created_at": now, "created_by": actor, "updated_at": now, "updated_by": actor}
            self._write(entries)
        self._event(ADDED_EVENT, identifier, actor=actor)
        return self.row(self.get(identifier))

    def edit(self, agent_id: str, *, name: Any = None, executable: Any = None, args: Any = None,
             actor: str = USER_ACTOR) -> dict[str, Any]:
        """Change a declaration. A new executable or argument list starts over: the activation pauses, the probe goes.

        The whole entry is validated, not only the field that changes: an entry a hand edit left invalid cannot be saved as is.
        """
        self._person(actor)
        with self._lock:
            self.get(agent_id)            # the 404
            entries = self._entries()
            stored = dict(entries[agent_id])
            updated = dict(stored)
            if name is not None:
                updated["name"] = name
            if executable is not None:
                updated["executable"] = executable
            if args is not None:
                updated["args"] = args
            updated["name"], updated["executable"], updated["args"] = self._validate_record(updated)
            changes = []
            if _normal(updated["executable"]) != _normal(stored["executable"]):
                changes.append({"field": "path", "from": stored["executable"], "to": updated["executable"]})
            if updated["args"] != stored["args"]:
                changes.append({"field": "args", "from": list(stored["args"]), "to": list(updated["args"])})
            updated.update(updated_at=_now(), updated_by=actor)
            entries[agent_id] = updated
            self._write(entries)
        if changes:
            self._services.connections.clear_acp_agent_probe(agent_id)
            self._services.executor_activations.pause_agent(agent_id, changes)
            self.close_sessions(agent_id)
        self._event(EDITED_EVENT, agent_id, actor=actor)
        return self.row(self.get(agent_id))

    def remove(self, agent_id: str, *, actor: str = USER_ACTOR) -> dict[str, Any]:
        """Remove the declaration, close its live conversations and delete its activation. Saved history stays."""
        self._person(actor)
        with self._lock:
            self.get(agent_id)       # the 404
            entries = self._entries()
            entries.pop(agent_id)
            self._write(entries)
        self.close_sessions(agent_id)
        self._services.executor_activations.forget_agent(agent_id)
        self._services.connections.clear_acp_agent_probe(agent_id)
        self._event(REMOVED_EVENT, agent_id, actor=actor)
        return {"id": agent_id, "removed": True}

    def close_sessions(self, agent_id: str) -> None:
        """Close the live and the opening conversations of this agent only (``runs.acp.close_agent``)."""
        acp = getattr(getattr(self._services, "runs", None), "acp", None)
        close = getattr(acp, "close_agent", None)
        if callable(close):
            close(agent_id)
