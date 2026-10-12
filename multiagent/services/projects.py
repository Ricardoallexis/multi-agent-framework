# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Project services (PRY-T02): projects made of stages (task groups), phases and tasks, for any domain.

Contract fixed by PRY-T01 (approved); implemented on the existing ``projects`` table (migration 011), the
task groups and tasks of ORQ and the team event log. The core stays domain-neutral: marketing, software or
operations projects only differ in the plan (a template or a plan of the assistant) they are created from.

A project is a row of ``projects``; a stage is a ``task_groups`` row with a ``project_id`` and a ``position``;
a task keeps living in ``tasks`` and is reviewed with the existing ``tasks.transition`` (``accept`` or
``request_changes``) and an actor ``user:...``. Nothing here executes anything: a plan is data.

Plans and templates
    ``plan`` validates a document that follows the template schema ``maf.project-template/1`` (a ``dict`` or JSON
    text) without writing, and returns what would be created plus ``plan_sha256``, the hash of the normalized
    plan. ``create`` writes the project, its stages, tasks and dependencies in one transaction and only if the
    hash still matches what the user confirmed. The validation is strict (unknown keys are rejected) and has no
    code path that runs anything. Ids are ``<key>`` (the project; ``id = name = key``), ``<key>-<CODE>`` (a stage)
    and ``<key>-<CODE>-T<nn>`` (a task). The ``assignee`` of a template task becomes its ``preferred``;
    ``done_when`` becomes the constraint "Terminado cuando: ..."; ``stage:EST`` becomes a dependency on every
    task EST has in the plan.

Actors
    ``user:[name]`` (and the legacy ``user`` and ``human``), ``person:<name>``, ``agent:<app>[/<model>]`` and
    ``executor:<id>``. Changing a project (create, update, archive, attach, reorder, close a stage) needs a
    ``user`` actor: an ``agent`` gets ``invalid_state`` with ``details.reason = "confirmation_required"``.
    The level (T1..T4) of an agent is read from the catalog by its model, never stored in the actor.

Revisions
    Writes carry ``expected_revision``: the project's for ``update``, ``archive``, ``unarchive``, ``attach_stage``
    and ``reorder``; the stage's (group's) for ``close_stage``, which is what ``set_group_state`` checks.

Every method returns JSON-ready data and raises only ``ServiceError``: ``not_found`` (project, stage, task),
``invalid_request`` (malformed data or plan, a dependency cycle, a payload over 2048 bytes) or ``invalid_state``
(an existing id, a changed plan, a stale revision, a non-user actor, an archived project, or work in progress).
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from ..db.project_store import ProjectStore, StaleProjectRevision
from ..tasks import graph, labels
from ..tasks.envelopes import MAX_CONSTRAINT_CHARS, MAX_CONTEXT_REFS, EnvelopeError, result_ref, validate_reference
from .errors import NOT_FOUND, ServiceError, service_errors
from .tasks import (_GROUP_ID, _TASK_ID, _canonical_json, _conflict, _identifier, _invalid, _missing,
                    _requires, _text)

if TYPE_CHECKING:
    from . import ApplicationServices

PLAN_SCHEMA = "maf.project-template/1"
MAX_DOC_BYTES = 256 * 1024
MAX_STAGES = 30
MAX_PHASES = 12
MAX_TASKS = 200
MAX_DEPENDS_ON = 50
VISIBILITIES = ("normal", "private")
DEFAULT_VISIBILITY = "private"   # decision 5 of PRY-T01: a new project is private unless asked otherwise
DONE_PREFIX = "Terminado cuando: "
MAX_DONE_WHEN = MAX_CONSTRAINT_CHARS - len(DONE_PREFIX)   # the whole restriction must fit the envelope's 200 characters
_BUSY_STATES = ("claimed", "running", "paused", "validating")   # work in progress: the project cannot be archived

_KEY = re.compile(r"^[a-z][a-z0-9]{1,15}$")
_CODE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")
_LOCAL_TASK = re.compile(r"^T\d{2,3}$")
_TEMPLATE_ID = re.compile(r"^[a-z0-9-]{2,40}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DEP_STAGE = re.compile(r"^stage:([A-Z][A-Z0-9]{1,9})$")
_DEP_OTHER = re.compile(r"^([A-Z][A-Z0-9]{1,9})-(T\d{2,3})$")

# Actor forms of PRY-T01 section 4 (the first word is the kind).
_ACTOR_FORMS: dict[str, re.Pattern[str]] = {
    "user": re.compile(r"^(?:user|human|user:[A-Za-z0-9 ._@+-]{0,80})$"),
    "person": re.compile(r"^person:[A-Za-z0-9 ._@+-]{1,80}$"),
    "agent": re.compile(r"^agent:(?P<app>[a-z0-9][a-z0-9_.-]{0,39})(?:/(?P<model>[A-Za-z0-9][A-Za-z0-9_.:+-]{0,59}))?$"),
    "executor": re.compile(r"^executor:[A-Za-z0-9][A-Za-z0-9_.-]{0,59}$"),
}


def actor_kind(actor: Any) -> str | None:
    """``user``, ``person``, ``agent`` or ``executor`` for an actor of a known form, else ``None`` (free text)."""
    if not isinstance(actor, str) or len(actor) > 100:
        return None
    for kind, pattern in _ACTOR_FORMS.items():
        if pattern.fullmatch(actor):
            return kind
    return None


def actor_view(actor: str, level_of: Any = None) -> dict[str, Any] | None:
    """How a person or agent is shown: ``{raw, kind, name | app, model, level}``; ``None`` for no actor.

    Free text without a known prefix is shown as it is (``kind = "other"``). The level only exists for agents and
    comes from the catalog by the model (``level_of``); it is never part of the actor.
    """
    if not actor:
        return None
    kind = actor_kind(actor)
    if kind is None:
        return {"raw": actor, "kind": "other", "name": actor}
    view: dict[str, Any] = {"raw": actor, "kind": kind}
    if kind == "agent":
        match = _ACTOR_FORMS["agent"].fullmatch(actor)
        model = match["model"] if match else None
        view.update(app=match["app"] if match else "", model=model,
                    level=level_of(model) if level_of is not None and model else None)
    elif kind == "executor":
        view["name"] = actor.split(":", 1)[1]
    else:
        view["name"] = actor.split(":", 1)[1] if ":" in actor else ""
    return view


def _actor(value: Any, field: str = "actor") -> str:
    actor = _text(field, value, maximum=100)
    if actor_kind(actor) is None:
        raise _invalid(field, f"{field} must be user:[name], person:<name>, agent:<app>[/<model>] or executor:<id>")
    return actor


def _user_actor(value: Any) -> str:
    """A valid ``user`` actor, or ``invalid_state`` (``confirmation_required``): only the user changes a project."""
    actor = _actor(value)
    if actor_kind(actor) != "user":
        raise _conflict("Only the user can change a project; confirm it as user:...", actor=actor,
                        reason="confirmation_required")
    return actor


def _revision(value: Any, field: str = "expected_revision") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _invalid(field, f"{field} must be a positive integer")
    return value


def _date(field: str, value: Any) -> str:
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise _invalid(field, f"{field} must be a date AAAA-MM-DD")
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        raise _invalid(field, f"{field} is not a real date: {value!r}") from None
    return value


def _object(path: str, value: Any, *, required: Iterable[str], optional: Iterable[str] = ()) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _invalid(path, f"{path} must be an object")
    required, optional = set(required), set(optional)
    unknown = sorted(set(map(str, value)) - required - optional)
    if unknown:
        raise _invalid(path, f"Unknown fields in {path}: {', '.join(unknown)}", fields=unknown)
    missing = sorted(required - set(value))
    if missing:
        raise _invalid(path, f"Missing fields in {path}: {', '.join(missing)}", fields=missing)
    return value


def _list(path: str, value: Any, *, maximum: int) -> list[Any]:
    if not isinstance(value, list):
        raise _invalid(path, f"{path} must be a list")
    if len(value) > maximum:
        raise _invalid(path, f"{path} has more than {maximum} items")
    return value


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise _invalid(field, f"{field} must be a lowercase SHA-256 hex digest")
    return value


class ProjectServices:
    def __init__(self, services: ApplicationServices):
        self._services = services
        self._store: ProjectStore | None = None

    @property
    def store(self) -> ProjectStore:
        # Created on first use: the facade can be built around a system without a database.
        if self._store is None:
            self._store = ProjectStore(self._services.system.db)
        return self._store

    @property
    def _tasks(self):
        return self._services.tasks

    # ------------------------------------------------------------------ labels
    @service_errors
    def labels(self) -> dict[str, Any]:
        """The shared map of labels (``labels.json``): ``{schema, task, stage, details}``."""
        return labels.load()

    # ------------------------------------------------------------------ plans
    def _document(self, doc: Any) -> Mapping[str, Any]:
        """The plan as a mapping: a ``dict`` or JSON text of at most 256 KB."""
        if isinstance(doc, (bytes, str)):
            raw = doc if isinstance(doc, bytes) else doc.encode("utf-8")
            if len(raw) > MAX_DOC_BYTES:
                raise _invalid("doc", f"The document is larger than {MAX_DOC_BYTES} bytes")
            try:
                doc = json.loads(raw.decode("utf-8"))
            except (UnicodeError, ValueError, RecursionError):
                raise _invalid("doc", "The document is not valid JSON") from None
        if not isinstance(doc, Mapping):
            raise _invalid("doc", "The document must be an object")
        try:
            size = len(_canonical_json(doc).encode("utf-8"))
        except (TypeError, ValueError, RecursionError):
            raise _invalid("doc", "The document must be strict JSON data") from None
        if size > MAX_DOC_BYTES:
            raise _invalid("doc", f"The document is larger than {MAX_DOC_BYTES} bytes")
        return doc

    def _normalize(self, doc: Any, *, key: Any, title: Any = None, visibility: Any = None
                   ) -> tuple[dict[str, Any], list[str]]:
        """Validate a plan against the template schema and give the normalized write set and warnings.

        Nothing here touches the database. Errors are ``invalid_request`` with ``details.field`` naming where.
        """
        doc = self._document(doc)
        _object("doc", doc, required=("schema", "id", "title", "stages"), optional=("description", "visibility"))
        if doc["schema"] != PLAN_SCHEMA:
            raise _invalid("schema", f"schema must be {PLAN_SCHEMA!r}")
        template_id = doc["id"]
        if not isinstance(template_id, str) or not _TEMPLATE_ID.fullmatch(template_id):
            raise _invalid("id", "id must be 2 to 40 lowercase letters, digits or hyphens")
        doc_title = _text("title", doc["title"], maximum=200)
        _text("description", doc.get("description", ""), maximum=2000, required=False)
        doc_visibility = doc.get("visibility")
        if doc_visibility is not None and doc_visibility not in VISIBILITIES:
            raise _invalid("visibility", f"visibility must be one of {', '.join(VISIBILITIES)}")
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise _invalid("key", "key must be 2 to 16 lowercase letters or digits, starting with a letter")
        project_title = _text("title", title, maximum=200) if title is not None else doc_title
        if visibility is not None and visibility not in VISIBILITIES:
            raise _invalid("visibility", f"visibility must be one of {', '.join(VISIBILITIES)}")
        chosen = visibility or doc_visibility or DEFAULT_VISIBILITY

        raw_stages = _list("stages", doc["stages"], maximum=MAX_STAGES)
        codes: list[str] = []
        stages: list[dict[str, Any]] = []
        total_tasks = 0
        for index, raw in enumerate(raw_stages):
            path = f"stages[{index}]"
            raw = _object(path, raw, required=("code", "title"), optional=("phases", "planned", "tasks"))
            code = raw["code"]
            if not isinstance(code, str) or not _CODE.fullmatch(code):
                raise _invalid(f"{path}.code", "code must be 2 to 10 uppercase letters or digits, starting with a letter")
            if code in codes:
                raise _invalid(f"{path}.code", f"Repeated stage code: {code}")
            codes.append(code)
            phases: list[str] = []
            for pindex, phase in enumerate(_list(f"{path}.phases", raw.get("phases", []), maximum=MAX_PHASES)):
                name = _text(f"{path}.phases[{pindex}]", phase, maximum=60)
                if name in phases:
                    raise _invalid(f"{path}.phases", f"Repeated phase: {name}")
                phases.append(name)
            planned_start = planned_end = None
            if "planned" in raw:
                planned = _object(f"{path}.planned", raw["planned"], required=(), optional=("start", "end"))
                planned_start = _date(f"{path}.planned.start", planned["start"]) if "start" in planned else None
                planned_end = _date(f"{path}.planned.end", planned["end"]) if "end" in planned else None
                if planned_start and planned_end and planned_start > planned_end:
                    raise _invalid(f"{path}.planned", "planned.start must not be after planned.end")
            tasks: list[dict[str, Any]] = []
            local_ids: list[str] = []
            for tindex, task_raw in enumerate(_list(f"{path}.tasks", raw.get("tasks", []), maximum=MAX_TASKS)):
                tpath = f"{path}.tasks[{tindex}]"
                task_raw = _object(tpath, task_raw, required=("id", "title"), optional=(
                    "objective", "done_when", "phase", "assignee", "depends_on", "requires", "due", "context_refs"))
                local = task_raw["id"]
                if not isinstance(local, str) or not _LOCAL_TASK.fullmatch(local):
                    raise _invalid(f"{tpath}.id", "id must look like T01 (T and 2 or 3 digits)")
                if local in local_ids:
                    raise _invalid(f"{tpath}.id", f"Repeated task id in stage {code}: {local}")
                local_ids.append(local)
                total_tasks += 1
                if total_tasks > MAX_TASKS:
                    raise _invalid("stages", f"The plan has more than {MAX_TASKS} tasks")
                phase = task_raw.get("phase", "")
                if phase != "":
                    phase = _text(f"{tpath}.phase", phase, maximum=60)
                    if phase not in phases:
                        raise _invalid(f"{tpath}.phase", f"Phase {phase!r} is not declared in stage {code}")
                done_when = _text(f"{tpath}.done_when", task_raw.get("done_when", ""), maximum=MAX_DONE_WHEN,
                                  required=False)
                refs: list[str] = []
                raw_refs = _list(f"{tpath}.context_refs", task_raw.get("context_refs", []), maximum=MAX_CONTEXT_REFS)
                for ref in raw_refs:
                    try:
                        ref = validate_reference(ref, field="context_refs")
                    except EnvelopeError as error:
                        raise _invalid(f"{tpath}.context_refs", str(error)) from None
                    if ref not in refs:
                        refs.append(ref)
                assignee = task_raw.get("assignee", "")
                if assignee != "":
                    assignee = _actor(assignee, f"{tpath}.assignee")
                requires = task_raw.get("requires")
                if requires is not None:
                    requires = _requires(requires, f"{tpath}.requires")
                depends = [_text(f"{tpath}.depends_on", d, maximum=40)
                           for d in _list(f"{tpath}.depends_on", task_raw.get("depends_on", []), maximum=MAX_DEPENDS_ON)]
                if len(set(depends)) != len(depends):
                    raise _invalid(f"{tpath}.depends_on", "depends_on has a repeated entry")
                tasks.append({
                    "local": local, "task_id": f"{key}-{code}-{local}",
                    "title": _text(f"{tpath}.title", task_raw["title"], maximum=200),
                    "objective": _text(f"{tpath}.objective", task_raw.get("objective", ""), maximum=4000, required=False),
                    "constraints": [DONE_PREFIX + done_when] if done_when else [],
                    "phase": phase, "preferred": assignee,
                    "due_at": _date(f"{tpath}.due", task_raw["due"]) if task_raw.get("due") is not None else None,
                    "context_refs": refs, "requires": requires, "depends_on": depends,
                })
            stages.append({"code": code, "group_id": f"{key}-{code}", "title": _text(f"{path}.title", raw["title"], maximum=200),
                           "position": index + 1, "phases": phases, "planned_start": planned_start,
                           "planned_end": planned_end, "tasks": tasks})

        edges, warnings = self._resolve_dependencies(key, stages)
        task_ids = [task["task_id"] for stage in stages for task in stage["tasks"]]
        try:
            graph.validate(task_ids, edges)
        except graph.GraphError as error:
            raise _invalid("depends_on", str(error), cycle=getattr(error, "cycle", None)) from None
        for stage in stages:
            for task in stage["tasks"]:
                del task["depends_on"], task["requires"]
        plan = {"project": {"id": key, "title": project_title, "visibility": chosen, "template": template_id},
                "stages": stages, "dependencies": edges}
        return plan, warnings

    @staticmethod
    def _resolve_dependencies(key: str, stages: list[dict[str, Any]]) -> tuple[list[dict[str, str]], list[str]]:
        """Turn ``T02``, ``EST-T01`` and ``stage:EST`` into edges between full task ids."""
        by_code = {stage["code"]: stage for stage in stages}
        edges: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        warnings: list[str] = []
        for stage in stages:
            default_requires = "accepted"
            for task in stage["tasks"]:
                requires = task["requires"] or default_requires
                targets: list[str] = []
                for token in task["depends_on"]:
                    where = f"stages[{stage['position'] - 1}].tasks[{stage['tasks'].index(task)}].depends_on"
                    if _LOCAL_TASK.fullmatch(token):
                        targets.append(f"{key}-{stage['code']}-{token}")
                    elif (match := _DEP_OTHER.fullmatch(token)):
                        targets.append(f"{key}-{match[1]}-{match[2]}")
                    elif (match := _DEP_STAGE.fullmatch(token)):
                        other = by_code.get(match[1])
                        if other is None:
                            raise _invalid(where, f"Stage {match[1]} does not exist in the plan")
                        if other is stage:
                            raise _invalid(where, "A task cannot depend on its own stage")
                        if not other["tasks"]:
                            warnings.append(f"{task['task_id']}: stage:{match[1]} has no tasks, so it adds no dependency")
                        targets.extend(t["task_id"] for t in other["tasks"])
                    else:
                        raise _invalid(where, f"Invalid dependency {token!r}: use T02, EST-T01 or stage:EST")
                for target in targets:
                    exists = any(t["task_id"] == target for s in stages for t in s["tasks"])
                    if not exists:
                        raise _invalid("depends_on", f"{task['task_id']} depends on {target}, which is not in the plan")
                    if (task["task_id"], target) not in seen:
                        seen.add((task["task_id"], target))
                        edges.append({"task_id": task["task_id"], "depends_on_id": target, "requires": requires})
        return edges, warnings

    @staticmethod
    def _plan_digest(plan: Mapping[str, Any]) -> str:
        return hashlib.sha256(_canonical_json(plan).encode("utf-8")).hexdigest()

    def _collisions(self, conn: sqlite3.Connection, plan: Mapping[str, Any]) -> None:
        """``invalid_state`` when the project, a stage or a task of the plan already exists."""
        key = plan["project"]["id"]
        if self.store.name_taken(key, conn=conn):
            raise _conflict(f"Project already exists: {key}", project_id=key)
        group_ids = [stage["group_id"] for stage in plan["stages"]]
        task_ids = [task["task_id"] for stage in plan["stages"] for task in stage["tasks"]]
        for ids, table, label in ((group_ids, "task_groups", "Stage"), (task_ids, "tasks", "Task")):
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                row = conn.execute(f"SELECT id FROM {table} WHERE id IN ({','.join('?' * len(chunk))}) LIMIT 1",
                                   chunk).fetchone()
                if row:
                    raise _conflict(f"{label} already exists: {row['id']}", id=row["id"], project_id=key)

    @staticmethod
    def _plan_summary(plan: Mapping[str, Any], digest: str, warnings: list[str]) -> dict[str, Any]:
        edges = plan["dependencies"]
        depends: dict[str, list[str]] = {}
        for edge in edges:
            depends.setdefault(edge["task_id"], []).append(edge["depends_on_id"])
        stages, tasks = [], []
        for stage in plan["stages"]:
            stages.append({"group_id": stage["group_id"], "code": stage["code"], "title": stage["title"],
                           "position": stage["position"], "phases": stage["phases"],
                           "planned_start": stage["planned_start"], "planned_end": stage["planned_end"],
                           "task_count": len(stage["tasks"])})
            for task in stage["tasks"]:
                tasks.append({"id": task["task_id"], "group_id": stage["group_id"], "title": task["title"],
                              "phase": task["phase"], "preferred": task["preferred"], "due_at": task["due_at"],
                              "depends_on": depends.get(task["task_id"], [])})
        return {"status": "planned", "plan_sha256": digest, "project": dict(plan["project"]), "stages": stages,
                "tasks": tasks, "dependencies": [dict(edge) for edge in edges],
                "counts": {"stages": len(stages), "tasks": len(tasks), "dependencies": len(edges)},
                "warnings": warnings}

    @service_errors
    def plan(self, doc: Any, *, key: str, title: str | None = None, visibility: str | None = None) -> dict[str, Any]:
        """Validate a plan or template document without writing and say what ``create`` would make.

        ``doc`` follows the template schema ``maf.project-template/1`` (a ``dict`` or JSON text of at most 256 KB).
        ``key`` is the project id and the prefix of its stages and tasks; ``title`` and ``visibility`` default to the
        document's, and ``visibility`` to ``private`` if neither says. The answer holds the stages, the tasks, the
        dependencies and ``plan_sha256``, which ``create`` demands. Errors: ``invalid_request`` (the document, a
        cycle, a reference without target, a phase not declared), ``invalid_state`` (an id already exists).
        """
        plan, warnings = self._normalize(doc, key=key, title=title, visibility=visibility)
        with self.store.db.connect() as conn:
            self._collisions(conn, plan)
        return self._plan_summary(plan, self._plan_digest(plan), warnings)

    # ------------------------------------------------------------------ create
    @service_errors
    def create(self, doc: Any, *, key: str, title: str | None = None, visibility: str | None = None,
               plan_sha256: str, actor: str) -> dict[str, Any]:
        """Create a project from a plan in one transaction; events ``project.created``, ``group.created``,
        ``task.created`` and ``dependency.added`` (``cause = project:<id>``).

        Needs a ``user`` actor and the ``plan_sha256`` that ``plan`` returned for this very document, key, title and
        visibility: another hash means the plan changed since the user confirmed it (``invalid_state``). The stages
        are created not authorized, so every task starts as pending until the user authorizes its stage. Returns the
        project view (see ``get``). Errors: those of ``plan`` and ``invalid_state`` (not a user, changed plan).
        """
        actor = _user_actor(actor)
        digest_in = _sha(plan_sha256, "plan_sha256")
        plan, warnings = self._normalize(doc, key=key, title=title, visibility=visibility)
        digest = self._plan_digest(plan)
        if digest != digest_in:
            raise _conflict("The plan is not the one that was confirmed", reason="plan_changed",
                            expected_plan_sha256=digest_in, plan_sha256=digest)
        project = plan["project"]
        cause = f"project:{project['id']}"
        try:
            with self._tasks.store.transaction() as conn:
                self._collisions(conn, plan)
                store = self._tasks.store
                self.store.insert_project(conn, project["id"], project["title"], visibility=project["visibility"],
                                          created_by=actor)
                for stage in plan["stages"]:
                    store.insert_group(conn, stage["group_id"], stage["title"], actor=actor, authorized=False,
                                       project_id=project["id"], position=stage["position"], phases=stage["phases"],
                                       planned_start=stage["planned_start"], planned_end=stage["planned_end"])
                for stage in plan["stages"]:
                    for task in stage["tasks"]:
                        store.insert_task(conn, task["task_id"], stage["group_id"], task["title"], actor=actor,
                                          objective=task["objective"], preferred=task["preferred"],
                                          context_refs=task["context_refs"], constraints=task["constraints"],
                                          phase=task["phase"], due_at=task["due_at"])
                for edge in plan["dependencies"]:
                    store.insert_dependency(conn, edge["task_id"], edge["depends_on_id"], edge["requires"])
                self._events_of_creation(conn, plan, actor=actor, cause=cause, digest=digest)
                return self._project_view_in(conn, project["id"])
        except sqlite3.IntegrityError:
            raise _conflict(f"Project already exists: {project['id']}", project_id=project["id"]) from None

    def _events_of_creation(self, conn: sqlite3.Connection, plan: Mapping[str, Any], *, actor: str, cause: str,
                            digest: str) -> None:
        store = self._tasks.store
        project = plan["project"]
        tasks, deps = self._tasks._snapshot(conn)
        statuses = self._tasks._status_map(tasks, deps)
        task_count = sum(len(stage["tasks"]) for stage in plan["stages"])
        store.append_event(conn, "project.created", actor=actor, cause=cause, ref=f"project:{project['id']}",
                           payload={"template": project["template"], "visibility": project["visibility"],
                                    "stages": len(plan["stages"]), "tasks": task_count,
                                    "dependencies": len(plan["dependencies"]), "plan_sha256": digest})
        edges_of: dict[str, list[dict[str, str]]] = {}
        for edge in plan["dependencies"]:
            edges_of.setdefault(edge["task_id"], []).append(edge)
        for stage in plan["stages"]:
            store.append_event(conn, "group.created", actor=actor, group_id=stage["group_id"], cause=cause,
                               payload={"authorized": False, "default_requires": "accepted"})
            for task in stage["tasks"]:
                store.append_event(conn, "task.created", actor=actor, group_id=stage["group_id"],
                                   task_id=task["task_id"], cause=cause,
                                   payload={"status": statuses[task["task_id"]]})
                for edge in edges_of.get(task["task_id"], []):
                    store.append_event(conn, "dependency.added", actor=actor, group_id=stage["group_id"],
                                       task_id=task["task_id"], cause=cause,
                                       payload={"depends_on_id": edge["depends_on_id"], "requires": edge["requires"]})

    # ------------------------------------------------------------------ views
    def _context(self, conn: sqlite3.Connection, group_ids: Iterable[str]) -> dict[str, Any]:
        """Everything the views need from the task tables, read once.

        Tasks, edges and release facts come whole (a dependency may point outside the project); the effective
        status is worked out only for the tasks of ``group_ids``, the stages the view is about.
        """
        wanted = set(group_ids)
        tasks, deps = self._tasks._snapshot(conn)
        release = self._tasks._release_context(conn)
        states = self._tasks._states(tasks)
        statuses = {task_id: self._tasks._effective_status(task_id, states, deps, tasks, release)
                    for task_id, task in tasks.items() if task["group_id"] in wanted}
        deps_of: dict[str, list[str]] = {}
        for dep in deps:
            deps_of.setdefault(dep["task_id"], []).append(dep["depends_on_id"])
        return {"tasks": tasks, "deps": deps, "deps_of": deps_of, "release": release, "states": states,
                "statuses": statuses, "level_of": self._services.sessions._level_of()}

    def _task_ref(self, task: Mapping[str, Any], group: Mapping[str, Any], ctx: Mapping[str, Any]) -> dict[str, Any]:
        status = ctx["statuses"][task["id"]]
        key = self._tasks._label_key(task, status, group, ctx["release"])
        who = task["assignee"] or task["preferred"]
        return {"id": task["id"], "group_id": task["group_id"], "kind": task.get("kind", "task"),
                "title": task["title"], "state": task["state"],
                "status": status, "label": labels.label("task", key, task["state"]), "phase": task["phase"],
                "due_at": task["due_at"], "assignee": task["assignee"], "preferred": task["preferred"],
                "assignee_info": actor_view(who, ctx["level_of"]), "attempt": task["attempt"],
                "revision": task["revision"], "depends_on": list(ctx["deps_of"].get(task["id"], []))}

    def _stage_view(self, conn: sqlite3.Connection, project_id: str, group: Mapping[str, Any],
                    ctx: Mapping[str, Any]) -> dict[str, Any]:
        refs = [self._task_ref(task, group, ctx) for task in ctx["tasks"].values() if task["group_id"] == group["id"]]
        keys = [ref["label"]["key"] for ref in refs]
        prefix = f"{project_id}-"
        closed_at = self.store.closed_at(group["id"], conn=conn) if group["lifecycle_state"] == "closed" else None
        return {"group_id": group["id"], "kind": group.get("kind", "stage"),
                "code": group["id"][len(prefix):] if group["id"].startswith(prefix) else group["id"],
                "title": group["title"], "position": group["position"],
                "label": labels.stage_label(group["lifecycle_state"], bool(group["authorized"]), keys),
                "authorized": bool(group["authorized"]), "lifecycle_state": group["lifecycle_state"],
                "revision": group["revision"], "phases": list(group.get("phases", [])),
                "planned_start": group.get("planned_start"), "planned_end": group.get("planned_end"),
                "closed_at": closed_at, "counts": labels.counts(keys), "progress": labels.progress(keys),
                "tasks": refs}

    def _view(self, conn: sqlite3.Connection, project: Mapping[str, Any], ctx: Mapping[str, Any]) -> dict[str, Any]:
        groups = self._tasks.store.list_groups(project_id=project["id"], conn=conn)
        stages = [self._stage_view(conn, project["id"], group, ctx) for group in groups]
        refs = [ref for stage in stages for ref in stage["tasks"]]
        keys = [ref["label"]["key"] for ref in refs]

        def by_key(*wanted: str) -> list[dict[str, Any]]:
            return [ref for ref in refs if ref["label"]["key"] in wanted]

        blocked = []
        for ref in by_key("blocked"):
            task = ctx["tasks"][ref["id"]]
            found = self._tasks._blockers(task["id"], ctx["states"], ctx["deps"], ctx["tasks"], ctx["release"])
            blocked.append({**ref, "blockers": [
                {"task_id": item["task_id"], "state": item.get("state"), "requires": item.get("requires")}
                for item in found]})
        mine = [ref for ref in by_key("ready", "doing", "changes")
                if (ref["assignee_info"] or {}).get("kind") == "user"]
        return {
            "id": project["id"], "name": project["name"], "title": project["title"] or project["name"],
            "visibility": project["visibility"], "archived": project["archived"], "archived_at": project["archived_at"],
            "created_by": project["created_by"], "created_at": project["created_at"], "revision": project["revision"],
            "progress": labels.progress(keys), "counts": labels.counts(keys), "stages": stages,
            "attention": {
                "reviews": by_key("review"),
                "verifications": [{k: v for k, v in stage.items() if k != "tasks"}
                                  for stage in stages if stage["label"]["key"] == "verify"],
                "mine": mine, "blocked": blocked,
            },
            "working_now": by_key("doing"),
            "milestones": [{"group_id": s["group_id"], "code": s["code"], "title": s["title"],
                            "position": s["position"], "planned": s["planned_end"], "actual": s["closed_at"]}
                           for s in stages],
        }

    def _project_view_in(self, conn: sqlite3.Connection, project_id: str) -> dict[str, Any]:
        project = self.store.get_project(project_id, conn=conn)
        if project is None:
            raise _missing("Project", project_id)
        stages = self._tasks.store.list_groups(project_id=project_id, conn=conn)
        return self._view(conn, project, self._context(conn, (group["id"] for group in stages)))

    @service_errors
    def get(self, project_id: str) -> dict[str, Any]:
        """The aggregated view of a project.

        ``progress`` (``done`` / (``total`` - ``cancelled``)), ``counts`` per task label, the ``stages`` in order
        (label, phases, planned dates, real ``closed_at`` and their tasks), ``attention`` («what you have to do»:
        ``reviews``, ``verifications``, ``mine`` and ``blocked`` with their blockers), ``working_now`` and
        ``milestones`` (planned end and real closing of every stage). A value without a source is ``None``; nothing is
        invented. Errors: ``not_found``.
        """
        with self.store.db.connect() as conn:
            return self._project_view_in(conn, _text("project_id", project_id, maximum=128))

    @service_errors
    def list(self, *, archived: bool = False) -> list[dict[str, Any]]:
        """Projects in creation order: the active ones, or with ``archived=True`` only the archived ones.

        Each item has the identity of the project, its ``progress``, ``counts``, the number of ``stages`` and
        ``needs_you`` (how many reviews, verifications, own tasks and blocked tasks wait). Runs create plain
        projects too (by name): they appear here with no stages.
        """
        with self.store.db.connect() as conn:
            ctx = self._context(conn, (g["id"] for g in self._tasks.store.list_groups(conn=conn) if g.get("project_id")))
            items = []
            for project in self.store.list_projects(archived=bool(archived), conn=conn):
                view = self._view(conn, project, ctx)
                items.append({**{k: view[k] for k in ("id", "name", "title", "visibility", "archived", "archived_at",
                                                      "created_by", "created_at", "revision", "progress", "counts")},
                              "stages": len(view["stages"]),
                              "needs_you": {"reviews": len(view["attention"]["reviews"]),
                                            "verifications": len(view["attention"]["verifications"]),
                                            "mine": len(view["attention"]["mine"]),
                                            "blocked": len(view["attention"]["blocked"])}})
            return items

    # ------------------------------------------------------------------ project writes
    def _project_or_missing(self, conn: sqlite3.Connection, project_id: Any) -> dict[str, Any]:
        project_id = _text("project_id", project_id, maximum=128)
        project = self.store.get_project(project_id, conn=conn)
        if project is None:
            raise _missing("Project", project_id)
        return project

    @staticmethod
    def _active(project: Mapping[str, Any]) -> None:
        if project["archived"]:
            raise _conflict(f"Project {project['id']} is archived", project_id=project["id"])

    def _stale(self, project: Mapping[str, Any], expected: int) -> None:
        if project["revision"] != expected:
            raise _conflict("Stale project revision", project_id=project["id"], expected_revision=expected,
                            revision=project["revision"])

    def _event(self, conn: sqlite3.Connection, event: str, project_id: str, *, actor: str, cause: str = "",
               group_id: str = "", payload: dict[str, Any] | None = None) -> None:
        self._tasks.store.append_event(conn, event, actor=actor, group_id=group_id, cause=cause,
                                       ref=f"project:{project_id}", payload=payload)

    @service_errors
    def update(self, project_id: str, *, actor: str, expected_revision: int, title: str | None = None,
               visibility: str | None = None) -> dict[str, Any]:
        """Change the title or the visibility; event ``project.updated`` when something changed.

        Needs a ``user`` actor and the project's ``expected_revision``. Errors: ``not_found``,
        ``invalid_request`` (title, visibility or nothing to change), ``invalid_state`` (not a user, stale revision).
        """
        actor = _user_actor(actor)
        expected_revision = _revision(expected_revision)
        changes: dict[str, Any] = {}
        if title is not None:
            changes["title"] = _text("title", title, maximum=200)
        if visibility is not None:
            if visibility not in VISIBILITIES:
                raise _invalid("visibility", f"visibility must be one of {', '.join(VISIBILITIES)}")
            changes["visibility"] = visibility
        if not changes:
            raise _invalid("project", "Give a title or a visibility to change")
        with self._tasks.store.transaction() as conn:
            project = self._project_or_missing(conn, project_id)
            self._stale(project, expected_revision)
            changes = {k: v for k, v in changes.items() if project[k] != v}
            if changes:
                try:
                    self.store.update_project(conn, project["id"], expected_revision=expected_revision, **changes)
                except StaleProjectRevision as stale:
                    raise _conflict(str(stale), project_id=project["id"], revision=stale.current) from None
                self._event(conn, "project.updated", project["id"], actor=actor,
                            payload={"fields": sorted(changes), "visibility": changes.get("visibility", project["visibility"])})
            return self._project_view_in(conn, project["id"])

    def _set_archived(self, project_id: str, archived: bool, actor: Any, expected_revision: Any) -> dict[str, Any]:
        actor = _user_actor(actor)
        expected_revision = _revision(expected_revision)
        with self._tasks.store.transaction() as conn:
            project = self._project_or_missing(conn, project_id)
            self._stale(project, expected_revision)
            if project["archived"] == archived:
                return self._project_view_in(conn, project["id"])
            if archived:
                busy = [row["id"] for row in conn.execute(
                    "SELECT t.id FROM tasks t JOIN task_groups g ON g.id=t.group_id WHERE g.project_id=? "
                    f"AND t.state IN ({','.join('?' * len(_BUSY_STATES))}) ORDER BY t.rowid LIMIT 20",
                    (project["id"], *_BUSY_STATES))]
                if busy:
                    raise _conflict("A project with work in progress cannot be archived", project_id=project["id"],
                                    task_ids=busy, reason="work_in_progress")
            try:
                self.store.update_project(conn, project["id"], expected_revision=expected_revision, archived=archived)
            except StaleProjectRevision as stale:
                raise _conflict(str(stale), project_id=project["id"], revision=stale.current) from None
            self._event(conn, "project.archived" if archived else "project.unarchived", project["id"], actor=actor)
            return self._project_view_in(conn, project["id"])

    @service_errors
    def archive(self, project_id: str, *, actor: str, expected_revision: int) -> dict[str, Any]:
        """Archive the project (nothing is deleted); event ``project.archived``.

        Refused while a task is ``claimed``, ``running``, ``paused`` or ``validating`` (``details.task_ids``). An
        archived project admits no claim and no change of structure. Already archived: nothing changes.
        Errors: ``not_found``, ``invalid_state`` (not a user, stale revision, work in progress).
        """
        return self._set_archived(project_id, True, actor, expected_revision)

    @service_errors
    def unarchive(self, project_id: str, *, actor: str, expected_revision: int) -> dict[str, Any]:
        """Bring an archived project back; event ``project.unarchived``. Errors as ``archive``."""
        return self._set_archived(project_id, False, actor, expected_revision)

    @service_errors
    def attach_stage(self, project_id: str, group_id: str, *, actor: str, expected_revision: int) -> dict[str, Any]:
        """Add an existing group (for example one imported from ORQ) as the last stage, without renaming it.

        Event ``project.stage_attached``. A group already in this project: nothing changes. Errors: ``not_found``
        (project or group), ``invalid_state`` (not a user, stale revision, archived project, group of another project).
        """
        actor = _user_actor(actor)
        expected_revision = _revision(expected_revision)
        group_id = _identifier("group_id", group_id, _GROUP_ID)
        with self._tasks.store.transaction() as conn:
            project = self._project_or_missing(conn, project_id)
            self._stale(project, expected_revision)
            self._active(project)
            group = self._tasks._group_or_missing(group_id, conn)
            if group.get("project_id") == project["id"]:
                return self._project_view_in(conn, project["id"])
            if group.get("project_id"):
                raise _conflict(f"Group {group_id} already belongs to project {group['project_id']}",
                                group_id=group_id, project_id=group["project_id"])
            position = len(self._tasks.store.list_groups(project_id=project["id"], conn=conn)) + 1
            self.store.attach_group(conn, group_id, project["id"], position)
            self.store.update_project(conn, project["id"], expected_revision=expected_revision)
            self._event(conn, "project.stage_attached", project["id"], actor=actor, group_id=group_id,
                        payload={"group_id": group_id, "position": position})
            return self._project_view_in(conn, project["id"])

    @service_errors
    def reorder(self, project_id: str, group_ids: Iterable[str], *, actor: str, expected_revision: int
                ) -> dict[str, Any]:
        """Put the stages in this order (positions ``1..n`` without gaps); event ``project.stages_reordered``.

        ``group_ids`` must hold exactly the project's stages. Errors: ``not_found``, ``invalid_request`` (not the
        same stages), ``invalid_state`` (not a user, stale revision, archived project).
        """
        actor = _user_actor(actor)
        expected_revision = _revision(expected_revision)
        if isinstance(group_ids, (str, bytes, Mapping)):
            raise _invalid("group_ids", "group_ids must be a list")
        wanted = [_identifier("group_ids", item, _GROUP_ID) for item in group_ids]
        with self._tasks.store.transaction() as conn:
            project = self._project_or_missing(conn, project_id)
            self._stale(project, expected_revision)
            self._active(project)
            current = [g["id"] for g in self._tasks.store.list_groups(project_id=project["id"], conn=conn)]
            if len(set(wanted)) != len(wanted) or sorted(wanted) != sorted(current):
                raise _invalid("group_ids", "group_ids must list each stage of the project exactly once",
                               stages=current)
            if wanted != current:
                self.store.write_positions(conn, project["id"], wanted)
                self.store.update_project(conn, project["id"], expected_revision=expected_revision)
                self._event(conn, "project.stages_reordered", project["id"], actor=actor,
                            payload={"count": len(wanted),
                                     "order_sha256": hashlib.sha256(_canonical_json(wanted).encode("utf-8")).hexdigest()})
            return self._project_view_in(conn, project["id"])

    # ------------------------------------------------------------------ closing a stage
    @service_errors
    def close_stage(self, project_id: str, group_id: str, *, actor: str, expected_revision: int) -> dict[str, Any]:
        """Verify and close a stage (``Espera tu verificación`` to ``Cerrada``); event ``group.lifecycle_changed``.

        The stage must be waiting for verification: every task done or cancelled and at least one done. A task that
        was accepted but has no delivery evidence gets it from its last result (``result://<task>/<attempt>``,
        recorded as ``release.delivery_recorded``), so a stage with no patch can close. Everything happens in one
        transaction. ``expected_revision`` is the stage's (``stages[].revision`` of ``get``). Returns the project
        view plus ``closed_stage`` (``group_id`` and ``evidence_recorded``).
        Errors: ``not_found`` (project, or a stage that is not in it), ``invalid_state`` (not a user, stale revision,
        archived project, a stage that is not waiting for verification, an accepted task with no result, a patch that
        was not integrated).
        """
        actor = _user_actor(actor)
        expected_revision = _revision(expected_revision)
        group_id = _identifier("group_id", group_id, _GROUP_ID)
        with self._tasks.store.transaction() as conn:
            project = self._project_or_missing(conn, project_id)
            self._active(project)
            group = self._tasks._group_or_missing(group_id, conn)
            if group.get("project_id") != project["id"]:
                raise ServiceError(NOT_FOUND, f"Stage not found in project {project['id']}: {group_id}", status=404,
                                   details={"project": project["id"], "group_id": group_id})
            if group["revision"] != expected_revision:
                raise _conflict("Stale group revision", group_id=group_id, expected_revision=expected_revision,
                                revision=group["revision"])
            if group["lifecycle_state"] == "closed":
                return {**self._project_view_in(conn, project["id"]),
                        "closed_stage": {"group_id": group_id, "evidence_recorded": []}}
            ctx = self._context(conn, [group_id])
            refs = [self._task_ref(task, group, ctx) for task in ctx["tasks"].values() if task["group_id"] == group_id]
            keys = [ref["label"]["key"] for ref in refs]
            if labels.stage_label_key("open", bool(group["authorized"]), keys) != "verify":
                waiting = [{"task_id": ref["id"], "label": ref["label"]["key"]}
                           for ref in refs if ref["label"]["key"] not in ("done", "cancelled")]
                raise _conflict("The stage is not waiting for verification", group_id=group_id,
                                reason="not_ready_to_verify", tasks=waiting[:50])
            recorded = self._derive_evidence(conn, project["id"], group_id, ctx, actor=actor)
            self._tasks._apply_group_state(conn, group_id, "closed", actor=actor, cause=f"project:{project['id']}",
                                           expected_revision=expected_revision, ignore_cancelled=True)
            return {**self._project_view_in(conn, project["id"]),
                    "closed_stage": {"group_id": group_id, "evidence_recorded": recorded}}

    def _derive_evidence(self, conn: sqlite3.Connection, project_id: str, group_id: str, ctx: Mapping[str, Any], *,
                         actor: str) -> list[str]:
        """Record the delivery of every accepted task that has none, from its last result."""
        tasks = [t for t in ctx["tasks"].values() if t["group_id"] == group_id and t["state"] == "accepted"]
        envelopes = self._services.envelopes.store
        plans: list[tuple[str, dict[str, Any]]] = []
        missing: list[str] = []
        unintegrated: list[str] = []
        for task in tasks:
            facts = ctx["release"]["facts"].get(task["id"])
            if facts and facts.get("delivery_sha256"):
                continue
            result = envelopes.latest_result(task["id"], conn=conn)
            if result is None:
                missing.append(task["id"])
                continue
            has_patch = bool(result["patch_sha256"])
            if has_patch:
                unintegrated.append(task["id"])
            plans.append((task["id"], {
                "delivery_ref": result_ref(task["id"], result["attempt"]), "delivery_sha256": result["result_sha256"],
                "has_patch": has_patch, "patchset_sha256": result["patch_sha256"], "test_verdict": None,
                "test_patchset_sha256": "", "test_ref": "", "integration_commit": "", "integration_ref": ""}))
        if missing:
            raise _conflict("Accepted tasks have no result to take their delivery from", group_id=group_id,
                            reason="missing_results", task_ids=missing[:50])
        if unintegrated:
            raise _conflict("Tasks delivered with a patch need their integration recorded first", group_id=group_id,
                            reason="integration_required", task_ids=unintegrated[:50])
        for task_id, fact in plans:
            self._tasks._write_release_fact(conn, task_id, actor=actor, fact=fact, event="release.delivery_recorded",
                                            ref=fact["delivery_ref"], cause=f"project:{project_id} close_stage",
                                            event_cause=f"project:{project_id}")
        return [task_id for task_id, _ in plans]

    # ------------------------------------------------------------------ chats linked to a task
    @staticmethod
    def _session_id(value: Any) -> str:
        from .sessions import _parse_id   # invalid_session_id (400) for a malformed id
        source, _ = _parse_id(value)
        if source == "framework":
            raise _invalid("session_id", "A framework session takes the task of its run; link only chats of other apps")
        return value

    def _links(self, conn: sqlite3.Connection, task_id: str) -> dict[str, Any]:
        return {"task_id": task_id, "sessions": [
            {"session_id": row["session_id"], "linked_by": row["linked_by"], "created_at": row["created_at"]}
            for row in self.store.session_links(task_id, conn=conn)]}

    @service_errors
    def link_session(self, task_id: str, session_id: str, *, actor: str) -> dict[str, Any]:
        """Link a chat of another application (``<source>:<id>``, never ``framework:``) to a task.

        Event ``task.session_linked`` (once: linking again changes nothing). Returns the task's links.
        Errors: ``not_found`` (task), ``invalid_session_id`` (400), ``invalid_request``.
        """
        task_id = _identifier("task_id", task_id, _TASK_ID)
        actor = _actor(actor)
        session_id = self._session_id(session_id)
        with self._tasks.store.transaction() as conn:
            task = self._tasks.store.get_task(task_id, conn=conn)
            if task is None:
                raise _missing("Task", task_id)
            if self.store.add_session_link(conn, task_id, session_id, linked_by=actor):
                self._tasks.store.append_event(conn, "task.session_linked", actor=actor, group_id=task["group_id"],
                                               task_id=task_id, ref=session_id, payload={"session_id": session_id})
            return self._links(conn, task_id)

    @service_errors
    def unlink_session(self, task_id: str, session_id: str, *, actor: str) -> dict[str, Any]:
        """Remove the link; event ``task.session_unlinked`` if there was one. Returns the task's links."""
        task_id = _identifier("task_id", task_id, _TASK_ID)
        actor = _actor(actor)
        session_id = self._session_id(session_id)
        with self._tasks.store.transaction() as conn:
            task = self._tasks.store.get_task(task_id, conn=conn)
            if task is None:
                raise _missing("Task", task_id)
            if self.store.remove_session_link(conn, task_id, session_id):
                self._tasks.store.append_event(conn, "task.session_unlinked", actor=actor, group_id=task["group_id"],
                                               task_id=task_id, ref=session_id, payload={"session_id": session_id})
            return self._links(conn, task_id)
