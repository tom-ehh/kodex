"""needs_review wiring: hard failure -> review item, corrections resolve it, wake triages
pipeline failures, and the whole correction -> approve -> skill loop (no network)."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from kodex.morpheus import review
from kodex.morpheus.models import (
    Episode,
    EpisodeKind,
    EpisodeStatus,
    Layer,
    SkillStatus,
)
from kodex.morpheus.sleep.api import SleepScheduler
from tests.test_analyze import HARD, MEDIUM, SIMPLE, FakeChat, make, post  # noqa: F401
from tests.test_morpheus_wake import cache_with, flag, settings, store, wake, zip_ep  # noqa: F401


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def on(make, tmp_path):  # noqa: F811
    def _on(chat, **kw):
        return make(chat, morpheus_enabled=True, morpheus_db=str(tmp_path / "m.sqlite"), **kw)
    return _on


def items(client, kind=None):
    return [i for i in review.items(client.app.state.morpheus) if kind in (None, i.kind)]


# ---- hard failure -> needs_review_form ----

def test_hard_failure_queues_form_for_review(on):
    client = on(FakeChat(broken={SIMPLE, MEDIUM, HARD}))
    r = post(client)
    body = r.json()
    assert r.status_code == 502 and body["needs_review"] is True
    assert body["form_id"] == r.headers["x-form-id"] and body["form"] is None
    (item,) = items(client, "needs_review_form")
    assert item.id == body["review_id"] and item.ref_id == body["form_id"]
    assert item.diff["tiers"] == ["simple", "medium", "hard"] and item.diff["has_draft"] is False


def test_invalid_draft_is_returned_and_correctable(on, monkeypatch):
    monkeypatch.setattr("kodex.parse.pipeline.validate_form_definition",
                        lambda form: [SimpleNamespace(message="tabs: too few")])
    client = on(FakeChat())
    r = post(client)
    body = r.json()
    assert r.status_code == 502 and body["form"]["tabs"] and body["needs_review"]
    edit = {"edits": [{"field_path": "imie", "attr": "required", "before": True, "after": False}]}
    c = client.post(f"/api/v1/forms/{body['form_id']}/corrections", json=edit)
    assert c.status_code == 201  # the draft was cached, so corrections work on it
    assert items(client, "needs_review_form") == []  # resolved by the correction
    (decided,) = [i for i in review.items(client.app.state.morpheus, None)
                  if i.kind == "needs_review_form"]
    assert (decided.status, decided.decided_by) == ("approved", "correction")


def test_disabled_morpheus_502_has_no_review(make):  # noqa: F811
    body = post(make(FakeChat(broken={SIMPLE, MEDIUM, HARD}))).json()
    assert body["needs_review"] is False and body["review_id"] is None


def test_needs_review_item_approve_and_reject_via_api(on):
    client = on(FakeChat(broken={SIMPLE, MEDIUM, HARD}))
    rid = post(client).json()["review_id"]
    r = client.post(f"/api/v1/morpheus/review/{rid}", json={"decision": "approve"})
    assert r.status_code == 200 and "acknowledged" in r.json()["result"]
    rid2 = post(client).json()["review_id"]
    assert client.post(f"/api/v1/morpheus/review/{rid2}", json={"decision": "reject"}).status_code == 200
    assert items(client, "needs_review_form") == []


# ---- wake triages pipeline failures on its own ----

def fail_ep(store, validator="PESEL", label="PESEL"):  # noqa: F811
    return store.add_episode(Episode(kind=EpisodeKind.VALIDATION_FAILURE, form_id="f",
                                     field_path="pesel", field_label=label, validator=validator,
                                     error="bad PESEL", tier_from="simple"))


@pytest.mark.anyio
async def test_one_off_failure_is_ignored_recurring_goes_to_review(store):  # noqa: F811
    w = wake(store)
    fail_ep(store)
    (o,) = await w.tick()
    assert (o.stage, o.decision) == ("triage", "ignored") and review.items(store) == []
    fail_ep(store)
    (o,) = await w.tick()
    assert o.decision == "needs_human"
    (item,) = review.items(store)
    assert item.kind == "recurring_failure" and item.diff["count"] == 2
    assert item.diff["validator"] == "PESEL" and item.ref_id == "pesel:PESEL:validator"
    fail_ep(store)
    await w.tick()
    assert len(review.items(store)) == 1  # same pattern reuses the pending item
    assert w.approve(item.id) == {"acknowledged": item.ref_id}


@pytest.mark.anyio
async def test_escalation_is_kept_as_signal(store):  # noqa: F811
    store.add_episode(Episode(kind=EpisodeKind.ESCALATION, form_id="f", tier_from="simple",
                              tier_to="medium"))
    (o,) = await wake(store).tick()
    assert o.stage == "escalation_signal"
    assert store.episodes()[0].status is EpisodeStatus.IGNORED


# ---- the human loop end to end: correction -> review -> approve -> skill ----

@pytest.mark.anyio
async def test_learn_now_correction_review_approve_activates_skill(store):  # noqa: F811
    w = wake(store)
    ep = zip_ep(store, learn_now=True)
    (o,) = await w.learn_now([ep.id])
    assert o.decision == "needs_human"  # first occurrence: a human must confirm
    (item,) = review.items(store)
    assert item.kind == "needs_human_episode"
    assert w.approve(item.id)["requeued"] is True
    outs = await w.tick()
    assert outs[-1].decision == "active"
    (sk,) = [s for s in store.list_skills(Layer.EPISODIC) if s.status is SkillStatus.ACTIVE]
    assert "ZIP_CODE" in sk.validator_rule.add_validators


# ---- sleep runs on its own ----

@pytest.mark.anyio
async def test_scheduler_fires_on_cron_and_on_pattern_count(store, monkeypatch):  # noqa: F811
    runs = []

    async def fake_cycle(store, deps, cfg, trigger):
        runs.append(trigger)
        return {"trigger": trigger}

    monkeypatch.setattr("kodex.morpheus.sleep.api.run_cycle", fake_cycle)
    app = SimpleNamespace(state=SimpleNamespace(morpheus=store, sleep_deps=object()))
    sch = SleepScheduler(app, settings(sleep_cron="0 3 * * *", sleep_min_patterns=2))
    await sch.step(datetime(2026, 11, 23, 3, 0, 10, tzinfo=UTC))
    await sch.step(datetime(2026, 11, 23, 3, 0, 40, tzinfo=UTC))  # same minute: once
    assert runs == ["cron"]
    for _ in range(2):
        e = zip_ep(store)
        review.set_episode(store, e.id, EpisodeStatus.SKILLED)
    await sch.step(datetime(2026, 11, 23, 9, 0, tzinfo=UTC))
    await sch.step(datetime(2026, 11, 23, 9, 1, tzinfo=UTC))  # no new patterns: no rerun
    assert runs == ["cron", "patterns:2"]


@pytest.mark.anyio
async def test_real_pipeline_failures_reach_the_queue(on):
    """Real failure episodes carry no validator; repeated ones must still be triaged."""
    client = on(FakeChat(broken={SIMPLE, MEDIUM}))
    post(client)
    st = client.app.state.morpheus
    outs = await wake(st).tick()
    assert any(o.decision == "needs_human" for o in outs)
    (item,) = [i for i in review.items(st) if i.kind == "recurring_failure"]
    assert item.diff["error_class"] == "invalid_json" and item.diff["where"] == "facts"
