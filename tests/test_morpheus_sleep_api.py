"""Sleep endpoints and the scheduler step, with fake adapters."""

import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from kodex.config import Settings, get_settings
from kodex.main import create_app
from kodex.morpheus import SkillStore
from kodex.morpheus.models import Episode, EpisodeKind, EpisodeStatus, Layer
from kodex.morpheus.sleep import state
from kodex.morpheus.sleep.api import SleepScheduler
from tests.test_morpheus_sleep import PESEL, ZIP, FakeGen, FakeJudge, FakeReplay, deps


def client(tmp_path, **kw):
    app = create_app()
    settings = Settings(_env_file=None, morpheus_enabled=True,
                        morpheus_db=str(tmp_path / "m.sqlite"), **kw)
    app.dependency_overrides[get_settings] = lambda: settings
    store = app.state.morpheus = SkillStore(settings.morpheus_db, snapshot=settings.skills_snapshot)
    if settings.skills_snapshot is None:
        for s in PESEL + ZIP:
            store.create(s)
    app.state.sleep_deps = deps()
    return TestClient(app), store, settings


def seeded(tmp_path):
    s = SkillStore(tmp_path / "m.sqlite")
    for sk in PESEL + ZIP:
        s.create(sk)
    s.close()


def test_sleep_wait_then_metrics_and_rollback(tmp_path):
    c, store, _ = client(tmp_path)
    v0 = store.current_version()
    r = c.post("/api/v1/morpheus/sleep", params={"wait": True})
    assert r.status_code == 202 and r.json()["status"] == "ok" and r.json()["activated"] == 2
    m = c.get("/api/v1/morpheus/metrics").json()
    assert len(m["cycles"]) == 1 and m["cycles"][0]["skill_count"] == 2
    r = c.post(f"/api/v1/morpheus/rollback/{v0}")
    assert r.status_code == 200 and r.json()["restored"] == v0
    assert {s.id for s in store.active()} == {s.id for s in PESEL + ZIP}
    assert c.post("/api/v1/morpheus/rollback/999").status_code == 404


def test_sleep_background(tmp_path):
    c, store, _ = client(tmp_path)
    with c:
        r = c.post("/api/v1/morpheus/sleep")
        assert r.status_code == 202 and r.json()["status"] == "started"
        for _ in range(100):
            if state.cycles(store):
                break
            asyncio.run(asyncio.sleep(0.01))
    assert state.cycles(store)[0]["status"] == "ok"


def test_sleep_refused_when_pinned_or_locked(tmp_path):
    seeded(tmp_path)
    c, store, _ = client(tmp_path, skills_snapshot=1)
    r = c.post("/api/v1/morpheus/sleep", params={"wait": True})
    assert r.status_code == 409 and "pinned" in r.json()["detail"]
    assert c.post("/api/v1/morpheus/rollback/1").status_code == 409

    c, store, _ = client(tmp_path / "b")
    state.try_lock(store, "someone")
    assert c.post("/api/v1/morpheus/sleep").status_code == 409


def test_disabled_is_503():
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    c = TestClient(app)
    assert c.get("/api/v1/morpheus/metrics").status_code == 503
    assert c.post("/api/v1/morpheus/sleep").status_code == 503


def test_scheduler_step_cron_and_patterns(tmp_path):
    _, store, settings = client(tmp_path, sleep_cron="0 3 * * *", sleep_min_patterns=1)

    class App:
        class state:
            pass
    App.state.morpheus = store
    App.state.sleep_deps = deps(judge=FakeJudge(same=0.0), gen=FakeGen(), replay=FakeReplay())
    sch = SleepScheduler(App, settings)
    at3 = datetime(2026, 11, 22, 3, 0, 10, tzinfo=UTC)
    assert asyncio.run(sch.step(at3))["trigger"] == "cron"
    assert asyncio.run(sch.step(at3)) is None  # same minute: once
    store.add_episode(Episode(kind=EpisodeKind.WORKER_CORRECTION, status=EpisodeStatus.SKILLED))
    later = datetime(2026, 11, 22, 4, 0, tzinfo=UTC)
    assert asyncio.run(sch.step(later))["trigger"] == "patterns:1"
    assert asyncio.run(sch.step(later)) is None  # no new episodes since
    assert not [s for s in store.active() if s.layer is Layer.GENERAL]
