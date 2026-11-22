"""SkillStore: Morpheus skills, episodes and skillset versions in one SQLite file (stdlib).

- Every skill change writes a new row (id, version) and an append-only `skill_events` row.
- The active set is a **skillset version** (a manifest of skill id+version); every change
  to it writes a new version and moves the `current` pointer. `rollback()` moves it back.
- A pinned store (`COI_SKILLS_SNAPSHOT`) serves that version's manifest and refuses any
  write that would change the active set; counters, episodes and proposals still work.
- Every stored record passes the PII lint (`sanitize.assert_clean`) first.
"""

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from coi_formularze.morpheus.models import (
    Actor,
    Episode,
    EpisodeStatus,
    HitOutcome,
    Layer,
    Skill,
    SkillEvent,
    SkillRef,
    SkillsetVersion,
    SkillStatus,
)
from coi_formularze.morpheus.sanitize import assert_clean

SCHEMA_VERSION = 1

_SCHEMA_V1 = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE episodes (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, kind TEXT NOT NULL, form_id TEXT,
  field_path TEXT, field_label TEXT, validator TEXT, error TEXT, before_shape TEXT,
  after_shape TEXT, edited_attr TEXT, tier_from TEXT, tier_to TEXT,
  skills_used TEXT NOT NULL DEFAULT '[]', flag_json TEXT, status TEXT NOT NULL,
  consolidated INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE skills (
  id TEXT NOT NULL, version INTEGER NOT NULL, name TEXT NOT NULL, layer TEXT NOT NULL,
  status TEXT NOT NULL, description TEXT NOT NULL, trigger_json TEXT NOT NULL,
  scope_json TEXT NOT NULL, pattern_json TEXT NOT NULL, prompt_snippet TEXT NOT NULL,
  validator_rule_json TEXT, provenance_json TEXT NOT NULL, expires_at TEXT,
  hits INTEGER NOT NULL DEFAULT 0, wins INTEGER NOT NULL DEFAULT 0,
  losses INTEGER NOT NULL DEFAULT 0, parent_version INTEGER, absorbed_into TEXT,
  pinned INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
  PRIMARY KEY (id, version)
);
CREATE TABLE skill_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, skill_id TEXT, version INTEGER,
  event TEXT NOT NULL, actor TEXT NOT NULL, detail_json TEXT NOT NULL
);
CREATE TRIGGER skill_events_no_update BEFORE UPDATE ON skill_events
  BEGIN SELECT RAISE(ABORT, 'skill_events is append-only'); END;
CREATE TRIGGER skill_events_no_delete BEFORE DELETE ON skill_events
  BEGIN SELECT RAISE(ABORT, 'skill_events is append-only'); END;
CREATE TABLE skill_hits (
  skill_id TEXT NOT NULL, version INTEGER NOT NULL, form_id TEXT, outcome TEXT NOT NULL,
  ts TEXT NOT NULL
);
CREATE TABLE skillset_versions (
  version INTEGER PRIMARY KEY, created_at TEXT NOT NULL, manifest_json TEXT NOT NULL,
  reason TEXT NOT NULL
);
CREATE TABLE review_queue (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, ref_id TEXT NOT NULL, diff_json TEXT,
  replay_json TEXT, status TEXT NOT NULL, decided_by TEXT, created_at TEXT NOT NULL
);
CREATE TABLE sleep_cycles (
  id TEXT PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT, trigger TEXT NOT NULL,
  candidates INTEGER, clusters INTEGER, drafts INTEGER, activated INTEGER, proposed INTEGER,
  pruned INTEGER, acc_gate_before REAL, acc_gate_after REAL, acc_heldout REAL,
  skill_count INTEGER, cost REAL
);
"""

# Index i holds the script that migrates schema version i to i+1.
_MIGRATIONS = [_SCHEMA_V1]


class PinnedError(RuntimeError):
    """The store is pinned to a skillset version; the active set cannot change."""


class SkillNotFound(KeyError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


class SkillStore:
    def __init__(self, path: str | Path, snapshot: int | None = None):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")
        self._depth = 0
        self._dirty: list[str] = []
        self._migrate()
        self.snapshot = snapshot
        if snapshot is not None and self._manifest_row(snapshot) is None:
            raise ValueError(f"COI_SKILLS_SNAPSHOT={snapshot}: no such skillset version")

    # ---- plumbing ----

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _migrate(self) -> None:
        with self._lock:
            current = self._db.execute("PRAGMA user_version").fetchone()[0]
            if current > SCHEMA_VERSION:
                raise RuntimeError(f"morpheus db schema {current} is newer than {SCHEMA_VERSION}")
            for v in range(current, SCHEMA_VERSION):
                self._db.executescript(f"BEGIN; {_MIGRATIONS[v]} PRAGMA user_version={v + 1}; COMMIT;")

    @contextmanager
    def batch(self, reason: str = "batch") -> Iterator[None]:
        """One transaction; at most one new skillset version for all changes inside."""
        with self._lock:
            outer = self._depth == 0
            if outer:
                self._db.execute("BEGIN IMMEDIATE")
                self._dirty = []
            self._depth += 1
            try:
                yield
                if outer and self._dirty:
                    self._write_skillset(reason if reason != "batch" else ", ".join(self._dirty))
                if outer:
                    self._db.execute("COMMIT")
            except BaseException:
                if outer:
                    self._db.execute("ROLLBACK")
                    self._dirty = []
                raise
            finally:
                self._depth -= 1

    def _event(self, skill_id, version, event, actor: Actor, detail=None) -> None:
        detail = detail or {}
        assert_clean(detail)
        self._db.execute(
            "INSERT INTO skill_events (ts, skill_id, version, event, actor, detail_json)"
            " VALUES (?,?,?,?,?,?)",
            (_iso(_now()), skill_id, version, event, Actor(actor).value, json.dumps(detail)),
        )

    def _set_changed(self, why: str) -> None:
        if self.snapshot is not None:
            raise PinnedError(f"store pinned to skillset {self.snapshot}: refused {why}")
        self._dirty.append(why)

    # ---- skills ----

    @staticmethod
    def _row_to_skill(r: sqlite3.Row) -> Skill:
        return Skill(
            id=r["id"], version=r["version"], name=r["name"], layer=r["layer"],
            status=r["status"], description=r["description"],
            trigger=json.loads(r["trigger_json"]), scope=json.loads(r["scope_json"]),
            pattern=json.loads(r["pattern_json"]), prompt_snippet=r["prompt_snippet"],
            validator_rule=json.loads(r["validator_rule_json"]) if r["validator_rule_json"] else None,
            provenance=json.loads(r["provenance_json"]),
            expires_at=r["expires_at"], hits=r["hits"], wins=r["wins"], losses=r["losses"],
            parent_version=r["parent_version"], absorbed_into=r["absorbed_into"],
            pinned=bool(r["pinned"]),
        )

    def _insert(self, s: Skill) -> None:
        assert_clean(s.model_dump(mode="json"))
        self._db.execute(
            "INSERT INTO skills VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                s.id, s.version, s.name, s.layer.value, s.status.value, s.description,
                s.trigger.model_dump_json(), json.dumps(s.scope), json.dumps(s.pattern),
                s.prompt_snippet,
                s.validator_rule.model_dump_json() if s.validator_rule else None,
                json.dumps(s.provenance), _iso(s.expires_at), s.hits, s.wins, s.losses,
                s.parent_version, s.absorbed_into, int(s.pinned), _iso(_now()),
            ),
        )

    def get(self, skill_id: str, version: int | None = None) -> Skill | None:
        with self._lock:
            if version is None:
                r = self._db.execute(
                    "SELECT * FROM skills WHERE id=? ORDER BY version DESC LIMIT 1", (skill_id,)
                ).fetchone()
            else:
                r = self._db.execute(
                    "SELECT * FROM skills WHERE id=? AND version=?", (skill_id, version)
                ).fetchone()
            return self._row_to_skill(r) if r else None

    def _latest(self, skill_id: str) -> Skill:
        if (s := self.get(skill_id)) is None:
            raise SkillNotFound(skill_id)
        return s

    def list_skills(
        self, layer: Layer | None = None, status: SkillStatus | None = None
    ) -> list[Skill]:
        """Latest version of each skill, optionally filtered."""
        with self._lock:
            rows = self._db.execute(
                "SELECT s.* FROM skills s JOIN (SELECT id, MAX(version) v FROM skills GROUP BY id) m"
                " ON s.id=m.id AND s.version=m.v ORDER BY s.id"
            ).fetchall()
        out = [self._row_to_skill(r) for r in rows]
        return [
            s for s in out
            if (layer is None or s.layer == layer) and (status is None or s.status == status)
        ]

    def create(self, skill: Skill, actor: Actor = Actor.HUMAN) -> Skill:
        with self.batch(f"create {skill.id}"):
            if self.get(skill.id) is not None:
                raise ValueError(f"skill {skill.id} exists; use patch()")
            skill = skill.model_copy(update={"version": 1, "parent_version": None})
            if skill.status is SkillStatus.ACTIVE:
                self._set_changed(f"create {skill.id}")
            self._insert(skill)
            self._event(skill.id, 1, "create", actor, {"layer": skill.layer.value,
                                                      "status": skill.status.value})
        return skill

    def patch(self, skill_id: str, changes: dict[str, Any], actor: Actor = Actor.HUMAN) -> Skill:
        """New version with `changes` merged field by field; the old row is kept and retired."""
        forbidden = {"id", "version", "parent_version", "hits", "wins", "losses"} & changes.keys()
        if forbidden:
            raise ValueError(f"cannot patch {sorted(forbidden)}")
        with self.batch(f"patch {skill_id}"):
            old = self._latest(skill_id)
            data = old.model_dump() | changes | {
                "version": old.version + 1, "parent_version": old.version,
                "hits": 0, "wins": 0, "losses": 0,
            }
            new = Skill.model_validate(data)
            if SkillStatus.ACTIVE in (old.status, new.status):
                self._set_changed(f"patch {skill_id}")
            if old.status is SkillStatus.ACTIVE:
                self._db.execute(
                    "UPDATE skills SET status=? WHERE id=? AND version=?",
                    (SkillStatus.RETIRED.value, skill_id, old.version),
                )
            self._insert(new)
            self._event(skill_id, new.version, "patch", actor, {"fields": sorted(changes)})
        return new

    def _set_status(self, skill_id, status: SkillStatus, actor, event, extra=None) -> Skill:
        with self.batch(f"{event} {skill_id}"):
            s = self._latest(skill_id)
            if SkillStatus.ACTIVE in (s.status, status) and s.status is not status:
                self._set_changed(f"{event} {skill_id}")
            self._db.execute(
                "UPDATE skills SET status=?, absorbed_into=COALESCE(?, absorbed_into)"
                " WHERE id=? AND version=?",
                (status.value, (extra or {}).get("absorbed_into"), skill_id, s.version),
            )
            self._event(skill_id, s.version, event, actor, extra)
        return self._latest(skill_id)

    def activate(self, skill_id: str, actor: Actor = Actor.HUMAN) -> Skill:
        return self._set_status(skill_id, SkillStatus.ACTIVE, actor, "activate")

    def retire(self, skill_id: str, actor: Actor = Actor.HUMAN,
               absorbed_into: str | None = None) -> Skill:
        extra = {"absorbed_into": absorbed_into} if absorbed_into else None
        return self._set_status(skill_id, SkillStatus.RETIRED, actor, "retire", extra)

    def archive(self, skill_id: str, actor: Actor = Actor.HUMAN) -> Skill:
        """Never delete: archived rows stay for audit and restore."""
        return self._set_status(skill_id, SkillStatus.ARCHIVED, actor, "archive")

    def record_hit(self, skill_id: str, version: int, outcome: HitOutcome,
                   form_id: str | None = None) -> None:
        """Usage counter; allowed while pinned (it does not change the active set)."""
        outcome = HitOutcome(outcome)
        col = {"win": "wins", "loss": "losses", "neutral": None}[outcome.value]
        with self.batch("hit"):
            if self.get(skill_id, version) is None:
                raise SkillNotFound(f"{skill_id}@{version}")
            self._db.execute(
                "INSERT INTO skill_hits VALUES (?,?,?,?,?)",
                (skill_id, version, form_id, outcome.value, _iso(_now())),
            )
            extra = f", {col}={col}+1" if col else ""
            self._db.execute(
                f"UPDATE skills SET hits=hits+1{extra} WHERE id=? AND version=?",
                (skill_id, version),
            )

    # ---- skillset versions ----

    def _manifest_row(self, version: int) -> sqlite3.Row | None:
        return self._db.execute(
            "SELECT * FROM skillset_versions WHERE version=?", (version,)
        ).fetchone()

    def _write_skillset(self, reason: str) -> int:
        refs = [
            {"id": r["id"], "version": r["version"]}
            for r in self._db.execute(
                "SELECT id, version FROM skills WHERE status=? ORDER BY id",
                (SkillStatus.ACTIVE.value,),
            )
        ]
        v = (self._db.execute("SELECT MAX(version) FROM skillset_versions").fetchone()[0] or 0) + 1
        self._db.execute(
            "INSERT INTO skillset_versions VALUES (?,?,?,?)",
            (v, _iso(_now()), json.dumps(refs), reason[:500]),
        )
        self._db.execute("INSERT OR REPLACE INTO meta VALUES ('current', ?)", (str(v),))
        return v

    def current_version(self) -> int | None:
        """The skillset version in effect: the pin if set, else the `current` pointer."""
        if self.snapshot is not None:
            return self.snapshot
        with self._lock:
            r = self._db.execute("SELECT value FROM meta WHERE key='current'").fetchone()
        return int(r[0]) if r else None

    def skillset(self, version: int) -> SkillsetVersion:
        with self._lock:
            r = self._manifest_row(version)
        if r is None:
            raise KeyError(version)
        return SkillsetVersion(
            version=r["version"], created_at=r["created_at"],
            manifest=[SkillRef(**x) for x in json.loads(r["manifest_json"])], reason=r["reason"],
        )

    def skillsets(self) -> list[SkillsetVersion]:
        with self._lock:
            vs = [r[0] for r in self._db.execute("SELECT version FROM skillset_versions ORDER BY 1")]
        return [self.skillset(v) for v in vs]

    def active(self) -> list[Skill]:
        """The active skills of the version in effect, ordered by id."""
        v = self.current_version()
        if v is None:
            return []
        out = [self.get(ref.id, ref.version) for ref in self.skillset(v).manifest]
        return [s for s in out if s is not None]

    def rollback(self, version: int, actor: Actor = Actor.HUMAN) -> int:
        """Restore skillset `version`: its rows become active, all others stop being active.

        Writes a new skillset version (history is never rewritten) and returns its number."""
        with self.batch(f"rollback to {version}"):
            target = {(r.id, r.version) for r in self.skillset(version).manifest}
            self._set_changed(f"rollback to {version}")
            for r in self._db.execute("SELECT id, version, status FROM skills").fetchall():
                key = (r["id"], r["version"])
                if key in target and r["status"] != SkillStatus.ACTIVE.value:
                    self._db.execute("UPDATE skills SET status='active' WHERE id=? AND version=?", key)
                elif key not in target and r["status"] == SkillStatus.ACTIVE.value:
                    self._db.execute("UPDATE skills SET status='retired' WHERE id=? AND version=?", key)
            self._event(None, None, "rollback", actor, {"to_skillset": version})
        return self.current_version()  # type: ignore[return-value]

    # ---- episodes ----

    def add_episode(self, ep: Episode) -> Episode:
        ep = ep.model_copy(update={"id": ep.id or f"ep_{uuid.uuid4().hex[:12]}",
                                   "created_at": ep.created_at or _now()})
        assert_clean(ep.model_dump(mode="json"))
        with self.batch("episode"):
            self._db.execute(
                "INSERT INTO episodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ep.id, _iso(ep.created_at), ep.kind.value, ep.form_id, ep.field_path,
                    ep.field_label, ep.validator, ep.error, ep.before_shape, ep.after_shape,
                    ep.edited_attr, ep.tier_from, ep.tier_to, json.dumps(ep.skills_used),
                    json.dumps(ep.flag) if ep.flag is not None else None, ep.status.value,
                    int(ep.consolidated),
                ),
            )
        return ep

    def episodes(self, status: EpisodeStatus | None = None) -> list[Episode]:
        q, args = "SELECT * FROM episodes", ()
        if status is not None:
            q, args = q + " WHERE status=?", (EpisodeStatus(status).value,)
        with self._lock:
            rows = self._db.execute(q + " ORDER BY created_at, id", args).fetchall()
        return [
            Episode(
                id=r["id"], created_at=r["created_at"], kind=r["kind"], form_id=r["form_id"],
                field_path=r["field_path"], field_label=r["field_label"],
                validator=r["validator"], error=r["error"], before_shape=r["before_shape"],
                after_shape=r["after_shape"], edited_attr=r["edited_attr"],
                tier_from=r["tier_from"], tier_to=r["tier_to"],
                skills_used=json.loads(r["skills_used"]),
                flag=json.loads(r["flag_json"]) if r["flag_json"] else None,
                status=r["status"], consolidated=bool(r["consolidated"]),
            )
            for r in rows
        ]

    # ---- audit ----

    def events(self, skill_id: str | None = None) -> list[SkillEvent]:
        q, args = "SELECT * FROM skill_events", ()
        if skill_id is not None:
            q, args = q + " WHERE skill_id=?", (skill_id,)
        with self._lock:
            rows = self._db.execute(q + " ORDER BY seq", args).fetchall()
        return [
            SkillEvent(ts=r["ts"], skill_id=r["skill_id"], version=r["version"],
                       event=r["event"], actor=r["actor"], detail=json.loads(r["detail_json"]))
            for r in rows
        ]
