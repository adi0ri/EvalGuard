"""PostgreSQL and Redis acceptance coverage for Phase 4 resilience."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from rq import Queue, SimpleWorker
from rq.job import JobStatus
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

import evalguard.api.routes as api_routes
import evalguard.evaluation.executor as execution_executor
import evalguard.workers.dispatch as execution_dispatcher
import evalguard.workers.jobs as execution_jobs
from evalguard.db.models import (
    CaseExecution,
    Customer,
    ExecutionAttempt,
    ExecutionDispatch,
    ExecutionFaultPlan,
    Experiment,
    GraderResult,
    Order,
    Refund,
    ToolInvocation,
    TraceEvent,
)
from evalguard.evaluation.resilience import FaultMode
from evalguard.workers.dispatch import create_dispatch, reconcile_pending_dispatches
from evalguard.workers.jobs import (
    recover_interrupted_execution,
    recover_stale_executions,
    run_case_execution,
)
from evalguard.workers.queue import (
    EXECUTION_JOB_FUNCTION,
    LEGACY_JOB_TIMEOUT_SECONDS,
    job_id_for_execution,
)

pytestmark = pytest.mark.integration
ExecutionFactory = Callable[..., UUID]
SessionFactory = sessionmaker[Session]


def _prepare_execution(
    execution_factory: ExecutionFactory,
    session_factory: SessionFactory,
    redis_connection: Redis,
    *,
    fault_mode: FaultMode | None = None,
    fail_attempts: int = 1,
    max_attempts: int = 3,
    job_timeout_seconds: int = 30,
    max_tool_steps: int = 8,
    retry_backoff_seconds: int = 0,
) -> UUID:
    execution_id = execution_factory(materialize_state=False, case_key="eligible-refund")
    with session_factory.begin() as session:
        execution = session.get(CaseExecution, execution_id)
        assert execution is not None
        experiment = session.get(Experiment, execution.experiment_id)
        assert experiment is not None
        experiment.max_attempts = max_attempts
        experiment.job_timeout_seconds = job_timeout_seconds
        experiment.max_tool_steps = max_tool_steps
        experiment.retry_backoff_seconds = retry_backoff_seconds
        create_dispatch(session, execution_id, generation=1)
        if fault_mode is not None:
            session.add(
                ExecutionFaultPlan(
                    case_execution_id=execution_id,
                    mode=fault_mode.value,
                    fail_attempts=fail_attempts,
                )
            )

    summary = reconcile_pending_dispatches(
        execution_ids=[execution_id],
        force=True,
        connection=redis_connection,
    )
    assert summary.dispatched == 1
    assert summary.deferred == 0
    return execution_id


def _work_queue(rq_queue: Queue, redis_connection: Redis) -> None:
    worker = SimpleWorker([rq_queue], connection=redis_connection)
    assert worker.work(burst=True, logging_level="CRITICAL", with_scheduler=False) is True


def _attempts(session: Session, execution_id: UUID) -> list[ExecutionAttempt]:
    return list(
        session.scalars(
            select(ExecutionAttempt)
            .where(ExecutionAttempt.case_execution_id == execution_id)
            .order_by(ExecutionAttempt.attempt_number)
        ).all()
    )


def _count(session: Session, model: type[object], execution_id: UUID) -> int:
    value = session.scalar(
        select(func.count()).select_from(model).where(model.case_execution_id == execution_id)
    )
    return int(value or 0)


def test_idempotent_api_submission_rolls_back_every_row_when_dispatch_creation_fails(
    client: TestClient,
    seeded_suite: object,
    session_factory: SessionFactory,
    rq_queue: Queue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del seeded_suite
    real_create_dispatch = api_routes.create_dispatch
    dispatch_calls = 0

    def fail_on_third_dispatch(
        session: Session,
        execution_id: UUID,
        generation: int,
        available_at: datetime | None = None,
    ) -> ExecutionDispatch:
        nonlocal dispatch_calls
        dispatch_calls += 1
        if dispatch_calls == 3:
            raise RuntimeError("synthetic dispatch-row creation failure")
        return real_create_dispatch(session, execution_id, generation, available_at)

    monkeypatch.setattr(api_routes, "create_dispatch", fail_on_third_dispatch)

    with pytest.raises(RuntimeError, match="synthetic dispatch-row creation failure"):
        client.post(
            "/api/v1/experiments",
            json={
                "provider": "deterministic-fake",
                "trials": 1,
                "fault_injection": {
                    "case_key": "address-change-first-blocked-status",
                    "mode": "transient_before_provider",
                    "fail_attempts": 1,
                },
            },
            headers={"Idempotency-Key": "phase4-atomic-dispatch-row-failure"},
        )

    assert dispatch_calls == 3
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Experiment)) == 0
        assert session.scalar(select(func.count()).select_from(CaseExecution)) == 0
        assert session.scalar(select(func.count()).select_from(ExecutionDispatch)) == 0
        assert session.scalar(select(func.count()).select_from(ExecutionFaultPlan)) == 0
    assert rq_queue.job_ids == []


def test_api_rejects_fault_injection_in_production_without_side_effects(
    client: TestClient,
    seeded_suite: object,
    session_factory: SessionFactory,
    rq_queue: Queue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del seeded_suite
    monkeypatch.setattr(
        api_routes,
        "get_settings",
        lambda: SimpleNamespace(environment="production"),
    )

    response = client.post(
        "/api/v1/experiments",
        json={
            "provider": "deterministic-fake",
            "trials": 1,
            "fault_injection": {
                "case_key": "eligible-refund",
                "mode": "transient_before_provider",
                "fail_attempts": 1,
            },
        },
        headers={"Idempotency-Key": "phase4-production-fault-rejected"},
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "Deterministic fault injection is disabled in this environment"
    }
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Experiment)) == 0
        assert session.scalar(select(func.count()).select_from(CaseExecution)) == 0
        assert session.scalar(select(func.count()).select_from(ExecutionDispatch)) == 0
        assert session.scalar(select(func.count()).select_from(ExecutionFaultPlan)) == 0
    assert rq_queue.job_ids == []


def test_durable_outbox_reconciles_matching_duplicate_and_defers_mismatched_collision(
    execution_factory: ExecutionFactory,
    session_factory: SessionFactory,
    redis_connection: Redis,
    rq_queue: Queue,
) -> None:
    matching_execution_id = execution_factory(materialize_state=False)
    with session_factory.begin() as session:
        created = create_dispatch(session, matching_execution_id, generation=1)
        repeated = create_dispatch(session, matching_execution_id, generation=1)
        assert repeated.id == created.id

    first = reconcile_pending_dispatches(
        execution_ids=[matching_execution_id],
        force=True,
        connection=redis_connection,
    )
    assert first.as_dict() == {
        "attempted": 1,
        "dispatched": 1,
        "deferred": 0,
        "transport_failures": 0,
        "stopped_on_transport_failure": False,
    }

    with session_factory.begin() as session:
        dispatch = session.scalar(
            select(ExecutionDispatch).where(
                ExecutionDispatch.case_execution_id == matching_execution_id
            )
        )
        assert dispatch is not None
        dispatch.status = "pending"
        dispatch.dispatched_at = None

    duplicate = reconcile_pending_dispatches(
        execution_ids=[matching_execution_id],
        force=True,
        connection=redis_connection,
    )
    assert duplicate.dispatched == 1
    assert rq_queue.job_ids.count(job_id_for_execution(matching_execution_id, 1)) == 1

    mismatched_execution_id = execution_factory(materialize_state=False)
    with session_factory.begin() as session:
        create_dispatch(session, mismatched_execution_id, generation=1)
    collision_job_id = job_id_for_execution(mismatched_execution_id, 1)
    rq_queue.enqueue(
        EXECUTION_JOB_FUNCTION,
        str(mismatched_execution_id),
        2,
        job_id=collision_job_id,
        unique=True,
    )

    collision = reconcile_pending_dispatches(
        execution_ids=[mismatched_execution_id],
        force=True,
        connection=redis_connection,
    )
    assert collision.attempted == 1
    assert collision.dispatched == 0
    assert collision.deferred == 1
    assert collision.transport_failures == 0
    assert collision.stopped_on_transport_failure is False
    with session_factory() as session:
        dispatch = session.scalar(
            select(ExecutionDispatch).where(
                ExecutionDispatch.case_execution_id == mismatched_execution_id
            )
        )
        assert dispatch is not None
        assert dispatch.status == "pending"
        assert dispatch.attempt_count == 1
        assert dispatch.last_error_kind == "DispatchIdentityError"

    legacy_execution_id = execution_factory(materialize_state=False)
    with session_factory.begin() as session:
        legacy_execution = session.get(CaseExecution, legacy_execution_id)
        assert legacy_execution is not None
        legacy_experiment = session.get(Experiment, legacy_execution.experiment_id)
        assert legacy_experiment is not None
        legacy_experiment.job_timeout_seconds = LEGACY_JOB_TIMEOUT_SECONDS
        create_dispatch(session, legacy_execution_id, generation=1)
    legacy_job_id = job_id_for_execution(legacy_execution_id, 1)
    rq_queue.enqueue(
        EXECUTION_JOB_FUNCTION,
        str(legacy_execution_id),
        job_id=legacy_job_id,
        job_timeout=LEGACY_JOB_TIMEOUT_SECONDS,
        unique=True,
    )
    legacy = reconcile_pending_dispatches(
        execution_ids=[legacy_execution_id],
        force=True,
        connection=redis_connection,
    )
    assert legacy.dispatched == 1

    new_policy_legacy_shape_id = execution_factory(materialize_state=False)
    with session_factory.begin() as session:
        create_dispatch(session, new_policy_legacy_shape_id, generation=1)
    new_policy_legacy_job_id = job_id_for_execution(new_policy_legacy_shape_id, 1)
    rq_queue.enqueue(
        EXECUTION_JOB_FUNCTION,
        str(new_policy_legacy_shape_id),
        job_id=new_policy_legacy_job_id,
        job_timeout=LEGACY_JOB_TIMEOUT_SECONDS,
        unique=True,
    )
    new_policy_legacy = reconcile_pending_dispatches(
        execution_ids=[new_policy_legacy_shape_id],
        force=True,
        connection=redis_connection,
    )
    assert new_policy_legacy.dispatched == 0
    assert new_policy_legacy.deferred == 1
    with session_factory() as session:
        dispatch = session.scalar(
            select(ExecutionDispatch).where(
                ExecutionDispatch.case_execution_id == new_policy_legacy_shape_id
            )
        )
        assert dispatch is not None
        assert dispatch.status == "pending"
        assert dispatch.last_error_kind == "DispatchIdentityError"

    wrong_timeout_execution_id = execution_factory(materialize_state=False)
    with session_factory.begin() as session:
        create_dispatch(session, wrong_timeout_execution_id, generation=1)
    wrong_timeout_job_id = job_id_for_execution(wrong_timeout_execution_id, 1)
    rq_queue.enqueue(
        EXECUTION_JOB_FUNCTION,
        str(wrong_timeout_execution_id),
        1,
        job_id=wrong_timeout_job_id,
        job_timeout=31,
        unique=True,
    )
    wrong_timeout = reconcile_pending_dispatches(
        execution_ids=[wrong_timeout_execution_id],
        force=True,
        connection=redis_connection,
    )
    assert wrong_timeout.dispatched == 0
    assert wrong_timeout.deferred == 1
    with session_factory() as session:
        dispatch = session.scalar(
            select(ExecutionDispatch).where(
                ExecutionDispatch.case_execution_id == wrong_timeout_execution_id
            )
        )
        assert dispatch is not None
        assert dispatch.status == "pending"
        assert dispatch.last_error_kind == "DispatchIdentityError"


def test_worker_rejects_a_wrong_timeout_delivery_without_consuming_an_attempt(
    execution_factory: ExecutionFactory,
    session_factory: SessionFactory,
    redis_connection: Redis,
    rq_queue: Queue,
) -> None:
    execution_id = execution_factory(materialize_state=False)
    with session_factory.begin() as session:
        create_dispatch(session, execution_id, generation=1)

    job = rq_queue.enqueue(
        EXECUTION_JOB_FUNCTION,
        str(execution_id),
        1,
        job_id=job_id_for_execution(execution_id, 1),
        job_timeout=31,
        unique=True,
    )
    _work_queue(rq_queue, redis_connection)

    assert job.get_status(refresh=True) == JobStatus.FAILED
    with session_factory() as session:
        execution = session.get(CaseExecution, execution_id)
        assert execution is not None
        assert execution.status == "queued"
        assert execution.attempt_count == 0
        assert _attempts(session, execution_id) == []

    collision = reconcile_pending_dispatches(
        execution_ids=[execution_id],
        force=True,
        connection=redis_connection,
    )
    assert collision.dispatched == 0
    assert collision.deferred == 1
    with session_factory() as session:
        dispatch = session.scalar(
            select(ExecutionDispatch).where(ExecutionDispatch.case_execution_id == execution_id)
        )
        assert dispatch is not None
        assert dispatch.status == "pending"
        assert dispatch.last_error_kind == "DispatchIdentityError"


@pytest.mark.parametrize("rq_job_state", ["failed", "missing"])
def test_maintenance_supersedes_an_unclaimed_terminal_or_missing_delivery(
    rq_job_state: str,
    execution_factory: ExecutionFactory,
    session_factory: SessionFactory,
    redis_connection: Redis,
    rq_queue: Queue,
) -> None:
    execution_id = _prepare_execution(
        execution_factory,
        session_factory,
        redis_connection,
        max_attempts=3,
    )
    with session_factory.begin() as session:
        execution = session.get(CaseExecution, execution_id)
        assert execution is not None
        experiment = session.get(Experiment, execution.experiment_id)
        assert experiment is not None
        experiment.retry_backoff_seconds = 60
    job_id = job_id_for_execution(execution_id, 1)
    job = rq_queue.fetch_job(job_id)
    assert job is not None
    if rq_job_state == "failed":
        rq_queue.remove(job)
        job.set_status(JobStatus.FAILED)
    else:
        job.delete()

    stale_dispatch_check = datetime.now(UTC) - timedelta(seconds=31)
    with session_factory.begin() as session:
        dispatch = session.scalar(
            select(ExecutionDispatch).where(
                ExecutionDispatch.case_execution_id == execution_id,
                ExecutionDispatch.generation == 1,
            )
        )
        assert dispatch is not None
        dispatch.last_attempt_at = stale_dispatch_check

    summary = reconcile_pending_dispatches(
        execution_ids=[execution_id],
        connection=redis_connection,
    )

    assert summary.attempted == 1
    assert summary.dispatched == 0
    assert summary.deferred == 1
    with session_factory() as session:
        execution = session.get(CaseExecution, execution_id)
        assert execution is not None
        assert execution.status == "retrying"
        assert execution.attempt_count == 1
        assert execution.dispatch_generation == 2
        attempts = _attempts(session, execution_id)
        assert len(attempts) == 1
        assert attempts[0].status == "interrupted"
        assert attempts[0].failure_kind == "worker_interruption"
        assert attempts[0].step_count == 0
        next_dispatch = session.scalar(
            select(ExecutionDispatch).where(
                ExecutionDispatch.case_execution_id == execution_id,
                ExecutionDispatch.generation == 2,
            )
        )
        assert next_dispatch is not None
        assert next_dispatch.status == "pending"


@pytest.mark.parametrize("delivery_state", ["pending_terminal", "dispatched_missing"])
def test_legacy_enqueue_failed_delivery_recovers_after_terminal_or_missing_rq_state(
    delivery_state: str,
    execution_factory: ExecutionFactory,
    session_factory: SessionFactory,
    redis_connection: Redis,
    rq_queue: Queue,
) -> None:
    execution_id = execution_factory(materialize_state=False)
    job_id = job_id_for_execution(execution_id, 1)
    with session_factory.begin() as session:
        execution = session.get(CaseExecution, execution_id)
        assert execution is not None
        experiment = session.get(Experiment, execution.experiment_id)
        assert experiment is not None
        execution.status = "enqueue_failed"
        experiment.status = "enqueue_failed"
        experiment.retry_backoff_seconds = 60
        create_dispatch(session, execution_id, generation=1)

    if delivery_state == "pending_terminal":
        job = rq_queue.enqueue(
            EXECUTION_JOB_FUNCTION,
            str(execution_id),
            1,
            job_id=job_id,
            job_timeout=30,
            unique=True,
        )
        rq_queue.remove(job)
        job.set_status(JobStatus.FAILED)
        summary = reconcile_pending_dispatches(
            execution_ids=[execution_id],
            connection=redis_connection,
        )
    else:
        initial = reconcile_pending_dispatches(
            execution_ids=[execution_id],
            force=True,
            connection=redis_connection,
        )
        assert initial.dispatched == 1
        job = rq_queue.fetch_job(job_id)
        assert job is not None
        job.delete()
        with session_factory.begin() as session:
            dispatch = session.scalar(
                select(ExecutionDispatch).where(
                    ExecutionDispatch.case_execution_id == execution_id,
                    ExecutionDispatch.generation == 1,
                )
            )
            assert dispatch is not None
            dispatch.last_attempt_at = datetime.now(UTC) - timedelta(seconds=31)
        summary = reconcile_pending_dispatches(
            execution_ids=[execution_id],
            connection=redis_connection,
        )

    assert summary.attempted == 1
    assert summary.dispatched == 0
    assert summary.deferred == 1
    with session_factory() as session:
        execution = session.get(CaseExecution, execution_id)
        assert execution is not None
        assert execution.status == "retrying"
        assert execution.attempt_count == 1
        assert execution.dispatch_generation == 2
        attempts = _attempts(session, execution_id)
        assert len(attempts) == 1
        assert attempts[0].status == "interrupted"
        assert attempts[0].failure_kind == "worker_interruption"
        next_dispatch = session.scalar(
            select(ExecutionDispatch).where(
                ExecutionDispatch.case_execution_id == execution_id,
                ExecutionDispatch.generation == 2,
            )
        )
        assert next_dispatch is not None
        assert next_dispatch.status == "pending"


def test_preclaim_interruption_consumes_the_final_attempt_without_an_unbounded_redelivery(
    execution_factory: ExecutionFactory,
    session_factory: SessionFactory,
) -> None:
    execution_id = execution_factory(materialize_state=False)
    job_id = job_id_for_execution(execution_id, 1)
    with session_factory.begin() as session:
        execution = session.get(CaseExecution, execution_id)
        assert execution is not None
        experiment = session.get(Experiment, execution.experiment_id)
        assert experiment is not None
        experiment.max_attempts = 1
        dispatch = create_dispatch(session, execution_id, generation=1)
        dispatch.status = "dispatched"

    wrong_generation = recover_interrupted_execution(execution_id, 2, job_id)
    wrong_job = recover_interrupted_execution(execution_id, 1, "unrelated-job")
    outcome = recover_interrupted_execution(execution_id, 1, job_id)
    repeated = recover_interrupted_execution(execution_id, 1, job_id)

    assert wrong_generation == "fresh_or_locked"
    assert wrong_job == "fresh_or_locked"
    assert outcome == "failed"
    assert repeated == "fresh_or_locked"
    with session_factory() as session:
        execution = session.get(CaseExecution, execution_id)
        assert execution is not None
        assert execution.status == "failed"
        assert execution.attempt_count == 1
        assert execution.dispatch_generation == 1
        attempts = _attempts(session, execution_id)
        assert len(attempts) == 1
        assert attempts[0].status == "interrupted"
        assert attempts[0].retryable is True


def test_recovered_attempt_fences_a_paused_worker_before_provider_or_tool_work(
    execution_factory: ExecutionFactory,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution_id = execution_factory(materialize_state=False, case_key="eligible-refund")
    job_id = job_id_for_execution(execution_id, 1)
    with session_factory.begin() as session:
        execution = session.get(CaseExecution, execution_id)
        assert execution is not None
        experiment = session.get(Experiment, execution.experiment_id)
        assert experiment is not None
        experiment.max_attempts = 3
        experiment.retry_backoff_seconds = 60
        create_dispatch(session, execution_id, generation=1)

    original_claim = execution_jobs._claim_attempt(execution_id, 1, job_id)
    assert original_claim.state == "claimed"
    assert original_claim.attempt_number == 1
    assert recover_interrupted_execution(execution_id, 1, job_id) == "recovered"

    monkeypatch.setattr(
        execution_jobs,
        "_claim_attempt",
        lambda *_args, **_kwargs: original_claim,
    )
    stale_result = execution_jobs.run_case_execution(str(execution_id), 1)

    assert stale_result == {
        "execution_id": str(execution_id),
        "status": "stale",
        "replayed": True,
        "duplicate_delivery": True,
    }
    with session_factory() as session:
        execution = session.get(CaseExecution, execution_id)
        assert execution is not None
        assert execution.status == "retrying"
        assert execution.attempt_count == 1
        assert execution.dispatch_generation == 2
        assert execution.final_resolution is None
        attempts = _attempts(session, execution_id)
        assert len(attempts) == 1
        assert attempts[0].status == "interrupted"
        assert _count(session, Customer, execution_id) == 0
        assert _count(session, Order, execution_id) == 0
        assert _count(session, Refund, execution_id) == 0
        assert _count(session, ToolInvocation, execution_id) == 0
        assert _count(session, GraderResult, execution_id) == 0
        traces = list(
            session.scalars(
                select(TraceEvent)
                .where(TraceEvent.case_execution_id == execution_id)
                .order_by(TraceEvent.sequence_number)
            ).all()
        )
        assert [trace.event_type for trace in traces] == ["execution_recovery_scheduled"]


def test_partial_dispatch_failure_stops_then_recovers_all_pending_rows(
    execution_factory: ExecutionFactory,
    session_factory: SessionFactory,
    redis_connection: Redis,
    rq_queue: Queue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution_ids = [execution_factory(materialize_state=False) for _ in range(4)]
    with session_factory.begin() as session:
        for execution_id in execution_ids:
            create_dispatch(session, execution_id, generation=1)

    real_enqueue = execution_dispatcher.enqueue_execution
    enqueue_calls = 0

    def fail_after_two(*args: object, **kwargs: object) -> object:
        nonlocal enqueue_calls
        enqueue_calls += 1
        if enqueue_calls > 2:
            raise OSError("synthetic transport failure after two dispatches")
        return real_enqueue(*args, **kwargs)

    monkeypatch.setattr(execution_dispatcher, "enqueue_execution", fail_after_two)
    partial = reconcile_pending_dispatches(limit=10, force=True, connection=redis_connection)

    assert partial.attempted == 3
    assert partial.dispatched == 2
    assert partial.deferred == 1
    assert partial.transport_failures == 1
    assert partial.stopped_on_transport_failure is True
    assert len(rq_queue.job_ids) == 2

    monkeypatch.setattr(execution_dispatcher, "enqueue_execution", real_enqueue)
    recovered = reconcile_pending_dispatches(limit=10, force=True, connection=redis_connection)

    assert recovered.attempted == 2
    assert recovered.dispatched == 2
    assert recovered.deferred == 0
    assert set(rq_queue.job_ids) == {
        job_id_for_execution(execution_id, 1) for execution_id in execution_ids
    }
    with session_factory() as session:
        dispatches = session.scalars(
            select(ExecutionDispatch).where(ExecutionDispatch.case_execution_id.in_(execution_ids))
        ).all()
        assert len(dispatches) == 4
        assert all(dispatch.status == "dispatched" for dispatch in dispatches)
        assert all(dispatch.last_error_kind is None for dispatch in dispatches)
        assert sorted(dispatch.attempt_count for dispatch in dispatches) == [1, 1, 1, 2]


def test_transient_before_provider_succeeds_on_attempt_two_and_duplicate_delivery_is_safe(
    execution_factory: ExecutionFactory,
    session_factory: SessionFactory,
    redis_connection: Redis,
    rq_queue: Queue,
) -> None:
    execution_id = _prepare_execution(
        execution_factory,
        session_factory,
        redis_connection,
        fault_mode=FaultMode.TRANSIENT_BEFORE_PROVIDER,
        fail_attempts=1,
        max_attempts=3,
    )

    _work_queue(rq_queue, redis_connection)

    with session_factory() as session:
        execution = session.get(CaseExecution, execution_id)
        assert execution is not None
        assert execution.status == "completed"
        assert execution.attempt_count == 2
        assert execution.dispatch_generation == 2
        attempts = _attempts(session, execution_id)
        assert [attempt.status for attempt in attempts] == ["retry_scheduled", "succeeded"]
        assert [attempt.failure_kind for attempt in attempts] == ["injected_transient", None]
        assert [attempt.step_count for attempt in attempts] == [0, 2]
        assert _count(session, Customer, execution_id) == 1
        assert _count(session, Order, execution_id) == 1
        assert _count(session, Refund, execution_id) == 1
        assert _count(session, ToolInvocation, execution_id) == 2

    stale = run_case_execution(str(execution_id), 1)
    duplicate = run_case_execution(str(execution_id), 2)
    assert stale == {
        "execution_id": str(execution_id),
        "status": "stale",
        "replayed": True,
        "duplicate_delivery": True,
    }
    assert duplicate == {
        "execution_id": str(execution_id),
        "status": "completed",
        "replayed": True,
    }
    with session_factory() as session:
        assert len(_attempts(session, execution_id)) == 2
        assert _count(session, Refund, execution_id) == 1
        assert _count(session, ToolInvocation, execution_id) == 2


def test_always_transient_exhausts_exactly_three_attempts_without_business_state(
    execution_factory: ExecutionFactory,
    session_factory: SessionFactory,
    redis_connection: Redis,
    rq_queue: Queue,
) -> None:
    execution_id = _prepare_execution(
        execution_factory,
        session_factory,
        redis_connection,
        fault_mode=FaultMode.TRANSIENT_BEFORE_PROVIDER,
        fail_attempts=3,
        max_attempts=3,
    )

    _work_queue(rq_queue, redis_connection)

    with session_factory() as session:
        execution = session.get(CaseExecution, execution_id)
        assert execution is not None
        assert execution.status == "failed"
        assert execution.attempt_count == 3
        assert execution.dispatch_generation == 3
        assert execution.last_failure_kind == "injected_transient"
        assert execution.last_failure_retryable is True
        attempts = _attempts(session, execution_id)
        assert [attempt.status for attempt in attempts] == [
            "retry_scheduled",
            "retry_scheduled",
            "failed",
        ]
        assert all(attempt.failure_kind == "injected_transient" for attempt in attempts)
        assert all(attempt.retryable is True for attempt in attempts)
        assert _count(session, Customer, execution_id) == 0
        assert _count(session, Order, execution_id) == 0
        assert _count(session, Refund, execution_id) == 0
        assert _count(session, ToolInvocation, execution_id) == 0


def test_permanent_fault_fails_once_without_retry_or_business_state(
    execution_factory: ExecutionFactory,
    session_factory: SessionFactory,
    redis_connection: Redis,
    rq_queue: Queue,
) -> None:
    execution_id = _prepare_execution(
        execution_factory,
        session_factory,
        redis_connection,
        fault_mode=FaultMode.PERMANENT_BEFORE_PROVIDER,
        fail_attempts=1,
        max_attempts=3,
    )

    _work_queue(rq_queue, redis_connection)

    with session_factory() as session:
        execution = session.get(CaseExecution, execution_id)
        assert execution is not None
        assert execution.status == "failed"
        assert execution.attempt_count == 1
        assert execution.dispatch_generation == 1
        attempts = _attempts(session, execution_id)
        assert len(attempts) == 1
        assert attempts[0].status == "failed"
        assert attempts[0].failure_kind == "injected_permanent"
        assert attempts[0].retryable is False
        assert _count(session, Customer, execution_id) == 0
        assert _count(session, Order, execution_id) == 0
        assert _count(session, Refund, execution_id) == 0
        assert _count(session, ToolInvocation, execution_id) == 0


def test_worker_fails_closed_on_a_persisted_fault_plan_outside_dev_or_test(
    execution_factory: ExecutionFactory,
    session_factory: SessionFactory,
    redis_connection: Redis,
    rq_queue: Queue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution_id = _prepare_execution(
        execution_factory,
        session_factory,
        redis_connection,
        fault_mode=FaultMode.TRANSIENT_BEFORE_PROVIDER,
        fail_attempts=1,
        max_attempts=3,
    )
    monkeypatch.setattr(
        execution_executor,
        "get_settings",
        lambda: SimpleNamespace(environment="production"),
    )

    _work_queue(rq_queue, redis_connection)

    with session_factory() as session:
        execution = session.get(CaseExecution, execution_id)
        assert execution is not None
        assert execution.status == "failed"
        assert execution.attempt_count == 1
        assert execution.last_failure_kind == "unexpected"
        attempts = _attempts(session, execution_id)
        assert len(attempts) == 1
        assert attempts[0].status == "failed"
        assert attempts[0].failure_kind == "unexpected"
        assert "FaultInjectionDisabledError" in str(attempts[0].error_message)
        assert _count(session, Customer, execution_id) == 0
        assert _count(session, Order, execution_id) == 0
        assert _count(session, ToolInvocation, execution_id) == 0


def test_rq_timeout_is_retryable_and_succeeds_on_the_next_generation(
    execution_factory: ExecutionFactory,
    session_factory: SessionFactory,
    redis_connection: Redis,
    rq_queue: Queue,
) -> None:
    execution_id = _prepare_execution(
        execution_factory,
        session_factory,
        redis_connection,
        fault_mode=FaultMode.TIMEOUT,
        fail_attempts=1,
        max_attempts=3,
        job_timeout_seconds=1,
    )
    first_job = rq_queue.fetch_job(job_id_for_execution(execution_id, 1))
    assert first_job is not None

    _work_queue(rq_queue, redis_connection)

    with session_factory() as session:
        execution = session.get(CaseExecution, execution_id)
        assert execution is not None
        assert execution.status == "retrying"
        assert execution.attempt_count == 1
        assert execution.dispatch_generation == 2
        attempts = _attempts(session, execution_id)
        assert [attempt.status for attempt in attempts] == ["retry_scheduled"]
        assert attempts[0].failure_kind == "timeout"
        assert attempts[0].retryable is True
        assert attempts[0].step_count == 0
        second_dispatch = session.scalar(
            select(ExecutionDispatch).where(
                ExecutionDispatch.case_execution_id == execution_id,
                ExecutionDispatch.generation == 2,
            )
        )
        assert second_dispatch is not None
        assert second_dispatch.status == "pending"
        assert second_dispatch.last_error_kind is None
    assert first_job.get_status(refresh=True) == JobStatus.FAILED

    timeout_reconciliation = reconcile_pending_dispatches(
        execution_ids=[execution_id],
        force=True,
        connection=redis_connection,
    )
    assert timeout_reconciliation.dispatched == 1
    second_job = rq_queue.fetch_job(job_id_for_execution(execution_id, 2))
    assert second_job is not None
    assert second_job.get_status(refresh=True) == JobStatus.QUEUED

    _work_queue(rq_queue, redis_connection)

    with session_factory() as session:
        execution = session.get(CaseExecution, execution_id)
        assert execution is not None
        assert execution.status == "completed"
        assert execution.attempt_count == 2
        attempts = _attempts(session, execution_id)
        assert [attempt.status for attempt in attempts] == ["retry_scheduled", "succeeded"]
        assert attempts[1].dispatch_generation == 2
        assert _count(session, Refund, execution_id) == 1


def test_transient_after_tools_rolls_back_first_attempt_then_commits_one_effect(
    execution_factory: ExecutionFactory,
    session_factory: SessionFactory,
    redis_connection: Redis,
    rq_queue: Queue,
) -> None:
    execution_id = _prepare_execution(
        execution_factory,
        session_factory,
        redis_connection,
        fault_mode=FaultMode.TRANSIENT_AFTER_TOOLS,
        fail_attempts=1,
        max_attempts=3,
        retry_backoff_seconds=60,
    )

    first_worker = SimpleWorker([rq_queue], connection=redis_connection)
    assert (
        first_worker.work(
            burst=True,
            max_jobs=1,
            logging_level="CRITICAL",
            with_scheduler=False,
        )
        is True
    )

    with session_factory() as session:
        execution = session.get(CaseExecution, execution_id)
        assert execution is not None
        assert execution.status == "retrying"
        assert execution.attempt_count == 1
        assert execution.dispatch_generation == 2
        assert execution.final_resolution is None
        attempts = _attempts(session, execution_id)
        assert len(attempts) == 1
        assert attempts[0].status == "retry_scheduled"
        assert attempts[0].failure_kind == "injected_transient"
        assert attempts[0].retryable is True
        assert attempts[0].step_count == 2
        assert attempts[0].completed_at is not None
        assert _count(session, Customer, execution_id) == 0
        assert _count(session, Order, execution_id) == 0
        assert _count(session, Refund, execution_id) == 0
        assert _count(session, ToolInvocation, execution_id) == 0
        assert _count(session, GraderResult, execution_id) == 0
        traces = list(
            session.scalars(
                select(TraceEvent)
                .where(TraceEvent.case_execution_id == execution_id)
                .order_by(TraceEvent.sequence_number)
            ).all()
        )
        assert [(trace.sequence_number, trace.event_type) for trace in traces] == [
            (1, "execution_retry_scheduled")
        ]
        assert traces[0].payload == {
            "attempt_number": 1,
            "failure_kind": "injected_transient",
            "retryable": True,
            "next_dispatch_generation": 2,
            "delay_seconds": 60,
        }
        second_dispatch = session.scalar(
            select(ExecutionDispatch).where(
                ExecutionDispatch.case_execution_id == execution_id,
                ExecutionDispatch.generation == 2,
            )
        )
        assert second_dispatch is not None
        assert second_dispatch.status == "pending"
        assert second_dispatch.available_at - attempts[0].completed_at == timedelta(seconds=60)
    assert job_id_for_execution(execution_id, 2) not in rq_queue.job_ids

    dispatched = reconcile_pending_dispatches(
        execution_ids=[execution_id],
        force=True,
        connection=redis_connection,
    )
    assert dispatched.dispatched == 1
    assert job_id_for_execution(execution_id, 2) in rq_queue.job_ids

    _work_queue(rq_queue, redis_connection)

    with session_factory() as session:
        execution = session.get(CaseExecution, execution_id)
        assert execution is not None
        assert execution.status == "completed"
        assert execution.attempt_count == 2
        assert execution.dispatch_generation == 2
        assert execution.final_resolution is not None
        attempts = _attempts(session, execution_id)
        assert [attempt.status for attempt in attempts] == ["retry_scheduled", "succeeded"]
        assert [attempt.step_count for attempt in attempts] == [2, 2]
        assert _count(session, Customer, execution_id) == 1
        assert _count(session, Order, execution_id) == 1
        assert _count(session, Refund, execution_id) == 1
        assert _count(session, ToolInvocation, execution_id) == 2
        assert _count(session, GraderResult, execution_id) > 0
        traces = list(
            session.scalars(
                select(TraceEvent)
                .where(TraceEvent.case_execution_id == execution_id)
                .order_by(TraceEvent.sequence_number)
            ).all()
        )
        assert [trace.event_type for trace in traces] == [
            "execution_retry_scheduled",
            "provider_request",
            "tool_call",
            "tool_result",
            "tool_call",
            "tool_result",
            "provider_response",
            "grader_completed",
        ]


def test_maximum_tool_steps_is_permanent_and_rolls_back_all_mutation(
    execution_factory: ExecutionFactory,
    session_factory: SessionFactory,
    redis_connection: Redis,
    rq_queue: Queue,
) -> None:
    execution_id = _prepare_execution(
        execution_factory,
        session_factory,
        redis_connection,
        max_attempts=3,
        max_tool_steps=1,
    )

    _work_queue(rq_queue, redis_connection)

    with session_factory() as session:
        execution = session.get(CaseExecution, execution_id)
        assert execution is not None
        assert execution.status == "failed"
        assert execution.attempt_count == 1
        attempts = _attempts(session, execution_id)
        assert len(attempts) == 1
        assert attempts[0].status == "failed"
        assert attempts[0].failure_kind == "maximum_tool_steps"
        assert attempts[0].retryable is False
        assert attempts[0].step_count == 2
        assert _count(session, Customer, execution_id) == 0
        assert _count(session, Order, execution_id) == 0
        assert _count(session, Refund, execution_id) == 0
        assert _count(session, ToolInvocation, execution_id) == 0
        assert _count(session, GraderResult, execution_id) == 0


def test_worker_interruption_respects_fresh_lease_and_expired_lease_recovers_once(
    execution_factory: ExecutionFactory,
    session_factory: SessionFactory,
    redis_connection: Redis,
    rq_queue: Queue,
) -> None:
    execution_id = _prepare_execution(
        execution_factory,
        session_factory,
        redis_connection,
        fault_mode=FaultMode.WORKER_INTERRUPTION,
        fail_attempts=1,
        max_attempts=3,
    )

    _work_queue(rq_queue, redis_connection)

    with session_factory() as session:
        execution = session.get(CaseExecution, execution_id)
        assert execution is not None
        assert execution.status == "running"
        assert execution.attempt_count == 1
        assert execution.dispatch_generation == 1
        assert execution.lease_expires_at is not None
        attempts = _attempts(session, execution_id)
        assert len(attempts) == 1
        assert attempts[0].status == "running"

    fresh = recover_stale_executions()
    assert fresh.examined == 0
    assert fresh.recovered == 0

    expired_at = datetime.now(UTC) - timedelta(seconds=1)
    with session_factory.begin() as session:
        execution = session.get(CaseExecution, execution_id)
        assert execution is not None
        execution.lease_expires_at = expired_at
        attempt = session.scalar(
            select(ExecutionAttempt).where(
                ExecutionAttempt.case_execution_id == execution_id,
                ExecutionAttempt.attempt_number == 1,
            )
        )
        assert attempt is not None
        attempt.lease_expires_at = expired_at

    recovered = recover_stale_executions()
    repeated = recover_stale_executions()
    assert recovered.examined == 1
    assert recovered.recovered == 1
    assert recovered.failed == 0
    assert repeated.examined == 0
    assert repeated.recovered == 0

    dispatch_summary = reconcile_pending_dispatches(
        execution_ids=[execution_id],
        force=True,
        connection=redis_connection,
    )
    assert dispatch_summary.dispatched == 1
    assert job_id_for_execution(execution_id, 2) in rq_queue.job_ids

    _work_queue(rq_queue, redis_connection)

    with session_factory() as session:
        execution = session.get(CaseExecution, execution_id)
        assert execution is not None
        assert execution.status == "completed"
        assert execution.dispatch_generation == 2
        attempts = _attempts(session, execution_id)
        assert [attempt.status for attempt in attempts] == ["interrupted", "succeeded"]
        assert attempts[0].failure_kind == "worker_interruption"
        assert attempts[0].retryable is True
        assert attempts[1].job_id == job_id_for_execution(execution_id, 2)
        assert _count(session, Refund, execution_id) == 1


def test_api_exposes_policy_attempt_dispatch_and_idempotency_resilience_intent(
    client: TestClient,
    seeded_suite: object,
    rq_queue: Queue,
) -> None:
    del seeded_suite
    payload = {
        "provider": "deterministic-fake",
        "trials": 1,
        "max_attempts": 3,
        "job_timeout_seconds": 45,
        "max_tool_steps": 5,
        "retry_backoff_seconds": 60,
        "fault_injection": {
            "case_key": "eligible-refund",
            "mode": "transient_before_provider",
            "fail_attempts": 1,
        },
    }
    headers = {"Idempotency-Key": "phase4-api-resilience-intent"}

    created = client.post("/api/v1/experiments", json=payload, headers=headers)
    assert created.status_code == 202
    experiment_id = UUID(created.json()["id"])

    experiment_response = client.get(f"/api/v1/experiments/{experiment_id}")
    assert experiment_response.status_code == 200
    experiment_payload = experiment_response.json()
    assert experiment_payload["max_attempts"] == 3
    assert experiment_payload["job_timeout_seconds"] == 45
    assert experiment_payload["max_tool_steps"] == 5
    assert experiment_payload["retry_backoff_seconds"] == 60
    target = next(
        execution
        for execution in experiment_payload["executions"]
        if execution["case_key"] == "eligible-refund"
    )
    execution_id = UUID(target["id"])

    first_attempt = run_case_execution(str(execution_id), 1)
    assert first_attempt["status"] == "retrying"
    assert first_attempt["attempt_number"] == 1
    assert first_attempt["failure_kind"] == "injected_transient"

    execution_response = client.get(f"/api/v1/executions/{execution_id}")
    assert execution_response.status_code == 200
    execution_payload = execution_response.json()
    assert execution_payload["status"] == "retrying"
    assert execution_payload["attempt_count"] == 1
    assert execution_payload["max_attempts"] == 3
    assert execution_payload["job_timeout_seconds"] == 45
    assert execution_payload["max_tool_steps"] == 5
    assert execution_payload["retry_backoff_seconds"] == 60
    assert execution_payload["dispatch_generation"] == 2
    assert execution_payload["last_failure_kind"] == "injected_transient"
    assert execution_payload["last_failure_retryable"] is True
    assert [attempt["status"] for attempt in execution_payload["attempts"]] == ["retry_scheduled"]
    assert [dispatch["generation"] for dispatch in execution_payload["dispatches"]] == [1, 2]
    assert [dispatch["status"] for dispatch in execution_payload["dispatches"]] == [
        "dispatched",
        "pending",
    ]
    retry_job_id = job_id_for_execution(execution_id, 2)
    assert retry_job_id not in rq_queue.job_ids

    replay = client.post("/api/v1/experiments", json=payload, headers=headers)
    assert replay.status_code == 202
    assert UUID(replay.json()["id"]) == experiment_id
    replayed_execution = client.get(f"/api/v1/executions/{execution_id}")
    assert replayed_execution.status_code == 200
    assert [dispatch["status"] for dispatch in replayed_execution.json()["dispatches"]] == [
        "dispatched",
        "pending",
    ]
    assert retry_job_id not in rq_queue.job_ids

    changed_policy = {**payload, "max_attempts": 2}
    policy_conflict = client.post(
        "/api/v1/experiments",
        json=changed_policy,
        headers=headers,
    )
    assert policy_conflict.status_code == 409

    changed_fault = {
        **payload,
        "fault_injection": {
            "case_key": "eligible-refund",
            "mode": "permanent_before_provider",
            "fail_attempts": 1,
        },
    }
    fault_conflict = client.post(
        "/api/v1/experiments",
        json=changed_fault,
        headers=headers,
    )
    assert fault_conflict.status_code == 409
