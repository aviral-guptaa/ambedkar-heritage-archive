"""The queue hand-off between the API and the worker.

The worker used to crash on startup, and then crash again every time the queue
ran dry, because nothing had ever run it. Those bugs were invisible to the rest
of the suite: a code path only exercised by hand is a code path that does not
work. These tests run the real providers against a real Redis and a real
database, and finish a job end to end.

Both backends are covered because both ship. ``RedisJobQueue`` is the production
path; ``DatabaseJobQueue`` is the offline fallback that keeps the archive usable
with no extra service.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager

import pytest


@pytest.fixture(params=["redis", "database"])
def queue(request, migrated_database):
    """A real queue, for whichever backends are actually reachable.

    ``migrated_database`` is requested first and deliberately. Importing
    ``app.providers.jobs`` builds the settings object, and settings are read at
    import time — so if this fixture ran before the harness had chosen a
    throwaway database, the suite would quietly use the developer's real one.
    Depending on it here makes the ordering explicit rather than accidental:
    without it, this file passed in a full run (an earlier test had already
    imported the application correctly) and failed when run on its own.
    """
    from app.providers.jobs import DatabaseJobQueue, RedisJobQueue

    provider = RedisJobQueue() if request.param == "redis" else DatabaseJobQueue()
    if not provider.is_available():
        pytest.skip(f"{request.param} backend is not reachable")
    return provider


def _make_job(queue) -> str:
    """A queued job id.

    The database backend enqueues by clearing a job row's state, so the row has
    to exist first; the Redis backend only carries the id.
    """
    job_id = uuid.uuid4().hex
    if queue.name != "database":
        return job_id
    from app.db.base import session_scope
    from app.models.enums import JobKind, JobState
    from app.models.ops import ProcessingJob

    # The database backend's enqueue is a no-op by design: the row is the queue
    # entry, so it is created already QUEUED.
    with session_scope() as db:
        db.add(ProcessingJob(id=job_id, kind=JobKind.FINALISE, state=JobState.QUEUED))
    return job_id


def _drain(queue) -> list[str]:
    """Empty the queue, returning the job ids it held, in the order given."""
    drained: list[str] = []
    while True:
        job_id = queue.dequeue(timeout=1)
        if job_id is None:
            return drained
        drained.append(job_id)


def test_enqueue_then_dequeue_returns_the_same_job_id(queue):
    _drain(queue)  # ignore anything left over from development
    job_id = _make_job(queue)
    queue.enqueue(job_id)
    try:
        assert queue.dequeue(timeout=5) == job_id
    finally:
        _drain(queue)


def test_an_idle_queue_returns_none_instead_of_raising(queue):
    """An empty poll is the common case; it must not crash the worker."""
    _drain(queue)
    assert queue.dequeue(timeout=1) is None
    assert queue.dequeue(timeout=1) is None


def test_jobs_come_back_in_the_order_they_were_queued(queue):
    _drain(queue)
    ids = [_make_job(queue) for _ in range(3)]
    try:
        for job_id in ids:
            queue.enqueue(job_id)
        assert _drain(queue) == ids
    finally:
        _drain(queue)


def test_queue_depth_reflects_pending_work(queue):
    _drain(queue)
    # The database backend counts every unfinished job row, including rows left
    # by an earlier run, so the change in depth is what is asserted.
    before = queue.queue_depth()
    job_id = _make_job(queue)
    queue.enqueue(job_id)
    try:
        assert queue.queue_depth() == before + 1
    finally:
        _drain(queue)
    assert queue.queue_depth() == before


def test_worker_claims_a_queued_job_and_runs_it(queue, seeded):
    """The whole path: a job in the database, taken by the worker, completed.

    This is the test that would have caught the startup crash.
    """
    from app.db.base import session_scope
    from app.models.archive import Document
    from app.models.enums import JobKind, JobState
    from app.models.ops import ProcessingJob
    from app.services.tasks import run_job

    _drain(queue)
    with session_scope() as db:
        document = db.query(Document).filter(Document.slug == "caste-in-india-1916").one()
        job = ProcessingJob(
            id=str(uuid.uuid4()),
            kind=JobKind.FINALISE,
            document_id=document.id,
            state=JobState.QUEUED,
        )
        db.add(job)
        job_id = job.id

    queue.enqueue(job_id)
    try:
        assert queue.dequeue(timeout=5) == job_id
        outcome = run_job(job_id)
        assert outcome["state"] == JobState.COMPLETED, outcome
    finally:
        with session_scope() as db:
            db.query(ProcessingJob).filter(ProcessingJob.id == job_id).delete()
        _drain(queue)


def test_worker_waits_for_a_schema_that_is_not_there_yet(migrated_database, monkeypatch):
    """A worker that starts before the web service has migrated waits.

    On Render the two start together and only the web service runs migrations, so
    the worker reliably loses a race it did not enter. Before this, the worker's
    first query raised, the process exited, and the platform restarted it into a
    backoff that can outlast the deploy. It should wait, and it should say so.
    """
    from app.worker import _recover_stuck_jobs

    attempts: list[int] = []
    waited: list[float] = []

    def slow_schema(*_args, **_kwargs):
        """Fail like a missing table would, then succeed."""
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError('relation "processing_job" does not exist')
        return 4

    monkeypatch.setattr("app.worker.time.sleep", waited.append)
    monkeypatch.setattr("app.worker.time.monotonic", lambda: 0.0)
    monkeypatch.setattr("app.worker.retry_stuck_jobs", slow_schema)
    monkeypatch.setattr("app.worker.session_scope", _null_session)

    assert _recover_stuck_jobs() == 4
    assert len(attempts) == 3, "it should have retried, not given up"
    assert waited, "it should have paused between attempts instead of spinning"


def test_worker_gives_up_rather_than_waiting_forever(migrated_database, monkeypatch):
    """A schema that never arrives must surface, not be swallowed by a retry loop."""
    from app.worker import _recover_stuck_jobs

    def missing_schema(*_args, **_kwargs):
        raise RuntimeError('relation "processing_job" does not exist')

    # A clock that jumps past the deadline, so the test does not really wait.
    ticks = iter([0.0, 0.0, 10_000.0, 10_000.0, 10_000.0, 10_000.0])
    monkeypatch.setattr("app.worker.time.monotonic", lambda: next(ticks, 10_000.0))
    monkeypatch.setattr("app.worker.time.sleep", lambda _s: None)
    monkeypatch.setattr("app.worker.retry_stuck_jobs", missing_schema)
    monkeypatch.setattr("app.worker.session_scope", _null_session)

    with pytest.raises(RuntimeError, match="processing_job"):
        _recover_stuck_jobs()


@contextmanager
def _null_session():
    """A session_scope stand-in: ``retry_stuck_jobs`` is stubbed, so the body is empty."""
    yield None
