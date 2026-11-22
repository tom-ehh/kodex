"""Extracted markdown -> Form Definition: draft passes, assembly, validation, escalation.

Per tier model: FormFacts -> Outline -> one SectionDraft per section (in parallel) ->
states. A pass whose answer fails its schema gets one retry with the errors fed back.
If a pass still fails, or the assembled form fails `validate_form_definition`, the
whole form goes one tier up; failing at `hard` raises `ParseFailed`.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from kodex.morpheus.hooks import Hooks, form_shape, section_slice
from kodex.morpheus.models import HitOutcome
from kodex.parse.llm import ChatModel, LlmError
from kodex.parse.prompts import prompt, retry_prompt
from kodex.route.model import Route, Tier
from kodex.route.router import escalate, tier_models
from kodex.schema import validate_form_definition
from kodex.schema.assemble import Assembled, DraftError, FormDraft, assemble
from kodex.schema.drafts import FormFacts, Outline, SectionDraft
from kodex.schema.naming import slug
from kodex.schema.states import (
    StateDraft,
    catalogue,
    describe,
    repair_states,
    state_problems,
    state_set_model,
)

log = logging.getLogger(__name__)


class PassFailed(Exception):
    """A draft pass gave no valid answer after its retry."""

    def __init__(self, task: str, errors: list[str]):
        super().__init__(f"{task}: {errors[:3]}")
        self.task, self.errors = task, errors


class ParseFailed(Exception):
    """Every tier up to `hard` failed; `attempts` says how."""

    def __init__(self, attempts: list[Attempt]):
        super().__init__("; ".join(f"{a.tier}: {a.error}" for a in attempts))
        self.attempts = attempts


@dataclass
class Attempt:
    tier: str
    model: str
    calls: int = 0
    cost: float = 0.0
    error: str | None = None


@dataclass
class ParseResult:
    result: Assembled
    route: Route
    attempts: list[Attempt] = field(default_factory=list)


def _parse(model: type[BaseModel], text: str) -> tuple[BaseModel | None, list[str]]:
    try:
        return model.model_validate_json(_strip_fence(text)), []
    except ValidationError as exc:
        return None, [f"{'.'.join(map(str, e['loc'])) or '<root>'}: {e['msg']}" for e in exc.errors()]


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    return t.strip()


class _Run:
    """One attempt at one tier model."""

    def __init__(self, chat: ChatModel, model: str, md: str, attempt: Attempt, retries: int,
                 hooks: Hooks | None = None):
        self.chat, self.model, self.md, self.attempt, self.retries = chat, model, md, attempt, retries
        self.hooks = hooks

    async def ask(self, task: str, schema_model: type[BaseModel], check=None, hints: str = "",
                  stats: dict | None = None, **ctx) -> BaseModel:
        schema = schema_model.model_json_schema()
        messages = [{"role": "user", "content": prompt(task, self.md, schema, hints, **ctx)}]
        errors: list[str] = []
        for n in range(self.retries + 1):
            if stats is not None:
                stats["tries"] = n + 1
            c = await self.chat.complete(self.model, messages, schema)
            self.attempt.calls += 1
            self.attempt.cost += c.cost
            obj, errors = _parse(schema_model, c.text)
            if obj is not None and check:
                errors = check(obj)
                obj = None if errors else obj
            if obj is not None:
                return obj
            messages += [{"role": "assistant", "content": c.text[:20000]},
                         {"role": "user", "content": retry_prompt(errors)}]
        if self.hooks and task != "section":  # sections record their own failure
            self.hooks.record_validation_failure(self.attempt.tier, task, None, errors)
        raise PassFailed(task, errors)

    async def section(self, page: str, sec, plan: str, titles: tuple = ()) -> SectionDraft:
        """One SectionDraft, with matching Morpheus skills as hints when enabled."""
        def same_title(obj):
            return [] if slug(obj.title) == slug(sec.title) else [f"title must be exactly {sec.title!r}"]

        h = self.hooks
        source = section_slice(self.md, sec.title, titles) if h else ""
        skills = h.section_skills(sec.title, sec.purpose, source) if h else []
        if h:
            h.used_now(skills)
        stats: dict = {}
        try:
            out = await self.ask("section", SectionDraft, same_title, hints=Hooks.hints(skills),
                                 stats=stats, section=sec.title, purpose=sec.purpose,
                                 page=page, outline=plan)
        except PassFailed as exc:
            if h:
                h.hit(skills, HitOutcome.LOSS)
                h.record_validation_failure(self.attempt.tier, slug(sec.title), sec.title,
                                            exc.errors, skills)
            raise
        if h:
            h.hit(skills, HitOutcome.WIN if stats.get("tries") == 1 else HitOutcome.NEUTRAL)
        return out

    async def draft(self) -> FormDraft:
        facts = await self.ask("facts", FormFacts)
        outline = await self.ask("outline", Outline, facts=facts.model_dump_json())
        titles = tuple(s.title for p in outline.pages for s in p.sections)
        plan = "\n".join(f"- {p.title}: " + "; ".join(s.title for s in p.sections) for p in outline.pages)
        sections = await asyncio.gather(*(
            self.section(p.title, s, plan, titles) for p in outline.pages for s in p.sections
        ))
        entries = catalogue(outline, sections)
        states = []
        if any(e.options for e in entries):
            model = state_set_model(entries)

            def repaired(obj) -> list[StateDraft]:
                return repair_states([StateDraft.model_validate(s.model_dump()) for s in obj.states], entries)

            answer = await self.ask("states", model, lambda obj: state_problems(repaired(obj), entries),
                                    catalogue=describe(entries))
            states = repaired(answer)
        return FormDraft(facts=facts, outline=outline, sections=list(sections),
                         states=[s.model_dump() for s in states])


async def parse(md: str, route: Route, settings, chat: ChatModel, retries: int = 1,
                hooks: Hooks | None = None) -> ParseResult:
    """`hooks`: Morpheus plug-in points (skills in, episodes out); None = Morpheus off."""
    models = tier_models(settings)
    attempts: list[Attempt] = []
    while True:
        attempt = Attempt(tier=route.tier.value, model=models[route.tier])
        attempts.append(attempt)
        applied: list = []
        try:
            draft = await _Run(chat, attempt.model, md, attempt, retries, hooks).draft()
            post = None
            if hooks:
                def post(fd, built, section, applied=applied):
                    used = hooks.apply_rules(fd.label, built, section)
                    hooks.used_now(used)
                    applied.extend(used)
            result = assemble(draft, post)
            issues = validate_form_definition(result.form)
            if hooks:
                hooks.hit(applied, HitOutcome.LOSS if issues else HitOutcome.WIN)
            if not issues:
                if hooks and len(attempts) > 1:
                    prev = attempts[-2]
                    hooks.record_escalation(prev.tier, attempt.tier, prev.error,
                                            f"failed:{prev.tier}", form_shape(result.form))
                return ParseResult(result=result, route=route, attempts=attempts)
            attempt.error = f"invalid form: {json.dumps([i.message for i in issues[:3]], ensure_ascii=False)}"
            log.warning("assembled form invalid (assembler bug?): %s", attempt.error)
        except (PassFailed, DraftError, ValidationError, LlmError) as exc:
            attempt.error = str(exc)[:300]
        if route.tier is Tier.HARD:
            if hooks:
                hooks.record_needs_review(attempt.tier, attempt.error)
            raise ParseFailed(attempts)
        route = escalate(route, f"{attempt.tier} failed: {attempt.error}"[:200])
