"""Output contracts of the general example bundle, with sample outputs for Mock runs.

The contracts are domain-neutral: they describe the shape of a summary, a comparison of
options and a review, not the subject matter. ``SAMPLE_OUTPUTS`` lets every workflow run in
Mock mode (no model calls, no API keys).
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from multiagent.contracts import DocumentSummary, FreeTextAnswer, StrictModel


# -- summarize_document -------------------------------------------------------------------------
class KeyPoint(StrictModel):
    id: str = Field(min_length=1)
    point: str = Field(min_length=1)
    evidence: str = Field(min_length=1)


class KeyPoints(StrictModel):
    key_points: list[KeyPoint] = Field(min_length=1)
    open_questions: list[str]


# -- research_options ---------------------------------------------------------------------------
class Finding(StrictModel):
    id: str = Field(min_length=1)
    finding: str = Field(min_length=1)
    confidence: Literal["low", "medium", "high"]
    source_note: str = Field(min_length=1)


class ResearchFindings(StrictModel):
    question: str = Field(min_length=1)
    findings: list[Finding] = Field(min_length=1)
    gaps: list[str]


class Option(StrictModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    pros: list[str] = Field(min_length=1)
    cons: list[str] = Field(min_length=1)


class OptionsComparison(StrictModel):
    criteria: list[str] = Field(min_length=1)
    options: list[Option] = Field(min_length=2)


class Recommendation(StrictModel):
    recommended_option_id: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    risks: list[str]
    next_steps: list[str] = Field(min_length=1)


# -- review_checklist ---------------------------------------------------------------------------
class ChecklistItem(StrictModel):
    criterion: str = Field(min_length=1)
    status: Literal["met", "partial", "not_met", "not_applicable"]
    comment: str = Field(min_length=1)


class ChecklistResult(StrictModel):
    items: list[ChecklistItem] = Field(min_length=1)


class ReviewReport(StrictModel):
    verdict: Literal["approved", "needs_changes", "rejected"]
    summary: str = Field(min_length=1)
    blocking_issues: list[str]
    suggestions: list[str]


class CollectedOutputs(StrictModel):
    steps: dict[str, dict[str, object]]


# -- ask_once -----------------------------------------------------------------------------------
# FreeTextAnswer and DocumentSummary live in the core (multiagent.contracts); they are re-exported here.
OUTPUT_SCHEMAS = {
    "KeyPoints": KeyPoints,
    "DocumentSummary": DocumentSummary,
    "ResearchFindings": ResearchFindings,
    "OptionsComparison": OptionsComparison,
    "Recommendation": Recommendation,
    "ChecklistResult": ChecklistResult,
    "ReviewReport": ReviewReport,
    "CollectedOutputs": CollectedOutputs,
    "FreeTextAnswer": FreeTextAnswer,
}

SAMPLE_OUTPUTS = {
    "FreeTextAnswer": {"answer": "This is a sample answer from Mock."},
    "CollectedOutputs": {"steps": {}},
    "KeyPoints": {
        "key_points": [
            {"id": "P1", "point": "The weekly meeting moves from Mondays to Wednesdays.",
             "evidence": "\"From March, the weekly meeting will be on Wednesdays at 10:00.\""},
            {"id": "P2", "point": "The minutes are published in the shared folder the same day.",
             "evidence": "\"The minutes will be uploaded to the shared folder before 18:00.\""},
        ],
        "open_questions": ["It does not say what happens when the Wednesday is a holiday."],
    },
    "DocumentSummary": {
        "title": "Change of the weekly meeting",
        "summary": "The weekly meeting moves to Wednesdays at 10:00 and the minutes are published the same day "
                   "in the shared folder.",
        "key_points": [
            "New day and time: Wednesday at 10:00.",
            "The minutes are published before 18:00 the same day.",
        ],
        "open_questions": ["What to do if the Wednesday is a holiday."],
    },
    "ResearchFindings": {
        "question": "Should we move to asynchronous weekly meetings?",
        "findings": [
            {"id": "H1", "finding": "Asynchronous meetings reduce interruptions, but delay the decisions "
                                     "that need a conversation.",
             "confidence": "medium", "source_note": "General experience of distributed teams; no source cited."},
            {"id": "H2", "finding": "The context says the team works in two time zones.",
             "confidence": "high", "source_note": "Fact given in the context of the query."},
        ],
        "gaps": ["The current cost, in hours, of the weekly meetings is not known."],
    },
    "OptionsComparison": {
        "criteria": ["Time cost", "Quality of decisions", "Ease of adoption"],
        "options": [
            {"id": "A", "name": "Keep the synchronous meeting",
             "description": "One live weekly meeting, as today.",
             "pros": ["Fast decisions", "Nothing changes"], "cons": ["Hard to coordinate across time zones"]},
            {"id": "B", "name": "Move to an asynchronous summary",
             "description": "Each person posts their progress in writing and they only meet to decide.",
             "pros": ["Less time in meetings", "A written record remains"],
             "cons": ["Requires discipline to write", "Slower for urgent questions"]},
        ],
    },
    "Recommendation": {
        "recommended_option_id": "B",
        "rationale": "It reduces time in meetings and fits both time zones, at the cost of requiring "
                     "writing discipline.",
        "risks": ["The summaries may be posted late or with little detail."],
        "next_steps": ["Try the format for four weeks.", "Measure the time saved and review the decision."],
    },
    "ChecklistResult": {
        "items": [
            {"criterion": "Every requirement has an owner", "status": "partial",
             "comment": "Two requirements in section 3 do not name an owner."},
            {"criterion": "Deadlines are specific", "status": "met",
             "comment": "Every deliverable has a date."},
            {"criterion": "Risks are described", "status": "not_met",
             "comment": "The document has no risks section."},
        ],
    },
    "ReviewReport": {
        "verdict": "needs_changes",
        "summary": "The document is clear and has specific deadlines, but it lacks owners and risks.",
        "blocking_issues": ["The risks section is missing."],
        "suggestions": ["Assign an owner to requirements 3.2 and 3.4.", "Add a risks table with their mitigation."],
    },
}
