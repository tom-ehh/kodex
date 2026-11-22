import copy

import pytest
from pydantic import ValidationError

from coi_formularze.schema import PIECES, json_schemas, llm, validate_form_definition
from coi_formularze.schema.code import Layout


def q(field, y=0):
    return {"x": 0, "y": y, "rows": 1, "cols": 3, "field": field}


def section(tech, children, **extra):
    return q({"id": f"id-{tech}", "type": "SECTION", "techName": tech, "title": tech.title(),
              "repeatingSectionGrid": children, **extra})


def text(tech, **extra):
    return q({"id": f"id-{tech}", "type": "TEXT_FIELD", "techName": tech, "title": tech,
              "validation": ["REQUIRED"], **extra})


def valid_form():
    return {
        "title": "Wniosek o coś",
        "tabs": [
            {"id": "t1", "title": "Strona 1", "type": "MAIN", "questions": [
                section("dane", [
                    text("imie"),
                    q({"id": "id-kraj", "type": "SELECT", "techName": "kraj", "dictionaryName": "Kraje",
                       "options": [], "validation": []}, y=1),
                    q({"id": "id-rodzaj", "type": "RADIO", "techName": "rodzaj", "validation": ["REQUIRED"],
                       "options": [{"id": "o1", "name": "Tak", "techName": "tak"}]}, y=2),
                ]),
                section("extra", [text("pesel", validation=["REQUIRED", "PESEL"])],
                        states=[{"type": "SHOW", "query": "\"Tak\" in tabs.x"}]),
            ]},
            {"id": "t2", "title": "Zgody", "type": "MAIN", "questions": [
                section("zgody", [q({"id": "id-zg", "type": "CONSENT_SECTION", "techName": "zg",
                                     "consentSectionConfig": {"consents": [{"description": "Zgoda",
                                                                            "required": True}]}})]),
            ]},
            {"id": "t3", "title": "Koniec", "type": "FINISH", "questions": [
                section("podsumowanie", [q({"id": "id-info", "type": "INFO_SECTION", "techName": "info",
                                             "description": "Dziękujemy"})]),
            ]},
        ],
        "flow": {"links": [{"id": "l1", "source": "t1", "target": "t2"},
                           {"id": "l2", "source": "t2", "target": "t3"}]},
    }


def child(doc, tab=0, sec=0, i=0):
    return doc["tabs"][tab]["questions"][sec]["field"]["repeatingSectionGrid"][i]["field"]


def test_json_schema_exports():
    schemas = json_schemas()
    assert set(schemas) == set(PIECES)
    assert schemas["llm.radio"]["properties"]["type"]["const"] == "RADIO"


def test_valid_form_passes():
    assert validate_form_definition(valid_form()) == []


BREAKS = {
    "one tab (rule 2)": lambda d: d.update(tabs=d["tabs"][-1:]),
    "title over 255 (rule 1)": lambda d: d.update(title="a" * 256),
    "no FINISH (rule 2)": lambda d: d["tabs"][-1].update(type="MAIN"),
    "two FINISH (rule 2)": lambda d: d["tabs"][0].update(type="FINISH"),
    "FINISH not last (rule 2)": lambda d: d["tabs"].insert(0, d["tabs"].pop()),
    "bad tab type": lambda d: d["tabs"][0].update(type="main"),
    "empty tab (rule 3)": lambda d: d["tabs"][1].update(questions=[]),
    "bare field on tab (rule 4)": lambda d: d["tabs"][0]["questions"].append(text("luzem")),
    "nested section (rule 4)": lambda d: d["tabs"][0]["questions"][0]["field"]["repeatingSectionGrid"].append(
        section("wewn", [text("x")])),
    "input field on FINISH (rule 5)": lambda d: d["tabs"][-1]["questions"].append(section("p", [text("y")])),
    "bare INFO_SECTION on FINISH (rule 4)": lambda d: d["tabs"][-1]["questions"].append(
        q({"id": "id-i3", "type": "INFO_SECTION", "techName": "i3", "description": "x"})),
    "input without validation (rule 6)": lambda d: child(d).pop("validation"),
    "unknown validator": lambda d: child(d).update(validation=["PATTERN"]),
    "dangling flow link (rule 7)": lambda d: d["flow"]["links"][1].update(target="nope"),
    "unreachable tab (rule 7)": lambda d: d["flow"]["links"].pop(),
    "bad state type (rule 8)": lambda d: child(d).update(states=[{"type": "HIDE", "query": "x"}]),
    "READ_ONLY on section": lambda d: d["tabs"][0]["questions"][1]["field"].update(
        states=[{"type": "READ_ONLY", "query": "x"}]),
    "empty query (rule 8)": lambda d: child(d).update(states=[{"type": "SHOW", "query": ""}]),
    "dictionary on TEXT_FIELD (rule 9)": lambda d: child(d).update(dictionaryName="Kraje"),
    "RADIO without options or dictionary": lambda d: child(d, i=2).update(options=[]),
    "option without techName": lambda d: child(d, i=2)["options"][0].pop("techName"),
    "section without title": lambda d: d["tabs"][0]["questions"][0]["field"].pop("title"),
    "INFO_SECTION without description": lambda d: child(d, tab=-1).pop("description"),
    "CONSENT_SECTION without consents": lambda d: child(d, tab=1).pop("consentSectionConfig"),
    "missing techName": lambda d: child(d).pop("techName"),
    "unknown component": lambda d: child(d).update(type="SLIDER"),
    "children on non-section": lambda d: child(d).update(repeatingSectionGrid=[text("z")]),
    "duplicate techName": lambda d: child(d, i=1).update(techName="imie"),
    "duplicate id": lambda d: child(d, i=1).update(id="id-imie"),
    "bad defaultValueType": lambda d: child(d).update(defaultValueType="REGISTRY"),
    "negative x": lambda d: d["tabs"][0]["questions"][0].update(x=-1),
    "fractional x": lambda d: d["tabs"][0]["questions"][0].update(x=0.5),
    "INFO_SECTION bare on MAIN (rule 4)": lambda d: d["tabs"][1]["questions"].append(
        q({"id": "id-i2", "type": "INFO_SECTION", "techName": "i2", "description": "x"})),
    "tab with only empty sections (rule 3)": lambda d: d["tabs"][1]["questions"][0]["field"].update(
        repeatingSectionGrid=[]),
    "PESEL on CHECKBOX (REQUIRED only)": lambda d: child(d, i=2).update(type="CHECKBOX", validation=["PESEL"]),
    "EMAIL on NUMBER (REQUIRED only)": lambda d: child(d).update(type="NUMBER", validation=["EMAIL"]),
    "dictionary plus options": lambda d: child(d, i=1)["options"].append({"id": "x", "name": "X", "techName": "x"}),
    "choice without options key": lambda d: child(d, i=2).pop("options"),
    "READ_ONLY on INFO_SECTION": lambda d: child(d, tab=-1).update(
        states=[{"type": "READ_ONLY", "query": "x"}]),
    "READ_ONLY on TEXT_BLOCK": lambda d: child(d).update(type="TEXT_BLOCK", validation=None,
                                                          states=[{"type": "READ_ONLY", "query": "x"}]),
}


def test_read_only_state_on_input_field_is_fine():
    doc = valid_form()
    child(doc).update(states=[{"type": "READ_ONLY", "query": "x"}])
    assert validate_form_definition(doc) == []


@pytest.mark.parametrize("name", BREAKS)
def test_rule_violation_is_reported(name):
    doc = copy.deepcopy(valid_form())
    BREAKS[name](doc)
    assert validate_form_definition(doc), name


@pytest.mark.parametrize("model", [llm.TextField, llm.Radio, llm.SectionHeader, llm.InfoSection])
def test_llm_pieces_reject_unknown_keys(model):
    doc = {"type": "SECTION" if model is llm.SectionHeader else model.model_fields["type"].annotation.__args__[0],
           "techName": "t", "title": "T", "description": "d", "validation": [],
           "options": [{"id": "a", "name": "A", "techName": "a"}], "requried": True}
    with pytest.raises(ValidationError, match="requried"):
        model.model_validate(doc)


def test_extra_platform_keys_are_allowed():
    doc = valid_form()
    child(doc, i=1).update(multiLevelTree=True, searchEnabled=True)
    assert validate_form_definition(doc) == []


PIECES_OK = [
    (llm.Radio, {"type": "RADIO", "techName": "r", "validation": [],
                 "options": [{"id": "a", "name": "A", "techName": "a"}]}),
    (llm.Select, {"type": "SELECT", "techName": "k", "validation": [], "dictionaryName": "Kraje",
                  "options": [], "searchEnabled": True}),
    (llm.TextField, {"type": "TEXT_FIELD", "techName": "t", "validation": ["REQUIRED"]}),
    (llm.SectionHeader, {"type": "SECTION", "techName": "s", "title": "S",
                         "states": [{"type": "SHOW", "query": "x"}]}),
    (Layout, {"x": 0, "y": 0, "rows": 1, "cols": 3}),
]
PIECES_BAD = [
    (llm.Radio, {"type": "RADIO", "techName": "r", "validation": [], "options": []}),
    (llm.TextField, {"type": "TEXT_FIELD", "techName": "t", "validation": [], "dictionaryName": "Kraje"}),
    (llm.TextField, {"type": "TEXT_FIELD", "techName": "t"}),
    (llm.SectionHeader, {"type": "SECTION", "techName": "s"}),
    (llm.SectionHeader, {"type": "SECTION", "techName": "s", "title": "S",
                         "states": [{"type": "READ_ONLY", "query": "x"}]}),
    (Layout, {"x": 0, "y": 0, "rows": 0, "cols": 3}),
]


@pytest.mark.parametrize(("model", "doc"), PIECES_OK)
def test_piece_accepts(model, doc):
    model.model_validate(doc)


@pytest.mark.parametrize(("model", "doc"), PIECES_BAD)
def test_piece_rejects(model, doc):
    with pytest.raises(ValidationError):
        model.model_validate(doc)
