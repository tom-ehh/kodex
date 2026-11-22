"""Dev tool: Morpheus sleep measurement run (P3), paid but capped.

1. Start store = seeds + the skills P2's wake run learned (docs/notes/morpheus-wake/bench/
   results.json "learned") + synthetic episodic skills (shapes only) so that ~5 PESEL-like
   skills exist, plus a few ZIP/NIP variants and one hard negative.
2. One real sleep cycle: local basal (pair judging + checks), the hard tier as writer, the
   medium tier replaying the 6 gate forms (held-out replayed only to measure).
3. Re-score all 12 forms (tools/bench_morpheus.py scoring): seeds only vs after wake (the
   start store) vs after sleep.

  uv run python tools/bench_morpheus_sleep.py --basal http://127.0.0.1:8109 --max-usd 1 \
      --cache .bench-cache --wake <main>/docs/notes/morpheus-wake/bench/results.json \
      --out <main>/docs/notes/morpheus-sleep/bench/cycle
"""

import argparse
import asyncio
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import bench_morpheus as bm
from bench_morpheus_wake import doc_of
from llm_score import truth_from_expected

from coi_formularze.config import Settings
from coi_formularze.morpheus import install_seeds, review
from coi_formularze.morpheus.match import ZIP_SCOPE
from coi_formularze.morpheus.models import (
    Layer,
    Skill,
    SkillStatus,
    SkillTrigger,
    ValidatorRule,
)
from coi_formularze.morpheus.sleep import SleepConfig, SleepDeps, run_cycle
from coi_formularze.morpheus.sleep.adapters import (
    BasalJudge,
    GateReplayer,
    LlmGeneralizer,
)
from coi_formularze.morpheus.store import SkillStore
from coi_formularze.morpheus.wake import Wake


def ep(id, labels, validator, family, snippet, before, after, rule, scope=None):
    return Skill(id=id, name=id.removeprefix("ep-"), layer=Layer.EPISODIC, description=snippet[:160],
                 trigger=SkillTrigger(labels_any=labels, validator=validator, field_family=family),
                 scope={"family": family} | (scope or {}),
                 pattern={"before": before, "after": after, "attr": "value"},
                 prompt_snippet=snippet, validator_rule=rule,
                 provenance={"synthetic": True, "source": "bench_morpheus_sleep"})


PESEL_RULE = ValidatorRule(add_validators=["PESEL"], post_fix="digits_only")
ZIP_RULE = ValidatorRule(add_validators=["ZIP_CODE"], post_fix="zip_code")
SYNTHETIC = [
    ep("ep-pesel-wnioskodawcy", ["PESEL wnioskodawcy"], "PESEL", "pesel",
       "Pole PESEL wnioskodawcy: rodzaj pesel, walidator PESEL. Zapisz 11 cyfr bez spacji.",
       "### ### ### ##", "###########", PESEL_RULE),
    ep("ep-pesel-pelnomocnika", ["PESEL pełnomocnika"], "PESEL", "pesel",
       "PESEL pełnomocnika: usuń myślniki, zostaw 11 cyfr. Walidator PESEL.",
       "######-#####", "###########", PESEL_RULE),
    ep("ep-pesel-wlasciciela", ["numer PESEL właściciela"], "PESEL", "pesel",
       "Numer PESEL właściciela zapisuj jako 11 cyfr bez separatorów; walidator PESEL.",
       "###-###-###-##", "###########", PESEL_RULE),
    ep("ep-pesel-malzonka", ["PESEL małżonka"], "PESEL", "pesel",
       "PESEL małżonka: same cyfry (11), bez spacji i kresek. Walidator PESEL.",
       "##### ######", "###########", PESEL_RULE),
    ep("ep-zip-korespondencja", ["kod pocztowy"], "ZIP_CODE", "zip_code",
       "Kod pocztowy w adresie do korespondencji: format ##-### z myślnikiem, walidator ZIP_CODE.",
       "#####", "##-###", ZIP_RULE, ZIP_SCOPE),
    ep("ep-nip-kreski", ["NIP"], "NIP", "nip_regon",
       "NIP zapisuj jako 10 cyfr bez kresek; walidator NIP.", "###-###-##-##", "##########",
       ValidatorRule(add_validators=["NIP"], post_fix="digits_only")),
    ep("ep-nip-zagraniczny", ["NIP podmiotu zagranicznego"], "NIP", "nip_regon",
       "NIP podmiotu zagranicznego: nie stosuj walidatora NIP (inny format).", "AA#########",
       "AA#########", ValidatorRule(add_validators=[])),
]
EXPECTED_CLUSTERS = [
    {"ep-pesel-digits-only", "ep-pesel-wnioskodawcy", "ep-pesel-pelnomocnika",
     "ep-pesel-wlasciciela", "ep-pesel-malzonka"},
    {"ep-zip-code-hyphen", "ep-zip-korespondencja"},
    {"ep-nip-remove-pl-prefix", "ep-nip-kreski"},
]


class TimedJudge(BasalJudge):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.lat: list[float] = []

    async def _ask(self, state, key):
        t = time.perf_counter()
        try:
            return await super()._ask(state, key)
        finally:
            self.lat.append(round((time.perf_counter() - t) * 1000, 1))


def build_store(path: Path, wake_results: Path) -> list[str]:
    path.unlink(missing_ok=True)
    s = SkillStore(path)
    install_seeds(s)
    learned = json.loads(wake_results.read_text())["learned"]
    for d in learned:
        if d["status"] == "active":
            sk = Skill.model_validate({k: v for k, v in d.items() if k not in ("hits", "wins", "losses")})
            s.create(sk.model_copy(update={"version": 1, "parent_version": None}))
    for sk in SYNTHETIC:
        s.create(sk)
    ids = [x.id for x in s.active()]
    s.close()
    return ids


def heldout_labels() -> dict:
    out = {}
    for slug in bm.HELDOUT:
        t = truth_from_expected(bm.EXAMPLES / slug / "expected.json")
        out[slug] = [{"title": x["title"], "fields": [{"label": f["label"], "validation": f["validation"]}
                                                     for f in x["fields"]]} for x in t["sections"]]
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--basal", default="http://127.0.0.1:8109")
    ap.add_argument("--max-usd", type=float, default=1.0)
    ap.add_argument("--cache", type=Path, default=Path(".bench-cache"))
    ap.add_argument("--wake", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--skip-rescore", action="store_true")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    bm.OUT = a.out
    settings = Settings(morpheus_enabled=True, router_url=a.basal, router_timeout_s=60,
                        morpheus_gate_md_dir=str(a.cache))
    chat = bm.CachedChat(settings.edenai_api_key.get_secret_value(), a.cache, a.max_usd,
                         timeout=settings.llm_timeout_s)
    writer_model = settings.morpheus_model or settings.tier_hard_model
    replay_model = settings.morpheus_replay_model or settings.tier_medium_model

    wake_db, sleep_db = a.out / "wake.sqlite", a.out / "sleep.sqlite"
    start_ids = build_store(wake_db, a.wake)
    shutil.copy(wake_db, sleep_db)

    store = SkillStore(sleep_db)
    judge = TimedJudge(settings.router_url, timeout=60)
    replayer = GateReplayer(chat, replay_model, str(a.cache), settings.skills_max_per_section,
                            labels=heldout_labels())
    deps = SleepDeps(judge=judge, generalizer=LlmGeneralizer(chat, writer_model),
                     replayer=replayer, checker=judge)
    before = {"total": len(store.active()),
              "by_layer": {ly.value: len([x for x in store.active() if x.layer is ly]) for ly in Layer}}
    t0 = time.monotonic()
    try:
        cfg = SleepConfig.from_settings(settings)
        cfg.measure_heldout = False  # 109 medium section drafts (~25 min); the re-score covers held-out
        out = await run_cycle(store, deps, cfg, trigger="bench")
    except bm.BudgetExceeded as exc:
        print("STOPPED in sleep:", exc)
        out = {"status": "budget", "detail": {}}
    sleep_s = round(time.monotonic() - t0, 1)
    sleep_spent = chat.spent
    after = {"total": len(store.active()),
             "by_layer": {ly.value: len([x for x in store.active() if x.layer is ly]) for ly in Layer}}
    general = [x.model_dump(mode="json") for x in store.list_skills(Layer.GENERAL)]
    retired = [{"id": x.id, "absorbed_into": x.absorbed_into, "status": x.status.value}
               for x in store.list_skills() if x.status is not SkillStatus.ACTIVE and x.layer is not Layer.SEED]
    queue = [i.model_dump() for i in review.items(store)]
    store.close()
    # what a human approving every proposal would get (Wake.approve retires the absorbed)
    approved_db = a.out / "approved.sqlite"
    shutil.copy(sleep_db, approved_db)
    st2 = SkillStore(approved_db)
    approved = [Wake(st2, settings).approve(i["id"]) for i in queue]
    approved_count = len(st2.active())
    st2.close()
    found = [set(c) for c in out.get("detail", {}).get("clusters", [])]
    print(json.dumps({k: v for k, v in out.items() if k != "detail"}, default=str), flush=True)

    results = []
    if not a.skip_rescore:
        seeds_db = a.out / "seeds.sqlite"
        seeds_db.unlink(missing_ok=True)
        s = SkillStore(seeds_db)
        install_seeds(s)
        s.close()
        try:
            for slug in bm.GATE + bm.HELDOUT:
                md, doc = doc_of(slug, settings, a.cache)
                for mode, path in (("seeds", seeds_db), ("wake", wake_db), ("sleep", sleep_db),
                                   ("approved", approved_db)):
                    r = await bm.run_one(slug, "on", md, doc, settings, chat, path)
                    r["mode"] = mode
                    print(f"{slug:55} {mode:6} {r.get('score', {}).get('validators_acc')} "
                          f"${chat.spent:.3f}", flush=True)
                    results.append(r)
        except bm.BudgetExceeded as exc:
            print("STOPPED in rescore:", exc)
    report = {
        "spent_usd": round(chat.spent, 4), "sleep_spent_usd": round(sleep_spent, 4),
        "sleep_wall_s": sleep_s, "models": {"writer": writer_model, "replay": replay_model,
                                            "basal": "Remek/basal-omni-pico (local CPU)"},
        "start_skills": start_ids, "skills_before": before, "skills_after": after,
        "expected_clusters": [sorted(c) for c in EXPECTED_CLUSTERS],
        "found_clusters": [sorted(c) for c in found],
        "exact_cluster_matches": sum(c in EXPECTED_CLUSTERS for c in found),
        "cycle": out, "general_skills": general, "approved": approved,
        "skills_after_approval": approved_count, "retired": retired, "review_queue": queue,
        "basal_latency_ms": judge.lat,
        "replay": {"calls": replayer.calls, "cache_hits": replayer.cache_hits,
                   "cost": round(replayer.cost, 5), "seconds": round(replayer.seconds, 1)},
        "summary": bm.summarise(results) if results else {}, "results": results,
    }
    (a.out / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    for p in (seeds_db if not a.skip_rescore else None, wake_db, sleep_db, approved_db):
        if p is not None:
            p.unlink(missing_ok=True)
    print(f"spent ${chat.spent:.4f}; wrote {a.out}")


if __name__ == "__main__":
    asyncio.run(main())
