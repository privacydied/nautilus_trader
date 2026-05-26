"""In-memory FIFO job queue with dedup by job_id."""

from __future__ import annotations

import collections

from .models import ConductorJobSpec


class ConductorJobQueue:
    """Simple FIFO queue of ConductorJobSpec with dedup by job_id.

    No Redis, no database, no multiprocessing queue in v0.
    """

    def __init__(self) -> None:
        self._queue: collections.deque[ConductorJobSpec] = collections.deque()
        self._seen: set[str] = set()

    def push(self, job: ConductorJobSpec) -> bool:
        """Push a job onto the queue.

        Returns True if accepted, False if a job with the same job_id
        has already been seen.
        """
        if job.job_id in self._seen:
            return False
        self._seen.add(job.job_id)
        self._queue.append(job)
        return True

    def pop(self) -> ConductorJobSpec | None:
        """Pop and return the next job, or None if empty."""
        try:
            return self._queue.popleft()
        except IndexError:
            return None

    def __len__(self) -> int:
        return len(self._queue)

    def seen_job_ids(self) -> set[str]:
        """Return a copy of the set of seen job IDs."""
        return set(self._seen)