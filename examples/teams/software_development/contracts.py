"""Output contracts of the software_development example bundle, with sample outputs for Mock runs."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from multiagent.contracts import StrictModel


class UserStory(StrictModel):
    id: str = Field(min_length=1)
    as_a: str = Field(min_length=1)
    i_want: str = Field(min_length=1)
    so_that: str = Field(min_length=1)


class ProductBrief(StrictModel):
    goal: str = Field(min_length=1)
    users: list[str] = Field(min_length=1)
    stories: list[UserStory] = Field(min_length=1)
    out_of_scope: list[str]


class Component(StrictModel):
    name: str = Field(min_length=1)
    responsibility: str = Field(min_length=1)


class TechnicalDesign(StrictModel):
    overview: str = Field(min_length=1)
    components: list[Component] = Field(min_length=1)
    interfaces: list[str]
    risks: list[str]


class PlannedTask(StrictModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    story_ids: list[str] = Field(min_length=1)
    depends_on: list[str]


class BackendPlan(StrictModel):
    tasks: list[PlannedTask] = Field(min_length=1)


class FrontendPlan(StrictModel):
    tasks: list[PlannedTask] = Field(min_length=1)


class AcceptanceTest(StrictModel):
    story_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    steps: list[str] = Field(min_length=1)
    expected: str = Field(min_length=1)


class TestStrategy(StrictModel):
    tests: list[AcceptanceTest] = Field(min_length=1)
    not_covered: list[str]


class DocumentSection(StrictModel):
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)


class DocumentPlan(StrictModel):
    audience: str = Field(min_length=1)
    sections: list[DocumentSection] = Field(min_length=1)


class DocumentationPlan(StrictModel):
    documents: list[DocumentPlan] = Field(min_length=1)


class Finding(StrictModel):
    severity: Literal["low", "medium", "high"]
    note: str = Field(min_length=1)


class ReviewReport(StrictModel):
    verdict: Literal["approve", "changes_requested"]
    findings: list[Finding]
    summary: str = Field(min_length=1)


OUTPUT_SCHEMAS = {
    "ProductBrief": ProductBrief,
    "TechnicalDesign": TechnicalDesign,
    "BackendPlan": BackendPlan,
    "FrontendPlan": FrontendPlan,
    "TestStrategy": TestStrategy,
    "DocumentationPlan": DocumentationPlan,
    "ReviewReport": ReviewReport,
}

SAMPLE_OUTPUTS = {
    "ProductBrief": {
        "goal": "Let users reset a forgotten password by email.",
        "users": ["Registered user", "Support agent"],
        "stories": [
            {"id": "S1", "as_a": "registered user", "i_want": "to request a reset link by email",
             "so_that": "I can regain access to my account"},
            {"id": "S2", "as_a": "registered user", "i_want": "the link to expire after 30 minutes",
             "so_that": "a leaked link cannot be used later"},
        ],
        "out_of_scope": ["Password reset by phone", "Account recovery without email access"],
    },
    "TechnicalDesign": {
        "overview": "A reset service issues single-use tokens and a mailer sends them; the web form consumes them.",
        "components": [
            {"name": "Reset service", "responsibility": "Creates, stores and validates single-use tokens."},
            {"name": "Mailer", "responsibility": "Sends the reset link to the account email."},
            {"name": "Reset form", "responsibility": "Collects the new password and submits the token."},
        ],
        "interfaces": ["POST /password-resets", "POST /password-resets/{token}"],
        "risks": ["Clock skew may expire tokens early.", "Email delivery delays are outside our control."],
    },
    "BackendPlan": {"tasks": [
        {"id": "B1", "title": "Create the token store with expiry", "story_ids": ["S2"], "depends_on": []},
        {"id": "B2", "title": "Expose the reset request and confirm endpoints", "story_ids": ["S1", "S2"],
         "depends_on": ["B1"]},
    ]},
    "FrontendPlan": {"tasks": [
        {"id": "F1", "title": "Build the request-reset form", "story_ids": ["S1"], "depends_on": ["B2"]},
        {"id": "F2", "title": "Build the new-password form and expired-link message", "story_ids": ["S1", "S2"],
         "depends_on": ["B2"]},
    ]},
    "TestStrategy": {
        "tests": [
            {"story_id": "S1", "title": "Reset by email", "steps": ["Request a reset", "Open the link"],
             "expected": "The password can be changed."},
            {"story_id": "S2", "title": "Expired link",
             "steps": ["Request a reset", "Wait 31 minutes", "Open the link"],
             "expected": "The link is rejected as expired."},
        ],
        "not_covered": ["Email provider outages"],
    },
    "DocumentationPlan": {"documents": [
        {"audience": "Users", "sections": [
            {"title": "Reset your password", "summary": "Steps to request and use a reset link."}]},
        {"audience": "Maintainers", "sections": [
            {"title": "Token lifecycle", "summary": "How tokens are created, expire and are revoked."}]},
    ]},
    "ReviewReport": {
        "verdict": "approve",
        "findings": [{"severity": "low", "note": "Add a rate limit note to the maintainer documentation."}],
        "summary": "Every story is covered by the design, the plans and the tests.",
    },
}