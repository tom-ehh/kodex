"""Skill writer: one tier-model call turns a flagged episode into a skill patch (wake.md §3).

The model answers a small JSON schema (`PatchDraft`); code assembles the stored `Skill`
(id, layer, trigger validator/family, provenance, expiry) so the model never chooses
those. Only whitelisted normalisers (`models.PostFix`) are accepted. The worker's live
`why` may go into the prompt; it is never returned, stored or logged.
"""

import json
import re
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError

from coi_formularze.morpheus.models import (
    Episode,
    Layer,
    PostFix,
    Skill,
    SkillStatus,
    SkillTrigger,
    ValidatorRule,
)
from coi_formularze.parse.llm import ChatModel


class RuleDraft(BaseModel):
    add_validators: list[str] = Field(default=[], description="Z: PESEL, NIP, REGON, ZIP_CODE, EMAIL.")
    required: bool | None = Field(None, description="true/false gdy poprawka zmienia wymagalność, inaczej null.")
    post_fix: PostFix | None = Field(None, description="Normalizacja wartości: zip_code, digits_only, trim, date_iso.")


class PatchDraft(BaseModel):
    name: str = Field(description="kebab-case, ascii, max 48 znaków, np. 'zip-code-hyphen'.")
    description: str = Field(description="Jedno zdanie, max 160 znaków, bez danych osobowych.")
    labels_any: list[str] = Field(description="1-5 słów kluczowych etykiety pola (bez wartości).")
    prompt_snippet: str = Field(description="Wskazówka dla modelu, max 300 znaków, bez wartości.")
    validator_rule: RuleDraft | None = None


ALLOWED_VALIDATORS = {"PESEL", "NIP", "REGON", "ZIP_CODE", "EMAIL"}


class WriterError(ValueError):
    pass


@dataclass
class Written:
    skill: Skill
    parent: Skill | None
    cost: float
    model: str


_PROMPT = """Jesteś modułem Morpheus, który zapisuje krótkie reguły (umiejętności) dla parsera
polskich formularzy urzędowych na podstawie poprawki pracownika. Zwróć WYŁĄCZNIE obiekt JSON
zgodny ze schematem. Nie wpisuj żadnych wartości pól ani danych osobowych: tylko kształty
(# cyfra, A/a litera) i nazwy reguł.

Poprawka (tylko kształty):
- pole: {path}
- etykieta: "{label}"
- rodzina pola: {family}
- walidator: {validator}
- edytowany atrybut: {attr}
- przed: {before}
- po: {after}
- uzasadnienie pracownika (tylko do zrozumienia, nie przepisuj): "{why}"
{existing}
Zasady:
- labels_any: słowa z etykiety, które rozpoznają ten rodzaj pola w innych formularzach;
  nie za ogólne (nie "data", "adres", "nazwa" same w sobie).
- prompt_snippet: jedno-dwa zdania po polsku, co parser ma zrobić z takim polem
  (rodzaj pola kind, required, walidator).
- validator_rule: tylko gdy poprawka dotyczy walidatora, wymagalności lub formatu wartości.
  Gdy poprawka wartości usuwa separatory/spacje/prefiks (zostają same cyfry), ustaw
  post_fix=digits_only; dla kodu pocztowego post_fix=zip_code; dla zbędnych spacji trim.

JSON Schema:
{schema}
"""


def build_prompt(ep: Episode, family: str, why: str | None, parent: Skill | None) -> str:
    existing = ""
    if parent is not None:
        existing = ("\nIstniejąca umiejętność do poprawienia (zwróć jej pełną nową wersję):\n"
                    + json.dumps({"name": parent.name, "description": parent.description,
                                  "labels_any": parent.trigger.labels_any,
                                  "prompt_snippet": parent.prompt_snippet,
                                  "validator_rule": parent.validator_rule.model_dump()
                                  if parent.validator_rule else None}, ensure_ascii=False) + "\n")
    return _PROMPT.format(path=ep.field_path, label=ep.field_label or "", family=family,
                          validator=ep.validator or "(brak)", attr=ep.edited_attr or "value",
                          before=ep.before_shape or "(brak)", after=ep.after_shape or "(brak)",
                          why=(why or "").strip()[:500], existing=existing,
                          schema=json.dumps(PatchDraft.model_json_schema(), ensure_ascii=False))


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s or "skill")[:48].strip("-")


def assemble(draft: PatchDraft, ep: Episode, family: str, provenance: dict,
             parent: Skill | None) -> Skill:
    """Code builds the record; only the draft's text fields come from the model."""
    rule = None
    if draft.validator_rule is not None:
        r = draft.validator_rule
        adds = [v.upper() for v in r.add_validators if v.upper() in ALLOWED_VALIDATORS]
        if adds or r.required is not None or r.post_fix:
            rule = ValidatorRule(add_validators=adds, required=r.required, post_fix=r.post_fix)
    labels = [x.strip()[:60] for x in draft.labels_any if x.strip()][:5]
    trigger = SkillTrigger(labels_any=labels, validator=ep.validator,
                           field_family=family if family != "other" else None)
    name = parent.name if parent else _slug(draft.name)
    return Skill(
        id=parent.id if parent else f"ep-{name}"[:64],
        name=name, layer=Layer.EPISODIC, status=SkillStatus.PROPOSED,
        description=draft.description.strip()[:160] or name,
        trigger=trigger, scope={"family": family},
        pattern={k: v for k, v in (("before", ep.before_shape), ("after", ep.after_shape),
                                   ("attr", ep.edited_attr)) if v},
        prompt_snippet=draft.prompt_snippet.strip()[:400], validator_rule=rule,
        provenance=provenance,
    )


async def write_skill(chat: ChatModel, model: str, ep: Episode, family: str, why: str | None,
                      parent: Skill | None, provenance: dict) -> Written:
    schema = PatchDraft.model_json_schema()
    prompt = build_prompt(ep, family, why, parent)
    c = await chat.complete(model, [{"role": "user", "content": prompt}], schema)
    text = c.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        draft = PatchDraft.model_validate_json(text)
        skill = assemble(draft, ep, family, provenance | {"model": model}, parent)
    except ValidationError as exc:
        raise WriterError(f"writer answer invalid: {len(exc.errors())} error(s)") from None
    return Written(skill=skill, parent=parent, cost=c.cost, model=model)
