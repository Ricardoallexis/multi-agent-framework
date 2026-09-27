from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping
from pydantic import BaseModel

from .base import LLMAdapter, LLMResponse
from ..catalog import ModelSpec
from ..contracts import (
    BrandField, BrandProfilePayload, ContentOutput, GroundingStatus,
    ResearchOutput, StrategyOutput, VisualBriefOutput,
)


class FakeAdapter(LLMAdapter):
    provider = "fake"

    def __init__(self, *, sample_outputs: Mapping[str, dict[str, Any]] | None = None,
                 output_schemas: Mapping[str, type[BaseModel]] | None = None):
        """Accept samples by contract name, resolving aliases through the registry."""
        self.sample_outputs = deepcopy(dict(sample_outputs or {}))
        self.output_schemas = dict(output_schemas) if output_schemas is not None else None

    def generate_structured(self, *, spec: ModelSpec, prompt: str, output_schema: type[BaseModel], requires_web: bool = False) -> LLMResponse:
        names = ([name for name, schema in self.output_schemas.items() if schema is output_schema]
                 if self.output_schemas is not None else [output_schema.__name__])
        samples = [self.sample_outputs[name] for name in names if name in self.sample_outputs]
        if len(samples) > 1:
            raise ValueError(f"Multiple samples identify the same schema {output_schema.__name__}")
        if samples:
            parsed = output_schema.model_validate(deepcopy(samples[0]))
        elif output_schema is ResearchOutput:
            parsed = ResearchOutput(
                objective="Simulated research",
                executive_summary="Simulated summary used to validate the research flow.",
                key_findings=["Simulated finding detailed enough to validate the contract."],
                verified_claims=["Simulated claim supported by the fixture."],
                source_urls=["https://example.com/source"] if requires_web else [],
                grounding_status=GroundingStatus.VERIFIED if requires_web else GroundingStatus.UNVERIFIED,
            )
        elif output_schema is StrategyOutput:
            parsed = StrategyOutput(
                objective="Define a test social-content strategy",
                target_audience="Technical test audience",
                content_angle="Explain practical value with technical clarity.",
                core_message="Interoperability supports more coherent and maintainable systems.",
                supporting_points=["A simulated supporting point with enough detail for validation."],
                content_structure=["Hook", "Development", "CTA"],
                hook_direction="Open with a useful question.",
                cta_strategy="Invite discussion.",
                claims_allowed=["Use only claims from the fixture."],
            )
        elif output_schema is ContentOutput:
            parsed = ContentOutput(
                title="Test post",
                hook="A clear way to explain technology without unnecessary complexity.",
                body="This is simulated content generated in dry-run mode to validate the full workflow without consuming any external API.",
                cta="Learn more about our solutions.",
                hashtags=["#ExampleBrand", "#Technology"],
                source_urls_used=["https://example.com/source"] if "example.com" in prompt else [],
            )
        elif output_schema is VisualBriefOutput:
            parsed = VisualBriefOutput(
                objective="Create a test social image",
                format="Post social",
                aspect_ratio="1:1",
                composition="Clean composition with one main subject and clear visual hierarchy for contract validation.",
                main_subject="Contemporary smart home",
                human_brief="Represent a smart home with discreet technology in a professional, clean, easy-to-understand composition.",
                technical_prompt="Contemporary smart home, professional architectural visualization, clean composition, subtle integrated technology, realistic lighting, no fake logos.",
                negative_constraints=["No fake logos", "No invented text"],
            )
        elif output_schema is BrandProfilePayload:
            parsed = BrandProfilePayload(
                name="Example Technology Studio",
                core={"purpose": BrandField(value="Turn technology into practical solutions", source="human_confirmed")},
                voice={"tone": BrandField(value="Professional and approachable", source="ai_proposed")},
            )
        else:
            raise ValueError(f"FakeAdapter has no fixture for {output_schema.__name__}")
        return LLMResponse(parsed=parsed, raw_text=parsed.model_dump_json(), provider=self.provider, model="fixture", tokens_in=10, tokens_out=20, latency_ms=1)

    def health(self) -> dict[str, Any]:
        return {"provider": self.provider, "available": True}
