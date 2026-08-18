"""Execution-scoped ParcelDesk tools with persisted call and trace records."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evalguard.db.models import Customer, Order, Refund, ToolInvocation, TraceEvent
from evalguard.domain.tool_schemas import (
    IssueRefundArgs,
    IssueRefundResult,
    LookupOrderArgs,
    LookupOrderResult,
)


class ToolExecutionError(RuntimeError):
    """A deterministic domain or execution-scope failure."""


def _record_trace(
    session: Session,
    case_execution_id: UUID,
    event_type: str,
    payload: dict[str, Any],
) -> TraceEvent:
    """Append an event using an execution-local monotonic sequence number."""

    current = session.scalar(
        select(func.coalesce(func.max(TraceEvent.sequence_number), 0)).where(
            TraceEvent.case_execution_id == case_execution_id
        )
    )
    event = TraceEvent(
        case_execution_id=case_execution_id,
        sequence_number=int(current or 0) + 1,
        event_type=event_type,
        payload=payload,
    )
    session.add(event)
    session.flush()
    return event


class ToolRuntime:
    """Bind every tool lookup and mutation to one isolated case execution."""

    def __init__(self, session: Session, case_execution_id: UUID) -> None:
        self.session = session
        self.case_execution_id = case_execution_id

    def trace(self, event_type: str, payload: dict[str, Any]) -> TraceEvent:
        return _record_trace(self.session, self.case_execution_id, event_type, payload)

    def _cached_invocation(self, tool_call_id: str) -> ToolInvocation | None:
        return self.session.scalar(
            select(ToolInvocation).where(
                ToolInvocation.case_execution_id == self.case_execution_id,
                ToolInvocation.tool_call_id == tool_call_id,
            )
        )

    def lookup_order(self, arguments: LookupOrderArgs, *, tool_call_id: str) -> LookupOrderResult:
        cached = self._cached_invocation(tool_call_id)
        if cached is not None:
            if cached.status != "completed" or cached.result is None:
                raise ToolExecutionError(f"Tool call {tool_call_id} is not replayable")
            return LookupOrderResult.model_validate(cached.result)

        invocation = ToolInvocation(
            case_execution_id=self.case_execution_id,
            tool_call_id=tool_call_id,
            tool_name="lookup_order",
            arguments=arguments.model_dump(mode="json"),
            status="running",
        )
        self.session.add(invocation)
        self.trace(
            "tool_call",
            {
                "tool_call_id": tool_call_id,
                "tool_name": "lookup_order",
                "arguments": arguments.model_dump(mode="json"),
            },
        )

        order = self.session.scalar(
            select(Order).where(
                Order.case_execution_id == self.case_execution_id,
                Order.external_id == arguments.order_id,
            )
        )
        if order is None:
            raise ToolExecutionError(f"Order {arguments.order_id} was not found in this execution")
        customer = self.session.scalar(
            select(Customer).where(
                Customer.id == order.customer_id,
                Customer.case_execution_id == self.case_execution_id,
            )
        )
        if customer is None:
            raise ToolExecutionError("The order's customer is outside this execution")

        result = LookupOrderResult(
            order_id=order.external_id,
            customer_id=customer.external_id,
            status=order.status,
            total_cents=order.total_cents,
            currency=order.currency,
        )
        invocation.result = result.model_dump(mode="json")
        invocation.status = "completed"
        invocation.completed_at = datetime.now(UTC)
        self.trace(
            "tool_result",
            {
                "tool_call_id": tool_call_id,
                "tool_name": "lookup_order",
                "result": result.model_dump(mode="json"),
            },
        )
        return result

    def issue_refund(self, arguments: IssueRefundArgs, *, tool_call_id: str) -> IssueRefundResult:
        cached = self._cached_invocation(tool_call_id)
        if cached is not None:
            if cached.status != "completed" or cached.result is None:
                raise ToolExecutionError(f"Tool call {tool_call_id} is not replayable")
            return IssueRefundResult.model_validate(cached.result)

        order = self.session.scalar(
            select(Order).where(
                Order.case_execution_id == self.case_execution_id,
                Order.external_id == arguments.order_id,
            )
        )
        if order is None:
            raise ToolExecutionError(f"Order {arguments.order_id} was not found in this execution")
        if order.status != "delivered":
            raise ToolExecutionError("Only the implemented delivered-order scenario is refundable")
        if arguments.amount_cents != order.total_cents:
            raise ToolExecutionError(
                "The refund amount must equal the order total in this scenario"
            )

        invocation = ToolInvocation(
            case_execution_id=self.case_execution_id,
            tool_call_id=tool_call_id,
            tool_name="issue_refund",
            arguments=arguments.model_dump(mode="json"),
            status="running",
        )
        self.session.add(invocation)
        self.trace(
            "tool_call",
            {
                "tool_call_id": tool_call_id,
                "tool_name": "issue_refund",
                "arguments": arguments.model_dump(mode="json"),
            },
        )

        existing_refund = self.session.scalar(
            select(Refund).where(
                Refund.case_execution_id == self.case_execution_id,
                Refund.order_id == order.id,
            )
        )
        duplicate = existing_refund is not None
        if existing_refund is None:
            refund = Refund(
                case_execution_id=self.case_execution_id,
                order_id=order.id,
                amount_cents=arguments.amount_cents,
                currency=order.currency,
                status="completed",
                reason=arguments.reason,
                tool_call_id=tool_call_id,
            )
            self.session.add(refund)
            self.session.flush()
        else:
            refund = existing_refund

        result = IssueRefundResult(
            refund_id=str(refund.id),
            order_id=order.external_id,
            amount_cents=refund.amount_cents,
            currency=refund.currency,
            status="completed",
            duplicate=duplicate,
        )
        invocation.result = result.model_dump(mode="json")
        invocation.status = "completed"
        invocation.completed_at = datetime.now(UTC)
        self.trace(
            "tool_result",
            {
                "tool_call_id": tool_call_id,
                "tool_name": "issue_refund",
                "result": result.model_dump(mode="json"),
            },
        )
        return result
