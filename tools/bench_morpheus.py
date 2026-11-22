"""Dev tool: the real analyze pipeline over the 12 labelled forms, Morpheus seed skills OFF vs ON.

Runs extract -> route -> parse (parse/pipeline.py, the API's code path) per form, once
without hooks and once with `Hooks` over a fresh store holding only the seed skills,
and scores both against each form's expected.json (tools/llm_score.py: fuzzy label
match, type / required / validators accuracy) plus per-validator precision/recall.

  uv run python tools/bench_morpheus.py --max-usd 2 --out <main>/docs/notes/morpheus-hooks/bench
  uv run python tools/bench_morpheus.py --forms numer-porzadkowy --modes off

LLM calls are cached on disk by sha256(model + messages + schema); a re-run is free.
OFF and ON share the cache, so their facts/outline passes are identical and only
sections whose prompt got hints differ (a paired comparison). Extracted markdown is
cached too. --max-usd caps the summed estimated_cost of real (non-cached) LLM calls;
the run stops with a report when the next call could cross it. OCR (the one scan) is
not counted. Settings (key, tier models) come from .env.
"""

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from llm_score import flatten_definition, match, score, truth_from_expected

from coi_formularze.config import Settings
from coi_formularze.extract import extract, ocr_engine_from_settings
from coi_formularze.morpheus import install_seeds
from coi_formularze.morpheus.hooks import Hooks
from coi_formularze.morpheus.store import SkillStore
from coi_formularze.parse.llm import Completion, EdenAiChat
from coi_formularze.parse.pipeline import ParseFailed, parse
from coi_formularze.route import route_document
from coi_formularze.validation.sniff import sniff

MAIN = Path(__file__).resolve().parents[1]
while not (MAIN / "docs" / "examples").is_dir() and MAIN.parent != MAIN:
    MAIN = MAIN.parent
EXAMPLES = MAIN / "docs" / "examples"

GATE = ["zwrot-oplaty-skarbowej", "wydanie-zaswiadczenia", "wynajecie-swietlicy-wiejskiej",
        "zajecie-pasa-drogowego", "psy-ras-agresywnych", "oplata-za-odpady"]
HELDOUT = ["numer-porzadkowy", "nadplata-podatku", "ho-wniosek-o-udostepnienie-informacji-publicznej",
           "ho-wniosek-o-dzierzawe-najem-uzyczenie-nieruchomosci", "ho-wniosek-o-rejestracje-pojazdu",
           "ho-wniosek-o-pozwolenie-na-uzytkowanie-pb-17"]
VALIDATORS = ["REQUIRED", "PESEL", "NIP", "REGON", "ZIP_CODE", "EMAIL"]


class BudgetExceeded(Exception):
    pass


def pick_input(slug: str) -> Path:
    files = sorted((EXAMPLES / slug / "input").iterdir())
    if slug == "ho-wniosek-o-rejestracje-pojazdu":  # the scan
        return next(f for f in files if f.suffix.lower() == ".jpg")
    for ext in (".pdf", ".docx"):
        for f in files:
            if f.suffix.lower() == ext:
                return f
    raise FileNotFoundError(slug)


class CachedChat(EdenAiChat):
    """EdenAiChat with a disk cache, token/cost accounting and a spend cap."""

    def __init__(self, key: str, cache: Path, max_usd: float, **kw):
        super().__init__(key, **kw)
        self.cache, self.max_usd = cache, max_usd
        self.spent = 0.0
        self.reserve = 0.0  # in-flight estimate
        self.log: list[dict] = []
        self._last_usage: dict = {}

    async def _post(self, body):
        resp = await super()._post(body)
        if resp.status_code == 200:
            self._last_usage = resp.json().get("usage") or {}
        return resp

    async def complete(self, model, messages, schema):
        h = hashlib.sha256(json.dumps([model, messages, schema], ensure_ascii=False,
                                      sort_keys=True).encode()).hexdigest()
        path = self.cache / f"{h[:32]}.json"
        if path.exists():
            rec = json.loads(path.read_text())
            self.log.append(rec | {"cached": True})
            return Completion(rec["text"], rec["finish"], 0.0, model)
        est = 0.02  # generous per-call upper bound for the cap check
        if self.spent + self.reserve + est > self.max_usd:
            raise BudgetExceeded(f"spent ${self.spent:.3f}; next call could pass ${self.max_usd}")
        self.reserve += est
        t0 = time.monotonic()
        try:
            c = await super().complete(model, messages, schema)
        finally:
            self.reserve -= est
        u = self._last_usage
        rec = {"model": model, "text": c.text, "finish": c.finish, "cost": c.cost,
               "latency_s": round(time.monotonic() - t0, 2),
               "prompt_tokens": u.get("prompt_tokens", 0),
               "completion_tokens": u.get("completion_tokens", 0)}
        self.spent += c.cost
        path.write_text(json.dumps(rec, ensure_ascii=False))
        self.log.append(rec | {"cached": False})
        return c


def markdown_of(slug: str, settings: Settings, cache: Path) -> tuple[str, object]:
    src = pick_input(slug)
    data = src.read_bytes()
    doc = extract(data, sniff(data), ocr_engine_from_settings(settings))
    p = cache / f"md-{slug}.md"
    p.write_text(doc.markdown)
    return doc.markdown, doc


def validator_counts(pred: dict, truth: dict) -> dict:
    """Per validator over label-matched fields: tp / fp / fn (presence of V on the field)."""
    pf = [f for s in pred["sections"] for f in s["fields"]]
    tf = [f for s in truth["sections"] for f in s["fields"]]
    out = {v: Counter() for v in VALIDATORS}
    for i, j in match([f["label"] for f in pf], [f["label"] for f in tf]):
        p, t = set(pf[i]["validation"]), set(tf[j]["validation"])
        for v in VALIDATORS:
            out[v]["tp"] += v in p and v in t
            out[v]["fp"] += v in p and v not in t
            out[v]["fn"] += v not in p and v in t
    return {v: dict(c) for v, c in out.items()}


async def run_one(slug, mode, md, doc, settings, chat, seeds_db) -> dict:
    hooks = None
    if mode == "on":
        store = SkillStore(seeds_db)
        hooks = Hooks(store, f"bench-{slug}", settings.skills_max_per_section)
    route = await route_document(doc, settings)
    start = len(chat.log)
    t0 = time.monotonic()
    rec: dict = {"form": slug, "set": "gate" if slug in GATE else "held-out", "mode": mode,
                 "tier_start": route.tier.value}
    try:
        res = await parse(md, route, settings, chat, hooks=hooks)
        form = res.result.form
        rec |= {"ok": True, "tier_final": res.route.tier.value, "escalations": len(res.attempts) - 1}
    except ParseFailed as exc:
        form = None
        rec |= {"ok": False, "tier_final": exc.attempts[-1].tier, "escalations": len(exc.attempts) - 1,
                "error": str(exc)[:300]}
    calls = chat.log[start:]
    rec |= {
        "wall_s": round(time.monotonic() - t0, 1),
        "llm_latency_s": round(sum(c.get("latency_s", 0) for c in calls), 1),
        "calls": len(calls), "cached_calls": sum(c["cached"] for c in calls),
        "prompt_tokens": sum(c.get("prompt_tokens", 0) for c in calls),
        "completion_tokens": sum(c.get("completion_tokens", 0) for c in calls),
        "cost_usd": round(sum(c.get("cost", 0) for c in calls), 5),  # first-run cost, cached or not
    }
    truth = truth_from_expected(EXAMPLES / slug / "expected.json")
    if form is not None:
        pred = flatten_definition(form)
        rec["score"] = score(pred, truth)
        rec["validators"] = validator_counts(pred, truth)
        (OUT / "forms").mkdir(parents=True, exist_ok=True)
        (OUT / "forms" / f"{slug}.{mode}.json").write_text(json.dumps(form, ensure_ascii=False, indent=1))
    if hooks:
        rec["skills_fired"] = dict(sorted(hooks.used.items()))
        rec["episodes"] = Counter(e.kind.value for e in hooks.episodes)
        hooks.store.close()
    return rec


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


METRICS = ["recall", "precision", "type_acc", "required_acc", "validators_acc", "section_recall"]


def summarise(results: list[dict]) -> dict:
    groups: dict = {}
    for r in results:
        for key in ((r["set"], r["mode"]), ("all", r["mode"])):
            groups.setdefault(key, []).append(r)
    out = {}
    for (st, mode), rs in groups.items():
        scored = [r for r in rs if "score" in r]
        v = {name: Counter() for name in VALIDATORS}
        for r in scored:
            for name, c in r["validators"].items():
                v[name].update(c)
        prf = {}
        for name, c in v.items():
            tp, fp, fn = c["tp"], c["fp"], c["fn"]
            prf[name] = {"tp": tp, "fp": fp, "fn": fn,
                         "precision": round(tp / (tp + fp), 3) if tp + fp else None,
                         "recall": round(tp / (tp + fn), 3) if tp + fn else None}
        fired = Counter()
        for r in rs:
            fired.update(r.get("skills_fired", {}))
        out[f"{st}/{mode}"] = {
            "forms": len(rs), "ok": sum(r["ok"] for r in rs),
            **{m: _mean([r["score"][m] for r in scored]) for m in METRICS},
            "validators": prf, "cost_usd": round(sum(r["cost_usd"] for r in rs), 4),
            "tokens": sum(r["prompt_tokens"] + r["completion_tokens"] for r in rs),
            "llm_latency_s": _mean([r["llm_latency_s"] for r in rs]),
            "escalations": sum(r["escalations"] for r in rs),
            "skills_fired": dict(fired.most_common()),
        }
    return out


def fmt(x):
    return "–" if x is None else (f"{x:.3f}" if isinstance(x, float) else str(x))


def markdown(results: list[dict], summary: dict, spent: float) -> str:
    L = ["# Morpheus seed skills: OFF vs ON", "",
         (f"Real LLM spend this run: ${spent:.4f} (cost columns: first-run cost of the calls, "
         "cached or not). Scoring: tools/llm_score.py vs expected.json."), "",
         "## Per set", "",
         "| set | mode | ok | recall | precision | type | required | validators | section rec | tokens | cost $ | esc |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for key in sorted(summary):
        s = summary[key]
        st, mode = key.split("/")
        L.append(f"| {st} | {mode} | {s['ok']}/{s['forms']} | " + " | ".join(fmt(s[m]) for m in METRICS)
                 + f" | {s['tokens']} | {s['cost_usd']:.4f} | {s['escalations']} |")
    L += ["", "## Per validator (label-matched fields; tp/fp/fn, P/R)", "",
          "| set/mode | " + " | ".join(VALIDATORS) + " |", "|---|" + "---|" * len(VALIDATORS)]
    for key in sorted(summary):
        cells = []
        for v in VALIDATORS:
            c = summary[key]["validators"][v]
            cells.append(f"{c['tp']}/{c['fp']}/{c['fn']} P{fmt(c['precision'])} R{fmt(c['recall'])}")
        L.append(f"| {key} | " + " | ".join(cells) + " |")
    L += ["", "## Per form", "",
          "| form | set | mode | ok | tier | recall | precision | type | required | validators | calls (cached) | llm s | cost $ | skills fired |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(results, key=lambda r: (r["set"], r["form"], r["mode"])):
        sc = r.get("score", {})
        tier = r["tier_start"] + ("→" + r["tier_final"] if r["tier_final"] != r["tier_start"] else "")
        fired = ", ".join(f"{k.removeprefix('seed-')}×{n}" for k, n in r.get("skills_fired", {}).items())
        L.append(f"| {r['form']} | {r['set']} | {r['mode']} | {'✓' if r['ok'] else '✗'} | {tier} | "
                 + " | ".join(fmt(sc.get(m)) for m in ["recall", "precision", "type_acc", "required_acc", "validators_acc"])
                 + f" | {r['calls']} ({r['cached_calls']}) | {r['llm_latency_s']} | {r['cost_usd']:.4f} | {fired} |")
    L += ["", "## Skills fired (ON, uses = prompt injections + rule applications)", ""]
    for key in ("gate/on", "held-out/on", "all/on"):
        if key in summary:
            L.append(f"- **{key}**: " + ", ".join(f"{k} {n}" for k, n in summary[key]["skills_fired"].items()))
    return "\n".join(L) + "\n"


async def main() -> None:
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--forms", default=",".join(GATE + HELDOUT))
    ap.add_argument("--modes", default="off,on")
    ap.add_argument("--max-usd", type=float, default=2.0)
    ap.add_argument("--cache", type=Path, default=Path(tempfile.gettempdir()) / "coi-bench-morpheus")
    ap.add_argument("--out", type=Path, default=MAIN / "docs/notes/morpheus-hooks/bench")
    a = ap.parse_args()
    OUT = a.out
    a.cache.mkdir(parents=True, exist_ok=True)
    a.out.mkdir(parents=True, exist_ok=True)
    settings = Settings(router_enabled=False) if "router_enabled" in Settings.model_fields else Settings()
    if settings.edenai_api_key is None:
        sys.exit("COI_EDENAI_API_KEY missing (.env)")
    chat = CachedChat(settings.edenai_api_key.get_secret_value(), a.cache, a.max_usd,
                      timeout=settings.llm_timeout_s)
    seeds_db = a.cache / "seeds.sqlite"
    seeds_db.unlink(missing_ok=True)
    s = SkillStore(seeds_db)
    install_seeds(s)
    s.close()
    results = []
    try:
        for slug in a.forms.split(","):
            md, doc = markdown_of(slug, settings, a.cache)
            for mode in a.modes.split(","):
                r = await run_one(slug, mode, md, doc, settings, chat, seeds_db)
                print(f"{slug:55} {mode:3} ok={r['ok']} {r.get('score', {}).get('validators_acc')} "
                      f"${chat.spent:.3f}", flush=True)
                results.append(r)
    except BudgetExceeded as exc:
        print(f"STOPPED: {exc}", flush=True)
    summary = summarise(results)
    (a.out / "results.json").write_text(json.dumps(
        {"spent_usd": round(chat.spent, 4), "models": {t: getattr(settings, f"tier_{t}_model")
                                                       for t in ("simple", "medium", "hard")},
         "summary": summary, "results": results}, ensure_ascii=False, indent=1, default=dict))
    (a.out / "README.md").write_text(markdown(results, summary, chat.spent))
    print(f"spent ${chat.spent:.4f}; wrote {a.out}")


OUT = Path(".")

if __name__ == "__main__":
    asyncio.run(main())
