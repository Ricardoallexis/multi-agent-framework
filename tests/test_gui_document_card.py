# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""ARR3-T06: the result document card of the run thread. The pure model and the card body run under Node."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "multiagent" / "gui" / "static" / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_node(script: str, data: dict | None = None):
    prelude = (f"import * as model from {json.dumps((JS / 'document-model.js').as_uri())};\n"
               f"import * as card from {json.dumps((JS / 'document-card.js').as_uri())};\n"
               "const DATA = JSON.parse(process.env.DATA || 'null');\n"
               "globalThis.fetch = () => { throw new Error('the card used the network'); };\n"
               "const walk = (n, out = []) => { if (n && typeof n === 'object') { if (Array.isArray(n)) n.forEach((c) => walk(c, out));"
               " else { out.push(n); walk(n.props && n.props.children, out); } } return out; };\n")
    done = subprocess.run([NODE, "--input-type=module", "-e", prelude + script], capture_output=True, text=True,
                          encoding="utf-8", timeout=60, env={**os.environ, "DATA": json.dumps(data)})
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@needs_node
def test_card_shows_pre_text():
    hostile = "# Título\n\n&lt;script&gt;alert(1)&lt;/script&gt; <b>x</b>"
    result = run_node("""
      const view = card.DocumentView({payload: {markdown: DATA}, onSave() {}});
      const pre = walk(view).filter((n) => n.type === 'pre');
      console.log(JSON.stringify({pres: pre.length, text: pre[0].props.children, cls: pre[0].props.class,
        html: walk(view).some((n) => n.props && ('dangerouslySetInnerHTML' in n.props))}));
    """, hostile)
    assert result == {"pres": 1, "text": hostile, "cls": "document-text", "html": False}
    for name in ("document-card.js", "document-model.js"):
        assert not re.search(r"innerHTML|dangerouslySetInnerHTML|insertAdjacentHTML|outerHTML", (JS / name).read_text(encoding="utf-8"))


@needs_node
def test_save_uses_blob():
    result = run_node("""
      const calls = []; let link;
      const doc = {createElement: (tag) => (link = {tag, click() { calls.push('click'); }}),
                   body: {appendChild: () => calls.push('append'), removeChild: () => calls.push('remove')}};
      let blob;
      const urls = {createObjectURL: (b) => { blob = b; calls.push('create'); return 'blob:fake'; },
                    revokeObjectURL: (u) => calls.push('revoke ' + u)};
      model.downloadMarkdown({markdown: '# Hola ñ', filename: 'resumen-3f2a9c1b.md'}, doc, urls);
      const made = model.markdownBlob('# Hola ñ');
      console.log(JSON.stringify({label: model.SAVE_LABEL, type: blob.type, size: blob.size, same: made.size === blob.size,
        text: await blob.text(), tag: link.tag, href: link.href, download: link.download, calls,
        fallback: model.documentFilename({}), fallbackNull: model.documentFilename(null)}));
    """)
    assert result["label"] == "Guardar como Markdown"
    assert result["type"] == "text/markdown;charset=utf-8" and result["text"] == "# Hola ñ"
    assert result["tag"] == "a" and result["href"] == "blob:fake" and result["download"] == "resumen-3f2a9c1b.md"
    assert result["calls"] == ["create", "append", "click", "remove", "revoke blob:fake"]
    assert result["fallback"] == "documento.md" == result["fallbackNull"]


@needs_node
def test_save_button_calls_the_handler_with_the_label():
    result = run_node("""
      let saved = 0;
      const view = card.DocumentView({payload: {markdown: '# T'}, onSave() { saved += 1; }});
      const button = walk(view).find((n) => n.type === 'button');
      button.props.onClick();
      console.log(JSON.stringify({label: button.props.children, saved, describedby: button.props['aria-describedby']}));
    """)
    assert result == {"label": "Guardar como Markdown", "saved": 1, "describedby": "help-thread-document_save-hint".replace("_", "-")}


@needs_node
def test_no_card_without_document():
    result = run_node("""
      const fail = (status, reason) => ({status, details: {reason}});
      console.log(JSON.stringify({
        noResult: model.documentState(null, fail(409, 'no_result')),
        notCompleted: model.documentState(null, fail(409, 'run_not_completed')),
        other409: model.documentState(null, fail(409, 'something_else')),
        server: model.documentState(null, fail(500, undefined)),
        network: model.documentState(null, {status: 0, details: {}}),
        ready: model.documentState({markdown: '# T'}, null),
        empty: model.documentState({markdown: ''}, null),
        missing: model.documentState(null, null)}));
    """)
    assert result == {"noResult": "none", "notCompleted": "none", "other409": "error", "server": "error",
                      "network": "error", "ready": "ready", "empty": "none", "missing": "none"}


def test_card_in_both_modes():
    # The card depends on the run alone: the thread mounts it for a completed run whatever the simple or advanced mode is.
    thread = (JS / "thread.js").read_text(encoding="utf-8")
    assert 'thread.status === "completed" && html`<${DocumentCard} token=${token} runId=${runId} />`' in thread
    for name in ("document-card.js", "document-model.js"):
        assert not re.search(r"\bmode\b|showRawOutput|data-mode", (JS / name).read_text(encoding="utf-8")), name
    # SIM-T03 passes the mode to ThreadCard in thread.js; the line that mounts DocumentCard stays free of it.
    mount = next(line for line in thread.splitlines() if "<${DocumentCard}" in line)
    assert not re.search(r"\bmode\b|showRawOutput|data-mode", mount), mount
    assert '"/gui/api/runs/"' not in (JS / "document-card.js").read_text(encoding="utf-8")  # the path comes from runPath
