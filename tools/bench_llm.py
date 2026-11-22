"""Dev tool: benchmark the LLM step (extracted markdown -> Form Definition JSON) on EdenAI.

Only the LLM step is measured. Inputs are the extracted markdown of forms with ground
truth (data/synthetic/xlsx/*.truth.json and docs/examples/*/expected.json). The target
JSON is filled in three parts (see tools/llm_parts.py): structure, fields per page,
choices; then merged by code and validated as a whole.

Two separate score axes:
  * schema adherence - valid JSON, schema-valid on first try, valid after retries with the
    errors fed back, retries used, violation kinds, merged form valid;
  * accuracy - field recall/precision by fuzzy label match, type / required / validators /
    section accuracy, option recall (tools/llm_score.py).
Plus latency, tokens and USD per form.

  uv run python tools/bench_llm.py --dry-run                       # cost estimate, no calls
  uv run python tools/bench_llm.py --tier cheap --limit 2 --max-usd 0.3
  uv run python tools/bench_llm.py --models deepseek-v4-flash,glm-5.3 --forms synth-xlsx-001,numer-porzadkowy

Models are named by short key (see MODELS) or an exact EdenAI id; the short key resolves
to the cheapest open-weights provider in the catalogue (falls back to the next one if a
provider errors). Responses are cached on disk by (model, prompt hash): reruns are free.
--max-usd is a hard cap on summed usage.estimated_cost of real (non-cached) calls.
Outputs: <out>/calls.csv (one row per call), <out>/forms.csv, <out>/results.json, and a
summary table per model on stdout.
"""

import argparse
import csv
import hashlib
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))

from llm_drafts import VARIANTS, run_variant
from llm_drafts import parse as parse_model
from llm_parts import (
    CHOICE_TYPES,
    PART_MODELS,
    build_prompt,
    merge,
    parse_part,
    retry_prompt,
    validate_merged,
)
from llm_score import (
    flatten_definition,
    score,
    truth_from_expected,
    truth_from_synthetic,
)

from kodex.config import Settings
from kodex.extract import extract
from kodex.validation.sniff import FileKind

BASE = "https://api.edenai.run/v3"
MAIN = Path(__file__).resolve().parents[1]
if ".claude" in MAIN.parts:  # worktree: docs/ and data/ live in the main tree
    MAIN = Path(*MAIN.parts[: MAIN.parts.index(".claude")])
SCRATCH = Path(
    "/tmp/claude-1000/-home-tommeh-Projects-hackathon-hacknation/"
    "d596f74e-dfc4-47d5-873f-79c42f43cc0b/scratchpad"
)

# short key -> (tier, regex on the lowercased last path component of the model name)
MODELS = {
    "qwen3.5-9b": ("cheap", r"qwen3\.5-9b"),
    "gpt-oss-20b": ("cheap", r"gpt-oss-20b"),
    "glm-4.7-flash": ("cheap", r"glm-4\.7-flash"),
    "qwen3.6-35b-a3b": ("mid", r"qwen3\.6-35b-a3b(-fp8)?"),
    "deepseek-v4-flash": ("mid", r"deepseek-v4-flash(-\d+)?"),
    "minimax-m3.1-flash": ("mid", r"minimax-m3\.1-flash"),
    "glm-5.3-flash": ("mid", r"glm-5\.3-flash"),
    "kimi-k3.1-pro": ("frontier", r"kimi-k3\.1-pro"),
    "deepseek-v4-pro": ("frontier", r"deepseek-v4-pro(-\d+)?"),
    "glm-5.3": ("frontier", r"glm-5\.3"),
    "qwen3.5-397b": ("frontier", r"qwen3\.5-397b-a17b(-fp8)?"),
}


class BudgetExceeded(Exception):
    pass


# --- catalogue -----------------------------------------------------------------------


def load_catalogue(key: str, cache_dir: Path) -> list[dict]:
    path = cache_dir / "models.json"
    if not path.exists() or time.time() - path.stat().st_mtime > 86400:
        r = httpx.get(
            f"{BASE}/models", headers={"Authorization": f"Bearer {key}"}, timeout=60
        )
        r.raise_for_status()
        path.write_text(r.text)
    return json.loads(path.read_text())["data"]


def _price(m: dict) -> tuple[float, float]:
    p = m.get("pricing") or {}
    return float(p.get("input_cost_per_token") or 0), float(
        p.get("output_cost_per_token") or 0
    )


def resolve(name: str, catalogue: list[dict]) -> list[dict]:
    """Candidates for a short key (cheapest first, open weights only) or an exact id."""
    exact = [m for m in catalogue if m["id"] == name]
    if exact:
        return exact
    if name not in MODELS:
        raise SystemExit(
            f"unknown model {name!r}; use a key of MODELS or an exact EdenAI id"
        )
    rx = re.compile(MODELS[name][1] + "$")
    cands = [
        m
        for m in catalogue
        if m.get("open_weights") is True
        and rx.match(m["model_name"].lower().split("/")[-1])
        and sum(_price(m)) > 0
    ]
    if not cands:
        raise SystemExit(f"no open-weights provider for {name}")
    # typical call: ~4k prompt tokens, ~1.5k completion tokens
    return sorted(cands, key=lambda m: _price(m)[0] * 4000 + _price(m)[1] * 1500)


def mode_for(m: dict) -> str:
    params = m.get("supported_parameters") or []
    if "response_format" not in params:
        return "prompt"
    return (
        "json_schema"
        if (m.get("capabilities") or {}).get("supports_response_schema")
        else "json_object"
    )


def effort_for(m: dict) -> str | None:
    efforts = (m.get("reasoning") or {}).get("supported_efforts") or []
    return next((e for e in ("none", "low") if e in efforts), None)


# --- client with cache and budget ----------------------------------------------------


class Client:
    def __init__(
        self, key: str, cache_dir: Path, max_usd: float, max_tokens: int = 8192
    ) -> None:
        self.http = httpx.Client(
            base_url=BASE, timeout=300, headers={"Authorization": f"Bearer {key}"}
        )
        self.cache_dir = cache_dir
        self.max_usd = max_usd
        self.max_tokens = max_tokens
        self.spent = 0.0
        self.lock = threading.Lock()
        self.stopped = False

    def _prepare(self, m: dict, messages: list[dict], schema: dict) -> tuple:
        mode = mode_for(m)
        body: dict = {
            "model": m["id"],
            "messages": messages,
            "temperature": 0,
            "max_tokens": self.max_tokens,
        }
        effort = effort_for(m)
        if effort:
            body["reasoning_effort"] = effort
        if mode == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "part", "strict": False, "schema": schema},
            }
        elif mode == "json_object":
            body["response_format"] = {"type": "json_object"}
        h = hashlib.sha256(
            json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        path = self.cache_dir / f"{re.sub(r'[^\w.-]', '_', m['id'])}-{h[:24]}.json"
        return (
            m,
            body,
            path,
            {"model_id": m["id"], "mode": mode, "effort": effort or ""},
        )

    def chat(
        self, cands: list[dict], messages: list[dict], schema: dict, est_usd: float
    ) -> dict:
        """One completion; tries providers in order. Returns a call record."""
        prepared = [self._prepare(m, messages, schema) for m in cands]
        for m, body, path, rec in prepared:  # a cached answer from any provider wins
            if path.exists():
                return (
                    rec | json.loads(path.read_text()) | {"cached": True, "cost": 0.0}
                )
        last_err = ""
        for m, body, path, rec in prepared:
            with self.lock:
                if self.stopped or self.spent + est_usd > self.max_usd:
                    self.stopped = True
                    raise BudgetExceeded(f"spent ${self.spent:.4f} of ${self.max_usd}")
            t = time.time()
            try:
                r = self.http.post("/llm/chat/completions", json=body)
            except httpx.HTTPError as exc:
                last_err = f"{m['id']}: {exc.__class__.__name__}"
                continue
            secs = time.time() - t
            if r.status_code != 200:
                last_err = f"{m['id']}: HTTP {r.status_code} {r.text[:200]}"
                continue
            d = r.json()
            u = d.get("usage") or {}
            pin, pout = _price(m)
            cost = u.get("estimated_cost") or u.get("cost") or d.get("cost")
            cost = (
                float(cost)
                if cost is not None
                else (
                    u.get("prompt_tokens", 0) * pin
                    + u.get("completion_tokens", 0) * pout
                )
            )
            try:
                text = d["choices"][0]["message"]["content"] or ""
                finish = d["choices"][0].get("finish_reason") or ""
            except KeyError, IndexError, TypeError:
                text, finish = "", "no_choices"
            out = {
                "text": text,
                "finish": finish,
                "secs": round(secs, 2),
                "prompt_tokens": u.get("prompt_tokens", 0),
                "completion_tokens": u.get("completion_tokens", 0),
                "orig_cost": cost,
            }
            with self.lock:
                self.spent += cost
            path.write_text(json.dumps(out, ensure_ascii=False))
            return rec | out | {"cached": False, "cost": cost}
        raise RuntimeError(last_err or "no provider")


# --- forms ---------------------------------------------------------------------------


def load_forms(data: Path, gold: Path) -> list[dict]:
    forms = []
    for row in csv.DictReader(
        (data / "synthetic/xlsx/manifest.csv").open(encoding="utf-8")
    ):
        forms.append(
            {
                "id": row["id"],
                "path": data / row["path"],
                "kind": FileKind.XLSX,
                "truth": truth_from_synthetic(data / row["truth"]),
            }
        )
    for ex in sorted(gold.glob("*/expected.json")):  # gold is data, read only here
        inputs = sorted(ex.parent.glob("input/*"))
        docx = [p for p in inputs if p.suffix.lower() == ".docx"]
        pdf = [p for p in inputs if p.suffix.lower() == ".pdf"]
        src, kind = (docx[0], FileKind.DOCX) if docx else (pdf[0], FileKind.PDF) if pdf else (None, None)
        if src:
            forms.append(
                {
                    "id": ex.parent.name,
                    "path": src,
                    "kind": kind,
                    "truth": truth_from_expected(ex),
                }
            )
    return forms


def markdown_of(form: dict, cache_dir: Path) -> str:
    p = cache_dir / f"md-{form['id']}.md"
    if not p.exists():
        p.write_text(extract(form["path"].read_bytes(), form["kind"]).markdown)
    return p.read_text()


# --- pipeline ------------------------------------------------------------------------


def run_part(
    client: Client,
    cands: list[dict],
    part: str,
    md: str,
    ctx: dict,
    retries: int,
    log: list[dict],
    tag: dict,
    est: float,
    model=None,
    text: str | None = None,
    check=None,
):
    schema = (model or PART_MODELS[part]).model_json_schema()
    content = text if text is not None else build_prompt(part, md, ctx, schema)
    messages = [{"role": "user", "content": content}]
    obj = None
    for attempt in range(retries + 1):
        call = client.chat(cands, messages, schema, est)
        if model is None:
            obj, errs = parse_part(part, call["text"], ctx)
        else:
            obj, errs = parse_model(model, call["text"], check)
        log.append(
            tag
            | {
                "part": part,
                "page": ctx.get("page", ""),
                "attempt": attempt,
                "model_id": call["model_id"],
                "mode": call["mode"],
                "effort": call["effort"],
                "cached": call["cached"],
                "secs": call["secs"],
                "prompt_tokens": call["prompt_tokens"],
                "completion_tokens": call["completion_tokens"],
                "cost": round(call["orig_cost"], 6),
                "finish": call["finish"],
                "json_ok": not any(k == "invalid_json" for k, _ in errs),
                "valid": obj is not None,
                "violations": ";".join(sorted({k for k, _ in errs})),
            }
        )
        if obj is not None:
            break
        messages += [
            {"role": "assistant", "content": call["text"][:20000]},
            {"role": "user", "content": retry_prompt(errs)},
        ]
    return obj


def _run_base(client, cands, md, retries, log, tag, est):
    """The original 3-part split: structure, fields per page, choices; code merge."""
    st = run_part(client, cands, "structure", md, {}, retries, log, tag, est)
    if st is None:
        return None, ["structure part failed"], 0, 1
    pages = []
    for i, page in enumerate(st.pages, start=1):
        ctx = {"page": i, "sections": [s.title for s in page.sections]}
        pages.append(run_part(client, cands, "fields", md, ctx, retries, log, tag, est))
    labels = [f.label for pf in pages if pf for f in pf.fields if f.type in CHOICE_TYPES]
    ch = None
    if labels:
        ctx = {"labels": labels}
        ch = run_part(client, cands, "choices", md, ctx, retries, log, tag, est)
    doc = merge(st, pages, ch)
    ok = 1 + sum(1 for x in pages if x) + (ch is not None or not labels)
    return doc, validate_merged(doc), ok, 1 + len(pages) + bool(labels)


def run_form(
    client: Client,
    key: str,
    cands: list[dict],
    form: dict,
    md: str,
    retries: int,
    est: float,
    variant: str = "base",
) -> tuple[dict, list[dict]]:
    log: list[dict] = []
    tag = {
        "model": key,
        "variant": variant,
        "tier": MODELS.get(key, ("custom",))[0],
        "form": form["id"],
    }
    res = tag | {"error": ""}
    try:
        if variant == "base":
            doc, issues, ok, total = _run_base(client, cands, md, retries, log, tag, est)
        else:

            def call(part, model, text, page, check):
                return run_part(
                    client, cands, part, md, {"page": page}, retries, log, tag, est,
                    model=model, text=text, check=check,
                )  # fmt: skip

            doc, issues, ok, total = run_variant(variant, call, md)
        pred = flatten_definition(doc) if doc else {"title": "", "sections": []}
        res |= {
            "merged_valid": doc is not None and not issues,
            "merge_issues": issues[:10],
            "parts_ok": ok,
            "parts_total": total,
        } | score(pred, form["truth"])
        res["prediction"] = doc
    except BudgetExceeded as exc:
        res["error"] = f"budget: {exc}"
    except Exception as exc:  # noqa: BLE001 - keep benchmarking other forms
        res["error"] = f"{exc.__class__.__name__}: {exc}"[:300]
    res |= {
        "secs": round(sum(c["secs"] for c in log), 1),
        "prompt_tokens": sum(c["prompt_tokens"] for c in log),
        "completion_tokens": sum(c["completion_tokens"] for c in log),
        "usd": round(sum(c["cost"] for c in log), 6),
        "calls": len(log),
    }
    return res, log


# --- dry run -------------------------------------------------------------------------


def estimate_form(md: str, truth: dict, m: dict) -> tuple[int, int]:
    """(prompt, completion) tokens for one form, incl. a retry allowance."""
    prompt_one = len(md) / 3.0 + 1800  # markdown + instructions + schema slice
    pages = len({s["page"] for s in truth["sections"]}) or 1
    nf = sum(len(s["fields"]) for s in truth["sections"])
    nopt = sum(len(f["options"]) for s in truth["sections"] for f in s["fields"])
    nch = sum(
        1 for s in truth["sections"] for f in s["fields"] if f["type"] in CHOICE_TYPES
    )
    calls = 1 + pages + (1 if nch else 0)
    out = 60 + 25 * len(truth["sections"]) + 45 * nf + 30 * nch + 12 * nopt
    if (m.get("capabilities") or {}).get("supports_reasoning"):
        out += 400 * calls  # reasoning overhead even at the lowest effort (rough)
    return int(prompt_one * calls * 1.25), int(out * 1.25)


def dry_run(keys: list[str], cand: dict, forms: list[dict], cache: Path) -> None:
    total = 0.0
    print(
        f"{'model':20} {'tier':9} {'provider id':50} {'mode':12} {'forms':>5} {'$est':>8}"
    )
    for k in keys:
        m = cand[k][0]
        pin, pout = _price(m)
        usd = 0.0
        for f in forms:
            pt, ct = estimate_form(markdown_of(f, cache), f["truth"], m)
            usd += pt * pin + ct * pout
        total += usd
        print(
            f"{k:20} {MODELS.get(k, ('custom',))[0]:9} {m['id']:50} {mode_for(m):12} {len(forms):5} {usd:8.3f}"
        )
    print(f"TOTAL estimated: ${total:.3f}")


# --- summary -------------------------------------------------------------------------


def _mean(xs: list) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def summarise(results: list[dict], calls: list[dict]) -> list[dict]:
    rows = []
    for key, var in dict.fromkeys((r["model"], r["variant"]) for r in results):
        rs = [r for r in results if (r["model"], r["variant"]) == (key, var) and not r["error"]]
        cs = [c for c in calls if (c["model"], c["variant"]) == (key, var)]
        firsts = [c for c in cs if c["attempt"] == 0]
        final = {}
        for c in cs:  # last attempt per (form, part, page)
            final[(c["form"], c["part"], c["page"])] = c
        kinds: dict[str, int] = {}
        for c in cs:
            for v in filter(None, c["violations"].split(";")):
                kinds[v] = kinds.get(v, 0) + 1
        rows.append(
            {
                "model": key,
                "variant": var,
                "tier": MODELS.get(key, ("custom",))[0],
                "forms": len(rs),
                "errors": sum(
                    1 for r in results if (r["model"], r["variant"]) == (key, var) and r["error"]
                ),
                # schema adherence
                "json_ok": _mean([c["json_ok"] for c in firsts]),
                "valid_1st": _mean([c["valid"] for c in firsts]),
                "valid_final": _mean([c["valid"] for c in final.values()]),
                "retries": sum(1 for c in cs if c["attempt"] > 0),
                "merged_valid": _mean([r["merged_valid"] for r in rs]),
                "violations": ",".join(
                    f"{k}:{v}"
                    for k, v in sorted(kinds.items(), key=lambda x: -x[1])[:4]
                ),
                # accuracy
                **{
                    m: _mean([r.get(m) for r in rs])
                    for m in (
                        "recall",
                        "precision",
                        "type_acc",
                        "required_acc",
                        "validators_acc",
                        "section_acc",
                        "option_recall",
                        "section_recall",
                    )
                },
                # cost
                "secs_form": _mean([r["secs"] for r in rs]),
                "tok_form": _mean(
                    [r["prompt_tokens"] + r["completion_tokens"] for r in rs]
                ),
                "usd_form": round(sum(r["usd"] for r in rs) / len(rs), 5)
                if rs
                else None,
                "modes": ",".join(sorted({c["mode"] for c in cs})),
            }
        )
    return rows


def print_table(rows: list[dict]) -> None:
    if not rows:
        return
    groups = [
        (
            "SCHEMA ADHERENCE",
            [
                "model",
                "variant",
                "tier",
                "forms",
                "errors",
                "json_ok",
                "valid_1st",
                "valid_final",
                "retries",
                "merged_valid",
                "modes",
                "violations",
            ],
        ),
        (
            "ACCURACY",
            [
                "model",
                "variant",
                "recall",
                "precision",
                "type_acc",
                "required_acc",
                "validators_acc",
                "section_acc",
                "option_recall",
                "section_recall",
            ],
        ),
        ("COST", ["model", "variant", "secs_form", "tok_form", "usd_form"]),
    ]
    for name, cols in groups:
        print(f"\n## {name}")
        w = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
        print(" | ".join(c.ljust(w[c]) for c in cols))
        print("-+-".join("-" * w[c] for c in cols))
        for r in rows:
            print(" | ".join(str(r[c]).ljust(w[c]) for c in cols))


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--models", default="", help="comma list of keys or exact EdenAI ids"
    )
    ap.add_argument("--tier", default="", help="cheap, mid, frontier (comma list)")
    ap.add_argument("--forms", default="", help="comma list of form ids")
    ap.add_argument("--limit", type=int, default=0, help="first N forms")
    ap.add_argument("--max-usd", type=float, default=3.0)
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--data", type=Path, default=MAIN / "data")
    ap.add_argument(
        "--gold", type=Path, default=MAIN / "docs" / "examples",
        help="dir of <form>/expected.json + <form>/input/* (default: docs/examples)",
    )  # fmt: skip
    ap.add_argument(
        "--variants", default="base", help=f"comma list of {','.join(VARIANTS)}"
    )
    ap.add_argument("--cache", type=Path, default=SCRATCH / "llm_cache")
    ap.add_argument("--out", type=Path, default=SCRATCH / "bench_llm")
    a = ap.parse_args()
    a.cache.mkdir(parents=True, exist_ok=True)
    a.out.mkdir(parents=True, exist_ok=True)
    key = Settings().edenai_api_key
    if key is None:
        raise SystemExit("EDENAI_API_KEY is not set")
    key = key.get_secret_value()

    keys = [k for k in a.models.split(",") if k] or list(MODELS)
    if a.tier:
        tiers = set(a.tier.split(","))
        keys = [k for k in keys if MODELS.get(k, ("custom",))[0] in tiers]
    catalogue = load_catalogue(key, a.cache)
    cand = {k: resolve(k, catalogue) for k in keys}

    forms = load_forms(a.data, a.gold)
    variants = [v for v in a.variants.split(",") if v]
    bad = set(variants) - set(VARIANTS)
    if bad:
        raise SystemExit(f"unknown variants {sorted(bad)}; use {VARIANTS}")
    if a.forms:
        want = a.forms.split(",")
        forms = [f for f in forms if f["id"] in want]
    if a.limit:
        forms = forms[: a.limit]

    if a.dry_run:
        dry_run(keys, cand, forms, a.cache)
        return

    client = Client(key, a.cache, a.max_usd)
    mds = {f["id"]: markdown_of(f, a.cache) for f in forms}
    results: list[dict] = []
    calls: list[dict] = []
    for k, var in [(k, v) for k in keys for v in variants]:
        if client.stopped:
            break
        m0 = cand[k][0]
        pin, pout = _price(m0)

        def one(f: dict, k=k, pin=pin, pout=pout, var=var):
            md = mds[f["id"]]
            est = (len(md) / 3.0 + 1800) * pin + 3000 * pout  # pessimistic per call
            return run_form(client, k, cand[k], f, md, a.retries, est, var)

        with ThreadPoolExecutor(a.workers) as pool:
            for res, log in pool.map(one, forms):
                results.append(res)
                calls.extend(log)
                print(
                    f"{k:20} {var:13} {res['form']:40} usd={res['usd']:.4f} recall={res.get('recall')} "
                    f"merged_valid={res.get('merged_valid')} {res['error']}",
                    file=sys.stderr,
                )

    if calls:
        with (a.out / "calls.csv").open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(calls[0]))
            w.writeheader()
            w.writerows(calls)
    flat = [
        {k: v for k, v in r.items() if k not in ("prediction", "merge_issues")}
        for r in results
    ]
    if flat:
        cols = list(dict.fromkeys(c for r in flat for c in r))
        with (a.out / "forms.csv").open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(flat)
    summary = summarise(results, calls)
    (a.out / "results.json").write_text(
        json.dumps(
            {
                "spent_usd": client.spent,
                "max_usd": a.max_usd,
                "summary": summary,
                "forms": results,
                "providers": {k: [m["id"] for m in cand[k]] for k in keys},
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    print_table(summary)
    print(
        f"\nspent ${client.spent:.4f} (cap ${a.max_usd}){' - STOPPED at cap' if client.stopped else ''}; "
        f"outputs in {a.out}"
    )


if __name__ == "__main__":
    main()
