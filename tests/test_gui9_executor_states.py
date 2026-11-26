# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Executor explanations and paused cards, without a browser, network or real CLI."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def node(source: str):
    script = f"""
Object.defineProperty(globalThis, "navigator", {{value: {{language: "es-MX"}}, configurable: true}});
const text = await import({json.dumps((STATIC_DIR / 'js' / 'text.js').as_uri())});
const activation = await import({json.dumps((STATIC_DIR / 'extensions' / 'apps' / 'activation-model.js').as_uri())});
{source}
"""
    result = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                            encoding="utf-8", cwd=STATIC_DIR, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@needs_node
def test_errors_use_stable_codes_and_reason_in_spanish():
    result = node("""
const err = (code, details) => text.errorText({code, details, message: "The raw server error"});
console.log(JSON.stringify({
  missing: err("invalid_state", {reason: "executor_unavailable", code: "executable_not_found", executor_id: "codex_cli"}),
  unavailable: err("invalid_state", {reason: "executor_unavailable", executor_id: "claude_cli"}),
  changed: err("invalid_state", {reason: "executor_paused_changed", changes: [{field: "path"}, {field: "account"}]}),
  limits: ["rate_limited", "plan_limit", "credits_exhausted", "limit_unknown", "quota"].map((failure_kind) => err("executor_failure", {failure_kind})),
  other: err("executor_failure", {failure_kind: "other"}),
  permission: err("executor_failure", {failure_kind: "permission_denied"}),
  invalid: err("invalid_state", {}),
  blocked: err("executor_failure", {failure_kind: "blocked_open_session"}),
  auth: err("executor_failure", {failure_kind: "authentication"}),
  stopped: err("invalid_state", {reason: "executor_stopped_by_person"}),
  unknown: err("opaque_code", {}),
}));
""")
    assert result["missing"] == ("No se pudo iniciar el ejecutor: no se encontró Codex. Comprueba que su CLI "
                                 "esté instalado y disponible desde la consola del sistema.")
    assert "Claude no está disponible" in result["unavailable"]
    assert "ruta de la aplicación, cuenta" in result["changed"]
    assert "Revísalo en Apps y agentes" in result["changed"]
    assert all(value == "Se alcanzó el límite de uso." for value in result["limits"])
    assert result["other"] == "La ejecución falló."
    assert result["permission"] == "Permiso denegado."
    assert result["invalid"] == "Esta acción ya no está disponible."
    assert result["blocked"] == "La sesión ya está abierta."
    assert result["auth"] == "Falló la autenticación."
    assert result["stopped"] == "El ejecutor está en pausa porque lo apagaste. Revísalo en Apps y agentes."
    assert result["unknown"] == "No se pudo completar la acción (opaque_code)."
    assert "raw server" not in json.dumps(result)


@needs_node
def test_paused_card_lists_changes_and_offers_review():
    result = node("""
const view = activation.activationView({id: "codex_cli"}, {state: "paused", paused: {reason: "changed",
  changes: [{field: "major", from: "2", to: "3"}, {field: "account", from: "one", to: "two"}]}});
console.log(JSON.stringify(view));
""")
    assert result["label"] == "En pausa" and result["actions"] == ["review"]
    assert "Cambió la versión 2 → 3" in result["detail"] and "Cambió la cuenta" in result["detail"]
    assert "Revísalo en Apps y agentes" in result["detail"]


def test_tracking_puts_explanation_before_collapsed_diagnostics():
    source = (STATIC_DIR / "js" / "new-session.js").read_text(encoding="utf-8")
    component = source.split("function SingleExecutorSession(", 1)[1].split("export function NewSession(", 1)[0]
    assert component.index("${failureText}") < component.index("${displayExecutorEvidence(")
    assert 't("new_session.technical_details")' in component


def render_executor_session(tmp_path, outcome, failure_kind):
    """Calls SingleExecutorSession with seeded state, without a DOM: hooks are stubs, htm builds plain vnodes."""
    js = STATIC_DIR / "js"
    source = (js / "new-session.js").read_text(encoding="utf-8")
    source = re.sub(r'from "\./([^"]+)"', lambda m: 'from ' + json.dumps((js / m.group(1)).as_uri()), source)
    source = source.replace('from "../vendor/htm-preact-standalone.module.js"',
                            "from " + json.dumps((tmp_path / "shim.mjs").as_uri()))
    (tmp_path / "ns.mjs").write_text(source + "\nexport { SingleExecutorSession };\n", encoding="utf-8")
    shim = f"""
export {{ html }} from {json.dumps((STATIC_DIR / 'vendor' / 'htm-preact-standalone.module.js').as_uri())};
let call = 0;
export const useState = (initial) => {{ const seeded = globalThis.__states[call++]; return [seeded === undefined ? initial : seeded, () => {{}}]; }};
export const useEffect = () => {{}};
export const useRef = (initial) => ({{ current: initial }});
"""
    (tmp_path / "shim.mjs").write_text(shim, encoding="utf-8")
    script = f"""
Object.defineProperty(globalThis, "navigator", {{value: {{language: "es-MX"}}, configurable: true}});
const attempt = {{outcome: {json.dumps(outcome)}, failure_kind: {json.dumps(failure_kind)},
  evidence: "stderr: raw", notes: ["failure:other"]}};
globalThis.__states = {{8: {{task_id: "t1", launch: {{outcome: {json.dumps(outcome)}}}}},
  9: {{data: {{task: {{state: "paused", status: "paused", attempts: [attempt], sessions: []}}}}, error: null}}}};
const mod = await import({json.dumps((tmp_path / 'ns.mjs').as_uri())});
const tree = mod.SingleExecutorSession({{token: "t", mock: false, connections: {{connections: []}},
  task: "do it", onTaskChange() {{}}, projects: [], onProject() {{}}, onMode() {{}}, onForm() {{}}, onBack() {{}}}});
const seen = [];
const walk = (node) => {{
  if (Array.isArray(node)) return node.forEach(walk);
  if (node === null || node === undefined || typeof node !== "object") {{
    if (typeof node === "string" && node.trim()) seen.push(node.trim());
    return;
  }}
  if (node.type === "details") seen.push("<details>");
  walk(node.props && node.props.children);
}};
walk(tree);
console.log(JSON.stringify(seen));
"""
    result = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                            encoding="utf-8", cwd=tmp_path, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@needs_node
@pytest.mark.parametrize("outcome,failure_kind,message", [
    ("blocked_open_session", "blocked_open_session", "La sesión ya está abierta."),
    ("failed", "other", "La ejecución falló."),
    ("timeout", None, "La ejecución falló."),
])
def test_session_card_shows_explanation_above_details(tmp_path, outcome, failure_kind, message):
    seen = render_executor_session(tmp_path, outcome, failure_kind)
    assert message in seen
    assert seen.index(message) < seen.index("<details>")
