from __future__ import annotations

import json
import re
import unicodedata
from copy import deepcopy
from typing import Any, Mapping
from pydantic import BaseModel

from .base import EffortTranslation, LLMAdapter, LLMResponse, ignored_effort
from ..catalog import ModelSpec
from ..contracts import (
    BrandField, BrandProfilePayload, ContentOutput, GroundingStatus, NewSessionProposal,
    ResearchOutput, StrategyOutput, VisualBriefOutput,
)
from ..routing_policy import EFFORT_LEVELS


_STOPWORDS = set(("de del la las el los un una unos unas y o u e en a al ante con para por que se lo le les me mi mis tu tus "
                  "su sus es son ser fue sobre como mas muy ya hay quiero quiere necesito necesitas quisiera hacer haz trabajar trabajo workflow "
                  "workflows flujo flujos the of and or to for in on with an is are my want need make do it this that").split())


def _words(text: str) -> set[str]:
    return {word for word in re.findall(r"[^\W_]{3,}", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower())
            if word not in _STOPWORDS}


def best_catalog_match(text: str, catalog: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, int]:
    """The catalog entry that shares the most words with ``text`` (ties: first id) and how many it shares."""
    wanted = _words(text)
    best, best_score = None, 0
    for entry in sorted(catalog, key=lambda item: (item["id"], item.get("bundle") or "")):
        score = len(wanted & _words(entry["id"].replace("_", " ") + " " + entry.get("description", "")))
        if score > best_score:
            best, best_score = entry, score
    return best, best_score


def _new_session_fixture(prompt: str) -> dict[str, Any]:
    """Deterministic stand-in for the "New session" interpreter (GUI2-T17): the catalog entry that shares the most
    words with the quoted request (ties: first id), with the request as its first required text input. No match,
    no workflow. It reads only the catalog line and the quoted lines (``| ``) of the prompt. When the request has two
    or more parts (``split_request``) and every part matches a workflow, it also says ``needs_plan``."""
    from ..planning.chat import split_request     # lazy: the planning package imports the services

    catalog: list[dict[str, Any]] = []
    quoted: list[str] = []
    for line in prompt.splitlines():
        if line.startswith("CATALOG_JSON: "):
            catalog = json.loads(line[len("CATALOG_JSON: "):])
        elif line.startswith("| "):
            quoted.append(line[2:])
    request = " ".join(quoted).strip()
    best, best_score = best_catalog_match(request, catalog)
    parts = split_request(request)
    needs_plan = len(parts) >= 2 and all(best_catalog_match(part, catalog)[0] is not None for part in parts)
    if best is None:
        return {"reason": "Simulated: no workflow shares words with the request."}
    target = next((item for item in best["inputs"] if item["required"] and not item["choices"]), None)
    answer = {"workflow_id": best["id"], "bundle": best.get("bundle"),
              "inputs": {target["name"]: request[:500]} if target else {},
              "project_name": "", "reason": f"Simulated: {best_score} shared word(s) with the request."}
    if needs_plan:
        answer["needs_plan"] = True
    return answer


class FakeAdapter(LLMAdapter):
    provider = "fake"

    def __init__(self, *, sample_outputs: Mapping[str, dict[str, Any]] | None = None,
                 output_schemas: Mapping[str, type[BaseModel]] | None = None,
                 supports_effort: bool = True):
        """Accept samples by contract name, resolving aliases through the registry.

        ``supports_effort`` lets tests play a provider that can (default) or cannot express a
        reasoning effort; the simulated setting is ``{"effort": <level>}``.
        """
        self.sample_outputs = deepcopy(dict(sample_outputs or {}))
        self.output_schemas = dict(output_schemas) if output_schemas is not None else None
        self.supports_effort = supports_effort

    def translate_effort(self, spec: ModelSpec, effort: str | None) -> EffortTranslation:
        if effort is None:
            return EffortTranslation(None)
        if effort not in EFFORT_LEVELS:
            return ignored_effort(effort, "unknown_effort")
        if not self.supports_effort:
            return ignored_effort(effort, "provider_does_not_support_effort")
        return EffortTranslation(effort, True, {"effort": effort})

    def generate_structured(self, *, spec: ModelSpec, prompt: str, output_schema: type[BaseModel], requires_web: bool = False,
                            effort: str | None = None) -> LLMResponse:
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
        elif output_schema is NewSessionProposal:
            parsed = NewSessionProposal.model_validate(_new_session_fixture(prompt))
        else:
            raise ValueError(f"FakeAdapter has no fixture for {output_schema.__name__}")
        return LLMResponse(parsed=parsed, raw_text=parsed.model_dump_json(), provider=self.provider, model="fixture", tokens_in=10, tokens_out=20, latency_ms=1,
                           metadata=self.translate_effort(spec, effort).metadata())

    def health(self) -> dict[str, Any]:
        return {"provider": self.provider, "available": True}
