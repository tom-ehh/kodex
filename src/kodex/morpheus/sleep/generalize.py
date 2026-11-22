"""Generalize a cluster into one general skill, with guardrails in code (sleep.md §3).

The model may answer merge, split (sub-groups) or reject. Code then checks: absorbs are
cluster members, every snippet sentence has evidence from >= 2 distinct cluster members,
the trigger is no wider than the union of the members' triggers, schema/length/PII. One
retry with the errors, then give up (the cluster stays episodic).
"""

import hashlib
import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from kodex.morpheus.match import fold
from kodex.morpheus.models import (
    Layer,
    Skill,
    SkillStatus,
    SkillTrigger,
    ValidatorRule,
)
from kodex.morpheus.sanitize import pii_findings
from kodex.morpheus.sleep.adapters import AdapterError, Draft, Generalizer

_SENT = re.compile(r"(?<=[.!?])\s+")


@dataclass
class GenResult:
    """One outcome per (sub)cluster."""

    members: list[str]
    skill: Skill | None = None  # merge that passed the guardrails
    absorbs: list[str] = field(default_factory=list)
    status: str = "rejected"  # merged | rejected | invalid | error
    errors: list[str] = field(default_factory=list)
    risk: str = ""
    cost: float = 0.0
    calls: int = 0


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(text.strip()) if s.strip()]


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9#]+", fold(text)))


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z]+", "-", fold(text)).strip("-")
    return "-".join(s.split("-")[:4]) or "rule"


def _letters_hash(text: str, n: int = 6) -> str:
    """Hash without digits, so generated ids never trip the PII digit-run lint."""
    return "".join(chr(ord("a") + b % 26) for b in hashlib.sha1(text.encode()).digest()[:n])


def check_draft(draft: Draft, cluster: list[Skill]) -> tuple[Skill | None, list[str], list[str]]:
    """Validate a merge draft. Returns (skill, absorbs, errors)."""
    errs: list[str] = []
    ids = {s.id for s in cluster}
    if not draft.skill:
        return None, [], ["merge without a skill"]
    not_cov = set(draft.not_covered) & ids
    absorbs = [a for a in dict.fromkeys(draft.absorbs or list(ids)) if a in ids and a not in not_cov]
    if unknown := set(draft.absorbs) - ids:
        errs.append(f"absorbs unknown ids {sorted(unknown)}")
    if len(absorbs) < 2:
        errs.append("a general skill must absorb at least 2 cluster members")
    d = dict(draft.skill)
    trig = d.get("trigger") or {}
    try:
        trigger = SkillTrigger.model_validate(trig)
    except ValidationError as exc:
        return None, absorbs, errs + [f"trigger: {exc.errors()[0]['msg']}"]
    union_labels = {fold(x) for s in cluster for x in s.trigger.labels_any}
    if wider := [x for x in trigger.labels_any if fold(x) not in union_labels]:
        errs.append(f"trigger wider than the union: new keywords {wider}")
    if trigger.validator and trigger.validator not in {s.trigger.validator for s in cluster}:
        errs.append(f"trigger validator {trigger.validator} not in the cluster")
    if trigger.field_family and trigger.field_family not in {s.trigger.field_family for s in cluster}:
        errs.append(f"field_family {trigger.field_family} not in the cluster")
    if trigger.regex and trigger.regex not in {s.trigger.regex for s in cluster}:
        errs.append("trigger regex not taken from a member (could be wider)")
    if not (trigger.labels_any or trigger.validator or trigger.regex or trigger.field_family):
        errs.append("empty trigger")
    snippet = (d.get("prompt_snippet") or d.get("snippet") or "").strip()
    if not snippet:
        errs.append("empty snippet")
    evs = [(_words(str(ev.get("sentence", ""))), set(ev.get("from", [])) & ids)
           for ev in draft.evidence if isinstance(ev, dict)]
    for sent in sentences(snippet):
        support = set()
        ws = _words(sent)
        for ew, src in evs:  # the same sentence, give or take punctuation and a few words
            if ew and ws and len(ew & ws) / len(ew | ws) >= 0.6:
                support |= src
        if len(support) < 2:
            errs.append(f"sentence lacks >= 2 sources: {sent[:60]!r}")
    if pii := pii_findings({k: v for k, v in d.items() if k != "id"}):
        errs.append(f"PII-like content: {pii}")
    if errs:
        return None, absorbs, errs
    name = _slug(d.get("name") or trigger.field_family or snippet)[:50]
    name = name.removeprefix("gen-") if name != "gen" else "rule"
    sid = f"gen-{name}-{_letters_hash(','.join(sorted(absorbs)) + snippet)}"[:64]
    vr = d.get("validator_rule")
    try:
        skill = Skill(
            id=sid, name=name, layer=Layer.GENERAL, status=SkillStatus.ACTIVE,
            description=(d.get("description") or snippet)[:160], trigger=trigger,
            pattern={k: str(v) for k, v in (d.get("pattern") or {}).items() if v}, prompt_snippet=snippet,
            validator_rule=ValidatorRule.model_validate(vr) if vr else None,
            provenance={"absorbs": [{"skill_id": a} for a in absorbs], "risk_noted": bool(draft.risk)},
        )
    except ValidationError as exc:
        return None, absorbs, [f"schema: {e['loc']} {e['msg']}" for e in exc.errors()]
    return skill, absorbs, []


async def generalize_cluster(gen: Generalizer, cluster: list[Skill], neighbours: list[Skill],
                             depth: int = 0) -> list[GenResult]:
    """Ask the generalizer (1 retry on guardrail errors). A split recurses once into sub-groups."""
    ids = [s.id for s in cluster]
    res = GenResult(ids)
    errors: list[str] | None = None
    for _attempt in range(2):
        try:
            draft = await gen.generalize(cluster, neighbours, errors)
        except AdapterError as exc:
            res.status, res.errors = "error", [str(exc)]
            return [res]
        res.calls += 1
        res.cost += draft.cost
        res.risk = draft.risk
        if draft.decision == "reject":
            res.status = "rejected"
            return [res]
        if draft.decision == "split":
            if depth > 0:
                res.status, res.errors = "rejected", ["nested split"]
                return [res]
            by_id = {s.id: s for s in cluster}
            out = [res]
            res.status = "split"
            for g in draft.groups:
                sub = [by_id[i] for i in dict.fromkeys(g) if i in by_id]
                if len(sub) >= 2:
                    out += await generalize_cluster(gen, sub, neighbours, depth + 1)
            return out
        skill, absorbs, errs = check_draft(draft, cluster)
        if not errs:
            res.skill, res.absorbs, res.status, res.errors = skill, absorbs, "merged", []
            return [res]
        res.status, res.errors, errors = "invalid", errs, errs
    return [res]
