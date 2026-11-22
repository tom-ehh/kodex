"""basal pair judging (design.md Decision 7 revised) and size-capped clustering.

Edge rule:
- only pairs that passed the retrieval floor are judged (basal alone never makes an edge);
- p(same) of the (A,B) order >= `strong` -> edge; < `low` -> no edge;
- borderline (`low` <= p < `strong`): judge (B,A) too and take the MINIMUM; edge iff >= `edge_min`.
- a code edge (no basal call): both skills apply the identical non-empty validator rule
  to the same validator and field family (design.md §6 "exact duplicates"); the
  retrieval floor still applies.
An edge only proposes a cluster: the generalizer, the checks and the replay gate still decide.
"""

from dataclasses import dataclass, field

from coi_formularze.morpheus.models import Skill
from coi_formularze.morpheus.sleep.adapters import SameJudge
from coi_formularze.morpheus.sleep.blocking import COS_FLOOR, Pair

BORDER_LOW = 0.45
BORDER_HIGH = 0.7
MAX_CLUSTER = 8


@dataclass
class Judged:
    pair: Pair
    p_ab: float
    p_ba: float | None
    p: float
    edge: bool
    source: str = "basal"  # basal | code


def rule_key(s: Skill) -> tuple | None:
    """Same validator + family + identical non-empty validator rule = the same code fix."""
    r = s.validator_rule
    if r is None or not (r.add_validators or r.post_fix or r.required is not None):
        return None
    return (s.trigger.validator, s.trigger.field_family, tuple(sorted(r.add_validators)),
            r.required, r.post_fix)


def edge_decision(p_ab: float, p_ba: float | None, cos: float, edge_min: float = 0.6,
                  floor: float = COS_FLOOR) -> tuple[float, bool]:
    """(p used, edge?) for one pair. Pure, so the rule is testable without basal."""
    p = p_ab if p_ba is None else min(p_ab, p_ba)
    return p, cos >= floor and p >= edge_min


def needs_reverse(p_ab: float) -> bool:
    return BORDER_LOW <= p_ab < BORDER_HIGH


async def judge_pairs(pairs: list[Pair], by_id: dict[str, Skill], judge: SameJudge,
                      edge_min: float = 0.6, code_edges: bool = True) -> tuple[list[Judged], int]:
    """Judge pairs in order; returns (results, basal calls)."""
    out, calls = [], 0
    for pr in pairs:
        a, b = by_id[pr.a], by_id[pr.b]
        if code_edges and (k := rule_key(a)) is not None and k == rule_key(b):
            out.append(Judged(pr, 1.0, None, 1.0, pr.cos >= COS_FLOOR, "code"))
            continue
        p_ab = await judge.p_same(a, b)
        calls += 1
        p_ba = None
        if needs_reverse(p_ab):
            p_ba = await judge.p_same(b, a)
            calls += 1
        p, edge = edge_decision(p_ab, p_ba, pr.cos, edge_min)
        out.append(Judged(pr, p_ab, p_ba, p, edge))
    return out, calls


@dataclass
class Cluster:
    members: list[str]
    edges: list[Judged] = field(default_factory=list)


def clusters_from_edges(judged: list[Judged], cap: int = MAX_CLUSTER) -> list[Cluster]:
    """Union-find over kept edges, strongest first; a union that would exceed `cap` is skipped
    (so a large component splits along its weakest links). Singletons are dropped."""
    parent: dict[str, str] = {}
    size: dict[str, int] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        size.setdefault(x, 1)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    kept = sorted((j for j in judged if j.edge), key=lambda j: (-j.p, -j.pair.cos))
    used: list[Judged] = []
    for j in kept:
        ra, rb = find(j.pair.a), find(j.pair.b)
        if ra == rb:
            used.append(j)
            continue
        if size[ra] + size[rb] > cap:
            continue
        parent[rb] = ra
        size[ra] += size[rb]
        used.append(j)
    groups: dict[str, list[str]] = {}
    for x in list(parent):
        groups.setdefault(find(x), []).append(x)
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        ms = set(members)
        out.append(Cluster(sorted(members), [j for j in used if j.pair.a in ms]))
    return sorted(out, key=lambda c: c.members)
