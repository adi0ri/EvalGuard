"""Durable PostgreSQL-to-RQ dispatch reconciliation."""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from evalguard.db.models import CaseExecution, ExecutionDispatch, Experiment
from evalguard.db.session import session_scope
from evalguard.workers.queue import (
    RUNNABLE_JOB_STATUSES,
    DispatchIdentityError,
    UnrunnableDispatchJobError,
    enqueue_execution,
    existing_execution_job,
    get_redis_connection,
    job_id_for_execution,
)

logger = logging.getLogger(__name__)

DEFAULT_DISPATCH_LIMIT = 100
MAX_RETRY_BACKOFF_MULTIPLIER = 32
DELIVERY_RECHECK_SECONDS = 30
RECOVERABLE_UNCLAIMED_STATUSES = ("queued", "retrying", "enqueue_failed")


@dataclass(frozen=True, slots=True)
class DispatchSummary:
    """Counts from one bounded dispatch reconciliation pass."""

    attempted: int = 0
    dispatched: int = 0
    deferred: int = 0
    transport_failures: int = 0
    stopped_on_transport_failure: bool = False

    def as_dict(self) -> dict[str, int | bool]:
        """Return a stable JSON-serializable representation for CLI output."""

        return {
            "attempted": self.attempted,
            "dispatched": self.dispatched,
            "deferred": self.deferred,
            "transport_failures": self.transport_failures,
            "stopped_on_transport_failure": self.stopped_on_transport_failure,
        }


@dataclass(frozen=True, slots=True)
class _DispatchOutcome:
    dispatched: bool
    transport_failure: bool
    abandoned_delivery: tuple[UUID, int, str] | None = None


def create_dispatch(
    session: Session,
    execution_id: UUID,
    generation: int,
    available_at: datetime | None = None,
) -> ExecutionDispatch:
    """Create or return one durable dispatch intent for an execution generation."""

    job_id = job_id_for_execution(execution_id, generation)
    execution = session.get(CaseExecution, execution_id)
    if execution is None:
        raise ValueError(f"Case execution {execution_id} does not exist")
    if generation < execution.dispatch_generation:
        raise ValueError(
            f"Dispatch generation {generation} is older than current generation "
            f"{execution.dispatch_generation}"
        )
    if generation > execution.dispatch_generation:
        execution.dispatch_generation = generation

    existing = session.scalar(
        select(ExecutionDispatch).where(
            ExecutionDispatch.case_execution_id == execution_id,
            ExecutionDispatch.generation == generation,
        )
    )
    if existing is not None:
        if existing.job_id != job_id:
            raise DispatchIdentityError(
                f"Persisted dispatch for execution {execution_id} generation {generation} "
                "has an unexpected job identity"
            )
        return existing

    dispatch = ExecutionDispatch(
        case_execution_id=execution_id,
        generation=generation,
        job_id=job_id,
        status="pending",
        attempt_count=0,
        available_at=available_at or datetime.now(UTC),
    )
    session.add(dispatch)
    session.flush()
    return dispatch


def _retry_delay_seconds(base_seconds: int, attempt_count: int) -> int:
    exponent = min(max(attempt_count - 1, 0), 5)
    multiplier = min(1 << exponent, MAX_RETRY_BACKOFF_MULTIPLIER)
    return base_seconds * multiplier


def _is_transport_failure(error: Exception) -> bool:
    return isinstance(error, (RedisError, OSError))


def _pending_dispatch_statement(
    *,
    now: datetime,
    execution_ids: tuple[UUID, ...] | None,
    force: bool,
    force_initial: bool,
    excluded_dispatch_ids: set[UUID],
) -> Select[tuple[ExecutionDispatch]]:
    pending_clause = ExecutionDispatch.status == "pending"
    if not force:
        due_clause = ExecutionDispatch.available_at <= now
        if force_initial:
            due_clause = or_(
                due_clause,
                CaseExecution.status.in_(("queued", "enqueue_failed")),
            )
        pending_clause = and_(pending_clause, due_clause)

    dispatch_clauses = [pending_clause]
    if not force:
        dispatch_clauses.append(
            and_(
                ExecutionDispatch.status == "dispatched",
                CaseExecution.status.in_(RECOVERABLE_UNCLAIMED_STATUSES),
                ExecutionDispatch.last_attempt_at.is_not(None),
                ExecutionDispatch.last_attempt_at
                <= now - timedelta(seconds=DELIVERY_RECHECK_SECONDS),
            )
        )

    statement = (
        select(ExecutionDispatch)
        .join(
            CaseExecution,
            CaseExecution.id == ExecutionDispatch.case_execution_id,
        )
        .where(
            ExecutionDispatch.generation == CaseExecution.dispatch_generation,
            or_(*dispatch_clauses),
        )
        .order_by(
            ExecutionDispatch.available_at,
            ExecutionDispatch.created_at,
            ExecutionDispatch.id,
        )
        .limit(1)
    )
    if execution_ids is not None:
        statement = statement.where(ExecutionDispatch.case_execution_id.in_(execution_ids))
    if excluded_dispatch_ids:
        statement = statement.where(ExecutionDispatch.id.not_in(excluded_dispatch_ids))
    return statement.with_for_update(
        of=ExecutionDispatch,
        skip_locked=execution_ids is None,
    )


def _attempt_dispatch(
    session: Session,
    dispatch: ExecutionDispatch,
    *,
    connection: Redis,
    attempted_at: datetime,
) -> _DispatchOutcome:
    execution = session.get(CaseExecution, dispatch.case_execution_id)
    if execution is None:
        raise RuntimeError(f"Dispatch {dispatch.id} references a missing execution")
    experiment = session.get(Experiment, execution.experiment_id)
    if experiment is None:
        raise RuntimeError(f"Execution {execution.id} references a missing experiment")

    dispatch.last_attempt_at = attempted_at
    dispatch.updated_at = attempted_at

    if dispatch.status == "dispatched":
        dispatch.attempt_count += 1
        try:
            existing = existing_execution_job(
                execution.id,
                dispatch.generation,
                experiment.job_timeout_seconds,
                connection,
            )
            existing_status = existing.get_status(refresh=True) if existing is not None else None
        except Exception as error:
            transport_failure = _is_transport_failure(error)
            dispatch.status = "pending"
            dispatch.last_error_kind = type(error).__name__[:50]
            dispatch.available_at = attempted_at + timedelta(
                seconds=_retry_delay_seconds(
                    experiment.retry_backoff_seconds,
                    dispatch.attempt_count,
                )
            )
            return _DispatchOutcome(
                dispatched=False,
                transport_failure=transport_failure,
            )

        if existing_status in RUNNABLE_JOB_STATUSES:
            dispatch.last_error_kind = None
            return _DispatchOutcome(dispatched=True, transport_failure=False)
        if execution.status not in RECOVERABLE_UNCLAIMED_STATUSES:
            dispatch.last_error_kind = None
            return _DispatchOutcome(dispatched=True, transport_failure=False)

        dispatch.last_error_kind = (
            "MissingRQJob"
            if existing is None
            else f"UnrunnableRQJob:{existing_status.value if existing_status else 'unknown'}"
        )[:50]
        logger.warning(
            "Execution delivery abandoned before a durable claim",
            extra={
                "event": "execution_delivery_abandoned",
                "execution_id": str(execution.id),
                "generation": dispatch.generation,
                "job_status": existing_status.value if existing_status is not None else "missing",
            },
        )
        return _DispatchOutcome(
            dispatched=False,
            transport_failure=False,
            abandoned_delivery=(execution.id, dispatch.generation, dispatch.job_id),
        )

    dispatch.attempt_count += 1
    try:
        enqueue_execution(
            execution.id,
            generation=dispatch.generation,
            job_timeout_seconds=experiment.job_timeout_seconds,
            connection=connection,
        )
    except UnrunnableDispatchJobError as error:
        dispatch.status = "dispatched"
        dispatch.dispatched_at = dispatch.dispatched_at or attempted_at
        if execution.status not in RECOVERABLE_UNCLAIMED_STATUSES:
            dispatch.last_error_kind = None
            return _DispatchOutcome(dispatched=True, transport_failure=False)
        status_value = error.status.value if error.status is not None else "unknown"
        dispatch.last_error_kind = f"UnrunnableRQJob:{status_value}"[:50]
        return _DispatchOutcome(
            dispatched=False,
            transport_failure=False,
            abandoned_delivery=(execution.id, dispatch.generation, dispatch.job_id),
        )
    except Exception as error:
        transport_failure = _is_transport_failure(error)
        dispatch.status = "pending"
        dispatch.last_error_kind = type(error).__name__[:50]
        dispatch.available_at = attempted_at + timedelta(
            seconds=_retry_delay_seconds(
                experiment.retry_backoff_seconds,
                dispatch.attempt_count,
            )
        )
        logger.log(
            logging.WARNING if transport_failure else logging.ERROR,
            "Execution dispatch deferred",
            exc_info=not isinstance(error, DispatchIdentityError),
            extra={
                "event": "execution_dispatch_deferred",
                "execution_id": str(execution.id),
                "generation": dispatch.generation,
                "error_kind": type(error).__name__,
                "transport_failure": transport_failure,
            },
        )
        return _DispatchOutcome(
            dispatched=False,
            transport_failure=transport_failure,
        )

    dispatched_at = datetime.now(UTC)
    dispatch.status = "dispatched"
    dispatch.dispatched_at = dispatched_at
    dispatch.last_error_kind = None
    dispatch.updated_at = dispatched_at
    return _DispatchOutcome(dispatched=True, transport_failure=False)


def reconcile_pending_dispatches(
    limit: int = DEFAULT_DISPATCH_LIMIT,
    execution_ids: Iterable[UUID] | None = None,
    force: bool = False,
    force_initial: bool = False,
    connection: Redis | None = None,
) -> DispatchSummary:
    """Publish a bounded set of current pending dispatches, one transaction each."""

    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("Dispatch reconciliation limit must be a positive integer")
    normalized_execution_ids = (
        None if execution_ids is None else tuple(dict.fromkeys(execution_ids))
    )
    if normalized_execution_ids == ():
        return DispatchSummary()

    attempted = 0
    dispatched_count = 0
    deferred = 0
    transport_failures = 0
    stopped_on_transport_failure = False
    processed_dispatch_ids: set[UUID] = set()
    active_connection = connection
    owns_connection = False

    try:
        while attempted < limit:
            outcome: _DispatchOutcome | None = None
            with session_scope() as session:
                now = datetime.now(UTC)
                dispatch = session.scalar(
                    _pending_dispatch_statement(
                        now=now,
                        execution_ids=normalized_execution_ids,
                        force=force,
                        force_initial=force_initial,
                        excluded_dispatch_ids=processed_dispatch_ids,
                    )
                )
                if dispatch is None:
                    break
                processed_dispatch_ids.add(dispatch.id)
                if active_connection is None:
                    active_connection = get_redis_connection()
                    owns_connection = True
                outcome = _attempt_dispatch(
                    session,
                    dispatch,
                    connection=active_connection,
                    attempted_at=now,
                )

            attempted += 1
            if outcome.abandoned_delivery is not None:
                from evalguard.workers.jobs import recover_interrupted_execution

                execution_id, generation, job_id = outcome.abandoned_delivery
                try:
                    recover_interrupted_execution(execution_id, generation, job_id)
                except Exception:
                    logger.exception(
                        "Abandoned delivery recovery deferred to a later maintenance pass",
                        extra={
                            "event": "execution_delivery_recovery_deferred",
                            "execution_id": str(execution_id),
                            "generation": generation,
                        },
                    )
            if outcome.dispatched:
                dispatched_count += 1
            else:
                deferred += 1
            if outcome.transport_failure:
                transport_failures += 1
                stopped_on_transport_failure = True
                break
    finally:
        if owns_connection and active_connection is not None:
            active_connection.close()

    return DispatchSummary(
        attempted=attempted,
        dispatched=dispatched_count,
        deferred=deferred,
        transport_failures=transport_failures,
        stopped_on_transport_failure=stopped_on_transport_failure,
    )
