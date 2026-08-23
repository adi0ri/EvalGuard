"""Execution-scoped ParcelDesk tools with persisted call and trace records."""

from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evalguard.db.models import (
    CaseExecution,
    Customer,
    Order,
    Refund,
    SupportTicket,
    ToolInvocation,
    TraceEvent,
)
from evalguard.domain.tool_schemas import (
    EscalateTicketArgs,
    EscalateTicketResult,
    IssueRefundArgs,
    IssueRefundResult,
    LookupOrderArgs,
    LookupOrderResult,
    UpdateShippingAddressArgs,
    UpdateShippingAddressResult,
)
from evalguard.evaluation.resilience import StepBudget

ADDRESS_CHANGEABLE_ORDER_STATUSES = frozenset({"pending", "processing"})
HIGH_VALUE_REFUND_THRESHOLD_CENTS = 50_000
ToolResultT = TypeVar("ToolResultT", bound=BaseModel)


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

    def __init__(
        self,
        session: Session,
        case_execution_id: UUID,
        *,
        actor_customer_external_id: str | None = None,
        max_tool_steps: int = 8,
    ) -> None:
        self.session = session
        self.case_execution_id = case_execution_id
        self.actor_customer_external_id = actor_customer_external_id
        self._step_budget = StepBudget(limit=max_tool_steps)

    @property
    def steps_attempted(self) -> int:
        """Return all requested tool calls, including replays and rejected calls."""

        return self._step_budget.attempted_steps

    def trace(self, event_type: str, payload: dict[str, Any]) -> TraceEvent:
        return _record_trace(self.session, self.case_execution_id, event_type, payload)

    def _cached_invocation(self, tool_call_id: str) -> ToolInvocation | None:
        return self.session.scalar(
            select(ToolInvocation).where(
                ToolInvocation.case_execution_id == self.case_execution_id,
                ToolInvocation.tool_call_id == tool_call_id,
            )
        )

    def _lock_execution(self) -> None:
        """Serialize calls that can share an execution-local identity or trace."""

        execution = self.session.scalar(
            select(CaseExecution)
            .where(CaseExecution.id == self.case_execution_id)
            .with_for_update()
        )
        if execution is None:
            raise ToolExecutionError(f"Execution {self.case_execution_id} was not found")

    def _locked_order(self, order_id: str) -> Order:
        """Lock one execution- and, when supplied, actor-owned order."""

        statement = select(Order).where(
            Order.case_execution_id == self.case_execution_id,
            Order.external_id == order_id,
        )
        if self.actor_customer_external_id is not None:
            statement = statement.join(
                Customer,
                (Customer.id == Order.customer_id)
                & (Customer.case_execution_id == Order.case_execution_id),
            ).where(
                Customer.case_execution_id == self.case_execution_id,
                Customer.external_id == self.actor_customer_external_id,
            )
        order = self.session.scalar(statement.with_for_update(of=Order))
        if order is None:
            if self.actor_customer_external_id is not None:
                raise ToolExecutionError("Order was not found or is unavailable to this customer")
            raise ToolExecutionError(f"Order {order_id} was not found in this execution")
        return order

    def _replay_cached(
        self,
        tool_call_id: str,
        tool_name: str,
        arguments: BaseModel,
        result_type: type[ToolResultT],
        order_id: str,
    ) -> ToolResultT | None:
        """Replay only when a stable call ID represents exactly the same intent."""

        cached = self._cached_invocation(tool_call_id)
        if cached is None:
            return None

        # The trusted actor is execution context rather than provider-controlled tool
        # input, so it is intentionally absent from persisted arguments. Re-check
        # ownership before replaying to prevent a different actor in the same execution
        # from receiving another customer's cached result.
        if self.actor_customer_external_id is not None:
            self._locked_order(order_id)

        validated_arguments = arguments.model_dump(mode="json")
        if cached.tool_name != tool_name or cached.arguments != validated_arguments:
            raise ToolExecutionError(
                f"Tool call {tool_call_id} was already used for different tool or arguments"
            )
        if cached.status == "failed":
            persisted_error = None
            if cached.result is not None:
                persisted_error = cached.result.get("error")
            if isinstance(persisted_error, str):
                raise ToolExecutionError(persisted_error)
            raise ToolExecutionError(f"Tool call {tool_call_id} previously failed")
        if cached.status != "completed" or cached.result is None:
            raise ToolExecutionError(f"Tool call {tool_call_id} is not replayable")
        try:
            result = result_type.model_validate(cached.result)
        except ValidationError as exc:
            raise ToolExecutionError(
                f"Tool call {tool_call_id} has an invalid persisted result"
            ) from exc
        self.trace(
            "tool_replay",
            {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "arguments": validated_arguments,
                "result": cached.result,
            },
        )
        return result

    def _start_invocation(
        self,
        tool_call_id: str,
        tool_name: str,
        arguments: BaseModel,
    ) -> ToolInvocation:
        arguments_payload = arguments.model_dump(mode="json")
        invocation = ToolInvocation(
            case_execution_id=self.case_execution_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=arguments_payload,
            status="running",
        )
        self.session.add(invocation)
        self.trace(
            "tool_call",
            {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "arguments": arguments_payload,
            },
        )
        return invocation

    def _complete_invocation(
        self,
        invocation: ToolInvocation,
        result: BaseModel,
    ) -> None:
        result_payload = result.model_dump(mode="json")
        invocation.result = result_payload
        invocation.status = "completed"
        invocation.completed_at = datetime.now(UTC)
        self.trace(
            "tool_result",
            {
                "tool_call_id": invocation.tool_call_id,
                "tool_name": invocation.tool_name,
                "result": result_payload,
            },
        )

    def _fail_invocation(
        self,
        invocation: ToolInvocation,
        error: ToolExecutionError,
    ) -> None:
        """Persist an expected domain rejection as ordered execution evidence."""

        error_payload = {
            "error": str(error),
            "error_type": type(error).__name__,
        }
        invocation.result = error_payload
        invocation.status = "failed"
        invocation.completed_at = datetime.now(UTC)
        self.trace(
            "tool_error",
            {
                "tool_call_id": invocation.tool_call_id,
                "tool_name": invocation.tool_name,
                "error": error_payload,
            },
        )

    def lookup_order(self, arguments: LookupOrderArgs, *, tool_call_id: str) -> LookupOrderResult:
        self._step_budget.consume()
        self._lock_execution()
        cached = self._replay_cached(
            tool_call_id,
            "lookup_order",
            arguments,
            LookupOrderResult,
            arguments.order_id,
        )
        if cached is not None:
            return cached

        invocation = self._start_invocation(tool_call_id, "lookup_order", arguments)
        try:
            order = self._locked_order(arguments.order_id)
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
                delivered_at=order.delivered_at,
            )
        except ToolExecutionError as exc:
            self._fail_invocation(invocation, exc)
            raise
        self._complete_invocation(invocation, result)
        return result

    def issue_refund(self, arguments: IssueRefundArgs, *, tool_call_id: str) -> IssueRefundResult:
        self._step_budget.consume()
        self._lock_execution()
        cached = self._replay_cached(
            tool_call_id,
            "issue_refund",
            arguments,
            IssueRefundResult,
            arguments.order_id,
        )
        if cached is not None:
            return cached

        invocation = self._start_invocation(tool_call_id, "issue_refund", arguments)
        try:
            order = self._locked_order(arguments.order_id)
            if order.status != "delivered":
                raise ToolExecutionError(
                    "Only the implemented delivered-order scenario is refundable"
                )
            if arguments.amount_cents != order.total_cents:
                raise ToolExecutionError(
                    "The refund amount must equal the order total in this scenario"
                )
            if order.total_cents >= HIGH_VALUE_REFUND_THRESHOLD_CENTS:
                raise ToolExecutionError(
                    "Refunds at or above 50000 cents require ticket escalation"
                )
        except ToolExecutionError as exc:
            self._fail_invocation(invocation, exc)
            raise

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
        self._complete_invocation(invocation, result)
        return result

    def update_shipping_address(
        self,
        arguments: UpdateShippingAddressArgs,
        *,
        tool_call_id: str,
    ) -> UpdateShippingAddressResult:
        """Update an unshipped order, converging repeated semantic requests."""

        self._step_budget.consume()
        self._lock_execution()
        cached = self._replay_cached(
            tool_call_id,
            "update_shipping_address",
            arguments,
            UpdateShippingAddressResult,
            arguments.order_id,
        )
        if cached is not None:
            return cached

        invocation = self._start_invocation(
            tool_call_id,
            "update_shipping_address",
            arguments,
        )
        try:
            order = self._locked_order(arguments.order_id)
            if order.status not in ADDRESS_CHANGEABLE_ORDER_STATUSES:
                raise ToolExecutionError(
                    "Shipping addresses can only be changed for pending or processing orders"
                )

            requested = arguments.address
            existing_values = (
                order.shipping_address_line1,
                order.shipping_address_line2,
                order.shipping_city,
                order.shipping_region,
                order.shipping_postal_code,
                order.shipping_country,
            )
            requested_values = (
                requested.line1,
                requested.line2,
                requested.city,
                requested.region,
                requested.postal_code,
                requested.country,
            )
            duplicate = existing_values == requested_values
        except ToolExecutionError as exc:
            self._fail_invocation(invocation, exc)
            raise
        if not duplicate:
            order.shipping_address_line1 = requested.line1
            order.shipping_address_line2 = requested.line2
            order.shipping_city = requested.city
            order.shipping_region = requested.region
            order.shipping_postal_code = requested.postal_code
            order.shipping_country = requested.country

        result = UpdateShippingAddressResult(
            order_id=order.external_id,
            address=requested,
            status="updated",
            duplicate=duplicate,
        )
        self._complete_invocation(invocation, result)
        return result

    def escalate_ticket(
        self,
        arguments: EscalateTicketArgs,
        *,
        tool_call_id: str,
    ) -> EscalateTicketResult:
        """Create one durable escalation per execution, order, and reason code."""

        self._step_budget.consume()
        self._lock_execution()
        cached = self._replay_cached(
            tool_call_id,
            "escalate_ticket",
            arguments,
            EscalateTicketResult,
            arguments.order_id,
        )
        if cached is not None:
            return cached

        invocation = self._start_invocation(tool_call_id, "escalate_ticket", arguments)
        try:
            order = self._locked_order(arguments.order_id)
        except ToolExecutionError as exc:
            self._fail_invocation(invocation, exc)
            raise
        ticket = self.session.scalar(
            select(SupportTicket).where(
                SupportTicket.case_execution_id == self.case_execution_id,
                SupportTicket.order_id == order.id,
                SupportTicket.reason_code == arguments.reason_code,
            )
        )
        duplicate = ticket is not None
        if ticket is None:
            ticket = SupportTicket(
                case_execution_id=self.case_execution_id,
                customer_id=order.customer_id,
                order_id=order.id,
                reason_code=arguments.reason_code,
                reason=arguments.reason,
                priority=arguments.priority,
                status="escalated",
                tool_call_id=tool_call_id,
                escalated_at=datetime.now(UTC),
            )
            self.session.add(ticket)
            self.session.flush()

        result = EscalateTicketResult.model_validate(
            {
                "ticket_id": str(ticket.id),
                "order_id": order.external_id,
                "reason_code": ticket.reason_code,
                "reason": ticket.reason,
                "priority": ticket.priority,
                "status": "escalated",
                "duplicate": duplicate,
            }
        )
        self._complete_invocation(invocation, result)
        return result
