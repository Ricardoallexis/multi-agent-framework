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
    EXECUTE_STEP = "execute_step"
    RESEARCH = "research"
    REVIEW = "review"
    RECOVERY = "recovery"
    RECONCILE = "reconcile"
    BUDGET_EXHAUSTED = "budget_exhausted"


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

    @model_validator(mode="after")
    def normalize_research_mode(self):
        if not self.requires_web and self.research_mode == ResearchMode.HUMAN_BRIDGE:
            self.research_mode = ResearchMode.NONE
        return self


class HumanReviewRequest(StrictModel):
    feedback: str = Field(default="", max_length=4000)


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


OUTPUT_SCHEMAS: dict[str, type[StrictModel]] = {
    "ResearchOutput": ResearchOutput,
    "StrategyOutput": StrategyOutput,
    "ContentOutput": ContentOutput,
    "VisualBriefOutput": VisualBriefOutput,
    "VisualHumanBriefOutput": VisualHumanBriefOutput,
    "VisualTechnicalPromptOutput": VisualTechnicalPromptOutput,
    "VisualBothOutput": VisualBothOutput,
    "BrandProfilePayload": BrandProfilePayload,
}
