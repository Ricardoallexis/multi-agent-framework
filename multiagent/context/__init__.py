# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Context compaction building blocks (CMP). ``summary`` makes and validates the summary (CMP-T03)."""
from __future__ import annotations

from .summary import (
    CONTEXT_AGENT_ID,
    CONTEXT_PROMPT_ID,
    CONTEXT_PROMPT_VERSION,
    SKIP_REASONS,
    SUMMARY_SCHEMA,
    ContextFacts,
    ContextSummaryContent,
    PinnedItem,
    PreviousSummary,
    SourceFacts,
    SummaryAccess,
    SummaryCall,
    SummaryCheckOutput,
    SummaryPolicy,
    SummaryResult,
    SummarySchemaError,
    SummarySource,
    ValidationIssue,
    ValidationReport,
    check_pinned_unchanged,
    content_sha256,
    extract_facts,
    parse_summary,
    render_summary_for_prompt,
    summarize_context,
    summary_artifact,
    validate_summary,
)

__all__ = [
    "CONTEXT_AGENT_ID", "CONTEXT_PROMPT_ID", "CONTEXT_PROMPT_VERSION", "SKIP_REASONS", "SUMMARY_SCHEMA",
    "ContextFacts", "ContextSummaryContent", "PinnedItem", "PreviousSummary", "SourceFacts", "SummaryAccess",
    "SummaryCall", "SummaryCheckOutput", "SummaryPolicy", "SummaryResult", "SummarySchemaError", "SummarySource",
    "ValidationIssue", "ValidationReport", "check_pinned_unchanged", "content_sha256", "extract_facts",
    "parse_summary", "render_summary_for_prompt", "summarize_context", "summary_artifact", "validate_summary",
]
