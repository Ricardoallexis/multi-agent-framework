"""Output contracts of the spec_review fixture bundle, with sample outputs for FakeAdapter."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from multiagent.contracts import StrictModel


class Requirement(StrictModel):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    priority: Literal["must", "should", "could"]


class RequirementsList(StrictModel):
    requirements: list[Requirement] = Field(min_length=1)


class Risk(StrictModel):
    requirement_id: str = Field(min_length=1)
    risk: str = Field(min_length=1)
    severity: Literal["low", "medium", "high"]


class RiskAssessment(StrictModel):
    risks: list[Risk]
    summary: str = Field(min_length=1)


class TestCase(StrictModel):
    requirement_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    steps: list[str] = Field(min_length=1)
    expected: str = Field(min_length=1)


class TestPlan(StrictModel):
    cases: list[TestCase] = Field(min_length=1)


OUTPUT_SCHEMAS = {
    "RequirementsList": RequirementsList,
    "RiskAssessment": RiskAssessment,
    "TestPlan": TestPlan,
}

SAMPLE_OUTPUTS = {
    "RequirementsList": {"requirements": [
        {"id": "R1", "text": "Users can reset their password by email.", "priority": "must"},
        {"id": "R2", "text": "Reset links expire after 30 minutes.", "priority": "should"},
    ]},
    "RiskAssessment": {
        "risks": [{"requirement_id": "R2", "risk": "Clock skew may expire links early.", "severity": "medium"}],
        "summary": "One medium risk around link expiry.",
    },
    "TestPlan": {"cases": [
        {"requirement_id": "R1", "title": "Reset by email", "steps": ["Request a reset", "Open the link"],
         "expected": "The password can be changed."},
        {"requirement_id": "R2", "title": "Expired link", "steps": ["Request a reset", "Wait 31 minutes", "Open the link"],
         "expected": "The link is rejected as expired."},
    ]},
}
