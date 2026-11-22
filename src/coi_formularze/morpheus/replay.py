"""Section replay: re-draft ONE section of a cached form with a given skill set.

Small public API, shared by wake (P2 light gate) and sleep (P3 replay gate):

- `SectionRef(page, title, purpose)`: which section to draft.
- `locate(form, field_path) -> (SectionRef, label) | None`: the section holding a field
  of an assembled Form Definition (by techName, the last path segment).
- `await replay_section(chat, model, md, section, skills, ...) -> SectionReplay`: run the
  SectionDraft pass (prompt hints from `skills`, matched as in the live pipeline) and
  build every field with `kinds.build` + the skills' validator rules. No store writes.
- `find_field(fields, label)`: the replayed field whose label best matches.
- `check_correction(field, attr, after_shape, skills)`: does the replayed field carry the
  worker's correction (and pass its validator)?

The Document text is never stored; callers pass the cached markdown in.
"""

import time
from dataclasses import dataclass, field

from coi_formularze.morpheus.hooks import Hooks
from coi_formularze.morpheus.match import fold
from coi_formularze.morpheus.models import Skill
from coi_formularze.parse.llm import ChatModel, LlmError
from coi_formularze.schema.kinds import Context, build
from coi_formularze.schema.naming import slug

_SECTIONS = {"SECTION", "REPEATING_SECTION"}


@dataclass(frozen=True)
class SectionRef:
    page: str
    title: str
    purpose: str = ""


@dataclass
class SectionReplay:
    ok: bool
    fields: list[dict] = field(default_factory=list)  # built `field` dicts, rules applied
    errors: list[str] = field(default_factory=list)
    skills_used: list[str] = field(default_factory=list)
    calls: int = 0
    cost: float = 0.0
    latency_s: float = 0.0


class FixedHooks(Hooks):
    """Hooks over a fixed skill list: matching only, never writes to a store."""

    def __init__(self, skills: list[Skill], max_per_section: int = 5):
        self.store, self.form_id, self.max = None, None, max_per_section  # type: ignore[assignment]
        self._skills = list(skills)
        self.used, self.episodes, self._sections = {}, [], {}

    def hit(self, skills, outcome) -> None:
        pass

    def _add(self, ep) -> None:
        pass


def locate(form: dict | None, field_path: str | None) -> tuple[SectionRef, str] | None:
    if not form or not field_path:
        return None
    tech = field_path.rsplit("/", 1)[-1].rsplit(".", 1)[-1]

    def walk(q, page, section):
        f = q["field"]
        if f["type"] in _SECTIONS:
            section = f.get("title") or section
        if f.get("techName") == tech and f["type"] not in _SECTIONS:
            return SectionRef(page, section or page), f.get("title") or ""
        for c in f.get("repeatingSectionGrid") or []:
            if (hit := walk(c, page, section)) is not None:
                return hit
        return None

    for tab in form.get("tabs", []):
        for q in tab.get("questions", []):
            if (hit := walk(q, tab.get("title", ""), None)) is not None:
                return hit
    return None


async def replay_section(chat: ChatModel, model: str, md: str, section: SectionRef,
                         skills: list[Skill], max_per_section: int = 5,
                         retries: int = 1) -> SectionReplay:
    from coi_formularze.parse.pipeline import (  # avoid an import cycle
        Attempt,
        PassFailed,
        _Run,
    )

    hooks = FixedHooks(skills, max_per_section)
    attempt = Attempt(tier="replay", model=model)
    run = _Run(chat, model, md, attempt, retries, hooks)
    t0 = time.monotonic()
    try:
        draft = await run.section(section.page, _Sec(section.title, section.purpose), "")
    except PassFailed as exc:
        return SectionReplay(ok=False, errors=exc.errors[:5], skills_used=sorted(hooks.used),
                             calls=attempt.calls, cost=attempt.cost,
                             latency_s=round(time.monotonic() - t0, 2))
    except (LlmError, ValueError) as exc:  # model service down, bad draft
        return SectionReplay(ok=False, errors=[type(exc).__name__], calls=attempt.calls,
                             cost=attempt.cost, latency_s=round(time.monotonic() - t0, 2))
    ctx = Context(tab=slug(section.page or "tab"), section=slug(section.title), repeating=False,
                  taken=set())
    fields: list[dict] = []
    for fd in draft.fields:
        built = build(fd, ctx)
        hooks.used_now(hooks.apply_rules(fd.label, built, section.title))
        fields += [q["field"] for q in built]
    return SectionReplay(ok=True, fields=fields, skills_used=sorted(hooks.used),
                         calls=attempt.calls, cost=attempt.cost,
                         latency_s=round(time.monotonic() - t0, 2))


@dataclass
class _Sec:
    title: str
    purpose: str


def find_field(fields: list[dict], label: str | None) -> dict | None:
    """Exact folded label, else the shortest field label containing it (or contained)."""
    if not label:
        return None
    want = fold(label)
    cands = [f for f in fields if f.get("title")]
    for f in cands:
        if fold(f["title"]) == want:
            return f
    near = [f for f in cands if want in fold(f["title"]) or fold(f["title"]) in want]
    return min(near, key=lambda f: len(f["title"])) if near else None


def post_fix_shape(fix: str | None, value_shape: str) -> str:
    """A whitelisted normaliser applied to a shape ('#####' -> '##-###' for zip_code)."""
    if fix == "zip_code":
        digits = value_shape.replace("-", "").replace(" ", "")
        return f"{digits[:2]}-{digits[2:]}" if digits == "#" * 5 else value_shape
    if fix == "digits_only":
        return "".join(c for c in value_shape if c == "#")
    if fix == "trim":
        return value_shape.strip()
    return value_shape


def check_correction(f: dict | None, attr: str | None, before: str | None, after: str | None,
                     skills: list[Skill]) -> tuple[bool, str]:
    """True when the replayed field matches the worker's correction. Returns (ok, reason)."""
    if f is None:
        return False, "field not found in replay"
    val = f.get("validation") or []
    after = (after or "").strip()
    if attr == "required":
        want = after.lower() in ("true", "1", "tak")
        return ("REQUIRED" in val) == want, f"required={'REQUIRED' in val}"
    if attr == "validator":
        wanted = {v for v in ("REQUIRED", "PESEL", "NIP", "REGON", "ZIP_CODE", "EMAIL") if v in after}
        missing = wanted - set(val)
        return not missing, f"validation={val}"
    if attr == "type":
        return f.get("type") == after.strip('"'), f"type={f.get('type')}"
    if attr in ("label", "options"):
        return True, "structural edit: field present"
    # value edit: a whitelisted normaliser must turn the before shape into the after shape
    for s in skills:
        rule = s.validator_rule
        if rule and rule.post_fix and post_fix_shape(rule.post_fix, before or "") == after:
            return True, f"post_fix {rule.post_fix}"
    return False, "no normaliser reproduces the correction"
