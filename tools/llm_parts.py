"""The target Form Definition split into small LLM parts, and the merge back.

Parts (each with its own Pydantic model, so each can be validated and retried alone):

1. ``Structure``  - form title + pages (tabs) + sections per page.
2. ``PageFields`` - fields of one page: section, label, type, required, extra validators.
3. ``Choices``    - options or a dictionary for every CHECKBOX / RADIO / SELECT from part 2.

Ids, techNames, grid placement (x/y/rows/cols), the FINISH tab and the flow are filled by
code in ``merge`` - they are the same for every form and never asked of the model.
The models mirror the rules in feat/form-schema (src/coi_formularze/schema/llm.py);
``validate_merged`` uses that package when it is importable, else a compact fallback.
"""

import itertools
import json
import re
import unicodedata
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

FieldType = Literal[
    "TEXT_FIELD", "TEXTAREA", "NUMBER", "DATEPICKER", "FILE_UPLOADER",
    "CHECKBOX", "RADIO", "SELECT", "TEXT_BLOCK", "CONSENT_SECTION",
]  # fmt: skip
ExtraValidator = Literal[
    "EMAIL",
    "PESEL",
    "NIP",
    "REGON",
    "ZIP_CODE",
    "EPUAP",
    "ADE",
    "SERIAL_NUMBER_ID_CARD",
]
CHOICE_TYPES = {"CHECKBOX", "RADIO", "SELECT"}
INPUT_TYPES = {
    "TEXT_FIELD",
    "TEXTAREA",
    "NUMBER",
    "DATEPICKER",
    "FILE_UPLOADER",
} | CHOICE_TYPES
DICTIONARIES = (
    "Województwo",
    "Powiat",
    "Gmina",
    "Miejscowość",
    "Kraje",
    "Numery kierunkowe",
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- part 1: structure -------------------------------------------------------------


class StructSection(_Strict):
    title: str = Field(min_length=1, description="Section title as on the form")
    repeating: bool = Field(
        False, description="True if the section repeats (table of rows)"
    )


class StructPage(_Strict):
    title: str = Field(min_length=1, description="Short thematic page title")
    sections: list[StructSection] = Field(min_length=1)


class Structure(_Strict):
    title: str = Field(min_length=1, max_length=255, description="Form title")
    pages: list[StructPage] = Field(min_length=1)


# --- part 2: fields of one page ----------------------------------------------------


class FieldItem(_Strict):
    section: str = Field(
        description="Title of the section the field belongs to (one of the given)"
    )
    label: str = Field(min_length=1, description="Field label as on the form")
    type: FieldType
    required: bool
    validators: list[ExtraValidator] = Field(
        default_factory=list, description="Format validators; only for TEXT_FIELD"
    )


class PageFields(_Strict):
    fields: list[FieldItem] = Field(min_length=1)


# --- part 3: choices ----------------------------------------------------------------


class ChoiceItem(_Strict):
    label: str = Field(description="Label of the choice field (one of the given)")
    dictionaryName: Literal[DICTIONARIES] | None = Field(  # type: ignore[valid-type]
        None, description="Predefined dictionary; then options must be empty"
    )
    options: list[str] = Field(
        default_factory=list, description="Own options, in form order"
    )


class Choices(_Strict):
    choices: list[ChoiceItem]


PART_MODELS: dict[str, type[BaseModel]] = {
    "structure": Structure,
    "fields": PageFields,
    "choices": Choices,
}


# --- context checks: rules one part can check only with the earlier parts ----------


def context_errors(part: str, obj: BaseModel, ctx: dict) -> list[tuple[str, str]]:
    """(kind, message) for rule breaks pydantic can't see alone."""
    errs: list[tuple[str, str]] = []
    if isinstance(obj, PageFields):
        allowed = {norm(s) for s in ctx["sections"]}
        for i, f in enumerate(obj.fields):
            if norm(f.section) not in allowed:
                errs.append(
                    (
                        "unknown_section",
                        f"fields.{i}.section {f.section!r} is not one of {ctx['sections']}",
                    )
                )
            if f.validators and f.type != "TEXT_FIELD":
                errs.append(
                    (
                        "validator_on_non_text",
                        f"fields.{i}: validators are only allowed on TEXT_FIELD",
                    )
                )
    elif isinstance(obj, Choices):
        want = {norm(lbl) for lbl in ctx["labels"]}
        got = {norm(c.label) for c in obj.choices}
        for lbl in sorted(want - got):
            errs.append(("missing_choice", f"no entry for choice field {lbl!r}"))
        for i, c in enumerate(obj.choices):
            if norm(c.label) not in want:
                errs.append(
                    (
                        "unknown_label",
                        f"choices.{i}.label {c.label!r} is not one of the given fields",
                    )
                )
            if bool(c.dictionaryName) == bool(c.options):
                errs.append(
                    (
                        "options_xor_dictionary",
                        f"choices.{i}: give options or a dictionaryName, not both/neither",
                    )
                )
    return errs


def parse_part(
    part: str, text: str, ctx: dict
) -> tuple[BaseModel | None, list[tuple[str, str]]]:
    """Parse model output into the part model. Returns (obj or None, [(kind, msg)])."""
    raw = extract_json(text)
    if raw is None:
        return None, [("invalid_json", "the answer is not a JSON object")]
    try:
        obj = PART_MODELS[part].model_validate(raw)
    except ValidationError as exc:
        return None, [
            (e["type"], f"{'.'.join(map(str, e['loc'])) or '<root>'}: {e['msg']}")
            for e in exc.errors()
        ]
    errs = context_errors(part, obj, ctx)
    return (None if errs else obj), errs


def extract_json(text: str) -> dict | None:
    """First JSON object in text (tolerates ``` fences and chatter around it)."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        v = json.loads(t)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        pass
    start = t.find("{")
    while start != -1:
        try:
            v, _ = json.JSONDecoder().raw_decode(t[start:])
            if isinstance(v, dict):
                return v
        except json.JSONDecodeError:
            pass
        start = t.find("{", start + 1)
    return None


# --- merge -------------------------------------------------------------------------


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.replace("ł", "l").replace("Ł", "L"))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")[:60] or "pole"


def merge(
    structure: Structure, pages: list[PageFields | None], choices: Choices | None
) -> dict:
    """Join the parts into a full Form Definition dict (code fills ids/grid/FINISH/flow)."""
    ns = uuid.uuid5(uuid.NAMESPACE_URL, structure.title)
    counter = iter(range(1_000_000))
    used: set[str] = set()

    def new_id() -> str:
        return str(uuid.uuid5(ns, str(next(counter))))

    def tech(name: str) -> str:
        base = t = slug(name)
        n = 2
        while t in used:
            t, n = f"{base}_{n}", n + 1
        used.add(t)
        return t

    by_label = {norm(c.label): c for c in (choices.choices if choices else [])}
    tabs = []
    for page, pf in zip(structure.pages, pages, strict=True):
        questions = []
        for y, sec in enumerate(page.sections):
            kids = [
                f
                for f in (pf.fields if pf else [])
                if norm(f.section) == norm(sec.title)
            ]
            grid = [
                {
                    "x": 0,
                    "y": i,
                    "rows": 1,
                    "cols": 3,
                    "field": _field(f, by_label, new_id(), tech(f.label), i),
                }
                for i, f in enumerate(kids)
            ]
            questions.append(
                {
                    "x": 0,
                    "y": y,
                    "rows": max(1, len(grid)),
                    "cols": 3,
                    "field": {
                        "id": new_id(),
                        "type": "REPEATING_SECTION" if sec.repeating else "SECTION",
                        "techName": tech(sec.title),
                        "title": sec.title,
                        "questionOrder": y,
                        "repeatingSectionGrid": grid,
                    },
                }
            )
        tabs.append(
            {
                "id": new_id(),
                "title": page.title,
                "type": "MAIN",
                "questions": questions,
            }
        )
    tabs.append(
        {
            "id": new_id(),
            "title": "Podsumowanie",
            "type": "FINISH",
            "questions": [
                {
                    "x": 0,
                    "y": 0,
                    "rows": 1,
                    "cols": 3,
                    "field": {
                        "id": new_id(),
                        "type": "SECTION",
                        "techName": tech("podglad"),
                        "title": "Podgląd",
                        "questionOrder": 0,
                        "repeatingSectionGrid": [
                            {
                                "x": 0,
                                "y": 0,
                                "rows": 1,
                                "cols": 3,
                                "field": {
                                    "id": new_id(),
                                    "type": "INFO_SECTION",
                                    "techName": tech("podglad_info"),
                                    "description": "Sprawdź dane przed wysłaniem wniosku.",
                                    "questionOrder": 0,
                                },
                            }
                        ],
                    },
                }
            ],
        }
    )
    links = [
        {"id": new_id(), "source": a["id"], "target": b["id"]}
        for a, b in itertools.pairwise(tabs)
    ]
    return {"title": structure.title, "tabs": tabs, "flow": {"links": links}}


def _field(f: FieldItem, by_label: dict, fid: str, tech_name: str, order: int) -> dict:
    out: dict = {
        "id": fid,
        "type": f.type,
        "techName": tech_name,
        "title": f.label,
        "questionOrder": order,
    }
    if f.type == "CONSENT_SECTION":
        out["consentSectionConfig"] = {
            "consents": [{"description": f.label, "required": f.required}]
        }
        return out
    if f.type == "TEXT_BLOCK":
        return out
    out["validation"] = (["REQUIRED"] if f.required else []) + (
        list(f.validators) if f.type == "TEXT_FIELD" else []
    )
    if f.type in CHOICE_TYPES:
        c = by_label.get(norm(f.label))
        if c and c.dictionaryName:
            out["dictionaryName"], out["options"] = c.dictionaryName, []
        else:
            opts = c.options if c else []
            out["options"] = [{"id": o, "name": o, "techName": slug(o)} for o in opts]
    return out


# --- full validation -----------------------------------------------------------------


def validate_merged(doc: dict) -> list[str]:
    """Issues in the merged Form Definition ([] = valid)."""
    try:
        from coi_formularze.schema import validate_form_definition  # feat/form-schema
    except ImportError:
        return _fallback_validate(doc)
    return [f"{i.path}: {i.message}" for i in validate_form_definition(doc)]


def _fallback_validate(doc: dict) -> list[str]:
    issues: list[str] = []
    tabs = doc.get("tabs") or []
    if not doc.get("title"):
        issues.append("title: empty")
    if len(tabs) < 2:
        issues.append("tabs: need at least one MAIN and the FINISH tab")
    techs: list[str] = []
    for ti, tab in enumerate(tabs):
        want = "FINISH" if ti == len(tabs) - 1 else "MAIN"
        if tab.get("type") != want:
            issues.append(f"tabs.{ti}.type: expected {want}")
        kids = 0
        for qi, q in enumerate(tab.get("questions") or []):
            sec = q["field"]
            techs.append(sec["techName"])
            if sec["type"] not in ("SECTION", "REPEATING_SECTION"):
                issues.append(f"tabs.{ti}.questions.{qi}: only sections sit on a tab")
            for ci, c in enumerate(sec.get("repeatingSectionGrid") or []):
                f, kids = c["field"], kids + 1
                techs.append(f["techName"])
                where = f"tabs.{ti}.questions.{qi}.grid.{ci}"
                if want == "FINISH" and f["type"] != "INFO_SECTION":
                    issues.append(f"{where}: FINISH holds only INFO_SECTION")
                if (
                    f["type"] in INPUT_TYPES
                    and "REQUIRED" not in f.get("validation", [])
                    and "validation" not in f
                ):
                    issues.append(f"{where}: input field without validation")
                if (
                    f["type"] in CHOICE_TYPES
                    and not f.get("options")
                    and not f.get("dictionaryName")
                ):
                    issues.append(f"{where}: needs options or a dictionaryName")
        if kids == 0:
            issues.append(f"tabs.{ti}: the tab holds no field")
    dup = {t for t in techs if techs.count(t) > 1}
    issues += [f"techName {t!r} is used more than once" for t in sorted(dup)]
    return issues


# --- prompts -------------------------------------------------------------------------

_COMMON = """Jesteś ekspertem od cyfryzacji polskich formularzy urzędowych. Poniżej jest treść
papierowego formularza (wyciągnięta do markdown). Zwróć WYŁĄCZNIE jeden obiekt JSON
zgodny z podanym JSON Schema, bez komentarzy i bez bloków ```.
"""

_TASKS = {
    "structure": """Zadanie: podziel formularz na strony tematyczne i sekcje.
- title: tytuł formularza (Wniosek/Deklaracja/Zgłoszenie/Oświadczenie + przedmiot).
- pages: kolejne strony (MAIN) - strona 1 to strony wniosku (urząd, wnioskodawca), dalej
  przedmiot wniosku, na końcu kontakt/opłaty/oświadczenia. Bez strony podsumowania.
- sections: sekcje formularza na danej stronie, tytuły jak na formularzu.
  repeating=true tylko dla sekcji powtarzalnych (np. tabela osób/działek).""",
    "fields": """Zadanie: wypisz WSZYSTKIE pola do wypełnienia z podanych sekcji tej strony.
Sekcje tej strony: {sections}
- section: dokładnie jeden z tytułów sekcji powyżej.
- label: etykieta pola jak na formularzu.
- type: TEXT_FIELD (krótki tekst), TEXTAREA (długi opis), NUMBER, DATEPICKER (data),
  FILE_UPLOADER (załącznik), RADIO (jeden wybór), CHECKBOX (wiele wyborów / pojedyncze
  pole wyboru), SELECT (lista rozwijana), CONSENT_SECTION (zgody/oświadczenia do
  zaznaczenia), TEXT_BLOCK (tylko tekst informacyjny).
- required: czy pole jest obowiązkowe (pola oznaczone jako nieobowiązkowe/opcjonalne,
  numer lokalu, telefon, e-mail zwykle false).
- validators: tylko dla TEXT_FIELD: PESEL, NIP, REGON, ZIP_CODE (kod pocztowy), EMAIL itd.""",
    "choices": """Zadanie: dla każdego pola wyboru podaj opcje albo słownik.
Pola wyboru: {labels}
- label: dokładnie jedna z etykiet powyżej (każda dokładnie raz).
- options: opcje jak na formularzu, w kolejności; albo
- dictionaryName: jeden z {dicts} (wtedy options = []). Słownik tylko gdy pasuje
  (np. województwo, kraj).""",
}


def build_prompt(part: str, markdown: str, ctx: dict, schema: dict) -> str:
    task = _TASKS[part].format(
        sections=json.dumps(ctx.get("sections", []), ensure_ascii=False),
        labels=json.dumps(ctx.get("labels", []), ensure_ascii=False),
        dicts=", ".join(DICTIONARIES),
    )
    return (
        f"{_COMMON}\n{task}\n\nJSON Schema:\n{json.dumps(schema, ensure_ascii=False)}\n\n"
        f"Formularz:\n<<<\n{markdown}\n>>>"
    )


def retry_prompt(errors: list[tuple[str, str]]) -> str:
    lines = "\n".join(f"- {m}" for _, m in errors[:20])
    return f"Twoja odpowiedź jest niepoprawna:\n{lines}\nZwróć poprawiony, pełny obiekt JSON."
