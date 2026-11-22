"""Write RESULTS.md for the bench-runs topic from each step's results.json."""

import json
import sys
from pathlib import Path

D = Path(sys.argv[1])
M = ["recall", "precision", "type_acc", "required_acc", "validators_acc"]


def load(name):
    p = D / name / "results.json"
    return json.loads(p.read_text()) if p.exists() else None


def f(x):
    return "–" if x is None else f"{x:.3f}"


def row(label, s, extra=""):
    return f"| {label} | {s['ok']}/{s['forms']} | " + " | ".join(f(s.get(m)) for m in M) + f" |{extra}"


HEAD = "| run | ok | recall | precision | type | required | validators |"
SEP = "|---|---|---|---|---|---|---|"
L = ["# Benchmark results (unattended run)", ""]
steps = (D / "steps.txt").read_text() if (D / "steps.txt").exists() else "(no steps.txt)"
L += ["## Steps", "", "```", steps.strip(), "```", ""]

# 1. routing
rr, rh = load("routing-routed"), load("routing-hard")
L += ["## 1. Routing: basal-routed vs everything on hard (skills off)", ""]
if rr and rh:
    L += ["| run | ok | recall | precision | type | required | validators | list $/form | LLM s | tiers | heuristic routes |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, r in (("routed", rr), ("forced hard", rh)):
        for st in ("all", "gate", "held-out"):
            s = r["summary"].get(f"{st}/off")
            if not s:
                continue
            L.append(row(f"{name} / {st}", s,
                         f" {s['list_cost_per_form']:.5f} | {s['llm_s_total']} | {s['tiers']} | {s['heuristic_routes']} |"))
    a, b = rr["summary"]["all/off"], rh["summary"]["all/off"]
    if a["list_cost_per_form"]:
        L += ["", (f"**Forced hard costs {b['list_cost_per_form'] / a['list_cost_per_form']:.1f}× the routed "
              f"cost per form and {b['llm_s_total'] / max(a['llm_s_total'], 1e-9):.1f}× the LLM time; "
              f"validators {f(a['validators_acc'])} routed vs {f(b['validators_acc'])} hard.** "
              "Costs use EdenAI list prices × tokens (PRICES in tools/bench_morpheus.py).")]
    if a["heuristic_routes"] or b["heuristic_routes"]:
        L += ["", "⚠ some forms fell back to the heuristic router (basal unreachable or slow) — see results.json route_source."]
else:
    L += ["Missing: see Steps."]
L.append("")

# 2. skills
sk = load("skills")
L += ["## 2. Seed skills v2: OFF vs ON (heuristic router)", ""]
if sk:
    L += [HEAD, SEP]
    for st in ("all", "gate", "held-out"):
        for mode in ("off", "on"):
            if f"{st}/{mode}" in sk["summary"]:
                L.append(row(f"{st} / {mode}", sk["summary"][f"{st}/{mode}"]))
    L += ["", "Earlier baseline (seeds v1, after the ZIP fix): docs/notes/morpheus-hooks/bench-zipfix/README.md."]
else:
    L += ["Missing: see Steps."]
L.append("")

# 3. morpheus
sl = load("morpheus-sleep")
L += ["## 3. Morpheus: seeds → wake → sleep → approved", ""]
if sl:
    sb, sa, sap = sl.get("skills_before", {}), sl.get("skills_after", {}), sl.get("skills_after_approval")
    count = {"seeds": None, "wake": sb.get("total"), "sleep": sa.get("total"), "approved": sap}
    L += ["| run | ok | recall | precision | type | required | validators | skills |",
          "|---|---|---|---|---|---|---|---|"]
    for st in ("all", "gate", "held-out"):
        for stage in ("seeds", "wake", "sleep", "approved"):
            s = sl["summary"].get(f"{st}/{stage}")
            if s:
                L.append(row(f"{st} / {stage}", s, f" {count[stage] if count[stage] is not None else '–'} |"))
    L += ["", (f"Clusters found {sl.get('exact_cluster_matches')} exact; general skills: "
          f"{[g.get('id') if isinstance(g, dict) else g for g in sl.get('general_skills', [])]}; "
          f"review queue: {len(sl.get('review_queue') or [])}.")]
else:
    L += ["Missing: see Steps (wake output: morpheus-wake/README.md)."]

L += ["", "Per-run detail: */README.md next to this file. Log: run.log."]
(D / "RESULTS.md").write_text("\n".join(L) + "\n")
print("wrote", D / "RESULTS.md")
