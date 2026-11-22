"""One sleep cycle (sleep.md §1 as revised by design.md).

lock -> snapshot N -> candidates (cap) -> retrieval floor + top-k pairs (budget) -> basal
same/different (min of both orders on borderline) -> clusters (cap 8) -> generalize
(split/reject allowed, guardrails) -> faithful/conflict checks -> replay gate on the 6 gate
forms -> activate N+1 | propose | abort on infra error -> prune + mini replay (restore on
regression) -> held-out measurement -> `sleep_cycles` row -> unlock.
"""

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from coi_formularze.morpheus import review
from coi_formularze.morpheus.models import Actor, Layer, Skill, SkillStatus
from coi_formularze.morpheus.sleep import state
from coi_formularze.morpheus.sleep.adapters import (
    GATE_FORMS,
    HELDOUT_FORMS,
    AdapterError,
    Checker,
    Generalizer,
    Replayer,
    ReplayResult,
    SameJudge,
)
from coi_formularze.morpheus.sleep.blocking import (
    block_key,
    candidate_pairs,
    cosine,
    skill_doc,
    tfidf_vectors,
)
from coi_formularze.morpheus.sleep.cluster import clusters_from_edges, judge_pairs
from coi_formularze.morpheus.sleep.generalize import GenResult, generalize_cluster
from coi_formularze.morpheus.sleep.prune import plan_prune
from coi_formularze.morpheus.store import SkillStore

log = logging.getLogger(__name__)

CONFLICT_MIN = 0.6  # provisional (sleep.md §2d), untested
MAX_REPLAYS = 10
NEIGHBOURS = 3


@dataclass
class SleepDeps:
    judge: SameJudge
    generalizer: Generalizer
    replayer: Replayer
    checker: Checker | None = None


@dataclass
class SleepConfig:
    max_candidates: int = 60
    pair_budget: int = 300
    same_min: float = 0.6
    faithful_min: float = 0.75
    episodic_max: int = 20
    general_max: int = 50
    grace_days: int = 14
    max_replays: int = MAX_REPLAYS
    measure_heldout: bool = True
    code_edges: bool = True

    @classmethod
    def from_settings(cls, s) -> SleepConfig:
        return cls(s.sleep_max_candidates, s.sleep_pair_budget, s.same_pattern_min,
                   s.faithful_min, s.episodic_max, s.general_max, s.episodic_ttl_days)


@dataclass
class _Run:
    id: str
    trigger: str
    started: datetime
    timings: dict[str, float] = field(default_factory=dict)
    detail: dict = field(default_factory=dict)
    cost: float = 0.0
    t0: float = field(default_factory=time.perf_counter)

    def lap(self, name: str) -> None:
        now = time.perf_counter()
        self.timings[name] = round(now - self.t0, 3)
        self.t0 = now


def regressions(before: ReplayResult, after: ReplayResult) -> list[str]:
    return sorted(c for c, ok in before.per_case.items() if ok and not after.per_case.get(c, False))


def gate_pass(before: ReplayResult, after: ReplayResult) -> bool:
    """No case that passed under N fails now, and accuracy does not drop."""
    return not regressions(before, after) and after.accuracy >= before.accuracy


def select_candidates(store: SkillStore, cap: int) -> list[Skill]:
    """Active episodic skills (plus general ones that lose), most-used first, then oldest."""
    pool = [s for s in store.active()
            if not s.absorbed_into and (s.layer is Layer.EPISODIC or
                                        (s.layer is Layer.GENERAL and s.losses > s.wins))]
    created = {s.id: state.skill_created_at(store, s.id) for s in pool}
    pool.sort(key=lambda s: (-s.hits, created[s.id] or datetime.max.replace(tzinfo=UTC), s.id))
    return pool[:cap]


def _neighbours(draft: Skill, members: set[str], active: list[Skill], k: int) -> list[Skill]:
    others = [s for s in active if s.id not in members and
              (block_key(s) == block_key(draft) or "*" in (block_key(s), block_key(draft)))]
    if not others:
        return []
    vecs = tfidf_vectors([skill_doc(draft)] + [skill_doc(s) for s in others])
    scored = sorted(zip((cosine(vecs[0], v) for v in vecs[1:]), others, strict=True),
                    key=lambda p: -p[0])
    return [s for c, s in scored[:k] if c > 0]


async def _checks(deps: SleepDeps, cfg: SleepConfig, g: GenResult, by_id: dict[str, Skill],
                  active: list[Skill]) -> tuple[bool, dict]:
    """faithful (min over absorbed >= floor) and no conflict with neighbours. Advisory
    tripwires; a failure (or a checker error) sends the draft to review, never activates."""
    if deps.checker is None:
        return True, {"checked": False}
    rep: dict = {"checked": True}
    try:
        f = [await deps.checker.faithful(by_id[a], g.skill) for a in g.absorbs]
        rep["faithful_min"] = round(min(f), 3)
        conflicts = {}
        for n in _neighbours(g.skill, set(g.absorbs), active, 5):
            p = await deps.checker.conflict(g.skill, n)
            conflicts[n.id] = round(p.get("conflict", 0.0), 3)
        rep["conflict_max"] = max(conflicts.values(), default=0.0)
        rep["conflicts"] = conflicts
    except AdapterError as exc:
        rep["error"] = str(exc)
        return False, rep
    ok = rep["faithful_min"] >= cfg.faithful_min and rep["conflict_max"] < CONFLICT_MIN
    return ok, rep


def _propose(store: SkillStore, g: GenResult, why: str, replay: dict | None) -> str:
    """Stage a draft in P2's review queue (diff JSON, no `proposed` skill row). Approving it
    (`Wake.approve`) activates the general skill and retires the absorbed ones."""
    diff = {"skill": g.skill.model_dump(mode="json"), "parent": None, "source": "sleep",
            "absorbs": [{"skill_id": a} for a in g.absorbs], "why": why,
            "changes": {k: [None, v] for k, v in g.skill.model_dump(
                mode="json", include={"description", "trigger", "prompt_snippet",
                                      "validator_rule"}).items()}}
    return review.enqueue(store, "proposed_skill", g.skill.id, diff=diff, replay=replay).id


def activate_general(store: SkillStore, skill: Skill, absorbs: list[str],
                     actor: Actor = Actor.SLEEP) -> Skill:
    """Create (or re-activate) a general skill and retire what it absorbs, in one batch."""
    with store.batch(f"activate general {skill.id}"):
        live = skill.model_copy(update={"status": SkillStatus.ACTIVE, "expires_at": None})
        if store.get(skill.id) is None:
            store.create(live, actor)
        else:
            store.activate(skill.id, actor)
        for a in absorbs:
            cur = store.get(a)
            if cur is not None and cur.status is SkillStatus.ACTIVE:
                store.retire(a, actor, absorbed_into=skill.id)
    return store.get(skill.id)  # type: ignore[return-value]


def _mark_consolidated(store: SkillStore, skills: list[Skill]) -> int:
    ids = [e for s in skills for e in s.provenance.get("episode_ids", []) if isinstance(e, str)]
    if not ids:
        return 0
    with store._lock:
        cur = store._db.execute(
            f"UPDATE episodes SET consolidated=1 WHERE id IN ({','.join('?' * len(ids))})", ids)
    return cur.rowcount


async def run_cycle(store: SkillStore, deps: SleepDeps, cfg: SleepConfig | None = None,
                    trigger: str = "manual", pinned: bool = False) -> dict:
    """Run one cycle; returns the summary (also logged to `sleep_cycles`)."""
    cfg = cfg or SleepConfig()
    run = _Run(f"sc_{uuid.uuid4().hex[:12]}", trigger, datetime.now(UTC))
    row = {"id": run.id, "started_at": run.started.isoformat(), "trigger": trigger}

    def finish(status: str, **extra) -> dict:
        row.update(extra, ended_at=datetime.now(UTC).isoformat(),
                   skill_count=len(store.active()), cost=round(run.cost, 6))
        run.detail.update(status=status, timings=run.timings)
        state.write_cycle(store, row, status, run.detail)
        return {**row, "status": status, "detail": run.detail}

    if pinned or store.snapshot is not None:
        return finish("skipped: pinned")
    if not state.try_lock(store, run.id):
        return finish("skipped: locked")
    try:
        return await _cycle(store, deps, cfg, run, row, finish)
    finally:
        state.release(store, run.id)


async def _cycle(store, deps, cfg, run, row, finish) -> dict:
    d = run.detail
    v_before = store.current_version()
    d["version_before"] = v_before
    active = store.active()
    cands = select_candidates(store, cfg.max_candidates)
    by_id = {s.id: s for s in active}
    row["candidates"] = len(cands)
    run.lap("select")

    pairs, n_pairs = candidate_pairs(cands, cfg.pair_budget)
    d.update(pairs_considered=n_pairs, pairs_judged=len(pairs))
    run.lap("blocking")
    try:
        judged, calls = await judge_pairs(pairs, by_id, deps.judge, cfg.same_min,
                                          cfg.code_edges)
    except AdapterError as exc:
        d["error"] = str(exc)
        run.lap("judge")
        return finish("aborted: judge")
    d.update(basal_calls=calls, edges=sum(j.edge for j in judged),
             reverse_calls=sum(j.p_ba is not None for j in judged),
             code_edges=sum(j.edge and j.source == "code" for j in judged),
             judged=[{"a": j.pair.a, "b": j.pair.b, "cos": round(j.pair.cos, 3),
                      "p_ab": round(j.p_ab, 3),
                      "p_ba": None if j.p_ba is None else round(j.p_ba, 3), "edge": j.edge,
                      "source": j.source}
                     for j in judged])
    state.heartbeat(store, run.id)
    run.lap("judge")

    clusters = clusters_from_edges(judged)
    row["clusters"] = len(clusters)
    d["clusters"] = [c.members for c in clusters]
    run.lap("cluster")

    results: list[GenResult] = []
    for c in clusters:
        members = [by_id[m] for m in c.members]
        neigh = _neighbours(members[0], set(c.members), active, NEIGHBOURS)
        results += await generalize_cluster(deps.generalizer, members, neigh)
        state.heartbeat(store, run.id)
    run.cost += sum(r.cost for r in results)
    drafts = [r for r in results if r.status == "merged"]
    row["drafts"] = len(drafts)
    d["generalize"] = [{"members": r.members, "status": r.status, "skill": r.skill.id if r.skill else None,
                        "absorbs": r.absorbs, "errors": r.errors[:5], "risk": r.risk[:200],
                        "calls": r.calls} for r in results]
    run.lap("generalize")

    checked: list[tuple[GenResult, dict]] = []
    proposed: list[str] = []
    d["checks"] = {}
    for g in drafts:
        ok, rep = await _checks(deps, cfg, g, by_id, active)
        d["checks"][g.skill.id] = {**rep, "passed": ok}
        if ok:
            checked.append((g, rep))
        else:
            _propose(store, g, "checks failed", {"checks": rep})
            proposed.append(g.skill.id)
    run.lap("checks")

    # Replay gate. Each draft is replayed on the gate sections its own and its absorbed
    # skills' triggers touch (focus), against the current set as reference; cached per
    # (form, section, touching skills), so references are reused.
    accepted: list[GenResult] = []
    focus_all = [g.skill for g, _ in checked] + [by_id[a] for g, _ in checked for a in g.absorbs]
    base = None
    if checked:
        try:
            base = await deps.replayer.replay(active, GATE_FORMS, focus_all)
            run.cost += base.cost
            current = list(active)
            for i, (g, _rep) in enumerate(checked):
                if i >= cfg.max_replays:
                    d.setdefault("deferred", []).append(g.skill.id)
                    continue
                focus = [g.skill] + [by_id[a] for a in g.absorbs]
                ref = await deps.replayer.replay(current, GATE_FORMS, focus)
                trial = [s for s in current if s.id not in g.absorbs] + [g.skill]
                res = await deps.replayer.replay(trial, GATE_FORMS, focus)
                run.cost += ref.cost + res.cost
                report = {"acc_before": round(ref.accuracy, 4), "acc_after": round(res.accuracy, 4),
                          "cases": len(res.per_case), "regressions": regressions(ref, res),
                          "fixed": regressions(res, ref)}
                if gate_pass(ref, res):
                    accepted.append(g)
                    current = trial
                else:
                    _propose(store, g, "replay gate failed", report)
                    proposed.append(g.skill.id)
                d.setdefault("gate", {})[g.skill.id] = report
                state.heartbeat(store, run.id)
            after = await deps.replayer.replay(current, GATE_FORMS, focus_all)
            run.cost += after.cost
            row["acc_gate_after"] = after.accuracy
        except AdapterError as exc:
            d["error"] = str(exc)
            run.lap("gate")
            return finish("aborted: replay", proposed=len(proposed))
    run.lap("gate")
    row["acc_gate_before"] = base.accuracy if base else None

    v_post = v_before
    if accepted:
        with store.batch(f"sleep {run.id}: activate {len(accepted)} general"):
            for g in accepted:
                activate_general(store, g.skill, g.absorbs)
        _mark_consolidated(store, [by_id[a] for g in accepted for a in g.absorbs])
        v_post = store.current_version()
    row.update(activated=len(accepted), proposed=len(proposed))
    d.update(activated=[g.skill.id for g in accepted], proposed_ids=proposed, version_post=v_post)
    run.lap("activate")

    # ---- prune + mini replay
    try:
        pruned, restored = await _prune(store, deps, cfg, run)
    except AdapterError as exc:
        d["error"] = str(exc)
        if store.current_version() != v_post:
            store.rollback(v_post, Actor.SLEEP)
        pruned, restored = [], []
    row["pruned"] = 0 if restored else len(pruned)
    d.update(pruned=pruned, restored=restored)
    run.lap("prune")

    row.setdefault("acc_gate_after", row["acc_gate_before"])
    if cfg.measure_heldout and store.current_version() == v_before:
        d["heldout_skipped"] = "active set unchanged"
    elif cfg.measure_heldout:
        try:
            ho = await deps.replayer.replay(store.active(), HELDOUT_FORMS)
            run.cost += ho.cost
            row["acc_heldout"] = ho.accuracy
        except AdapterError as exc:
            d["heldout_error"] = str(exc)
    d["version_after"] = store.current_version()
    run.lap("heldout")
    return finish("ok")


async def _prune(store, deps, cfg, run) -> tuple[list[dict], list[str]]:
    """Apply the prune plan, then a mini replay on the sections the pruned skills touched:
    any new failure (or lower accuracy) restores them (rollback to the pre-prune version)."""
    active = store.active()
    created = {s.id: state.skill_created_at(store, s.id) for s in active}
    plan = plan_prune(active, datetime.now(UTC), created, cfg.episodic_max, cfg.general_max,
                      cfg.grace_days)
    if not plan:
        return [], []
    by_id = {s.id: s for s in active}
    focus = [by_id[a.skill_id] for a in plan]
    ref = await deps.replayer.replay(active, GATE_FORMS, focus)
    run.cost += ref.cost
    v_post = store.current_version()
    with store.batch(f"sleep {run.id}: prune {len(plan)}"):
        for a in plan:
            if a.action == "retire":
                store.retire(a.skill_id, Actor.SLEEP, absorbed_into=a.absorbed_into)
            else:
                store.archive(a.skill_id, Actor.SLEEP)
    pruned = [{"skill_id": a.skill_id, "action": a.action, "reason": a.reason} for a in plan]
    mini = await deps.replayer.replay(store.active(), GATE_FORMS, focus)
    run.cost += mini.cost
    run.detail["prune_replay"] = {"acc_before": ref.accuracy, "acc_after": mini.accuracy,
                                  "regressions": regressions(ref, mini)}
    if not gate_pass(ref, mini):
        store.rollback(v_post, Actor.SLEEP)
        log.info("sleep %s: prune regressed the gate; restored %d skills", run.id, len(plan))
        return pruned, [a.skill_id for a in plan]
    return pruned, []


def summary_json(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, indent=1, default=str)
