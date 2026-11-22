"""Merge, part validation and scoring of tools/bench_llm.py (no network)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from llm_parts import (
    Choices,
    PageFields,
    Structure,
    extract_json,
    merge,
    parse_part,
    validate_merged,
)
from llm_score import flatten_definition, match, score, similarity

STRUCT = Structure.model_validate(
    {
        "title": "Wniosek o dodatek",
        "pages": [
            {"title": "Wnioskodawca", "sections": [{"title": "Dane wnioskodawcy"}]},
            {"title": "Lokal", "sections": [{"title": "Dane lokalu"}]},
        ],
    }
)
PAGES = [
    PageFields.model_validate(
        {
            "fields": [
                {
                    "section": "Dane wnioskodawcy",
                    "label": "Imię",
                    "type": "TEXT_FIELD",
                    "required": True,
                },
                {
                    "section": "Dane wnioskodawcy",
                    "label": "Numer PESEL",
                    "type": "TEXT_FIELD",
                    "required": True,
                    "validators": ["PESEL"],
                },
            ]
        }
    ),
    PageFields.model_validate(
        {
            "fields": [
                {
                    "section": "Dane lokalu",
                    "label": "Tytuł prawny",
                    "type": "RADIO",
                    "required": True,
                },
                {
                    "section": "Dane lokalu",
                    "label": "Województwo",
                    "type": "SELECT",
                    "required": False,
                },
            ]
        }
    ),
]
CHOICES = Choices.model_validate(
    {
        "choices": [
            {"label": "Tytuł prawny", "options": ["najem", "własność"]},
            {"label": "Województwo", "dictionaryName": "Województwo"},
        ]
    }
)


def test_merge_is_valid_and_round_trips():
    doc = merge(STRUCT, PAGES, CHOICES)
    assert validate_merged(doc) == []
    assert [t["type"] for t in doc["tabs"]] == ["MAIN", "MAIN", "FINISH"]
    flat = flatten_definition(doc)
    fields = {f["label"]: f for s in flat["sections"] for f in s["fields"]}
    assert fields["Numer PESEL"]["validation"] == ["PESEL", "REQUIRED"]
    assert fields["Tytuł prawny"]["options"] == ["najem", "własność"]
    assert fields["Województwo"]["dictionary"] == "Województwo"
    assert fields["Województwo"]["validation"] == []


def test_merge_survives_failed_page():
    doc = merge(STRUCT, [PAGES[0], None], None)
    assert any("no field" in i or "rule 3" in i for i in validate_merged(doc))


def test_parse_part_reports_kinds():
    ctx = {"sections": ["Dane lokalu"]}
    obj, errs = parse_part("fields", "not json", ctx)
    assert obj is None and errs[0][0] == "invalid_json"
    bad = {
        "fields": [
            {
                "section": "Inna",
                "label": "X",
                "type": "NUMBER",
                "required": True,
                "validators": ["PESEL"],
            }
        ]
    }
    obj, errs = parse_part("fields", "```json\n" + json.dumps(bad) + "\n```", ctx)
    assert obj is None
    assert {k for k, _ in errs} == {"unknown_section", "validator_on_non_text"}
    obj, errs = parse_part("fields", json.dumps({"fields": [{"label": "X"}]}), ctx)
    assert obj is None and "missing" in {k for k, _ in errs}


def test_choices_context():
    obj, errs = parse_part(
        "choices",
        json.dumps({"choices": [{"label": "A", "options": []}]}),
        {"labels": ["A", "B"]},
    )
    assert obj is None
    assert {k for k, _ in errs} == {"missing_choice", "options_xor_dictionary"}


def test_extract_json_with_chatter():
    assert extract_json('Oto wynik: {"a": {"b": 1}} koniec') == {"a": {"b": 1}}
    assert extract_json("[1, 2]") is None


def test_similarity_and_match():
    assert similarity("Numer PESEL:", "numer pesel") == 1.0
    assert similarity("PESEL", "Numer PESEL wnioskodawcy") >= 0.85
    assert match(["Imię", "Nazwisko", "Zupełnie inne"], ["Nazwisko", "Imie"]) == [
        (1, 0),
        (0, 1),
    ] or sorted(match(["Imię", "Nazwisko", "Zupełnie inne"], ["Nazwisko", "Imię"])) == [
        (0, 1),
        (1, 0),
    ]


def test_score():
    truth = {
        "title": "Wniosek o dodatek",
        "sections": [
            {
                "title": "Dane wnioskodawcy",
                "page": 1,
                "fields": [
                    {
                        "label": "Imię",
                        "type": "TEXT_FIELD",
                        "required": True,
                        "validation": ["REQUIRED"],
                        "options": [],
                        "dictionary": None,
                    },
                    {
                        "label": "Numer PESEL",
                        "type": "TEXT_FIELD",
                        "required": True,
                        "validation": ["PESEL", "REQUIRED"],
                        "options": [],
                        "dictionary": None,
                    },
                    {
                        "label": "Nazwisko",
                        "type": "TEXT_FIELD",
                        "required": True,
                        "validation": ["REQUIRED"],
                        "options": [],
                        "dictionary": None,
                    },
                ],
            },
            {
                "title": "Dane lokalu",
                "page": 2,
                "fields": [
                    {
                        "label": "Tytuł prawny",
                        "type": "CHECKBOX",
                        "required": True,
                        "validation": ["REQUIRED"],
                        "options": ["najem", "własność", "inny"],
                        "dictionary": None,
                    }
                ],
            },
        ],
    }
    s = score(flatten_definition(merge(STRUCT, PAGES, CHOICES)), truth)
    assert s["matched"] == 3 and s["recall"] == 0.75 and s["precision"] == 0.75
    assert s["type_acc"] == round(2 / 3, 3)  # RADIO vs CHECKBOX
    assert s["validators_acc"] == 1.0 and s["section_acc"] == 1.0
    assert s["option_recall"] == round(2 / 3, 3)
    assert s["section_recall"] == 1.0 and s["title_sim"] == 1.0


# --- draft variants (tools/llm_drafts.py), with a scripted fake model ------------------

from llm_drafts import parse as parse_model
from llm_drafts import run_variant

ANSWERS = {
    "structure": {"title": "Wniosek o dodatek", "pages": [
        {"title": "Wnioskodawca", "sections": [{"title": "Dane wnioskodawcy"}]}]},
    "facts": {"title": "Wniosek o dodatek", "addressee": "Wójt Gminy X", "filers": ["person"]},
    "outline": {"pages": [{"title": "Wnioskodawca", "sections": [
        {"title": "Dane wnioskodawcy", "purpose": "kto składa"},
        {"title": "Pełnomocnik", "purpose": "dane pełnomocnika"}]}]},
    "page": {"sections": [{"title": "Dane wnioskodawcy", "fields": [
        {"label": "PESEL", "kind": "pesel", "required": True},
        {"label": "Składam jako", "kind": "single_choice", "required": True,
         "options": ["Osoba", "Pełnomocnik"]}]}]},
    "Dane wnioskodawcy": {"title": "Dane wnioskodawcy", "fields": [
        {"label": "PESEL", "kind": "pesel", "required": True},
        {"label": "Składam jako", "kind": "single_choice", "required": True,
         "options": ["Osoba", "Pełnomocnik"]}]},
    "Pełnomocnik": {"title": "Pełnomocnik", "fields": [
        {"label": "Imię", "kind": "text", "required": True}]},
    "states": {"states": [{"target": "pelnomocnik", "show_when": {
        "field": "dane_wnioskodawcy.skladam_jako", "equals": "Pełnomocnik"}}]},
}


def fake_call(part, model, text, page, check):
    key = part
    if part == "section":
        key = "Pełnomocnik" if "„Pełnomocnik”" in text else "Dane wnioskodawcy"
    obj, errs = parse_model(model, json.dumps(ANSWERS[key]), check)
    assert not errs, errs
    return obj


def test_variants_assemble_valid_forms():
    for variant in ("kinds", "drafts", "drafts_states"):
        doc, issues, ok, total = run_variant(variant, fake_call, "md")
        assert issues == [] and ok == total, (variant, issues)
        flat = flatten_definition(doc)
        types = [f["type"] for s in flat["sections"] for f in s["fields"]]
        assert types[:2] == ["TEXT_FIELD", "RADIO"]
    sec = doc["tabs"][0]["questions"][1]["field"]
    assert "Pełnomocnik" in sec["states"][0]["query"]


def test_variant_page_check_rejects_unknown_section():
    bad = {"sections": [{"title": "Inna", "fields": [{"label": "A", "kind": "text", "required": True}]}]}

    def call(part, model, text, page, check):
        if part == "page":
            obj, errs = parse_model(model, json.dumps(bad), check)
            assert obj is None and {k for k, _ in errs} == {"unknown_section", "missing_section"}
            return None
        return fake_call(part, model, text, page, check)

    doc, _issues, ok, total = run_variant("kinds", call, "md")
    assert doc is None and ok == 1 and total == 2
