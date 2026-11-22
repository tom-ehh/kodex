"""Pruning (sleep.md §4): plan which active skills to archive or retire. Never deletes.

Rules, in order; pinned skills and seeds are never touched:
1. expiry: an episodic skill past `expires_at` -> archive;
2. loser: `losses >= 3 and losses > 2*wins` -> archive (the cycle's mini replay confirms);
3. dominated: an episodic skill whose keywords are a subset of an active general skill's,
   with the same validator/family and trigram cosine >= 0.9 -> retire, absorbed_into it;
4. caps: over `episodic_max` / `general_max`, archive the lowest
   `(wins - 2*losses) / max(hits, 1)` (oldest first on ties); zero-use skills younger than the
   grace period (`episodic_ttl_days`) are spared ("absence of evidence").
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from kodex.morpheus.match import fold
from kodex.morpheus.models import Layer, Skill
from kodex.morpheus.sleep.blocking import cosine, skill_doc, tfidf_vectors

LOSER_MIN = 3
DOMINATED_COS = 0.9


@dataclass(frozen=True)
class PruneAction:
    skill_id: str
    action: str  # archive | retire
    reason: str
    absorbed_into: str | None = None


def _score(s: Skill) -> float:
    return (s.wins - 2 * s.losses) / max(s.hits, 1)


def plan_prune(active: list[Skill], now: datetime, created: dict[str, datetime | None],
               episodic_max: int, general_max: int, grace_days: int) -> list[PruneAction]:
    acts: dict[str, PruneAction] = {}
    prunable = [s for s in active if not s.pinned and s.layer is not Layer.SEED]
    for s in prunable:
        if s.layer is Layer.EPISODIC and s.expires_at and s.expires_at <= now:
            acts[s.id] = PruneAction(s.id, "archive", "expired")
        elif s.losses >= LOSER_MIN and s.losses > 2 * s.wins:
            acts[s.id] = PruneAction(s.id, "archive", "loser")
    generals = [s for s in active if s.layer is Layer.GENERAL and s.id not in acts]
    episodics = [s for s in prunable if s.layer is Layer.EPISODIC and s.id not in acts]
    if generals and episodics:
        vecs = dict(zip([s.id for s in generals + episodics],
                        tfidf_vectors([skill_doc(s) for s in generals + episodics]), strict=True))
        for e in episodics:
            ek = {fold(x) for x in e.trigger.labels_any}
            for g in generals:
                same_key = (e.trigger.validator, e.trigger.field_family) == \
                    (g.trigger.validator, g.trigger.field_family)
                if same_key and ek <= {fold(x) for x in g.trigger.labels_any} and \
                        cosine(vecs[e.id], vecs[g.id]) >= DOMINATED_COS:
                    acts[e.id] = PruneAction(e.id, "retire", "dominated", g.id)
                    break
    grace = timedelta(days=grace_days)
    epoch = datetime.min.replace(tzinfo=now.tzinfo)
    for layer, cap in ((Layer.EPISODIC, episodic_max), (Layer.GENERAL, general_max)):
        left = [s for s in active if s.layer is layer and s.id not in acts]
        over = len(left) - cap
        if over <= 0:
            continue
        cand = [s for s in left if not s.pinned and not (
            s.hits == 0 and (c := created.get(s.id)) is not None and now - c < grace)]
        cand.sort(key=lambda s: (_score(s), created.get(s.id) or epoch, s.id))
        for s in cand[:over]:
            acts[s.id] = PruneAction(s.id, "archive", f"over {layer.value} cap {cap}")
    return list(acts.values())
