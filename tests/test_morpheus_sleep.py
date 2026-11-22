"""Morpheus sleep (P3) with fake adapters: triggers, bounds, judge rule, generalize, gate,
rollback, prune + restore, metrics."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from coi_formularze.config import Settings
from coi_formularze.morpheus import (
    Layer,
    Skill,
    SkillStatus,
    SkillStore,
    SkillTrigger,
    review,
)
from coi_formularze.morpheus.models import Episode, EpisodeKind, EpisodeStatus
from coi_formularze.morpheus.sleep import (
    GATE_FORMS,
    HELDOUT_FORMS,
    AdapterError,
    Cron,
    CronError,
    Draft,
    ReplayResult,
    SleepConfig,
    SleepDeps,
    gate_pass,
    run_cycle,
    should_sleep,
    state,
)
from coi_formularze.morpheus.sleep.blocking import Pair, candidate_pairs
from coi_formularze.morpheus.sleep.cluster import (
    Judged,
    clusters_from_edges,
    edge_decision,
    judge_pairs,
)
from coi_formularze.morpheus.sleep.generalize import check_draft, generalize_cluster
from coi_formularze.morpheus.sleep.prune import plan_prune

NOW = datetime(2026, 11, 22, 3, 0, tzinfo=UTC)


def ep(id, labels, validator, snippet, before="", after="", **kw):
    return Skill(id=id, name=id, layer=Layer.EPISODIC, description=snippet[:160],
                 trigger=SkillTrigger(labels_any=labels, validator=validator),
                 pattern={"before": before, "after": after}, prompt_snippet=snippet, **kw)


PESEL = [
    ep("pesel-a", ["pesel wnioskodawcy"], "PESEL", "Zwróć PESEL jako 11 cyfr bez separatorów.",
       "### ### ### ##", "###########"),
    ep("pesel-b", ["pesel pełnomocnika"], "PESEL", "Usuń myślniki i spacje z numeru PESEL.",
       "###-###-###-##", "###########"),
    ep("pesel-c", ["pesel"], "PESEL", "PESEL zapisz jako 11 cyfr bez spacji i myślników.",
       "#### ### ####", "###########"),
]
ZIP = [
    ep("zip-a", ["kod pocztowy"], "ZIP_CODE", "Wstaw myślnik po drugiej cyfrze kodu pocztowego.",
       "#####", "##-###"),
    ep("zip-b", ["kod pocztowy korespondencyjny"], "ZIP_CODE",
       "Zapisz kod pocztowy w formacie XX-XXX z myślnikiem.", "#####", "##-###"),
]
OTHER = ep("email-a", ["e-mail"], "EMAIL", "Przytnij spacje wokół adresu e-mail.")


@pytest.fixture
def store(tmp_path):
    s = SkillStore(tmp_path / "m.sqlite")
    for sk in PESEL + ZIP + [OTHER]:
        s.create(sk)
    yield s
    s.close()


class FakeJudge:
    """p(same) by validator: same validator -> `same`, else `diff`; optional per-order map."""

    def __init__(self, same=0.9, diff=0.1, table=None, fail=False):
        self.same, self.diff, self.table, self.fail = same, diff, table or {}, fail
        self.calls: list[tuple[str, str]] = []

    async def p_same(self, a, b):
        if self.fail:
            raise AdapterError("basal down")
        self.calls.append((a.id, b.id))
        if (a.id, b.id) in self.table:
            return self.table[(a.id, b.id)]
        return self.same if a.trigger.validator == b.trigger.validator else self.diff


def merge_draft(cluster, **over):
    ids = [s.id for s in cluster]
    labels = sorted({lab for s in cluster for lab in s.trigger.labels_any})
    snippet = "Zapisz wartość bez separatorów w ustalonym formacie."
    d = Draft(decision="merge", absorbs=ids, evidence=[{"sentence": snippet, "from": ids[:2]}],
              skill={"name": f"gen {cluster[0].trigger.validator}", "description": "ogólna",
                     "trigger": {"labels_any": labels, "validator": cluster[0].trigger.validator},
                     "prompt_snippet": snippet}, cost=0.01)
    for k, v in over.items():
        setattr(d, k, v)
    return d


class FakeGen:
    def __init__(self, fn=None):
        self.fn = fn or (lambda cluster, errors: merge_draft(cluster))
        self.calls = 0

    async def generalize(self, cluster, neighbours, errors=None):
        self.calls += 1
        return self.fn(cluster, errors)


class FakeReplay:
    """Accuracy from a function of the skill set; `fail_on` raises (infra error)."""

    def __init__(self, fn=None, fail_on=None):
        self.fn = fn or (lambda ids: {"f1": True, "f2": True})
        self.fail_on = fail_on
        self.runs: list[tuple[set[str], tuple]] = []

    async def replay(self, skills, forms, focus=None):
        ids = {s.id for s in skills}
        self.runs.append((ids, forms))
        if self.fail_on and self.fail_on(ids, len(self.runs)):
            raise AdapterError("replay infra")
        cases = self.fn(ids)
        return ReplayResult(sum(cases.values()) / len(cases), cases, cost=0.1)


class FakeChecker:
    def __init__(self, faithful=0.9, conflict=0.1):
        self.f, self.c = faithful, conflict

    async def faithful(self, original, draft):
        return self.f

    async def conflict(self, draft, other):
        return {"conflict": self.c, "compatible": 1 - self.c, "duplicate": 0.0}


def deps(**kw):
    return SleepDeps(judge=kw.get("judge", FakeJudge()), generalizer=kw.get("gen", FakeGen()),
                     replayer=kw.get("replay", FakeReplay()), checker=kw.get("checker"))


def run(store, d, cfg=None, **kw):
    return asyncio.run(run_cycle(store, d, cfg or SleepConfig(), **kw))


def settings(**kw):
    return Settings(_env_file=None, **kw)


# ---------------------------------------------------------------- triggers


def test_cron_parse_and_match():
    c = Cron.parse("0 3 * * *")
    assert c.matches(NOW) and not c.matches(NOW.replace(minute=1))
    c = Cron.parse("*/15 8-10 * * 1-5")
    assert c.matches(datetime(2026, 11, 23, 9, 30)) and not c.matches(datetime(2026, 11, 22, 9, 30))
    assert Cron.parse("0 0 * * 7").matches(datetime(2026, 11, 22, 0, 0))  # Sunday
    for bad in ("* * *", "61 * * * *", "*/0 * * * *", "a * * * *"):
        with pytest.raises(CronError):
            Cron.parse(bad)


def test_should_sleep_triggers(store):
    s = settings(sleep_min_patterns=2)
    assert should_sleep(store, s, NOW, manual=True).reason == "manual"
    cron = Cron.parse("0 3 * * *")
    assert should_sleep(store, s, NOW, cron=cron).reason == "cron"
    assert not should_sleep(store, s, NOW, cron=cron, last_cron_minute=NOW).go
    assert should_sleep(store, s, NOW + timedelta(minutes=5), cron=cron).reason == "idle"
    for _ in range(2):
        store.add_episode(Episode(kind=EpisodeKind.WORKER_CORRECTION, status=EpisodeStatus.SKILLED))
    store.add_episode(Episode(kind=EpisodeKind.WORKER_CORRECTION, status=EpisodeStatus.IGNORED))
    d = should_sleep(store, s, NOW + timedelta(minutes=5))
    assert d.go and d.reason == "patterns:2"
    assert not should_sleep(store, s, NOW, last_pattern_count=2).go  # nothing new since


def test_pinned_refuses_even_manual(store, tmp_path):
    assert should_sleep(store, settings(skills_snapshot=1), NOW, manual=True).reason == "skipped: pinned"
    pinned = SkillStore(tmp_path / "m.sqlite", snapshot=1)
    out = run(pinned, deps())
    assert out["status"] == "skipped: pinned"
    assert state.cycles(pinned)[-1]["status"] == "skipped: pinned"
    pinned.close()


def test_lock_one_cycle_at_a_time(store):
    assert state.try_lock(store, "other")
    assert should_sleep(store, settings(), NOW, manual=True).reason == "skipped: locked"
    assert run(store, deps())["status"] == "skipped: locked"
    state.release(store, "other")
    assert run(store, deps())["status"] == "ok"
    assert not state.locked(store)  # released after the cycle


def test_stale_lock_is_taken_over(store):
    assert state.try_lock(store, "dead")
    assert state.try_lock(store, "me", stale_s=0)


# ---------------------------------------------------------------- blocking / judge / cluster


def test_pair_budget_and_topk_bounds():
    many = [ep(f"p-{i:02d}", [f"pesel osoby {i}"], "PESEL", f"Usuń separatory z PESEL wariant {i}.")
            for i in range(30)]
    pairs, total = candidate_pairs(many, budget=1000, k=3)
    assert total <= 30 * 3 and len(pairs) == total
    cut, total2 = candidate_pairs(many, budget=10, k=3)
    assert len(cut) == 10 and total2 == total
    assert [p.cos for p in cut] == sorted((p.cos for p in cut), reverse=True)


def test_blocking_keeps_validators_apart():
    pairs, _ = candidate_pairs(PESEL + ZIP, budget=100)
    for p in pairs:
        assert p.a.split("-")[0] == p.b.split("-")[0]


def test_min_of_both_orders_on_borderline():
    p, edge = edge_decision(0.65, 0.4, cos=0.5)
    assert p == 0.4 and not edge
    p, edge = edge_decision(0.65, 0.62, cos=0.5)
    assert p == 0.62 and edge

    a, b = PESEL[0], PESEL[1]
    pair = Pair(a.id, b.id, 0.5)
    judge = FakeJudge(table={(a.id, b.id): 0.65, (b.id, a.id): 0.3})
    out, calls = asyncio.run(judge_pairs([pair], {a.id: a, b.id: b}, judge))
    assert calls == 2 and out[0].p == 0.3 and not out[0].edge
    judge = FakeJudge(table={(a.id, b.id): 0.95})
    out, calls = asyncio.run(judge_pairs([pair], {a.id: a, b.id: b}, judge))
    assert calls == 1 and out[0].edge  # confident: no reverse call


def test_never_merge_on_basal_alone():
    # basal certain, but the pair is below the retrieval floor: no edge
    assert edge_decision(0.99, None, cos=0.1) == (0.99, False)
    # and unrelated skills never even reach basal (not proposed by the floor)
    judge = FakeJudge(same=1.0, diff=1.0)
    pairs, _ = candidate_pairs([PESEL[0], OTHER], budget=100)
    asyncio.run(judge_pairs(pairs, {s.id: s for s in (PESEL[0], OTHER)}, judge))
    assert ("pesel-a", "email-a") not in judge.calls


def test_cluster_size_cap():
    ids = [f"s{i}" for i in range(10)]
    judged = [Judged(Pair(ids[i], ids[i + 1], 0.9), 0.9 - i / 100, None, 0.9 - i / 100, True)
              for i in range(9)]
    cl = clusters_from_edges(judged, cap=4)
    assert all(len(c.members) <= 4 for c in cl) and sum(len(c.members) for c in cl) >= 8


# ---------------------------------------------------------------- generalize


def test_generalize_guardrails():
    skill, absorbs, errs = check_draft(merge_draft(PESEL), PESEL)
    assert not errs and skill.layer is Layer.GENERAL and set(absorbs) == {s.id for s in PESEL}
    d = merge_draft(PESEL)
    d.skill["trigger"]["labels_any"] = ["pesel", "numer dowodu"]
    assert any("wider" in e for e in check_draft(d, PESEL)[2])
    d = merge_draft(PESEL, evidence=[{"sentence": d.skill["prompt_snippet"], "from": ["pesel-a"]}])
    assert any(">= 2 sources" in e for e in check_draft(d, PESEL)[2])
    d = merge_draft(PESEL, not_covered=["pesel-c"])
    assert check_draft(d, PESEL)[1] == ["pesel-a", "pesel-b"]  # partial absorb
    d = merge_draft(PESEL)
    d.skill["prompt_snippet"] = "Wpisz 12345678901 jako PESEL."
    d.evidence = [{"sentence": d.skill["prompt_snippet"], "from": ["pesel-a", "pesel-b"]}]
    assert any("PII" in e for e in check_draft(d, PESEL)[2])


def test_generalize_split_reject_and_retry():
    def split(cluster, errors):
        if len(cluster) == 3:
            return Draft(decision="split", groups=[["pesel-a", "pesel-b"], ["pesel-c"]])
        return merge_draft(cluster)
    out = asyncio.run(generalize_cluster(FakeGen(split), PESEL, []))
    assert [r.status for r in out] == ["split", "merged"] and out[1].members == ["pesel-a", "pesel-b"]

    out = asyncio.run(generalize_cluster(FakeGen(lambda c, e: Draft("reject")), PESEL, []))
    assert out[0].status == "rejected"

    def bad_then_good(cluster, errors):
        d = merge_draft(cluster)
        if errors is None:
            d.skill["trigger"]["labels_any"].append("nowe słowo")
        return d
    gen = FakeGen(bad_then_good)
    out = asyncio.run(generalize_cluster(gen, PESEL, []))
    assert out[0].status == "merged" and gen.calls == 2


# ---------------------------------------------------------------- full cycle


def test_cycle_gate_pass_activates_and_retires(store):
    store.patch("pesel-a", {"provenance": {"episode_ids": ["ep_x"]}})
    store.add_episode(Episode(id="ep_x", kind=EpisodeKind.WORKER_CORRECTION))
    v0 = store.current_version()
    out = run(store, deps(checker=FakeChecker()))
    assert out["status"] == "ok" and out["activated"] == 2 and out["proposed"] == 0
    assert out["clusters"] == 2 and out["detail"]["pairs_judged"] > 0
    gen = [s for s in store.active() if s.layer is Layer.GENERAL]
    assert len(gen) == 2
    for s in PESEL + ZIP:
        got = store.get(s.id)
        assert got.status is SkillStatus.RETIRED and got.absorbed_into in {g.id for g in gen}
    assert store.get("email-a").status is SkillStatus.ACTIVE
    assert store.current_version() == v0 + 1  # one version for the whole activation
    assert store.episodes()[0].consolidated


def test_cycle_gate_fail_proposes(store):
    # any skill set without pesel-b regresses case f2
    rep = FakeReplay(lambda ids: {"f1": True, "f2": "pesel-b" in ids})
    v0 = store.current_version()
    out = run(store, deps(replay=rep))
    assert out["activated"] == 1 and out["proposed"] == 1
    assert store.get("pesel-b").status is SkillStatus.ACTIVE
    assert store.list_skills(status=SkillStatus.PROPOSED) == []  # P2 style: diff only
    [item] = review.items(store)
    assert item.kind == "proposed_skill" and item.diff["source"] == "sleep"
    assert item.diff["skill"]["trigger"]["validator"] == "PESEL"
    assert {a["skill_id"] for a in item.diff["absorbs"]} == {"pesel-a", "pesel-b", "pesel-c"}
    proposed = [SimpleNamespace(id=item.ref_id)]
    assert out["detail"]["gate"][proposed[0].id]["regressions"] == ["f2"]
    assert store.current_version() == v0 + 1


def test_gate_uses_only_gate_forms_and_heldout_only_measures(store):
    rep = FakeReplay()
    run(store, deps(replay=rep))
    assert len(GATE_FORMS) == 6 and not set(GATE_FORMS) & set(HELDOUT_FORMS)
    assert [f for _, f in rep.runs].count(HELDOUT_FORMS) == 1
    assert rep.runs[-1][1] == HELDOUT_FORMS  # measured last, after every decision


def test_gate_pass_rule():
    r = lambda acc, **c: ReplayResult(acc, c)
    assert gate_pass(r(0.5, a=True, b=False), r(0.5, a=True, b=False))
    assert not gate_pass(r(0.5, a=True, b=False), r(0.5, a=False, b=True))  # new regression
    assert not gate_pass(r(0.8, a=True), r(0.7, a=True))


def test_checks_fail_proposes_without_replay(store):
    rep = FakeReplay()
    out = run(store, deps(checker=FakeChecker(faithful=0.2), replay=rep))
    assert out["activated"] == 0 and out["proposed"] == 2
    assert rep.runs == []  # no gate run (nothing to activate), held-out skipped: unchanged
    assert out["detail"]["heldout_skipped"]


def test_replay_infra_error_aborts_untouched(store):
    v0 = store.current_version()
    before = {s.id for s in store.active()}
    out = run(store, deps(replay=FakeReplay(fail_on=lambda ids, n: n == 2)))
    assert out["status"] == "aborted: replay"
    assert store.current_version() == v0 and {s.id for s in store.active()} == before
    assert not state.locked(store)


def test_judge_infra_error_aborts(store):
    out = run(store, deps(judge=FakeJudge(fail=True)))
    assert out["status"] == "aborted: judge" and not state.locked(store)


def test_rollback_restores_episodics(store):
    v0 = store.current_version()
    run(store, deps())
    assert store.get("pesel-a").status is SkillStatus.RETIRED
    store.rollback(v0)
    assert {s.id for s in store.active()} == {s.id for s in PESEL + ZIP + [OTHER]}


# ---------------------------------------------------------------- prune


def test_prune_rules():
    old = NOW - timedelta(days=30)
    skills = [
        ep("exp", ["a"], "PESEL", "x.", expires_at=NOW - timedelta(days=1)),
        ep("lose", ["b"], "NIP", "x.", hits=5, wins=1, losses=4),
        ep("pin", ["c"], "NIP", "x.", hits=5, losses=5, pinned=True),
        Skill(id="seed-x", name="seed-x", layer=Layer.SEED, description="s",
              trigger=SkillTrigger(validator="NIP"), losses=9),
    ]
    acts = {a.skill_id: a.reason for a in plan_prune(skills, NOW, {}, 20, 50, 14)}
    assert acts == {"exp": "expired", "lose": "loser"}

    g = Skill(id="gen-zip", name="gen-zip", layer=Layer.GENERAL, description="z",
              trigger=SkillTrigger(labels_any=["kod pocztowy"], validator="ZIP_CODE"),
              prompt_snippet=ZIP[0].prompt_snippet, pattern=ZIP[0].pattern)
    acts = plan_prune([g, ZIP[0]], NOW, {}, 20, 50, 14)
    assert [(a.skill_id, a.action, a.absorbed_into) for a in acts] == [("zip-a", "retire", "gen-zip")]

    many = [ep(f"e{i}", [f"l{i}"], None, "x.", hits=1, wins=i) for i in range(5)]
    young = ep("young", ["ly"], None, "x.")  # zero use, inside grace: spared
    created = {s.id: old for s in many} | {"young": NOW}
    acts = plan_prune(many + [young], NOW, created, 3, 50, 14)
    assert sorted(a.skill_id for a in acts) == ["e0", "e1", "e2"]


def test_prune_mini_replay_restores_on_regression(store):
    store.create(ep("loser-x", ["nip"], "NIP", "Usuń kreski z NIP.", ))
    for _ in range(4):
        store.record_hit("loser-x", 1, "loss")
    # removing loser-x regresses case f1: prune must be undone
    rep = FakeReplay(lambda ids: {"f1": "loser-x" in ids, "f2": True})
    out = run(store, deps(judge=FakeJudge(same=0.0, diff=0.0), replay=rep))
    assert out["detail"]["restored"] == ["loser-x"] and out["pruned"] == 0
    assert store.get("loser-x").status is SkillStatus.ACTIVE

    rep = FakeReplay()
    out = run(store, deps(judge=FakeJudge(same=0.0, diff=0.0), replay=rep))
    assert out["pruned"] == 1 and store.get("loser-x").status is SkillStatus.ARCHIVED


# ---------------------------------------------------------------- metrics


def test_metrics_row(store):
    out = run(store, deps(), trigger="cron")
    rows = state.cycles(store)
    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == out["id"] and r["trigger"] == "cron" and r["status"] == "ok"
    assert r["candidates"] == 6 and r["clusters"] == 2 and r["activated"] == 2
    assert r["acc_gate_before"] == 1.0 and r["acc_gate_after"] == 1.0 and r["acc_heldout"] == 1.0
    assert r["skill_count"] == 3 and r["cost"] > 0 and r["ended_at"]
    assert {"select", "judge", "gate", "prune"} <= r["detail"]["timings"].keys()


def test_approving_a_sleep_proposal_retires_absorbed(store):
    from coi_formularze.morpheus.wake import Wake
    rep = FakeReplay(lambda ids: {"f1": True, "f2": "pesel-b" in ids})
    run(store, deps(replay=rep))
    [item] = review.items(store)
    res = Wake(store, settings()).approve(item.id)
    assert res["skill_id"] == item.ref_id and set(res["absorbed"]) == {"pesel-a", "pesel-b", "pesel-c"}
    gen = store.get(item.ref_id)
    assert gen.status is SkillStatus.ACTIVE and gen.layer is Layer.GENERAL and gen.expires_at is None
    assert store.get("pesel-b").absorbed_into == item.ref_id
    assert review.items(store) == []


# ---------------------------------------------------------------- real adapters (no network)


def test_gate_replayer_focus_cache_and_scoring(tmp_path):
    from coi_formularze.morpheus.sleep.adapters import GateReplayer
    from tests.test_morpheus_wake import REPLAY, Chat
    from tests.test_morpheus_zip_scope import LEARNED_ZIP

    (tmp_path / "f.md").write_text("Adres zamieszkania\nKod pocztowy ______\n")
    labels = {"f": [{"title": "Adres zamieszkania", "fields": [
        {"label": "Kod pocztowy", "validation": ["REQUIRED", "ZIP_CODE"]}]}]}
    chat = Chat()
    rp = GateReplayer(chat, REPLAY, str(tmp_path), labels=labels)
    without = asyncio.run(rp.replay([], ("f",)))
    with_zip = asyncio.run(rp.replay([LEARNED_ZIP], ("f",)))
    assert without.per_case == {"f/Adres zamieszkania/Kod pocztowy": False}
    assert with_zip.accuracy == 1.0 and rp.calls == 2
    asyncio.run(rp.replay([LEARNED_ZIP, PESEL[0]], ("f",)))  # PESEL doesn't touch it: cached
    assert rp.calls == 2 and rp.cache_hits == 1
    none = asyncio.run(rp.replay([LEARNED_ZIP], ("f",), focus=[PESEL[0]]))
    assert none.per_case == {} and rp.calls == 2
    with pytest.raises(AdapterError):
        asyncio.run(rp.replay([], ("unknown",)))


def test_basal_judge_uses_wake_client(monkeypatch):
    from coi_formularze.morpheus import basal
    from coi_formularze.morpheus.sleep.adapters import BasalJudge
    from coi_formularze.route.router import BasalUnavailable

    seen = []

    async def fake(state, questions, url, timeout, client=None):
        seen.append((state, questions))
        return {"same": {"choice": "same", "probabilities": {"same": 0.8, "different": 0.2}}}
    monkeypatch.setattr(basal, "ask_questions", fake)
    j = BasalJudge("http://x")
    assert asyncio.run(j.p_same(PESEL[0], PESEL[1])) == 0.8
    assert seen[0][1] == {"same": basal.SLEEP_QUESTIONS["same"]}
    assert "UMIEJĘTNOŚĆ A" in seen[0][0] and "UMIEJĘTNOŚĆ B" in seen[0][0]

    async def down(*a, **k):
        raise BasalUnavailable("down")
    monkeypatch.setattr(basal, "ask_questions", down)
    with pytest.raises(AdapterError):
        asyncio.run(j.p_same(PESEL[0], PESEL[1]))


def test_llm_generalizer_uses_writer_prompt():
    import json as _json

    from coi_formularze.morpheus.sleep.adapters import LlmGeneralizer
    from coi_formularze.parse.llm import Completion

    class C:
        async def complete(self, model, messages, schema):
            self.messages = messages
            d = merge_draft(PESEL)
            return Completion(_json.dumps({"decision": "merge", "skill": d.skill, "absorbs": d.absorbs,
                                           "evidence": d.evidence}), "stop", 0.02, model)
    chat = C()
    draft = asyncio.run(LlmGeneralizer(chat, "hard").generalize(PESEL, [ZIP[0]]))
    assert draft.decision == "merge" and draft.cost == 0.02
    assert "Uogólniaj tylko to, co WSPÓLNE" in chat.messages[0]["content"]
    assert '"zip-a"' in chat.messages[1]["content"]  # neighbours passed for non-overlap
    assert not check_draft(draft, PESEL)[2]


def test_code_edge_for_identical_rules_without_basal():
    from coi_formularze.morpheus.models import ValidatorRule
    rule = ValidatorRule(add_validators=["PESEL"], post_fix="digits_only")
    a = PESEL[0].model_copy(update={"validator_rule": rule})
    b = PESEL[1].model_copy(update={"validator_rule": rule})
    c = PESEL[2].model_copy(update={"validator_rule": ValidatorRule(required=False)})
    judge = FakeJudge(same=0.0)
    by = {s.id: s for s in (a, b, c)}
    out, calls = asyncio.run(judge_pairs([Pair(a.id, b.id, 0.5), Pair(a.id, c.id, 0.5),
                                          Pair(a.id, b.id, 0.1)], by, judge))
    assert [(j.edge, j.source) for j in out] == [(True, "code"), (False, "basal"), (False, "code")]
    assert calls == 1  # only the a-c pair went to basal; below the floor no edge either
    out, _ = asyncio.run(judge_pairs([Pair(a.id, b.id, 0.5)], by, judge, code_edges=False))
    assert not out[0].edge


def test_evidence_matches_sentences_loosely():
    d = merge_draft(PESEL)
    snippet = d.skill["prompt_snippet"]
    d.evidence = [{"sentence": snippet.rstrip(".") + " zawsze", "from": ["pesel-a", "pesel-b"]}]
    assert not check_draft(d, PESEL)[2]
    d.evidence = [{"sentence": "Coś zupełnie innego o kodzie.", "from": ["pesel-a", "pesel-b"]}]
    assert check_draft(d, PESEL)[2]
