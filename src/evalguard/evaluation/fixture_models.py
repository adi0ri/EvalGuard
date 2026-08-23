"""Validation for source-controlled scenario fixtures and expectations."""

from collections import Counter
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evalguard.domain.tool_schemas import (
    EscalateTicketArgs,
    EscalationReasonCode,
    IssueRefundArgs,
    LookupOrderArgs,
    ShippingAddress,
    TicketPriority,
    UpdateShippingAddressArgs,
)


class FixtureModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CustomerFixture(FixtureModel):
    external_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)


class OrderFixture(FixtureModel):
    external_id: str = Field(min_length=1, max_length=64)
    total_cents: int = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    status: str
    placed_at: datetime
    delivered_at: datetime | None = None


class CaseFixture(FixtureModel):
    customer: CustomerFixture
    order: OrderFixture


class EligibleRefundExpectation(FixtureModel):
    order_id: str
    refund_amount_cents: int = Field(gt=0)
    required_tools: list[str]
    resolution_status: str


type ScenarioCategory = Literal[
    "normal_success",
    "ineligible_request",
    "policy_boundary",
    "authorization_privacy",
    "duplicate_timeout_recovery",
]
type PolicyOutcome = Literal[
    "refund_issued",
    "address_updated",
    "status_provided",
    "ticket_escalated",
    "request_declined",
    "access_denied",
    "recovered",
]
type ToolName = Literal[
    "lookup_order",
    "issue_refund",
    "update_shipping_address",
    "escalate_ticket",
]
type InvocationStatus = Literal["completed", "failed"]


class ScenarioOrderFixture(OrderFixture):
    """One relational order and the fixture customer that owns it."""

    customer_external_id: str = Field(min_length=1, max_length=64)
    shipping_address: ShippingAddress | None = None


class ScenarioFixture(FixtureModel):
    """Complete isolated business state and deterministic policy clock for a case."""

    requester_customer_id: str = Field(min_length=1, max_length=64)
    policy_as_of: datetime
    refund_window_days: int = Field(default=30, ge=1, le=365)
    customers: tuple[CustomerFixture, ...] = Field(min_length=1)
    orders: tuple[ScenarioOrderFixture, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_relational_ownership(self) -> Self:
        customer_ids = [customer.external_id for customer in self.customers]
        if len(customer_ids) != len(set(customer_ids)):
            raise ValueError("scenario customer external IDs must be unique")
        order_ids = [order.external_id for order in self.orders]
        if len(order_ids) != len(set(order_ids)):
            raise ValueError("scenario order external IDs must be unique")
        if self.requester_customer_id not in customer_ids:
            raise ValueError("requester_customer_id must identify a fixture customer")
        unknown_owners = {
            order.customer_external_id
            for order in self.orders
            if order.customer_external_id not in customer_ids
        }
        if unknown_owners:
            raise ValueError("every scenario order must reference a fixture customer")
        return self


class ExpectedRefund(FixtureModel):
    order_id: str = Field(min_length=1, max_length=64)
    amount_cents: int = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    status: Literal["completed"] = "completed"


class ExpectedAddress(FixtureModel):
    order_id: str = Field(min_length=1, max_length=64)
    mode: Literal["unchanged", "equals"]
    address: ShippingAddress | None = None

    @model_validator(mode="after")
    def require_address_for_equality(self) -> Self:
        if self.mode == "equals" and self.address is None:
            raise ValueError("an equals address expectation requires an address")
        return self


class ExpectedTicket(FixtureModel):
    order_id: str = Field(min_length=1, max_length=64)
    reason_code: EscalationReasonCode
    reason: str = Field(min_length=1, max_length=500)
    priority: TicketPriority
    status: Literal["escalated"] = "escalated"


class ExpectedInvocationBase(FixtureModel):
    tool_call_id: str = Field(min_length=1, max_length=100)
    status: InvocationStatus
    duplicate: bool | None = None
    error_contains: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_outcome_details(self) -> Self:
        if self.status == "failed" and self.error_contains is None:
            raise ValueError("a failed invocation expectation requires error_contains")
        if self.status == "failed" and self.duplicate is not None:
            raise ValueError("a failed invocation cannot carry a duplicate result")
        return self


class LookupInvocationExpectation(ExpectedInvocationBase):
    tool_name: Literal["lookup_order"] = "lookup_order"
    arguments: LookupOrderArgs


class IssueRefundInvocationExpectation(ExpectedInvocationBase):
    tool_name: Literal["issue_refund"] = "issue_refund"
    arguments: IssueRefundArgs


class UpdateAddressInvocationExpectation(ExpectedInvocationBase):
    tool_name: Literal["update_shipping_address"] = "update_shipping_address"
    arguments: UpdateShippingAddressArgs


class EscalateInvocationExpectation(ExpectedInvocationBase):
    tool_name: Literal["escalate_ticket"] = "escalate_ticket"
    arguments: EscalateTicketArgs


type ExpectedToolInvocation = Annotated[
    LookupInvocationExpectation
    | IssueRefundInvocationExpectation
    | UpdateAddressInvocationExpectation
    | EscalateInvocationExpectation,
    Field(discriminator="tool_name"),
]


class ToolCallBounds(FixtureModel):
    tool_name: ToolName
    minimum: int = Field(ge=0)
    maximum: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.maximum < self.minimum:
            raise ValueError("maximum tool-call count cannot be below minimum")
        return self


class ResolutionExpectation(FixtureModel):
    status: str = Field(min_length=1, max_length=50)
    action: str = Field(min_length=1, max_length=100)
    order_id: str | None = Field(default=None, min_length=1, max_length=64)
    protected_fields: tuple[str, ...] = ()


class ScenarioExpectation(FixtureModel):
    """Independent deterministic oracle over state, calls, and final resolution."""

    outcome: PolicyOutcome
    order_id: str = Field(min_length=1, max_length=64)
    refunds: tuple[ExpectedRefund, ...] = ()
    addresses: tuple[ExpectedAddress, ...] = ()
    tickets: tuple[ExpectedTicket, ...] = ()
    tool_invocations: tuple[ExpectedToolInvocation, ...] = ()
    forbidden_tools: tuple[ToolName, ...] = ()
    tool_call_bounds: tuple[ToolCallBounds, ...] = ()
    resolution: ResolutionExpectation

    @model_validator(mode="after")
    def validate_tool_contract(self) -> Self:
        expected_action = {
            "refund_issued": "refund_issued",
            "address_updated": "shipping_address_updated",
            "status_provided": "order_status_provided",
            "ticket_escalated": "ticket_escalated",
            "request_declined": "request_declined",
            "access_denied": "access_denied",
            "recovered": "refund_issued",
        }[self.outcome]
        if self.resolution.action != expected_action:
            raise ValueError("policy outcome and resolution action must agree")
        if self.resolution.order_id != self.order_id:
            raise ValueError("resolution order must match the policy target order")
        if self.outcome in {"refund_issued", "recovered"} and not self.refunds:
            raise ValueError("a refund outcome requires expected refund state")
        if self.outcome == "address_updated" and not any(
            row.mode == "equals" for row in self.addresses
        ):
            raise ValueError("an address-updated outcome requires an exact final address")
        if self.outcome == "ticket_escalated" and not self.tickets:
            raise ValueError("a ticket-escalated outcome requires expected ticket state")
        mutation_order_ids = {
            *(row.order_id for row in self.refunds),
            *(row.order_id for row in self.tickets),
            *(row.order_id for row in self.addresses if row.mode == "equals"),
        }
        if mutation_order_ids - {self.order_id}:
            raise ValueError("expected state mutations must target the policy order")
        denied_address_mutation = any(row.mode == "equals" for row in self.addresses)
        if self.outcome in {"request_declined", "access_denied"} and (
            self.refunds or self.tickets or denied_address_mutation
        ):
            raise ValueError("a denied outcome cannot expect state mutations")

        observed_names: list[ToolName] = [
            invocation.tool_name for invocation in self.tool_invocations
        ]
        forbidden = set(self.forbidden_tools)
        if forbidden.intersection(observed_names):
            raise ValueError("a required invocation cannot also be forbidden")
        if len(forbidden) != len(self.forbidden_tools):
            raise ValueError("forbidden tool names must be unique")

        if not self.tool_call_bounds:
            counts: Counter[ToolName] = Counter(observed_names)
            all_tool_names: tuple[ToolName, ...] = (
                "lookup_order",
                "issue_refund",
                "update_shipping_address",
                "escalate_ticket",
            )
            object.__setattr__(
                self,
                "tool_call_bounds",
                tuple(
                    ToolCallBounds(
                        tool_name=tool_name,
                        minimum=counts[tool_name],
                        maximum=counts[tool_name],
                    )
                    for tool_name in all_tool_names
                ),
            )
        elif len({bound.tool_name for bound in self.tool_call_bounds}) != len(
            self.tool_call_bounds
        ):
            raise ValueError("tool-call bounds must name each tool at most once")
        return self


class LookupPlanStep(FixtureModel):
    tool_name: Literal["lookup_order"] = "lookup_order"
    tool_call_id: str = Field(min_length=1, max_length=100)
    arguments: LookupOrderArgs
    expect_error: bool = False


class IssueRefundPlanStep(FixtureModel):
    tool_name: Literal["issue_refund"] = "issue_refund"
    tool_call_id: str = Field(min_length=1, max_length=100)
    arguments: IssueRefundArgs
    expect_error: bool = False


class UpdateAddressPlanStep(FixtureModel):
    tool_name: Literal["update_shipping_address"] = "update_shipping_address"
    tool_call_id: str = Field(min_length=1, max_length=100)
    arguments: UpdateShippingAddressArgs
    expect_error: bool = False


class EscalatePlanStep(FixtureModel):
    tool_name: Literal["escalate_ticket"] = "escalate_ticket"
    tool_call_id: str = Field(min_length=1, max_length=100)
    arguments: EscalateTicketArgs
    expect_error: bool = False


type FakePlanStep = Annotated[
    LookupPlanStep | IssueRefundPlanStep | UpdateAddressPlanStep | EscalatePlanStep,
    Field(discriminator="tool_name"),
]


class FakeResolutionPlan(FixtureModel):
    status: str = Field(min_length=1, max_length=50)
    action: str = Field(min_length=1, max_length=100)
    order_id: str | None = Field(default=None, min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=500)
    source_tool_call_id: str | None = Field(default=None, min_length=1, max_length=100)


class FakePlan(FixtureModel):
    steps: tuple[FakePlanStep, ...] = ()
    resolution: FakeResolutionPlan

    @model_validator(mode="after")
    def validate_resolution_source(self) -> Self:
        source_call_id = self.resolution.source_tool_call_id
        if source_call_id is not None and not any(
            step.tool_call_id == source_call_id for step in self.steps
        ):
            raise ValueError("resolution source_tool_call_id must identify a plan step")
        return self


class ScenarioCaseExpected(FixtureModel):
    category: ScenarioCategory
    policy: ScenarioExpectation
    fake_plan: FakePlan
