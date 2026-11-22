"""Morpheus P1 hooks: skills in prompts and rules, episodes, corrections, DocCache (no network)."""

import copy
import json
import logging

import pytest

from kodex.morpheus import Skill, SkillStore, pii_findings
from kodex.morpheus.doccache import DocCache
from kodex.morpheus.hooks import HINTS_HEADER, Hooks
from kodex.morpheus.models import EpisodeKind, EpisodeStatus
from kodex.parse.prompts import prompt
from tests.test_analyze import ANSWERS, HARD, MEDIUM, SIMPLE, FakeChat, make, post  # noqa: F401

PESEL_OUTLINE = {"pages": [{"title": "Wnioskodawca", "sections": [
    {"title": "Dane wnioskodawcy", "purpose": "imię, PESEL i sposób składania"},
    {"title": "Pełnomocnik", "purpose": "dane pełnomocnika"}]}]}


class PromptChat(FakeChat):
    """FakeChat that keeps every prompt and answers the PESEL outline."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.prompts: list[str] = []

    async def complete(self, model, messages, schema):
        self.prompts.append(messages[0]["content"])
        return await super().complete(model, messages, schema)


@pytest.fixture(autouse=True)
def pesel_outline(monkeypatch):
    answers = copy.deepcopy(ANSWERS)
    answers["outline"] = PESEL_OUTLINE
    monkeypatch.setattr("tests.test_analyze.ANSWERS", answers)


@pytest.fixture
def on(make, tmp_path):  # noqa: F811
    def _on(chat, **kw):
        return make(chat, morpheus_enabled=True, morpheus_db=str(tmp_path / "m.sqlite"), **kw)
    return _on


def section_prompts(chat):
    return [p for p in chat.prompts if "Zadanie: wypisz WSZYSTKIE pola" in p]


def fields(form):
    return {f["field"]["title"]: f["field"] for t in form["tabs"] for q in t["questions"]
            for f in q["field"].get("repeatingSectionGrid", []) if "title" in f["field"]}


def strip_ids(form):
    text = json.dumps(form, sort_keys=True)
    for key in ("id", "source", "target"):
        text = __import__("re").sub(rf'"{key}": "[0-9a-f-]{{36}}"', f'"{key}": "X"', text)
    return text


# ---- skills in ----

def test_seed_skill_snippet_goes_into_matching_section_prompt(on):
    chat = PromptChat()
    assert post(on(chat)).status_code == 200
    applicant, proxy = sorted(section_prompts(chat), key=lambda p: "„Pełnomocnik”" in p)
    assert HINTS_HEADER in applicant and "walidatory REQUIRED i PESEL" in applicant
    assert HINTS_HEADER not in proxy  # 'dane pełnomocnika' matches no skill


def test_disabled_means_no_hints_and_same_output(make, on):  # noqa: F811
    off_chat, on_chat = PromptChat(), PromptChat()
    off = post(make(off_chat))
    assert not any(HINTS_HEADER in p for p in off_chat.prompts)
    assert prompt("facts", "md", {}) == prompt("facts", "md", {}, "")
    # with only seeds the drafted test form is unchanged (PESEL already required)
    assert strip_ids(off.json()) == strip_ids(post(on(on_chat)).json())
    assert "x-form-id" in off.headers


def test_hand_written_skill_changes_prompt_and_output(on, tmp_path):
    store = SkillStore(tmp_path / "m.sqlite")
    store.create(Skill(
        id="hand-imie", name="imie-optional", layer="general", description="test",
        trigger={"labels_any": ["imię"]}, prompt_snippet="Pole imię zawsze opcjonalne.",
        validator_rule={"required": False}))
    store.close()
    chat = PromptChat()
    client = on(chat)
    body = post(client, envelope="true").json()
    assert any("Pole imię zawsze opcjonalne." in p for p in section_prompts(chat))
    assert "REQUIRED" not in fields(body["form"])["Imię"]["validation"]  # drafted required=True
    hits = client.app.state.morpheus._db.execute(
        "SELECT skill_id, outcome FROM skill_hits WHERE skill_id='hand-imie'").fetchall()
    assert {tuple(h) for h in hits} >= {("hand-imie", "win")}


def test_validator_rule_adds_validator_to_text_field(tmp_path):
    store = SkillStore(tmp_path / "m.sqlite")
    from kodex.morpheus import install_seeds
    install_seeds(store)
    h = Hooks(store, "f1")
    q = [{"field": {"type": "TEXT_FIELD", "title": "Numer NIP", "validation": []}}]
    applied = h.apply_rules("Numer NIP", q)
    assert [s.id for s in applied] == ["seed-nip"] and q[0]["field"]["validation"] == ["NIP"]
    phone = [{"field": {"type": "TEXT_FIELD", "validation": ["REQUIRED"]}}]
    h.apply_rules("Nr telefonu", phone)
    assert phone[0]["field"]["validation"] == []  # keep optional
    radio = [{"field": {"type": "RADIO", "validation": []}}]
    h.apply_rules("NIP firmy", radio)
    assert radio[0]["field"]["validation"] == []  # no NIP validator on a radio
    combo = [{"field": {"type": "TEXT_FIELD", "validation": []}}]
    assert h.apply_rules("Nr telefonu / adres e-mail", combo) == []
    assert combo[0]["field"]["validation"] == []  # two families: ambiguous, untouched


# ---- episodes out ----

def episodes_of(client):
    return client.app.state.morpheus.episodes()


def test_escalation_records_episode_without_pii(on):
    client = on(FakeChat(broken={SIMPLE, MEDIUM}))
    assert post(client).status_code == 200
    eps = episodes_of(client)
    kinds = [e.kind for e in eps]
    assert EpisodeKind.ESCALATION in kinds and EpisodeKind.VALIDATION_FAILURE in kinds
    esc = next(e for e in eps if e.kind is EpisodeKind.ESCALATION)
    assert (esc.tier_from, esc.tier_to) == ("medium", "hard") and esc.after_shape
    assert all(pii_findings(e.model_dump(mode="json", exclude={"id", "created_at", "form_id"})) == []
               for e in eps)


def test_needs_review_episode(on):
    client = on(FakeChat(broken={SIMPLE, MEDIUM, HARD}))
    assert post(client).status_code == 502
    assert any(e.status is EpisodeStatus.NEEDS_HUMAN for e in episodes_of(client))


def test_failure_episode_shapes_pii_in_errors(tmp_path):
    store = SkillStore(tmp_path / "m.sqlite")
    h = Hooks(store, "f1")
    h.record_validation_failure("simple", "dane", "PESEL", ["fields.0: bad value 44051401458"])
    (ep,) = store.episodes()
    assert "44051401458" not in json.dumps(ep.model_dump(mode="json"))
    assert ep.error.startswith("aaaaaa.#")


def test_disabled_writes_nothing(make, tmp_path):  # noqa: F811
    db = tmp_path / "m.sqlite"
    client = make(FakeChat(broken={SIMPLE}), morpheus_db=str(db))
    assert post(client).status_code == 200
    assert not db.exists() and getattr(client.app.state, "morpheus", None) is None


# ---- corrections ----

WHY = "Bo urzędnik tak powiedział SEKRET-XYZ"


def test_corrections_store_shapes_never_why(on, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    client = on(FakeChat())
    r = post(client)
    form_id = r.headers["x-form-id"]
    body = {"why": WHY, "learn_now": True, "edits": [
        {"field_path": "pesel", "attr": "value", "before": "4405140145", "after": "44051401458"},
        {"field_path": "imie", "attr": "required", "before": True, "after": False}]}
    c = client.post(f"/api/v1/forms/{form_id}/corrections", json=body)
    assert c.status_code == 201 and len(c.json()["episode_ids"]) == 2
    eps = [e for e in episodes_of(client) if e.kind is EpisodeKind.WORKER_CORRECTION]
    value = next(e for e in eps if e.edited_attr == "value")
    assert (value.before_shape, value.after_shape, value.field_label) == ("##########", "###########", "PESEL")
    assert all(e.flag == {"learn_now": True} for e in eps)
    client.app.state.morpheus._db.commit()
    raw = (tmp_path / "m.sqlite").read_bytes()
    assert b"SEKRET-XYZ" not in raw and b"44051401458" not in raw
    assert "SEKRET-XYZ" not in caplog.text


def test_corrections_unknown_id_and_disabled(on, make):  # noqa: F811
    edit = {"edits": [{"field_path": "x", "attr": "value", "before": "a", "after": "b"}]}
    assert on(FakeChat()).post("/api/v1/forms/nope/corrections", json=edit).status_code == 404
    assert make(FakeChat()).post("/api/v1/forms/nope/corrections", json=edit).status_code == 503


# ---- DocCache ----

def test_doc_cache_ttl():
    now = [0.0]
    cache = DocCache(10, clock=lambda: now[0])
    cache.put("a", "doc", {"t": 1})
    assert cache.get("a").doc == "doc"
    now[0] = 9.9
    assert len(cache) == 1
    now[0] = 10.0
    assert cache.get("a") is None and len(cache) == 0


def test_doc_cache_cap():
    cache = DocCache(10, max_items=2)
    for k in "abc":
        cache.put(k, k)
    assert cache.get("a") is None and cache.get("c").doc == "c"
