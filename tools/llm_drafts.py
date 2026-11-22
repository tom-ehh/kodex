"""Bench variants built on coi_formularze.schema drafts (multi-pass, code-side kinds).

Variants (``base`` lives in bench_llm.run_form):

* ``kinds``         - base Structure part, then one call per page returning SectionDrafts
                      (fields by *kind*, options inline); code assembles. Isolates the
                      kinds/code-side widget choice from the pass split.
* ``drafts``        - FormFacts -> Outline -> one SectionDraft per section; code assembles.
* ``drafts_states`` - drafts + a states pass whose schema is built per form.

No skills, no templates: the model's pages and sections are what gets assembled.
"""

import json
from collections.abc import Callable

from llm_parts import Structure, extract_json, retry_prompt
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from coi_formularze.schema.assemble import FormDraft, assemble
from coi_formularze.schema.drafts import (
    FormFacts,
    Outline,
    OutlinePage,
    OutlineSection,
    SectionDraft,
)
from coi_formularze.schema.naming import slug
from coi_formularze.schema.states import catalogue, describe, state_set_model

VARIANTS = ("base", "kinds", "drafts", "drafts_states")

_COMMON = """Jesteś ekspertem od cyfryzacji polskich formularzy urzędowych. Poniżej jest treść
papierowego formularza (wyciągnięta do markdown). Zwróć WYŁĄCZNIE jeden obiekt JSON
zgodny z podanym JSON Schema, bez komentarzy i bez bloków ```.
"""

_KINDS = """Rodzaje pól (kind): text (krótki tekst), long_text (dłuższy opis), number, date,
file (załącznik), info (tylko tekst informacyjny), single_choice (jeden wybór z opcji),
multi_choice (wiele wyborów), yes_no (Tak/Nie), pesel, nip, regon, krs, email, phone,
zip_code (kod pocztowy), country, voivodeship, county, commune, locality (miejscowość).
Adres rozpisz na osobne pola (np. locality, zip_code, text dla ulicy i numeru).
- required: czy pole jest obowiązkowe (numer lokalu, telefon, e-mail zwykle false).
- options: tylko dla single_choice/multi_choice, jak na formularzu; 'Inne' przez other_option.
- extra_validators: tylko dla kind=text (np. EPUAP); zwykle pusta lista.
- reusable_list: nazwa NOWEGO słownika (WIELKIE_LITERY), gdy opcje to ogólna lista."""

_TASKS = {
    "facts": """Zadanie: podaj podstawowe fakty o formularzu.
- title: tytuł usługi (Wniosek/Zgłoszenie/Deklaracja/Oświadczenie + przedmiot).
- addressee: adresat dokładnie jak na formularzu.
- filers: kto może złożyć wniosek: person (osoba fizyczna) i/lub entity (firma, instytucja).""",
    "outline": """Zadanie: zaplanuj strony i sekcje e-usługi dla tego formularza.
Fakty: {facts}
- pages: strony tematyczne w kolejności: najpierw strony wniosku (urząd, wnioskodawca,
  ewentualnie pełnomocnik), potem przedmiot wniosku, na końcu kontakt, opłaty,
  załączniki i oświadczenia. Bez strony podsumowania.
- sections: sekcje na stronie; purpose = jedno zdanie, jakie dane zbiera.
  repeatable=true tylko gdy obywatel wypełnia sekcję wiele razy (tabela osób/działek).""",
    "section": """Zadanie: wypisz WSZYSTKIE pola sekcji „{section}” ({purpose}).
Strona: „{page}”. Plan całego formularza:
{outline}
title = dokładnie „{section}”. Tylko pola tej sekcji.
""" + _KINDS,
    "page": """Zadanie: wypisz WSZYSTKIE pola z sekcji tej strony.
Sekcje tej strony: {sections}
sections: jedna pozycja na każdą sekcję powyżej, title dokładnie jak powyżej.
""" + _KINDS,
    "states": """Zadanie: wskaż, które sekcje lub pola pokazują się tylko przy określonej odpowiedzi
(np. 'Dane pełnomocnika' tylko gdy 'Składam jako' = 'Pełnomocnik').
Dostępne klucze (klucz [rodzaj]: etykieta, opcje):
{catalogue}
- target: klucz sekcji lub pola, które ma się pokazać.
- show_when: warunek na polu z opcjami (equals / not_equals; all_of / any_of).
Jeśli nic nie zależy od odpowiedzi, zwróć {{"states": []}}.""",
}


class PageSections(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sections: list[SectionDraft] = Field(min_length=1)


def prompt(task: str, md: str, schema: dict, **ctx) -> str:
    return (f"{_COMMON}\n{_TASKS[task].format(**ctx)}\n\nJSON Schema:\n"
            f"{json.dumps(schema, ensure_ascii=False)}\n\nFormularz:\n<<<\n{md}\n>>>")


def parse(model: type[BaseModel], text: str, check: Callable | None = None):
    raw = extract_json(text)
    if raw is None:
        return None, [("invalid_json", "the answer is not a JSON object")]
    try:
        obj = model.model_validate(raw)
    except ValidationError as exc:
        return None, [(e["type"], f"{'.'.join(map(str, e['loc'])) or '<root>'}: {e['msg']}")
                      for e in exc.errors()]
    errs = check(obj) if check else []
    return (None if errs else obj), errs


def _outline_text(outline: Outline) -> str:
    return "\n".join(f"- {p.title}: " + "; ".join(s.title for s in p.sections) for p in outline.pages)


def _page_check(titles: list[str]):
    def check(obj: PageSections):
        got, want = [slug(s.title) for s in obj.sections], [slug(t) for t in titles]
        errs = [("unknown_section", f"section {s.title!r} is not one of {titles}")
                for s in obj.sections if slug(s.title) not in want]
        errs += [("missing_section", f"no entry for section {t!r}")
                 for t, k in zip(titles, want, strict=True) if k not in got]
        return errs
    return check


def run_variant(variant: str, call: Callable, md: str) -> tuple[dict | None, list[str], int, int]:
    """Returns (form dict or None, issues, parts_ok, parts_total).

    `call(part, model, prompt_text, page, check)` runs one part with retries and
    returns the parsed object or None.
    """
    ok = total = 0

    def run(part, model, text, page="", check=None):
        nonlocal ok, total
        total += 1
        obj = call(part, model, text, page, check)
        ok += obj is not None
        return obj

    if variant == "kinds":
        st = run("structure", Structure, prompt_structure(md))
        if st is None:
            return None, ["structure part failed"], ok, total
        facts = FormFacts.model_construct(title=st.title, addressee="", filers=["person"])
        outline = Outline.model_construct(pages=[
            OutlinePage.model_construct(title=p.title, sections=[
                OutlineSection.model_construct(title=s.title, purpose=s.title, repeatable=s.repeating)
                for s in p.sections]) for p in st.pages])
        sections: list[SectionDraft] = []
        for i, page in enumerate(st.pages, start=1):
            titles = [s.title for s in page.sections]
            text = prompt("page", md, PageSections.model_json_schema(),
                          sections=json.dumps(titles, ensure_ascii=False))
            ps = run("page", PageSections, text, i, _page_check(titles))
            got = {slug(s.title): s for s in (ps.sections if ps else [])}
            sections += [got.get(slug(t)) or SectionDraft.model_construct(title=t, fields=[]) for t in titles]
        return _assemble(facts, outline, sections, [], ok, total)

    facts = run("facts", FormFacts, prompt("facts", md, FormFacts.model_json_schema()))
    if facts is None:
        return None, ["facts part failed"], ok, total
    outline = run("outline", Outline, prompt(
        "outline", md, Outline.model_json_schema(), facts=facts.model_dump_json()))
    if outline is None:
        return None, ["outline part failed"], ok, total
    plan = _outline_text(outline)
    sections = []
    for i, page in enumerate(outline.pages, start=1):
        for sec in page.sections:
            text = prompt("section", md, SectionDraft.model_json_schema(), section=sec.title,
                          purpose=sec.purpose, page=page.title, outline=plan)

            def same_title(obj, want=sec.title):
                return [] if slug(obj.title) == slug(want) else [
                    ("wrong_section", f"title must be exactly {want!r}")]
            d = run("section", SectionDraft, text, i, same_title)
            sections.append(d or SectionDraft.model_construct(title=sec.title, fields=[]))
    states = []
    if variant == "drafts_states":
        good = [s for s in sections if s.fields]
        entries = catalogue(outline, good)
        model = state_set_model(entries)
        if any(e.options for e in entries):
            ss = run("states", model, prompt("states", md, model.model_json_schema(),
                                             catalogue=describe(entries)))
            states = list(ss.states) if ss else []
    return _assemble(facts, outline, sections, states, ok, total)


def prompt_structure(md: str) -> str:
    from llm_parts import build_prompt
    return build_prompt("structure", md, {}, Structure.model_json_schema())


def _assemble(facts, outline, sections, states, ok, total):
    """Drop failed (empty) sections, then assemble and validate like production would."""
    from coi_formularze.schema import validate_form_definition

    keep = {slug(s.title) for s in sections if s.fields}
    pages = [OutlinePage.model_construct(title=p.title, sections=[s for s in p.sections if slug(s.title) in keep])
             for p in outline.pages]
    pages = [p for p in pages if p.sections]
    if not pages:
        return None, ["no section drafted"], ok, total
    try:
        draft = FormDraft.model_validate({
            "facts": facts,  # instance: not re-validated (kinds variant builds it from Structure)
            "outline": {"pages": [p.model_dump() for p in pages]},
            "sections": [s.model_dump() for s in sections if s.fields],
            "states": [s.model_dump() for s in states],
        })
    except ValidationError as exc:
        return None, [f"draft: {e['msg']}" for e in exc.errors()][:10], ok, total
    form = assemble(draft).form
    issues = [f"{i.path}: {i.message}" for i in validate_form_definition(form)]
    return form, issues, ok, total


__all__ = ["VARIANTS", "parse", "retry_prompt", "run_variant"]
