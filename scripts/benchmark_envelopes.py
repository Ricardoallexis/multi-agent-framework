# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Context benchmark of ORQ-3 (criterion U): bytes an agent reads to start or resume a task.

Compares two ways of handing a task with dependencies to an agent:

* **current mode** — what a team protocol built on handoff documents makes the agent
  read: its profile, the minimum context, a context package, the task card, and for
  every dependency its delivery entry and handoff document. The sizes are fixed
  pieces measured once on such a team channel (ORQ-3 contract §10, lower bounds),
  so the script never reads a private channel and runs in a clean clone;
* **envelope mode** — the task envelope, the results of its dependencies and, to
  resume, the events after the consumer's cursor, all produced by the framework on a
  synthetic, deterministic fixture (group T1 -> T2 || T3 -> T4, Mock mode).

Tokens are estimated as ``chars / 4``. The reduction is reported, not promised.

Usage::

    python scripts/benchmark_envelopes.py          # JSON report on stdout
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Current mode: fixed piece sizes in characters (ORQ-3 contract §10, lower bounds).
CURRENT_PIECES = {
    "profile": 2224,
    "minimum_context": 7662,
    "context_package": 17857,
    "task_card": 1947,
}
CURRENT_PER_DEPENDENCY = {"delivery_entry": 1940, "handoff": 12627}
CURRENT_PER_EVENT = {"inbox_entry": 1940}  # each new channel entry read to resume

PATCH = ("diff --git a/src/module.py b/src/module.py\n" + "+value = 1\n" * 40).encode("utf-8")
SUMMARY = "Implemented the change described in the objective; tests cover the new behaviour."


def _size(value: Any) -> int:
    return len(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _tokens(size: int) -> int:
    return (size + 3) // 4


def _services(data_dir: Path):
    from multiagent.config import Settings
    from multiagent.services import ApplicationServices

    settings = Settings(_env_file=None, local_dir=data_dir, worker_enabled=False,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    return ApplicationServices.create(settings, dry_run=True)


def _deliver(services, task_id: str, agent: str) -> None:
    tasks, envelopes = services.tasks, services.envelopes
    for action in ("claim", "start"):
        tasks.transition(task_id, action, actor=agent)
    blob = envelopes.put_blob(PATCH + task_id.encode(), actor=agent, media_type="text/x-patch")
    envelopes.submit_result(task_id, {
        "summary": SUMMARY, "decisions": ["Kept the public API unchanged"], "tests_not_run": ["full suite"],
        "patch": {"blob": f"sha256:{blob['sha256']}", "files": ["src/module.py"]},
    }, actor=agent)
    tasks.transition(task_id, "accept", actor="user")


def run_benchmark(data_dir: Path) -> dict[str, Any]:
    """Build the fixture under ``data_dir`` and return the report (deterministic sizes)."""
    services = _services(data_dir)
    tasks, envelopes = services.tasks, services.envelopes
    tasks.create_group("BENCH", "Benchmark", actor="user", authorized=True)
    tasks.create_task("BENCH", "T1", "Base", actor="user", objective="Create the base module.")
    tasks.create_task("BENCH", "T2", "Left", actor="user", objective="Extend the module on the left.", depends_on=["T1"])
    tasks.create_task("BENCH", "T3", "Right", actor="user", objective="Extend the module on the right.",
                      depends_on=["T1"])
    tasks.create_task("BENCH", "T4", "Join", actor="user", objective="Join both extensions and document them.",
                      depends_on=["T2", "T3"], file_scope=["src/module.py", "docs/module.md"])
    envelopes.set_task_context("T4", actor="user", base_commit="0000000",
                               context_refs=["doc://docs/module.md"], constraints=["no push"])
    for task_id in ("T1", "T2", "T3"):
        _deliver(services, task_id, f"agent:{task_id.lower()}")

    # Start T4: the envelope and the results of its two dependencies.
    envelope = envelopes.get_envelope("T4")
    results = [envelopes.get_result(dep["result_ref"]) for dep in envelope["depends_on"]]
    start_new = _size(envelope) + sum(_size(result) for result in results)
    start_reads = 1 + len(results)
    dependencies = len(envelope["depends_on"])
    start_current = (sum(CURRENT_PIECES.values())
                     + dependencies * sum(CURRENT_PER_DEPENDENCY.values()))

    # Resume T4 after an interruption: the events after the agent's acknowledged cursor.
    consumer = "agent:t4"
    envelopes.ack_events(consumer, envelope["events_after"])
    tasks.transition("T4", "claim", actor=consumer)
    tasks.transition("T4", "start", actor=consumer)
    tasks.transition("T4", "pause", actor=consumer)
    tasks.transition("T4", "resume", actor=consumer)
    envelopes.set_task_context("T4", actor="user", constraints=["no push", "keep docs short"])
    delta = envelopes.read_events(consumer, scope="task:T4")
    resume_new = _size(delta["events"])
    resume_current = (CURRENT_PIECES["context_package"] + CURRENT_PIECES["task_card"]
                      + len(delta["events"]) * CURRENT_PER_EVENT["inbox_entry"])

    def scenario(current: int, new: int, reads_current: int, reads_new: int) -> dict[str, Any]:
        return {"current_bytes": current, "new_bytes": new,
                "current_tokens_est": _tokens(current), "new_tokens_est": _tokens(new),
                "current_reads": reads_current, "new_reads": reads_new,
                "ratio": round(new / current, 4)}

    return {
        "fixture": "T1 -> T2 || T3 -> T4 (Mock mode)",
        "token_estimate": "chars / 4",
        "start_task_with_dependencies": scenario(
            start_current, start_new, len(CURRENT_PIECES) + dependencies * len(CURRENT_PER_DEPENDENCY), start_reads),
        "resume_from_cursor": scenario(resume_current, resume_new, 2 + len(delta["events"]), 1),
        "events_to_resume": len(delta["events"]),
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as folder:
        report = run_benchmark(Path(folder))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
