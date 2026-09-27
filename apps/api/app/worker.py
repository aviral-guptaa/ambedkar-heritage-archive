"""Worker process.

Run with ``python -m app.worker``. The worker takes jobs from whichever queue
provider is configured (Redis when available, otherwise the database) and runs
them through :mod:`app.services.tasks`. It is safe to run several against one
database: jobs are claimed with ``SELECT ... FOR UPDATE SKIP LOCKED``.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from types import FrameType

from app.core.config import settings
from app.core.logging import get_logger, configure_logging
from app.db.base import session_scope
from app.providers.jobs import get_queue
from app.services.tasks import claim_next_job, retry_stuck_jobs, run_job

log = get_logger(__name__)

_stop = False

# A worker and the web service start at the same time on Render, and the web
# service is the one that runs migrations. The worker will therefore usually
# lose a race it did not enter, and the schema it needs is not there yet.
#
# Exiting would be survivable — the platform restarts a crashed process — but it
# turns a normal cold start into a crash loop, and the backoff means the worker
# can still be waiting when the deploy has already been declared healthy. Waiting
# a bounded time and saying so is the honest version: if the schema never turns
# up, the wait ends and the original error is raised rather than swallowed.
_SCHEMA_WAIT_SECONDS = 120.0
_SCHEMA_POLL_SECONDS = 2.0


def _recover_stuck_jobs() -> int:
    """Requeue interrupted jobs, waiting for the schema if it is not there yet."""
    deadline = time.monotonic() + _SCHEMA_WAIT_SECONDS
    announced = False
    last_error: Exception | None = None
    while True:
        try:
            with session_scope() as db:
                return retry_stuck_jobs(db)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        if time.monotonic() >= deadline:
            log.error(
                "schema did not appear before the wait expired",
                waited_seconds=_SCHEMA_WAIT_SECONDS,
                error=str(last_error),
            )
            raise last_error  # type: ignore[misc]
        if not announced:
            log.info(
                "waiting for the database schema before claiming jobs",
                timeout_seconds=_SCHEMA_WAIT_SECONDS,
            )
            announced = True
        time.sleep(_SCHEMA_POLL_SECONDS)


def _handle_signal(signum: int, _frame: FrameType | None) -> None:  # noqa: ANN001
    global _stop  # noqa: PLW0603
    _stop = True
    log.info("stop requested", signal=signum)


def main() -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Digital Heritage Archive worker")
    parser.add_argument("--once", action="store_true", help="Process one job and exit.")
    parser.add_argument(
        "--poll-interval", type=float, default=2.0, help="Seconds between empty polls."
    )
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    queue = get_queue()
    recovered = _recover_stuck_jobs()
    log.info(
        "worker started",
        queue=queue.name,
        environment=settings.environment,
        requeued_after_crash=recovered,
    )

    processed = 0
    while not _stop:
        job_id: str | None = None
        if queue.is_available() and queue.name != "inline":
            job_id = queue.dequeue(timeout=int(args.poll_interval))
        if job_id is None:
            with session_scope() as db:
                job = claim_next_job(db)
                job_id = job.id if job is not None else None
        if job_id is None:
            if args.once:
                break
            time.sleep(args.poll_interval)
            continue
        try:
            outcome = run_job(job_id)
        except Exception as exc:  # noqa: BLE001
            log.error("worker crashed on job", job_id=job_id, error=str(exc))
            outcome = {"state": "CRASHED", "error": str(exc)}
        processed += 1
        log.info("job processed", job_id=job_id, state=outcome.get("state"))
        if args.once:
            break

    log.info("worker stopped", jobs_processed=processed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
