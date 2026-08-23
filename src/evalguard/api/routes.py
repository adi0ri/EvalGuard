"""Suite, experiment, and execution endpoints through Phase 4."""

import hashlib
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from evalguard.api.schemas import (
    CustomerRead,
    ExecutionAttemptRead,
    ExecutionBrief,
    ExecutionDetail,
    ExecutionDispatchRead,
    ExperimentAccepted,
    ExperimentCreate,
    ExperimentDetail,
    GraderResultRead,
    OrderRead,
    RefundRead,
    ShippingAddressRead,
    SuiteSummary,
    SupportTicketRead,
    ToolInvocationRead,
    TraceEventRead,
)
from evalguard.core.config import get_settings
from evalguard.db.models import (
    CaseExecution,
    Customer,
    EvaluationCase,
    EvaluationSuite,
    ExecutionAttempt,
    ExecutionDispatch,
    ExecutionFaultPlan,
    Experiment,
    GraderResult,
    Order,
    Refund,
    SupportTicket,
    ToolInvocation,
    TraceEvent,
)
from evalguard.db.session import get_db
from evalguard.workers.dispatch import create_dispatch, reconcile_pending_dispatches

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")
SessionDependency = Annotated[Session, Depends(get_db)]


def _idempotency_advisory_lock_id(idempotency_key: str) -> int:
    """Map an opaque request key to PostgreSQL's signed 64-bit lock namespace."""
    digest = hashlib.sha256(f"evalguard:experiment:{idempotency_key}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


@contextmanager
def _idempotency_submission_session(
    session: Session,
    idempotency_key: str,
) -> Iterator[Session]:
    """Use one connection for key serialization and durable acceptance."""

    lock_id = _idempotency_advisory_lock_id(idempotency_key)
    bind = session.get_bind()
    if not isinstance(bind, Engine):
        raise RuntimeError("Idempotency locking requires an engine-bound database session")

    with bind.connect() as lock_connection:
        locked_session = Session(bind=lock_connection, expire_on_commit=False)
        locked_session.execute(select(func.pg_advisory_lock(lock_id)))
        locked_session.commit()
        try:
            yield locked_session
        finally:
            try:
                locked_session.rollback()
                unlocked = locked_session.scalar(select(func.pg_advisory_unlock(lock_id)))
                locked_session.commit()
                if unlocked is not True:
                    logger.error(
                        "Failed to release experiment idempotency lock",
                        extra={"event": "idempotency_unlock_failed", "lock_id": lock_id},
                    )
            finally:
                locked_session.close()


def _shipping_address(order: Order) -> ShippingAddressRead | None:
    line1 = order.shipping_address_line1
    city = order.shipping_city
    region = order.shipping_region
    postal_code = order.shipping_postal_code
    country = order.shipping_country
    if line1 is None or city is None or region is None or postal_code is None or country is None:
        return None
    return ShippingAddressRead(
        line1=line1,
        line2=order.shipping_address_line2,
        city=city,
        region=region,
        postal_code=postal_code,
        country=country,
    )


def _execution_ids(session: Session, experiment_id: UUID) -> list[UUID]:
    return list(
        session.scalars(
            select(CaseExecution.id)
            .join(EvaluationCase, EvaluationCase.id == CaseExecution.case_id)
            .where(CaseExecution.experiment_id == experiment_id)
            .order_by(
                EvaluationCase.key,
                CaseExecution.trial_number,
                CaseExecution.id,
            )
        ).all()
    )


def _accepted(session: Session, experiment: Experiment) -> ExperimentAccepted:
    return ExperimentAccepted(
        id=experiment.id,
        status=experiment.status,
        execution_ids=_execution_ids(session, experiment.id),
    )


def _execution_rows(session: Session, experiment_id: UUID) -> list[CaseExecution]:
    return list(
        session.scalars(
            select(CaseExecution)
            .join(EvaluationCase, EvaluationCase.id == CaseExecution.case_id)
            .where(CaseExecution.experiment_id == experiment_id)
            .order_by(
                EvaluationCase.key,
                CaseExecution.trial_number,
                CaseExecution.id,
            )
        ).all()
    )


def _fault_intent(
    session: Session,
    experiment_id: UUID,
) -> tuple[str, str, int] | None:
    row = session.execute(
        select(EvaluationCase.key, ExecutionFaultPlan.mode, ExecutionFaultPlan.fail_attempts)
        .join(CaseExecution, CaseExecution.case_id == EvaluationCase.id)
        .join(
            ExecutionFaultPlan,
            ExecutionFaultPlan.case_execution_id == CaseExecution.id,
        )
        .where(CaseExecution.experiment_id == experiment_id)
    ).one_or_none()
    if row is None:
        return None
    return str(row.key), str(row.mode), int(row.fail_attempts)


def _request_matches_experiment(
    session: Session,
    experiment: Experiment,
    suite_id: UUID,
    payload: ExperimentCreate,
) -> bool:
    requested_fault = payload.fault_injection
    fault_intent = _fault_intent(session, experiment.id)
    expected_fault = (
        None
        if requested_fault is None
        else (
            requested_fault.case_key,
            requested_fault.mode.value,
            requested_fault.fail_attempts,
        )
    )
    return (
        experiment.suite_id == suite_id
        and experiment.provider == payload.provider
        and experiment.max_attempts == payload.max_attempts
        and experiment.job_timeout_seconds == payload.job_timeout_seconds
        and experiment.max_tool_steps == payload.max_tool_steps
        and experiment.retry_backoff_seconds == payload.retry_backoff_seconds
        and fault_intent == expected_fault
    )


def _best_effort_dispatch(execution_ids: list[UUID]) -> None:
    """Reduce queue latency while leaving PostgreSQL as the durable acceptance point."""

    try:
        reconcile_pending_dispatches(
            execution_ids=execution_ids,
            force_initial=True,
        )
    except Exception:
        logger.exception(
            "Immediate dispatch deferred to worker reconciliation",
            extra={"event": "experiment_dispatch_deferred"},
        )


@router.get("/suites", response_model=list[SuiteSummary])
def list_suites(session: SessionDependency) -> list[SuiteSummary]:
    suites = session.scalars(
        select(EvaluationSuite)
        .where(EvaluationSuite.is_published.is_(True))
        .order_by(EvaluationSuite.name, EvaluationSuite.version)
    ).all()
    return [
        SuiteSummary(
            id=suite.id,
            name=suite.name,
            version=suite.version,
            description=suite.description,
            definition_hash=suite.definition_hash,
            case_count=int(
                session.scalar(
                    select(func.count(EvaluationCase.id)).where(EvaluationCase.suite_id == suite.id)
                )
                or 0
            ),
        )
        for suite in suites
    ]


def _create_experiment(
    payload: ExperimentCreate,
    response: Response,
    session: Session,
    idempotency_key: str | None,
) -> ExperimentAccepted:
    suite = session.scalar(
        select(EvaluationSuite).where(
            EvaluationSuite.name == payload.suite_name,
            EvaluationSuite.version == payload.suite_version,
            EvaluationSuite.is_published.is_(True),
        )
    )
    if suite is None:
        raise HTTPException(status_code=404, detail="Evaluation suite version not found")

    cases = list(
        session.scalars(
            select(EvaluationCase)
            .where(EvaluationCase.suite_id == suite.id)
            .order_by(EvaluationCase.key)
        ).all()
    )
    if not cases:
        raise HTTPException(status_code=409, detail="Evaluation suite has no cases")
    case_keys = {case.key for case in cases}
    requested_fault = payload.fault_injection
    if requested_fault is not None:
        if requested_fault.case_key not in case_keys:
            raise HTTPException(
                status_code=422,
                detail="Fault injection case_key is not part of the selected suite",
            )
        if get_settings().environment.lower() not in {"development", "test"}:
            raise HTTPException(
                status_code=403,
                detail="Deterministic fault injection is disabled in this environment",
            )

    if idempotency_key is not None:
        existing = session.scalar(
            select(Experiment).where(Experiment.idempotency_key == idempotency_key)
        )
        if existing is not None:
            if not _request_matches_experiment(session, existing, suite.id, payload):
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency-Key was already used for a different experiment",
                )
            response.headers["Location"] = f"/api/v1/experiments/{existing.id}"
            return _accepted(session, existing)

    experiment = Experiment(
        suite_id=suite.id,
        provider=payload.provider,
        status="queued",
        idempotency_key=idempotency_key,
        max_attempts=payload.max_attempts,
        job_timeout_seconds=payload.job_timeout_seconds,
        max_tool_steps=payload.max_tool_steps,
        retry_backoff_seconds=payload.retry_backoff_seconds,
    )
    session.add(experiment)
    session.flush()
    executions = [
        CaseExecution(
            experiment_id=experiment.id,
            case_id=case.id,
            trial_number=1,
            status="queued",
        )
        for case in cases
    ]
    session.add_all(executions)
    session.flush()
    for execution, case in zip(executions, cases, strict=True):
        create_dispatch(session, execution.id, generation=1)
        if requested_fault is not None and case.key == requested_fault.case_key:
            session.add(
                ExecutionFaultPlan(
                    case_execution_id=execution.id,
                    mode=requested_fault.mode.value,
                    fail_attempts=requested_fault.fail_attempts,
                )
            )
    session.commit()
    session.refresh(experiment)

    response.headers["Location"] = f"/api/v1/experiments/{experiment.id}"
    return _accepted(session, experiment)


@router.post(
    "/experiments",
    response_model=ExperimentAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_experiment(
    payload: ExperimentCreate,
    response: Response,
    session: SessionDependency,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ] = None,
) -> ExperimentAccepted:
    if idempotency_key is None:
        accepted = _create_experiment(payload, response, session, idempotency_key)
    else:
        with _idempotency_submission_session(session, idempotency_key) as locked_session:
            accepted = _create_experiment(payload, response, locked_session, idempotency_key)

    _best_effort_dispatch(accepted.execution_ids)
    return accepted


@router.get("/experiments/{experiment_id}", response_model=ExperimentDetail)
def get_experiment(experiment_id: UUID, session: SessionDependency) -> ExperimentDetail:
    experiment = session.get(Experiment, experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    suite = session.get(EvaluationSuite, experiment.suite_id)
    if suite is None:
        raise HTTPException(status_code=500, detail="Experiment suite is missing")

    rows = session.execute(
        select(CaseExecution, EvaluationCase)
        .join(EvaluationCase, EvaluationCase.id == CaseExecution.case_id)
        .where(CaseExecution.experiment_id == experiment.id)
        .order_by(EvaluationCase.key, CaseExecution.trial_number)
    ).all()
    return ExperimentDetail(
        id=experiment.id,
        suite_id=suite.id,
        suite_name=suite.name,
        suite_version=suite.version,
        provider=experiment.provider,
        status=experiment.status,
        idempotency_key=experiment.idempotency_key,
        max_attempts=experiment.max_attempts,
        job_timeout_seconds=experiment.job_timeout_seconds,
        max_tool_steps=experiment.max_tool_steps,
        retry_backoff_seconds=experiment.retry_backoff_seconds,
        created_at=experiment.created_at,
        completed_at=experiment.completed_at,
        executions=[
            ExecutionBrief(
                id=execution.id,
                case_id=case.id,
                case_key=case.key,
                trial_number=execution.trial_number,
                status=execution.status,
                attempt_count=execution.attempt_count,
            )
            for execution, case in rows
        ],
    )


def _execution_records(session: Session, case_execution_id: UUID) -> dict[str, Any]:
    return {
        "customers": session.scalars(
            select(Customer)
            .where(Customer.case_execution_id == case_execution_id)
            .order_by(Customer.external_id)
        ).all(),
        "orders": session.scalars(
            select(Order)
            .where(Order.case_execution_id == case_execution_id)
            .order_by(Order.external_id)
        ).all(),
        "refunds": session.scalars(
            select(Refund)
            .where(Refund.case_execution_id == case_execution_id)
            .order_by(Refund.created_at, Refund.id)
        ).all(),
        "support_tickets": session.scalars(
            select(SupportTicket)
            .where(SupportTicket.case_execution_id == case_execution_id)
            .order_by(SupportTicket.created_at, SupportTicket.id)
        ).all(),
        "attempts": session.scalars(
            select(ExecutionAttempt)
            .where(ExecutionAttempt.case_execution_id == case_execution_id)
            .order_by(ExecutionAttempt.attempt_number)
        ).all(),
        "dispatches": session.scalars(
            select(ExecutionDispatch)
            .where(ExecutionDispatch.case_execution_id == case_execution_id)
            .order_by(ExecutionDispatch.generation)
        ).all(),
        "trace_events": session.scalars(
            select(TraceEvent)
            .where(TraceEvent.case_execution_id == case_execution_id)
            .order_by(TraceEvent.sequence_number)
        ).all(),
        "tool_invocations": session.scalars(
            select(ToolInvocation)
            .where(ToolInvocation.case_execution_id == case_execution_id)
            .order_by(ToolInvocation.started_at, ToolInvocation.id)
        ).all(),
        "grader_results": session.scalars(
            select(GraderResult)
            .where(GraderResult.case_execution_id == case_execution_id)
            .order_by(GraderResult.criterion)
        ).all(),
    }


@router.get("/executions/{execution_id}", response_model=ExecutionDetail)
def get_execution(execution_id: UUID, session: SessionDependency) -> ExecutionDetail:
    execution = session.get(CaseExecution, execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Case execution not found")
    case = session.get(EvaluationCase, execution.case_id)
    if case is None:
        raise HTTPException(status_code=500, detail="Evaluation case is missing")
    experiment = session.get(Experiment, execution.experiment_id)
    if experiment is None:
        raise HTTPException(status_code=500, detail="Execution experiment is missing")
    records = _execution_records(session, execution.id)

    return ExecutionDetail(
        id=execution.id,
        experiment_id=execution.experiment_id,
        case_id=execution.case_id,
        case_key=case.key,
        trial_number=execution.trial_number,
        status=execution.status,
        attempt_count=execution.attempt_count,
        max_attempts=experiment.max_attempts,
        job_timeout_seconds=experiment.job_timeout_seconds,
        max_tool_steps=experiment.max_tool_steps,
        retry_backoff_seconds=experiment.retry_backoff_seconds,
        dispatch_generation=execution.dispatch_generation,
        lease_expires_at=execution.lease_expires_at,
        last_failure_kind=execution.last_failure_kind,
        last_failure_retryable=execution.last_failure_retryable,
        final_resolution=execution.final_resolution,
        error_message=execution.error_message,
        created_at=execution.created_at,
        started_at=execution.started_at,
        completed_at=execution.completed_at,
        customers=[
            CustomerRead(external_id=row.external_id, name=row.name, email=row.email)
            for row in records["customers"]
        ],
        orders=[
            OrderRead(
                external_id=row.external_id,
                customer_id=row.customer_id,
                total_cents=row.total_cents,
                currency=row.currency,
                status=row.status,
                shipping_address=_shipping_address(row),
            )
            for row in records["orders"]
        ],
        refunds=[
            RefundRead(
                id=row.id,
                order_id=row.order_id,
                amount_cents=row.amount_cents,
                currency=row.currency,
                status=row.status,
                reason=row.reason,
                tool_call_id=row.tool_call_id,
            )
            for row in records["refunds"]
        ],
        support_tickets=[
            SupportTicketRead(
                id=row.id,
                case_execution_id=row.case_execution_id,
                customer_id=row.customer_id,
                order_id=row.order_id,
                reason_code=row.reason_code,
                reason=row.reason,
                priority=row.priority,
                status=row.status,
                tool_call_id=row.tool_call_id,
                created_at=row.created_at,
                escalated_at=row.escalated_at,
            )
            for row in records["support_tickets"]
        ],
        attempts=[
            ExecutionAttemptRead(
                attempt_number=row.attempt_number,
                dispatch_generation=row.dispatch_generation,
                job_id=row.job_id,
                status=row.status,
                retryable=row.retryable,
                failure_kind=row.failure_kind,
                error_message=row.error_message,
                step_count=row.step_count,
                started_at=row.started_at,
                lease_expires_at=row.lease_expires_at,
                completed_at=row.completed_at,
            )
            for row in records["attempts"]
        ],
        dispatches=[
            ExecutionDispatchRead(
                generation=row.generation,
                job_id=row.job_id,
                status=row.status,
                attempt_count=row.attempt_count,
                available_at=row.available_at,
                last_attempt_at=row.last_attempt_at,
                dispatched_at=row.dispatched_at,
                last_error_kind=row.last_error_kind,
            )
            for row in records["dispatches"]
        ],
        trace_events=[
            TraceEventRead(
                sequence_number=row.sequence_number,
                event_type=row.event_type,
                payload=row.payload,
                created_at=row.created_at,
            )
            for row in records["trace_events"]
        ],
        tool_invocations=[
            ToolInvocationRead(
                tool_call_id=row.tool_call_id,
                tool_name=row.tool_name,
                arguments=row.arguments,
                result=row.result,
                status=row.status,
            )
            for row in records["tool_invocations"]
        ],
        grader_results=[
            GraderResultRead(
                grader_name=row.grader_name,
                criterion=row.criterion,
                passed=row.passed,
                message=row.message,
                details=row.details,
            )
            for row in records["grader_results"]
        ],
    )
