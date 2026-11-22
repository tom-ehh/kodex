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

from coi_formularze.parse.llm import ChatModel, LlmError
from coi_formularze.parse.prompts import prompt, retry_prompt
from coi_formularze.route.model import Route, Tier
from coi_formularze.route.router import escalate, tier_models
from coi_formularze.schema import validate_form_definition
from coi_formularze.schema.assemble import Assembled, DraftError, FormDraft, assemble
from coi_formularze.schema.drafts import FormFacts, Outline, SectionDraft
from coi_formularze.schema.naming import slug
from coi_formularze.schema.states import catalogue, describe, state_set_model

log = logging.getLogger(__name__)


class PassFailed(Exception):
    """A draft pass gave no valid answer after its retry."""


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

    def __init__(self, chat: ChatModel, model: str, md: str, attempt: Attempt, retries: int):
        self.chat, self.model, self.md, self.attempt, self.retries = chat, model, md, attempt, retries

    async def ask(self, task: str, schema_model: type[BaseModel], check=None, **ctx) -> BaseModel:
        schema = schema_model.model_json_schema()
        messages = [{"role": "user", "content": prompt(task, self.md, schema, **ctx)}]
        errors: list[str] = []
        for _ in range(self.retries + 1):
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
        raise PassFailed(f"{task}: {errors[:3]}")

    async def draft(self) -> FormDraft:
        facts = await self.ask("facts", FormFacts)
        outline = await self.ask("outline", Outline, facts=facts.model_dump_json())
        plan = "\n".join(f"- {p.title}: " + "; ".join(s.title for s in p.sections) for p in outline.pages)

        def same_title(want: str):
            return lambda obj: [] if slug(obj.title) == slug(want) else [f"title must be exactly {want!r}"]

        sections = await asyncio.gather(*(
            self.ask("section", SectionDraft, same_title(s.title), section=s.title,
                     purpose=s.purpose, page=p.title, outline=plan)
            for p in outline.pages for s in p.sections
        ))
        entries = catalogue(outline, sections)
        states = []
        if any(e.options for e in entries):
            model = state_set_model(entries)
            states = list((await self.ask("states", model, catalogue=describe(entries))).states)
        return FormDraft(facts=facts, outline=outline, sections=list(sections),
                         states=[s.model_dump() for s in states])


async def parse(md: str, route: Route, settings, chat: ChatModel, retries: int = 1) -> ParseResult:
    models = tier_models(settings)
    attempts: list[Attempt] = []
    while True:
        attempt = Attempt(tier=route.tier.value, model=models[route.tier])
        attempts.append(attempt)
        try:
            draft = await _Run(chat, attempt.model, md, attempt, retries).draft()
            result = assemble(draft)
            issues = validate_form_definition(result.form)
            if not issues:
                return ParseResult(result=result, route=route, attempts=attempts)
            attempt.error = f"invalid form: {json.dumps([i.message for i in issues[:3]], ensure_ascii=False)}"
            log.warning("assembled form invalid (assembler bug?): %s", attempt.error)
        except (PassFailed, DraftError, ValidationError, LlmError) as exc:
            attempt.error = str(exc)[:300]
        if route.tier is Tier.HARD:
            raise ParseFailed(attempts)
        route = escalate(route, f"{attempt.tier} failed: {attempt.error}"[:200])
