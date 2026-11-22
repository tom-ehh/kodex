"""When to sleep: cron (`COI_SLEEP_CRON`), early on N pending pattern episodes, or manual.

Never while `COI_SKILLS_SNAPSHOT` pins the active set; never while another cycle holds the
lock. The idle check of sleep.md (no extraction in flight) is left out in v1.
"""

from dataclasses import dataclass
from datetime import datetime

from kodex.morpheus.sleep import state
from kodex.morpheus.store import SkillStore

_RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]  # min hour dom month dow (0 or 7=Sunday)


class CronError(ValueError):
    pass


def _field(spec: str, lo: int, hi: int) -> frozenset[int]:
    out: set[int] = set()
    for part in spec.split(","):
        base, _, step_s = part.partition("/")
        step = int(step_s) if step_s else 1
        if step <= 0:
            raise CronError(f"bad step in {part!r}")
        if base == "*":
            a, b = lo, hi
        elif "-" in base:
            a, b = (int(x) for x in base.split("-", 1))
        else:
            a = int(base)
            b = hi if step_s else a
        if a < lo or b > hi or a > b:
            raise CronError(f"{part!r} outside {lo}-{hi}")
        out.update(range(a, b + 1, step))
    return frozenset(out)


@dataclass(frozen=True)
class Cron:
    minute: frozenset[int]
    hour: frozenset[int]
    dom: frozenset[int]
    month: frozenset[int]
    dow: frozenset[int]
    dom_any: bool
    dow_any: bool

    @classmethod
    def parse(cls, text: str) -> Cron:
        parts = text.split()
        if len(parts) != 5:
            raise CronError(f"cron needs 5 fields, got {text!r}")
        try:
            f = [_field(p, lo, hi) for p, (lo, hi) in zip(parts, _RANGES, strict=True)]
        except ValueError as exc:
            raise CronError(str(exc)) from exc
        f[4] = frozenset(d % 7 for d in f[4])
        return cls(*f, dom_any=parts[2] == "*", dow_any=parts[4] == "*")

    def matches(self, t: datetime) -> bool:
        if t.minute not in self.minute or t.hour not in self.hour or t.month not in self.month:
            return False
        dom_ok = t.day in self.dom
        dow_ok = (t.isoweekday() % 7) in self.dow
        if self.dom_any or self.dow_any:  # classic cron: OR only when both are restricted
            return dom_ok and dow_ok
        return dom_ok or dow_ok


@dataclass(frozen=True)
class Decision:
    go: bool
    reason: str


def should_sleep(store: SkillStore, settings, now: datetime, manual: bool = False,
                 cron: Cron | None = None, last_cron_minute: datetime | None = None,
                 last_pattern_count: int | None = None) -> Decision:
    """Pinned and lock checks first (they refuse even a manual run), then the triggers."""
    if settings.skills_snapshot is not None or store.snapshot is not None:
        return Decision(False, "skipped: pinned")
    if state.locked(store):
        return Decision(False, "skipped: locked")
    if manual:
        return Decision(True, "manual")
    minute = now.replace(second=0, microsecond=0)
    if cron is not None and cron.matches(now) and last_cron_minute != minute:
        return Decision(True, "cron")
    n = state.pending_pattern_episodes(store)
    # Fire again only when new pattern episodes arrived since the last early run.
    if n >= settings.sleep_min_patterns and n != last_pattern_count:
        return Decision(True, f"patterns:{n}")
    return Decision(False, "idle")
