# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Context compaction building blocks (CMP). ``estimate`` measures a prompt against the model window and warns
before the call (CMP-T02); ``summary`` makes and validates the summary that replaces older context (CMP-T03);
``workflow`` says what the context of a workflow step is made of and what a compaction replaces (CMP-T04)."""
from __future__ import annotations

from .estimate import (
    CODE_CONTEXT_WINDOW_UNKNOWN,
    ESTIMATOR,
    EVENT_CONTEXT_WARNING,
    EVENT_LEVELS,
    LEVEL_OK,
    LEVEL_THRESHOLD,
    LEVEL_WARNING,
    LEVEL_WINDOW_UNKNOWN,
    ContextEstimate,
    ContextGuard,
    ContextSettings,
    compaction_pending,
    context_settings_for,
    context_settings_for_tasks,
    estimate_call,
    estimate_context,
    estimate_prompt_tokens,
)
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

from .workflow import (
    EVENT_CONTEXT_COMPACTED,
    EVENT_CONTEXT_COMPACTION_SKIPPED,
    CompactionPlan,
    CompactionState,
    ContextPart,
    OutputRecord,
    StepContext,
    build_context_parts,
    plan_compaction,
    resolve_compaction_state,
)

__all__ = [
    "CODE_CONTEXT_WINDOW_UNKNOWN", "ESTIMATOR", "EVENT_CONTEXT_WARNING", "EVENT_LEVELS", "LEVEL_OK",
    "LEVEL_THRESHOLD", "LEVEL_WARNING", "LEVEL_WINDOW_UNKNOWN", "ContextEstimate", "ContextGuard",
    "ContextSettings", "compaction_pending", "context_settings_for", "context_settings_for_tasks",
    "estimate_call", "estimate_context", "estimate_prompt_tokens",
    "CONTEXT_AGENT_ID", "CONTEXT_PROMPT_ID", "CONTEXT_PROMPT_VERSION", "SKIP_REASONS", "SUMMARY_SCHEMA",
    "ContextFacts", "ContextSummaryContent", "PinnedItem", "PreviousSummary", "SourceFacts", "SummaryAccess",
    "SummaryCall", "SummaryCheckOutput", "SummaryPolicy", "SummaryResult", "SummarySchemaError", "SummarySource",
    "ValidationIssue", "ValidationReport", "check_pinned_unchanged", "content_sha256", "extract_facts",
    "parse_summary", "render_summary_for_prompt", "summarize_context", "summary_artifact", "validate_summary",
    "EVENT_CONTEXT_COMPACTED", "EVENT_CONTEXT_COMPACTION_SKIPPED", "CompactionPlan", "CompactionState",
    "ContextPart", "OutputRecord", "StepContext", "build_context_parts", "plan_compaction",
    "resolve_compaction_state",
]
