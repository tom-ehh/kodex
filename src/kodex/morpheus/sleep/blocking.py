"""Retrieval floor (CPU, no model): char-trigram TF-IDF cosine, blocking, top-k, pair budget.

calibration.md: trigram cosine AUROC 0.85 on the 30 sleep pairs; cos >= 0.3 recalls 0.93.
The pair count is bounded by `n*k` (top-k per node) and then by the hard budget.
"""

import math
from collections import Counter
from dataclasses import dataclass

from kodex.morpheus.match import fold
from kodex.morpheus.models import Skill

COS_FLOOR = 0.3
TOP_K = 5


def skill_doc(s: Skill) -> str:
    parts = [*s.trigger.labels_any, s.trigger.field_family or "", s.trigger.validator or "",
             s.pattern.get("before", ""), s.pattern.get("after", ""), s.prompt_snippet,
             s.description]
    return fold(" ".join(p for p in parts if p))


def trigrams(text: str) -> Counter:
    t = f"  {text} "
    return Counter(t[i:i + 3] for i in range(len(t) - 2))


def tfidf_vectors(docs: list[str]) -> list[dict[str, float]]:
    grams = [trigrams(d) for d in docs]
    df = Counter(g for c in grams for g in c)
    n = len(docs)
    out = []
    for c in grams:
        v = {g: tf * (math.log((1 + n) / (1 + df[g])) + 1) for g, tf in c.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        out.append({g: x / norm for g, x in v.items()})
    return out


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(g, 0.0) for g, x in a.items())


def block_key(s: Skill) -> str:
    """Cheap blocking: same validator, else same field family, else one shared bucket."""
    return (s.trigger.validator or s.trigger.field_family or "*").upper()


@dataclass(frozen=True)
class Pair:
    a: str
    b: str
    cos: float


def candidate_pairs(skills: list[Skill], budget: int, k: int = TOP_K,
                    floor: float = COS_FLOOR) -> tuple[list[Pair], int]:
    """Pairs above the floor, each node's top-k within its block (plus the `*` bucket),
    ordered by cosine descending and cut at `budget`. Returns (pairs, n_before_budget)."""
    vecs = dict(zip([s.id for s in skills], tfidf_vectors([skill_doc(s) for s in skills]),
                    strict=True))
    keys = {s.id: block_key(s) for s in skills}
    chosen: dict[tuple[str, str], float] = {}
    for s in skills:
        scored = []
        for o in skills:
            if o.id == s.id:
                continue
            if keys[s.id] != keys[o.id] and "*" not in (keys[s.id], keys[o.id]):
                continue
            c = cosine(vecs[s.id], vecs[o.id])
            if c >= floor:
                scored.append((c, o.id))
        scored.sort(reverse=True)
        for c, oid in scored[:k]:
            chosen[tuple(sorted((s.id, oid)))] = c
    pairs = sorted((Pair(a, b, c) for (a, b), c in chosen.items()),
                   key=lambda p: (-p.cos, p.a, p.b))
    return pairs[:budget], len(pairs)
