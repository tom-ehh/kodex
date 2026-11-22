"""/forms/analyze end to end with a scripted fake model (no network)."""

import json

import pytest
from fastapi.testclient import TestClient

from coi_formularze.api import routes
from coi_formularze.api.deps import get_chat
from coi_formularze.config import Settings, get_settings
from coi_formularze.extract.model import Document, Page, Source
from coi_formularze.main import create_app
from coi_formularze.parse.llm import Completion, LlmError
from coi_formularze.schema import validate_form_definition
from tests import samples

ANSWERS = {
    "facts": {"title": "Wniosek o dodatek", "addressee": "Wójt Gminy X", "filers": ["person"]},
    "outline": {"pages": [{"title": "Wnioskodawca", "sections": [
        {"title": "Dane wnioskodawcy", "purpose": "kto składa"},
        {"title": "Pełnomocnik", "purpose": "dane pełnomocnika"}]}]},
    "Dane wnioskodawcy": {"title": "Dane wnioskodawcy", "fields": [
        {"label": "PESEL", "kind": "pesel", "required": True},
        {"label": "Składam jako", "kind": "single_choice", "required": True,
         "options": ["Osoba", "Pełnomocnik"]},
        {"label": "Rasa psa", "kind": "single_choice", "required": False,
         "options": ["Akita", "Rottweiler"], "reusable_list": "RASY_PSOW"}]},
    "Pełnomocnik": {"title": "Pełnomocnik", "fields": [{"label": "Imię", "kind": "text", "required": True}]},
    "states": {"states": [{"target": "pelnomocnik", "show_when": {
        "field": "dane_wnioskodawcy.skladam_jako", "equals": "Pełnomocnik"}}]},
}


class FakeChat:
    """Answers by pass; models in `broken` answer garbage, models in `down` raise."""

    def __init__(self, broken=(), down=()):
        self.broken, self.down, self.calls = set(broken), set(down), []

    async def complete(self, model, messages, schema):
        self.calls.append(model)
        if model in self.down:
            raise LlmError("model service answered 500")
        if model in self.broken:
            return Completion("not json", "stop", 0.001, model)
        text = messages[0]["content"]
        if "Zadanie: podaj podstawowe fakty" in text:
            key = "facts"
        elif "Zadanie: zaplanuj strony" in text:
            key = "outline"
        elif "Zadanie: wskaż, które sekcje" in text:
            key = "states"
        else:
            key = "Pełnomocnik" if "„Pełnomocnik”" in text else "Dane wnioskodawcy"
        return Completion(json.dumps(ANSWERS[key], ensure_ascii=False), "stop", 0.001, model)


SIMPLE, MEDIUM, HARD = "m-simple", "m-medium", "m-hard"


@pytest.fixture
def make(monkeypatch):
    doc = Document(kind="pdf", pages=[Page(number=1, source=Source.NATIVE, markdown="Wniosek o dodatek\nPESEL ____")])
    monkeypatch.setattr(routes, "extract", lambda data, kind, ocr: doc)

    def _make(chat, **overrides):
        app = create_app()
        settings = Settings(_env_file=None, edenai_api_key="k", tier_simple_model=SIMPLE,
                            tier_medium_model=MEDIUM, tier_hard_model=HARD, **overrides)
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_chat] = lambda: chat
        return TestClient(app)
    return _make


def post(client, **params):
    return client.post("/api/v1/forms/analyze", params=params, files={"file": ("a.pdf", samples.PDF)})


def test_bare_body_is_an_importable_form(make):
    r = post(make(FakeChat()))
    assert r.status_code == 200
    form = r.json()
    assert set(form) == {"title", "tabs", "flow"} and validate_form_definition(form) == []
    assert form["title"] == "Wniosek o dodatek"
    pelnomocnik = form["tabs"][0]["questions"][1]["field"]
    assert "Pełnomocnik" in pelnomocnik["states"][0]["query"]


def test_envelope_wraps_the_same_form(make):
    chat = FakeChat()
    body = post(make(chat), envelope="true").json()
    assert set(body) == {"form_id", "form", "route", "attempts", "dictionary_proposals"}
    assert validate_form_definition(body["form"]) == []
    assert body["attempts"][0]["model"] == body["route"]["model"] and body["attempts"][0]["error"] is None
    assert body["attempts"][0]["calls"] == len(chat.calls)
    assert body["dictionary_proposals"][0]["name"] == "RASY_PSOW"


def test_failed_tier_escalates(make):
    chat = FakeChat(broken={SIMPLE, MEDIUM}, down=())
    body = post(make(chat), envelope="true").json()
    tiers = [(a["tier"], a["error"] is None) for a in body["attempts"]]
    assert tiers[-1] == ("hard", True) and all(not ok for _, ok in tiers[:-1])
    assert body["route"]["tier"] == "hard" and body["route"]["escalated"]


def test_model_service_down_escalates(make):
    body = post(make(FakeChat(down={SIMPLE, MEDIUM})), envelope="true").json()
    assert body["route"]["tier"] == "hard"


def test_all_tiers_fail_gives_502(make):
    r = post(make(FakeChat(broken={SIMPLE, MEDIUM, HARD})))
    assert r.status_code == 502
    assert [a["tier"] for a in r.json()["attempts"]][-1] == "hard"


def test_no_api_key_gives_503(make):
    assert post(make(None)).status_code == 503


def test_invalid_file_still_422(make):
    r = make(FakeChat()).post("/api/v1/forms/analyze", files={"file": ("a.pdf", samples.PNG)})
    assert r.status_code == 422 and r.json()["file"]["ok"] is False


def test_extraction_failure_gives_422(make, monkeypatch):
    from coi_formularze.extract import ExtractionError

    def boom(*a):
        raise ExtractionError("encrypted PDF")
    monkeypatch.setattr(routes, "extract", boom)
    r = post(make(FakeChat()))
    assert r.status_code == 422 and "encrypted PDF" in r.json()["detail"]


def test_section_state_on_its_own_question_is_repaired(make, monkeypatch):
    # A section shown by a question inside it used to fail every tier ("cannot depend on itself").
    answers = {**ANSWERS, "Pełnomocnik": {"title": "Pełnomocnik", "fields": [
        {"label": "Czy pełnomocnik", "kind": "yes_no", "required": True},
        {"label": "Imię", "kind": "text", "required": True}]},
        "states": {"states": [{"target": "pelnomocnik", "show_when": {
            "field": "pelnomocnik.czy_pelnomocnik", "equals": "Tak"}}]}}
    monkeypatch.setitem(globals(), "ANSWERS", answers)
    body = post(make(FakeChat()), envelope="true").json()
    assert [a["error"] for a in body["attempts"]] == [None]
    grid = body["form"]["tabs"][0]["questions"][1]["field"]
    assert grid["states"] == []
    assert [len(c["field"]["states"]) for c in grid["repeatingSectionGrid"]] == [0, 1]
