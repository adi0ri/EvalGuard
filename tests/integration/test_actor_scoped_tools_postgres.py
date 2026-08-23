"""PostgreSQL coverage for trusted actor ownership within one execution."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from evalguard.db.models import (
    Customer,
    Order,
    Refund,
    SupportTicket,
    ToolInvocation,
    TraceEvent,
)
from evalguard.domain.tool_schemas import (
    EscalateTicketArgs,
    IssueRefundArgs,
    LookupOrderArgs,
    ShippingAddress,
    UpdateShippingAddressArgs,
)
from evalguard.domain.tools import ToolExecutionError, ToolRuntime

pytestmark = pytest.mark.integration

ToolName = Literal[
    "lookup_order",
    "issue_refund",
    "update_shipping_address",
    "escalate_ticket",
]
ACTOR_ORDER_ERROR = "Order was not found or is unavailable to this customer"
NEW_ADDRESS = ShippingAddress(
    line1="44 Public Harbor",
    line2=None,
    city="Port Aurora",
    region="CA",
    postal_code="94107",
    country="US",
)


def _invoke_tool(
    runtime: ToolRuntime,
    tool_name: ToolName,
    order_id: str,
    tool_call_id: str,
) -> object:
    if tool_name == "lookup_order":
        return runtime.lookup_order(
            LookupOrderArgs(order_id=order_id),
            tool_call_id=tool_call_id,
        )
    if tool_name == "issue_refund":
        return runtime.issue_refund(
            IssueRefundArgs(
                order_id=order_id,
                amount_cents=7_777,
                reason="Synthetic actor-ownership check.",
            ),
            tool_call_id=tool_call_id,
        )
    if tool_name == "update_shipping_address":
        return runtime.update_shipping_address(
            UpdateShippingAddressArgs(order_id=order_id, address=NEW_ADDRESS),
            tool_call_id=tool_call_id,
        )
    return runtime.escalate_ticket(
        EscalateTicketArgs(
            order_id=order_id,
            reason_code="manual_review",
            reason="Synthetic actor-ownership check.",
            priority="normal",
        ),
        tool_call_id=tool_call_id,
    )


def _add_customer_order(
    session: Session,
    execution_id: UUID,
    *,
    customer_external_id: str,
    order_external_id: str,
    status: str,
    total_cents: int = 7_777,
    customer_id: UUID | None = None,
) -> Order:
    if customer_id is None:
        customer = Customer(
            case_execution_id=execution_id,
            external_id=customer_external_id,
            name=f"Synthetic {customer_external_id}",
            email=f"{customer_external_id.lower()}@example.test",
        )
        session.add(customer)
        session.flush()
        customer_id = customer.id
    order = Order(
        case_execution_id=execution_id,
        customer_id=customer_id,
        external_id=order_external_id,
        total_cents=total_cents,
        currency="USD",
        status=status,
        placed_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        delivered_at=(datetime(2026, 8, 5, 12, 0, tzinfo=UTC) if status == "delivered" else None),
        shipping_address_line1="90 Private Way",
        shipping_address_line2=None,
        shipping_city="Port Aurora",
        shipping_region="CA",
        shipping_postal_code="94108",
        shipping_country="US",
    )
    session.add(order)
    session.flush()
    return order


def test_actor_scope_makes_foreign_and_nonexistent_orders_indistinguishable(
    session_factory: sessionmaker[Session],
    execution_factory: Callable[..., UUID],
) -> None:
    execution_id = execution_factory(
        order_external_id="PD-ACTOR",
        customer_external_id="CUST-ACTOR",
    )
    with session_factory.begin() as session:
        foreign_order = _add_customer_order(
            session,
            execution_id,
            customer_external_id="CUST-FOREIGN",
            order_external_id="PD-FOREIGN",
            status="processing",
        )
        foreign_order_id = foreign_order.id

    tool_names: tuple[ToolName, ...] = (
        "lookup_order",
        "issue_refund",
        "update_shipping_address",
        "escalate_ticket",
    )
    observed_errors: dict[tuple[ToolName, str], str] = {}
    with session_factory.begin() as session:
        runtime = ToolRuntime(
            session,
            execution_id,
            actor_customer_external_id="CUST-ACTOR",
        )
        for tool_name in tool_names:
            for target_kind, order_id in (
                ("foreign", "PD-FOREIGN"),
                ("missing", "PD-DOES-NOT-EXIST"),
            ):
                with pytest.raises(ToolExecutionError) as caught:
                    _invoke_tool(
                        runtime,
                        tool_name,
                        order_id,
                        f"actor-{tool_name}-{target_kind}",
                    )
                observed_errors[(tool_name, target_kind)] = str(caught.value)

    for tool_name in tool_names:
        assert observed_errors[(tool_name, "foreign")] == ACTOR_ORDER_ERROR
        assert observed_errors[(tool_name, "missing")] == ACTOR_ORDER_ERROR

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Refund)) == 0
        assert session.scalar(select(func.count()).select_from(SupportTicket)) == 0
        persisted_foreign_order = session.get(Order, foreign_order_id)
        assert persisted_foreign_order is not None
        assert persisted_foreign_order.shipping_address_line1 == "90 Private Way"
        assert persisted_foreign_order.shipping_postal_code == "94108"

        invocations = session.scalars(
            select(ToolInvocation)
            .where(ToolInvocation.case_execution_id == execution_id)
            .order_by(ToolInvocation.started_at, ToolInvocation.id)
        ).all()
        assert len(invocations) == 8
        assert all(invocation.status == "failed" for invocation in invocations)
        assert all(
            invocation.result
            == {
                "error": ACTOR_ORDER_ERROR,
                "error_type": "ToolExecutionError",
            }
            for invocation in invocations
        )
        traces = session.scalars(
            select(TraceEvent)
            .where(TraceEvent.case_execution_id == execution_id)
            .order_by(TraceEvent.sequence_number)
        ).all()
        assert [trace.sequence_number for trace in traces] == list(range(1, 17))
        assert [trace.event_type for trace in traces] == [
            event_type for _ in range(8) for event_type in ("tool_call", "tool_error")
        ]


def test_actor_scope_allows_each_tool_for_owned_orders(
    session_factory: sessionmaker[Session],
    execution_factory: Callable[..., UUID],
) -> None:
    execution_id = execution_factory(
        order_external_id="PD-ACTOR-DELIVERED",
        customer_external_id="CUST-ACTOR",
        total_cents=7_777,
    )
    with session_factory.begin() as session:
        actor = session.scalar(
            select(Customer).where(
                Customer.case_execution_id == execution_id,
                Customer.external_id == "CUST-ACTOR",
            )
        )
        assert actor is not None
        _add_customer_order(
            session,
            execution_id,
            customer_external_id="CUST-ACTOR",
            order_external_id="PD-ACTOR-PENDING",
            status="pending",
            customer_id=actor.id,
        )

    with session_factory.begin() as session:
        runtime = ToolRuntime(
            session,
            execution_id,
            actor_customer_external_id="CUST-ACTOR",
        )
        lookup = runtime.lookup_order(
            LookupOrderArgs(order_id="PD-ACTOR-DELIVERED"),
            tool_call_id="owned-lookup",
        )
        refund = runtime.issue_refund(
            IssueRefundArgs(
                order_id="PD-ACTOR-DELIVERED",
                amount_cents=7_777,
                reason="Synthetic owned refund.",
            ),
            tool_call_id="owned-refund",
        )
        address = runtime.update_shipping_address(
            UpdateShippingAddressArgs(order_id="PD-ACTOR-PENDING", address=NEW_ADDRESS),
            tool_call_id="owned-address",
        )
        ticket = runtime.escalate_ticket(
            EscalateTicketArgs(
                order_id="PD-ACTOR-DELIVERED",
                reason_code="manual_review",
                reason="Synthetic owned escalation.",
                priority="normal",
            ),
            tool_call_id="owned-escalation",
        )

        assert lookup.customer_id == "CUST-ACTOR"
        assert refund.order_id == "PD-ACTOR-DELIVERED"
        assert address.address == NEW_ADDRESS
        assert ticket.order_id == "PD-ACTOR-DELIVERED"


def test_cached_state_change_replays_for_owner_but_not_for_another_actor(
    session_factory: sessionmaker[Session],
    execution_factory: Callable[..., UUID],
) -> None:
    execution_id = execution_factory(
        order_external_id="PD-ACTOR",
        customer_external_id="CUST-ACTOR",
        total_cents=7_777,
    )
    with session_factory.begin() as session:
        session.add(
            Customer(
                case_execution_id=execution_id,
                external_id="CUST-OTHER",
                name="Synthetic Other Actor",
                email="other-actor@example.test",
            )
        )

    arguments = IssueRefundArgs(
        order_id="PD-ACTOR",
        amount_cents=7_777,
        reason="Synthetic stable actor replay.",
    )
    with session_factory.begin() as session:
        owner_runtime = ToolRuntime(
            session,
            execution_id,
            actor_customer_external_id="CUST-ACTOR",
        )
        first = owner_runtime.issue_refund(arguments, tool_call_id="actor-stable-refund")
        replay = owner_runtime.issue_refund(arguments, tool_call_id="actor-stable-refund")
        assert replay == first

        other_runtime = ToolRuntime(
            session,
            execution_id,
            actor_customer_external_id="CUST-OTHER",
        )
        with pytest.raises(ToolExecutionError, match=ACTOR_ORDER_ERROR):
            other_runtime.issue_refund(arguments, tool_call_id="actor-stable-refund")

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Refund)) == 1
        assert session.scalar(select(func.count()).select_from(ToolInvocation)) == 1
        assert session.scalar(select(func.count()).select_from(TraceEvent)) == 3
        assert session.scalars(
            select(TraceEvent.event_type)
            .where(TraceEvent.case_execution_id == execution_id)
            .order_by(TraceEvent.sequence_number)
        ).all() == ["tool_call", "tool_result", "tool_replay"]
