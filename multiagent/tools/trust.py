"""Every tool result is untrusted prompt data."""
from __future__ import annotations

from multiagent.document_trust import BlobSink, Provenance, Screening, SourceKind, derive, screen, wrap_untrusted
from multiagent.tasks.envelopes import canonical_json
from .contracts import ToolResult


def result_provenance(result: ToolResult, *parents: Provenance) -> Provenance:
    return derive(SourceKind.TOOL_OUTPUT, *parents, ref=f"tool:{result.request_id}",
                  sha256=result.output_sha256, label=result.tool_id)


def _result_text(result: ToolResult) -> str:
    value = result.output if result.ok else {"code": result.error.code.value, "message": result.error.message}
    return canonical_json(value).decode("utf-8")


def render_result_for_prompt(result: ToolResult, *parents: Provenance) -> str:
    return wrap_untrusted(_result_text(result), result_provenance(result, *parents))


def screen_result(result: ToolResult, *, policy: str = "quarantine_warn", store: BlobSink | None = None) -> Screening:
    return screen(_result_text(result), result_provenance(result), policy=policy, store=store)
