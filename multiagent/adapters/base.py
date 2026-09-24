from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from ..catalog import ModelSpec


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


class LLMAdapter(ABC):
    provider: str

    @abstractmethod
    def generate_structured(self, *, spec: ModelSpec, prompt: str, output_schema: type[BaseModel], requires_web: bool = False) -> LLMResponse:
        raise NotImplementedError

    @abstractmethod
    def health(self) -> dict[str, Any]:
        raise NotImplementedError
