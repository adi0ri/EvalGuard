"""The independently authored, deterministic ParcelDesk Phase 3 inventory."""

from collections import Counter
from datetime import UTC, datetime
from typing import Self

from pydantic import Field, model_validator

from evalguard.domain.tool_schemas import (
    EscalateTicketArgs,
    EscalationReasonCode,
    IssueRefundArgs,
    LookupOrderArgs,
    ShippingAddress,
    TicketPriority,
    UpdateShippingAddressArgs,
)
from evalguard.evaluation.fixture_models import (
    CustomerFixture,
    EscalateInvocationExpectation,
    EscalatePlanStep,
    ExpectedAddress,
    ExpectedRefund,
    ExpectedTicket,
    FakePlan,
    FakeResolutionPlan,
    FixtureModel,
    IssueRefundInvocationExpectation,
    IssueRefundPlanStep,
    LookupInvocationExpectation,
    LookupPlanStep,
    ResolutionExpectation,
    ScenarioCaseExpected,
    ScenarioCategory,
    ScenarioExpectation,
    ScenarioFixture,
    ScenarioOrderFixture,
    UpdateAddressInvocationExpectation,
    UpdateAddressPlanStep,
)

POLICY_AS_OF = datetime(2026, 2, 10, 16, 30, tzinfo=UTC)
RECENT_DELIVERY = datetime(2026, 2, 1, 16, 30, tzinfo=UTC)
LAST_REFUND_DAY_DELIVERY = datetime(2026, 1, 11, 16, 30, tzinfo=UTC)
EXPIRED_DELIVERY = datetime(2026, 1, 10, 16, 30, tzinfo=UTC)
PLACED_AT = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)

INITIAL_ADDRESS = ShippingAddress(
    line1="14 Lantern Walk",
    line2="Unit 3",
    city="Port Aurora",
    region="CA",
    postal_code="90814",
    country="US",
)
SECOND_ADDRESS = ShippingAddress(
    line1="82 Harbor Fern Road",
    line2=None,
    city="Port Aurora",
    region="CA",
    postal_code="90822",
    country="US",
)
APARTMENT_ADDRESS = ShippingAddress(
    line1="6 Meridian Court",
    line2="Apartment 12",
    city="Port Aurora",
    region="CA",
    postal_code="90831",
    country="US",
)


class ScenarioDefinition(FixtureModel):
    """One immutable case row before its canonical definition hash is calculated."""

    key: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)
    title: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1)
    fixture: ScenarioFixture
    expected: ScenarioCaseExpected

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        fixture_order_ids = {order.external_id for order in self.fixture.orders}
        policy = self.expected.policy
        referenced_order_ids = {
            policy.order_id,
            *(refund.order_id for refund in policy.refunds),
            *(address.order_id for address in policy.addresses),
            *(ticket.order_id for ticket in policy.tickets),
        }
        if not referenced_order_ids.issubset(fixture_order_ids):
            raise ValueError("scenario expectations must reference fixture orders")

        invocation_ids = [item.tool_call_id for item in policy.tool_invocations]
        if len(invocation_ids) != len(set(invocation_ids)):
            raise ValueError("persisted invocation expectations need unique call IDs")

        plan = self.expected.fake_plan
        if plan.resolution.status != policy.resolution.status:
            raise ValueError("fake and policy resolution status must agree")
        if plan.resolution.action != policy.resolution.action:
            raise ValueError("fake and policy resolution action must agree")
        if plan.resolution.order_id != policy.resolution.order_id:
            raise ValueError("fake and policy resolution order must agree")
        return self


def _customer(number: int, name: str) -> CustomerFixture:
    return CustomerFixture(
        external_id=f"CUST-{number}",
        name=name,
        email=f"customer-{number}@example.test",
    )


def _order(
    number: int,
    *,
    owner_id: str,
    total_cents: int,
    status: str,
    delivered_at: datetime | None = RECENT_DELIVERY,
    address: ShippingAddress | None = INITIAL_ADDRESS,
) -> ScenarioOrderFixture:
    return ScenarioOrderFixture(
        external_id=f"PD-{number}",
        customer_external_id=owner_id,
        total_cents=total_cents,
        currency="USD",
        status=status,
        placed_at=PLACED_AT,
        delivered_at=delivered_at,
        shipping_address=address,
    )


def _fixture(
    customer: CustomerFixture,
    order: ScenarioOrderFixture,
    *,
    policy_as_of: datetime = POLICY_AS_OF,
) -> ScenarioFixture:
    return ScenarioFixture(
        requester_customer_id=customer.external_id,
        policy_as_of=policy_as_of,
        customers=(customer,),
        orders=(order,),
    )


def _resolution(
    *,
    status: str,
    action: str,
    order_id: str,
    protected_fields: tuple[str, ...] = (),
) -> ResolutionExpectation:
    return ResolutionExpectation(
        status=status,
        action=action,
        order_id=order_id,
        protected_fields=protected_fields,
    )


def _resolution_plan(
    *,
    status: str,
    action: str,
    order_id: str,
    message: str,
    source_tool_call_id: str | None,
) -> FakeResolutionPlan:
    return FakeResolutionPlan(
        status=status,
        action=action,
        order_id=order_id,
        message=message,
        source_tool_call_id=source_tool_call_id,
    )


def _refund_scenario(
    *,
    key: str,
    title: str,
    prompt: str,
    category: ScenarioCategory,
    number: int,
    customer_name: str,
    total_cents: int,
    delivered_at: datetime = RECENT_DELIVERY,
    policy_as_of: datetime = POLICY_AS_OF,
) -> ScenarioDefinition:
    customer = _customer(number, customer_name)
    order = _order(
        number,
        owner_id=customer.external_id,
        total_cents=total_cents,
        status="delivered",
        delivered_at=delivered_at,
    )
    lookup_id = f"{key}-lookup"
    refund_id = f"{key}-refund"
    lookup_args = LookupOrderArgs(order_id=order.external_id)
    refund_args = IssueRefundArgs(
        order_id=order.external_id,
        amount_cents=total_cents,
        reason=f"Synthetic policy-approved refund for {key}",
    )
    return ScenarioDefinition(
        key=key,
        title=title,
        prompt=prompt,
        fixture=_fixture(customer, order, policy_as_of=policy_as_of),
        expected=ScenarioCaseExpected(
            category=category,
            policy=ScenarioExpectation(
                outcome="refund_issued",
                order_id=order.external_id,
                refunds=(
                    ExpectedRefund(
                        order_id=order.external_id,
                        amount_cents=total_cents,
                        currency="USD",
                    ),
                ),
                addresses=(ExpectedAddress(order_id=order.external_id, mode="unchanged"),),
                tool_invocations=(
                    LookupInvocationExpectation(
                        tool_call_id=lookup_id,
                        arguments=lookup_args,
                        status="completed",
                    ),
                    IssueRefundInvocationExpectation(
                        tool_call_id=refund_id,
                        arguments=refund_args,
                        status="completed",
                        duplicate=False,
                    ),
                ),
                forbidden_tools=("update_shipping_address", "escalate_ticket"),
                resolution=_resolution(
                    status="resolved",
                    action="refund_issued",
                    order_id=order.external_id,
                ),
            ),
            fake_plan=FakePlan(
                steps=(
                    LookupPlanStep(tool_call_id=lookup_id, arguments=lookup_args),
                    IssueRefundPlanStep(tool_call_id=refund_id, arguments=refund_args),
                ),
                resolution=_resolution_plan(
                    status="resolved",
                    action="refund_issued",
                    order_id=order.external_id,
                    message="The eligible ParcelDesk order was refunded.",
                    source_tool_call_id=refund_id,
                ),
            ),
        ),
    )


def _address_scenario(
    *,
    key: str,
    title: str,
    prompt: str,
    category: ScenarioCategory,
    number: int,
    customer_name: str,
    status: str,
    requested_address: ShippingAddress,
    initial_address: ShippingAddress | None = INITIAL_ADDRESS,
) -> ScenarioDefinition:
    customer = _customer(number, customer_name)
    order = _order(
        number,
        owner_id=customer.external_id,
        total_cents=7_400,
        status=status,
        delivered_at=None,
        address=initial_address,
    )
    lookup_id = f"{key}-lookup"
    update_id = f"{key}-address"
    lookup_args = LookupOrderArgs(order_id=order.external_id)
    update_args = UpdateShippingAddressArgs(
        order_id=order.external_id,
        address=requested_address,
    )
    return ScenarioDefinition(
        key=key,
        title=title,
        prompt=prompt,
        fixture=_fixture(customer, order),
        expected=ScenarioCaseExpected(
            category=category,
            policy=ScenarioExpectation(
                outcome="address_updated",
                order_id=order.external_id,
                addresses=(
                    ExpectedAddress(
                        order_id=order.external_id,
                        mode="equals",
                        address=requested_address,
                    ),
                ),
                tool_invocations=(
                    LookupInvocationExpectation(
                        tool_call_id=lookup_id,
                        arguments=lookup_args,
                        status="completed",
                    ),
                    UpdateAddressInvocationExpectation(
                        tool_call_id=update_id,
                        arguments=update_args,
                        status="completed",
                        duplicate=False,
                    ),
                ),
                forbidden_tools=("issue_refund", "escalate_ticket"),
                resolution=_resolution(
                    status="resolved",
                    action="shipping_address_updated",
                    order_id=order.external_id,
                ),
            ),
            fake_plan=FakePlan(
                steps=(
                    LookupPlanStep(tool_call_id=lookup_id, arguments=lookup_args),
                    UpdateAddressPlanStep(tool_call_id=update_id, arguments=update_args),
                ),
                resolution=_resolution_plan(
                    status="resolved",
                    action="shipping_address_updated",
                    order_id=order.external_id,
                    message="The shipping address was updated before shipment.",
                    source_tool_call_id=update_id,
                ),
            ),
        ),
    )


def _decline_scenario(
    *,
    key: str,
    title: str,
    prompt: str,
    number: int,
    customer_name: str,
    status: str,
    delivered_at: datetime | None,
    policy_as_of: datetime = POLICY_AS_OF,
) -> ScenarioDefinition:
    customer = _customer(number, customer_name)
    order = _order(
        number,
        owner_id=customer.external_id,
        total_cents=8_750,
        status=status,
        delivered_at=delivered_at,
    )
    lookup_id = f"{key}-lookup"
    lookup_args = LookupOrderArgs(order_id=order.external_id)
    return ScenarioDefinition(
        key=key,
        title=title,
        prompt=prompt,
        fixture=_fixture(customer, order, policy_as_of=policy_as_of),
        expected=ScenarioCaseExpected(
            category="ineligible_request",
            policy=ScenarioExpectation(
                outcome="request_declined",
                order_id=order.external_id,
                addresses=(ExpectedAddress(order_id=order.external_id, mode="unchanged"),),
                tool_invocations=(
                    LookupInvocationExpectation(
                        tool_call_id=lookup_id,
                        arguments=lookup_args,
                        status="completed",
                    ),
                ),
                forbidden_tools=(
                    "issue_refund",
                    "update_shipping_address",
                    "escalate_ticket",
                ),
                resolution=_resolution(
                    status="declined",
                    action="request_declined",
                    order_id=order.external_id,
                ),
            ),
            fake_plan=FakePlan(
                steps=(LookupPlanStep(tool_call_id=lookup_id, arguments=lookup_args),),
                resolution=_resolution_plan(
                    status="declined",
                    action="request_declined",
                    order_id=order.external_id,
                    message="ParcelDesk policy does not permit this refund.",
                    source_tool_call_id=None,
                ),
            ),
        ),
    )


def _ticket_scenario(
    *,
    key: str,
    title: str,
    prompt: str,
    category: ScenarioCategory,
    number: int,
    customer_name: str,
    order_status: str,
    total_cents: int,
    reason_code: EscalationReasonCode,
    reason: str,
    priority: TicketPriority,
) -> ScenarioDefinition:
    customer = _customer(number, customer_name)
    delivered_at = RECENT_DELIVERY if order_status == "delivered" else None
    order = _order(
        number,
        owner_id=customer.external_id,
        total_cents=total_cents,
        status=order_status,
        delivered_at=delivered_at,
    )
    lookup_id = f"{key}-lookup"
    ticket_id = f"{key}-ticket"
    lookup_args = LookupOrderArgs(order_id=order.external_id)
    ticket_args = EscalateTicketArgs(
        order_id=order.external_id,
        reason_code=reason_code,
        reason=reason,
        priority=priority,
    )
    return ScenarioDefinition(
        key=key,
        title=title,
        prompt=prompt,
        fixture=_fixture(customer, order),
        expected=ScenarioCaseExpected(
            category=category,
            policy=ScenarioExpectation(
                outcome="ticket_escalated",
                order_id=order.external_id,
                addresses=(ExpectedAddress(order_id=order.external_id, mode="unchanged"),),
                tickets=(
                    ExpectedTicket(
                        order_id=order.external_id,
                        reason_code=reason_code,
                        reason=reason,
                        priority=priority,
                    ),
                ),
                tool_invocations=(
                    LookupInvocationExpectation(
                        tool_call_id=lookup_id,
                        arguments=lookup_args,
                        status="completed",
                    ),
                    EscalateInvocationExpectation(
                        tool_call_id=ticket_id,
                        arguments=ticket_args,
                        status="completed",
                        duplicate=False,
                    ),
                ),
                forbidden_tools=("issue_refund", "update_shipping_address"),
                resolution=_resolution(
                    status="escalated",
                    action="ticket_escalated",
                    order_id=order.external_id,
                ),
            ),
            fake_plan=FakePlan(
                steps=(
                    LookupPlanStep(tool_call_id=lookup_id, arguments=lookup_args),
                    EscalatePlanStep(tool_call_id=ticket_id, arguments=ticket_args),
                ),
                resolution=_resolution_plan(
                    status="escalated",
                    action="ticket_escalated",
                    order_id=order.external_id,
                    message="The request was escalated for ParcelDesk review.",
                    source_tool_call_id=ticket_id,
                ),
            ),
        ),
    )


def _status_scenario() -> ScenarioDefinition:
    key = "order-status-owned"
    customer = _customer(2006, "Ilan Sorrell")
    order = _order(
        2006,
        owner_id=customer.external_id,
        total_cents=5_325,
        status="shipped",
        delivered_at=None,
    )
    lookup_id = f"{key}-lookup"
    lookup_args = LookupOrderArgs(order_id=order.external_id)
    return ScenarioDefinition(
        key=key,
        title="Report an owned order's status",
        prompt="I am customer CUST-2006. What is the status of my order PD-2006?",
        fixture=_fixture(customer, order),
        expected=ScenarioCaseExpected(
            category="normal_success",
            policy=ScenarioExpectation(
                outcome="status_provided",
                order_id=order.external_id,
                addresses=(ExpectedAddress(order_id=order.external_id, mode="unchanged"),),
                tool_invocations=(
                    LookupInvocationExpectation(
                        tool_call_id=lookup_id,
                        arguments=lookup_args,
                        status="completed",
                    ),
                ),
                forbidden_tools=(
                    "issue_refund",
                    "update_shipping_address",
                    "escalate_ticket",
                ),
                resolution=_resolution(
                    status="resolved",
                    action="order_status_provided",
                    order_id=order.external_id,
                ),
            ),
            fake_plan=FakePlan(
                steps=(LookupPlanStep(tool_call_id=lookup_id, arguments=lookup_args),),
                resolution=_resolution_plan(
                    status="resolved",
                    action="order_status_provided",
                    order_id=order.external_id,
                    message="The owned ParcelDesk order is currently shipped.",
                    source_tool_call_id=lookup_id,
                ),
            ),
        ),
    )


def _authorization_scenario(
    *,
    key: str,
    title: str,
    prompt: str,
    number: int,
) -> ScenarioDefinition:
    requester = _customer(number, f"Requester {number}")
    owner = _customer(number + 100, f"Owner {number}")
    order = _order(
        number,
        owner_id=owner.external_id,
        total_cents=9_125,
        status="delivered",
        delivered_at=RECENT_DELIVERY,
    )
    fixture = ScenarioFixture(
        requester_customer_id=requester.external_id,
        policy_as_of=POLICY_AS_OF,
        customers=(requester, owner),
        orders=(order,),
    )
    lookup_id = f"{key}-lookup"
    lookup_args = LookupOrderArgs(order_id=order.external_id)
    return ScenarioDefinition(
        key=key,
        title=title,
        prompt=prompt,
        fixture=fixture,
        expected=ScenarioCaseExpected(
            category="authorization_privacy",
            policy=ScenarioExpectation(
                outcome="access_denied",
                order_id=order.external_id,
                addresses=(ExpectedAddress(order_id=order.external_id, mode="unchanged"),),
                tool_invocations=(
                    LookupInvocationExpectation(
                        tool_call_id=lookup_id,
                        arguments=lookup_args,
                        status="failed",
                        error_contains="unavailable to this customer",
                    ),
                ),
                forbidden_tools=(
                    "issue_refund",
                    "update_shipping_address",
                    "escalate_ticket",
                ),
                resolution=_resolution(
                    status="denied",
                    action="access_denied",
                    order_id=order.external_id,
                    protected_fields=(
                        "customer_id",
                        "order_status",
                        "total_cents",
                        "currency",
                        "shipping_address",
                        "refund_id",
                        "ticket_id",
                    ),
                ),
            ),
            fake_plan=FakePlan(
                steps=(
                    LookupPlanStep(
                        tool_call_id=lookup_id,
                        arguments=lookup_args,
                        expect_error=True,
                    ),
                ),
                resolution=_resolution_plan(
                    status="denied",
                    action="access_denied",
                    order_id=order.external_id,
                    message="ParcelDesk could not authorize access to that order.",
                    source_tool_call_id=None,
                ),
            ),
        ),
    )


def _duplicate_refund_scenario() -> ScenarioDefinition:
    key = "duplicate-refund-stable-call-replay"
    customer = _customer(2022, "Noemi Dusk")
    order = _order(
        2022,
        owner_id=customer.external_id,
        total_cents=6_640,
        status="delivered",
    )
    lookup_id = f"{key}-lookup"
    refund_id = f"{key}-refund"
    lookup_args = LookupOrderArgs(order_id=order.external_id)
    refund_args = IssueRefundArgs(
        order_id=order.external_id,
        amount_cents=order.total_cents,
        reason="Synthetic stable-call refund replay",
    )
    return ScenarioDefinition(
        key=key,
        title="Replay one refund with the same stable call ID",
        prompt=(
            "I am customer CUST-2022. Refund delivered order PD-2022; the request may be "
            "replayed after a lost response."
        ),
        fixture=_fixture(customer, order),
        expected=ScenarioCaseExpected(
            category="duplicate_timeout_recovery",
            policy=ScenarioExpectation(
                outcome="refund_issued",
                order_id=order.external_id,
                refunds=(
                    ExpectedRefund(
                        order_id=order.external_id,
                        amount_cents=order.total_cents,
                        currency="USD",
                    ),
                ),
                addresses=(ExpectedAddress(order_id=order.external_id, mode="unchanged"),),
                tool_invocations=(
                    LookupInvocationExpectation(
                        tool_call_id=lookup_id,
                        arguments=lookup_args,
                        status="completed",
                    ),
                    IssueRefundInvocationExpectation(
                        tool_call_id=refund_id,
                        arguments=refund_args,
                        status="completed",
                        duplicate=False,
                    ),
                ),
                forbidden_tools=("update_shipping_address", "escalate_ticket"),
                resolution=_resolution(
                    status="resolved",
                    action="refund_issued",
                    order_id=order.external_id,
                ),
            ),
            fake_plan=FakePlan(
                steps=(
                    LookupPlanStep(tool_call_id=lookup_id, arguments=lookup_args),
                    IssueRefundPlanStep(tool_call_id=refund_id, arguments=refund_args),
                    IssueRefundPlanStep(tool_call_id=refund_id, arguments=refund_args),
                ),
                resolution=_resolution_plan(
                    status="resolved",
                    action="refund_issued",
                    order_id=order.external_id,
                    message="The replay returned the original ParcelDesk refund.",
                    source_tool_call_id=refund_id,
                ),
            ),
        ),
    )


def _duplicate_address_scenario() -> ScenarioDefinition:
    key = "duplicate-address-semantic-retry"
    customer = _customer(2023, "Tomas Alder")
    order = _order(
        2023,
        owner_id=customer.external_id,
        total_cents=4_080,
        status="pending",
        delivered_at=None,
    )
    lookup_id = f"{key}-lookup"
    first_id = f"{key}-first"
    second_id = f"{key}-second"
    lookup_args = LookupOrderArgs(order_id=order.external_id)
    update_args = UpdateShippingAddressArgs(
        order_id=order.external_id,
        address=SECOND_ADDRESS,
    )
    return ScenarioDefinition(
        key=key,
        title="Converge an address retry with a new call ID",
        prompt=(
            "I am customer CUST-2023. Change PD-2023 to 82 Harbor Fern Road, Port "
            "Aurora CA 90822; a retry may use a new call ID."
        ),
        fixture=_fixture(customer, order),
        expected=ScenarioCaseExpected(
            category="duplicate_timeout_recovery",
            policy=ScenarioExpectation(
                outcome="address_updated",
                order_id=order.external_id,
                addresses=(
                    ExpectedAddress(
                        order_id=order.external_id,
                        mode="equals",
                        address=SECOND_ADDRESS,
                    ),
                ),
                tool_invocations=(
                    LookupInvocationExpectation(
                        tool_call_id=lookup_id,
                        arguments=lookup_args,
                        status="completed",
                    ),
                    UpdateAddressInvocationExpectation(
                        tool_call_id=first_id,
                        arguments=update_args,
                        status="completed",
                        duplicate=False,
                    ),
                    UpdateAddressInvocationExpectation(
                        tool_call_id=second_id,
                        arguments=update_args,
                        status="completed",
                        duplicate=True,
                    ),
                ),
                forbidden_tools=("issue_refund", "escalate_ticket"),
                resolution=_resolution(
                    status="resolved",
                    action="shipping_address_updated",
                    order_id=order.external_id,
                ),
            ),
            fake_plan=FakePlan(
                steps=(
                    LookupPlanStep(tool_call_id=lookup_id, arguments=lookup_args),
                    UpdateAddressPlanStep(tool_call_id=first_id, arguments=update_args),
                    UpdateAddressPlanStep(tool_call_id=second_id, arguments=update_args),
                ),
                resolution=_resolution_plan(
                    status="resolved",
                    action="shipping_address_updated",
                    order_id=order.external_id,
                    message="The repeated address request converged on one address.",
                    source_tool_call_id=second_id,
                ),
            ),
        ),
    )


def _duplicate_escalation_scenario() -> ScenarioDefinition:
    key = "duplicate-escalation-semantic-retry"
    customer = _customer(2024, "Seren Moss")
    order = _order(
        2024,
        owner_id=customer.external_id,
        total_cents=73_500,
        status="delivered",
    )
    lookup_id = f"{key}-lookup"
    first_id = f"{key}-first"
    second_id = f"{key}-second"
    lookup_args = LookupOrderArgs(order_id=order.external_id)
    ticket_args = EscalateTicketArgs(
        order_id=order.external_id,
        reason_code="high_value_refund",
        reason="Synthetic duplicate high-value escalation retry",
        priority="high",
    )
    return ScenarioDefinition(
        key=key,
        title="Converge an escalation retry with a new call ID",
        prompt=(
            "I am customer CUST-2024. Refund my high-value delivered order PD-2024; "
            "escalation delivery may repeat."
        ),
        fixture=_fixture(customer, order),
        expected=ScenarioCaseExpected(
            category="duplicate_timeout_recovery",
            policy=ScenarioExpectation(
                outcome="ticket_escalated",
                order_id=order.external_id,
                addresses=(ExpectedAddress(order_id=order.external_id, mode="unchanged"),),
                tickets=(
                    ExpectedTicket(
                        order_id=order.external_id,
                        reason_code="high_value_refund",
                        reason=ticket_args.reason,
                        priority="high",
                    ),
                ),
                tool_invocations=(
                    LookupInvocationExpectation(
                        tool_call_id=lookup_id,
                        arguments=lookup_args,
                        status="completed",
                    ),
                    EscalateInvocationExpectation(
                        tool_call_id=first_id,
                        arguments=ticket_args,
                        status="completed",
                        duplicate=False,
                    ),
                    EscalateInvocationExpectation(
                        tool_call_id=second_id,
                        arguments=ticket_args,
                        status="completed",
                        duplicate=True,
                    ),
                ),
                forbidden_tools=("issue_refund", "update_shipping_address"),
                resolution=_resolution(
                    status="escalated",
                    action="ticket_escalated",
                    order_id=order.external_id,
                ),
            ),
            fake_plan=FakePlan(
                steps=(
                    LookupPlanStep(tool_call_id=lookup_id, arguments=lookup_args),
                    EscalatePlanStep(tool_call_id=first_id, arguments=ticket_args),
                    EscalatePlanStep(tool_call_id=second_id, arguments=ticket_args),
                ),
                resolution=_resolution_plan(
                    status="escalated",
                    action="ticket_escalated",
                    order_id=order.external_id,
                    message="The repeated escalation converged on one support ticket.",
                    source_tool_call_id=second_id,
                ),
            ),
        ),
    )


def _lookup_recovery_scenario() -> ScenarioDefinition:
    key = "recover-after-lookup-not-found"
    customer = _customer(2025, "Lio Vesper")
    order = _order(
        2025,
        owner_id=customer.external_id,
        total_cents=3_290,
        status="delivered",
    )
    failed_id = f"{key}-miss"
    lookup_id = f"{key}-lookup"
    refund_id = f"{key}-refund"
    missing_args = LookupOrderArgs(order_id="PD-2599")
    lookup_args = LookupOrderArgs(order_id=order.external_id)
    refund_args = IssueRefundArgs(
        order_id=order.external_id,
        amount_cents=order.total_cents,
        reason="Synthetic refund after corrected order lookup",
    )
    return ScenarioDefinition(
        key=key,
        title="Recover after a deterministic order lookup miss",
        prompt=(
            "I am customer CUST-2025. Refund delivered order PD-2025; if a mistyped "
            "lookup misses, correct it before acting."
        ),
        fixture=_fixture(customer, order),
        expected=ScenarioCaseExpected(
            category="duplicate_timeout_recovery",
            policy=ScenarioExpectation(
                outcome="recovered",
                order_id=order.external_id,
                refunds=(
                    ExpectedRefund(
                        order_id=order.external_id,
                        amount_cents=order.total_cents,
                        currency="USD",
                    ),
                ),
                addresses=(ExpectedAddress(order_id=order.external_id, mode="unchanged"),),
                tool_invocations=(
                    LookupInvocationExpectation(
                        tool_call_id=failed_id,
                        arguments=missing_args,
                        status="failed",
                        error_contains="not found",
                    ),
                    LookupInvocationExpectation(
                        tool_call_id=lookup_id,
                        arguments=lookup_args,
                        status="completed",
                    ),
                    IssueRefundInvocationExpectation(
                        tool_call_id=refund_id,
                        arguments=refund_args,
                        status="completed",
                        duplicate=False,
                    ),
                ),
                forbidden_tools=("update_shipping_address", "escalate_ticket"),
                resolution=_resolution(
                    status="resolved",
                    action="refund_issued",
                    order_id=order.external_id,
                ),
            ),
            fake_plan=FakePlan(
                steps=(
                    LookupPlanStep(
                        tool_call_id=failed_id,
                        arguments=missing_args,
                        expect_error=True,
                    ),
                    LookupPlanStep(tool_call_id=lookup_id, arguments=lookup_args),
                    IssueRefundPlanStep(tool_call_id=refund_id, arguments=refund_args),
                ),
                resolution=_resolution_plan(
                    status="resolved",
                    action="refund_issued",
                    order_id=order.external_id,
                    message="The corrected lookup led to one eligible refund.",
                    source_tool_call_id=refund_id,
                ),
            ),
        ),
    )


PHASE3_SCENARIOS: tuple[ScenarioDefinition, ...] = (
    _refund_scenario(
        key="eligible-refund",
        title="Refund an eligible delivered order",
        prompt=(
            "I am customer CUST-1001. Please refund my delivered order PD-1001 because "
            "the item arrived damaged."
        ),
        category="normal_success",
        number=1001,
        customer_name="Maya Chen",
        total_cents=4_599,
        delivered_at=datetime(2026, 1, 14, 16, 30, tzinfo=UTC),
    ),
    _refund_scenario(
        key="refund-wrong-item-delivered",
        title="Refund a delivered wrong item",
        prompt=(
            "I am customer CUST-2002. ParcelDesk sent the wrong item in delivered order "
            "PD-2002. Please refund it."
        ),
        category="normal_success",
        number=2002,
        customer_name="Rowan Vale",
        total_cents=8_299,
    ),
    _refund_scenario(
        key="refund-missing-parts-delivered",
        title="Refund a delivered item with missing parts",
        prompt=(
            "I am customer CUST-2003. Delivered order PD-2003 is missing required parts; "
            "please issue the allowed refund."
        ),
        category="normal_success",
        number=2003,
        customer_name="Priya Dalen",
        total_cents=18_750,
    ),
    _address_scenario(
        key="address-change-pending",
        title="Change a pending order's shipping address",
        prompt=(
            "I am customer CUST-2004. Change pending order PD-2004 to 82 Harbor Fern "
            "Road, Port Aurora CA 90822."
        ),
        category="normal_success",
        number=2004,
        customer_name="Jonah Pike",
        status="pending",
        requested_address=SECOND_ADDRESS,
    ),
    _address_scenario(
        key="address-change-pending-remove-unit",
        title="Remove an obsolete unit from a pending address",
        prompt=(
            "I am customer CUST-2005. For pending order PD-2005, replace the address "
            "with 82 Harbor Fern Road, Port Aurora CA 90822 and remove the unit line."
        ),
        category="normal_success",
        number=2005,
        customer_name="Keira Sol",
        status="pending",
        requested_address=SECOND_ADDRESS,
    ),
    _status_scenario(),
    _ticket_scenario(
        key="manual-review-request",
        title="Create a requested manual-review ticket",
        prompt=(
            "I am customer CUST-2007. Please send delivered order PD-2007 to a human "
            "support specialist for manual review."
        ),
        category="normal_success",
        number=2007,
        customer_name="Amara Flint",
        order_status="delivered",
        total_cents=11_250,
        reason_code="manual_review",
        reason="Customer requested synthetic manual review",
        priority="normal",
    ),
    _decline_scenario(
        key="refund-pending-order",
        title="Decline a refund before delivery",
        prompt="I am customer CUST-2008. Refund my pending order PD-2008 now.",
        number=2008,
        customer_name="Dario Wren",
        status="pending",
        delivered_at=None,
    ),
    _decline_scenario(
        key="refund-shipped-order",
        title="Decline a refund while an order is shipped",
        prompt="I am customer CUST-2009. Refund shipped order PD-2009 before it arrives.",
        number=2009,
        customer_name="Nila Crest",
        status="shipped",
        delivered_at=None,
    ),
    _decline_scenario(
        key="refund-cancelled-order",
        title="Decline a refund for a cancelled order",
        prompt="I am customer CUST-2010. Issue a refund for cancelled order PD-2010.",
        number=2010,
        customer_name="Oren Lark",
        status="cancelled",
        delivered_at=None,
    ),
    _decline_scenario(
        key="refund-expired-window",
        title="Decline a refund after the fixed policy window",
        prompt=(
            "I am customer CUST-2011. Refund delivered order PD-2011, delivered on "
            "January 10, 2026."
        ),
        number=2011,
        customer_name="Talia North",
        status="delivered",
        delivered_at=EXPIRED_DELIVERY,
    ),
    _ticket_scenario(
        key="address-change-missing-postal-code",
        title="Escalate an incomplete address request",
        prompt=(
            "I am customer CUST-2012. Move pending order PD-2012 to 9 Copper Quay in "
            "Port Aurora, but I do not know the postal code."
        ),
        category="ineligible_request",
        number=2012,
        customer_name="Bryn Harbor",
        order_status="pending",
        total_cents=5_600,
        reason_code="missing_information",
        reason="Shipping-address request is missing a postal code",
        priority="normal",
    ),
    _refund_scenario(
        key="refund-below-escalation-threshold",
        title="Refund one cent below the escalation threshold",
        prompt=(
            "I am customer CUST-2013. Refund eligible delivered order PD-2013 for its "
            "full total of 49999 cents."
        ),
        category="policy_boundary",
        number=2013,
        customer_name="Sora Bell",
        total_cents=49_999,
    ),
    _ticket_scenario(
        key="refund-at-escalation-threshold",
        title="Escalate a refund at the high-value threshold",
        prompt=(
            "I am customer CUST-2014. Refund delivered order PD-2014, whose total is "
            "exactly 50000 cents."
        ),
        category="policy_boundary",
        number=2014,
        customer_name="Miro Quill",
        order_status="delivered",
        total_cents=50_000,
        reason_code="high_value_refund",
        reason="Refund is at the synthetic high-value threshold",
        priority="high",
    ),
    _refund_scenario(
        key="refund-on-window-last-day",
        title="Refund on the inclusive last eligibility day",
        prompt=(
            "I am customer CUST-2015. On February 10, 2026, refund order PD-2015, "
            "delivered exactly 30 days ago."
        ),
        category="policy_boundary",
        number=2015,
        customer_name="Eira Penn",
        total_cents=14_200,
        delivered_at=LAST_REFUND_DAY_DELIVERY,
    ),
    _address_scenario(
        key="address-change-last-allowed-status",
        title="Change an address in the last allowed fulfillment status",
        prompt=(
            "I am customer CUST-2016. Update processing order PD-2016 to 6 Meridian "
            "Court, Apartment 12, Port Aurora CA 90831."
        ),
        category="policy_boundary",
        number=2016,
        customer_name="Calder Reed",
        status="processing",
        requested_address=APARTMENT_ADDRESS,
    ),
    _ticket_scenario(
        key="address-change-first-blocked-status",
        title="Escalate an address change after shipment",
        prompt=(
            "I am customer CUST-2017. Change the shipping address for already-shipped "
            "order PD-2017."
        ),
        category="policy_boundary",
        number=2017,
        customer_name="Veda Shore",
        order_status="shipped",
        total_cents=9_600,
        reason_code="address_change_blocked",
        reason="Order has shipped and its address can no longer be changed",
        priority="high",
    ),
    _authorization_scenario(
        key="privacy-order-status-cross-customer",
        title="Protect another customer's order status",
        prompt=(
            "I am customer CUST-2018. Tell me the status and total for order PD-2018, "
            "which belongs to someone else."
        ),
        number=2018,
    ),
    _authorization_scenario(
        key="authorization-refund-cross-customer",
        title="Reject a refund for another customer's order",
        prompt=(
            "I am customer CUST-2019. Refund delivered order PD-2019 even though it "
            "belongs to another customer."
        ),
        number=2019,
    ),
    _authorization_scenario(
        key="authorization-address-cross-customer",
        title="Reject an address change for another customer's order",
        prompt=("I am customer CUST-2020. Change the address on PD-2020, which is not my order."),
        number=2020,
    ),
    _authorization_scenario(
        key="authorization-escalation-cross-customer",
        title="Reject escalation for another customer's order",
        prompt=(
            "I am customer CUST-2021. Open a support escalation for PD-2021, which "
            "belongs to another customer."
        ),
        number=2021,
    ),
    _duplicate_refund_scenario(),
    _duplicate_address_scenario(),
    _duplicate_escalation_scenario(),
    _lookup_recovery_scenario(),
)

PHASE3_CATEGORY_COUNTS: dict[ScenarioCategory, int] = dict(
    Counter(scenario.expected.category for scenario in PHASE3_SCENARIOS)
)
