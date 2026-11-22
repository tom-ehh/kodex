"""Morpheus P1: the pipeline's plug-in points (design.md §1).

`Hooks` is created per analyze request when Morpheus is enabled; the pipeline calls it
at fixed points and never otherwise. With `hooks=None` the pipeline is unchanged.

- **Skills in:** `section_skills` picks active skills for a section (title + purpose)
  and `hints` renders their prompt snippets as a fixed "Wskazówki" block;
  `apply_rules` applies matched `validator_rule`s in code after `kinds.build`.
- **Episodes out:** `record_*` write shape-only episodes (PII lint in the store).
- **Hits:** one skill hit per use; outcome from whether the section/field validated.

Nothing here may break a request: store errors are logged and swallowed.
"""

import json
import logging
import re
from collections.abc import Iterable

from kodex.morpheus.match import SectionTarget, active_skills, scope_ok
from kodex.morpheus.models import (
    Episode,
    EpisodeKind,
    EpisodeStatus,
    HitOutcome,
    Skill,
)
from kodex.morpheus.sanitize import pii_findings, shape
from kodex.morpheus.store import SkillStore

log = logging.getLogger(__name__)

HINTS_HEADER = "Wskazówki (sprawdzone reguły dla takich pól):"
_TEXT_TYPES = frozenset({"TEXT_FIELD"})  # only these take non-REQUIRED validators


def _clean(text: str | None, limit: int = 200) -> str | None:
    """Keep `text` if it passes the PII lint, else its shape (digits never survive)."""
    if text is None:
        return None
    text = text[:limit]
    return text if not pii_findings(text) else shape(text, limit)


clean_text = _clean  # public alias for the API layer


class Hooks:
    def __init__(self, store: SkillStore, form_id: str | None = None, max_per_section: int = 5):
        self.store, self.form_id, self.max = store, form_id, max_per_section
        self._skills = store.active()
        self.used: dict[str, int] = {}  # skill id -> times injected or applied (this request)
        self._sections: dict[str, tuple[str, str]] = {}  # title -> (where, own) for scoped rules
        self.episodes: list[Episode] = []

    # ---- skills in ----

    def section_skills(self, title: str, purpose: str = "", source: str = "") -> list[Skill]:
        """Skills with a prompt snippet whose trigger matches the section's title/purpose
        or a line of its own source-text slice (`section_slice`); capped as before."""
        lines = [ln for ln in source.splitlines() if ln.strip()]
        target = SectionTarget.of([t for t in (title, purpose) if t] + lines)
        own = f"{title}\n{purpose}"
        where = f"{own}\n{source}"
        self._sections[title] = (where, own)
        pool = [s for s in self._skills if scope_ok(s, where, own)]
        return [s for s in active_skills(target, pool, self.max) if s.prompt_snippet]

    @staticmethod
    def hints(skills: Iterable[Skill]) -> str:
        lines = [f"- {s.prompt_snippet}" for s in skills]
        return f"{HINTS_HEADER}\n" + "\n".join(lines) if lines else ""

    def rule_skills(self, label: str, validators: Iterable[str],
                    section: str | None = None) -> list[Skill]:
        """Rule skills for one field. Scoped skills (ZIP) need the field's `section`:
        its text decides (address cues), and the section title or the label must not
        say foreign ("zagraniczny")."""
        target = SectionTarget.of([label], validators)
        ctx = self._sections.get(section) if section is not None else None
        if ctx is None and section is not None:
            ctx = (section, section)
        where, own = (ctx[0] + "\n" + label, ctx[1] + "\n" + label) if ctx else (None, None)
        pool = [s for s in self._skills if scope_ok(s, where, own)]
        return [s for s in active_skills(target, pool, self.max) if s.validator_rule]

    def apply_rules(self, label: str, questions: list[dict], section: str | None = None) -> list[Skill]:
        """Apply matched validator rules to one built field (questions from kinds.build).

        Rules: `add_validators` (TEXT_FIELD only; REQUIRED goes through `required`),
        `required` true/false (the highest-ranked skill that sets it wins), `post_fix`
        (a whitelisted value normaliser: a Form Definition holds no values, so it is
        recorded for fill-time use and changes nothing here). A label matching skills of
        two field families (a combined 'telefon / e-mail' box) gets no family rules.
        Returns the skills applied.
        """
        field = questions[0]["field"]
        if "validation" not in field:
            return []
        skills = self.rule_skills(label, field["validation"], section)
        families = {s.trigger.field_family for s in skills if s.trigger.field_family}
        if len(families) > 1:  # 'telefon / e-mail': one field, two families -> leave it alone
            skills = [s for s in skills if not s.trigger.field_family]
        if not skills:
            return []
        val: list[str] = list(field["validation"])
        decided = None
        for s in skills:
            rule = s.validator_rule
            if field["type"] in _TEXT_TYPES:
                val += [v for v in rule.add_validators if v != "REQUIRED" and v not in val]
            if decided is None and rule.required is not None:
                decided = rule.required
        if decided is True and "REQUIRED" not in val:
            val.insert(0, "REQUIRED")
        elif decided is False:
            val = [v for v in val if v != "REQUIRED"]
        field["validation"] = val
        return skills

    def used_now(self, skills: Iterable[Skill]) -> None:
        for s in skills:
            self.used[s.id] = self.used.get(s.id, 0) + 1

    # ---- hits ----

    def hit(self, skills: Iterable[Skill], outcome: HitOutcome) -> None:
        for s in skills:
            try:
                self.store.record_hit(s.id, s.version, outcome, self.form_id)
            except Exception:  # never break the request
                log.exception("morpheus: record_hit failed")

    # ---- episodes out ----

    def _add(self, ep: Episode) -> None:
        try:
            self.episodes.append(self.store.add_episode(ep))
        except Exception:
            log.exception("morpheus: episode not stored")

    def record_validation_failure(self, tier: str, path: str, label: str | None,
                                  errors: list[str], skills: Iterable[Skill] = ()) -> None:
        """A pass still failing after its retry."""
        err = errors[0] if errors else None
        self._add(Episode(
            kind=EpisodeKind.VALIDATION_FAILURE, form_id=self.form_id, field_path=_clean(path),
            field_label=_clean(label), validator=_validator_in(err), error=_clean(err),
            tier_from=tier, skills_used=sorted({s.id for s in skills}),
        ))

    def record_escalation(self, tier_from: str, tier_to: str, error: str | None,
                          before: str, after: str) -> None:
        """A failed tier followed by a tier that produced a valid form (shape diff)."""
        self._add(Episode(
            kind=EpisodeKind.ESCALATION, form_id=self.form_id, error=_clean(error),
            before_shape=_clean(before), after_shape=_clean(after),
            tier_from=tier_from, tier_to=tier_to, skills_used=sorted(self.used),
        ))

    def record_needs_review(self, tier: str, error: str | None) -> None:
        """Every tier up to `hard` failed: the form needs a human."""
        self._add(Episode(
            kind=EpisodeKind.VALIDATION_FAILURE, form_id=self.form_id, error=_clean(error),
            tier_from=tier, skills_used=sorted(self.used), status=EpisodeStatus.NEEDS_HUMAN,
        ))


SLICE_MAX_LINES, SLICE_MAX_CHARS = 60, 4000


def section_slice(md: str, title: str, other_titles: Iterable[str] = ()) -> str:
    """The section's own part of the markdown: from the first line containing its title
    up to the next line containing another section's title (capped). '' if not found."""
    from kodex.morpheus.match import fold

    lines = md.splitlines()
    want = fold(title)
    others = [fold(t) for t in other_titles if t and fold(t) != want and len(fold(t)) > 3]
    start = next((i for i, ln in enumerate(lines) if want and want in fold(ln)), None)
    if start is None:
        return ""
    out = []
    for ln in lines[start + 1:start + 1 + SLICE_MAX_LINES]:
        f = fold(ln)
        if any(o in f for o in others):
            break
        out.append(ln)
    return "\n".join(out)[:SLICE_MAX_CHARS]


_VALIDATOR_RX = re.compile(r"\b(REQUIRED|PESEL|NIP|REGON|ZIP_CODE|EMAIL)\b")


def _validator_in(error: str | None) -> str | None:
    m = _VALIDATOR_RX.search(error or "")
    return m.group(1) if m else None


def form_shape(form: dict) -> str:
    """A form's structure without its text: one letter per field type, '|' between tabs."""
    abbrev = {"TEXT_FIELD": "t", "TEXTAREA": "l", "NUMBER": "n", "DATEPICKER": "d",
              "FILE_UPLOADER": "f", "CHECKBOX": "c", "RADIO": "r", "SELECT": "s",
              "TEXT_BLOCK": "i", "INFO_SECTION": "i", "SECTION": "S", "REPEATING_SECTION": "R"}

    def walk(q: dict) -> str:
        f = q["field"]
        kids = "".join(walk(c) for c in f.get("repeatingSectionGrid") or [])
        code = abbrev.get(f["type"], "?")
        return f"{code}[{kids}]" if kids else code

    return "|".join("".join(walk(q) for q in t["questions"]) for t in form.get("tabs", []))[:200]


def _attr_shape(attr: str, value) -> str | None:
    """Values become shapes; structural attrs (type, validator, required…) stay readable."""
    if value is None:
        return None
    if attr == "value":
        return shape(str(value), 200)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return _clean(text)


def _label_of(form: dict | None, path: str) -> str | None:
    tech = path.rsplit("/", 1)[-1].rsplit(".", 1)[-1]

    def walk(q):
        f = q["field"]
        if f.get("techName") == tech:
            return f.get("title")
        for c in f.get("repeatingSectionGrid") or []:
            if (t := walk(c)) is not None:
                return t
        return None

    for tab in (form or {}).get("tabs", []):
        for q in tab.get("questions", []):
            if (t := walk(q)) is not None:
                return t
    return None


def record_corrections(store: SkillStore, form_id: str, form: dict | None, edits,
                       learn_now: bool = False) -> list[Episode]:
    """One `worker_correction` episode per edit. The worker's "why" is never passed here."""
    out = []
    for e in edits:
        out.append(store.add_episode(Episode(
            kind=EpisodeKind.WORKER_CORRECTION, form_id=form_id, field_path=_clean(e.field_path),
            field_label=_clean(_label_of(form, e.field_path)), edited_attr=e.attr,
            before_shape=_attr_shape(e.attr, e.before), after_shape=_attr_shape(e.attr, e.after),
            validator=_validator_in(_attr_shape(e.attr, e.after)) if e.attr == "validator" else None,
            flag={"learn_now": True} if learn_now else None,
        )))
    return out
