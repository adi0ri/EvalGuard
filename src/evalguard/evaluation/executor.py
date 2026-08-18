"""Transactional execution of the Phase 1 eligible-refund case."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from evalguard.db.models import (
    CaseExecution,
    Customer,
    EvaluationCase,
    Experiment,
    GraderResult,
    Order,
    Refund,
    TraceEvent,
)
from evalguard.domain.tools import ToolRuntime
from evalguard.evaluation.fixture_models import CaseFixture, EligibleRefundExpectation
from evalguard.evaluation.grader import RefundEvidence, RefundSnapshot, grade_eligible_refund
from evalguard.providers.fake import DeterministicFakeProvider


class ExecutionNotFoundError(RuntimeError):
    pass


class UnsupportedProviderError(RuntimeError):
    pass


def _materialize_fixture(
    session: Session,
    execution: CaseExecution,
    case: EvaluationCase,
) -> tuple[Customer, Order]:
    fixture = CaseFixture.model_validate(case.fixture)
    customer = Customer(
        case_execution_id=execution.id,
        external_id=fixture.customer.external_id,
        name=fixture.customer.name,
        email=fixture.customer.email,
    )
    session.add(customer)
    session.flush()
    order = Order(
        case_execution_id=execution.id,
        customer_id=customer.id,
        external_id=fixture.order.external_id,
        total_cents=fixture.order.total_cents,
        currency=fixture.order.currency,
        status=fixture.order.status,
        placed_at=fixture.order.placed_at,
        delivered_at=fixture.order.delivered_at,
    )
    session.add(order)
    session.flush()
    return customer, order


def _collect_refund_evidence(
    session: Session,
    execution: CaseExecution,
    expectation: EligibleRefundExpectation,
) -> RefundEvidence:
    refund_rows = session.execute(
        select(Refund, Order)
        .join(Order, Order.id == Refund.order_id)
        .where(
            Refund.case_execution_id == execution.id,
            Order.case_execution_id == execution.id,
        )
        .order_by(Refund.created_at, Refund.id)
    ).all()
    tool_events = session.scalars(
        select(TraceEvent)
        .where(
            TraceEvent.case_execution_id == execution.id,
            TraceEvent.event_type == "tool_call",
        )
        .order_by(TraceEvent.sequence_number)
    ).all()
    ordered_tool_names = tuple(str(event.payload.get("tool_name", "")) for event in tool_events)
    return RefundEvidence(
        expected_order_id=expectation.order_id,
        expected_amount_cents=expectation.refund_amount_cents,
        expected_tool_names=tuple(expectation.required_tools),
        expected_resolution_status=expectation.resolution_status,
        refunds=tuple(
            RefundSnapshot(
                refund_id=str(refund.id),
                order_id=order.external_id,
                amount_cents=refund.amount_cents,
                currency=refund.currency,
                status=refund.status,
            )
            for refund, order in refund_rows
        ),
        ordered_tool_names=ordered_tool_names,
        final_resolution=execution.final_resolution,
    )


def execute_case(session: Session, case_execution_id: UUID) -> dict[str, Any]:
    """Run, persist, and grade one case in a single database transaction.

    Holding one transaction is intentionally simple for the deterministic Phase 1
    provider. A later real network provider should split this into shorter durable
    steps with explicit retry state.
    """

    execution = session.scalar(
        select(CaseExecution).where(CaseExecution.id == case_execution_id).with_for_update()
    )
    if execution is None:
        raise ExecutionNotFoundError(str(case_execution_id))
    if execution.status == "completed":
        return {"execution_id": str(execution.id), "status": "completed", "replayed": True}

    experiment = session.get(Experiment, execution.experiment_id)
    case = session.get(EvaluationCase, execution.case_id)
    if experiment is None or case is None:
        raise ExecutionNotFoundError("Execution references are missing")
    if experiment.provider != DeterministicFakeProvider.name:
        raise UnsupportedProviderError(experiment.provider)

    execution.status = "running"
    if execution.started_at is None:
        execution.started_at = datetime.now(UTC)
    experiment.status = "running"
    _materialize_fixture(session, execution, case)

    expectation = EligibleRefundExpectation.model_validate(case.expected)
    runtime = ToolRuntime(session, execution.id)
    provider = DeterministicFakeProvider()
    resolution = provider.run(
        prompt=case.prompt,
        expected_order_id=expectation.order_id,
        expected_amount_cents=expectation.refund_amount_cents,
        tools=runtime,
    )
    execution.final_resolution = resolution
    session.flush()

    outcomes = grade_eligible_refund(_collect_refund_evidence(session, execution, expectation))
    for outcome in outcomes:
        session.add(
            GraderResult(
                case_execution_id=execution.id,
                grader_name="eligible_refund_v1",
                criterion=outcome.criterion,
                passed=outcome.passed,
                message=outcome.message,
                details=outcome.details,
            )
        )
    runtime.trace(
        "grader_completed",
        {
            "grader_name": "eligible_refund_v1",
            "passed": all(outcome.passed for outcome in outcomes),
            "criteria": [
                {"criterion": outcome.criterion, "passed": outcome.passed} for outcome in outcomes
            ],
        },
    )

    completed_at = datetime.now(UTC)
    execution.status = "completed"
    execution.completed_at = completed_at
    experiment.status = "completed"
    experiment.completed_at = completed_at
    session.flush()
    return {
        "execution_id": str(execution.id),
        "status": execution.status,
        "passed": all(outcome.passed for outcome in outcomes),
        "replayed": False,
    }
