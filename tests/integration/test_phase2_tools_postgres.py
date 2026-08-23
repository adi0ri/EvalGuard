"""Phase 2 state-change, policy, idempotency, and isolation tests on PostgreSQL."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from evalguard.db.models import Customer, Order, Refund, SupportTicket, ToolInvocation, TraceEvent
from evalguard.domain.tool_schemas import (
    EscalateTicketArgs,
    IssueRefundArgs,
    LookupOrderArgs,
    ShippingAddress,
    UpdateShippingAddressArgs,
)
from evalguard.domain.tools import ToolExecutionError, ToolRuntime

pytestmark = pytest.mark.integration


class ForcedPhase2Rollback(RuntimeError):
    """Test-only error raised after Phase 2 mutations have flushed."""


ORIGINAL_ADDRESS = {
    "line1": "8 Harbor Walk",
    "line2": None,
    "city": "Port Aurora",
    "region": "CA",
    "postal_code": "94105",
    "country": "US",
}
UPDATED_ADDRESS = ShippingAddress(
    line1="14 Cedar Lane",
    line2="Suite 2",
    city="Port Aurora",
    region="CA",
    postal_code="94107",
    country="US",
)


def test_address_update_succeeds_and_duplicate_intent_is_a_noop(
    session_factory: sessionmaker[Session],
    execution_factory: Callable[..., UUID],
) -> None:
    execution_id = execution_factory(
        order_status="processing",
        shipping_address=ORIGINAL_ADDRESS,
    )
    arguments = UpdateShippingAddressArgs(order_id="PD-1001", address=UPDATED_ADDRESS)

    with session_factory.begin() as session:
        runtime = ToolRuntime(session, execution_id)
        first = runtime.update_shipping_address(arguments, tool_call_id="address-update-1")
        replay = runtime.update_shipping_address(arguments, tool_call_id="address-update-1")
        semantic_duplicate = runtime.update_shipping_address(
            arguments,
            tool_call_id="address-update-2",
        )

        assert first.status == "updated"
        assert first.duplicate is False
        assert replay == first
        assert semantic_duplicate.status == "updated"
        assert semantic_duplicate.duplicate is True
        assert semantic_duplicate.address == UPDATED_ADDRESS

    with session_factory() as session:
        order = session.scalar(
            select(Order).where(
                Order.case_execution_id == execution_id,
                Order.external_id == "PD-1001",
            )
        )
        assert order is not None
        assert (
            order.shipping_address_line1,
            order.shipping_address_line2,
            order.shipping_city,
            order.shipping_region,
            order.shipping_postal_code,
            order.shipping_country,
        ) == ("14 Cedar Lane", "Suite 2", "Port Aurora", "CA", "94107", "US")
        assert (
            session.scalar(
                select(func.count())
                .select_from(ToolInvocation)
                .where(ToolInvocation.case_execution_id == execution_id)
            )
            == 2
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(TraceEvent)
                .where(TraceEvent.case_execution_id == execution_id)
            )
            == 5
        )
        assert session.scalars(
            select(TraceEvent.event_type)
            .where(TraceEvent.case_execution_id == execution_id)
            .order_by(TraceEvent.sequence_number)
        ).all() == [
            "tool_call",
            "tool_result",
            "tool_replay",
            "tool_call",
            "tool_result",
        ]


def test_concurrent_exact_address_call_replays_one_persisted_invocation(
    session_factory: sessionmaker[Session],
    execution_factory: Callable[..., UUID],
) -> None:
    execution_id = execution_factory(
        order_status="processing",
        shipping_address=ORIGINAL_ADDRESS,
    )
    start = Barrier(2)
    arguments = UpdateShippingAddressArgs(order_id="PD-1001", address=UPDATED_ADDRESS)

    def update() -> tuple[str, bool]:
        start.wait(timeout=5)
        with session_factory.begin() as session:
            result = ToolRuntime(session, execution_id).update_shipping_address(
                arguments,
                tool_call_id="concurrent-exact-address-1",
            )
            return result.status, result.duplicate

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: update(), range(2)))

    assert results == [("updated", False), ("updated", False)]
    with session_factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ToolInvocation)
                .where(ToolInvocation.case_execution_id == execution_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(TraceEvent)
                .where(TraceEvent.case_execution_id == execution_id)
            )
            == 3
        )
        assert session.scalars(
            select(TraceEvent.event_type)
            .where(TraceEvent.case_execution_id == execution_id)
            .order_by(TraceEvent.sequence_number)
        ).all() == ["tool_call", "tool_result", "tool_replay"]


def test_concurrent_semantic_address_updates_converge_without_extra_mutation(
    session_factory: sessionmaker[Session],
    execution_factory: Callable[..., UUID],
) -> None:
    execution_id = execution_factory(
        order_status="processing",
        shipping_address=ORIGINAL_ADDRESS,
    )
    start = Barrier(2)
    arguments = UpdateShippingAddressArgs(order_id="PD-1001", address=UPDATED_ADDRESS)

    def update(call_number: int) -> bool:
        start.wait(timeout=5)
        with session_factory.begin() as session:
            return (
                ToolRuntime(session, execution_id)
                .update_shipping_address(
                    arguments,
                    tool_call_id=f"concurrent-semantic-address-{call_number}",
                )
                .duplicate
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        duplicate_flags = list(pool.map(update, (1, 2)))

    assert sorted(duplicate_flags) == [False, True]
    with session_factory() as session:
        order = session.scalar(
            select(Order).where(
                Order.case_execution_id == execution_id,
                Order.external_id == "PD-1001",
            )
        )
        assert order is not None
        assert order.shipping_address_line1 == UPDATED_ADDRESS.line1
        assert (
            session.scalar(
                select(func.count())
                .select_from(ToolInvocation)
                .where(ToolInvocation.case_execution_id == execution_id)
            )
            == 2
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(TraceEvent)
                .where(TraceEvent.case_execution_id == execution_id)
            )
            == 4
        )


def test_address_update_rejects_an_order_that_has_already_shipped(
    session_factory: sessionmaker[Session],
    execution_factory: Callable[..., UUID],
) -> None:
    execution_id = execution_factory(
        order_status="delivered",
        shipping_address=ORIGINAL_ADDRESS,
    )

    with session_factory.begin() as session:
        runtime = ToolRuntime(session, execution_id)
        with pytest.raises(ToolExecutionError, match="pending|processing|shipping"):
            runtime.update_shipping_address(
                UpdateShippingAddressArgs(order_id="PD-1001", address=UPDATED_ADDRESS),
                tool_call_id="address-rejected-1",
            )
        with pytest.raises(ToolExecutionError, match="pending|processing|shipping"):
            runtime.update_shipping_address(
                UpdateShippingAddressArgs(order_id="PD-1001", address=UPDATED_ADDRESS),
                tool_call_id="address-rejected-1",
            )

    with session_factory() as session:
        order = session.scalar(
            select(Order).where(
                Order.case_execution_id == execution_id,
                Order.external_id == "PD-1001",
            )
        )
        assert order is not None
        assert (
            order.shipping_address_line1,
            order.shipping_city,
            order.shipping_postal_code,
        ) == ("8 Harbor Walk", "Port Aurora", "94105")
        invocation = session.scalar(
            select(ToolInvocation).where(
                ToolInvocation.case_execution_id == execution_id,
                ToolInvocation.tool_call_id == "address-rejected-1",
            )
        )
        assert invocation is not None
        assert invocation.status == "failed"
        assert invocation.result is not None
        assert "pending or processing" in invocation.result["error"]
        assert session.scalars(
            select(TraceEvent.event_type)
            .where(TraceEvent.case_execution_id == execution_id)
            .order_by(TraceEvent.sequence_number)
        ).all() == ["tool_call", "tool_error"]


def test_high_value_refund_requires_one_idempotent_escalation(
    session_factory: sessionmaker[Session],
    execution_factory: Callable[..., UUID],
) -> None:
    execution_id = execution_factory(total_cents=50_000, order_status="delivered")

    with session_factory.begin() as session:
        runtime = ToolRuntime(session, execution_id)
        with pytest.raises(ToolExecutionError, match="escalat"):
            runtime.issue_refund(
                IssueRefundArgs(
                    order_id="PD-1001",
                    amount_cents=50_000,
                    reason="Synthetic high-value refund request",
                ),
                tool_call_id="high-value-refund-1",
            )

        first = runtime.escalate_ticket(
            EscalateTicketArgs(
                order_id="PD-1001",
                reason_code="high_value_refund",
                reason="Refund exceeds the synthetic self-service threshold.",
                priority="high",
            ),
            tool_call_id="high-value-escalation-1",
        )
        duplicate = runtime.escalate_ticket(
            EscalateTicketArgs(
                order_id="PD-1001",
                reason_code="high_value_refund",
                reason="A repeated request for the same required escalation.",
                priority="urgent",
            ),
            tool_call_id="high-value-escalation-2",
        )

        assert first.status == "escalated"
        assert first.duplicate is False
        assert duplicate.ticket_id == first.ticket_id
        assert duplicate.duplicate is True

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Refund)) == 0
        tickets = session.scalars(
            select(SupportTicket).where(SupportTicket.case_execution_id == execution_id)
        ).all()
        assert len(tickets) == 1
        assert tickets[0].status == "escalated"
        assert tickets[0].reason_code == "high_value_refund"
        assert tickets[0].priority == "high"
        assert tickets[0].escalated_at is not None
        rejected_refund = session.scalar(
            select(ToolInvocation).where(
                ToolInvocation.case_execution_id == execution_id,
                ToolInvocation.tool_call_id == "high-value-refund-1",
            )
        )
        assert rejected_refund is not None
        assert rejected_refund.status == "failed"
        assert rejected_refund.result is not None
        assert "require ticket escalation" in rejected_refund.result["error"]
        assert session.scalars(
            select(TraceEvent.event_type)
            .where(TraceEvent.case_execution_id == execution_id)
            .order_by(TraceEvent.sequence_number)
        ).all() == [
            "tool_call",
            "tool_error",
            "tool_call",
            "tool_result",
            "tool_call",
            "tool_result",
        ]


def test_address_and_escalation_evidence_roll_back_with_business_state(
    session_factory: sessionmaker[Session],
    execution_factory: Callable[..., UUID],
) -> None:
    execution_id = execution_factory(
        order_status="processing",
        shipping_address=ORIGINAL_ADDRESS,
    )

    with pytest.raises(ForcedPhase2Rollback), session_factory.begin() as session:
        runtime = ToolRuntime(session, execution_id)
        runtime.update_shipping_address(
            UpdateShippingAddressArgs(order_id="PD-1001", address=UPDATED_ADDRESS),
            tool_call_id="rollback-address-1",
        )
        runtime.escalate_ticket(
            EscalateTicketArgs(
                order_id="PD-1001",
                reason_code="manual_review",
                reason="Synthetic rollback evidence.",
                priority="normal",
            ),
            tool_call_id="rollback-escalation-1",
        )
        assert session.scalar(select(func.count()).select_from(SupportTicket)) == 1
        raise ForcedPhase2Rollback

    with session_factory() as session:
        order = session.scalar(
            select(Order).where(
                Order.case_execution_id == execution_id,
                Order.external_id == "PD-1001",
            )
        )
        assert order is not None
        assert order.shipping_address_line1 == ORIGINAL_ADDRESS["line1"]
        assert session.scalar(select(func.count()).select_from(SupportTicket)) == 0
        assert session.scalar(select(func.count()).select_from(ToolInvocation)) == 0
        assert session.scalar(select(func.count()).select_from(TraceEvent)) == 0


def test_tool_call_id_cannot_be_reused_for_different_intent(
    session_factory: sessionmaker[Session],
    execution_factory: Callable[..., UUID],
) -> None:
    execution_id = execution_factory(order_status="processing")
    first = UpdateShippingAddressArgs(order_id="PD-1001", address=UPDATED_ADDRESS)
    conflicting = UpdateShippingAddressArgs(
        order_id="PD-1001",
        address=ShippingAddress(
            line1="91 Juniper Road",
            city="Port Aurora",
            region="CA",
            postal_code="94109",
            country="US",
        ),
    )

    with session_factory.begin() as session:
        runtime = ToolRuntime(session, execution_id)
        runtime.update_shipping_address(first, tool_call_id="address-conflict-1")
        with pytest.raises(ToolExecutionError, match="different|conflict|reused"):
            runtime.update_shipping_address(conflicting, tool_call_id="address-conflict-1")
        with pytest.raises(ToolExecutionError, match="different|conflict|reused"):
            runtime.escalate_ticket(
                EscalateTicketArgs(
                    order_id="PD-1001",
                    reason_code="manual_review",
                    reason="This call ID already belongs to another tool.",
                    priority="normal",
                ),
                tool_call_id="address-conflict-1",
            )


def test_address_and_escalation_tools_cannot_cross_execution_boundaries(
    session_factory: sessionmaker[Session],
    execution_factory: Callable[..., UUID],
) -> None:
    owner_execution_id = execution_factory(
        order_external_id="PD-PRIVATE",
        customer_external_id="CUST-OWNER",
        order_status="processing",
        shipping_address=ORIGINAL_ADDRESS,
    )
    other_execution_id = execution_factory(materialize_state=False)

    with session_factory.begin() as session:
        runtime = ToolRuntime(session, other_execution_id)
        with pytest.raises(ToolExecutionError, match="not found in this execution"):
            runtime.update_shipping_address(
                UpdateShippingAddressArgs(order_id="PD-PRIVATE", address=UPDATED_ADDRESS),
                tool_call_id="cross-execution-address-1",
            )
        with pytest.raises(ToolExecutionError, match="not found in this execution"):
            runtime.escalate_ticket(
                EscalateTicketArgs(
                    order_id="PD-PRIVATE",
                    reason_code="manual_review",
                    reason="Must not cross execution ownership.",
                    priority="normal",
                ),
                tool_call_id="cross-execution-escalation-1",
            )

    with session_factory() as session:
        owner_order = session.scalar(
            select(Order).where(
                Order.case_execution_id == owner_execution_id,
                Order.external_id == "PD-PRIVATE",
            )
        )
        assert owner_order is not None
        assert owner_order.shipping_address_line1 == "8 Harbor Walk"
        assert session.scalar(select(func.count()).select_from(SupportTicket)) == 0


def test_concurrent_duplicate_escalations_converge_on_one_ticket(
    session_factory: sessionmaker[Session],
    execution_factory: Callable[..., UUID],
) -> None:
    execution_id = execution_factory(total_cents=75_000, order_status="delivered")
    start = Barrier(2)

    def escalate(call_number: int) -> tuple[str, bool]:
        start.wait(timeout=5)
        with session_factory.begin() as session:
            result = ToolRuntime(session, execution_id).escalate_ticket(
                EscalateTicketArgs(
                    order_id="PD-1001",
                    reason_code="high_value_refund",
                    reason=f"Concurrent synthetic escalation {call_number}",
                    priority="high",
                ),
                tool_call_id=f"concurrent-escalation-{call_number}",
            )
            return result.ticket_id, result.duplicate

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(escalate, (1, 2)))

    assert len({ticket_id for ticket_id, _ in results}) == 1
    assert sorted(duplicate for _, duplicate in results) == [False, True]
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(SupportTicket)) == 1


def test_support_ticket_foreign_keys_reject_cross_execution_ownership(
    session_factory: sessionmaker[Session],
    execution_factory: Callable[..., UUID],
) -> None:
    owner_execution_id = execution_factory(
        order_external_id="PD-OWNER",
        customer_external_id="CUST-OWNER",
    )
    other_execution_id = execution_factory(
        order_external_id="PD-OTHER",
        customer_external_id="CUST-OTHER",
    )

    with pytest.raises(IntegrityError), session_factory.begin() as session:
        ToolRuntime(session, other_execution_id).lookup_order(
            arguments=LookupOrderArgs(order_id="PD-OTHER"),
            tool_call_id="other-execution-lookup-1",
        )
        owner_order = session.scalar(
            select(Order).where(Order.case_execution_id == owner_execution_id)
        )
        owner_customer = session.scalar(
            select(Customer).where(Customer.case_execution_id == owner_execution_id)
        )
        assert owner_order is not None
        assert owner_customer is not None
        session.add(
            SupportTicket(
                case_execution_id=other_execution_id,
                customer_id=owner_customer.id,
                order_id=owner_order.id,
                reason_code="manual_review",
                reason="This cross-execution relation must fail.",
                priority="normal",
                status="escalated",
                tool_call_id="other-execution-lookup-1",
            )
        )
        session.flush()

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(SupportTicket)) == 0


def test_support_ticket_foreign_key_rejects_the_wrong_customer_for_its_order(
    session_factory: sessionmaker[Session],
    execution_factory: Callable[..., UUID],
) -> None:
    execution_id = execution_factory(
        order_external_id="PD-OWNER",
        customer_external_id="CUST-OWNER",
    )

    with pytest.raises(IntegrityError), session_factory.begin() as session:
        other_customer = Customer(
            case_execution_id=execution_id,
            external_id="CUST-NOT-OWNER",
            name="Synthetic Non-Owner",
            email="non-owner@example.test",
        )
        session.add(other_customer)
        session.flush()
        ToolRuntime(session, execution_id).lookup_order(
            LookupOrderArgs(order_id="PD-OWNER"),
            tool_call_id="same-execution-ticket-owner-check",
        )
        order = session.scalar(
            select(Order).where(
                Order.case_execution_id == execution_id,
                Order.external_id == "PD-OWNER",
            )
        )
        assert order is not None
        session.add(
            SupportTicket(
                case_execution_id=execution_id,
                customer_id=other_customer.id,
                order_id=order.id,
                reason_code="manual_review",
                reason="This same-execution owner mismatch must fail.",
                priority="normal",
                status="escalated",
                tool_call_id="same-execution-ticket-owner-check",
            )
        )
        session.flush()

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(SupportTicket)) == 0
