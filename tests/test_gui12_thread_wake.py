# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Run-thread stream notifications use the read API and keep the polling fallback."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


JS = Path(__file__).resolve().parents[1] / "multiagent" / "gui" / "static" / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@needs_node
def test_wake_delay_reads_after_quiet_and_limits_a_burst():
    script = f"import {{ POLL_MS, THREAD_WAKE_MS, threadWakeDelay }} from {json.dumps((JS / 'thread-model.js').as_uri())};\n" + """
      let lastReadAt = null;
      const reads = [];
      const read = (at) => { if (threadWakeDelay(lastReadAt, at) === 0) { reads.push(at); lastReadAt = at; } };
      read(0);                         // an isolated event reads immediately
      for (let at = 500; at < 10000; at += 500) read(at);
      read(12000);                     // one last read after the burst goes quiet
      console.log(JSON.stringify({poll: POLL_MS, wake: THREAD_WAKE_MS, reads,
        tooSoon: reads.some((at, index) => index && at - reads[index - 1] < THREAD_WAKE_MS),
        wait: threadWakeDelay(lastReadAt, 12500), quiet: threadWakeDelay(lastReadAt, 14000)}));
    """
    result = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                            encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"poll": 4000, "wake": 2000,
                                        "reads": [0, 2000, 4000, 6000, 8000, 12000],
                                        "tooSoon": False, "wait": 1500, "quiet": 0}


def test_run_thread_follows_its_stream_and_closes_it():
    source = (JS / "thread.js").read_text(encoding="utf-8")
    run = source[source.index("export function RunThread"):source.index("// AVA-T05: the message box")]
    assert 'import { parseBlock, startStream } from "./stream.js";' in source
    assert 'session: "framework:" + runId' in run
    assert 'after: String(current.current.cursor)' in run
    assert 'onEvent: () => {' in run and 'wake();' in run
    assert 'const page = await apiGet("/gui/api/threads/runs/"' in run
    assert 'setInterval(() => { if (!isTerminal(current.current.status)) wake(); }, POLL_MS)' in run
    assert 'if (isTerminal(current.current.status)) wakeControl.current?.stop();' in run
    assert 'return () => { stop(); wakeControl.current = null;' in run
    assert 'stopStream();' in run
    assert 'if (stopped || document.hidden || isTerminal(current.current.status)) return;' in run
    assert 'quietTimer = setTimeout(wake, THREAD_WAKE_MS)' in run
