"""Background job queue.

Two interchangeable backends:

* ``rq``       — Redis Queue, used when REDIS_URL is reachable (Docker stack).
* ``database`` — a durable SQL queue in ``processing_jobs``. Workers poll with
  ``FOR UPDATE SKIP LOCKED``. It survives restarts, is fully inspectable in the
  admin UI, and needs no extra service, which keeps the offline edge
  deployment viable.

Both expose the same contract, and ``processing_jobs`` is always the system of
record for status regardless of backend, so the admin dashboard never shows a
state the database does not hold.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

from sqlalchemy import func, select, text

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.models.ops import ProcessingJob
from app.providers.base import JobQueue, ProviderUnavailable

log = get_logger(__name__)

QUEUE_KEY = "dha:jobs"
POLL_INTERVAL_SECONDS = 1.0
JOB_LEASE_SECONDS = 900


class RedisJobQueue:
    name = "rq"

    def __init__(self) -> None:
        self._conn = None
        self._queue = None
        self._error: str | None = None

    def _ensure(self):  # noqa: ANN202
        if self._queue is not None:
            return self._queue
        if self._error:
            raise ProviderUnavailable(self._error)
        try:
            import redis
            from rq import Queue

            conn = redis.Redis.from_url(settings.redis_url, socket_timeout=3)
            conn.ping()
            self._conn = conn
            self._queue = Queue(QUEUE_KEY, connection=conn, default_timeout=JOB_LEASE_SECONDS)
            return self._queue
        except Exception as exc:  # noqa: BLE001
            self._error = f"Redis unavailable at {settings.redis_url}: {exc}"
            raise ProviderUnavailable(self._error) from exc

    def is_available(self) -> bool:
        try:
            import redis

            client = redis.Redis.from_url(settings.redis_url, socket_timeout=2)
            client.ping()
            return True
        except Exception:  # noqa: BLE001
            return False

    def enqueue(self, job_id: str) -> None:
        queue = self._ensure()
        queue.enqueue("app.workers.tasks.run_job", job_id, job_timeout=JOB_LEASE_SECONDS)

    def dequeue(self, timeout: int = 5) -> str | None:
        """Take one job id off the queue, or return None if none arrived.

        Redis is only used as a durable hand-off point: the worker runs the job
        itself through :func:`app.services.tasks.run_job`, so that the
        ``processing_jobs`` table stays the system of record for status. rq's
        ``dequeue_any`` wants a list of Queue objects and returns a
        ``(job, queue)`` pair, so both are handled explicitly here.
        """
        queue = self._ensure()
        from rq.exceptions import DequeueTimeout

        try:
            result = queue.dequeue_any([queue], timeout=timeout, connection=self._conn)
        except DequeueTimeout:
            # An idle poll is the normal case, not an error. rq raises rather
            # than returning None, and the worker must be able to idle instead of
            # crashing the moment the queue drains.
            return None
        if result is None:
            return None
        rq_job, _source = result
        args = rq_job.args or ()
        return str(args[0]) if args else None

    def queue_depth(self) -> int:
        try:
            return int(self._ensure().count)
        except ProviderUnavailable:
            return 0

    def workers_alive(self) -> int:
        try:
            queue = self._ensure()
            return len(queue.workers)
        except Exception:  # noqa: BLE001
            return 0


class DatabaseJobQueue:
    """SQL-backed durable queue. No external service required."""

    name = "database"

    def is_available(self) -> bool:
        return True

    def enqueue(self, job_id: str) -> None:
        # The row already exists with state QUEUED; this hook exists so the two
        # backends are interchangeable at the call site.
        return None

    def dequeue(self, timeout: int = 5) -> str | None:
        import time

        deadline = time.monotonic() + max(0, timeout)
        while True:
            db = SessionLocal()
            try:
                job = db.scalar(
                    select(ProcessingJob)
                    .where(ProcessingJob.state == "QUEUED")
                    .order_by(ProcessingJob.created_at)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                if job is not None:
                    job.state = "PROCESSING"  # type: ignore[assignment]
                    job.started_at = datetime.now(UTC)
                    job.heartbeat_at = datetime.now(UTC)
                    job.attempts = (job.attempts or 0) + 1
                    job_id = job.id
                    db.commit()
                    return job_id
            except Exception:  # noqa: BLE001
                db.rollback()
                raise
            finally:
                db.close()
            if time.monotonic() >= deadline:
                return None
            time.sleep(POLL_INTERVAL_SECONDS)

    def queue_depth(self) -> int:
        db = SessionLocal()
        try:
            return int(
                db.scalar(
                    select(func.count())
                    .select_from(ProcessingJob)
                    .where(ProcessingJob.state.in_(["QUEUED", "RETRYING"]))
                )
                or 0
            )
        finally:
            db.close()

    def workers_alive(self) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=JOB_LEASE_SECONDS)
        db = SessionLocal()
        try:
            return int(
                db.scalar(
                    select(func.count(func.distinct(ProcessingJob.created_by)))
                    .where(
                        ProcessingJob.state == "PROCESSING",
                        ProcessingJob.heartbeat_at >= cutoff,
                    )
                )
                or 0
            )
        except Exception:  # noqa: BLE001  # pragma: no cover
            return 0
        finally:
            db.close()


class InlineJobQueue:
    """Executes the job immediately in-process.

    Used by the CLI and the test-suite so ingestion can be verified without
    running a worker, and selected as the real queue backend by
    ``QUEUE_BACKEND=inline`` — which is what the free Render deployment runs,
    because free Render has no background worker to run one. The job record
    still records the real timings.

    The cost is that the work happens inside whichever request enqueued it, so
    an upload that triggers OCR holds that request open, and a process killed
    mid-job leaves the job stuck with nothing to recover it.
    """

    name = "inline"

    def __init__(self) -> None:
        # app.services.tasks, not app.workers.tasks: there is no app/workers
        # package, and nothing constructed this class before it became the queue
        # backend for a deployment with no worker to run one.
        from app.services.tasks import run_job

        self._run = run_job

    def is_available(self) -> bool:
        return True

    def enqueue(self, job_id: str) -> None:
        self._run(job_id)

    def dequeue(self, timeout: int = 5) -> str | None:
        return None

    def queue_depth(self) -> int:
        return 0

    def workers_alive(self) -> int:
        return 0


@lru_cache(maxsize=1)
def get_queue() -> JobQueue:
    if settings.queue_backend == "inline":
        # No worker, no Redis, no second process. The job runs in the request
        # that enqueued it, which is the only arrangement that works on a
        # platform that only offers free web services. It is a real trade: work
        # that used to be spread out over a background process now happens
        # inside a user-facing request, and a process that dies mid-job leaves
        # that job stuck, because recovering it is the worker's job.
        return InlineJobQueue()
    if settings.queue_backend == "rq":
        queue = RedisJobQueue()
        if queue.is_available():
            return queue
        log.warning("QUEUE_BACKEND=rq but Redis is unreachable; using the database queue")
    if settings.queue_backend == "database":
        return DatabaseJobQueue()
    redis_queue = RedisJobQueue()
    return redis_queue if redis_queue.is_available() else DatabaseJobQueue()


def queue_capability() -> dict[str, Any]:
    queue = get_queue()
    info: dict[str, Any] = {
        "backend": queue.name,
        "available": True,
        "depth": queue.queue_depth(),
        "workers": queue.workers_alive(),
    }
    if queue.name == "database":
        try:
            db = SessionLocal()
            try:
                db.execute(text("SELECT 1"))
            finally:
                db.close()
        except Exception as exc:  # noqa: BLE001  # pragma: no cover
            info["error"] = str(exc)[:160]
    if queue.name == "inline":
        # A count of zero workers is true and alarming at the same time. Say what
        # it means, or a reader concludes the queue is broken.
        info["detail"] = (
            "jobs run inside the API process as they are enqueued; "
            "there is no separate worker to count"
        )
    return info


def reset_queue_cache() -> None:
    """Drop the memoised queue so configuration changes take effect."""
    get_queue.cache_clear()
