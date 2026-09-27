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
    with session_scope() as db:
        recovered = retry_stuck_jobs(db)
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
