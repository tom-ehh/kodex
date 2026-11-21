"""Ground truth loading and accuracy scoring for the LLM benchmark (no network).

Both truth sources and the merged prediction are flattened to the same shape:
``{"title", "sections": [{"title", "page", "fields": [{"label", "type", "required",
"validation", "options", "dictionary"}]}]}``.
"""

import json
from difflib import SequenceMatcher
from pathlib import Path

from llm_parts import norm

MATCH = 0.75  # fuzzy label threshold


def truth_from_synthetic(path: Path) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    return {
        "title": d["title"],
        "sections": [
            {
                "title": s["title"],
                "page": s["page"],
                "fields": [
                    {
                        "label": f["label"],
                        "type": f["component"],
                        "required": f["required"],
                        "validation": sorted(f["validation"]),
                        "options": f.get("options") or [],
                        "dictionary": None,
                    }
                    for f in s["fields"]
                ],
            }
            for s in d["sections"]
        ],
    }


def flatten_definition(doc: dict) -> dict:
    """A Form Definition (expected.json or our merge) -> flat shape; FINISH tab skipped."""
    sections = []
    for pi, tab in enumerate(doc.get("tabs") or [], start=1):
        if tab.get("type") == "FINISH":
            continue
        for q in tab.get("questions") or []:
            sec = q["field"]
            fields = []
            for c in sec.get("repeatingSectionGrid") or []:
                f = c["field"]
                if f["type"] == "INFO_SECTION":
                    continue
                val = f.get("validation") or []
                fields.append(
                    {
                        "label": f.get("title") or f.get("techName", ""),
                        "type": f["type"],
                        "required": "REQUIRED" in val
                        or bool((f.get("consentSectionConfig") or {}).get("required")),
                        "validation": sorted(val),
                        "options": [o.get("name", "") for o in f.get("options") or []],
                        "dictionary": f.get("dictionaryName") or None,
                    }
                )
            sections.append(
                {"title": sec.get("title") or "", "page": pi, "fields": fields}
            )
    return {"title": doc.get("title", ""), "sections": sections}


def truth_from_expected(path: Path) -> dict:
    return flatten_definition(json.loads(path.read_text(encoding="utf-8-sig")))


def similarity(a: str, b: str) -> float:
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    r = SequenceMatcher(None, a, b).ratio()
    short, long_ = sorted((a, b), key=len)
    if len(short) >= 5 and short in long_:
        r = max(r, 0.85)
    return r


def match(
    pred: list[str], truth: list[str], threshold: float = MATCH
) -> list[tuple[int, int]]:
    """Greedy one-to-one fuzzy matching, best pairs first. Returns (pred_i, truth_j)."""
    pairs = sorted(
        (
            (similarity(p, t), i, j)
            for i, p in enumerate(pred)
            for j, t in enumerate(truth)
        ),
        reverse=True,
    )
    used_p, used_t, out = set(), set(), []
    for s, i, j in pairs:
        if s < threshold:
            break
        if i not in used_p and j not in used_t:
            used_p.add(i)
            used_t.add(j)
            out.append((i, j))
    return out


def _fields(flat: dict) -> list[dict]:
    return [dict(f, section=s["title"]) for s in flat["sections"] for f in s["fields"]]


def score(pred: dict, truth: dict) -> dict:
    """Accuracy metrics of a flat prediction against flat truth."""
    pf, tf = _fields(pred), _fields(truth)
    pairs = match([f["label"] for f in pf], [f["label"] for f in tf])
    n = len(pairs)

    def rate(ok: int, total: int) -> float | None:
        return round(ok / total, 3) if total else None

    type_ok = req_ok = val_ok = sec_ok = 0
    opt_hit = opt_total = 0
    for i, j in pairs:
        p, t = pf[i], tf[j]
        type_ok += p["type"] == t["type"]
        req_ok += bool(p["required"]) == bool(t["required"])
        val_ok += set(p["validation"]) == set(t["validation"])
        sec_ok += similarity(p["section"], t["section"]) >= MATCH
        if t["options"]:
            opt_total += len(t["options"])
            opt_hit += len(match(p["options"], t["options"]))
        elif t["dictionary"]:
            opt_total += 1
            opt_hit += p["dictionary"] == t["dictionary"]
    sec_pairs = match(
        [s["title"] for s in pred["sections"]], [s["title"] for s in truth["sections"]]
    )
    return {
        "truth_fields": len(tf),
        "pred_fields": len(pf),
        "matched": n,
        "recall": rate(n, len(tf)),
        "precision": rate(n, len(pf)),
        "type_acc": rate(type_ok, n),
        "required_acc": rate(req_ok, n),
        "validators_acc": rate(val_ok, n),
        "section_acc": rate(sec_ok, n),
        "option_recall": rate(opt_hit, opt_total),
        "section_recall": rate(len(sec_pairs), len(truth["sections"])),
        "title_sim": round(similarity(pred["title"], truth["title"]), 3),
    }
