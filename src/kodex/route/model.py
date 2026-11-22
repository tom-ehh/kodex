"""Typed routing decision."""

from dataclasses import dataclass, field, replace
from enum import StrEnum


class Tier(StrEnum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    HARD = "hard"

    def up(self) -> Tier:
        """The next harder tier; HARD stays HARD."""
        order = list(Tier)
        return order[min(order.index(self) + 1, len(order) - 1)]


@dataclass(frozen=True, slots=True)
class Route:
    tier: Tier
    confidence: float  # 0..1; the model's for "basal", a fixed estimate for "heuristic"
    probabilities: dict[str, float] = field(default_factory=dict)
    source: str = "heuristic"  # "basal" | "heuristic"
    escalated: bool = False  # tier was bumped above the raw decision
    reason: str | None = None  # why the fallback was used, or why it escalated

    def bumped(self, reason: str) -> Route:
        if self.tier is Tier.HARD:
            return replace(self, reason=reason)
        return replace(self, tier=self.tier.up(), escalated=True, reason=reason)
