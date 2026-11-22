"""Morpheus data model: skills, episodes and skillset versions (design.md §3).

Stored records hold shapes and patterns only: no extracted text, no field values, no
worker free text. `sanitize.pii_findings` enforces that on every store write.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

NAME_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"  # agentskills.io style


class Layer(StrEnum):
    SEED = "seed"
    EPISODIC = "episodic"
    GENERAL = "general"


class SkillStatus(StrEnum):
    ACTIVE = "active"
    PROPOSED = "proposed"
    RETIRED = "retired"
    ARCHIVED = "archived"


class Actor(StrEnum):
    SEED = "seed"
    WAKE = "wake"
    SLEEP = "sleep"
    HUMAN = "human"


class EpisodeKind(StrEnum):
    VALIDATION_FAILURE = "validation_failure"
    ESCALATION = "escalation"
    WORKER_CORRECTION = "worker_correction"


class EpisodeStatus(StrEnum):
    PENDING = "pending"
    IGNORED = "ignored"
    NEEDS_HUMAN = "needs_human"
    SKILLED = "skilled"


class HitOutcome(StrEnum):
    WIN = "win"
    LOSS = "loss"
    NEUTRAL = "neutral"


# Whitelisted deterministic normalisers; model-written code never runs.
PostFix = Literal["zip_code", "digits_only", "trim", "date_iso"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SkillTrigger(_Model):
    """When a skill applies. Empty trigger never matches (no catch-all skills)."""

    labels_any: list[str] = Field(default_factory=list, max_length=20)
    regex: str | None = Field(None, max_length=200)
    validator: str | None = None
    field_family: str | None = None


class ValidatorRule(_Model):
    """Applied in code after drafting: validators to attach, requiredness, a normaliser."""

    add_validators: list[str] = Field(default_factory=list)
    required: bool | None = None  # None: leave requiredness alone
    post_fix: PostFix | None = None


class Skill(_Model):
    id: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    version: int = Field(1, ge=1)
    name: str = Field(max_length=64, pattern=NAME_PATTERN)
    layer: Layer
    status: SkillStatus = SkillStatus.ACTIVE
    description: str = Field(max_length=160)
    trigger: SkillTrigger
    scope: dict[str, Any] = Field(default_factory=dict)
    pattern: dict[str, str] = Field(default_factory=dict)  # e.g. before/after shapes
    prompt_snippet: str = Field("", max_length=400)
    validator_rule: ValidatorRule | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)  # episode ids, scores; no text
    expires_at: datetime | None = None
    hits: int = 0
    wins: int = 0
    losses: int = 0
    parent_version: int | None = None
    absorbed_into: str | None = None
    pinned: bool = False


class Episode(_Model):
    id: str | None = None
    created_at: datetime | None = None
    kind: EpisodeKind
    form_id: str | None = None
    field_path: str | None = None
    field_label: str | None = Field(None, max_length=200)
    validator: str | None = None
    error: str | None = Field(None, max_length=200)
    before_shape: str | None = Field(None, max_length=200)
    after_shape: str | None = Field(None, max_length=200)
    edited_attr: str | None = None
    tier_from: str | None = None
    tier_to: str | None = None
    skills_used: list[str] = Field(default_factory=list)
    flag: dict[str, Any] | None = None
    status: EpisodeStatus = EpisodeStatus.PENDING
    consolidated: bool = False


class SkillRef(_Model):
    id: str
    version: int


class SkillsetVersion(_Model):
    version: int
    created_at: datetime
    manifest: list[SkillRef]
    reason: str


class SkillEvent(_Model):
    ts: datetime
    skill_id: str | None
    version: int | None
    event: str
    actor: Actor
    detail: dict[str, Any] = Field(default_factory=dict)
