from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    INTERRUPTED = "interrupted"


class WaitingReason(str, Enum):
    SECRET_REVIEW = "secret_review"
    TOOL_APPROVAL = "tool_approval"
    EXECUTE_STEP = "execute_step"
    RESEARCH = "research"
    REVIEW = "review"
    APPROVAL_PENDING = "approval_pending"
    RECOVERY = "recovery"
    RECONCILE = "reconcile"
    BUDGET_EXHAUSTED = "budget_exhausted"
    # Supervised economic profile (ECO-T06): the step waits for a human to approve a move to a stronger model.
    ESCALATION = "escalation_approval"
    # CNT-T04 (contract CNT-T01 section 4, decision 5): the router ran out of fallbacks of the same level because of a
    # usage limit. The hold of a task (``tasks.execution_json``, ``services/hold.py``) is the model for the run's wait.
    LIMIT_REACHED = "limit_reached"
    # BDI-T04 (contract BDI-T01 section 8): a model call is held because the content of the step needs a person to
    # look at it first (policy ``pause``). The run waits until it is approved or rejected through the approval path
    # of runs (``RunService.approve`` / ``reject``, event ``document_review_resolved``), which does not authenticate
    # its caller (see ``document_review``).
    CONTENT_REVIEW = "content_review"
    # AVA-T03: a message of the person (``interrupt`` or ``adjust``) was read at the boundary of a step and the run waits for
    # them. They settle it with the approval path of runs (``approve`` goes on, ``reject`` rejects; ``cancel`` always works);
    # the inbox never settles it. The message is consumed when it pauses the run, so it never pauses the run twice.
    USER_MESSAGE = "user_message"
    # USO-T04 (contract CMP-T01 section 4, order 300): a model call would pass a budget in cut mode, or has no known price, so
    # the step waits for a person before it is made, the way a supervised escalation waits. They settle it with the approval
    # path of runs: ``approve`` lets that one call through, ``reject`` fails the run (``Store.finish_budget_cut``).
    BUDGET_CUT = "budget_cut_approval"
    EGRESS_REVIEW = "egress_review"
    FUNCTION_REVIEW = "function_review"
    FUNCTION_FAILURE = "function_failure"


class EgressBlocked(Exception):
    """Outbound content denied before a provider call (stable code for run errors)."""

    code = "egress_blocked"


class HumanAction(str, Enum):
    APPROVE = "approve"
    REVISE = "revise"
    REJECT = "reject"
    REGENERATE = "regenerate"


class ExecutionMode(str, Enum):
    AUTO = "auto"
    LOCAL = "local"
    CLOUD = "cloud"
    HUMAN_GUIDED = "human_guided"


class ResearchMode(str, Enum):
    HUMAN_BRIDGE = "human_bridge"
    GEMINI_GROUNDED = "gemini_grounded"
    NONE = "none"


class PipelineMode(str, Enum):
    QUICK = "quick"
    FULL = "full"


class GroundingStatus(str, Enum):
    VERIFIED = "VERIFIED"
    PARTIAL = "PARTIAL"
    UNVERIFIED = "UNVERIFIED"


class Provenance(str, Enum):
    SYSTEM = "system"
    API = "api"
    HUMAN_REPORTED = "human_reported"
    HUMAN_CREATED = "human_created"


class OutputMode(str, Enum):
    HUMAN_BRIEF = "human_brief"
    TECHNICAL_PROMPT = "technical_prompt"
    BOTH = "both"


class ModelTier(str, Enum):
    LOCAL = "local"
    CLOUD = "cloud"


class TaskRequirements(StrictModel):
    requires_web: bool = False
    output_schema: str
    tier: ModelTier


class SocialPostRequest(StrictModel):
    project_name: str = Field(min_length=2, max_length=120)
    objective: str = Field(min_length=5, max_length=1000)
    topic: str = Field(min_length=2, max_length=500)
    platform: str = Field(default="Instagram", min_length=2, max_length=80)
    audience: str = Field(default="", max_length=500)
    instructions: str = Field(default="", max_length=4000)
    requires_web: bool = False
    use_brand_context: bool = True
    idempotency_key: str = Field(default="", max_length=120)
    execution_mode: ExecutionMode = ExecutionMode.AUTO
    research_mode: ResearchMode = ResearchMode.HUMAN_BRIDGE
    pipeline_mode: PipelineMode = PipelineMode.QUICK
    step_modes: dict[str, ExecutionMode] = Field(default_factory=dict)
    sensitive: bool = False
    confirm_cloud: bool = False

    @model_validator(mode="after")
    def normalize_research_mode(self):
        if not self.requires_web and self.research_mode == ResearchMode.HUMAN_BRIDGE:
            self.research_mode = ResearchMode.NONE
        return self


class RevisionComment(StrictModel):
    target: str = Field(min_length=1, max_length=200, pattern=r"^(?:[^.\[\]]+|\[[0-9]+\])(?:\.[^.\[\]]+|\[[0-9]+\])*$")
    kind: Literal["fix", "add", "remove", "rewrite", "other"]
    text: str = Field(min_length=1, max_length=2000)
    quote: str | None = Field(default=None, max_length=2000)

    @field_validator("target")
    @classmethod
    def no_controls_in_target(cls, value: str) -> str:
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Target must not contain control characters")
        return value


class FeedbackRequest(StrictModel):
    feedback: str = Field(default="", max_length=4000)


class HumanReviewRequest(FeedbackRequest):
    comments: list[RevisionComment] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_feedback_or_comments(self):
        if not self.feedback.strip() and not self.comments:
            raise ValueError("Feedback or comments are required")
        return self


class HumanStepSubmission(StrictModel):
    raw_response: str = Field(min_length=1)
    provider: str = Field(default="human", max_length=120)
    model: str = Field(default="", max_length=200)
    prompt_used: str = Field(default="", max_length=50000)
    notes: str = Field(default="", max_length=4000)


class ResearchOutput(StrictModel):
    objective: str = Field(min_length=5)
    executive_summary: str = Field(default="", max_length=5000)
    key_findings: list[str] = Field(min_length=1, max_length=20)
    verified_claims: list[str] = Field(default_factory=list, max_length=30)
    uncertain_claims: list[str] = Field(default_factory=list, max_length=30)
    conflicting_information: list[str] = Field(default_factory=list, max_length=20)
    dates: list[str] = Field(default_factory=list, max_length=30)
    numbers_and_statistics: list[str] = Field(default_factory=list, max_length=30)
    uncertainties: list[str] = Field(default_factory=list, max_length=20)
    source_urls: list[str] = Field(default_factory=list, max_length=40)
    source_quality: list[str] = Field(default_factory=list, max_length=40)
    gaps: list[str] = Field(default_factory=list, max_length=20)
    grounding_status: GroundingStatus = GroundingStatus.UNVERIFIED

    @field_validator("key_findings")
    @classmethod
    def no_placeholders(cls, values: list[str]):
        bad = {"n/a", "tbd", "...", "lorem ipsum"}
        for value in values:
            if value.strip().lower() in bad or len(value.strip()) < 8:
                raise ValueError("key_findings contains an empty value or placeholder")
        return values


class StrategyOutput(StrictModel):
    objective: str = Field(min_length=5)
    target_audience: str = Field(min_length=2)
    audience_problem: str = Field(default="", max_length=2000)
    content_angle: str = Field(min_length=5, max_length=2000)
    core_message: str = Field(min_length=5, max_length=2000)
    supporting_points: list[str] = Field(min_length=1, max_length=12)
    content_structure: list[str] = Field(min_length=1, max_length=12)
    hook_direction: str = Field(default="", max_length=1000)
    cta_strategy: str = Field(default="", max_length=1000)
    tone: str = Field(default="", max_length=1000)
    claims_allowed: list[str] = Field(default_factory=list, max_length=30)
    claims_to_avoid: list[str] = Field(default_factory=list, max_length=30)
    differentiators: list[str] = Field(default_factory=list, max_length=12)
    platform_considerations: list[str] = Field(default_factory=list, max_length=12)


class ContentOutput(StrictModel):
    title: str = Field(min_length=1, max_length=180)
    hook: str = Field(min_length=8, max_length=500)
    body: str = Field(min_length=30, max_length=12000)
    cta: str = Field(default="", max_length=500)
    hashtags: list[str] = Field(default_factory=list, max_length=20)
    source_urls_used: list[str] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def reject_placeholders(self):
        joined = f"{self.title} {self.hook} {self.body}".lower()
        for bad in ["lorem ipsum", "[insert", "tbd", "content here"]:
            if bad in joined:
                raise ValueError(f"placeholder content detected: {bad}")
        return self


class ProductionMedium(str, Enum):
    IMAGE = "image"
    VIDEO = "video"


class VisualBriefOutput(StrictModel):
    objective: str = Field(min_length=5)
    medium: ProductionMedium = ProductionMedium.IMAGE
    output_mode: OutputMode = OutputMode.BOTH
    format: str = Field(default="", max_length=200)
    aspect_ratio: str = Field(default="", max_length=80)
    visual_hierarchy: list[str] = Field(default_factory=list, max_length=12)
    composition: str = Field(min_length=20, max_length=5000)
    main_subject: str = Field(min_length=3, max_length=2000)
    secondary_elements: list[str] = Field(default_factory=list, max_length=20)
    environment: str = Field(default="", max_length=2000)
    lighting: str = Field(default="", max_length=1000)
    brand_application: list[str] = Field(default_factory=list, max_length=20)
    typography_direction: str = Field(default="", max_length=1000)
    text_overlay: list[str] = Field(default_factory=list, max_length=20)
    accessibility_notes: list[str] = Field(default_factory=list, max_length=20)
    production_notes: list[str] = Field(default_factory=list, max_length=20)
    human_brief: str = Field(min_length=30, max_length=8000)
    technical_prompt: str = Field(min_length=30, max_length=16000)
    negative_constraints: list[str] = Field(default_factory=list, max_length=30)


# Compatibility aliases retained for existing structured-output contracts.
class VisualHumanBriefOutput(StrictModel):
    objective: str = Field(min_length=5)
    medium: ProductionMedium
    human_brief: str = Field(min_length=30)
    constraints: list[str] = Field(default_factory=list)


class VisualTechnicalPromptOutput(StrictModel):
    objective: str = Field(min_length=5)
    medium: ProductionMedium
    technical_prompt: str = Field(min_length=30)
    negative_constraints: list[str] = Field(default_factory=list)


class VisualBothOutput(StrictModel):
    objective: str = Field(min_length=5)
    medium: ProductionMedium
    human_brief: str = Field(min_length=30)
    technical_prompt: str = Field(min_length=30)
    negative_constraints: list[str] = Field(default_factory=list)


class BrandField(StrictModel):
    value: str = Field(min_length=1)
    source: Literal["ai_proposed", "human_confirmed", "human_derived"] = "ai_proposed"
    evidence: list[str] = Field(default_factory=list, max_length=20)


class BrandProfilePayload(StrictModel):
    name: str = Field(min_length=2)
    core: dict[str, BrandField] = Field(default_factory=dict)
    voice: dict[str, BrandField] = Field(default_factory=dict)
    visual: dict[str, BrandField] = Field(default_factory=dict)
    strategy: dict[str, BrandField] = Field(default_factory=dict)
    assets: list[str] = Field(default_factory=list)

    def confirmed_section(self, section: str) -> dict[str, str]:
        values: dict[str, BrandField] = getattr(self, section)
        return {k: v.value for k, v in values.items() if v.source == "human_confirmed"}

    def usable_section(self, section: str) -> dict[str, str]:
        values: dict[str, BrandField] = getattr(self, section)
        return {k: v.value for k, v in values.items() if v.source in {"human_confirmed", "human_derived"}}


class AssetSource(str, Enum):
    AI_GENERATED = "ai_generated"
    HUMAN_CREATED = "human_created"
    AI_ASSISTED = "ai_assisted"
    EXTERNAL = "external"


class AssetStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    PUBLISHED = "published"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class PublicationStatus(str, Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class AssetMetadata(StrictModel):
    project_id: str
    source: AssetSource
    status: AssetStatus = AssetStatus.DRAFT
    title: str = ""
    notes: str = ""
    tags: list[str] = Field(default_factory=list)
    rejection_reason: str = ""
    prompt_id: str = ""
    prompt_version: int | None = None
    model_id: str = ""
    run_id: str = ""
    step_id: str = ""
    attempt: int | None = None


class MetricSnapshotInput(StrictModel):
    publication_id: str
    platform: str
    metrics: dict[str, float | int | str | None]
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class FreeTextAnswer(StrictModel):
    """The neutral contract of a single answer: one non-empty text."""

    answer: str = Field(min_length=1)


class DocumentSummary(StrictModel):
    """The neutral contract of a document summary."""

    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    key_points: list[str] = Field(min_length=1)
    open_questions: list[str]


NEUTRAL_CONTRACTS = ("FreeTextAnswer", "DocumentSummary")


OUTPUT_SCHEMAS: dict[str, type[StrictModel]] = {
    "ResearchOutput": ResearchOutput,
    "StrategyOutput": StrategyOutput,
    "ContentOutput": ContentOutput,
    "VisualBriefOutput": VisualBriefOutput,
    "VisualHumanBriefOutput": VisualHumanBriefOutput,
    "VisualTechnicalPromptOutput": VisualTechnicalPromptOutput,
    "VisualBothOutput": VisualBothOutput,
    "BrandProfilePayload": BrandProfilePayload,
    "FreeTextAnswer": FreeTextAnswer,
    "DocumentSummary": DocumentSummary,
}


class NewSessionProposal(StrictModel):
    """What a model proposes for the free text of "New session" (GUI2-T17). The service validates it against the
    declared workflows and their inputs; nothing here starts a run."""

    workflow_id: str = Field(default="", max_length=120)
    bundle: str | None = Field(default=None, max_length=120)
    inputs: dict[str, str] = Field(default_factory=dict)
    project_name: str = Field(default="", max_length=120)
    reason: str = Field(default="", max_length=400)
    needs_plan: bool = False


class ProjectDraftTask(StrictModel):
    """One task of a drafted project (PRY-T06): only a title and what it is for. Ids, assignees and dependencies are never the model's."""

    title: str = Field(min_length=1, max_length=160)
    objective: str = Field(default="", max_length=600)


class ProjectDraftStage(StrictModel):
    """One stage of a drafted project: a title and its tasks."""

    title: str = Field(min_length=1, max_length=120)
    tasks: list[ProjectDraftTask] = Field(min_length=1, max_length=12)


class ProjectDraftProposal(StrictModel):
    """What a model drafts for a project asked for in a conversation (PRY-T06): a title and stages with tasks. The
    service turns it into a project plan and ``projects.plan`` checks it; nothing here creates anything."""

    title: str = Field(min_length=1, max_length=120)
    stages: list[ProjectDraftStage] = Field(min_length=1, max_length=12)


class OfficeExportProposal(StrictModel):
    """A model's advisory export summary; the server chooses package contents."""

    summary: str = Field(min_length=1, max_length=1200)
    keep: list[str] = Field(default_factory=list, max_length=100)
    omit: list[str] = Field(default_factory=list, max_length=100)
    questions: list[str] = Field(default_factory=list, max_length=20)
