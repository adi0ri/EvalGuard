"""RQ job entry points with durable attempts, retry bounds, and lease recovery."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from rq import get_current_job
from rq.job import Job
from rq.timeouts import JobTimeoutException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evalguard.core.config import get_settings
from evalguard.core.logging import configure_logging
from evalguard.db.models import (
    CaseExecution,
    ExecutionAttempt,
    ExecutionDispatch,
    Experiment,
    ProviderTurn,
    TraceEvent,
)
from evalguard.db.session import session_scope
from evalguard.evaluation.executor import (
    ExecutionNotFoundError,
    StaleExecutionClaimError,
    execute_case,
)
from evalguard.evaluation.lifecycle import reconcile_experiment_status
from evalguard.evaluation.real_executor import execute_openai_case
from evalguard.evaluation.resilience import FaultClassification, classify_failure
from evalguard.providers.configuration import OPENAI_PROVIDER
from evalguard.workers.dispatch import create_dispatch, reconcile_pending_dispatches
from evalguard.workers.queue import (
    DispatchIdentityError,
    get_queue,
    job_id_for_execution,
    validate_execution_job,
)

logger = logging.getLogger(__name__)
LEASE_RECOVERY_GRACE_SECONDS = 2
MAX_SAME_ATTEMPT_RECOVERIES = 3
SAFE_PROVIDER_RESUME_STATES = frozenset(
    {"prepared", "response_received", "tools_completed", "final_response"}
)


@dataclass(frozen=True, slots=True)
class AttemptClaim:
    state: str
    attempt_number: int | None
    job_id: str
    dispatch_generation: int
    provider: str
    delivery_kind: str | None = None


@dataclass(frozen=True, slots=True)
class RecoverySummary:
    examined: int = 0
    recovered: int = 0
    failed: int = 0
    fresh_or_locked: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "examined": self.examined,
            "recovered": self.recovered,
            "failed": self.failed,
            "fresh_or_locked": self.fresh_or_locked,
        }


def _append_trace(
    session: Session,
    case_execution_id: UUID,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    current_sequence = session.scalar(
        select(func.coalesce(func.max(TraceEvent.sequence_number), 0)).where(
            TraceEvent.case_execution_id == case_execution_id
        )
    )
    session.add(
        TraceEvent(
            case_execution_id=case_execution_id,
            sequence_number=int(current_sequence or 0) + 1,
            event_type=event_type,
            payload=payload,
        )
    )


def _claim_attempt(
    case_execution_id: UUID,
    dispatch_generation: int,
    job_id: str,
) -> AttemptClaim:
    """Claim one delivery while serializing against duplicates and recovery."""

    with session_scope() as session:
        execution = session.scalar(
            select(CaseExecution).where(CaseExecution.id == case_execution_id).with_for_update()
        )
        if execution is None:
            raise ExecutionNotFoundError(str(case_execution_id))
        experiment = session.get(Experiment, execution.experiment_id)
        if experiment is None:
            raise ExecutionNotFoundError("Execution experiment is missing")

        if dispatch_generation != execution.dispatch_generation:
            return AttemptClaim("stale", None, job_id, dispatch_generation, experiment.provider)
        if execution.status in {"completed", "failed"}:
            return AttemptClaim(
                execution.status,
                execution.attempt_count or None,
                job_id,
                dispatch_generation,
                experiment.provider,
            )
        if execution.status == "running":
            return AttemptClaim(
                "busy",
                execution.attempt_count or None,
                job_id,
                dispatch_generation,
                experiment.provider,
            )

        dispatch = session.scalar(
            select(ExecutionDispatch)
            .where(
                ExecutionDispatch.case_execution_id == execution.id,
                ExecutionDispatch.generation == dispatch_generation,
            )
            .with_for_update()
        )
        if dispatch is None or dispatch.job_id != job_id:
            raise DispatchIdentityError(
                f"Execution {execution.id} generation {dispatch_generation} "
                "does not match its persisted delivery"
            )

        started_at = session.scalar(select(func.now()))
        if not isinstance(started_at, datetime):
            raise RuntimeError("PostgreSQL did not return its current timestamp")
        lease_expires_at = started_at + timedelta(
            seconds=experiment.job_timeout_seconds + LEASE_RECOVERY_GRACE_SECONDS
        )

        if dispatch.delivery_kind == "resume":
            if experiment.provider != OPENAI_PROVIDER:
                raise DispatchIdentityError(
                    "Only real-provider executions may receive resume deliveries"
                )
            if execution.status != "retrying" or dispatch.attempt_number != execution.attempt_count:
                raise DispatchIdentityError(
                    f"Resume delivery for execution {execution.id} does not target "
                    "its current attempt"
                )
            attempt = session.scalar(
                select(ExecutionAttempt)
                .where(
                    ExecutionAttempt.case_execution_id == execution.id,
                    ExecutionAttempt.attempt_number == dispatch.attempt_number,
                )
                .with_for_update()
            )
            if attempt is None or attempt.status != "resume_scheduled":
                raise DispatchIdentityError(
                    f"Resume delivery for execution {execution.id} has no scheduled attempt"
                )
            attempt.status = "running"
            attempt.lease_expires_at = lease_expires_at
            attempt.completed_at = None
            execution.status = "running"
            execution.started_at = execution.started_at or started_at
            execution.completed_at = None
            execution.error_message = None
            execution.lease_expires_at = lease_expires_at
            reconcile_experiment_status(session, execution.experiment_id)
            return AttemptClaim(
                "claimed",
                attempt.attempt_number,
                job_id,
                dispatch_generation,
                experiment.provider,
                dispatch.delivery_kind,
            )

        if dispatch.delivery_kind != "attempt" or dispatch.attempt_number is not None:
            raise DispatchIdentityError(
                f"Execution {execution.id} has an invalid attempt delivery identity"
            )
        if execution.attempt_count >= experiment.max_attempts:
            failure_time = datetime.now(UTC)
            execution.status = "failed"
            execution.completed_at = failure_time
            execution.lease_expires_at = None
            execution.last_failure_kind = "attempt_budget_exhausted"
            execution.last_failure_retryable = False
            execution.error_message = "Execution attempt budget was already exhausted"
            reconcile_experiment_status(
                session,
                execution.experiment_id,
                completed_at=failure_time,
            )
            return AttemptClaim(
                "failed",
                execution.attempt_count or None,
                job_id,
                dispatch_generation,
                experiment.provider,
                dispatch.delivery_kind,
            )

        attempt_number = execution.attempt_count + 1
        execution.attempt_count = attempt_number
        execution.status = "running"
        execution.started_at = execution.started_at or started_at
        execution.completed_at = None
        execution.error_message = None
        execution.lease_expires_at = lease_expires_at
        session.add(
            ExecutionAttempt(
                case_execution_id=execution.id,
                attempt_number=attempt_number,
                dispatch_generation=dispatch_generation,
                job_id=job_id,
                status="running",
                step_count=0,
                started_at=started_at,
                lease_expires_at=lease_expires_at,
            )
        )
        reconcile_experiment_status(session, execution.experiment_id)
        return AttemptClaim(
            "claimed",
            attempt_number,
            job_id,
            dispatch_generation,
            experiment.provider,
            dispatch.delivery_kind,
        )


def _steps_from_error(error: BaseException) -> int:
    for attribute in ("attempted_steps", "steps_attempted"):
        value = getattr(error, attribute, None)
        if isinstance(value, int) and value >= 0:
            return value
    return 0


def _retry_delay_seconds(experiment: Experiment, attempt_number: int) -> int:
    multiplier = 1 << max(0, attempt_number - 1)
    return min(300, experiment.retry_backoff_seconds * multiplier)


def _finish_error(
    case_execution_id: UUID,
    claim: AttemptClaim,
    error: Exception,
    classification: FaultClassification,
) -> bool:
    """Persist one failed attempt and return whether another was scheduled."""

    if claim.attempt_number is None:
        return False
    with session_scope() as session:
        execution = session.scalar(
            select(CaseExecution).where(CaseExecution.id == case_execution_id).with_for_update()
        )
        if execution is None or execution.status in {"completed", "failed"}:
            return False
        if (
            execution.dispatch_generation != claim.dispatch_generation
            or execution.attempt_count != claim.attempt_number
            or execution.status != "running"
        ):
            return False
        experiment = session.get(Experiment, execution.experiment_id)
        if experiment is None:
            raise ExecutionNotFoundError("Execution experiment is missing")
        attempt = session.scalar(
            select(ExecutionAttempt)
            .where(
                ExecutionAttempt.case_execution_id == execution.id,
                ExecutionAttempt.attempt_number == claim.attempt_number,
            )
            .with_for_update()
        )
        if attempt is None or attempt.status != "running":
            return False

        failure_time = datetime.now(UTC)
        safe_error = f"{type(error).__name__}: {error}"[:1000]
        if experiment.provider == OPENAI_PROVIDER:
            latest_turn = session.scalar(
                select(ProviderTurn)
                .where(
                    ProviderTurn.case_execution_id == execution.id,
                    ProviderTurn.attempt_number == claim.attempt_number,
                )
                .order_by(ProviderTurn.sequence_number.desc())
                .limit(1)
                .with_for_update()
            )
            if latest_turn is not None and latest_turn.status == "request_started":
                safe_error = (
                    "Provider request crossed the send fence without durable response evidence"
                )
                classification = FaultClassification(
                    kind="provider_outcome_indeterminate",
                    retryable=False,
                )
                latest_turn.status = "indeterminate"
                latest_turn.retryable = False
                latest_turn.error_kind = classification.kind
                latest_turn.safe_error_message = safe_error
                latest_turn.completed_at = failure_time
                _append_trace(
                    session,
                    execution.id,
                    "provider_outcome_indeterminate",
                    {
                        "turn": latest_turn.sequence_number,
                        "client_request_id": latest_turn.client_request_id,
                        "failure_kind": classification.kind,
                        "retryable": False,
                    },
                )
            elif (
                latest_turn is not None
                and latest_turn.status in {"response_invalid", "failed", "indeterminate"}
                and latest_turn.error_kind is not None
            ):
                classification = FaultClassification(
                    kind=latest_turn.error_kind,
                    retryable=False,
                )
                if latest_turn.safe_error_message is not None:
                    safe_error = latest_turn.safe_error_message[:1000]
        attempt.retryable = classification.retryable
        attempt.failure_kind = classification.kind
        attempt.error_message = safe_error
        attempt.step_count = max(attempt.step_count, _steps_from_error(error))
        attempt.completed_at = failure_time
        execution.last_failure_kind = classification.kind
        execution.last_failure_retryable = classification.retryable
        execution.error_message = safe_error
        execution.lease_expires_at = None

        should_retry = (
            experiment.provider != OPENAI_PROVIDER
            and classification.retryable
            and claim.attempt_number < experiment.max_attempts
        )
        if should_retry:
            attempt.status = "retry_scheduled"
            execution.status = "retrying"
            execution.completed_at = None
            execution.dispatch_generation += 1
            delay_seconds = _retry_delay_seconds(experiment, claim.attempt_number)
            create_dispatch(
                session,
                execution.id,
                generation=execution.dispatch_generation,
                available_at=failure_time + timedelta(seconds=delay_seconds),
            )
            _append_trace(
                session,
                execution.id,
                "execution_retry_scheduled",
                {
                    "attempt_number": claim.attempt_number,
                    "failure_kind": classification.kind,
                    "retryable": True,
                    "next_dispatch_generation": execution.dispatch_generation,
                    "delay_seconds": delay_seconds,
                },
            )
            reconcile_experiment_status(session, execution.experiment_id)
            return True

        attempt.status = "failed"
        execution.status = "failed"
        execution.completed_at = failure_time
        _append_trace(
            session,
            execution.id,
            "execution_failed",
            {
                "error_type": type(error).__name__,
                "message": str(error)[:500],
                "failure_kind": classification.kind,
                "retryable": classification.retryable,
                "attempt_number": claim.attempt_number,
                "attempts_exhausted": classification.retryable,
            },
        )
        reconcile_experiment_status(
            session,
            execution.experiment_id,
            completed_at=failure_time,
        )
        return False


def _terminalize_real_provider_attempt(
    session: Session,
    execution: CaseExecution,
    attempt: ExecutionAttempt,
    *,
    failed_at: datetime,
    failure_kind: str,
    message: str,
    error_type: str,
    attempts_exhausted: bool = False,
) -> str:
    safe_message = message[:1000]
    attempt.status = "failed"
    attempt.retryable = False
    attempt.failure_kind = failure_kind[:50]
    attempt.error_message = safe_message
    attempt.completed_at = failed_at
    execution.status = "failed"
    execution.completed_at = failed_at
    execution.lease_expires_at = None
    execution.last_failure_kind = failure_kind[:50]
    execution.last_failure_retryable = False
    execution.error_message = safe_message
    _append_trace(
        session,
        execution.id,
        "execution_failed",
        {
            "error_type": error_type,
            "message": safe_message[:500],
            "failure_kind": failure_kind[:50],
            "retryable": False,
            "attempt_number": attempt.attempt_number,
            "attempts_exhausted": attempts_exhausted,
        },
    )
    reconcile_experiment_status(
        session,
        execution.experiment_id,
        completed_at=failed_at,
    )
    return "failed"


def _schedule_real_provider_resume(
    session: Session,
    execution: CaseExecution,
    experiment: Experiment,
    attempt: ExecutionAttempt,
    *,
    database_now: datetime,
    preclaim_interruption: bool,
) -> str:
    latest_turn = session.scalar(
        select(ProviderTurn)
        .where(
            ProviderTurn.case_execution_id == execution.id,
            ProviderTurn.attempt_number == attempt.attempt_number,
        )
        .order_by(ProviderTurn.sequence_number.desc())
        .limit(1)
        .with_for_update()
    )
    latest_state = None if latest_turn is None else latest_turn.status

    if latest_turn is not None and latest_turn.status == "request_started":
        failure_kind = "provider_outcome_indeterminate"
        message = "Provider request crossed the send fence without durable response evidence"
        latest_turn.status = "indeterminate"
        latest_turn.retryable = False
        latest_turn.error_kind = failure_kind
        latest_turn.safe_error_message = message
        latest_turn.completed_at = database_now
        _append_trace(
            session,
            execution.id,
            "provider_outcome_indeterminate",
            {
                "turn": latest_turn.sequence_number,
                "client_request_id": latest_turn.client_request_id,
                "failure_kind": failure_kind,
                "retryable": False,
            },
        )
        return _terminalize_real_provider_attempt(
            session,
            execution,
            attempt,
            failed_at=database_now,
            failure_kind=failure_kind,
            message=message,
            error_type="ProviderOutcomeIndeterminate",
        )

    if latest_state is not None and latest_state not in SAFE_PROVIDER_RESUME_STATES:
        failure_kind = (
            latest_turn.error_kind
            if latest_turn is not None and latest_turn.error_kind is not None
            else f"provider_turn_{latest_state}"
        )
        message = (
            latest_turn.safe_error_message
            if latest_turn is not None and latest_turn.safe_error_message is not None
            else f"Provider turn is terminal and cannot resume: status={latest_state}"
        )
        return _terminalize_real_provider_attempt(
            session,
            execution,
            attempt,
            failed_at=database_now,
            failure_kind=failure_kind,
            message=message,
            error_type="TerminalProviderTurn",
        )

    if attempt.recovery_count >= MAX_SAME_ATTEMPT_RECOVERIES:
        return _terminalize_real_provider_attempt(
            session,
            execution,
            attempt,
            failed_at=database_now,
            failure_kind="worker_recovery_exhausted",
            message=(
                "Real-provider attempt exhausted its bounded same-attempt worker recovery budget"
            ),
            error_type="WorkerRecoveryExhausted",
            attempts_exhausted=True,
        )

    attempt.recovery_count += 1
    attempt.status = "resume_scheduled"
    attempt.completed_at = None
    execution.status = "retrying"
    execution.completed_at = None
    execution.lease_expires_at = None
    execution.last_failure_kind = "worker_interruption"
    execution.last_failure_retryable = True
    execution.error_message = "Worker stopped at a durable real-provider recovery boundary"
    execution.dispatch_generation += 1
    delay_seconds = _retry_delay_seconds(experiment, attempt.recovery_count)
    create_dispatch(
        session,
        execution.id,
        generation=execution.dispatch_generation,
        available_at=database_now + timedelta(seconds=delay_seconds),
        delivery_kind="resume",
        attempt_number=attempt.attempt_number,
    )
    _append_trace(
        session,
        execution.id,
        "execution_recovery_scheduled",
        {
            "attempt_number": attempt.attempt_number,
            "failure_kind": "worker_interruption",
            "retryable": True,
            "next_dispatch_generation": execution.dispatch_generation,
            "delay_seconds": delay_seconds,
            "preclaim_interruption": preclaim_interruption,
            "same_attempt_resume": True,
            "recovery_count": attempt.recovery_count,
            "provider_turn_state": latest_state or "none",
        },
    )
    reconcile_experiment_status(session, execution.experiment_id)
    return "recovered"


def _recover_real_provider_execution(
    session: Session,
    execution: CaseExecution,
    experiment: Experiment,
    *,
    database_now: datetime,
    cutoff: datetime | None,
    force: bool,
    expected_generation: int | None,
    expected_job_id: str | None,
) -> str:
    preclaim_interruption = execution.status in {"queued", "retrying", "enqueue_failed"}
    if preclaim_interruption:
        if not force or expected_generation is None or expected_job_id is None:
            return "fresh_or_locked"
        if execution.dispatch_generation != expected_generation:
            return "fresh_or_locked"
    elif execution.status == "running":
        effective_cutoff = cutoff or database_now
        if not force and (
            execution.lease_expires_at is None or execution.lease_expires_at > effective_cutoff
        ):
            return "fresh_or_locked"
    else:
        return "fresh_or_locked"

    dispatch = session.scalar(
        select(ExecutionDispatch)
        .where(
            ExecutionDispatch.case_execution_id == execution.id,
            ExecutionDispatch.generation == execution.dispatch_generation,
        )
        .with_for_update()
    )
    if dispatch is None:
        return "fresh_or_locked"
    if expected_generation is not None and dispatch.generation != expected_generation:
        return "fresh_or_locked"
    if expected_job_id is not None and dispatch.job_id != expected_job_id:
        return "fresh_or_locked"

    if preclaim_interruption and dispatch.delivery_kind == "attempt":
        if dispatch.attempt_number is not None:
            return "fresh_or_locked"
        if execution.attempt_count >= experiment.max_attempts:
            execution.status = "failed"
            execution.completed_at = database_now
            execution.lease_expires_at = None
            execution.last_failure_kind = "attempt_budget_exhausted"
            execution.last_failure_retryable = False
            execution.error_message = "Execution attempt budget was already exhausted"
            reconcile_experiment_status(
                session,
                execution.experiment_id,
                completed_at=database_now,
            )
            return "failed"
        execution.attempt_count += 1
        execution.started_at = execution.started_at or database_now
        attempt = ExecutionAttempt(
            case_execution_id=execution.id,
            attempt_number=execution.attempt_count,
            dispatch_generation=dispatch.generation,
            job_id=dispatch.job_id,
            status="running",
            step_count=0,
            recovery_count=0,
            started_at=database_now,
            lease_expires_at=database_now,
        )
        session.add(attempt)
    else:
        if preclaim_interruption:
            if (
                dispatch.delivery_kind != "resume"
                or dispatch.attempt_number != execution.attempt_count
            ):
                return "fresh_or_locked"
        elif dispatch.delivery_kind == "attempt":
            if dispatch.attempt_number is not None:
                return "fresh_or_locked"
        elif (
            dispatch.delivery_kind != "resume" or dispatch.attempt_number != execution.attempt_count
        ):
            return "fresh_or_locked"
        current_attempt = session.scalar(
            select(ExecutionAttempt)
            .where(
                ExecutionAttempt.case_execution_id == execution.id,
                ExecutionAttempt.attempt_number == execution.attempt_count,
            )
            .with_for_update()
        )
        if current_attempt is None:
            return "fresh_or_locked"
        expected_attempt_status = "resume_scheduled" if preclaim_interruption else "running"
        if current_attempt.status != expected_attempt_status:
            return "fresh_or_locked"
        if dispatch.delivery_kind == "attempt" and (
            current_attempt.dispatch_generation != dispatch.generation
            or current_attempt.job_id != dispatch.job_id
        ):
            return "fresh_or_locked"
        attempt = current_attempt

    return _schedule_real_provider_resume(
        session,
        execution,
        experiment,
        attempt,
        database_now=database_now,
        preclaim_interruption=preclaim_interruption,
    )


def _recover_one(
    case_execution_id: UUID,
    *,
    cutoff: datetime | None,
    force: bool,
    expected_generation: int | None = None,
    expected_job_id: str | None = None,
) -> str:
    with session_scope() as session:
        execution = session.scalar(
            select(CaseExecution)
            .where(CaseExecution.id == case_execution_id)
            .with_for_update(skip_locked=True)
        )
        if execution is None:
            return "fresh_or_locked"
        database_now = session.scalar(select(func.now()))
        if not isinstance(database_now, datetime):
            raise RuntimeError("PostgreSQL did not return its current timestamp")
        experiment = session.get(Experiment, execution.experiment_id)
        if experiment is None:
            raise ExecutionNotFoundError("Execution experiment is missing")

        if experiment.provider == OPENAI_PROVIDER:
            return _recover_real_provider_execution(
                session,
                execution,
                experiment,
                database_now=database_now,
                cutoff=cutoff,
                force=force,
                expected_generation=expected_generation,
                expected_job_id=expected_job_id,
            )

        preclaim_interruption = execution.status in {"queued", "retrying", "enqueue_failed"}
        if preclaim_interruption:
            if not force or expected_generation is None or expected_job_id is None:
                return "fresh_or_locked"
            if execution.dispatch_generation != expected_generation:
                return "fresh_or_locked"
            dispatch = session.scalar(
                select(ExecutionDispatch)
                .where(
                    ExecutionDispatch.case_execution_id == execution.id,
                    ExecutionDispatch.generation == expected_generation,
                )
                .with_for_update()
            )
            if dispatch is None or dispatch.job_id != expected_job_id:
                return "fresh_or_locked"
            if execution.attempt_count >= experiment.max_attempts:
                execution.status = "failed"
                execution.completed_at = database_now
                execution.lease_expires_at = None
                execution.last_failure_kind = "attempt_budget_exhausted"
                execution.last_failure_retryable = False
                execution.error_message = "Execution attempt budget was already exhausted"
                reconcile_experiment_status(
                    session,
                    execution.experiment_id,
                    completed_at=database_now,
                )
                return "failed"
            execution.attempt_count += 1
            execution.started_at = execution.started_at or database_now
            attempt = ExecutionAttempt(
                case_execution_id=execution.id,
                attempt_number=execution.attempt_count,
                dispatch_generation=expected_generation,
                job_id=expected_job_id,
                status="interrupted",
                retryable=True,
                failure_kind="worker_interruption",
                error_message="Worker stopped before committing a durable attempt claim",
                step_count=0,
                started_at=database_now,
                lease_expires_at=database_now,
                completed_at=database_now,
            )
            session.add(attempt)
        else:
            if execution.status != "running":
                return "fresh_or_locked"
            effective_cutoff = cutoff or database_now
            if not force and (
                execution.lease_expires_at is None or execution.lease_expires_at > effective_cutoff
            ):
                return "fresh_or_locked"
            running_attempt = session.scalar(
                select(ExecutionAttempt)
                .where(
                    ExecutionAttempt.case_execution_id == execution.id,
                    ExecutionAttempt.attempt_number == execution.attempt_count,
                )
                .with_for_update()
            )
            if running_attempt is None or running_attempt.status != "running":
                return "fresh_or_locked"
            if expected_generation is not None and (
                execution.dispatch_generation != expected_generation
                or running_attempt.dispatch_generation != expected_generation
            ):
                return "fresh_or_locked"
            if expected_job_id is not None and running_attempt.job_id != expected_job_id:
                return "fresh_or_locked"

            attempt = running_attempt
            attempt.status = "interrupted"
            attempt.retryable = True
            attempt.failure_kind = "worker_interruption"
            attempt.error_message = "Worker stopped before committing an execution outcome"
            attempt.completed_at = database_now

        message = str(attempt.error_message)
        execution.lease_expires_at = None
        execution.last_failure_kind = "worker_interruption"
        execution.last_failure_retryable = True
        execution.error_message = message

        if execution.attempt_count < experiment.max_attempts:
            execution.status = "retrying"
            execution.completed_at = None
            execution.dispatch_generation += 1
            delay_seconds = _retry_delay_seconds(experiment, execution.attempt_count)
            create_dispatch(
                session,
                execution.id,
                generation=execution.dispatch_generation,
                available_at=database_now + timedelta(seconds=delay_seconds),
            )
            _append_trace(
                session,
                execution.id,
                "execution_recovery_scheduled",
                {
                    "attempt_number": execution.attempt_count,
                    "failure_kind": "worker_interruption",
                    "retryable": True,
                    "next_dispatch_generation": execution.dispatch_generation,
                    "delay_seconds": delay_seconds,
                    "preclaim_interruption": preclaim_interruption,
                },
            )
            reconcile_experiment_status(session, execution.experiment_id)
            return "recovered"

        execution.status = "failed"
        execution.completed_at = database_now
        _append_trace(
            session,
            execution.id,
            "execution_failed",
            {
                "error_type": "WorkerInterruption",
                "message": message,
                "failure_kind": "worker_interruption",
                "retryable": True,
                "attempt_number": execution.attempt_count,
                "attempts_exhausted": True,
            },
        )
        reconcile_experiment_status(
            session,
            execution.experiment_id,
            completed_at=database_now,
        )
        return "failed"


def recover_stale_executions(
    *,
    limit: int = 100,
    cutoff: datetime | None = None,
) -> RecoverySummary:
    """Reclaim expired running attempts without racing a live transaction."""

    if limit < 1:
        raise ValueError("Recovery limit must be at least 1")
    with session_scope() as session:
        cutoff_expression = cutoff if cutoff is not None else func.now()
        execution_ids = list(
            session.scalars(
                select(CaseExecution.id)
                .where(
                    CaseExecution.status == "running",
                    CaseExecution.lease_expires_at.is_not(None),
                    CaseExecution.lease_expires_at <= cutoff_expression,
                )
                .order_by(CaseExecution.lease_expires_at, CaseExecution.id)
                .limit(limit)
            ).all()
        )

    counts = {"recovered": 0, "failed": 0, "fresh_or_locked": 0}
    for execution_id in execution_ids:
        outcome = _recover_one(execution_id, cutoff=cutoff, force=False)
        counts[outcome] += 1
    return RecoverySummary(
        examined=len(execution_ids),
        recovered=counts["recovered"],
        failed=counts["failed"],
        fresh_or_locked=counts["fresh_or_locked"],
    )


def recover_interrupted_execution(
    case_execution_id: UUID,
    dispatch_generation: int,
    job_id: str,
) -> str:
    """Immediately recover a delivery known to have lost its work horse."""

    return _recover_one(
        case_execution_id,
        cutoff=None,
        force=True,
        expected_generation=dispatch_generation,
        expected_job_id=job_id,
    )


def validate_delivery_identity(
    case_execution_id: UUID,
    dispatch_generation: int,
    current_job: Job,
) -> None:
    """Validate the live RQ delivery before it can consume an attempt."""

    with session_scope() as session:
        execution = session.get(CaseExecution, case_execution_id)
        if execution is None:
            raise ExecutionNotFoundError(str(case_execution_id))
        experiment = session.get(Experiment, execution.experiment_id)
        if experiment is None:
            raise ExecutionNotFoundError("Execution experiment is missing")
        dispatch = session.scalar(
            select(ExecutionDispatch).where(
                ExecutionDispatch.case_execution_id == case_execution_id,
                ExecutionDispatch.generation == dispatch_generation,
            )
        )
        if dispatch is None:
            raise DispatchIdentityError(
                f"Execution {case_execution_id} generation {dispatch_generation} "
                "has no persisted dispatch"
            )
        validate_execution_job(
            current_job,
            queue=get_queue(current_job.connection),
            case_execution_id=case_execution_id,
            generation=dispatch_generation,
            job_timeout_seconds=experiment.job_timeout_seconds,
            expected_job_id=dispatch.job_id,
        )


def run_case_execution(
    case_execution_id: str,
    dispatch_generation: int = 1,
) -> dict[str, Any]:
    """Run one durable attempt or safely ignore a duplicate/stale delivery."""

    settings = get_settings()
    configure_logging(settings.log_level)
    execution_uuid = UUID(case_execution_id)
    current_job = get_current_job()
    if current_job is not None:
        validate_delivery_identity(execution_uuid, dispatch_generation, current_job)
    job_id = (
        current_job.id
        if current_job is not None
        else job_id_for_execution(execution_uuid, dispatch_generation)
    )
    try:
        claim = _claim_attempt(execution_uuid, dispatch_generation, job_id)
    except Exception:
        try:
            recovery_outcome = recover_interrupted_execution(
                execution_uuid,
                dispatch_generation,
                job_id,
            )
            logger.warning(
                "Recovered execution after a pre-claim job failure",
                extra={
                    "event": "preclaim_execution_recovered",
                    "execution_id": case_execution_id,
                    "outcome": recovery_outcome,
                },
            )
        except Exception:
            logger.exception(
                "Pre-claim recovery deferred to durable dispatch maintenance",
                extra={
                    "event": "preclaim_execution_recovery_deferred",
                    "execution_id": case_execution_id,
                },
            )
        raise
    if claim.state == "completed":
        return {"execution_id": case_execution_id, "status": "completed", "replayed": True}
    if claim.state == "failed":
        return {"execution_id": case_execution_id, "status": "failed", "replayed": True}
    if claim.state in {"busy", "stale"}:
        return {
            "execution_id": case_execution_id,
            "status": claim.state,
            "replayed": True,
            "duplicate_delivery": True,
        }
    if claim.attempt_number is None:
        raise RuntimeError("A claimed execution must have an attempt number")

    try:
        if claim.provider == OPENAI_PROVIDER:
            result = execute_openai_case(
                execution_uuid,
                expected_attempt_number=claim.attempt_number,
                expected_dispatch_generation=claim.dispatch_generation,
            )
        else:
            with session_scope() as session:
                result = execute_case(
                    session,
                    execution_uuid,
                    expected_attempt_number=claim.attempt_number,
                    expected_dispatch_generation=claim.dispatch_generation,
                )
    except StaleExecutionClaimError:
        logger.warning(
            "Ignored execution work whose durable claim was already fenced",
            extra={
                "event": "stale_execution_claim_ignored",
                "execution_id": case_execution_id,
                "attempt_number": claim.attempt_number,
                "dispatch_generation": claim.dispatch_generation,
            },
        )
        return {
            "execution_id": case_execution_id,
            "status": "stale",
            "replayed": True,
            "duplicate_delivery": True,
        }
    except Exception as exc:
        classification = classify_failure(exc)
        retry_scheduled = _finish_error(execution_uuid, claim, exc, classification)
        if retry_scheduled:
            if not isinstance(exc, JobTimeoutException):
                reconcile_pending_dispatches(execution_ids=[execution_uuid])
            logger.warning(
                "Case execution retry scheduled",
                extra={
                    "event": "case_execution_retry_scheduled",
                    "execution_id": case_execution_id,
                    "failure_kind": classification.kind,
                },
            )
            retry_result = {
                "execution_id": case_execution_id,
                "status": "retrying",
                "attempt_number": claim.attempt_number,
                "failure_kind": classification.kind,
            }
            if isinstance(exc, JobTimeoutException):
                raise
            return retry_result
        logger.exception(
            "Case execution failed",
            extra={"event": "case_execution_failed", "execution_id": case_execution_id},
        )
        raise
    logger.info(
        "Case execution completed",
        extra={"event": "case_execution_completed", "execution_id": case_execution_id},
    )
    return result
