"""Drafts -> kinds -> states -> assembled Form Definition (ported from PR #2, no templates)."""

import pytest
from pydantic import ValidationError

from coi_formularze.schema import FormDefinition, validate_form_definition
from coi_formularze.schema.assemble import FINISH_TITLE, FormDraft, assemble
from coi_formularze.schema.drafts import (
    FieldDraft,
    FormFacts,
    Outline,
    OutlinePage,
    OutlineSection,
    SectionDraft,
)
from coi_formularze.schema.naming import slug, unique
from coi_formularze.schema.states import (
    AllOf,
    AnyOfLeaves,
    Equals,
    Location,
    NotEquals,
    StateDraft,
    catalogue,
    describe,
    repair_states,
    state_problems,
    state_set_model,
    to_jsonata,
)

FACTS = FormFacts(title="Wniosek o testowanie", addressee="Wójt Gminy Testowo", filers=["person"])


def draft_with(*fields: FieldDraft, repeatable: bool = False, states: list | None = None,
               extra: list[tuple[str, list[FieldDraft]]] = ()) -> FormDraft:
    sections = [SectionDraft(title="Sekcja", fields=list(fields))]
    outline_secs = [OutlineSection(title="Sekcja", purpose="test", repeatable=repeatable)]
    for title, fs in extra:
        sections.append(SectionDraft(title=title, fields=fs))
        outline_secs.append(OutlineSection(title=title, purpose="test"))
    outline = Outline(pages=[OutlinePage(title="Przedmiot", sections=outline_secs)])
    return FormDraft(facts=FACTS, outline=outline, sections=sections, states=states or [])


def built(draft: FormDraft) -> FormDefinition:
    form = assemble(draft).form
    assert validate_form_definition(form) == []
    return FormDefinition.model_validate(form)


def fields(*fs: FieldDraft, **kw) -> list:
    form = built(draft_with(*fs, **kw))
    return [c.field for c in form.tabs[0].questions[0].field.repeatingSectionGrid]


def section(form: FormDefinition, tech: str):
    return next(q.field for t in form.tabs for q in t.questions if q.field.techName == tech)


# --- naming ----------------------------------------------------------------------------


def test_slug_transliterates_polish():
    assert slug("Wybierz, co mamy zrobić z nadpłatą") == "wybierz_co_mamy_zrobic_z_nadplata"
    assert slug("Łódź – ŻÓŁW") == "lodz_zolw"
    assert slug("!!!") == "pole"


def test_unique_adds_qualifier_then_number():
    taken = {"ulica"}
    assert unique("ulica", taken, "lokalizacja") == "ulica_lokalizacja"
    assert unique("ulica", taken, "lokalizacja") == "ulica_lokalizacja_2"
    assert unique("nowa", taken) == "nowa"


# --- draft rules (errors feed retries) ---------------------------------------------------


@pytest.mark.parametrize(("kwargs", "message"), [
    ({"kind": "single_choice", "options": ["A"]}, "at least 2 options"),
    ({"kind": "text", "options": ["A", "B"]}, "options are only for"),
    ({"kind": "multi_choice", "options": ["A", "a"]}, "must not repeat"),
    ({"kind": "yes_no", "other_option": True}, "other_option is only"),
    ({"kind": "single_choice", "options": ["A", "Inne"], "other_option": True}, "use other_option"),
    ({"kind": "pesel", "extra_validators": ["NIP"]}, "extra_validators are only"),
    ({"kind": "long_text", "extra_validators": ["EMAIL"]}, "extra_validators are only"),
    ({"kind": "text", "extra_validators": ["REQUIRED"]}, "use required=true"),
    ({"kind": "text", "reusable_list": "LISTA"}, "reusable_list is only"),
    ({"kind": "single_choice", "options": ["A", "B"], "reusable_list": "lower"}, "pattern"),
    ({"kind": "address"}, "kind"),
])  # fmt: skip
def test_field_draft_rules(kwargs, message):
    with pytest.raises(ValidationError, match=message):
        FieldDraft(label="Pole", required=True, **kwargs)


def test_field_draft_strips_and_forbids_extra():
    assert FieldDraft(label="  Imię ", kind="text", required=True).label == "Imię"
    with pytest.raises(ValidationError, match="Extra inputs"):
        FieldDraft(label="Imię", kind="text", required=True, techName="imie")


def test_section_labels_unique_by_slug():
    with pytest.raises(ValidationError, match="unique within a section"):
        SectionDraft(title="S", fields=[FieldDraft(label="Ulica", kind="text", required=True),
                                        FieldDraft(label="ulica!", kind="text", required=True)])


def test_facts_rules():
    with pytest.raises(ValidationError, match="must start with"):
        FormFacts(title="Prośba o coś", addressee="X", filers=["person"])
    with pytest.raises(ValidationError, match="must not repeat"):
        FormFacts(title="Wniosek o coś", addressee="X", filers=["person", "person"])
    with pytest.raises(ValidationError):
        FormFacts(title="Wniosek o coś", addressee="X", filers=[])


def test_outline_unique_sections_and_pages():
    a, b = OutlineSection(title="A", purpose="x"), OutlineSection(title="a!", purpose="y")
    with pytest.raises(ValidationError, match="section titles must be unique"):
        Outline(pages=[OutlinePage(title="P", sections=[a, b])])
    c = OutlineSection(title="C", purpose="z")
    with pytest.raises(ValidationError, match="page titles must be unique"):
        Outline(pages=[OutlinePage(title="P", sections=[a]), OutlinePage(title="p", sections=[c])])


def test_form_draft_sections_must_match_outline():
    d = draft_with(FieldDraft(label="Pole", kind="text", required=True)).model_dump()
    d["sections"][0]["title"] = "Inna"
    with pytest.raises(ValidationError, match=r"missing \['sekcja'\], extra \['inna'\]"):
        FormDraft.model_validate(d)


def test_form_draft_reserves_summary_page():
    d = draft_with(FieldDraft(label="Pole", kind="text", required=True)).model_dump()
    d["outline"]["pages"][0]["title"] = FINISH_TITLE
    with pytest.raises(ValidationError, match="reserved"):
        FormDraft.model_validate(d)


def test_form_draft_checks_states():
    yes = FieldDraft(label="Czy", kind="yes_no", required=True)
    other = FieldDraft(label="Opis", kind="text", required=False)
    with pytest.raises(ValidationError, match="has no option 'Może'"):
        draft_with(yes, other, states=[{"target": "sekcja.opis", "show_when": {"field": "sekcja.czy", "equals": "Może"}}])
    with pytest.raises(ValidationError, match="does not exist"):
        draft_with(yes, other, states=[{"target": "nie_ma", "show_when": {"field": "sekcja.czy", "equals": "Tak"}}])


# --- kinds -> components ------------------------------------------------------------------


@pytest.mark.parametrize(("kind", "type_", "validation"), [
    ("text", "TEXT_FIELD", ["REQUIRED"]), ("long_text", "TEXTAREA", ["REQUIRED"]),
    ("number", "NUMBER", ["REQUIRED"]), ("date", "DATEPICKER", ["REQUIRED"]),
    ("file", "FILE_UPLOADER", ["REQUIRED"]), ("pesel", "TEXT_FIELD", ["REQUIRED", "PESEL"]),
    ("nip", "TEXT_FIELD", ["REQUIRED", "NIP"]), ("regon", "TEXT_FIELD", ["REQUIRED", "REGON"]),
    ("email", "TEXT_FIELD", ["REQUIRED", "EMAIL"]), ("zip_code", "TEXT_FIELD", ["REQUIRED", "ZIP_CODE"]),
])  # fmt: skip
def test_simple_kinds(kind, type_, validation):
    (f,) = fields(FieldDraft(label="Pole", kind=kind, required=True))
    assert (f.type, f.validation) == (type_, validation)


def test_optional_field_and_extra_validators():
    (f,) = fields(FieldDraft(label="Pole", kind="text", required=False, extra_validators=["EPUAP"]))
    assert f.validation == ["EPUAP"]


def test_dictionary_kinds():
    country, gmina = fields(FieldDraft(label="Kraj urodzenia", kind="country", required=True),
                            FieldDraft(label="Gmina", kind="commune", required=True))
    assert (country.type, country.dictionaryName, country.defaultValue) == ("SELECT", "Kraje", ["Polska"])
    assert (gmina.dictionaryName, gmina.searchEnabled, gmina.options) == ("Gmina", True, [])


@pytest.mark.parametrize(("count", "type_", "search"), [(2, "RADIO", None), (5, "RADIO", None),
                                                         (6, "SELECT", False), (16, "SELECT", True)])
def test_choice_widget_by_option_count(count, type_, search):
    opts = [f"Opcja {i}" for i in range(count)]
    (f,) = fields(FieldDraft(label="Wybór", kind="single_choice", required=True, options=opts))
    assert f.type == type_ and getattr(f, "searchEnabled", None) == search
    assert [o.name for o in f.options] == opts and f.options[0].techName == "opcja_0"


def test_multi_choice_and_yes_no():
    multi, yes = fields(FieldDraft(label="Cele", kind="multi_choice", required=True, options=["A", "B"]),
                        FieldDraft(label="Czy zgoda", kind="yes_no", required=True))
    assert multi.type == "CHECKBOX"
    assert (yes.type, [o.name for o in yes.options]) == ("RADIO", ["Tak", "Nie"])


def test_other_option_adds_text_shown_on_inne():
    choice, other = fields(FieldDraft(label="Rodzaj", kind="single_choice", required=True,
                                      options=["A", "B"], other_option=True))
    assert [o.name for o in choice.options] == ["A", "B", "Inne"]
    assert other.type == "TEXT_FIELD" and other.techName == "rodzaj_inne"
    assert '"Inne" in' in other.states[0].query and "'rodzaj' in field.techName" in other.states[0].query


def test_info_kind_is_plain_text_block():
    (f,) = fields(FieldDraft(label="Ważne", kind="info", required=False, help="Przeczytaj to"))
    assert (f.type, f.title, f.description) == ("TEXT_BLOCK", "Ważne", "Przeczytaj to")


def test_duplicate_labels_across_sections_get_qualified():
    form = built(draft_with(FieldDraft(label="Ulica", kind="text", required=True),
                            extra=[("Adres", [FieldDraft(label="Ulica", kind="text", required=True)])]))
    techs = [c.field.techName for q in form.tabs[0].questions for c in q.field.repeatingSectionGrid]
    assert techs == ["ulica", "ulica_adres"]


# --- states -----------------------------------------------------------------------------


def test_catalogue_and_per_form_schema():
    d = draft_with(FieldDraft(label="Czy", kind="yes_no", required=True),
                   FieldDraft(label="Opis", kind="text", required=False))
    entries = catalogue(d.outline, d.sections)
    assert [e.key for e in entries] == ["sekcja", "sekcja.czy", "sekcja.opis"]
    assert "sekcja.czy [yes_no]" in describe(entries)
    model = state_set_model(entries)
    model.model_validate({"states": [{"target": "sekcja.opis", "show_when": {"field": "sekcja.czy", "equals": "Tak"}}]})
    for bad in ({"target": "nie_ma", "show_when": {"field": "sekcja.czy", "equals": "Tak"}},
                {"target": "sekcja.opis", "show_when": {"field": "sekcja.opis", "equals": "x"}},
                {"target": "sekcja.opis", "show_when": {"field": "sekcja.czy", "equals": "Może"}}):
        with pytest.raises(ValidationError):
            model.model_validate({"states": [bad]})


def test_section_state_controlled_from_inside_moves_to_its_fields():
    # PB-17: "show 'Dane pełnomocnika' when 'Dane pełnomocnika.Rodzaj' = ..." failed every tier.
    rodzaj = FieldDraft(label="Rodzaj", kind="single_choice", required=True, options=["Pełnomocnik", "Do doręczeń"])
    imie = FieldDraft(label="Imię", kind="text", required=True)
    tel = FieldDraft(label="Tel", kind="phone", required=False)
    d = draft_with(rodzaj, imie, tel)
    entries = catalogue(d.outline, d.sections)
    when = {"field": "sekcja.rodzaj", "equals": "Pełnomocnik"}
    states = [StateDraft.model_validate(x) for x in (
        {"target": "sekcja", "show_when": when},
        {"target": "sekcja.tel", "show_when": {"field": "sekcja.rodzaj", "equals": "Do doręczeń"}})]
    assert "cannot depend on itself" in state_problems(states, entries)[0]
    fixed = repair_states(states, entries)
    assert state_problems(fixed, entries) == []
    assert [(s.target, s.show_when.equals) for s in fixed] == [
        ("sekcja.imie", "Pełnomocnik"), ("sekcja.tel", "Do doręczeń")]
    form = built(draft_with(rodzaj, imie, tel, states=[s.model_dump() for s in fixed]))
    grid = form.tabs[0].questions[0].field
    assert grid.states == [] and grid.repeatingSectionGrid[0].field.states == []
    assert "Pełnomocnik" in grid.repeatingSectionGrid[1].field.states[0].query


def test_no_choice_fields_means_no_states():
    d = draft_with(FieldDraft(label="Opis", kind="text", required=False))
    model = state_set_model(catalogue(d.outline, d.sections))
    assert model.model_validate({"states": []})
    with pytest.raises(ValidationError):
        model.model_validate({"states": [{"target": "sekcja", "show_when": {"field": "x", "equals": "y"}}]})


def _locate(key: str) -> Location:
    return Location("Strona 1", "sekcja", key, False)


def test_compile_matches_bonus_pattern():
    q = to_jsonata(Equals(field="pole", equals="Tak"), _locate("cel"), _locate)
    assert q == ("(\"Tak\" in tabs['Strona 1' in title].questions['sekcja' in field.techName]"
                 ".field.repeatingSectionGrid['pole' in field.techName].field.__userAnswer)")
    assert to_jsonata(NotEquals(field="pole", not_equals="Polska"), _locate("cel"), _locate).startswith('$not("Polska"')


def test_compile_nested_and_escapes():
    pred = AllOf(all_of=[
        Equals(field="a", equals='Opcja "1"'),
        AnyOfLeaves(any_of=[Equals(field="b", equals="X"), NotEquals(field="c", not_equals="Y")]),
    ])
    q = to_jsonata(pred, _locate("cel"), lambda k: Location("Strona 'A'", "s", k, False))
    assert '"Opcja \\"1\\""' in q and "Strona \\'A\\'" in q
    assert "\nand\n(" in q and "\nor\n$not(" in q


def test_repeatable_section_and_same_section_state():
    form = built(draft_with(
        FieldDraft(label="Płeć", kind="single_choice", required=True, options=["Pies", "Suka"]),
        FieldDraft(label="Data cieczki", kind="date", required=False),
        repeatable=True,
        states=[{"target": "sekcja.data_cieczki", "show_when": {"field": "sekcja.plec", "equals": "Suka"}}]))
    sec = form.tabs[0].questions[0].field
    assert sec.type == "REPEATING_SECTION"
    assert "[$repeatingSectionIndex]['plec' in fieldTechName].answers" in sec.repeatingSectionGrid[1].field.states[0].query


def test_section_level_state():
    form = built(draft_with(
        FieldDraft(label="Czy masz psa", kind="yes_no", required=True),
        extra=[("Załączniki", [FieldDraft(label="Książeczka szczepień", kind="file", required=True)])],
        states=[{"target": "zalaczniki", "show_when": {"field": "sekcja.czy_masz_psa", "equals": "Tak"}}]))
    assert section(form, "zalaczniki").states[0].query.startswith('("Tak" in tabs[\'Przedmiot\' in title]')


# --- assembly -------------------------------------------------------------------------------


def test_pages_finish_and_flow():
    d = draft_with(FieldDraft(label="Pole", kind="text", required=True)).model_dump()
    d["outline"]["pages"].append({"title": "Kontakt", "sections": [{"title": "Telefon", "purpose": "x"}]})
    d["sections"].append({"title": "Telefon", "fields": [{"label": "Numer", "kind": "phone", "required": False}]})
    form = built(FormDraft.model_validate(d))
    assert [(t.title, t.type) for t in form.tabs] == [("Przedmiot", "MAIN"), ("Kontakt", "MAIN"),
                                                      (FINISH_TITLE, "FINISH")]
    assert [(link.source, link.target) for link in form.flow.links] == [
        (form.tabs[0].id, form.tabs[1].id), (form.tabs[1].id, form.tabs[2].id)]
    assert form.title == FACTS.title


def test_layout_stacks_sections():
    form = built(draft_with(FieldDraft(label="A", kind="text", required=True),
                            FieldDraft(label="B", kind="text", required=True),
                            extra=[("Druga", [FieldDraft(label="C", kind="text", required=True)])]))
    qs = form.tabs[0].questions
    assert [(q.y, q.rows, q.field.questionOrder) for q in qs] == [(0, 2, 0), (2, 1, 2)]
    assert [c.y for c in qs[0].field.repeatingSectionGrid] == [0, 1]


def test_fresh_ids_each_run():
    d = draft_with(FieldDraft(label="Pole", kind="text", required=True))
    assert assemble(d).form["tabs"][0]["id"] != assemble(d).form["tabs"][0]["id"]


def test_dictionary_proposals_beside_form():
    breeds = ["Pit bull terrier", "Rottweiler", "Akbash dog"]
    result = assemble(draft_with(FieldDraft(label="Rasa psa", kind="single_choice", required=True,
                                            options=breeds, reusable_list="RASY_PSOW_AGRESYWNYCH")))
    (p,) = result.dictionary_proposals
    assert (p.name, p.entries, p.used_by) == ("RASY_PSOW_AGRESYWNYCH", breeds, ["rasa_psa"])
    assert "dictionaryName" not in str(result.form["tabs"][0])  # options stay inline: it still imports
