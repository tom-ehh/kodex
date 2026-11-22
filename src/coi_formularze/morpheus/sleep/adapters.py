"""Seams the sleep cycle calls through: pair judge, checks, generalizer, replayer.

The cycle only sees these Protocols (tests use fakes). The real implementations are thin
wrappers over the wake modules: `morpheus.basal` (typed questions), `morpheus.writer`
(generalize prompt) and `morpheus.replay` (section replay of the gate forms).
"""

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from coi_formularze.morpheus import basal, writer
from coi_formularze.morpheus.gate import _gate_md, gate_forms
from coi_formularze.morpheus.match import SectionTarget, scope_ok, score
from coi_formularze.morpheus.models import Skill
from coi_formularze.morpheus.replay import SectionRef, find_field, replay_section
from coi_formularze.morpheus.sanitize import pii_findings
from coi_formularze.route.router import BasalUnavailable

# docs/examples/README.md "Evaluation sets": the gate may be learned from and replayed on;
# held-out is measuring only and never decides anything.
GATE_FORMS = (
    "zwrot-oplaty-skarbowej", "wydanie-zaswiadczenia", "wynajecie-swietlicy-wiejskiej",
    "zajecie-pasa-drogowego", "psy-ras-agresywnych", "oplata-za-odpady",
)
HELDOUT_FORMS = (
    "numer-porzadkowy", "nadplata-podatku", "ho-wniosek-o-udostepnienie-informacji-publicznej",
    "ho-wniosek-o-dzierzawe-najem-uzyczenie-nieruchomosci", "ho-wniosek-o-rejestracje-pojazdu",
    "ho-wniosek-o-pozwolenie-na-uzytkowanie-pb-17",
)


class AdapterError(RuntimeError):
    """An adapter's backing service failed (basal down, LLM error, replay infra)."""


def skill_text(s: Skill, tag: str = "") -> str:
    """The anonymised text basal and the generalizer see: field, pattern, snippet."""
    pole = ", ".join(s.trigger.labels_any) or s.trigger.field_family or s.name
    if s.trigger.validator:
        pole += f" (walidator {s.trigger.validator})"
    wzorzec = " -> ".join(v for v in (s.pattern.get("before"), s.pattern.get("after")) if v)
    head = f"UMIEJĘTNOŚĆ {tag}".rstrip()
    return (f"{head}\npole: {pole}\nwzorzec: {wzorzec or s.description}\n"
            f"fragment: {s.prompt_snippet or s.description}")


def scrub(text: str) -> str:
    """Refuse to send anything the PII lint flags (skills should never hold PII)."""
    if pii_findings(text):
        raise AdapterError("PII-like content in basal state; refused")
    return text


# ---------------------------------------------------------------- protocols


class SameJudge(Protocol):
    async def p_same(self, a: Skill, b: Skill) -> float:
        """P(same pattern) for the ordered pair (a, b)."""


class Checker(Protocol):
    async def faithful(self, original: Skill, draft: Skill) -> float: ...
    async def conflict(self, draft: Skill, other: Skill) -> dict[str, float]:
        """Probabilities over {conflict, compatible, duplicate}."""


@dataclass
class Draft:
    """A generalizer answer (sleep.md §3), already parsed."""

    decision: str  # merge | split | reject
    skill: dict[str, Any] | None = None
    absorbs: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    not_covered: list[str] = field(default_factory=list)
    groups: list[list[str]] = field(default_factory=list)  # for split
    risk: str = ""
    cost: float = 0.0


class Generalizer(Protocol):
    async def generalize(self, cluster: list[Skill], neighbours: list[Skill],
                         errors: list[str] | None = None) -> Draft: ...


@dataclass
class ReplayResult:
    accuracy: float
    per_case: dict[str, bool]  # case id -> passed
    cost: float = 0.0


class Replayer(Protocol):
    async def replay(self, skills: list[Skill], forms: tuple[str, ...],
                     focus: list[Skill] | None = None) -> ReplayResult:
        """Draft `forms` with exactly `skills` loaded and score the fields' validators vs
        expected.json. `focus`: only the sections those skills' triggers touch (None = all)."""


# ---------------------------------------------------------------- basal (morpheus.basal)


class BasalJudge:
    """SameJudge + Checker over `morpheus.basal.ask_questions` (calibrated wording F)."""

    def __init__(self, url: str, timeout: float = 30.0, client: httpx.AsyncClient | None = None):
        self.url, self.timeout, self.client = url, timeout, client
        self.calls = 0

    async def _ask(self, state: str, key: str) -> dict:
        self.calls += 1
        try:
            answers = await basal.ask_questions(scrub(state), {key: basal.SLEEP_QUESTIONS[key]},
                                                self.url, self.timeout, self.client)
            return answers[key]
        except (BasalUnavailable, KeyError, TypeError) as exc:
            raise AdapterError(f"basal: {exc}") from exc

    async def p_same(self, a: Skill, b: Skill) -> float:
        ans = await self._ask(skill_text(a, "A") + "\n\n" + skill_text(b, "B"), "same")
        return float(ans["probabilities"]["same"])

    async def faithful(self, original: Skill, draft: Skill) -> float:
        state = f"SKRÓT ORYGINALNY\n{skill_text(original)}\n\nREGUŁA OGÓLNA\n{skill_text(draft)}"
        ans = await self._ask(state, "faithful")
        return float(ans.get("noul", ans.get("probabilities", {}).get("true", 0.0)))

    async def conflict(self, draft: Skill, other: Skill) -> dict[str, float]:
        ans = await self._ask(skill_text(draft, "A") + "\n\n" + skill_text(other, "B"), "conflict")
        return {k: float(v) for k, v in ans["probabilities"].items()}


# ---------------------------------------------------------------- writer (morpheus.writer)


class LlmGeneralizer:
    """Generalizer over `morpheus.writer.write_general` (tier model, `hard` by default)."""

    def __init__(self, chat, model: str):
        self.chat, self.model = chat, model

    async def generalize(self, cluster, neighbours, errors=None) -> Draft:
        try:
            data, cost = await writer.write_general(self.chat, self.model, cluster, neighbours, errors)
        except Exception as exc:  # WriterError, LlmError, network
            raise AdapterError(f"generalizer: {type(exc).__name__}: {exc}") from exc
        known = {k: data[k] for k in Draft.__dataclass_fields__ if k in data and k != "cost"}
        return Draft(**known, cost=cost or 0.0)


# ---------------------------------------------------------------- replay (morpheus.replay)


def _fingerprint(skills: list[Skill]) -> str:
    return hashlib.sha1(",".join(sorted(f"{s.id}@{s.version}" for s in skills)).encode()).hexdigest()[:16]


class GateReplayer:
    """Replayer over `morpheus.replay.replay_section` on labelled forms (default: the 6 gate
    forms of `gate_labels.json`, markdown from `COI_MORPHEUS_GATE_MD_DIR`).

    A case is one expected field: it passes when the replayed field exists and its
    validator set equals expected.json's. Results are cached per (form, section, the
    skills that can touch that section), so an unchanged section is never re-drafted."""

    def __init__(self, chat, model: str, md_dir: str | None, max_per_section: int = 5,
                 labels: dict[str, list[dict]] | None = None):
        self.chat, self.model, self.md_dir, self.max = chat, model, md_dir, max_per_section
        self.labels = dict(gate_forms()) | (labels or {})
        self.cache: dict[tuple, tuple[dict[str, bool], float]] = {}
        self.calls = self.cache_hits = 0
        self.cost = 0.0
        self.seconds = 0.0

    @staticmethod
    def _touches(skill: Skill, section: dict) -> bool:
        labels = [section["title"]] + [f["label"] for f in section["fields"]]
        vals = {v for f in section["fields"] for v in f["validation"]}
        where = "\n".join(labels)
        return score(skill, SectionTarget.of(labels, vals)) > 0 and scope_ok(skill, where, where)

    async def _section(self, slug: str, md: str, sec: dict, skills: list[Skill]) -> dict[str, bool]:
        relevant = [s for s in skills if self._touches(s, sec)]
        key = (slug, sec["title"], _fingerprint(relevant))
        if key in self.cache:
            self.cache_hits += 1
            return self.cache[key][0]
        t = time.perf_counter()
        r = await replay_section(self.chat, self.model, md, SectionRef("", sec["title"]),
                                 skills, self.max)
        self.seconds += time.perf_counter() - t
        self.calls += 1
        self.cost += r.cost
        cases = {}
        for f in sec["fields"]:
            got = find_field(r.fields, f["label"]) if r.ok else None
            cases[f"{slug}/{sec['title']}/{f['label']}"] = got is not None and \
                set(got.get("validation") or []) == set(f["validation"])
        self.cache[key] = (cases, r.cost)
        return cases

    async def replay(self, skills, forms, focus=None) -> ReplayResult:
        cost0 = self.cost
        cases: dict[str, bool] = {}
        for slug in forms:
            if slug not in self.labels:
                raise AdapterError(f"no labels for form {slug}")
            secs = [s for s in self.labels[slug]
                    if focus is None or any(self._touches(f, s) for f in focus)]
            if not secs:
                continue
            md = _gate_md(self.md_dir, slug)
            if md is None:
                raise AdapterError(f"no markdown for form {slug} (COI_MORPHEUS_GATE_MD_DIR)")
            for sec in secs:
                cases |= await self._section(slug, md, sec, list(skills))
        acc = sum(cases.values()) / len(cases) if cases else 1.0
        return ReplayResult(acc, cases, cost=self.cost - cost0)


class UnavailableReplayer:
    """Used when there is no LLM or no gate markdown: every gate run is an infra error,
    so nothing activates (drafts are not even proposed; the cycle aborts untouched)."""

    async def replay(self, skills, forms, focus=None) -> ReplayResult:
        raise AdapterError("replay unavailable (no LLM key or COI_MORPHEUS_GATE_MD_DIR)")
