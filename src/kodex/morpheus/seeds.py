"""Seed skills shipped in the package (`seeds/*.json`, read-only in the image)."""

import json
from importlib import resources

from kodex.morpheus.models import Actor, Layer, Skill, SkillStatus
from kodex.morpheus.sanitize import assert_clean


def load_seed_skills() -> list[Skill]:
    """Parse and lint every seed file; sorted by id. Raises on an invalid seed."""
    out = []
    for f in resources.files("kodex.morpheus").joinpath("seeds").iterdir():
        if f.name.endswith(".json"):
            data = json.loads(f.read_text(encoding="utf-8"))
            assert_clean(data)
            skill = Skill.model_validate(data | {"layer": Layer.SEED})
            out.append(skill)
    ids = [s.id for s in out]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate seed ids")
    return sorted(out, key=lambda s: s.id)


def install_seeds(store) -> list[str]:
    """Add seeds the store doesn't have yet (one skillset version). Returns the new ids.

    Existing ids are left alone: a learned patch of a seed is never overwritten."""
    new = [s for s in load_seed_skills() if store.get(s.id) is None]
    if not new:
        return []
    with store.batch(f"install seeds: {', '.join(s.id for s in new)}"):
        for s in new:
            store.create(s.model_copy(update={"status": SkillStatus.ACTIVE}), actor=Actor.SEED)
    return [s.id for s in new]
