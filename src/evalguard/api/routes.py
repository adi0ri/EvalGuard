"""Phase 2 suite, experiment, and execution endpoints."""

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
    ExecutionBrief,
    ExecutionDetail,
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
from evalguard.db.models import (
    CaseExecution,
    Customer,
    EvaluationCase,
    EvaluationSuite,
    Experiment,
    GraderResult,
    Order,
    Refund,
    SupportTicket,
    ToolInvocation,
    TraceEvent,
)
from evalguard.db.session import get_db
from evalguard.workers.queue import enqueue_execution

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
    """Use one connection for key serialization, persistence, and dispatch status."""

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
            .where(CaseExecution.experiment_id == experiment_id)
            .order_by(CaseExecution.created_at, CaseExecution.id)
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
            .where(CaseExecution.experiment_id == experiment_id)
            .order_by(CaseExecution.created_at, CaseExecution.id)
        ).all()
    )


def _dispatch_executions(
    session: Session,
    experiment: Experiment,
    executions: list[CaseExecution],
) -> None:
    try:
        for execution in executions:
            enqueue_execution(execution.id)
    except Exception as exc:
        logger.exception(
            "Failed to enqueue experiment",
            extra={"event": "experiment_enqueue_failed", "experiment_id": experiment.id},
        )
        session.rollback()
        persisted_experiment = session.scalar(
            select(Experiment)
            .where(Experiment.id == experiment.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if persisted_experiment is not None:
            persisted_executions = list(
                session.scalars(
                    select(CaseExecution)
                    .where(CaseExecution.experiment_id == persisted_experiment.id)
                    .order_by(CaseExecution.created_at, CaseExecution.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                ).all()
            )
            accepted_by_worker = persisted_experiment.status in {"running", "completed"} or any(
                row.status in {"running", "completed"} for row in persisted_executions
            )
            if accepted_by_worker:
                session.commit()
                return
            persisted_experiment.status = "enqueue_failed"
            for persisted_execution in persisted_executions:
                if persisted_execution.status == "queued":
                    persisted_execution.status = "enqueue_failed"
                    persisted_execution.error_message = type(exc).__name__
            session.commit()
        raise HTTPException(status_code=503, detail="Unable to enqueue experiment") from exc


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

    if idempotency_key is not None:
        existing = session.scalar(
            select(Experiment).where(Experiment.idempotency_key == idempotency_key)
        )
        if existing is not None:
            if existing.suite_id != suite.id or existing.provider != payload.provider:
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency-Key was already used for a different experiment",
                )
            if existing.status == "enqueue_failed":
                executions = _execution_rows(session, existing.id)
                existing.status = "queued"
                existing.completed_at = None
                for execution in executions:
                    execution.status = "queued"
                    execution.error_message = None
                    execution.completed_at = None
                session.commit()
                _dispatch_executions(session, existing, executions)
                session.refresh(existing)
            response.headers["Location"] = f"/api/v1/experiments/{existing.id}"
            return _accepted(session, existing)

    cases = session.scalars(
        select(EvaluationCase)
        .where(EvaluationCase.suite_id == suite.id)
        .order_by(EvaluationCase.key)
    ).all()
    if not cases:
        raise HTTPException(status_code=409, detail="Evaluation suite has no cases")

    experiment = Experiment(
        suite_id=suite.id,
        provider=payload.provider,
        status="queued",
        idempotency_key=idempotency_key,
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
    session.commit()

    _dispatch_executions(session, experiment, executions)
    session.refresh(experiment)

    response.headers["Location"] = f"/api/v1/experiments/{experiment.id}"
    return ExperimentAccepted(
        id=experiment.id,
        status=experiment.status,
        execution_ids=[execution.id for execution in executions],
    )


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
        return _create_experiment(payload, response, session, idempotency_key)

    with _idempotency_submission_session(session, idempotency_key) as locked_session:
        return _create_experiment(payload, response, locked_session, idempotency_key)


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
        created_at=experiment.created_at,
        completed_at=experiment.completed_at,
        executions=[
            ExecutionBrief(
                id=execution.id,
                case_id=case.id,
                case_key=case.key,
                trial_number=execution.trial_number,
                status=execution.status,
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
    records = _execution_records(session, execution.id)

    return ExecutionDetail(
        id=execution.id,
        experiment_id=execution.experiment_id,
        case_id=execution.case_id,
        case_key=case.key,
        trial_number=execution.trial_number,
        status=execution.status,
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
