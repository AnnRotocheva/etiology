from dataclasses import dataclass
from enum import Enum


class ModelTier(str, Enum):
    """Уровень мощности модели, не привязанный к провайдеру или конкретной модели.

    Triage Agent — FAST, Diagnostic Collector — STANDARD, Bug Report Composer /
    Post-mortem — STRONG (см. docs/architecture.md §4, §6.2).
    """

    FAST = "fast"
    STANDARD = "standard"
    STRONG = "strong"


@dataclass
class ModelMessage:
    role: str  # "user" | "assistant"
    content: str


@dataclass
class ModelRequest:
    tier: ModelTier
    messages: list[ModelMessage]
    system: str | None = None
    max_tokens: int = 1024


@dataclass
class ModelResponse:
    content: str
    stop_reason: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
