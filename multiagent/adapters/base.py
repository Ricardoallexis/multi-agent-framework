from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from ..catalog import ModelSpec
from ..routing_policy import EFFORT_LEVELS


@dataclass
class LLMResponse:
    parsed: BaseModel
    raw_text: str
    provider: str
    model: str
    model_digest: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    sources: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EffortTranslation:
    """What one provider does with a requested reasoning effort.

    ``requested`` is the step's effort (None when none was asked for). ``applied`` is
    True only when the provider call carries a matching setting; ``setting`` holds
    those provider parameters (``None`` when the effort is ignored) and ``note`` says
    why it was ignored. An unsupported effort never fails the call.
    """

    requested: str | None
    applied: bool = False
    setting: dict[str, Any] | None = None
    note: str | None = None

    def metadata(self) -> dict[str, Any]:
        """Entries for ``LLMResponse.metadata``; empty when no effort was requested."""
        if self.requested is None:
            return {}
        return {
            "effort_requested": self.requested,
            "effort_applied": self.applied,
            "effort_setting": dict(self.setting) if self.setting is not None else None,
            "effort_note": self.note,
        }


def ignored_effort(effort: str | None, note: str) -> EffortTranslation:
    """Translation for a provider (or model) that cannot express ``effort``."""
    return EffortTranslation(effort, False, None, note)


class LLMAdapter(ABC):
    provider: str

    @abstractmethod
    def generate_structured(self, *, spec: ModelSpec, prompt: str, output_schema: type[BaseModel], requires_web: bool = False,
                            effort: str | None = None) -> LLMResponse:
        """Return one structured response.

        ``effort`` (one of ``EFFORT_LEVELS``, or None for the provider default) is
        translated by :meth:`translate_effort`; callers pass it only when set.
        """
        raise NotImplementedError

    def translate_effort(self, spec: ModelSpec, effort: str | None) -> EffortTranslation:
        """Pure mapping from a generic effort to this provider's request setting.

        The default ignores every effort; adapters that can express it override this
        and use the result to build their request, so the request and the record agree.
        """
        if effort is None:
            return EffortTranslation(None)
        if effort not in EFFORT_LEVELS:
            return ignored_effort(effort, "unknown_effort")
        return ignored_effort(effort, "provider_does_not_support_effort")

    @abstractmethod
    def health(self) -> dict[str, Any]:
        raise NotImplementedError
