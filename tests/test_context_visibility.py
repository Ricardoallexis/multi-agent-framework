"""CMP-T06: context warnings and compactions remain visible and exportable."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tools.evals.routing import export_runs
from gui_text import ui_text

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "multiagent" / "gui" / "static" / "js"


def test_context_events_are_rendered_in_run_and_session_details():
    run_detail = ui_text(GUI / "run-detail.js")
    session_detail = ui_text(GUI / "session-detail.js")
    text = ui_text(GUI / "session-text.js")

    assert "contextEventText(event.type, event.payload)" in run_detail
    assert "contextEventText(event && event.type, payload)" in session_detail
    assert "contextEventText" in run_detail and "contextEventText" in session_detail
    for words in (
        "Aviso de contexto", "fracción medida ", "umbral de compactación ",
        "Contexto compactado", "antes ", "después ", "instantánea ",
    ):
        assert words in text
    assert "Aviso de contexto" not in (GUI / "run-detail.js").read_text(encoding="utf-8")
    assert "Aviso de contexto" not in (GUI / "session-detail.js").read_text(encoding="utf-8")


def _export_db(path: Path, events: list[tuple[str, dict]]):
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE runs (
                id TEXT PRIMARY KEY, status TEXT, workflow_id TEXT, max_llm_calls INTEGER,
                max_run_seconds INTEGER, brand_version INTEGER, budget_exhausted INTEGER, request_json TEXT
            );
            CREATE TABLE run_events (
                id INTEGER PRIMARY KEY, run_id TEXT, event_type TEXT, payload_json TEXT
            );
            CREATE TABLE telemetry (
                id INTEGER PRIMARY KEY, run_id TEXT, step_id TEXT, agent_id TEXT, attempt INTEGER,
                provider TEXT, model TEXT, model_digest TEXT, prompt_id TEXT, prompt_version INTEGER,
                tokens_in INTEGER, tokens_out INTEGER, num_ctx INTEGER, duration_ms INTEGER,
                estimated_cost_usd REAL, cost_basis TEXT, success INTEGER, error_type TEXT, created_at TEXT
            );
            INSERT INTO runs VALUES ('with-context', 'completed', 'workflow', 4, 60, 1, 0, '{"topic":"x"}');
            INSERT INTO runs VALUES ('without-context', 'completed', 'workflow', 4, 60, 1, 0, '{"topic":"x"}');
        """)
        for event_type, payload in events:
            conn.execute(
                "INSERT INTO run_events(run_id,event_type,payload_json) VALUES(?,?,?)",
                ("with-context", event_type, json.dumps(payload)),
            )


def _label(run_id: str):
    return {
        "run_id": run_id, "case_id": run_id, "route": "legacy", "input_version": "v1",
        "rubric_version": "r1", "rubric_hash": "rubric-hash", "critical_error": False,
    }


def test_run_export_keeps_context_warning_and_compaction_facts(tmp_path):
    path = tmp_path / "runs.db"
    warning = {
        "step_id": "draft", "attempt": 2, "ratio": 0.72, "warning_at": 0.62,
        "threshold": 0.7, "level": "threshold", "prompt": "must not be exported",
    }
    compacted = {
        "step_id": "draft", "attempt": 2, "tokens_before": 12000, "tokens_after": 3800,
        "summary_ref": "blob:sha256:abc123", "summary": "must not be exported",
    }
    _export_db(path, [("context_warning", warning), ("context_compacted", compacted)])

    records = export_runs.export_records(path, [_label("with-context"), _label("without-context")])
    by_case = {record["case_id"]: record for record in records}

    assert by_case["with-context"]["context_events"] == [
        {
            "event_type": "context_warning", "step_id": "draft", "attempt": 2, "level": "threshold",
            "ratio": 0.72, "warning_at": 0.62, "threshold": 0.7,
        },
        {
            "event_type": "context_compacted", "step_id": "draft", "attempt": 2,
            "tokens_before": 12000, "tokens_after": 3800, "summary_ref": "blob:sha256:abc123",
        },
    ]
    assert "prompt" not in json.dumps(by_case["with-context"])
    assert "must not be exported" not in json.dumps(by_case["with-context"])
    assert "context_events" not in by_case["without-context"]
    assert by_case["without-context"]["calls"] == 0
    assert by_case["without-context"]["cost"] == 0
