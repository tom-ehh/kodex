"""Morpheus: sleep-inspired skill learning for the form parser (P0: store + matching).

Inspired by Nous Research's Hermes Agent and AI sleep research; not Hermes Agent itself.
Nothing here runs unless `COI_MORPHEUS_ENABLED=true`; `open_store` returns None otherwise
and never touches the database path.
"""

from coi_formularze.morpheus.match import SectionTarget, active_skills, fold
from coi_formularze.morpheus.models import (
    Actor,
    Episode,
    EpisodeKind,
    EpisodeStatus,
    HitOutcome,
    Layer,
    Skill,
    SkillEvent,
    SkillRef,
    SkillsetVersion,
    SkillStatus,
    SkillTrigger,
    ValidatorRule,
)
from coi_formularze.morpheus.sanitize import PIIError, pii_findings, shape
from coi_formularze.morpheus.seeds import install_seeds, load_seed_skills
from coi_formularze.morpheus.store import PinnedError, SkillNotFound, SkillStore


def open_store(settings) -> SkillStore | None:
    """The store for these settings with seeds installed, or None when Morpheus is off."""
    if not settings.morpheus_enabled:
        return None
    store = SkillStore(settings.morpheus_db, snapshot=settings.skills_snapshot)
    if settings.skills_snapshot is None:
        install_seeds(store)
    return store


__all__ = [
    "Actor", "Episode", "EpisodeKind", "EpisodeStatus", "HitOutcome", "Layer", "PIIError",
    "PinnedError", "SectionTarget", "Skill", "SkillEvent", "SkillNotFound", "SkillRef",
    "SkillStatus", "SkillStore", "SkillTrigger", "SkillsetVersion", "ValidatorRule",
    "active_skills", "fold", "install_seeds", "load_seed_skills", "open_store",
    "pii_findings", "shape",
]
