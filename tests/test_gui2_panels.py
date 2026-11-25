# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI2-T05: the right-panel sections «Cambios», «Tokens» and «Fuentes».

Synthetic Claude sessions, subagents and framework runs in ``tmp_path`` (never the user's real folders). Checks: the
new read-only route ``/gui/api/runs/{id}/usage`` (token, answer, errors), the client logic (``panel-model.js`` and
``panel-text.js``, pure modules run with Node when it is installed) fed with the real answers of the API, and static
checks of the client code and the stylesheet (CSP rules, accessibility, help, only /gui/api routes, no message text)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from tests.gui_text import ui_text
from multiagent.gui import STATIC_DIR
from multiagent.gui.security import CSP
from multiagent.gui.token import ensure_token
from session_fixtures import CLAUDE_ID, add_framework_run, claude_user, jsonl, make_services, make_stores
from test_gui_sessions_view import FORBIDDEN

JS = STATIC_DIR / "js"
NODE = shutil.which("node")
NEW_JS = ["panel-text.js", "panel-model.js", "panel-data.js", "panel-parts.js", "panel-changes.js", "panel-tokens.js",
          "panel-sources.js"]
SECRET = "SECRETO-QUE-NO-DEBE-SALIR"
NEW_TOPICS = ["panel.changes", "panel.tokens", "panel.sources", "panel.retry", "panel.more", "panel.by_model",
              "panel.cost_evidence"]


def read(name: str) -> str:
    return ui_text((JS / name))


def css() -> str:
    return re.sub(r"/\*.*?\*/", "", (STATIC_DIR / "panels.css").read_text(encoding="utf-8"), flags=re.S)


# --- synthetic data -------------------------------------------------------------------------------------------

def assistant(when: str, content: list, mid: str, *, model: str = "claude-sonnet-5-5", usage: dict | None = None) -> dict:
    message = {"id": mid, "role": "assistant", "model": model, "content": content}
    if usage is not None:
        message["usage"] = usage
    return {"type": "assistant", "timestamp": when, "message": message}


def use(call_id: str, name: str, args: dict) -> dict:
    return {"type": "tool_use", "id": call_id, "name": name, "input": args}


def result(when: str, call_id: str) -> dict:
    return {"type": "user", "timestamp": when, "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": call_id, "content": f"salida {SECRET}"}]}}


def write_session(stores) -> dict[str, str]:
    """A Claude session with edits, reads, a fetch, a search and an attachment, and two subagents."""
    work = stores.work
    paths = {"app": str(work / "src" / "app.py"), "new": str(work / "src" / "nuevo.py"), "readme": str(work / "README.md"),
             "src": str(work / "src"), "outside": str(stores.other / "ajeno.txt")}
    jsonl(stores.claude / "projects" / "proj" / f"{CLAUDE_ID}.jsonl", [
        claude_user(f"haz esto {SECRET}", "2026-10-11T10:00:00.000Z", str(work)),
        {"type": "user", "timestamp": "2026-10-11T10:00:01.000Z", "message": {"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "data": "AAAA"}}]}},
        assistant("2026-10-11T10:00:05.000Z", [
            use("t1", "Edit", {"file_path": paths["app"], "old_string": SECRET, "new_string": "nuevo"}),
            use("t2", "Write", {"file_path": paths["new"], "content": SECRET}),
            use("t3", "Read", {"file_path": paths["readme"]}),
            use("t4", "Grep", {"pattern": SECRET, "path": paths["src"]}),
            use("t5", "WebFetch", {"url": f"https://ejemplo.test/docs/guia?clave={SECRET}#parte", "prompt": SECRET}),
            use("t6", "WebSearch", {"query": SECRET}),
            use("t7", "Bash", {"command": f"echo {SECRET}"})], "m1", usage={"input_tokens": 100, "output_tokens": 50}),
        result("2026-10-11T10:00:09.000Z", "t1"),
        assistant("2026-10-11T10:01:00.000Z", [
            use("t8", "Edit", {"file_path": paths["app"].replace("\\", "/"), "old_string": "a", "new_string": "b"}),
            use("t9", "Read", {"file_path": paths["readme"]}),
            use("t10", "Read", {"file_path": paths["outside"]}),
            {"type": "text", "text": f"listo {SECRET}"}], "m2", usage={"input_tokens": 200, "output_tokens": 70, "cache_read_input_tokens": 30}),
        {"type": "custom-title", "customTitle": "Sesion de prueba"},
    ])
    for name, usage in (("one", {"input_tokens": 10, "output_tokens": 5}), ("two", None)):
        jsonl(stores.claude / "projects" / "proj" / CLAUDE_ID / "subagents" / f"agent-{name}.jsonl", [
            {"type": "user", "timestamp": "2026-10-11T10:00:10.000Z", "message": {"role": "user", "content": f"tarea {SECRET}"}},
            assistant("2026-10-11T10:00:20.000Z", [{"type": "text", "text": f"hecho {SECRET}"}], f"s-{name}",
                      model="claude-haiku-5", usage=usage)])
    return paths


@pytest.fixture
def stores(tmp_path):
    return make_stores(tmp_path)


@pytest.fixture
def world(stores):
    services = make_services(stores)
    token = ensure_token(services.settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, stores=stores, token=token,
                              auth={"Authorization": f"Bearer {token}"})
    services.runs.close()


def basis(model: str, tokens_in: int, tokens_out: int, cost: float, *, currency: str = "USD") -> dict:
    side = {"amount": 0.5, "denominator": 1000000, "unit": "token", "source": f"Lista de precios sintetica de {model}",
            "valid_from": "2026-01-01T00:00:00Z", "valid_until": "2036-01-01T00:00:00Z", "provenance": "official_price_page"}
    return {"model_id": model, "currency": currency, "cost": cost, "tokens_in": tokens_in, "tokens_out": tokens_out,
            "priced_at": "2026-10-11T10:00:00+00:00", "input": side, "output": {**side, "amount": 1.5}}


def record(world, run_id: str, step: str, model: str, tokens_in, tokens_out, cost=None, cost_basis=None) -> None:
    world.services.system.store.record_telemetry(
        run_id=run_id, step_id=step, agent_id="agente", attempt=1, provider="prueba", model=model, model_digest="",
        prompt_id="p", prompt_version=1, prompt_sha256="", brand_version=None, tokens_in=tokens_in, tokens_out=tokens_out,
        num_ctx=None, latency_ms=1, duration_ms=1, estimated_cost_usd=cost,
        cost_basis=json.dumps(cost_basis) if cost_basis else None, success=1, error_type="")


def priced_run(world) -> str:
    run_id = add_framework_run(world.services, "costos")
    record(world, run_id, "s0", "modelo-a", 1000, 200, 0.0008, basis("modelo-a", 1000, 200, 0.0008))
    record(world, run_id, "s1", "modelo-b", 3000, 400, 0.002, basis("modelo-b", 3000, 400, 0.002))
    record(world, run_id, "s1", "modelo-b", 50, None)            # tokens not reported, no price
    record(world, run_id, "s2", "modelo-c", 70, 10)              # tokens but no price evidence
    return run_id


# --- the route ------------------------------------------------------------------------------------------------

def test_usage_route_needs_the_token_like_every_gui_route(world):
    run_id = priced_run(world)
    url = f"/gui/api/runs/{run_id}/usage"
    assert world.client.get(url).status_code == 401
    assert world.client.get(url, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert world.client.get(url, headers={"Authorization": f"Basic {world.token}"}).status_code == 401
    assert world.client.get(url, headers=world.auth).status_code == 200


def test_usage_route_is_the_services_answer_with_unknowns_as_null(world):
    run_id = priced_run(world)
    body = world.client.get(f"/gui/api/runs/{run_id}/usage", headers=world.auth).json()
    assert body == world.services.runs.usage(run_id)
    assert body["cost_is_estimate"] is True and len(body["records"]) == 4
    run = body["totals"]["run"]
    assert run["records"] == 4 and run["records_without_usage"] == 1            # one call reported only its input tokens
    assert run["tokens_in"] == 4120 and run["tokens_out"] == 610
    assert run["estimated_cost_usd"] == pytest.approx(0.0028) and run["cost_complete"] is False
    assert run["cost_known_records"] == 2 and run["cost_unknown_records"] == 2
    assert [entry["model"] for entry in body["totals"]["by_model"]] == ["modelo-a", "modelo-b", "modelo-c"]
    unreported = body["records"][2]
    assert unreported["tokens_out"] is None and unreported["tokens_total"] is None and unreported["cost_basis"] is None
    assert body["records"][0]["cost_basis"]["input"]["provenance"] == "official_price_page"


def test_usage_route_unknown_run_is_404_and_a_run_without_calls_has_unknown_totals(world):
    assert world.client.get("/gui/api/runs/no-existe/usage", headers=world.auth).status_code == 404
    empty = add_framework_run(world.services, "vacia")
    body = world.client.get(f"/gui/api/runs/{empty}/usage", headers=world.auth).json()
    run = body["totals"]["run"]
    assert body["records"] == [] and run["records"] == 0
    assert run["tokens_in"] is None and run["tokens_out"] is None and run["estimated_cost_usd"] is None   # never 0
    assert run["cost_complete"] is False


def test_usage_route_is_read_only(world):
    run_id = priced_run(world)
    for method in ("post", "put", "delete", "patch"):
        response = getattr(world.client, method)(f"/gui/api/runs/{run_id}/usage", headers=world.auth)
        assert response.status_code in (405, 422), method


# --- the client logic, run with Node ---------------------------------------------------------------------------

def run_node(tmp_path, script: str, data=None):
    """Runs an ES-module script; the JSON `data` is read from a file (Windows limits the command line)."""
    path = tmp_path / "data.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    done = subprocess.run([NODE, "--input-type=module", "-e", script, str(path)], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def uri(name: str) -> str:
    return json.dumps((JS / name).as_uri())


PRELUDE = """
import fs from "node:fs";
import * as model from %s;
import * as text from %s;
const data = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
""" % (uri("panel-model.js"), uri("panel-text.js"))
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@needs_node
def test_subject_is_the_session_or_the_run_open_in_the_centre(tmp_path):
    out = run_node(tmp_path, PRELUDE + """
      const s = model.subjectOf;
      console.log(JSON.stringify([
        s({ section: "sessions", id: "claude:x" }, "claude:x"), s({ section: "sessions", id: null }, null),
        s({ section: "runs", id: "abc123" }, null), s({ section: "runs", id: null }, null),
        s({ section: "sessions", id: "framework:r9" }, "framework:r9"), s({ section: "runs", id: "../x" }, null),
        s({ section: "new", id: null }, null)]));
    """)
    assert out[0] == {"kind": "session", "runId": None, "sessionId": "claude:x", "key": "session:claude:x"}
    assert out[1] is None and out[3] is None and out[5] is None and out[6] is None
    assert out[2] == {"kind": "run", "runId": "abc123", "sessionId": "framework:abc123", "key": "run:abc123"}
    assert out[4]["kind"] == "run" and out[4]["runId"] == "r9" and out[4]["key"] == "run:r9"


@needs_node
def test_tools_are_classified_by_name(tmp_path):
    names = ["Edit", "MultiEdit", "Write", "NotebookEdit", "apply_patch", "str_replace_based_edit_tool", "create", "Read",
             "Grep", "Glob", "LS", "view", "NotebookRead", "WebFetch", "web_fetch", "WebSearch", "web_search", "Bash",
             "shell", "Task", "", None]
    out = run_node(tmp_path, PRELUDE + "console.log(JSON.stringify(data.map(model.toolClass)));", names)
    assert out == ["write", "write", "write", "write", "write", "write", "write", "read", "read", "read", "read", "read",
                   "read", "fetch", "fetch", "search", "search", "other", "other", "other", "other", "other"]


@needs_node
def test_digest_counts_edits_reads_pages_searches_and_attachments_without_inventing_lines(tmp_path):
    items = [
        {"kind": "tool_call", "tool": {"name": "Edit", "summary": "file_path=C:\\w\\a.py"}, "files": ["C:\\w\\a.py"]},
        {"kind": "tool_call", "tool": {"name": "Write"}, "files": ["C:\\w\\b.py"]},
        {"kind": "tool_call", "tool": {"name": "Edit"}, "files": ["c:/w/a.py"]},          # same file, other separators and case
        {"kind": "tool_call", "tool": {"name": "Read"}, "files": ["C:\\w\\r.md"]},
        {"kind": "tool_call", "tool": {"name": "Read"}, "files": ["C:\\w\\r.md", "C:\\w\\q.md"]},
        {"kind": "tool_call", "tool": {"name": "WebFetch", "summary": "url=https://x.test/a/b?token=1#f, prompt=hola"}, "files": []},
        {"kind": "tool_call", "tool": {"name": "WebFetch", "summary": "url=ftp://x.test/a, prompt=hola"}, "files": []},
        {"kind": "tool_call", "tool": {"name": "WebSearch", "summary": "query=algo"}, "files": []},
        {"kind": "tool_call", "tool": {"name": "Bash", "summary": "command=ls"}, "files": ["C:\\w\\no.txt"]},
        {"kind": "tool_call", "tool": None, "files": []},
        {"kind": "user_message", "text": "[image]"}, {"kind": "assistant_message", "text": "[document]"},
        {"kind": "user_message", "text": "[image] de verdad"}, {"kind": "tool_result", "text": "x"}, None,
    ]
    out = run_node(tmp_path, PRELUDE + """
      const digest = model.newDigest(); model.addItems(digest, data);
      console.log(JSON.stringify(model.snapshotDigest(digest)));
    """, items)
    assert out["items"] == 15 and out["tools"] == 10 and out["searches"] == 1 and out["attachments"] == 2
    # newest first: a.py was touched again after b.py; the first spelling of a path is the one shown
    assert [(f["path"], f["edits"]) for f in out["edits"]] == [("C:\\w\\a.py", 2), ("C:\\w\\b.py", 1)]
    assert all(f["additions"] is None and f["deletions"] is None for f in out["edits"])        # the items carry no lines
    assert {f["path"]: f["count"] for f in out["reads"]} == {"C:\\w\\r.md": 2, "C:\\w\\q.md": 1}
    assert [(entry["url"], entry["count"]) for entry in out["urls"]] == [("https://x.test/a/b", 1)]   # no query, fragment or ftp
    assert out["more"] is False


@needs_node
def test_most_recently_touched_files_come_first_and_counts_are_used_when_a_reader_gives_them(tmp_path):
    items = [
        {"kind": "tool_call", "tool": {"name": "Edit"}, "files": ["a.py"], "changes": [{"path": "a.py", "additions": 3, "deletions": 1}]},
        {"kind": "tool_call", "tool": {"name": "Edit"}, "files": ["b.py"]},
        {"kind": "tool_call", "tool": {"name": "Edit"}, "files": ["a.py"], "changes": [{"path": "a.py", "additions": 2, "deletions": None},
                                                                                     {"path": "no-lista.py", "additions": 9, "deletions": 9}]},
        {"kind": "tool_call", "tool": {"name": "Edit"}, "files": ["c.py"], "changes": "roto"},
    ]
    out = run_node(tmp_path, PRELUDE + """
      const digest = model.newDigest(); model.addItems(digest, data);
      const changes = model.changesModel({ kind: "session" }, { view: model.snapshotDigest(digest) });
      console.log(JSON.stringify({ files: changes.files.map((f) => [f.path, f.edits, f.additions, f.deletions]), totals: changes.totals,
        summary: text.changesSummary(changes.totals) }));
    """, items)
    # newest first; a.py counted twice (3+2 added, 1+null removed); a count for a path the call does not list still counts;
    # a file with no count stays null (never 0); a broken `changes` value is ignored
    assert out["files"] == [["c.py", 1, None, None], ["no-lista.py", 1, 9, 9], ["a.py", 2, 5, 1], ["b.py", 1, None, None]]
    assert out["totals"]["counted"] == 2 and out["totals"]["files"] == 4
    assert out["summary"] == "4 archivos modificados · +14 −10 líneas (de 2 archivos con recuento)"


@needs_node
def test_a_digest_keeps_at_most_300_paths_and_says_there_are_more(tmp_path):
    items = [{"kind": "tool_call", "tool": {"name": "Write"}, "files": [f"f{n}.txt"]} for n in range(305)]
    out = run_node(tmp_path, PRELUDE + """
      const digest = model.newDigest(); model.addItems(digest, data);
      const snap = model.snapshotDigest(digest);
      console.log(JSON.stringify({ n: snap.edits.length, more: snap.more, first: snap.edits[0].path, max: model.MAX_PATHS }));
    """, items)
    assert out == {"n": 300, "more": True, "first": "f299.txt", "max": 300}


# The session of the API -> the client model, end to end.

def api_get(world, path: str) -> dict:
    response = world.client.get(f"/gui/api{path}", headers=world.auth)
    assert response.status_code == 200, (path, response.text)
    return response.json()


@needs_node
def test_changes_and_sources_of_a_real_session_answer_show_paths_only(world, tmp_path):
    paths = write_session(world.stores)
    session = f"claude:{CLAUDE_ID}"
    items = api_get(world, f"/sessions/{session}/items?limit=200")
    assert items["done"] is True and any(item["kind"] == "tool_call" for item in items["items"])
    out = run_node(tmp_path, PRELUDE + """
      const digest = model.newDigest(); model.addItems(digest, data.items.items);
      const subject = { kind: "session" };
      const shot = { view: model.snapshotDigest(digest), summary: data.summary };
      const changes = model.changesModel(subject, shot);
      const sources = model.sourcesModel(subject, shot);
      console.log(JSON.stringify({ changes, sources, count: model.sourcesCount(sources),
        shown: changes.files.map((f) => text.relativePath(f.path, data.summary.cwd)),
        summary: text.changesSummary(changes.totals) }));
    """, {"items": items, "summary": api_get(world, f"/sessions/{session}")})
    assert sorted(out["shown"]) == ["src/app.py", "src/nuevo.py"]                   # relative to the session folder
    edits = {file["path"]: file["edits"] for file in out["changes"]["files"]}
    assert edits == {paths["app"]: 2, paths["new"]: 1}              # the second Edit spelled the path with "/": same file
    assert out["changes"]["totals"]["counted"] == 0 and "no constan" in out["summary"]   # the items have no +/- : not invented
    sources = out["sources"]
    assert sources["kind"] == "session" and sources["folder"] == str(world.stores.work)
    read_paths = {entry["path"]: entry["count"] for entry in sources["files"]}
    assert read_paths == {paths["readme"]: 2, paths["src"]: 1, paths["outside"]: 1}
    assert {entry["path"]: entry["shown"] for entry in sources["files"]}[paths["readme"]] == "README.md"
    assert sources["urls"][0]["url"] == "https://ejemplo.test/docs/guia" and sources["urls"][0]["count"] == 1
    assert sources["searches"] == 1 and sources["attachments"] == 1 and out["count"] == 1 + 3 + 1 + 1 + 1
    assert SECRET not in json.dumps(out)                    # no message, argument or result text reaches the model


@needs_node
def test_tokens_of_a_session_orchestrator_against_workers_with_unknown_cost(world, tmp_path):
    write_session(world.stores)
    session = f"claude:{CLAUDE_ID}"
    summary, children = api_get(world, f"/sessions/{session}"), api_get(world, f"/sessions/{session}/children")
    assert len(children["children"]) == 2
    out = run_node(tmp_path, PRELUDE + """
      const view = model.tokensModel({ kind: "session" }, { summary: data.summary, children: data.children });
      console.log(JSON.stringify({ view, total: text.tokensLine(view.total), orchestrator: text.tokensLine(view.orchestrator.tokens, { cache: true }),
        workers: text.tokensLine(view.workers.tokens), cost: text.costText(view.cost), note: text.costNote(view.cost),
        none: text.tokensLine(null) }));
    """, {"summary": summary, "children": children})
    view = out["view"]
    assert view["orchestrator"]["label"] == "Orquestador (la sesión)"
    assert view["orchestrator"]["tokens"]["input"] == 300 and view["orchestrator"]["tokens"]["output"] == 120
    assert out["orchestrator"] == "entrada 300 · salida 120 · caché leída 30"
    assert view["workers"]["count"] == 2 and view["workers"]["unreported"] == 1           # one subagent has no usage
    assert out["workers"] == "entrada 10 · salida 5"
    assert out["total"] == "entrada 310 · salida 125" and view["incomplete"] is True
    assert [(entry["model"], entry["count"], entry["unreported"]) for entry in view["byModel"]] == [("claude-haiku-5", 2, 1)]
    assert out["cost"] == "desconocido" and "apps no informan el precio" in out["note"] and out["none"] == "desconocido"
    assert view["cost"]["state"] == "unknown"


@needs_node
def test_a_subagent_open_in_the_centre_is_its_own_row_with_no_workers(world, tmp_path):
    write_session(world.stores)
    sub = f"claude:{CLAUDE_ID}:agent-one"
    summary, children = api_get(world, f"/sessions/{sub}"), api_get(world, f"/sessions/{sub}/children")
    out = run_node(tmp_path, PRELUDE + """
      const view = model.tokensModel({ kind: "session" }, data);
      console.log(JSON.stringify({ label: view.orchestrator.label, workers: view.workers, total: text.tokensLine(view.total) }));
    """, {"summary": summary, "children": children})
    assert out["label"] == "Esta conversación (subagente)" and out["workers"]["count"] == 0
    assert out["workers"]["tokens"] is None and out["total"] == "entrada 10 · salida 5"


@needs_node
def test_tokens_of_a_run_come_from_the_telemetry_with_partial_cost_and_price_evidence(world, tmp_path):
    run_id = priced_run(world)
    usage = api_get(world, f"/runs/{run_id}/usage")
    out = run_node(tmp_path, PRELUDE + """
      const view = model.tokensModel({ kind: "run" }, { usage: data });
      console.log(JSON.stringify({ view, total: text.tokensLine(view.total), cost: text.costText(view.cost), note: text.costNote(view.cost),
        price: text.priceText(view.cost.evidence[0].input, view.cost.evidence[0].currency),
        proof: text.evidenceText(view.cost.evidence[0].input) }));
    """, usage)
    view = out["view"]
    assert view["orchestrator"]["noModel"] is True and view["orchestrator"]["tokens"] is None
    assert view["workers"]["count"] == 4 and view["workers"]["unreported"] == 1 and out["total"] == "entrada 4120 · salida 610"
    assert view["incomplete"] is True
    assert view["cost"]["state"] == "partial" and view["cost"]["unknownCalls"] == 2
    assert out["cost"] == "0,0028 USD (parcial: 2 llamadas sin precio)" and "deja fuera 2 llamadas" in out["note"]
    assert [item["model"] for item in view["cost"]["evidence"]] == ["modelo-a", "modelo-b"]       # only the priced calls have evidence
    assert out["price"] == "0,5 USD por 1.000.000 tokens"
    assert out["proof"].startswith("fuente: Lista de precios sintetica de modelo-a; vigente del 2026-01-01 al 2036-01-01")
    assert out["proof"].endswith("procedencia: official_price_page")
    models = {entry["model"]: entry for entry in view["byModel"]}
    assert models["modelo-b"]["count"] == 2 and models["modelo-b"]["unreported"] == 1 and models["modelo-c"]["tokens"]["output"] == 10


@needs_node
def test_an_unknown_cost_is_never_written_as_zero(world, tmp_path):
    no_calls = add_framework_run(world.services, "sin llamadas")
    unpriced = add_framework_run(world.services, "sin precio")
    record(world, unpriced, "s0", "modelo-x", 10, 5)
    free = add_framework_run(world.services, "gratis")
    record(world, free, "s0", "modelo-local", 10, 5, 0.0, basis("modelo-local", 10, 5, 0.0))
    other = add_framework_run(world.services, "otra moneda")
    record(world, other, "s0", "modelo-eur", 100, 50, None, basis("modelo-eur", 100, 50, 0.005, currency="EUR"))
    runs = {name: api_get(world, f"/runs/{run}/usage") for name, run in
            (("none", no_calls), ("unpriced", unpriced), ("free", free), ("other", other))}
    out = run_node(tmp_path, PRELUDE + """
      const rows = {};
      for (const [name, usage] of Object.entries(data)) {
        const view = model.runTokens(usage);
        rows[name] = { cost: text.costText(view.cost), state: view.cost.state, reason: view.cost.reason, total: text.tokensLine(view.total),
          note: text.costNote(view.cost) };
      }
      console.log(JSON.stringify(rows));
    """, runs)
    assert out["none"]["cost"] == "desconocido" and out["none"]["reason"] == "none" and out["none"]["total"] == "desconocido"
    assert out["none"]["note"] == "Todavía no hay llamadas a modelos en esta ejecución."
    assert out["unpriced"]["cost"] == "desconocido" and out["unpriced"]["state"] == "unknown"
    assert out["unpriced"]["total"] == "entrada 10 · salida 5" and "no se inventa" in out["unpriced"]["note"]
    assert out["free"]["cost"] == "0,00 USD" and out["free"]["state"] == "known"        # a declared price of 0 is a real 0
    assert out["other"]["cost"] == "0,005 EUR" and out["other"]["state"] == "known"     # another currency is shown apart


@needs_node
def test_changes_and_sources_of_a_run_use_artifact_file_lists_prompts_and_input_names(world, tmp_path):
    run_id = add_framework_run(world.services, "fuentes")
    world.services.system.db.log_event(run_id, "step_started", {"step_id": "s0", "prompt_id": "researcher", "prompt_version": 3})
    world.services.system.db.log_event(run_id, "step_started", {"step_id": "s1", "prompt_id": "researcher", "prompt_version": 3})
    world.services.system.db.log_event(run_id, "step_started", {"step_id": "s2", "prompt_id": "creator", "prompt_version": 1})
    view = api_get(world, f"/runs/{run_id}")
    assert {"workflow_id", "bundle", "steps", "events", "events_total", "inputs", "artifacts", "terminal"} <= set(view)
    view["artifacts"] = [
        {"step_id": "s0", "attempt": 1, "data": {"files": ["a.py", {"path": "b.py", "additions": 4, "deletions": 1}]}},
        {"step_id": "s1", "attempt": 1, "data": {"changed_files": [{"file": "b.py", "added": 6, "removed": 2}, {"name": ""}, 7]}},
        {"step_id": "s2", "attempt": 1, "data": {"text": SECRET}},
        {"step_id": "s3", "attempt": 1, "data": None},
    ]
    out = run_node(tmp_path, PRELUDE + """
      const subject = { kind: "run" };
      const changes = model.changesModel(subject, { run: data });
      const sources = model.sourcesModel(subject, { run: data });
      console.log(JSON.stringify({ changes, sources, summary: text.changesSummary(changes.totals), count: model.sourcesCount(sources),
        prompts: sources.prompts.map(text.promptText) }));
    """, view)
    files = {file["path"]: file for file in out["changes"]["files"]}
    assert set(files) == {"a.py", "b.py"} and out["changes"]["artifacts"] == 4
    assert files["b.py"]["edits"] == 2 and (files["b.py"]["additions"], files["b.py"]["deletions"]) == (6, 2)   # the latest result counts
    assert files["a.py"]["additions"] is None and out["changes"]["totals"]["counted"] == 1
    assert out["summary"] == "2 archivos modificados · +6 −2 líneas (de 1 archivo con recuento)"
    sources = out["sources"]
    assert sources["kind"] == "run" and sources["workflow"] == view["workflow_id"] and sources["prompts"] != []
    assert out["prompts"] == ["researcher v3", "creator v1"]                                  # once each, in order of use
    assert sources["inputs"] == list(view["inputs"]) and sources["partial"] is False
    assert SECRET not in json.dumps(out)


@needs_node
def test_a_run_whose_results_list_no_files_says_so(tmp_path):
    out = run_node(tmp_path, PRELUDE + """
      console.log(JSON.stringify([model.changesModel({ kind: "run" }, { run: data }), model.changesModel({ kind: "run" }, { run: null })]));
    """, {"artifacts": [{"step_id": "s0", "data": {"title": "x"}}]})
    assert out[0]["files"] == [] and out[0]["artifacts"] == 1
    assert out[1] == {"files": [], "totals": {"files": 0, "edits": 0, "additions": 0, "deletions": 0, "counted": 0}, "artifacts": 0, "more": False}


@needs_node
def test_text_helpers(tmp_path):
    out = run_node(tmp_path, PRELUDE + """
      console.log(JSON.stringify({
        tokens: [null, {}, { input: 12345, output: 6 }, { total: 77 }, { input: 5, cache_read: 9 }].map((t) => text.tokensLine(t)),
        cache: text.tokensLine({ input: 1, output: 2, cache_read: 3, cache_write: 4 }, { cache: true }),
        money: [text.fmtMoney(0.00123, "USD"), text.fmtMoney(12, "EUR"), text.fmtMoney(0, "USD")],
        rel: [text.relativePath("C:\\\\w\\\\p\\\\src\\\\a.py", "c:/w/p"), text.relativePath("/x/y/z.py", "/x/y/"), text.relativePath("/x/yy/z.py", "/x/y"),
          text.relativePath("a.py", null), text.relativePath("C:\\\\w\\\\p", "C:\\\\w\\\\p")],
        split: [text.splitPath("src/a.py"), text.splitPath("a.py"), text.splitPath("C:\\\\w\\\\a.py"), text.splitPath("dir/")],
        edits: [text.editsText(1), text.editsText(3)], appears: [text.appearsText(1), text.appearsText(2)],
        summary: [text.changesSummary({ files: 0 }), text.changesSummary({ files: 1, counted: 0 }), text.changesSummary({ files: 2, counted: 2, additions: 1200, deletions: 3 })],
        wd: [text.withoutData(0), text.withoutData(2)],
        searches: [text.searchesText(1), text.searchesText(2)], attach: [text.attachmentsText(1), text.attachmentsText(3)],
        cost: [text.costText(null), text.costText({ state: "unknown" }), text.costText({ state: "known", amount: 1.5, currency: "USD", other: [] }),
          text.costText({ state: "partial", amount: 1, currency: "USD", unknownCalls: 1, other: [{ currency: "EUR", amount: 2 }] })],
        price: [text.priceText(null), text.priceText({ amount: 3, denominator: 1000, unit: "image" }, "USD")],
        loading: [text.loadingText(0), text.loadingText(2500)],
      }));
    """)
    assert out["tokens"] == ["desconocido", "desconocido", "entrada 12.345 · salida 6", "total 77", "entrada 5"]
    assert out["cache"] == "entrada 1 · salida 2 · caché leída 3 · caché escrita 4"
    assert out["money"] == ["0,00123 USD", "12,00 EUR", "0,00 USD"]
    assert out["rel"] == ["src/a.py", "z.py", "/x/yy/z.py", "a.py", "C:\\w\\p"]
    assert out["split"] == [{"dir": "src/", "name": "a.py"}, {"dir": "", "name": "a.py"}, {"dir": "C:\\w\\", "name": "a.py"},
                            {"dir": "dir/", "name": "dir/"}]
    assert out["edits"] == ["1 edición", "3 ediciones"] and out["appears"] == ["en 1 resultado", "en 2 resultados"]
    assert out["summary"] == ["Ningún archivo modificado.", "1 archivo modificado · líneas añadidas y quitadas: no constan",
                              "2 archivos modificados · +1200 −3 líneas"]      # Spanish groups from five digits: 1200, 12.345
    assert out["wd"] == ["", "2 sin datos de tokens"]
    assert out["searches"] == ["1 búsqueda web", "2 búsquedas web"] and out["attach"][0].startswith("1 adjunto")
    assert out["cost"] == ["desconocido", "desconocido", "1,50 USD", "1,00 USD + 2,00 EUR (parcial: 1 llamada sin precio)"]
    assert out["price"] == ["desconocido", "3 USD por 1000 image"]
    assert out["loading"] == ["Leyendo la sesión…", "Leyendo la sesión… 2500 elementos"]


@needs_node
def test_urls_lose_their_query_fragment_and_credentials(tmp_path):
    urls = ["https://u:p@ejemplo.test/a?x=1#f", "http://ejemplo.test/", "HTTPS://Ejemplo.test:8443/p/q", "javascript:alert(1)", "ftp://x/y",
            "ejemplo.test/a", "", None, "https://ejemplo.test/a,"]
    summaries = ["url=https://a.test/x?k=1, prompt=hola", "prompt=hola, url=https://b.test/y", "url=", "query=https://c.test", None]
    out = run_node(tmp_path, PRELUDE + """
      console.log(JSON.stringify({ urls: data.urls.map(model.cleanUrl), summaries: data.summaries.map(model.urlOfSummary) }));
    """, {"urls": urls, "summaries": summaries})
    assert out["urls"] == ["https://ejemplo.test/a", "http://ejemplo.test", "https://ejemplo.test:8443/p/q", None, None, None, None, None,
                           "https://ejemplo.test/a"]
    assert out["summaries"] == ["https://a.test/x", "https://b.test/y", None, None, None]


# --- the client code, read as text -------------------------------------------------------------------------------

def used(text: str, name: str) -> set[str]:
    return set(re.findall(rf"\b{name}\.([a-z_]+)", text))


def test_new_files_exist_and_are_wired_into_the_slots_of_the_right_panel():
    for name in NEW_JS:
        assert (JS / name).is_file(), name
    assert (STATIC_DIR / "panels.css").is_file()
    slots, panel = read("panel-slots.js"), read("right-panel.js")
    for slot, panel_name in (("ChangesSlot", "ChangesPanel"), ("TokensSlot", "TokensPanel"), ("SourcesSlot", "SourcesPanel")):
        assert re.search(rf"export function {slot}\(\{{ token, route, sessionId \}}\)", slots), slot
        assert f"Panel=${{{panel_name}}}" in slots, slot
    assert re.search(r"const slot = \{ token, route, sessionId \};", panel)                  # the contract of GUI2-T02 is intact
    assert 'id="subagents-slot" class="slot" data-slot="subagents"' in slots                  # the T04 slot is untouched
    assert "<${Panel} key=${subject.key} token=${token} subject=${subject} />" in slots      # one instance per session or run
    assert (JS / "main.js").read_text(encoding="utf-8").count("RightPanel") == 2               # the shell composes it as before
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="/gui/static/panels.css">' in html and "style=" not in html


@pytest.mark.parametrize("name", NEW_JS)
def test_new_code_has_no_html_injection_eval_inline_handlers_or_remote_code(name):
    text = read(name)
    for pattern, label in FORBIDDEN:
        assert not re.search(pattern, text), f"{name}: {label}"
    assert "__html" not in text and "style=" not in text and "document.body" not in text   # no inline styles either (CSP)


def test_the_new_code_depends_only_on_shared_helpers_never_on_the_views():
    allowed = {"api.js", "help.js", "text.js", *NEW_JS}
    for name in NEW_JS:
        imported = set(re.findall(r'from "\./([a-z-]+\.js)"', read(name)))
        assert imported <= allowed, (name, imported - allowed)
    for name in ("panel-text.js", "panel-model.js"):      # the pure modules import nothing but each other: they run under Node
        assert set(re.findall(r'from "([^"]+)"', read(name))) <= {"./panel-text.js"}, name
    assert "vendor" not in read("panel-text.js") + read("panel-model.js")


def test_data_comes_only_from_token_protected_gui_routes_and_is_read_slowly():
    data = read("panel-data.js")
    paths = set(re.findall(r'"(/[a-z]+/[^"]*)"', data))
    assert paths == {"/gui/api/sessions/"}, paths                                         # runs go through runPath()
    assert 'runPath(subject.runId)' in data and 'runPath(subject.runId, "usage")' in data
    for tail in ('"/items"', '"/children"'):
        assert f"sessionPath(id, {tail})" in data
    for text in (data, *(read(name) for name in NEW_JS)):
        assert "/api/v1" not in text and "fetch(" not in text and "EventSource" not in text and "localStorage" not in text
    assert "apiGet(" in data                                                                # the token travels in api.js
    delays = {name: int(value) for name, value in re.findall(r"export const (\w+_MS) = (\d+);", data)}
    assert delays["POLL_MS"] >= 10000 and delays["IDLE_POLL_MS"] >= 30000 and delays["FINISHED_POLL_MS"] >= 60000
    assert "document.hidden" in data and 'getElementById("right-panel")' in data and "aside.hidden" in data    # paused when not seen
    assert "registry" in data and "listeners.size === 0" in data and "owner.stop()" in data   # shared and reference counted
    assert re.search(r"MAX_ITEMS = (\d+)", read("panel-model.js")) and "state.capped" in data


def test_fields_read_by_the_client_exist_in_the_api_answers(world):
    paths = write_session(world.stores)
    session = f"claude:{CLAUDE_ID}"
    summary, children = api_get(world, f"/sessions/{session}"), api_get(world, f"/sessions/{session}/children")
    items = api_get(world, f"/sessions/{session}/items")
    run_id = priced_run(world)
    usage, view = api_get(world, f"/runs/{run_id}/usage"), api_get(world, f"/runs/{run_id}")
    model = read("panel-model.js")
    assert used(model, "summary") <= set(summary)
    assert {"cwd", "tokens", "models", "parent_id", "status"} <= set(summary)
    assert used(model, "child") <= set(children["children"][0])
    tool_call = next(item for item in items["items"] if item["kind"] == "tool_call")
    assert {"kind", "tool", "files", "text"} <= set(tool_call) and {"name", "summary"} <= set(tool_call["tool"])
    assert {"next_cursor", "done", "warnings", "items"} <= set(items)
    totals = usage["totals"]
    assert {"run", "by_model"} <= set(totals) and {"records", "tokens_in", "tokens_out", "records_without_usage", "failed",
                                                    "estimated_cost_usd", "cost_unknown_records", "cost_other_currencies",
                                                    "cost_complete"} <= set(totals["run"])
    assert {"records", "tokens_in", "tokens_out", "records_without_usage", "model"} <= set(totals["by_model"][0])
    assert {"model", "cost_basis"} <= set(usage["records"][0]) and {"input", "output", "currency", "priced_at"} <= set(usage["records"][0]["cost_basis"])
    assert {"amount", "denominator", "unit", "source", "valid_from", "valid_until", "provenance"} <= set(usage["records"][0]["cost_basis"]["input"])
    assert {"artifacts", "events", "events_total", "steps", "inputs", "workflow_id", "bundle", "terminal"} <= set(view)
    assert paths  # the session was really written


def test_the_panel_never_shows_message_text_only_paths_counts_and_names():
    for name in ("panel-changes.js", "panel-sources.js", "panel-tokens.js"):
        text = read(name)
        for field in (r"\.text\b", r"item\.text", r"tool\.summary", "raw_response", "revision_feedback", r"\.prompt\b", r"inputs\[", "Object.values"):
            assert not re.search(field, text), (name, field)
    model = read("panel-model.js")
    assert "item.text" in model and model.count("item.text") == 1                           # only the attachment marker is read
    assert "Object.keys(" in model and "Object.values" not in model and "Object.entries(" in model   # input NAMES, never values


def test_markup_is_accessible_and_keyboard_driven():
    parts, changes, tokens, sources = (read(n) for n in ("panel-parts.js", "panel-changes.js", "panel-tokens.js", "panel-sources.js"))
    for source in (changes, tokens, sources):
        assert 'role="status" aria-live="polite"' in source             # the summary line is announced politely when it changes
    assert 'aria-busy=${data.loading ?' in changes and 'aria-busy=${data.loading ?' in sources
    assert 'role="alert"' in parts and 'role="status" aria-busy="true"' in parts
    assert 'aria-expanded=${expanded ? "true" : "false"}' in parts and "<button" in parts and 'type="button"' in parts
    assert '<span aria-hidden="true">+</span>' in parts and "líneas añadidas" in parts and 'class="sr-only"' in parts   # not colour-only
    assert "<ul class=\"ctx-list\" aria-label=" in changes and "aria-labelledby=" in sources and "aria-label=" in tokens
    assert "<details" in tokens and "<summary>" in tokens                                    # native, keyboard-operable folds
    assert 'title=${file.path}' in changes                                                   # the full path is one hover/focus away
    assert "tabindex" not in parts + changes + tokens + sources                              # nothing is made focusable by hand


def test_stylesheet_has_touch_targets_focus_wrapping_and_no_fixed_layout():
    sheet = css()
    for selector in (".ctx-more", ".ctx-details > summary"):
        body = re.search(rf"{re.escape(selector)} \{{([^}}]*)\}}", sheet)[1]
        assert "min-height: var(--target)" in body, selector                                 # 44 px targets
    assert "outline: 2px solid var(--accent)" in sheet and ":focus-visible" in sheet
    assert "overflow-wrap: anywhere" in sheet and "min-width: 0" in sheet                    # long paths wrap at 375 px
    assert re.search(r"\.ctx-path \{[^}]*flex: 1 1 100%", sheet)                             # the path has a line of its own
    for forbidden in ("position: fixed", "@import", "url(", "style=", "http://", "https://", "!important"):
        assert forbidden not in sheet, forbidden
    assert "var(--ok)" in sheet and "var(--bad)" in sheet
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", sheet)                                      # no colour of its own: dark mode follows app.css


# --- help (GUI-T12 rules; the generic scan is tests/test_gui_help.py) ----------------------------------------------

def test_every_new_control_has_help_and_the_new_topics_exist():
    help_text = read("help-text.js")
    for topic in NEW_TOPICS:
        assert f'"{topic}": {{' in help_text, topic
    helped = "".join(read(name) for name in ("panel-parts.js", "panel-tokens.js", "right-panel.js"))
    for topic in NEW_TOPICS:
        assert f'topic="{topic}"' in helped, topic
    parts = read("panel-parts.js")
    for button in re.findall(r"<button[^>]*>", parts, re.S):
        assert "helpFor(" in button, button
    test_help = (Path(__file__).parent / "test_gui_help.py").read_text(encoding="utf-8")
    for name in ("panel-parts.js", "panel-changes.js", "panel-tokens.js", "panel-sources.js"):
        assert f'"{name}"' in test_help, name                                                # the views are scanned by the help test


def test_texts_are_spanish_and_never_zero_for_unknown():
    text, tokens = read("panel-text.js"), read("panel-tokens.js")
    for label in ("desconocido", "Leyendo la sesión", "no constan", "sin precio", "Costo estimado", "Evidencia de precio",
                  "Orquestador", "Trabajadores"):
        assert label in text + tokens + read("panel-model.js"), label
    assert re.search(r'export const UNKNOWN = "desconocido"', text)


# --- served files ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", NEW_JS)
def test_new_client_files_are_served_with_the_csp_and_no_token(world, name):
    response = world.client.get(f"/gui/static/js/{name}")
    assert response.status_code == 200 and "javascript" in response.headers["content-type"]
    assert response.headers["content-security-policy"] == CSP and response.headers["x-content-type-options"] == "nosniff"


def test_the_stylesheet_and_the_page_are_served_with_the_csp(world):
    sheet = world.client.get("/gui/static/panels.css")
    assert sheet.status_code == 200 and ".ctx-row" in sheet.text and sheet.headers["content-security-policy"] == CSP
    page = world.client.get("/gui/")
    assert "/gui/static/panels.css" in page.text and page.headers["content-security-policy"] == CSP
