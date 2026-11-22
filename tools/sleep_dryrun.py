"""Morpheus sleep dry run: real local basal for pair judging/checks, fake generalizer + replay.

Seeds synthetic episodic skills from the calibration pairs (docs/notes/morpheus/calibration/
pairs.json), runs one full sleep cycle and compares the clusters with the labels. No paid calls.

    basal-serve --model Remek/basal-omni-pico --port 8109 &
    uv run python tools/sleep_dryrun.py --pairs <pairs.json> --url http://127.0.0.1:8109 --out <dir>
"""

import argparse
import asyncio
import json
import statistics
import tempfile
import time
from pathlib import Path

from kodex.morpheus import Layer, Skill, SkillStore, SkillTrigger
from kodex.morpheus.match import fold
from kodex.morpheus.sleep import (
    Draft,
    ReplayResult,
    SleepConfig,
    SleepDeps,
    run_cycle,
)
from kodex.morpheus.sleep.adapters import BasalJudge

POSITIVE = [f"P{i:02d}" for i in range(1, 11)]
HARD = ["P16", "P17", "P18", "P19", "P22", "P24"]
VALIDATORS = {"pesel": "PESEL", "nip": "NIP", "regon": "REGON", "kod pocztowy": "ZIP_CODE",
              "e-mail": "EMAIL"}
# Hand labels beyond pairs.json (by operation): the A side of these hard-negative pairs
# does the same fix as a positive pair, so it belongs to that pair's group.
EXTRA_SAME = [("p16-a", "p01-a"), ("p17-a", "p02-a"), ("p19-a", "p05-a"), ("p24-a", "p04-a"),
              ("p10-a", "p03-a")]
FAMILIES = {"telefon": "phone", "data": "date", "checkbox": "checkbox", "ulica": "street"}


def to_skill(sid: str, s: dict) -> Skill:
    pole = fold(s["pole"])
    validator = next((v for k, v in VALIDATORS.items() if k in pole), None)
    family = None if validator else next((v for k, v in FAMILIES.items() if k in pole), None)
    return Skill(id=sid, name=sid, layer=Layer.EPISODIC, description=s["fragment"][:160],
                 trigger=SkillTrigger(labels_any=[s["pole"]], validator=validator,
                                      field_family=family),
                 pattern={"after": s["wzorzec"]}, prompt_snippet=s["fragment"])


def seed(pairs: list[dict]) -> tuple[list[Skill], list[tuple[str, str, bool, str]]]:
    """Unique skills from the chosen pairs + the labelled (a, b, same, pair id) list."""
    by_content: dict[str, str] = {}
    skills: list[Skill] = []
    labelled = []
    for p in pairs:
        if p["id"] not in POSITIVE + HARD:
            continue
        ids = []
        for side in "AB":
            key = json.dumps(p[side], sort_keys=True, ensure_ascii=False)
            if key not in by_content:
                sid = f"{p['id'].lower()}-{side.lower()}"
                by_content[key] = sid
                skills.append(to_skill(sid, p[side]))
            ids.append(by_content[key])
        labelled.append((ids[0], ids[1], p["same"], p["id"]))
    return skills, labelled


class UnionGeneralizer:
    """Fake: merge the cluster, union of labels, every member as evidence."""

    calls = 0

    async def generalize(self, cluster, neighbours, errors=None):
        self.calls += 1
        ids = [s.id for s in cluster]
        snippet = cluster[0].prompt_snippet
        t = cluster[0].trigger
        return Draft(decision="merge", absorbs=ids,
                     evidence=[{"sentence": x, "from": ids} for x in snippet.split(". ")],
                     skill={"name": f"gen {t.validator or t.field_family or 'rule'}",
                            "description": snippet,
                            "trigger": {"labels_any": sorted({x for s in cluster
                                                              for x in s.trigger.labels_any}),
                                        "validator": t.validator, "field_family": t.field_family},
                            "pattern": cluster[0].pattern, "prompt_snippet": snippet})


class PassReplay:
    async def replay(self, skills, forms, focus=None):
        return ReplayResult(1.0, {f: True for f in forms})


class TimedJudge(BasalJudge):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.lat: list[float] = []

    async def _ask(self, state, key):
        t = time.perf_counter()
        try:
            return await super()._ask(state, key)
        finally:
            self.lat.append((time.perf_counter() - t) * 1000)


def evaluate(clusters: list[list[str]], labelled, skills) -> dict:
    """Pairwise co-clustering vs expected groups (pairs.json "same" + EXTRA_SAME)."""
    parent = {s.id: s.id for s in skills}

    def find(x):
        while parent[x] != x:
            x = parent[x]
        return x
    for a, b, same, _ in labelled:
        if same:
            parent[find(b)] = find(a)
    for a, b in EXTRA_SAME:
        parent[find(b)] = find(a)
    groups: dict[str, list[str]] = {}
    for s in skills:
        groups.setdefault(find(s.id), []).append(s.id)
    expected = sorted(sorted(g) for g in groups.values() if len(g) > 1)
    found = sorted(sorted(c) for c in clusters)
    gold = {s.id: find(s.id) for s in skills}
    where = {m: i for i, c in enumerate(found) for m in c}
    ids = [s.id for s in skills]
    tp = fp = fn = 0
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            same, together = gold[a] == gold[b], a in where and where.get(a) == where.get(b)
            tp += same and together
            fp += (not same) and together
            fn += same and not together
    hard = [{"pair": pid, "a": a, "b": b, "clustered": a in where and where.get(a) == where.get(b)}
            for a, b, same, pid in labelled if not same]
    return {"tp": tp, "fp": fp, "fn": fn,
            "pair_precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "pair_recall": round(tp / (tp + fn), 3) if tp + fn else None,
            "hard_negatives_wrongly_clustered": sum(h["clustered"] for h in hard),
            "hard_negatives": hard,
            "expected_clusters": expected, "found_clusters": found,
            "exact_cluster_matches": sum(c in expected for c in found)}


async def main(args) -> dict:
    pairs = json.loads(Path(args.pairs).read_text())
    skills, labelled = seed(pairs)
    with tempfile.TemporaryDirectory() as tmp:
        store = SkillStore(Path(tmp) / "m.sqlite")
        for s in skills:
            store.create(s)
        judge = TimedJudge(args.url, timeout=120)
        gen = UnionGeneralizer()
        deps = SleepDeps(judge=judge, generalizer=gen, replayer=PassReplay(),
                         checker=judge if args.checks else None)
        t = time.perf_counter()
        out = await run_cycle(store, deps, SleepConfig(measure_heldout=False), trigger="dryrun")
        wall = time.perf_counter() - t
        n_general = sum(s.layer is Layer.GENERAL for s in store.active())
        store.close()
    d = out["detail"]
    ev = evaluate(d["clusters"], labelled, skills)
    return {
        "skills_seeded": len(skills), "status": out["status"], "wall_s": round(wall, 2),
        "pairs_considered": d.get("pairs_considered"), "pairs_judged": d.get("pairs_judged"),
        "basal_calls": judge.calls, "reverse_calls": d.get("reverse_calls"),
        "edges": d.get("edges"), "clusters": len(d["clusters"]),
        "generalizer_calls": gen.calls, "drafts": out.get("drafts"),
        "activated": out.get("activated"), "proposed": out.get("proposed"),
        "general_skills_after": n_general, "skill_count_after": out.get("skill_count"),
        "basal_latency_ms": {"mean": round(statistics.mean(judge.lat), 1),
                             "p50": round(statistics.median(judge.lat), 1),
                             "max": round(max(judge.lat), 1)} if judge.lat else None,
        "timings_s": d.get("timings"), "checks": d.get("checks"), "evaluation": ev,
        "judged": d.get("judged"), "generalize": d.get("generalize"),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", required=True)
    ap.add_argument("--url", default="http://127.0.0.1:8109")
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-checks", dest="checks", action="store_false")
    a = ap.parse_args()
    res = asyncio.run(main(a))
    Path(a.out).mkdir(parents=True, exist_ok=True)
    (Path(a.out) / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(json.dumps({k: v for k, v in res.items() if k not in ("judged", "generalize", "evaluation",
                                                               "checks")}, indent=1))
    ev = res["evaluation"]
    print("pair P/R", ev["pair_precision"], ev["pair_recall"], "exact", ev["exact_cluster_matches"],
          "/", len(ev["expected_clusters"]))
