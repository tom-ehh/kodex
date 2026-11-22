"""Morpheus P2 wake: flag gate, writer, light gate, activate/propose, review (no network)."""

import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from kodex.api.deps import get_settings
from kodex.config import Settings
from kodex.main import create_app
from kodex.morpheus import SkillStore, review
from kodex.morpheus.basal import BasalFlag, build_state, parse_flag
from kodex.morpheus.doccache import DocCache
from kodex.morpheus.hooks import Hooks, section_slice
from kodex.morpheus.models import (
    Episode,
    EpisodeKind,
    EpisodeStatus,
    Layer,
    SkillStatus,
)
from kodex.morpheus.replay import locate, post_fix_shape
from kodex.morpheus.wake import Wake, code_family, shape_ok
from kodex.parse.llm import Completion
from kodex.route.router import BasalUnavailable

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


WRITER, REPLAY = "writer-model", "replay-model"
WHY = "format XX-XXX, SEKRET-WHY-123"

FORM = {"title": "T", "tabs": [{"title": "Adres", "questions": [{"field": {
    "type": "SECTION", "title": "Adres zamieszkania", "techName": "adres", "repeatingSectionGrid": [
        {"field": {"type": "TEXT_FIELD", "title": "Kod pocztowy", "techName": "kod_pocztowy",
                   "validation": ["REQUIRED"]}}]}}]}]}
MD = "Adres zamieszkania\nKod pocztowy ______\nMiejscowość ____"

GOOD_SKILL = {"name": "zip-code-hyphen", "description": "Kod pocztowy w formacie XX-XXX.",
              "labels_any": ["kod pocztowy"],
              "prompt_snippet": "Pole kod pocztowy: kind=zip_code, walidator ZIP_CODE.",
              "validator_rule": {"add_validators": ["ZIP_CODE"], "required": None, "post_fix": "zip_code"}}


class Chat:
    """Writer answers `writer`; replay answers one SectionDraft for the asked section."""

    def __init__(self, writer=None, section_ok=True):
        self.writer = writer or GOOD_SKILL
        self.section_ok = section_ok
        self.prompts: list[tuple[str, str]] = []

    async def complete(self, model, messages, schema):
        text = messages[0]["content"]
        self.prompts.append((model, text))
        if model == WRITER:
            return Completion(json.dumps(self.writer, ensure_ascii=False), "stop", 0.0, model)
        if not self.section_ok:
            return Completion("nope", "stop", 0.001, model)
        title = text.split("pola sekcji „", 1)[1].split("”", 1)[0]
        return Completion(json.dumps({"title": title, "fields": [
            {"label": "Kod pocztowy", "kind": "text", "required": True}]}), "stop", 0.001, model)


def flag(p=0.9, c=0.9):
    async def ask(ep, why, url, timeout):
        ask.seen.append(why)
        return BasalFlag(p_pattern=p, family="zip_code", family_conf=0.9, consistent=c)
    ask.seen = []
    return ask


async def down(ep, why, url, timeout):
    raise BasalUnavailable("down")


def settings(**kw):
    base = {"_env_file": None, "morpheus_enabled": True, "morpheus_model": WRITER,
            "morpheus_replay_model": REPLAY, "morpheus_basal_retries": 2}
    return Settings(**(base | kw))


@pytest.fixture
def store(tmp_path):
    s = SkillStore(tmp_path / "m.sqlite")
    yield s
    s.close()


def cache_with(form_id="f1"):
    c = DocCache(3600)
    c.put(form_id, SimpleNamespace(markdown=MD), FORM)
    return c


def zip_ep(store, form_id="f1", after="##-###", learn_now=False, label="Kod pocztowy",
           validator="ZIP_CODE", path="kod_pocztowy"):
    return store.add_episode(Episode(
        kind=EpisodeKind.WORKER_CORRECTION, form_id=form_id, field_path=path, field_label=label,
        validator=validator, before_shape="#####", after_shape=after, edited_attr="value",
        flag={"learn_now": True} if learn_now else None))


def wake(store, ask=None, chat=None, cache=None, **kw):
    return Wake(store, settings(**kw), chat or Chat(), cache if cache is not None else cache_with(),
                ask=ask or flag())


def learned(store):
    return [s for s in store.list_skills(Layer.EPISODIC)]


# ---- pure pieces ----

def test_shape_check_family_and_post_fix():
    ep = Episode(kind=EpisodeKind.WORKER_CORRECTION, validator="ZIP_CODE", field_label="Kod",
                 before_shape="#####", after_shape="##-###", edited_attr="value")
    assert shape_ok(ep) and code_family(ep) == "zip_code"
    assert not shape_ok(ep.model_copy(update={"after_shape": "####-#"}))
    assert not shape_ok(ep.model_copy(update={"after_shape": "#####"}))  # nothing changed
    assert code_family(ep.model_copy(update={"validator": None, "edited_attr": "required"})) == "required_flag"
    assert post_fix_shape("zip_code", "#####") == "##-###"
    assert locate(FORM, "adres/kod_pocztowy")[0].title == "Adres zamieszkania"


def test_basal_state_has_no_history_count_and_parses():
    ep = Episode(kind=EpisodeKind.WORKER_CORRECTION, field_label="Kod", validator="ZIP_CODE")
    st = build_state(ep, "bo format")
    assert "Liczba podobnych" not in st and "bo format" in st
    f = parse_flag({"answers": {"scope": {"choice": "pattern", "probabilities": {"pattern": 0.7, "one_off": 0.3}},
                                "family": {"choice": "zip_code", "confidence": 0.8},
                                "validator_consistent": {"noul": 0.6}}})
    assert (f.p_pattern, f.family, f.consistent) == (0.7, "zip_code", 0.6)
    with pytest.raises(BasalUnavailable):
        parse_flag({"status": "starting"})


def test_section_slice_feeds_hint_matching(store):
    md = "Dane wnioskodawcy\nImię\nAdres zamieszkania\nKod pocztowy ___\nOświadczenia\nPodpis"
    sl = section_slice(md, "Dane wnioskodawcy", ["Oświadczenia"])
    assert "Kod pocztowy" in sl and "Podpis" not in sl
    from kodex.morpheus import install_seeds
    install_seeds(store)
    h = Hooks(store)
    assert not any(s.id == "seed-zip-code" for s in h.section_skills("Dane wnioskodawcy"))
    assert any(s.id == "seed-zip-code" for s in h.section_skills("Dane wnioskodawcy", "", sl))


# ---- flag gate ----

async def test_cold_start_never_creates_a_skill(store):
    ep = zip_ep(store)
    out = await wake(store).tick()
    assert out[0].stage == "cold_start" and learned(store) == []
    assert store.episodes()[0].status is EpisodeStatus.IGNORED
    assert review.items(store) == [] and ep.id


async def test_learn_now_first_occurrence_goes_to_review(store):
    ep = zip_ep(store, learn_now=True)
    out = await wake(store).learn_now([ep.id])
    assert out[0].decision == "needs_human" and learned(store) == []
    [item] = review.items(store)
    assert item.kind == "needs_human_episode" and item.ref_id == ep.id


async def test_second_occurrence_activates_episodic_skill(store):
    zip_ep(store)
    await wake(store).tick()
    v0 = store.current_version()
    zip_ep(store)
    w = wake(store)
    [out] = await w.tick()
    assert out.decision == "active", out
    [sk] = learned(store)
    assert sk.status is SkillStatus.ACTIVE and sk.trigger.validator == "ZIP_CODE"
    assert sk.validator_rule.post_fix == "zip_code" and sk.provenance["hist"] == 2
    assert sk.expires_at - datetime.now(UTC) > timedelta(days=13)
    assert store.current_version() != v0
    assert out.detail["replay"]["status"] == "pass"


@pytest.mark.parametrize("p,c", [(0.3, 0.9), (0.9, 0.2)])
async def test_basal_confirm_and_veto(store, p, c):
    zip_ep(store), zip_ep(store)
    outs = await wake(store, ask=flag(p, c)).tick()
    assert outs[-1].stage == "basal_veto" and learned(store) == []


async def test_shape_check_failure_is_ignored(store):
    zip_ep(store, after="####-#"), zip_ep(store, after="####-#")
    outs = await wake(store).tick()
    assert outs[-1].stage == "shape" and learned(store) == []


async def test_basal_down_retries_then_rules_only(store):
    zip_ep(store), zip_ep(store)
    w = wake(store, ask=down)
    assert [o.stage for o in await w.tick()] == ["cold_start", "basal_retry"]
    await w.tick()  # retry 2
    outs = await w.tick()  # retries exhausted: rules only needs hist >= 3
    assert outs[-1].stage == "basal_veto" and learned(store) == []
    zip_ep(store)
    w2 = wake(store, ask=down, morpheus_basal_retries=0)
    outs = await w2.tick()
    assert outs[-1].decision == "active" and learned(store)[0].provenance["rules_only"]


# ---- writer + light gate ----

async def test_pii_in_written_skill_is_refused(store):
    bad = GOOD_SKILL | {"prompt_snippet": "Np. kod 00-950 Warszawa"}
    zip_ep(store), zip_ep(store)
    outs = await wake(store, chat=Chat(writer=bad)).tick()
    assert outs[-1].stage == "lint" and "pii" in outs[-1].detail["lint"]["reasons"][0]
    assert learned(store) == []


async def test_too_broad_trigger_is_refused(store):
    broad = GOOD_SKILL | {"labels_any": ["a"], "validator_rule": None}
    zip_ep(store), zip_ep(store)
    outs = await wake(store, chat=Chat(writer=broad)).tick()
    assert outs[-1].stage == "lint" and "too broad" in outs[-1].detail["lint"]["reasons"][0]


async def test_replay_fail_proposes_then_human_approves(store):
    no_fix = GOOD_SKILL | {"validator_rule": {"add_validators": ["ZIP_CODE"]}}
    zip_ep(store), zip_ep(store)
    w = wake(store, chat=Chat(writer=no_fix))
    outs = await w.tick()
    assert outs[-1].decision == "proposed" and learned(store) == []
    [item] = review.items(store)
    assert item.kind == "proposed_skill" and item.replay["replay"]["status"] == "fail"
    assert "changes" in item.diff
    w.approve(item.id)
    [sk] = learned(store)
    assert sk.status is SkillStatus.ACTIVE and review.items(store) == []


async def test_replay_section_draft_failure_proposes(store):
    zip_ep(store), zip_ep(store)
    outs = await wake(store, chat=Chat(section_ok=False)).tick()
    assert outs[-1].decision == "proposed"
    assert outs[-1].detail["replay"]["reason"] == "section draft failed"


async def test_expired_doc_is_unreplayed_with_half_ttl(store):
    zip_ep(store), zip_ep(store)
    outs = await wake(store, cache=DocCache(3600)).tick()
    assert outs[-1].decision == "active" and outs[-1].detail["replay"]["status"] == "unreplayed"
    left = learned(store)[0].expires_at - datetime.now(UTC)
    assert timedelta(days=6) < left <= timedelta(days=7)


async def test_existing_skill_is_patched_with_parent_version(store):
    zip_ep(store), zip_ep(store)
    w = wake(store)
    await w.tick()
    zip_ep(store)
    outs = await w.tick()
    assert outs[-1].decision == "active"
    [sk] = learned(store)
    assert sk.version == 2 and sk.parent_version == 1


async def test_cap_evicts_and_rollback_restores(store):
    zip_ep(store), zip_ep(store)
    w = wake(store, episodic_max=1)
    await w.tick()
    v_one = store.current_version()
    pesel = GOOD_SKILL | {"name": "pesel-digits", "labels_any": ["pesel"],
                          "validator_rule": {"add_validators": ["PESEL"], "post_fix": "digits_only"}}
    for _ in range(2):
        store.add_episode(Episode(kind=EpisodeKind.WORKER_CORRECTION, form_id="none",
                                  field_path="pesel", field_label="Numer PESEL", validator="PESEL",
                                  before_shape="### ### ### ##", after_shape="#" * 11,
                                  edited_attr="value"))
    w.chat = Chat(writer=pesel)
    outs = await w.tick()
    assert outs[-1].decision == "active"
    active = [s.id for s in store.active() if s.layer is Layer.EPISODIC]
    assert active == ["ep-pesel-digits"]
    store.rollback(v_one)
    assert [s.id for s in store.active() if s.layer is Layer.EPISODIC] == ["ep-zip-code-hyphen"]


async def test_expiry_retires_old_episodic(store):
    zip_ep(store), zip_ep(store)
    w = wake(store)
    await w.tick()
    w.now = lambda: datetime.now(UTC) + timedelta(days=30)
    assert w.expire() == ["ep-zip-code-hyphen"]
    assert learned(store)[0].status is SkillStatus.RETIRED


async def test_why_reaches_basal_and_writer_but_is_never_stored(store, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    zip_ep(store)
    ep = zip_ep(store)
    ask, chat = flag(), Chat()
    w = wake(store, ask=ask, chat=chat)
    w.remember_why([ep.id], WHY)
    await w.tick()
    assert WHY in ask.seen
    assert any(WHY in t for m, t in chat.prompts if m == WRITER)
    assert not any(WHY in t for m, t in chat.prompts if m == REPLAY)
    assert w._whys == {}
    store._db.commit()
    raw = (tmp_path / "m.sqlite").read_bytes()
    assert b"SEKRET-WHY" not in raw and "SEKRET-WHY" not in caplog.text


async def test_pinned_store_never_learns(store, tmp_path):
    zip_ep(store), zip_ep(store)
    v = store.current_version() or store._write_skillset("init")
    pinned = SkillStore(tmp_path / "m.sqlite", snapshot=v)
    assert await wake(pinned).tick() == []
    pinned.close()


# ---- API ----

def test_review_api_flow(store, tmp_path):
    import asyncio
    no_fix = GOOD_SKILL | {"validator_rule": {"add_validators": ["ZIP_CODE"]}}
    zip_ep(store), zip_ep(store)
    w = wake(store, chat=Chat(writer=no_fix))
    asyncio.run(w.tick())
    app = create_app()
    st = settings(morpheus_db=str(tmp_path / "m.sqlite"))
    app.dependency_overrides[get_settings] = lambda: st
    app.state.morpheus, app.state.wake = store, w
    c = TestClient(app)
    [item] = c.get("/api/v1/morpheus/review").json()
    assert item["kind"] == "proposed_skill"
    assert c.post("/api/v1/morpheus/review/nope", json={"decision": "approve"}).status_code == 404
    r = c.post(f"/api/v1/morpheus/review/{item['id']}", json={"decision": "approve"})
    assert r.status_code == 200 and r.json()["result"]["skill_id"] == "ep-zip-code-hyphen"
    skills = c.get("/api/v1/morpheus/skills", params={"layer": "episodic", "status": "active"}).json()
    assert [s["id"] for s in skills] == ["ep-zip-code-hyphen"]
    assert c.get("/api/v1/morpheus/review").json() == []


def test_review_api_reject_and_disabled(store, tmp_path):
    ep = zip_ep(store, learn_now=True)
    import asyncio
    w = wake(store)
    asyncio.run(w.learn_now([ep.id]))
    app = create_app()
    st = settings(morpheus_db=str(tmp_path / "m.sqlite"))
    app.dependency_overrides[get_settings] = lambda: st
    app.state.morpheus, app.state.wake = store, w
    c = TestClient(app)
    [item] = c.get("/api/v1/morpheus/review").json()
    assert c.post(f"/api/v1/morpheus/review/{item['id']}", json={"decision": "reject"}).status_code == 200
    assert store.episodes()[0].status is EpisodeStatus.IGNORED
    off = create_app()
    off.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    assert TestClient(off).get("/api/v1/morpheus/skills").status_code == 503


async def test_human_approval_of_cold_start_runs_the_gate(store):
    ep = zip_ep(store, learn_now=True)
    w = wake(store)
    await w.learn_now([ep.id])
    [item] = review.items(store)
    assert w.approve(item.id)["requeued"]
    outs = await w.tick()
    assert outs[0].decision == "active" and outs[0].detail["replay"]["status"] == "pass"


def test_email_shape_is_not_pii_but_email_is():
    from kodex.morpheus import pii_findings
    assert pii_findings("aaaa@aaa.aa") == [] and pii_findings("jan@wp.pl")


PHONE_SKILL = {"name": "phone-optional", "description": "Telefon nie jest wymagany.",
               "labels_any": ["telefon"], "prompt_snippet": "Pole telefon: required=false.",
               "validator_rule": {"add_validators": [], "required": False, "post_fix": None}}


def phone_ep(store, attr="required", before="true", after="false"):
    return store.add_episode(Episode(
        kind=EpisodeKind.WORKER_CORRECTION, form_id="f2", field_path="telefon",
        field_label="Telefon", validator=None, before_shape=before, after_shape=after,
        edited_attr=attr))


async def test_structural_edit_skips_consistency_veto(store):
    # E15 "phone not required": basal P(pattern)=0.90, consistent=0.19 -> no veto now
    phone_ep(store), phone_ep(store)
    w = Wake(store, settings(), Chat(writer=PHONE_SKILL), DocCache(3600), ask=flag(0.9, 0.19))
    outs = await w.tick()
    assert outs[-1].stage != "basal_veto", outs[-1]
    assert outs[-1].decision == "active"  # document expired -> unreplayed, still activated
    [sk] = learned(store)
    assert sk.validator_rule.required is False
    ep = store.episodes()[-1]
    assert ep.flag["veto_skipped"] is True


async def test_value_edit_keeps_consistency_veto(store):
    zip_ep(store), zip_ep(store)
    outs = await wake(store, ask=flag(0.9, 0.19)).tick()
    assert outs[-1].stage == "basal_veto"
    assert store.episodes()[-1].flag["veto_skipped"] is False


async def test_structural_edit_still_needs_pattern(store):
    phone_ep(store), phone_ep(store)
    w = Wake(store, settings(), Chat(writer=PHONE_SKILL), DocCache(3600), ask=flag(0.2, 0.9))
    assert (await w.tick())[-1].stage == "basal_veto"
