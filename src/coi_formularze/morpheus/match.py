"""Trigger matching: which skills apply to a section (labels + validators) or a Document.

Cheap and deterministic: validator equality, label keywords (case- and
diacritics-insensitive substring), then an optional regex over the labels. A skill whose
trigger names nothing never matches. Results are capped and ordered stably.
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache

from coi_formularze.morpheus.models import Layer, Skill, SkillStatus

# More specific layers first: a fresh episodic fix outranks a general rule, both outrank seeds.
_LAYER_RANK = {Layer.EPISODIC: 0, Layer.GENERAL: 1, Layer.SEED: 2}
_FOLD = str.maketrans({"ł": "l", "Ł": "L", "đ": "d", "ø": "o"})  # no NFKD decomposition


def fold(text: str) -> str:
    """Lowercase, strip diacritics, collapse whitespace: 'Kod  POCZTOWY łódź' → 'kod pocztowy lodz'."""
    decomposed = unicodedata.normalize("NFKD", text.translate(_FOLD))
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.casefold().split())


@dataclass(frozen=True)
class SectionTarget:
    """What a skill is matched against: field labels and the validators already chosen."""

    labels: tuple[str, ...] = ()
    validators: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def of(cls, labels: Iterable[str] = (), validators: Iterable[str] = ()) -> SectionTarget:
        return cls(tuple(labels), frozenset(v.upper() for v in validators))


def target_of_document(doc) -> SectionTarget:
    """A Document (extract.model) as a target: its non-empty markdown lines are the labels."""
    return SectionTarget.of(line for line in doc.markdown.splitlines() if line.strip())


@lru_cache(maxsize=256)
def _regex(pattern: str) -> re.Pattern[str] | None:
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return None


def score(skill: Skill, target: SectionTarget) -> int:
    """0 = no match. Validator match 2, keyword 1, regex 1."""
    t = skill.trigger
    s = 0
    if t.validator and t.validator.upper() in target.validators:
        s += 2
    folded = [fold(label) for label in target.labels]
    keys = [fold(k) for k in t.labels_any if k.strip()]
    if keys and any(k in label for k in keys for label in folded):
        s += 1
    if t.regex and (rx := _regex(t.regex)) and any(
        rx.search(label) or rx.search(f) for label, f in zip(target.labels, folded, strict=True)
    ):
        s += 1
    return s


def active_skills(target, skills: Iterable[Skill], max_per_section: int = 5) -> list[Skill]:
    """Active skills matching `target` (a SectionTarget, a Document or a list of labels)."""
    if not isinstance(target, SectionTarget):
        target = (
            target_of_document(target) if hasattr(target, "markdown")
            else SectionTarget.of(target)
        )
    scored = [
        (sc, s) for s in skills
        if s.status is SkillStatus.ACTIVE and (sc := score(s, target)) > 0
    ]
    scored.sort(key=lambda p: (-p[0], _LAYER_RANK[p[1].layer], p[1].id))
    return [s for _, s in scored[:max_per_section]]
