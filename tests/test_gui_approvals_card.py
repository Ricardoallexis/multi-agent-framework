# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""SEC-T04: Pure Node tests and static checks for the «Aprobaciones» extension and New Session secret-review resend."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.gui_text import ui_text
from multiagent.gui import STATIC_DIR
from test_gui_extensions import check_extension_code, check_extension_styles
from test_gui_help import check_extensions

EXT_DIR = STATIC_DIR / "extensions" / "approvals"
JS_DIR = STATIC_DIR / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_node(script: str, data: dict | None = None) -> dict:
    """Run an ES module script in Node with network calls blocked."""
    prelude = (
        f'import * as approvals from {json.dumps((EXT_DIR / "model.js").as_uri())};\n'
        f'import * as ns from {json.dumps((JS_DIR / "new-session-model.js").as_uri())};\n'
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
def test_cards_cover_every_kind_destination_and_location():
    out = run_node("""
      const items = [
        {
          id: "apr-1", kind: "secret_send",
          destination: { provider: "openai", model: "gpt-4o", tier: "cloud" },
          location: { run: "run-42", step: "draft" },
          secret_types: ["private_key", "long_token"],
          masked_sample: "-----BEGIN…KEY-----",
          created_at: "2026-11-20T12:00:00Z",
          sha256: "a".repeat(64),
        },
        {
          id: "apr-2", kind: "osv_query",
          destination: "api.osv.dev",
          location: { bundle: "ext_bundle" },
          secret_types: [],
          masked_sample: "requests==2.32.0 (*)",
          created_at: "2026-11-20T12:01:00Z",
          sha256: "b".repeat(64),
        },
        {
          id: "apr-3", kind: "bundle_load",
          destination: { executor: "claude_cli", model: "sonnet" },
          location: { surface: "new_session" },
          secret_types: ["bearer"],
          masked_sample: "Bearer ab…yz",
          created_at: "2026-11-20T12:02:00Z",
          sha256: "c".repeat(64),
        },
        {
          id: "apr-4", kind: "secret_send",
          destination: "codex_cli",
          location: { plan: "plan-7", task: "task-9" },
          secret_types: ["secret_key"],
          masked_sample: "sk-12…89ab",
          created_at: "2026-11-20T12:03:00Z",
          sha256: "d".repeat(64),
        },
      ];
      console.log(JSON.stringify({
        cards: items.map(approvals.describeApproval),
        ordered: approvals.orderApprovals(items, "apr-3").map((x) => x.id),
        hasHit: approvals.hasSelectedApproval(items, "apr-3"),
        hasMiss: approvals.hasSelectedApproval(items, "apr-missing"),
      }));
    """)
    cards = out["cards"]
    assert cards[0]["title"] == "Envío con forma de secreto"
    assert cards[0]["destination"] == "openai · gpt-4o · cloud"
    assert cards[0]["location"] == "Ejecución run-42 · paso draft"
    assert cards[0]["types"] == ["Clave privada", "Token largo"]
    assert cards[0]["approveLabel"] == "Aprobar este envío" and cards[0]["rejectLabel"] == "Rechazar"

    assert cards[1]["title"] == "Consulta a OSV.dev"
    assert cards[1]["destination"] == "api.osv.dev"
    assert cards[1]["location"] == "Bundle ext_bundle"
    assert cards[1]["types"] == ["Consulta a OSV.dev"]

    assert cards[2]["title"] == "Carga de bundle externo"
    assert cards[2]["destination"] == "claude_cli · sonnet"
    assert cards[2]["location"] == "Nueva sesión"
    assert cards[2]["types"] == ["Token Bearer"]

    assert cards[3]["location"] == "Tarea task-9 · Plan plan-7"
    assert cards[3]["types"] == ["Clave secreta"]
    assert out["ordered"] == ["apr-3", "apr-1", "apr-2", "apr-4"]
    assert out["hasHit"] is True and out["hasMiss"] is False


@needs_node
def test_masked_sample_never_exposes_a_raw_secret():
    raw_secret = "sk-synthetic-SECRET-VALUE-1234567890abcdef"
    out = run_node("""
      const masked = approvals.safeMaskedSample("sk-sy…cdef");
      const unmasked = approvals.safeMaskedSample(DATA.raw);
      const longMasked = approvals.safeMaskedSample("sk-" + "x".repeat(200) + "…yz");
      const empty = approvals.safeMaskedSample("");
      const card = approvals.describeApproval({ id: "a1", kind: "secret_send", masked_sample: DATA.raw });
      console.log(JSON.stringify({ masked, unmasked, longMaskedLen: longMasked.length, empty, cardSample: card.maskedSample }));
    """, {"raw": raw_secret})
    assert out["masked"] == "sk-sy…cdef"
    assert raw_secret not in out["unmasked"] and raw_secret not in out["cardSample"]
    assert "sin enmascarar" in out["unmasked"]
    assert out["longMaskedLen"] <= 160
    assert out["empty"] == "Sin muestra"


@needs_node
def test_approve_runs_challenge_then_decision_without_network():
    out = run_node("""
      const calls = [];
      const item = { id: "apr-10", sha256: "e".repeat(64) };
      const post = async (path, body) => {
        calls.push({ path, body });
        if (path.endsWith("/challenge")) return { code: "one-time-42", expires_in: 120, sha256: "e".repeat(64) };
        return { id: "apr-10", state: body.decision === "approve" ? "approved" : "rejected" };
      };
      const approved = await approvals.submitApprovalDecision({ item, decision: "approve", post });
      const rejected = await approvals.submitApprovalDecision({ item, decision: "reject", post });
      console.log(JSON.stringify({ calls, approved, rejected }));
    """)
    assert out["approved"] == {"ok": True, "result": {"id": "apr-10", "state": "approved"}}
    assert out["rejected"] == {"ok": True, "result": {"id": "apr-10", "state": "rejected"}}
    assert out["calls"] == [
        {"path": "/gui/api/approvals/apr-10/challenge", "body": {}},
        {"path": "/gui/api/approvals/apr-10/decision", "body": {"code": "one-time-42", "decision": "approve"}},
        {"path": "/gui/api/approvals/apr-10/challenge", "body": {}},
        {"path": "/gui/api/approvals/apr-10/decision", "body": {"code": "one-time-42", "decision": "reject"}},
    ]


@needs_node
def test_expired_challenge_is_retryable_and_second_attempt_succeeds():
    out = run_node("""
      let attempt = 0;
      const item = { id: "apr-exp", sha256: "f".repeat(64) };
      const post = async (path, body) => {
        if (path.endsWith("/challenge")) {
          attempt += 1;
          return { code: "code-" + attempt, expires_in: 120, sha256: "f".repeat(64) };
        }
        if (attempt === 1) {
          throw { status: 409, code: "invalid_state", details: { reason: "user_click_required", cause: "invalid_or_expired_code" } };
        }
        return { id: "apr-exp", state: "approved", codeUsed: body.code };
      };
      const first = await approvals.submitApprovalDecision({ item, decision: "approve", post });
      const second = await approvals.submitApprovalDecision({ item, decision: "approve", post });
      console.log(JSON.stringify({ first, second }));
    """)
    assert out["first"]["ok"] is False
    assert out["first"]["retryable"] is True
    assert out["first"]["sessionUnavailable"] is False
    assert "venció" in out["first"]["message"]
    assert out["second"] == {"ok": True, "result": {"id": "apr-exp", "state": "approved", "codeUsed": "code-2"}}


@needs_node
def test_session_unavailable_shows_gui_restart_notice():
    out = run_node("""
      const item = { id: "apr-nosess", sha256: "1".repeat(64) };
      const byCause = await approvals.submitApprovalDecision({
        item,
        decision: "approve",
        post: async () => {
          throw { status: 409, code: "invalid_state", details: { reason: "user_click_required", cause: "missing_or_invalid_session" } };
        },
      });
      const byReason = approvals.approvalErrorInfo({
        status: 409,
        code: "invalid_state",
        details: { reason: "approval_session_unavailable" },
      });
      console.log(JSON.stringify({ byCause, byReason }));
    """)
    for res in (out["byCause"], out["byReason"]):
        assert res["sessionUnavailable"] is True
        assert res["retryable"] is False
        assert "reinicia la GUI" in res["message"]
        assert "multiagent gui" in res["message"]


@needs_node
def test_no_approve_all_action_and_new_session_secret_review_resend():
    out = run_node("""
      const exported = Object.keys(approvals);
      const heldResp = { source: "keywords", reason: "secret_review_required", proposal: null, approval_id: "apr-ns-1" };
      const isHeld = ns.isSecretReviewRequired(heldResp);
      const notHeld = ns.isSecretReviewRequired({ source: "keywords", reason: "no_match", proposal: null });
      const stateBefore = ns.createConversation();
      const held = ns.createSecretReview({
        text: "/workflow resume con sk-test-1234",
        requestText: "resume con sk-test-1234",
        confirmCloud: true,
        approvalId: heldResp.approval_id,
      });
      const resend = ns.secretResendRequest(held);
      const firstBody = ns.interpretBody(resend.text, { confirmCloud: resend.confirmCloud });
      const resendBody = ns.interpretBody(resend.text, { confirmCloud: resend.confirmCloud, approvalId: resend.approvalId });
      const catalog = [{ id: "sum", bundle: null, description: "resume documento" }];
      const sentOnce = ns.reduce(stateBefore, {
        type: "send",
        text: held.text,
        catalog,
        interpretation: { source: "keywords", reason: "no_match", proposal: null },
      });
      console.log(JSON.stringify({
        exported, isHeld, notHeld, stateBeforeMessages: stateBefore.messages.length,
        held, resend, firstBody, resendBody,
        userMessagesCount: sentOnce.messages.filter((m) => m.role === "user").length,
        userMessageText: sentOnce.messages[0].text,
        notice: ns.SECRET_REVIEW_NOTICE,
      }));
    """)
    assert not any("approveall" in name.lower() or "approve_all" in name.lower() or "batch" in name.lower()
                   for name in out["exported"])
    assert out["isHeld"] is True and out["notHeld"] is False
    assert out["stateBeforeMessages"] == 0
    assert out["held"]["text"] == "/workflow resume con sk-test-1234"
    assert out["firstBody"] == {"text": "resume con sk-test-1234", "confirm_cloud": True}
    assert out["resendBody"] == {"text": "resume con sk-test-1234", "confirm_cloud": True, "approval_id": "apr-ns-1"}
    assert out["userMessagesCount"] == 1
    assert out["userMessageText"] == "/workflow resume con sk-test-1234"
    assert "Aprobaciones" in out["notice"]


def test_extension_static_rules_single_title_and_no_shortcuts():
    index_src = ui_text((EXT_DIR / "index.js"))
    model_src = ui_text((EXT_DIR / "model.js"))
    ns_src = ui_text((JS_DIR / "new-session.js"))
    ns_model_src = ui_text((JS_DIR / "new-session-model.js"))
    assert not re.search(r"<h[123]\b", index_src)
    assert 'topic: "approvals.view"' in index_src and 'owner: "approvals"' in index_src
    assert 'topic="approvals.view"' not in index_src
    assert "aprobar todos" not in (index_src + model_src).lower()
    assert "recordar" not in index_src.lower()
    assert "keydown" not in index_src.lower() and "keypress" not in index_src.lower()
    assert "fetch(" not in model_src
    assert "SECRET_REVIEW_REASON" in ns_src and "isSecretReviewRequired" in ns_src
    assert "secret_review_required" in ns_model_src and "approval_id" in ns_model_src
    assert "Este mensaje lleva algo con forma de secreto" in ns_model_src
    check_extension_code(STATIC_DIR)
    check_extension_styles(STATIC_DIR)
    check_extensions(STATIC_DIR)


@needs_node
def test_all_touched_scripts_pass_node_check():
    for path in (
        EXT_DIR / "index.js",
        EXT_DIR / "model.js",
        EXT_DIR / "help.js",
        JS_DIR / "new-session-model.js",
        JS_DIR / "new-session.js",
    ):
        run = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True, timeout=60)
        assert run.returncode == 0, (path.name, run.stderr)


# MEM-T01: the two memory approvals show the entry title as text and only offer «Aprobar» and «Rechazar»
@needs_node
def test_memory_cards_show_the_entry_title_as_text_and_the_plain_words():
    out = run_node("""
      const hostile = "<img src=x onerror=alert(1)>";
      const trust = approvals.describeApproval({ id: "t", kind: "memory_trust", destination: { memory: "mem-1", action: "trust" },
        location: { source: "memory", entry: "mem-1", title: hostile }, sha256: "a".repeat(64) });
      const del = approvals.describeApproval({ id: "d", kind: "memory_delete", location: { title: "  " } });
      const plain = approvals.describeApproval({ id: "s", kind: "secret_send" });
      console.log(JSON.stringify({ trust, del, plain: [plain.memory, plain.entryTitle] }));
    """)
    trust, delete = out["trust"], out["del"]
    assert trust["memory"] is True and trust["entryTitle"] == "<img src=x onerror=alert(1)>"
    assert "apunte del proyecto" in trust["title"] and "fiable" in trust["reason"] and "apunte" in trust["reason"]
    assert (trust["approveLabel"], trust["rejectLabel"]) == ("Aprobar", "Rechazar")
    assert "apunte" in delete["reason"] and "borra" in delete["reason"] and delete["entryTitle"] == "Apunte sin título"
    assert out["plain"] == [False, ""]
    index_src = ui_text((EXT_DIR / "index.js"))
    help_src = ui_text((EXT_DIR / "help.js"))
    for kind, words in (("memory_trust", "apunte"), ("memory_delete", "El apunte se borra")):
        topic = help_src[help_src.index(f'"approvals.{kind}"'):]
        topic = topic[:topic.index("},")]
        assert words in topic and "envío" not in topic and "nota" not in topic.lower()
    assert 'helpFor("approvals.memory_trust"' in index_src and 'helpFor("approvals.memory_delete"' in index_src
    assert index_src.count('helpFor("approvals.approve"') == 1 and "!view.memory &&" in index_src
    assert "view.entryTitle" in index_src and "innerHTML" not in index_src and "style=" not in index_src


# MEM-T02: a memory_trust that clashes with an entry the person confirmed shows both texts, as text, and says what Aprobar does
@needs_node
def test_a_clashing_memory_card_shows_the_pair_as_text_and_the_plain_words():
    out = run_node("""
      const hostile = "<img src=x onerror=alert(1)>";
      const base = { id: "t", kind: "memory_trust", destination: { memory: "mem-1", action: "trust" },
        location: { source: "memory", entry: "mem-1", title: "Plazo", conflicts_with: "mem-2" }, sha256: "a".repeat(64) };
      const clash = approvals.describeApproval({ ...base, conflict: {
        new: { id: "mem-1", title: hostile, body: "x".repeat(900) }, existing: { id: "mem-2", title: "Plazo", body: "31 de marzo" } } });
      const plain = approvals.describeApproval(base);
      const broken = approvals.describeApproval({ ...base, conflict: { new: "x", existing: null } });
      const foreign = approvals.describeApproval({ id: "s", kind: "secret_send", conflict: { new: {}, existing: {} } });
      console.log(JSON.stringify({ clash, plain: plain.conflict, broken: broken.conflict, foreign: foreign.conflict }));
    """)
    clash = out["clash"]
    assert clash["conflict"]["new"]["title"] == "<img src=x onerror=alert(1)>"
    assert len(clash["conflict"]["new"]["body"]) == 240 and clash["conflict"]["new"]["body"].endswith("…")
    assert clash["conflict"]["existing"] == {"title": "Plazo", "body": "31 de marzo"}
    for word in ("contradice", "apunte", "fiable", "Pendientes", "borres"):
        assert word in clash["reason"]
    assert out["plain"] is None and out["broken"] is None and out["foreign"] is None
    index_src = ui_text((EXT_DIR / "index.js"))
    help_src = ui_text((EXT_DIR / "help.js"))
    topic = help_src[help_src.index('"approvals.memory_clash"'):]
    topic = topic[:topic.index("},")]
    for words in ("El apunte nuevo pasa a fiable", "Pendientes", "Nada se borra con este clic"):
        assert words in topic
    assert 'helpFor("approvals.memory_clash"' in index_src and "view.conflict.new.title" in index_src
    assert "view.conflict.existing.body" in index_src and "innerHTML" not in index_src and "style=" not in index_src
