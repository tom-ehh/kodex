"""Sleep HTTP endpoints (design.md §5) and the in-process scheduler.

`POST /api/v1/morpheus/sleep`, `POST /api/v1/morpheus/rollback/{version}`,
`GET /api/v1/morpheus/metrics`. The review endpoints belong to P2.
"""

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from coi_formularze.api.deps import ChatDep, MorpheusDep, SettingsDep
from coi_formularze.auth import require_auth
from coi_formularze.morpheus.models import Actor
from coi_formularze.morpheus.sleep import state
from coi_formularze.morpheus.sleep.adapters import (
    AdapterError,
    BasalJudge,
    GateReplayer,
    LlmGeneralizer,
    UnavailableReplayer,
)
from coi_formularze.morpheus.sleep.cycle import SleepConfig, SleepDeps, run_cycle
from coi_formularze.morpheus.sleep.trigger import Cron, CronError, should_sleep
from coi_formularze.morpheus.store import PinnedError, SkillStore

log = logging.getLogger(__name__)
TICK_S = 30.0


class _NoGeneralizer:
    async def generalize(self, cluster, neighbours, errors=None):
        raise AdapterError("no LLM configured (COI_EDENAI_API_KEY)")


def default_deps(settings, chat) -> SleepDeps:
    """basal at COI_ROUTER_URL; generalize with COI_MORPHEUS_MODEL (default hard tier);
    replay the gate forms with COI_MORPHEUS_REPLAY_MODEL (default medium tier) when the
    gate markdown (COI_MORPHEUS_GATE_MD_DIR) and an LLM key exist, else no gate (no merge)."""
    judge = BasalJudge(settings.router_url, timeout=max(settings.router_timeout_s, 30.0))
    gen_model = settings.morpheus_model or settings.tier_hard_model
    replay_model = settings.morpheus_replay_model or settings.tier_medium_model
    gen = LlmGeneralizer(chat, gen_model) if chat is not None and gen_model else _NoGeneralizer()
    if chat is not None and replay_model and settings.morpheus_gate_md_dir:
        replayer = GateReplayer(chat, replay_model, settings.morpheus_gate_md_dir,
                                settings.skills_max_per_section)
    else:
        replayer = UnavailableReplayer()
    return SleepDeps(judge=judge, generalizer=gen, replayer=replayer, checker=judge)


def get_sleep_deps(request: Request, settings: SettingsDep, chat: ChatDep) -> SleepDeps:
    deps = getattr(request.app.state, "sleep_deps", None)
    if deps is None:
        deps = request.app.state.sleep_deps = default_deps(settings, chat)
    return deps


SleepDepsDep = Annotated[SleepDeps, Depends(get_sleep_deps)]

router = APIRouter(prefix="/api/v1/morpheus", tags=["morpheus"],
                   dependencies=[Depends(require_auth)])


def _need(store: SkillStore | None) -> SkillStore:
    if store is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Morpheus is disabled.")
    return store


def _background(app, coro) -> None:
    tasks = getattr(app.state, "sleep_tasks", None)
    if tasks is None:
        tasks = app.state.sleep_tasks = set()
    t = asyncio.get_running_loop().create_task(coro)
    tasks.add(t)
    t.add_done_callback(tasks.discard)


@router.post("/sleep", summary="Run a sleep cycle now", status_code=status.HTTP_202_ACCEPTED,
             responses={200: {"description": "Cycle finished (`wait=true`)."},
                        409: {"description": "Pinned (`COI_SKILLS_SNAPSHOT`) or a cycle is running."},
                        503: {"description": "Morpheus is disabled."}})
async def post_sleep(request: Request, settings: SettingsDep, morpheus: MorpheusDep,
                     deps: SleepDepsDep,
                     wait: Annotated[bool, Query(description="Wait and return the summary.")] = False
                     ) -> dict[str, Any]:
    """Manual sleep ("learn now" for the whole queue). Skips nothing: the replay gate always
    runs. Refused while the skill set is pinned or another cycle holds the lock."""
    store = _need(morpheus)
    d = should_sleep(store, settings, datetime.now(UTC), manual=True)
    if not d.go:
        raise HTTPException(status.HTTP_409_CONFLICT, d.reason)
    cfg = SleepConfig.from_settings(settings)
    if wait:
        out = await run_cycle(store, deps, cfg, trigger="manual")
        request.app.state.sleep_last = out
        return out
    _background(request.app, run_cycle(store, deps, cfg, trigger="manual"))
    return {"status": "started", "trigger": "manual"}


@router.post("/rollback/{version}", summary="Restore a skillset version",
             responses={404: {"description": "No such version."},
                        409: {"description": "Pinned."}, 503: {"description": "Disabled."}})
def post_rollback(version: int, morpheus: MorpheusDep) -> dict[str, int]:
    """Make skillset `version` active again; writes a new version (history is kept)."""
    store = _need(morpheus)
    try:
        new = store.rollback(version, Actor.HUMAN)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No skillset version {version}.") from exc
    except PinnedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {"restored": version, "version": new}


@router.get("/metrics", summary="Sleep cycle metrics",
            responses={503: {"description": "Morpheus is disabled."}})
def get_metrics(morpheus: MorpheusDep) -> dict[str, Any]:
    """One row per sleep cycle (acc_gate before/after, acc_heldout, skill_count, cost, ...)
    plus the skillset version in effect, for the pitch chart."""
    store = _need(morpheus)
    return {"current_version": store.current_version(), "cycles": state.cycles(store)}


# ---------------------------------------------------------------- scheduler


class SleepScheduler:
    """Checks every `tick` seconds: cron minute, else pending pattern episodes."""

    def __init__(self, app, settings, tick: float = TICK_S):
        self.app, self.settings, self.tick = app, settings, tick
        self.cron = None
        if settings.sleep_cron.strip():
            try:
                self.cron = Cron.parse(settings.sleep_cron)
            except CronError as exc:
                log.warning("COI_SLEEP_CRON ignored: %s", exc)
        self.last_cron_minute = None
        self.last_patterns: int | None = None

    def _store(self) -> SkillStore | None:
        from coi_formularze.morpheus import open_store
        store = getattr(self.app.state, "morpheus", None)
        if store is None:
            store = self.app.state.morpheus = open_store(self.settings)
        return store

    async def step(self, now: datetime) -> dict | None:
        store = self._store()
        if store is None:
            return None
        d = should_sleep(store, self.settings, now, cron=self.cron,
                         last_cron_minute=self.last_cron_minute,
                         last_pattern_count=self.last_patterns)
        if not d.go:
            return None
        if d.reason == "cron":
            self.last_cron_minute = now.replace(second=0, microsecond=0)
        else:
            self.last_patterns = state.pending_pattern_episodes(store)
        deps = getattr(self.app.state, "sleep_deps", None)
        if deps is None:
            deps = self.app.state.sleep_deps = default_deps(self.settings,
                                                            getattr(self.app.state, "chat", None))
        return await run_cycle(store, deps, SleepConfig.from_settings(self.settings),
                               trigger=d.reason)

    async def loop(self) -> None:
        while True:
            try:
                await self.step(datetime.now(UTC))
            except Exception:  # never kill the API process over a sleep cycle
                log.exception("sleep scheduler step failed")
            await asyncio.sleep(self.tick)


def start_scheduler(app, settings) -> asyncio.Task | None:
    if not settings.morpheus_enabled or settings.skills_snapshot is not None:
        return None
    return asyncio.get_running_loop().create_task(SleepScheduler(app, settings).loop())


async def stop_scheduler(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
