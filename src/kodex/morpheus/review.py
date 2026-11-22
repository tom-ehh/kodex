"""Review queue and episode-status helpers over the SkillStore's SQLite (design.md §3).

Kept out of store.py so P2 (wake) and P3 (sleep) can add to it without touching the
store's core. Every write passes the PII lint, like the store's own writes.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from kodex.morpheus.models import EpisodeStatus
from kodex.morpheus.sanitize import assert_clean
from kodex.morpheus.store import SkillStore


class ReviewItem(BaseModel):
    id: str
    kind: str  # proposed_skill | needs_human_episode | needs_review_form
    ref_id: str
    diff: dict[str, Any] | None = None
    replay: dict[str, Any] | None = None
    status: str  # pending | approved | rejected
    decided_by: str | None = None
    created_at: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


def enqueue(store: SkillStore, kind: str, ref_id: str, diff: dict | None = None,
            replay: dict | None = None) -> ReviewItem:
    """Add a pending item; an existing pending item for the same ref is reused."""
    assert_clean({"diff": diff, "replay": replay})
    with store.batch("review"):
        r = store._db.execute(
            "SELECT id FROM review_queue WHERE ref_id=? AND kind=? AND status='pending'",
            (ref_id, kind)).fetchone()
        if r:
            store._db.execute("UPDATE review_queue SET diff_json=?, replay_json=? WHERE id=?",
                              (json.dumps(diff), json.dumps(replay), r["id"]))
            rid = r["id"]
        else:
            rid = f"rv_{uuid.uuid4().hex[:12]}"
            store._db.execute(
                "INSERT INTO review_queue VALUES (?,?,?,?,?,?,?,?)",
                (rid, kind, ref_id, json.dumps(diff), json.dumps(replay), "pending", None, _now()))
    return get_item(store, rid)  # type: ignore[return-value]


def _row(r) -> ReviewItem:
    return ReviewItem(id=r["id"], kind=r["kind"], ref_id=r["ref_id"],
                      diff=json.loads(r["diff_json"]) if r["diff_json"] else None,
                      replay=json.loads(r["replay_json"]) if r["replay_json"] else None,
                      status=r["status"], decided_by=r["decided_by"], created_at=r["created_at"])


def get_item(store: SkillStore, item_id: str) -> ReviewItem | None:
    with store._lock:
        r = store._db.execute("SELECT * FROM review_queue WHERE id=?", (item_id,)).fetchone()
    return _row(r) if r else None


def items(store: SkillStore, status: str | None = "pending") -> list[ReviewItem]:
    q, args = "SELECT * FROM review_queue", ()
    if status:
        q, args = q + " WHERE status=?", (status,)
    with store._lock:
        return [_row(r) for r in store._db.execute(q + " ORDER BY created_at, id", args)]


def decide(store: SkillStore, item_id: str, status: str, by: str = "human") -> None:
    with store.batch("review"):
        store._db.execute("UPDATE review_queue SET status=?, decided_by=? WHERE id=?",
                          (status, by, item_id))


def set_episode(store: SkillStore, episode_id: str, status: EpisodeStatus | None = None,
                flag: dict | None = None) -> None:
    """Update an episode's status and/or flag (shapes, scores; never text)."""
    if flag is not None:
        assert_clean(flag)
    with store.batch("episode"):
        if status is not None:
            store._db.execute("UPDATE episodes SET status=? WHERE id=?",
                              (EpisodeStatus(status).value, episode_id))
        if flag is not None:
            store._db.execute("UPDATE episodes SET flag_json=? WHERE id=?",
                              (json.dumps(flag), episode_id))
