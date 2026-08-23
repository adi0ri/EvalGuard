"""Transactional execution of immutable EvalGuard cases."""

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
    SupportTicket,
    ToolInvocation,
    TraceEvent,
)
from evalguard.domain.tool_schemas import ShippingAddress
from evalguard.domain.tools import ToolRuntime
from evalguard.evaluation.fixture_models import (
    CaseFixture,
    EligibleRefundExpectation,
    ScenarioCaseExpected,
    ScenarioFixture,
)
from evalguard.evaluation.grader import (
    CriterionResult,
    InvocationSnapshot,
    RefundEvidence,
    RefundSnapshot,
    ScenarioEvidence,
    ScenarioOrderSnapshot,
    TicketSnapshot,
    TraceSnapshot,
    grade_eligible_refund,
    grade_scenario,
)
from evalguard.evaluation.lifecycle import reconcile_experiment_status
from evalguard.evaluation.scenarios import PHASE3_SCENARIOS, ScenarioDefinition
from evalguard.evaluation.suite import PHASE3_CASE_DEFINITION_HASHES
from evalguard.providers.fake import DeterministicFakeProvider


class ExecutionNotFoundError(RuntimeError):
    pass


class UnsupportedProviderError(RuntimeError):
    pass


class UnsupportedCaseDefinitionError(RuntimeError):
    pass


def _materialize_legacy_fixture(
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


def _materialize_scenario_fixture(
    session: Session,
    execution: CaseExecution,
    case: EvaluationCase,
) -> ScenarioFixture:
    """Create the complete relational state declared by one Phase 3 fixture."""

    fixture = ScenarioFixture.model_validate(case.fixture)
    customers: dict[str, Customer] = {}
    for customer_fixture in fixture.customers:
        customer = Customer(
            case_execution_id=execution.id,
            external_id=customer_fixture.external_id,
            name=customer_fixture.name,
            email=customer_fixture.email,
        )
        session.add(customer)
        customers[customer_fixture.external_id] = customer
    session.flush()

    for order_fixture in fixture.orders:
        address = order_fixture.shipping_address
        session.add(
            Order(
                case_execution_id=execution.id,
                customer_id=customers[order_fixture.customer_external_id].id,
                external_id=order_fixture.external_id,
                total_cents=order_fixture.total_cents,
                currency=order_fixture.currency,
                status=order_fixture.status,
                shipping_address_line1=address.line1 if address is not None else None,
                shipping_address_line2=address.line2 if address is not None else None,
                shipping_city=address.city if address is not None else None,
                shipping_region=address.region if address is not None else None,
                shipping_postal_code=address.postal_code if address is not None else None,
                shipping_country=address.country if address is not None else None,
                placed_at=order_fixture.placed_at,
                delivered_at=order_fixture.delivered_at,
            )
        )
    session.flush()
    return fixture


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


def _shipping_address_snapshot(order: Order) -> ShippingAddress | None:
    values = (
        order.shipping_address_line1,
        order.shipping_city,
        order.shipping_region,
        order.shipping_postal_code,
        order.shipping_country,
    )
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise UnsupportedCaseDefinitionError(
            f"Order {order.external_id} has an incomplete persisted shipping address"
        )
    return ShippingAddress.model_validate(
        {
            "line1": order.shipping_address_line1,
            "line2": order.shipping_address_line2,
            "city": order.shipping_city,
            "region": order.shipping_region,
            "postal_code": order.shipping_postal_code,
            "country": order.shipping_country,
        }
    )


def _scenario_definition(case: EvaluationCase) -> ScenarioDefinition:
    definition = next(
        (scenario for scenario in PHASE3_SCENARIOS if scenario.key == case.key),
        None,
    )
    expected_hash = PHASE3_CASE_DEFINITION_HASHES.get(case.key)
    if definition is None or expected_hash is None or case.definition_hash != expected_hash:
        raise UnsupportedCaseDefinitionError(
            f"Case {case.key} is not a recognized Phase 3 definition"
        )
    if (
        case.title != definition.title
        or case.prompt != definition.prompt
        or case.fixture != definition.fixture.model_dump(mode="json")
        or case.expected != definition.expected.model_dump(mode="json")
    ):
        raise UnsupportedCaseDefinitionError(
            f"Persisted Phase 3 case {case.key} differs from source-controlled content"
        )
    return definition


def _collect_scenario_evidence(
    session: Session,
    execution: CaseExecution,
    fixture: ScenarioFixture,
    expectation: ScenarioCaseExpected,
) -> ScenarioEvidence:
    order_rows = session.execute(
        select(Order, Customer)
        .join(
            Customer,
            (Customer.id == Order.customer_id)
            & (Customer.case_execution_id == Order.case_execution_id),
        )
        .where(Order.case_execution_id == execution.id)
        .order_by(Order.external_id)
    ).all()
    refund_rows = session.execute(
        select(Refund, Order)
        .join(
            Order,
            (Order.id == Refund.order_id) & (Order.case_execution_id == Refund.case_execution_id),
        )
        .where(Refund.case_execution_id == execution.id)
        .order_by(Refund.created_at, Refund.id)
    ).all()
    ticket_rows = session.execute(
        select(SupportTicket, Order, Customer)
        .join(
            Order,
            (Order.id == SupportTicket.order_id)
            & (Order.case_execution_id == SupportTicket.case_execution_id),
        )
        .join(
            Customer,
            (Customer.id == SupportTicket.customer_id)
            & (Customer.case_execution_id == SupportTicket.case_execution_id),
        )
        .where(SupportTicket.case_execution_id == execution.id)
        .order_by(SupportTicket.created_at, SupportTicket.id)
    ).all()
    trace_rows = session.scalars(
        select(TraceEvent)
        .where(TraceEvent.case_execution_id == execution.id)
        .order_by(TraceEvent.sequence_number)
    ).all()
    invocation_rows = session.scalars(
        select(ToolInvocation)
        .where(ToolInvocation.case_execution_id == execution.id)
        .order_by(ToolInvocation.started_at, ToolInvocation.id)
    ).all()
    invocation_by_id = {row.tool_call_id: row for row in invocation_rows}
    ordered_call_ids = [
        str(row.payload.get("tool_call_id", ""))
        for row in trace_rows
        if row.event_type == "tool_call"
    ]
    ordered_invocations = [
        invocation_by_id[call_id] for call_id in ordered_call_ids if call_id in invocation_by_id
    ]
    traced_call_ids = set(ordered_call_ids)
    ordered_invocations.extend(
        row for row in invocation_rows if row.tool_call_id not in traced_call_ids
    )

    return ScenarioEvidence(
        fixture=fixture,
        expectation=expectation.policy,
        orders=tuple(
            ScenarioOrderSnapshot(
                order_id=order.external_id,
                customer_id=customer.external_id,
                status=order.status,
                total_cents=order.total_cents,
                currency=order.currency,
                shipping_address=_shipping_address_snapshot(order),
            )
            for order, customer in order_rows
        ),
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
        tickets=tuple(
            TicketSnapshot(
                ticket_id=str(ticket.id),
                order_id=order.external_id,
                customer_id=customer.external_id,
                reason_code=ticket.reason_code,
                reason=ticket.reason,
                priority=ticket.priority,
                status=ticket.status,
            )
            for ticket, order, customer in ticket_rows
        ),
        invocations=tuple(
            InvocationSnapshot(
                tool_call_id=row.tool_call_id,
                tool_name=row.tool_name,
                arguments=row.arguments,
                status=row.status,
                result=row.result,
            )
            for row in ordered_invocations
        ),
        trace_events=tuple(
            TraceSnapshot(
                sequence_number=row.sequence_number,
                event_type=row.event_type,
                payload=row.payload,
            )
            for row in trace_rows
        ),
        final_resolution=execution.final_resolution,
        category=expectation.category,
    )


def _persist_grader_results(
    session: Session,
    execution: CaseExecution,
    runtime: ToolRuntime,
    outcomes: tuple[CriterionResult, ...],
) -> None:
    for outcome in outcomes:
        session.add(
            GraderResult(
                case_execution_id=execution.id,
                grader_name=outcome.grader_name,
                criterion=outcome.criterion,
                passed=outcome.passed,
                message=outcome.message,
                details=outcome.details,
            )
        )
    runtime.trace(
        "grader_completed",
        {
            "grader_names": sorted({outcome.grader_name for outcome in outcomes}),
            "passed": all(outcome.passed for outcome in outcomes),
            "criteria": [
                {
                    "grader_name": outcome.grader_name,
                    "criterion": outcome.criterion,
                    "passed": outcome.passed,
                }
                for outcome in outcomes
            ],
        },
    )


def execute_case(
    session: Session,
    case_execution_id: UUID,
    *,
    provider: DeterministicFakeProvider | None = None,
) -> dict[str, Any]:
    """Run, persist, and grade one case in a single database transaction.

    Holding one transaction is intentionally simple for the deterministic fake
    provider. A later real network provider should split this into shorter durable
    steps with explicit retry state.
    """

    execution = session.scalar(
        select(CaseExecution).where(CaseExecution.id == case_execution_id).with_for_update()
    )
    if execution is None:
        raise ExecutionNotFoundError(str(case_execution_id))

    experiment = session.get(Experiment, execution.experiment_id)
    case = session.get(EvaluationCase, execution.case_id)
    if experiment is None or case is None:
        raise ExecutionNotFoundError("Execution references are missing")
    if case.suite_id != experiment.suite_id:
        raise UnsupportedCaseDefinitionError(
            "Case execution references a case outside its experiment suite"
        )
    if experiment.provider != DeterministicFakeProvider.name:
        raise UnsupportedProviderError(experiment.provider)
    if execution.status == "completed":
        return {"execution_id": str(execution.id), "status": "completed", "replayed": True}

    execution.status = "running"
    if execution.started_at is None:
        execution.started_at = datetime.now(UTC)
    selected_provider = provider or DeterministicFakeProvider()

    if "category" in case.expected:
        definition = _scenario_definition(case)
        fixture = _materialize_scenario_fixture(session, execution, case)
        expectation = ScenarioCaseExpected.model_validate(case.expected)
        runtime = ToolRuntime(
            session,
            execution.id,
            actor_customer_external_id=fixture.requester_customer_id,
        )
        resolution = selected_provider.run_scenario(definition=definition, tools=runtime)
        execution.final_resolution = resolution
        session.flush()
        outcomes = grade_scenario(
            _collect_scenario_evidence(
                session,
                execution,
                fixture,
                expectation,
            )
        )
    else:
        _materialize_legacy_fixture(session, execution, case)
        legacy_expectation = EligibleRefundExpectation.model_validate(case.expected)
        runtime = ToolRuntime(session, execution.id)
        resolution = selected_provider.run(
            prompt=case.prompt,
            expected_order_id=legacy_expectation.order_id,
            expected_amount_cents=legacy_expectation.refund_amount_cents,
            tools=runtime,
        )
        execution.final_resolution = resolution
        session.flush()
        outcomes = grade_eligible_refund(
            _collect_refund_evidence(session, execution, legacy_expectation)
        )

    _persist_grader_results(session, execution, runtime, outcomes)

    completed_at = datetime.now(UTC)
    execution.status = "completed"
    execution.completed_at = completed_at
    reconcile_experiment_status(
        session,
        experiment.id,
        completed_at=completed_at,
    )
    session.flush()
    return {
        "execution_id": str(execution.id),
        "status": execution.status,
        "passed": all(outcome.passed for outcome in outcomes),
        "replayed": False,
    }
