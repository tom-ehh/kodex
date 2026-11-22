"""Morpheus wake: episode -> flag -> write -> light gate -> activate | propose (design.md P2).

Flag gate, decision 7 (revised): rules lead, System One confirms. A skill is written
only if ALL hold:
1. history: >= `flag_hist_min` episodes (incl. this one) with the same field family +
   validator (or edited attr) in 14 days, counted in code;
2. basal `scope` (wording B, no count in state) P(pattern) >= `flag_pattern_min`;
3. the code shape check passes on the corrected value;
4. basal `validator_consistent` >= `flag_consistent_min` (a veto only).
`worth` is computed in code and stored for refitting; basal `family` is a tag.

Cold start: the first occurrence never creates a skill; with `learn_now` it goes to the
review queue (a human approval then skips rules 1, 2 and 4, never the light gate).
basal down: the episode waits `morpheus_basal_retries` ticks, then a rules-only decision
needs history >= max(flag_hist_min, 3) (design.md §6 fallback).

Runs in a background task (`run_forever`) or right after a `learn_now` correction, never
on the request path. The worker's `why` lives only in memory (`remember_why`) until its
episode is processed; it is never stored or logged.
"""

import asyncio
import contextlib
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from coi_formularze.morpheus import review
from coi_formularze.morpheus.basal import ask_flag
from coi_formularze.morpheus.gate import lint, smoke
from coi_formularze.morpheus.match import fold
from coi_formularze.morpheus.models import (
    Actor,
    Episode,
    EpisodeKind,
    EpisodeStatus,
    Layer,
    Skill,
    SkillStatus,
)
from coi_formularze.morpheus.replay import (
    check_correction,
    find_field,
    locate,
    replay_section,
)
from coi_formularze.morpheus.sanitize import PIIError
from coi_formularze.morpheus.store import SkillStore
from coi_formularze.morpheus.writer import WriterError, write_skill
from coi_formularze.route.router import BasalUnavailable

log = logging.getLogger(__name__)

HISTORY_DAYS = 14
RULES_ONLY_HIST = 3

# label keyword -> family (code side; basal's family is only a tag)
_FAMILY_KEYS = [
    ("pesel", "pesel"), ("nip", "nip_regon"), ("regon", "nip_regon"),
    ("kod pocztowy", "zip_code"), ("e-mail", "email_phone"), ("email", "email_phone"),
    ("telefon", "email_phone"), ("data", "date"), ("ulica", "address"),
    ("miejscowosc", "address"), ("adres", "address"), ("imie", "name"), ("nazwisko", "name"),
    ("nazwa", "name"), ("brak", "choice_checkbox"),
]
_VALIDATOR_FAMILY = {"PESEL": "pesel", "NIP": "nip_regon", "REGON": "nip_regon",
                     "ZIP_CODE": "zip_code", "EMAIL": "email_phone"}


def code_family(ep: Episode) -> str:
    if ep.edited_attr == "required" or ep.validator == "REQUIRED":
        return "required_flag"
    if ep.validator in _VALIDATOR_FAMILY:
        return _VALIDATOR_FAMILY[ep.validator]
    label = fold(ep.field_label or "")
    for key, fam in _FAMILY_KEYS:
        if key in label:
            return fam
    return "other"


def history_key(ep: Episode) -> tuple[str, str]:
    return code_family(ep), ep.validator or f"attr:{ep.edited_attr or 'value'}"


_SHAPES = {
    "ZIP_CODE": re.compile(r"^##-###$"), "PESEL": re.compile(r"^#{11}$"),
    "NIP": re.compile(r"^#{10}$"), "REGON": re.compile(r"^(#{9}|#{14})$"),
    "EMAIL": re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
}


def shape_ok(ep: Episode) -> bool:
    """Code consistency check (calibration: 0.79 vs basal 0.59-0.69)."""
    if ep.before_shape is not None and ep.after_shape == ep.before_shape:
        return False  # nothing changed (a trimmed space is a change)
    after = (ep.after_shape or "").strip()
    if ep.edited_attr in (None, "value"):
        rx = _SHAPES.get(ep.validator or "")
        return bool(after) and (rx.match(after) is not None if rx else True)
    if ep.edited_attr == "required":
        return after.lower() in ("true", "false")
    return bool(after)


def worth(hist: int, flagged: bool, shape: bool) -> int:
    if not shape:
        return 0
    return 3 if flagged else (2 if hist >= 2 else 1)


@dataclass
class Outcome:
    episode_id: str
    stage: str  # cold_start | shape | basal_retry | basal_veto | written | lint | activated | proposed | ...
    decision: str  # ignored | needs_human | pending | active | proposed
    skill_id: str | None = None
    hist: int = 0
    timings: dict = field(default_factory=dict)
    cost: float = 0.0
    detail: dict = field(default_factory=dict)


class Wake:
    def __init__(self, store: SkillStore, settings, chat=None, doc_cache=None,
                 ask=ask_flag, now=lambda: datetime.now(UTC)):
        self.store, self.settings, self.chat, self.cache = store, settings, chat, doc_cache
        self.ask, self.now = ask, now
        self._whys: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()
        self.writer_model = settings.morpheus_model or settings.tier_hard_model
        self.replay_model = settings.morpheus_replay_model or settings.tier_medium_model

    # ---- live why (memory only) ----

    def remember_why(self, episode_ids, why: str | None) -> None:
        if not why:
            return
        exp = time.monotonic() + self.settings.morpheus_doc_ttl_s
        for i in episode_ids:
            self._whys[i] = (why, exp)

    def _take_why(self, ep_id: str) -> str | None:
        now = time.monotonic()
        for k in [k for k, (_, e) in self._whys.items() if e <= now]:
            del self._whys[k]
        item = self._whys.pop(ep_id, None)
        return item[0] if item else None

    # ---- loop ----

    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("morpheus wake_tick failed")
            await asyncio.sleep(self.settings.morpheus_wake_interval_s)

    async def tick(self) -> list[Outcome]:
        """Expire old episodic skills, then process every pending correction."""
        if self.store.snapshot is not None:
            return []
        self.expire()
        pending = [e for e in self.store.episodes(EpisodeStatus.PENDING)
                   if e.kind is EpisodeKind.WORKER_CORRECTION]
        return [await self.process_one(e) for e in pending]

    async def learn_now(self, episode_ids: list[str]) -> list[Outcome]:
        """Process these episodes immediately (the gate is never skipped)."""
        wanted = set(episode_ids)
        eps = [e for e in self.store.episodes(EpisodeStatus.PENDING) if e.id in wanted]
        return [await self.process_one(e) for e in eps]

    # ---- one episode ----

    def _history(self, ep: Episode) -> int:
        key, since = history_key(ep), self.now() - timedelta(days=HISTORY_DAYS)
        return sum(1 for e in self.store.episodes()
                   if e.kind is EpisodeKind.WORKER_CORRECTION and history_key(e) == key
                   and (e.created_at is None or since <= e.created_at <= ep.created_at))

    def _same_key_ids(self, ep: Episode) -> list[str]:
        key = history_key(ep)
        return [e.id for e in self.store.episodes() if history_key(e) == key][-10:]

    def _finish(self, ep: Episode, flag: dict, status: EpisodeStatus, out: Outcome,
                queue: bool = False) -> Outcome:
        review.set_episode(self.store, ep.id, status, flag)
        if queue:
            review.enqueue(self.store, "needs_human_episode", ep.id,
                           diff={"episode_ids": [ep.id], "stage": out.stage, "flag": flag})
        return out

    async def process_one(self, ep: Episode) -> Outcome:
        async with self._lock:
            return await self._process(ep, self._take_why(ep.id))

    async def _process(self, ep: Episode, why: str | None) -> Outcome:
        s = self.settings
        flag = dict(ep.flag or {})
        learn_now, approved = bool(flag.get("learn_now")), bool(flag.get("human_approved"))
        hist = self._history(ep)
        fam = code_family(ep)
        shape = shape_ok(ep)
        flag.update({"hist": hist, "family_code": fam, "shape_ok": shape})
        out = Outcome(ep.id, "", "", hist=hist)

        if hist < s.flag_hist_min and not approved:
            out.stage, out.decision = "cold_start", "needs_human" if learn_now else "ignored"
            flag["worth"] = worth(hist, False, shape)
            return self._finish(ep, flag, EpisodeStatus.NEEDS_HUMAN if learn_now
                                else EpisodeStatus.IGNORED, out, queue=learn_now)
        if not shape:
            out.stage, out.decision = "shape", "ignored"
            flag["worth"] = 0
            return self._finish(ep, flag, EpisodeStatus.IGNORED, out)

        if not approved:
            t0 = time.monotonic()
            try:
                b = await self.ask(ep, why, s.router_url, s.router_timeout_s)
                out.timings["basal_s"] = round(time.monotonic() - t0, 3)
                flag["basal"] = b.scores()
                confirmed = b.p_pattern >= s.flag_pattern_min and b.consistent >= s.flag_consistent_min
                flag["family_basal"] = b.family
            except BasalUnavailable:
                retries = int(flag.get("basal_retries", 0)) + 1
                flag["basal_retries"] = retries
                if retries <= s.morpheus_basal_retries:
                    out.stage, out.decision = "basal_retry", "pending"
                    return self._finish(ep, flag, EpisodeStatus.PENDING, out)
                flag["rules_only"] = True
                confirmed = hist >= max(s.flag_hist_min, RULES_ONLY_HIST)
            if not confirmed:
                out.stage, out.decision = "basal_veto", "needs_human"
                flag["worth"] = worth(hist, False, shape)
                return self._finish(ep, flag, EpisodeStatus.NEEDS_HUMAN, out, queue=learn_now)
        flag["worth"] = worth(hist, True, shape)
        if self.chat is None:
            out.stage, out.decision = "no_writer", "pending"
            return self._finish(ep, flag, EpisodeStatus.PENDING, out)

        # ---- write ----
        parent = self._parent(ep, fam)
        prov = {"episode_ids": self._same_key_ids(ep), "hist": hist,
                "basal": flag.get("basal"), "worth": flag["worth"],
                "rules_only": bool(flag.get("rules_only")), "human_approved": approved}
        t0 = time.monotonic()
        try:
            w = await write_skill(self.chat, self.writer_model, ep, fam, why, parent, prov)
        except (WriterError, PIIError, ValueError) as exc:
            out.stage, out.decision = "writer_failed", "needs_human"
            flag["writer_error"] = type(exc).__name__
            return self._finish(ep, flag, EpisodeStatus.NEEDS_HUMAN, out, queue=learn_now)
        except Exception as exc:  # noqa: BLE001 - model service down: retry next tick
            out.stage, out.decision = "writer_down", "pending"
            flag["writer_error"] = type(exc).__name__
            return self._finish(ep, flag, EpisodeStatus.PENDING, out)
        out.timings["write_s"] = round(time.monotonic() - t0, 3)
        out.cost += w.cost
        cand = w.skill

        # ---- light gate ----
        lt = lint(cand)
        out.detail["lint"] = {"ok": lt.ok, "reasons": lt.reasons, "breadth": lt.breadth}
        if not lt.ok:
            out.stage, out.decision = "lint", "ignored"
            flag["gate"] = out.detail
            return self._finish(ep, flag, EpisodeStatus.IGNORED, out)
        base = [x for x in self.store.active() if parent is None or x.id != parent.id]
        live = cand.model_copy(update={"status": SkillStatus.ACTIVE})  # as it would run
        rp = await self._replay(ep, live, base, out)
        out.detail["replay"] = rp
        sm = {"status": "skipped"}
        if rp["status"] != "fail":
            t0 = time.monotonic()
            sm = await smoke(self.chat, self.replay_model, live, base, s.morpheus_gate_md_dir,
                             s.skills_max_per_section)
            out.timings["smoke_s"] = round(time.monotonic() - t0, 3)
            out.cost += sm.get("cost", 0.0)
        out.detail["smoke"] = sm
        flag["gate"] = {k: out.detail[k] for k in ("replay", "smoke")} | {"lint": lt.ok}
        out.skill_id = cand.id
        if rp["status"] in ("pass", "unreplayed") and sm["status"] != "fail":
            self._activate(cand, parent, unreplayed=rp["status"] == "unreplayed", gate=flag["gate"])
            out.stage, out.decision = "activated", "active"
            return self._finish(ep, flag, EpisodeStatus.SKILLED, out)
        self._propose(cand, parent, ep, out.detail)
        out.stage, out.decision = "proposed", "proposed"
        return self._finish(ep, flag, EpisodeStatus.NEEDS_HUMAN, out)

    def _parent(self, ep: Episode, fam: str) -> Skill | None:
        """An active learned skill for the same validator + family: patched, not duplicated."""
        for sk in self.store.active():
            if sk.layer in (Layer.EPISODIC, Layer.GENERAL) and \
                    sk.trigger.validator == ep.validator and \
                    (sk.trigger.field_family or "other") == fam:
                return sk
        return None

    async def _replay(self, ep: Episode, cand: Skill, base: list[Skill], out: Outcome) -> dict:
        cached = self.cache.get(ep.form_id) if (self.cache and ep.form_id) else None
        if cached is None or cached.form is None:
            return {"status": "unreplayed", "reason": "document expired"}
        where = locate(cached.form, ep.field_path)
        if where is None:
            return {"status": "unreplayed", "reason": "field not in cached form"}
        ref, label = where
        t0 = time.monotonic()
        r = await replay_section(self.chat, self.replay_model, cached.doc.markdown, ref,
                                 [*base, cand], self.settings.skills_max_per_section)
        out.timings["replay_s"] = round(time.monotonic() - t0, 3)
        out.cost += r.cost
        if not r.ok:
            return {"status": "fail", "reason": "section draft failed", "errors": len(r.errors)}
        f = find_field(r.fields, label or ep.field_label)
        ok, why_txt = check_correction(f, ep.edited_attr, ep.before_shape, ep.after_shape, [cand])
        if ok and ep.validator and ep.edited_attr in (None, "value") and f is not None \
                and ep.validator not in (f.get("validation") or []):
            ok, why_txt = False, f"validator {ep.validator} missing"
        return {"status": "pass" if ok else "fail", "reason": why_txt,
                "skill_used": cand.id in r.skills_used, "section": ref.title}

    def _expires(self, unreplayed: bool) -> datetime:
        days = self.settings.episodic_ttl_days / (2 if unreplayed else 1)
        return self.now() + timedelta(days=days)

    def _activate(self, cand: Skill, parent: Skill | None, unreplayed: bool, gate: dict) -> Skill:
        exp = self._expires(unreplayed)
        with self.store.batch(f"wake activate {cand.id}"):
            if parent is not None:
                changes = cand.model_dump(include={"description", "trigger", "scope", "pattern",
                                                   "prompt_snippet", "validator_rule", "provenance"})
                changes |= {"status": SkillStatus.ACTIVE, "expires_at": exp}
                sk = self.store.patch(parent.id, changes, Actor.WAKE)
            else:
                sid, n = cand.id, 2
                while self.store.get(sid) is not None:  # name clash with a retired skill
                    sid, n = f"{cand.id[:60]}-{n}", n + 1
                sk = self.store.create(cand.model_copy(update={
                    "id": sid, "status": SkillStatus.ACTIVE, "expires_at": exp}), Actor.WAKE)
            self.store._event(sk.id, sk.version, "wake_gate", Actor.WAKE,
                              {"unreplayed": unreplayed, "replay": gate.get("replay", {}).get("status"),
                               "smoke": gate.get("smoke", {}).get("status")})
            self._evict(keep=sk.id)
        return sk

    def _evict(self, keep: str) -> None:
        """Over the cap: retire the weakest (wins - losses), soonest-expiring, never `keep`."""
        eps = [x for x in self.store.list_skills(Layer.EPISODIC, SkillStatus.ACTIVE)
               if not x.pinned and x.id != keep]
        over = len(eps) + 1 - self.settings.episodic_max
        if over <= 0:
            return
        eps.sort(key=lambda x: (x.wins - x.losses, x.expires_at or self.now()))
        for x in eps[:over]:
            self.store.retire(x.id, Actor.WAKE)

    def expire(self) -> list[str]:
        now = self.now()
        gone = [x.id for x in self.store.list_skills(Layer.EPISODIC, SkillStatus.ACTIVE)
                if x.expires_at and not x.pinned and x.expires_at.replace(
                    tzinfo=x.expires_at.tzinfo or UTC) <= now]
        if gone:
            with self.store.batch("wake expire"):
                for sid in gone:
                    self.store.retire(sid, Actor.WAKE)
        return gone

    def _propose(self, cand: Skill, parent: Skill | None, ep: Episode, detail: dict) -> None:
        diff = {"skill": cand.model_dump(mode="json"), "episode_ids": [ep.id],
                "parent": {"id": parent.id, "version": parent.version} if parent else None,
                "changes": _diff(parent, cand)}
        review.enqueue(self.store, "proposed_skill", cand.id, diff=diff,
                       replay={k: detail.get(k) for k in ("replay", "smoke", "lint")})

    def approve(self, item_id: str) -> dict:
        """Human decision on a review item: activate a proposal, or re-run an episode."""
        item = review.get_item(self.store, item_id)
        if item is None or item.status != "pending":
            raise KeyError(item_id)
        if item.kind == "proposed_skill":
            cand = Skill.model_validate(item.diff["skill"])
            parent = None
            if item.diff.get("parent"):
                parent = self.store.get(item.diff["parent"]["id"])
                if parent is not None and parent.status is not SkillStatus.ACTIVE:
                    parent = None
            sk = self._activate(cand, parent, unreplayed=False,
                                gate={"replay": {"status": "human"}, "smoke": {"status": "human"}})
            review.decide(self.store, item_id, "approved")
            return {"skill_id": sk.id, "version": sk.version}
        ep = next((e for e in self.store.episodes() if e.id == item.ref_id), None)
        if ep is None:
            raise KeyError(item.ref_id)
        review.set_episode(self.store, ep.id, EpisodeStatus.PENDING,
                           (ep.flag or {}) | {"human_approved": True})
        review.decide(self.store, item_id, "approved")
        return {"episode_id": ep.id, "requeued": True}

    def reject(self, item_id: str) -> dict:
        item = review.get_item(self.store, item_id)
        if item is None or item.status != "pending":
            raise KeyError(item_id)
        if item.kind == "needs_human_episode":
            review.set_episode(self.store, item.ref_id, EpisodeStatus.IGNORED)
        review.decide(self.store, item_id, "rejected")
        return {"rejected": item_id}


def _diff(parent: Skill | None, cand: Skill) -> dict:
    keys = ("description", "trigger", "prompt_snippet", "validator_rule")
    new = cand.model_dump(mode="json", include=set(keys))
    if parent is None:
        return {k: [None, new[k]] for k in keys}
    old = parent.model_dump(mode="json", include=set(keys))
    return {k: [old[k], new[k]] for k in keys if old[k] != new[k]}


async def start_worker(wake: Wake) -> asyncio.Task:
    return asyncio.create_task(wake.run_forever(), name="morpheus-wake")


async def stop_worker(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
