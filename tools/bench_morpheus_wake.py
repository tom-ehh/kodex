"""Dev tool: Morpheus wake measurement run (P2).

1. Synthetic worker corrections (shapes only) on gate-form fields, built from the review
   findings (docs/examples/REVIEW-2026-11-22.md) and calibration/episodes.json: each
   pattern twice plus one-offs. The calibration reason is passed as the live `why`.
2. `Wake.tick()` with real basal (COI_ROUTER_URL, e.g. a local basal-serve) and the real
   writer/replay models through the bench's disk-cached chat (spend cap).
3. Re-score all 12 forms (tools/bench_morpheus.py scoring): seeds only vs seeds + the
   learned episodic skills.

  uv run python tools/bench_morpheus_wake.py --basal http://127.0.0.1:8108 --max-usd 1 \
      --cache .bench-cache --out <main>/docs/notes/morpheus-wake/bench
"""

import argparse
import asyncio
import json
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import bench_morpheus as bm

from coi_formularze.config import Settings
from coi_formularze.extract import extract, ocr_engine_from_settings
from coi_formularze.extract.model import Document, Page, Source
from coi_formularze.morpheus import install_seeds, review
from coi_formularze.morpheus.doccache import DocCache
from coi_formularze.morpheus.models import Episode, EpisodeKind, Layer
from coi_formularze.morpheus.store import SkillStore
from coi_formularze.morpheus.wake import Wake
from coi_formularze.validation.sniff import sniff

FORMS_DIR = bm.MAIN / "docs/notes/morpheus-hooks/bench/forms"  # seeds-on forms from P1 bench

# (pattern, calibration id, gate slug, techName, label, validator, attr, before, after)
EPISODES = [
    ("zip", "E01", "zwrot-oplaty-skarbowej", "kod_pocztowy", "Kod pocztowy", "ZIP_CODE", "value", "#####", "##-###"),
    ("pesel", "E04", "wydanie-zaswiadczenia", "pesel", "PESEL", "PESEL", "value", "######## ###", "#" * 11),
    ("nip", "E06", "zwrot-oplaty-skarbowej", "nip", "NIP", "NIP", "value", "###-###-##-##", "#" * 10),
    ("email", "E09", "zajecie-pasa-drogowego", "adres_e_mail", "Adres e-mail", "EMAIL", "value", "aaaa.aaa.aa", "aaaa@aaa.aa"),
    ("phone-optional", "E12", "zwrot-oplaty-skarbowej", "numer_telefonu", "Numer telefonu", None, "required", "true", "false"),
    ("zip", "E03", "wydanie-zaswiadczenia", "kod_pocztowy", "Kod pocztowy", "ZIP_CODE", "value", "#####", "##-###"),
    ("pesel", "E05", "oplata-za-odpady", "pesel", "PESEL", "PESEL", "value", "###-####-####", "#" * 11),
    ("nip", "E07", "oplata-za-odpady", "nip_regon", "NIP/REGON", "NIP", "value", "AA##########", "#" * 10),
    ("email", "E10", "oplata-za-odpady", "adres_e_mail", "Adres e-mail", "EMAIL", "value", "aaaa@aaa.aa ", "aaaa@aaa.aa"),
    ("phone-optional", "E15", "psy-ras-agresywnych", "telefon", "Telefon", None, "required", "true", "false"),
    ("one-off", "E32", "wynajecie-swietlicy-wiejskiej", "kod_pocztowy", "Kod pocztowy", "ZIP_CODE", "value", "A#-###", "##-###"),
    ("one-off", "E38", "zajecie-pasa-drogowego", "adres_e_mail_dane_wnioskodawcy", "Adres e-mail", "EMAIL", "value", "aaaa@aaa,aa", "aaaa@aaa.aa"),
    ("one-off", "E34", "wynajecie-swietlicy-wiejskiej", "imie_i_nazwisko_wnioskodawcy", "Imię i nazwisko wnioskodawcy", None, "value", "Aaaaaa", "Aaaaaaa"),
    ("one-off", "E52", "wynajecie-swietlicy-wiejskiej", "imie_i_nazwisko_wnioskodawcy", "Imię i nazwisko wnioskodawcy", None, "value", "Aaaaaa", "Aaaaaa"),
]


def reasons() -> dict[str, str]:
    eps = json.loads((bm.MAIN / "docs/notes/morpheus/calibration/episodes.json").read_text())
    return {e["id"]: e["reason"] for e in eps}


def doc_of(slug: str, settings, cache: Path):
    """The extracted Document; the scan reuses the cached OCR markdown (no new OCR call)."""
    md_file = cache / f"md-{slug}.md"
    if slug == "ho-wniosek-o-rejestracje-pojazdu" and md_file.exists():
        md = md_file.read_text()
        return md, Document(kind="jpg", pages=[Page(number=1, source=Source.OCR, markdown=md)])
    data = bm.pick_input(slug).read_bytes()
    doc = extract(data, sniff(data), ocr_engine_from_settings(settings))
    return doc.markdown, doc


def fmt(x):
    return bm.fmt(x)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--basal", default="http://127.0.0.1:8108")
    ap.add_argument("--max-usd", type=float, default=1.0)
    ap.add_argument("--cache", type=Path, default=Path(".bench-cache"))
    ap.add_argument("--out", type=Path, default=bm.MAIN / "docs/notes/morpheus-wake/bench")
    ap.add_argument("--skip-rescore", action="store_true")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    bm.OUT = a.out
    settings = Settings(router_enabled=False, morpheus_enabled=True, router_url=a.basal,
                        router_timeout_s=30, morpheus_gate_md_dir=str(a.cache),
                        morpheus_basal_retries=0)
    chat = bm.CachedChat(settings.edenai_api_key.get_secret_value(), a.cache, a.max_usd,
                         timeout=settings.llm_timeout_s)

    # ---- 1. episodes on a fresh seeds store, gate docs in the DocCache ----
    db = a.out / "wake.sqlite"
    db.unlink(missing_ok=True)
    store = SkillStore(db)
    install_seeds(store)
    cache = DocCache(86400)
    why_of = reasons()
    for slug in bm.GATE:
        md = (a.cache / f"md-{slug}.md").read_text()
        form = json.loads((FORMS_DIR / f"{slug}.on.json").read_text())
        cache.put(f"bench-{slug}", type("D", (), {"markdown": md})(), form)
    wake = Wake(store, settings, chat, cache)
    ids = []
    for pat, cid, slug, tech, label, val, attr, before, after in EPISODES:
        ep = store.add_episode(Episode(
            kind=EpisodeKind.WORKER_CORRECTION, form_id=f"bench-{slug}", field_path=tech,
            field_label=label, validator=val, edited_attr=attr, before_shape=before,
            after_shape=after))
        wake.remember_why([ep.id], why_of[cid])
        ids.append((ep.id, pat, cid, slug, label))

    # ---- 2. wake ----
    t0 = time.monotonic()
    try:
        outcomes = await wake.tick()
    except bm.BudgetExceeded as exc:
        print("STOPPED in wake:", exc)
        outcomes = []
    wake_s = round(time.monotonic() - t0, 1)
    wake_spent = chat.spent
    by_id = {o.episode_id: o for o in outcomes}
    rows = []
    for eid, pat, cid, slug, label in ids:
        o = by_id.get(eid)
        eflag = next(e for e in store.episodes() if e.id == eid).flag or {}
        rows.append({"episode": cid, "pattern": pat, "form": slug, "label": label,
                     "hist": o.hist if o else None, "stage": o.stage if o else None,
                     "decision": o.decision if o else None, "skill": o.skill_id if o else None,
                     "basal": eflag.get("basal"), "worth": eflag.get("worth"),
                     "timings": o.timings if o else {}, "cost": o.cost if o else 0,
                     "replay": (o.detail.get("replay") or {}).get("status") if o else None,
                     "replay_reason": (o.detail.get("replay") or {}).get("reason") if o else None,
                     "smoke": (o.detail.get("smoke") or {}).get("status") if o else None,
                     "lint": (o.detail.get("lint") or {}) if o else None})
    learned = [s.model_dump(mode="json") for s in store.list_skills(Layer.EPISODIC)]
    queue = [i.model_dump() for i in review.items(store)]
    store.close()

    # ---- 3. re-score: seeds only vs seeds + learned ----
    results = []
    if not a.skip_rescore:
        seeds_db = a.cache / "seeds.sqlite"
        seeds_db.unlink(missing_ok=True)
        s = SkillStore(seeds_db)
        install_seeds(s)
        s.close()
        learned_db = a.out / "learned.sqlite"
        shutil.copy(db, learned_db)
        try:
            for slug in bm.GATE + bm.HELDOUT:
                md, doc = doc_of(slug, settings, a.cache)
                for mode, path in (("seeds", seeds_db), ("learned", learned_db)):
                    r = await bm.run_one(slug, "on", md, doc, settings, chat, path)
                    r["mode"] = mode
                    print(f"{slug:55} {mode:8} {r.get('score', {}).get('validators_acc')} ${chat.spent:.3f}",
                          flush=True)
                    results.append(r)
        except bm.BudgetExceeded as exc:
            print("STOPPED in rescore:", exc)
        learned_db.unlink(missing_ok=True)
    summary = bm.summarise(results) if results else {}
    for p in (db, a.out / "wake.sqlite-journal"):
        p.unlink(missing_ok=True)

    funnel = Counter(r["stage"] for r in rows)
    lat = {}
    for k in ("basal_s", "write_s", "replay_s", "smoke_s"):
        xs = [r["timings"][k] for r in rows if k in r["timings"]]
        lat[k] = {"n": len(xs), "mean": round(sum(xs) / len(xs), 2) if xs else None,
                  "max": max(xs) if xs else None}
    report = {"spent_usd": round(chat.spent, 4), "wake_spent_usd": round(wake_spent, 4),
              "wake_wall_s": wake_s, "models": {"writer": wake.writer_model, "replay": wake.replay_model},
              "funnel": dict(funnel), "episodes": rows, "learned": learned, "review_queue": queue,
              "latency": lat, "summary": summary, "results": results}
    (a.out / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=dict))
    (a.out / "README.md").write_text(render(report))
    print(f"spent ${chat.spent:.4f}; wrote {a.out}")


def render(r: dict) -> str:
    L = ["# Morpheus wake measurement run", "",
         (f"Writer `{r['models']['writer']}`, replay `{r['models']['replay']}`, basal "
         "`Remek/basal-omni-pico` (local, CPU). Real spend: "
         f"${r['spent_usd']:.4f} (wake ${r['wake_spent_usd']:.4f}); the hard tier reports no cost, "
         f"so writer calls show $0. Wake wall time {r['wake_wall_s']} s."), "",
         "## Episodes", "",
         "| ep | pattern | form | label | hist | basal P(pattern) / consistent | stage | replay | smoke | skill |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for e in r["episodes"]:
        b = e["basal"] or {}
        bs = f"{b.get('p_pattern', '–')} / {b.get('consistent', '–')}" if b else "–"
        L.append(f"| {e['episode']} | {e['pattern']} | {e['form']} | {e['label']} | {e['hist']} | {bs} | "
                 f"{e['stage']} | {e['replay'] or '–'} | {e['smoke'] or '–'} | {e['skill'] or '–'} |")
    L += ["", "## Funnel", "", "| stage | n |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in sorted(r["funnel"].items(), key=lambda kv: -kv[1])]
    L += ["", "## Latency per stage (s)", "", "| stage | n | mean | max |", "|---|---|---|---|"]
    L += [f"| {k} | {v['n']} | {fmt(v['mean'])} | {fmt(v['max'])} |" for k, v in r["latency"].items()]
    L += ["", "## Learned skills", ""]
    for s in r["learned"]:
        rule = s.get("validator_rule") or {}
        L.append(f"- **{s['id']}** v{s['version']} ({s['status']}): {s['description']} — trigger "
                 f"{s['trigger']['labels_any']} / {s['trigger']['validator']}; rule {rule}; "
                 f"snippet: {s['prompt_snippet']}")
    if r["review_queue"]:
        L += ["", "## Review queue", ""]
        L += [f"- {i['kind']} {i['ref_id']}" for i in r["review_queue"]]
    if r["summary"]:
        L += ["", "## Re-score: seeds only vs seeds + learned", "",
              "| set | mode | ok | recall | precision | type | required | validators | section rec | esc |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for key in sorted(r["summary"]):
            s = r["summary"][key]
            st, mode = key.split("/")
            L.append(f"| {st} | {mode} | {s['ok']}/{s['forms']} | "
                     + " | ".join(fmt(s[m]) for m in bm.METRICS) + f" | {s['escalations']} |")
        L += ["", "### Per validator (tp/fp/fn)", "", "| set/mode | " + " | ".join(bm.VALIDATORS) + " |",
              "|---|" + "---|" * len(bm.VALIDATORS)]
        for key in sorted(r["summary"]):
            v = r["summary"][key]["validators"]
            L.append(f"| {key} | " + " | ".join(f"{v[x]['tp']}/{v[x]['fp']}/{v[x]['fn']}" for x in bm.VALIDATORS) + " |")
        L += ["", "### Skills fired", ""]
        for key in sorted(r["summary"]):
            L.append(f"- **{key}**: " + ", ".join(f"{k} {n}" for k, n in r["summary"][key]["skills_fired"].items()))
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    asyncio.run(main())
