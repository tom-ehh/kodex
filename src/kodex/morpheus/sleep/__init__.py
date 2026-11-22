"""Morpheus sleep (P3): consolidate episodic skills into general ones, gated by replay.

Entry points: `run_cycle` (one cycle), `should_sleep` (trigger logic), `SleepScheduler`
(asyncio cron/patterns loop in the API process), `router` (HTTP endpoints).
"""

from kodex.morpheus.sleep.adapters import (
    GATE_FORMS,
    HELDOUT_FORMS,
    AdapterError,
    Draft,
    ReplayResult,
)
from kodex.morpheus.sleep.cycle import (
    SleepConfig,
    SleepDeps,
    gate_pass,
    run_cycle,
)
from kodex.morpheus.sleep.trigger import (
    Cron,
    CronError,
    Decision,
    should_sleep,
)

__all__ = [
    "GATE_FORMS", "HELDOUT_FORMS", "AdapterError", "Cron", "CronError", "Decision", "Draft",
    "ReplayResult", "SleepConfig", "SleepDeps", "gate_pass", "run_cycle", "should_sleep",
]
