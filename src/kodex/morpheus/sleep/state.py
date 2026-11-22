"""Sleep state over the store's SQLite: the cycle lock, cycle rows, small queries.

Tables `sleep_lock` and `sleep_cycle_detail` come from the store's schema migration v2.
"""

import json
from datetime import UTC, datetime, timedelta

from kodex.morpheus.store import SkillStore

LOCK_STALE_S = 600.0


def _now() -> datetime:
    return datetime.now(UTC)


def try_lock(store: SkillStore, holder: str, stale_s: float = LOCK_STALE_S) -> bool:
    """Take the single sleep lock (BEGIN IMMEDIATE); a holder whose heartbeat is older than
    `stale_s` is presumed dead and replaced."""
    with store._lock:
        db = store._db
        db.execute("BEGIN IMMEDIATE")
        try:
            r = db.execute("SELECT holder, heartbeat_at FROM sleep_lock WHERE name='sleep'").fetchone()
            if r and r["holder"] != holder and \
                    datetime.fromisoformat(r["heartbeat_at"]) > _now() - timedelta(seconds=stale_s):
                db.execute("ROLLBACK")
                return False
            db.execute("INSERT OR REPLACE INTO sleep_lock VALUES ('sleep', ?, ?)",
                       (holder, _now().isoformat()))
            db.execute("COMMIT")
            return True
        except BaseException:
            db.execute("ROLLBACK")
            raise


def heartbeat(store: SkillStore, holder: str) -> None:
    with store._lock:
        store._db.execute("UPDATE sleep_lock SET heartbeat_at=? WHERE name='sleep' AND holder=?",
                          (_now().isoformat(), holder))


def release(store: SkillStore, holder: str) -> None:
    with store._lock:
        store._db.execute("DELETE FROM sleep_lock WHERE name='sleep' AND holder=?", (holder,))


def locked(store: SkillStore, stale_s: float = LOCK_STALE_S) -> bool:
    with store._lock:
        r = store._db.execute("SELECT heartbeat_at FROM sleep_lock WHERE name='sleep'").fetchone()
    return bool(r) and datetime.fromisoformat(r[0]) > _now() - timedelta(seconds=stale_s)


def write_cycle(store: SkillStore, row: dict, status: str, detail: dict) -> None:
    """One `sleep_cycles` row (design.md §3 columns) + its detail JSON (ids and numbers only)."""
    cols = ["id", "started_at", "ended_at", "trigger", "candidates", "clusters", "drafts",
            "activated", "proposed", "pruned", "acc_gate_before", "acc_gate_after",
            "acc_heldout", "skill_count", "cost"]
    with store._lock:
        store._db.execute(
            f"INSERT OR REPLACE INTO sleep_cycles ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [row.get(c) for c in cols])
        store._db.execute("INSERT OR REPLACE INTO sleep_cycle_detail VALUES (?,?,?)",
                          (row["id"], status, json.dumps(detail, ensure_ascii=False)))


def cycles(store: SkillStore, limit: int = 200) -> list[dict]:
    with store._lock:
        rows = store._db.execute(
            "SELECT c.*, d.status AS status, d.detail_json FROM sleep_cycles c"
            " LEFT JOIN sleep_cycle_detail d ON d.cycle_id=c.id ORDER BY c.started_at LIMIT ?",
            (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = json.loads(d.pop("detail_json") or "{}")
        out.append(d)
    return out


def skill_created_at(store: SkillStore, skill_id: str) -> datetime | None:
    """When version 1 of a skill was written (its age for prune grace)."""
    with store._lock:
        r = store._db.execute("SELECT MIN(created_at) FROM skills WHERE id=?", (skill_id,)).fetchone()
    return datetime.fromisoformat(r[0]) if r and r[0] else None


def pending_pattern_episodes(store: SkillStore) -> int:
    """Pattern episodes waiting for sleep: wake confirmed them as a pattern and turned them
    into an episodic skill (`status=skilled`), and no sleep has consolidated them yet."""
    with store._lock:
        return store._db.execute(
            "SELECT COUNT(*) FROM episodes WHERE status='skilled' AND consolidated=0").fetchone()[0]
