"""In-memory job store. Holds validation reports and results only, never file bytes.

Jobs are lost on restart, which fits the "temporary processing only" rule. The
store is bounded: past `capacity`, the oldest job is evicted.
"""

import threading
import uuid
from collections import OrderedDict
from datetime import UTC, datetime

from kodex.api.schemas import (
    FileReportOut,
    ItemStatus,
    Job,
    JobItem,
    JobStatus,
)


class JobStore:
    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()

    def create(self, reports: list[FileReportOut]) -> Job:
        items = [
            JobItem(
                index=i,
                file=r,
                status=ItemStatus.PENDING if r.ok else ItemStatus.REJECTED,
            )
            for i, r in enumerate(reports)
        ]
        accepted = sum(r.ok for r in reports)
        job = Job(
            id=uuid.uuid4().hex,
            created_at=datetime.now(UTC),
            status=JobStatus.PENDING if accepted else JobStatus.DONE,
            total=len(reports),
            accepted=accepted,
            rejected=len(reports) - accepted,
            items=items,
        )
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > self._capacity:
                self._jobs.popitem(last=False)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return list(reversed(self._jobs.values()))  # newest first

    def delete(self, job_id: str) -> bool:
        with self._lock:
            return self._jobs.pop(job_id, None) is not None
