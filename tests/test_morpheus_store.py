import sqlite3
import threading

import pytest

from coi_formularze.morpheus import (
    Actor,
    Episode,
    EpisodeKind,
    HitOutcome,
    Layer,
    PIIError,
    PinnedError,
    Skill,
    SkillStatus,
    SkillStore,
    SkillTrigger,
)
from coi_formularze.morpheus.store import SCHEMA_VERSION


def mk(id="sk-zip", status=SkillStatus.ACTIVE, **kw):
    return Skill(
        id=id, name=id, layer=Layer.EPISODIC, status=status, description="zip dash",
        trigger=SkillTrigger(labels_any=["kod pocztowy"], validator="ZIP_CODE"),
        prompt_snippet="Format NN-NNN.", **kw,
    )


@pytest.fixture
def store(tmp_path):
    s = SkillStore(tmp_path / "sub" / "m.sqlite")
    yield s
    s.close()


def test_create_get_list(store):
    store.create(mk())
    assert store.get("sk-zip").version == 1
    assert [s.id for s in store.active()] == ["sk-zip"]
    assert store.current_version() == 1
    with pytest.raises(ValueError):
        store.create(mk())


def test_proposed_does_not_change_skillset(store):
    store.create(mk(status=SkillStatus.PROPOSED))
    assert store.current_version() is None and store.active() == []
    store.activate("sk-zip")
    assert [s.id for s in store.active()] == ["sk-zip"]


def test_patch_versions_and_parent(store):
    store.create(mk())
    new = store.patch("sk-zip", {"prompt_snippet": "Kod: NN-NNN."}, actor=Actor.WAKE)
    assert (new.version, new.parent_version) == (2, 1)
    assert store.get("sk-zip", 1).status is SkillStatus.RETIRED
    assert [(s.id, s.version) for s in store.active()] == [("sk-zip", 2)]
    with pytest.raises(ValueError):
        store.patch("sk-zip", {"version": 9})


def test_retire_archive_and_rollback(store):
    store.create(mk())
    store.create(mk("sk-other"))
    v_both = store.current_version()
    store.patch("sk-zip", {"description": "v2"})
    store.retire("sk-other", absorbed_into="sk-zip")
    assert store.get("sk-other").absorbed_into == "sk-zip"
    assert [s.id for s in store.active()] == ["sk-zip"]
    new_v = store.rollback(v_both)
    assert new_v > v_both
    assert {(s.id, s.version) for s in store.active()} == {("sk-zip", 1), ("sk-other", 1)}
    store.archive("sk-other")
    assert store.get("sk-other").status is SkillStatus.ARCHIVED


def test_record_hit_counters(store):
    store.create(mk())
    store.record_hit("sk-zip", 1, HitOutcome.WIN, form_id="f1")
    store.record_hit("sk-zip", 1, HitOutcome.LOSS)
    s = store.get("sk-zip")
    assert (s.hits, s.wins, s.losses) == (2, 1, 1)


def test_pinned_snapshot_freezes_active_set(tmp_path):
    path = tmp_path / "m.sqlite"
    s = SkillStore(path)
    s.create(mk())
    v = s.current_version()
    s.create(mk("sk-later"))
    s.close()
    pinned = SkillStore(path, snapshot=v)
    assert [x.id for x in pinned.active()] == ["sk-zip"]
    for write in (
        lambda: pinned.create(mk("sk-new")),
        lambda: pinned.patch("sk-zip", {"description": "x"}),
        lambda: pinned.retire("sk-zip"),
        lambda: pinned.rollback(v),
    ):
        with pytest.raises(PinnedError):
            write()
    assert pinned.get("sk-new") is None and pinned.get("sk-zip").version == 1
    pinned.create(mk("sk-prop", status=SkillStatus.PROPOSED))  # proposals still allowed
    pinned.record_hit("sk-zip", 1, HitOutcome.WIN)
    with pytest.raises(ValueError):
        SkillStore(path, snapshot=999)


def test_audit_is_append_only(store):
    store.create(mk())
    store.patch("sk-zip", {"description": "v2"})
    store.retire("sk-zip")
    assert [e.event for e in store.events("sk-zip")] == ["create", "patch", "retire"]
    with pytest.raises(sqlite3.DatabaseError):
        store._db.execute("DELETE FROM skill_events")
    with pytest.raises(sqlite3.DatabaseError):
        store._db.execute("UPDATE skill_events SET event='x'")


def test_pii_refused_and_rolled_back(store):
    with pytest.raises(PIIError):
        store.create(mk(pattern={"before": "44051401359"}))
    assert store.get("sk-zip") is None and store.events() == []
    store.create(mk())
    with pytest.raises(PIIError):
        store.patch("sk-zip", {"prompt_snippet": "np. jan@example.com"})
    assert store.get("sk-zip").version == 1
    with pytest.raises(PIIError):
        store.add_episode(Episode(kind=EpisodeKind.WORKER_CORRECTION, before_shape="00-950"))


def test_episodes_roundtrip(store):
    ep = store.add_episode(Episode(
        kind=EpisodeKind.VALIDATION_FAILURE, field_label="Kod pocztowy",
        validator="ZIP_CODE", before_shape="#####", after_shape="##-###", form_id="f1",
    ))
    assert ep.id and store.episodes()[0].after_shape == "##-###"


def test_migration_is_idempotent(tmp_path):
    p = tmp_path / "m.sqlite"
    SkillStore(p).create(mk())
    assert SkillStore(p).get("sk-zip") is not None
    assert sqlite3.connect(p).execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_thread_safe_writes(store):
    def work(i):
        store.create(mk(f"sk-{i}"))
        store.record_hit(f"sk-{i}", 1, HitOutcome.WIN)

    ts = [threading.Thread(target=work, args=(i,)) for i in range(20)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(store.active()) == 20
    assert len(store.skillsets()) == 20
