# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""SKL-T03: Pure Node tests and static checks for bundle_scan approval cards in «Aprobaciones»."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR
from test_gui_extensions import check_extension_code, check_extension_styles
from test_gui_help import check_extensions

EXT_DIR = STATIC_DIR / "extensions" / "approvals"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_node(script: str, data: dict | None = None) -> dict:
    """Run an ES module script in Node with network calls blocked."""
    prelude = (
        f'import * as approvals from {json.dumps((EXT_DIR / "model.js").as_uri())};\n'
        f'import * as bundleScan from {json.dumps((EXT_DIR / "bundle-scan.js").as_uri())};\n'
        "const DATA = JSON.parse(process.env.DATA || 'null');\n"
        "globalThis.fetch = () => { throw new Error('network calls are forbidden in pure model tests'); };\n"
    )
    done = subprocess.run(
        [NODE, "--input-type=module", "-e", prelude + script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        env={**os.environ, "DATA": json.dumps(data)},
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@needs_node
def test_osv_query_and_bundle_load_cards():
    out = run_node("""
      const osvItem = {
        id: "apr-osv-1",
        kind: "osv_query",
        destination: { bundle: "team_ext", kind: "osv_query" },
        location: "api.osv.dev receives package names and versions from: requirements.txt, pyproject.toml",
        sha256: "a".repeat(64),
        created_at: "2026-11-20T12:00:00Z",
      };
      const osvEmpty = {
        id: "apr-osv-2",
        kind: "osv_query",
        destination: { bundle: "team_empty", kind: "osv_query" },
        location: "api.osv.dev receives package names and versions from: no manifests found",
        sha256: "b".repeat(64),
      };
      const loadItem = {
        id: "apr-load-1",
        kind: "bundle_load",
        destination: { bundle: "team_ext", kind: "bundle_load" },
        location: { bundle: "team_ext", fingerprint: "a".repeat(64) },
        sha256: "a".repeat(64),
        created_at: "2026-11-20T12:05:00Z",
      };
      const scan = {
        bundle: "team_ext",
        external: true,
        state: "do_not_install",
        verdict: "do_not_install",
        coverage: { "skills/review": 100 },
        findings: [{ level: "high", rule: "exec_shell", file: "SKILL.md", line: 12, message: "Shell command found" }],
        reason: null,
      };
      console.log(JSON.stringify({
        isOsv: bundleScan.isBundleScanApproval(osvItem),
        isLoad: bundleScan.isBundleScanApproval(loadItem),
        isSecret: bundleScan.isBundleScanApproval({ id: "s1", kind: "secret_send" }),
        scanPath: bundleScan.scanStatusPath("team ext"),
        osvCard: bundleScan.describeOsvQuery(osvItem),
        osvEmptyCard: bundleScan.describeOsvQuery(osvEmpty),
        loadCard: bundleScan.describeBundleLoad(loadItem, scan),
        scanningNotice: bundleScan.SCANNING_NOTICE,
        pollMs: bundleScan.SCAN_POLL_MS,
        isRunning: bundleScan.isScanningState({ state: "running" }),
        shouldPoll: bundleScan.shouldPollScans([{ state: "safe" }, { state: "running" }]),
      }));
    """)
    assert out["isOsv"] is True and out["isLoad"] is True and out["isSecret"] is False
    assert out["scanPath"] == "/gui/api/bundles/team%20ext/scan"

    osv = out["osvCard"]
    assert osv["bundle"] == "team_ext"
    assert osv["destinationHost"] == "api.osv.dev"
    assert osv["manifests"] == ["requirements.txt", "pyproject.toml"]
    assert "requirements.txt" in osv["outboundSummary"] and "paquetes" in osv["outboundSummary"]
    assert osv["approveLabel"] == "Permitir la consulta"
    assert osv["rejectLabel"] == "No permitir"
    assert "sin analizar" in osv["rejectionConsequence"].lower()

    assert out["osvEmptyCard"]["manifests"] == []
    assert "no se encontraron manifiestos" in out["osvEmptyCard"]["manifestsText"].lower()

    load = out["loadCard"]
    assert load["bundle"] == "team_ext"
    assert load["verdict"]["text"] == "No instalar"
    assert load["approveLabel"] == "Cargar este bundle"
    assert load["rejectLabel"] == "No cargar"
    assert load["note"] == (
        "Sin LLM, SkillSpector busca palabras y patrones: muchos hallazgos altos son falsos positivos. "
        "Revisa los principales antes de decidir"
    )
    assert load["findings"]["items"][0]["fileText"] == "SKILL.md:12"

    assert out["scanningNotice"] == "Escaneando · unos 25 s por carpeta"
    assert 1000 <= out["pollMs"] <= 5000
    assert out["isRunning"] is True and out["shouldPoll"] is True


@needs_node
def test_every_verdict_label_and_coverage_with_zero_percent():
    out = run_node("""
      const verdicts = {
        safe: bundleScan.formatVerdict({ state: "safe", verdict: "safe", coverage: { ".": 100 } }),
        caution: bundleScan.formatVerdict({ state: "caution", verdict: "caution", coverage: { ".": 100 } }),
        do_not_install: bundleScan.formatVerdict({ state: "do_not_install", verdict: "do_not_install", coverage: { ".": 100 } }),
        not_analyzed: bundleScan.formatVerdict({ state: "not_analyzed", verdict: null, coverage: {} }),
        unscanned: bundleScan.formatVerdict({ state: "unscanned", verdict: null, coverage: {} }),
        partialOverride: bundleScan.formatVerdict({ state: "safe", verdict: "safe", coverage: { a: 100, b: 0 } }),
      };
      const cov = bundleScan.formatCoverage({ "skills/a": 100, "skills/b": 0, "skills/c": -1 });
      console.log(JSON.stringify({ verdicts, cov }));
    """)
    assert out["verdicts"]["safe"]["text"] == "Seguro"
    assert out["verdicts"]["caution"]["text"] == "Con avisos"
    assert out["verdicts"]["do_not_install"]["text"] == "No instalar"
    assert out["verdicts"]["not_analyzed"]["text"] == "Sin analizar"
    assert out["verdicts"]["unscanned"]["text"] == "Sin analizar"
    assert out["verdicts"]["partialOverride"]["text"] == "Sin analizar"

    entries = {entry["folder"]: entry for entry in out["cov"]["entries"]}
    assert entries["skills/a"]["unanalyzed"] is False and entries["skills/a"]["text"] == "100 %"
    assert entries["skills/b"]["unanalyzed"] is True and entries["skills/b"]["text"] == "0 % · sin analizar"
    assert entries["skills/c"]["unanalyzed"] is True and entries["skills/c"]["text"] == "sin analizar"
    assert out["cov"]["allComplete"] is False


@needs_node
def test_top_ten_findings_limit_and_plain_text_script_safety():
    out = run_node("""
      const list = [];
      for (let i = 0; i < 12; i += 1) {
        list.push({ level: "low", rule: "low_" + i, file: "a.py", line: i + 1, message: "Low item " + i });
      }
      list.push({ level: "critical", rule: "<script>alert(1)</script>", file: "bad/file.py", line: 7, message: "<script>fetch('x')</script>\\nsecond line" });
      list.push({ level: "high", rule: "high_rule", file: "b.py", line: 2, message: "High item" });
      list.push({ level: "info", rule: "info_rule", file: "c.py", line: 9, message: "Info item" });
      const top = bundleScan.selectTopFindings(list);
      const card = bundleScan.describeBundleLoad(
        { id: "apr-xss", kind: "bundle_load", destination: { bundle: "b1" } },
        { bundle: "b1", state: "do_not_install", verdict: "do_not_install", coverage: { ".": 100 }, findings: list },
      );
      console.log(JSON.stringify({ top, cardFindings: card.findings }));
    """)
    top = out["top"]
    assert len(top["items"]) == 10
    assert top["total"] == 15 and top["hidden"] == 5
    first = top["items"][0]
    assert first["level"] == "Crítico"
    assert first["rule"] == "<script>alert(1)</script>"
    assert first["fileText"] == "bad/file.py:7"
    assert first["summary"] == "<script>fetch('x')</script> second line"
    assert top["items"][1]["level"] == "Alto"
    assert len(out["cardFindings"]["items"]) == 10


@needs_node
def test_osv_reject_leads_to_not_analyzed_without_clicking_approve():
    out = run_node("""
      const calls = [];
      const item = {
        id: "apr-osv-reject",
        kind: "osv_query",
        destination: { bundle: "ext_one", kind: "osv_query" },
        location: "api.osv.dev receives package names and versions from: requirements.txt",
        sha256: "c".repeat(64),
      };
      const post = async (path, body) => {
        calls.push({ path, body });
        if (path.endsWith("/challenge")) return { code: "once-99", expires_in: 120, sha256: "c".repeat(64) };
        return { id: item.id, state: body.decision === "reject" ? "rejected" : "approved" };
      };
      const rejected = await approvals.submitApprovalDecision({ item, decision: "reject", post });
      const resultingCard = bundleScan.describeBundleLoad(
        { id: "apr-load-after", kind: "bundle_load", destination: { bundle: "ext_one" } },
        { bundle: "ext_one", state: "not_analyzed", verdict: null, reason: "osv_rejected", coverage: {}, findings: [] },
      );
      console.log(JSON.stringify({ calls, rejected, resultingCard }));
    """)
    assert out["rejected"] == {"ok": True, "result": {"id": "apr-osv-reject", "state": "rejected"}}
    assert out["calls"] == [
        {"path": "/gui/api/approvals/apr-osv-reject/challenge", "body": {}},
        {"path": "/gui/api/approvals/apr-osv-reject/decision", "body": {"code": "once-99", "decision": "reject"}},
    ]
    assert out["resultingCard"]["verdict"]["text"] == "Sin analizar"
    assert "api.osv.dev" in out["resultingCard"]["reasonText"]


def test_bundle_scan_extension_static_rules():
    bundle_scan_src = (EXT_DIR / "bundle-scan.js").read_text(encoding="utf-8")
    index_src = (EXT_DIR / "index.js").read_text(encoding="utf-8")
    assert not re.search(r"<h[123]\b", bundle_scan_src)
    assert not re.search(r"<h[123]\b", index_src)
    assert "fetch(" not in bundle_scan_src
    assert "sessionStorage" not in bundle_scan_src
    assert "aprobar todos" not in bundle_scan_src.lower()
    assert "recordar" not in bundle_scan_src.lower()
    assert "keydown" not in bundle_scan_src.lower() and "keypress" not in bundle_scan_src.lower()
    # htm/Preact renders string interpolations as text nodes (never innerHTML / __html).
    check_extension_code(STATIC_DIR)
    check_extension_styles(STATIC_DIR)
    check_extensions(STATIC_DIR)


@needs_node
def test_bundle_scan_scripts_pass_node_check():
    for path in (
        EXT_DIR / "bundle-scan.js",
        EXT_DIR / "index.js",
        EXT_DIR / "help.js",
        EXT_DIR / "model.js",
    ):
        run = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True, timeout=60)
        assert run.returncode == 0, (path.name, run.stderr)
