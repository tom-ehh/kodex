"""Semantic field kinds -> components, validators and dictionaries, per the standards.

The model says what a field *is* (`pesel`, `yes_no`, `country`…); code picks the
widget: radio for up to 5 options, select above that, searchable above 15;
validators for identifiers; dictionaries for countries and administrative units.

Output is plain question dicts (`{"field": {...}}`, no ids or grid yet); assemble.py
places them and form.py validates the result.
"""

from dataclasses import dataclass, field
from typing import Any

from kodex.schema.drafts import FieldDraft
from kodex.schema.naming import slug, unique
from kodex.schema.states import (
    OTHER_OPTION,
    Equals,
    Location,
    field_options,
    to_jsonata,
)

RADIO_MAX = 5
SEARCH_ABOVE = 15

SIMPLE = {
    "text": ("TEXT_FIELD", []), "long_text": ("TEXTAREA", []), "number": ("NUMBER", []),
    "date": ("DATEPICKER", []), "file": ("FILE_UPLOADER", []),
    "pesel": ("TEXT_FIELD", ["PESEL"]), "nip": ("TEXT_FIELD", ["NIP"]),
    "regon": ("TEXT_FIELD", ["REGON"]), "krs": ("TEXT_FIELD", []),
    "email": ("TEXT_FIELD", ["EMAIL"]), "phone": ("TEXT_FIELD", []),
    "zip_code": ("TEXT_FIELD", ["ZIP_CODE"]),
}  # fmt: skip
DICTIONARIES = {  # kind -> (dictionary, searchable)
    "country": ("Kraje", True), "voivodeship": ("Województwo", False),
    "county": ("Powiat", False), "commune": ("Gmina", True), "locality": ("Miejscowość", True),
}  # fmt: skip


@dataclass
class Context:
    """Where a section's fields go; collects tech names across the whole form."""

    tab: str
    section: str  # section techName
    repeating: bool
    taken: set[str]
    techs: list[str] = field(default_factory=list)  # tech names made for the last draft

    def name(self, base: str) -> str:
        tech = unique(base, self.taken, self.section)
        self.techs.append(tech)
        return tech

    def at(self, tech: str) -> Location:
        return Location(self.tab, self.section, tech, self.repeating)


def make_field(type_: str, tech: str, title: str | None = None, **extra: Any) -> dict:
    out: dict = {"type": type_, "techName": tech}
    if title:
        out["title"] = title
    out.update({k: v for k, v in extra.items() if v not in (None, "")})
    out.setdefault("states", [])
    return out


def options(names) -> list[dict]:
    return [{"id": n, "name": n, "techName": slug(n)} for n in names]


def _validators(draft: FieldDraft, own: list[str]) -> list[str]:
    out = ["REQUIRED"] if draft.required else []
    for v in [*own, *draft.extra_validators]:
        if v not in out:
            out.append(v)
    return out


def build(draft: FieldDraft, ctx: Context) -> list[dict]:
    """Questions for one drafted field (two for a choice with an 'Inne' option)."""
    ctx.techs = []
    tech = ctx.name(slug(draft.label))
    if draft.kind == "info":
        return [{"field": make_field("TEXT_BLOCK", tech, draft.label, description=draft.help)}]

    common = {"description": draft.help}
    if draft.kind in SIMPLE:
        type_, own = SIMPLE[draft.kind]
        validation = _validators(draft, own)  # only TEXT_FIELD kinds have own/extra validators
        return [{"field": make_field(type_, tech, draft.label, validation=validation, **common)}]
    if draft.kind in DICTIONARIES:
        name, search = DICTIONARIES[draft.kind]
        extra: dict = {"dictionaryName": name, "options": [], "searchEnabled": search}
        if name == "Kraje":
            extra["defaultValue"] = ["Polska"]
        validation = ["REQUIRED"] if draft.required else []
        return [{"field": make_field("SELECT", tech, draft.label, validation=validation, **common, **extra)}]

    names = field_options(draft.kind, draft.options, draft.other_option)
    if draft.kind == "multi_choice":
        type_, extra = "CHECKBOX", {}
    elif len(names) <= RADIO_MAX:
        type_, extra = "RADIO", {}
    else:
        type_, extra = "SELECT", {"searchEnabled": len(names) > SEARCH_ABOVE}
    validation = ["REQUIRED"] if draft.required else []
    main = make_field(type_, tech, draft.label, options=options(names), validation=validation,
                      **common, **extra)
    out = [{"field": main}]
    if draft.other_option:
        other_tech = ctx.name(f"{tech}_inne")
        other = make_field("TEXT_FIELD", other_tech, "Wpisz, jakie", validation=validation)
        pred = Equals(field=tech, equals=OTHER_OPTION)
        other["states"] = [{"type": "SHOW", "query": to_jsonata(pred, ctx.at(other_tech), lambda _: ctx.at(tech))}]
        out.append({"field": other})
    return out
