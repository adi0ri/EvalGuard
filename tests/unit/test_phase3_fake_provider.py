"""Focused unit coverage for deterministic Phase 3 provider plans and defects."""

from unittest.mock import MagicMock, call

import pytest

from evalguard.domain.tool_schemas import (
    EscalateTicketResult,
    IssueRefundResult,
    LookupOrderResult,
    UpdateShippingAddressResult,
)
from evalguard.domain.tools import ToolExecutionError, ToolRuntime
from evalguard.evaluation.fixture_models import (
    EscalatePlanStep,
    IssueRefundPlanStep,
    UpdateAddressPlanStep,
)
from evalguard.evaluation.scenarios import PHASE3_SCENARIOS, ScenarioDefinition
from evalguard.providers.fake import DeterministicFakeProvider, FakeBehavior


def _scenario(key: str) -> ScenarioDefinition:
    return next(row for row in PHASE3_SCENARIOS if row.key == key)


def _tools() -> MagicMock:
    return MagicMock(spec=ToolRuntime)


def test_reference_scenario_executes_typed_plan_and_builds_state_reference() -> None:
    definition = _scenario("eligible-refund")
    tools = _tools()
    tools.lookup_order.return_value = LookupOrderResult(
        order_id="PD-1001",
        customer_id="CUST-1001",
        status="delivered",
        total_cents=4_599,
        currency="USD",
    )
    tools.issue_refund.return_value = IssueRefundResult(
        refund_id="refund-1",
        order_id="PD-1001",
        amount_cents=4_599,
        currency="USD",
        status="completed",
        duplicate=False,
    )

    resolution = DeterministicFakeProvider().run_scenario(
        definition=definition,
        tools=tools,
    )

    assert resolution == {
        "status": "resolved",
        "action": "refund_issued",
        "message": "The eligible ParcelDesk order was refunded.",
        "synthetic": True,
        "order_id": "PD-1001",
        "refund_id": "refund-1",
        "amount_cents": 4_599,
        "currency": "USD",
    }
    assert tools.method_calls[1:3] == [
        call.lookup_order(
            definition.expected.fake_plan.steps[0].arguments,
            tool_call_id="eligible-refund-lookup",
        ),
        call.issue_refund(
            definition.expected.fake_plan.steps[1].arguments,
            tool_call_id="eligible-refund-refund",
        ),
    ]
    assert tools.trace.call_args_list[0].args[0] == "provider_request"
    assert tools.trace.call_args_list[-1].args[0] == "provider_response"


@pytest.mark.parametrize(
    ("behavior", "case_key", "expected_names"),
    [
        (
            FakeBehavior.CLAIM_SUCCESS_WITHOUT_ACTION,
            "eligible-refund",
            ["lookup_order"],
        ),
        (
            FakeBehavior.WRONG_TOOL_ORDER,
            "eligible-refund",
            ["issue_refund", "lookup_order"],
        ),
        (
            FakeBehavior.OMIT_REQUIRED_ESCALATION,
            "refund-at-escalation-threshold",
            ["lookup_order"],
        ),
        (
            FakeBehavior.ATTEMPT_FORBIDDEN_ACTION,
            "refund-pending-order",
            ["lookup_order", "issue_refund"],
        ),
        (
            FakeBehavior.REPEAT_STATE_CHANGE_NEW_CALL_ID,
            "eligible-refund",
            ["lookup_order", "issue_refund", "issue_refund"],
        ),
    ],
)
def test_defect_modes_transform_the_reference_plan_deterministically(
    behavior: FakeBehavior,
    case_key: str,
    expected_names: list[str],
) -> None:
    provider = DeterministicFakeProvider(behavior_id=behavior)

    steps = provider._behavior_steps(_scenario(case_key))

    assert [step.tool_name for step in steps] == expected_names
    if behavior is FakeBehavior.REPEAT_STATE_CHANGE_NEW_CALL_ID:
        assert steps[-1].tool_call_id.endswith("-defect-repeat")
        assert steps[-1].tool_call_id != steps[-2].tool_call_id


def test_argument_defects_remain_valid_typed_tool_calls() -> None:
    wrong_refund = DeterministicFakeProvider(
        behavior_id=FakeBehavior.WRONG_REFUND_AMOUNT
    )._behavior_steps(_scenario("eligible-refund"))
    refund_step = next(step for step in wrong_refund if isinstance(step, IssueRefundPlanStep))
    assert refund_step.arguments.amount_cents == 4_600

    wrong_address = DeterministicFakeProvider(
        behavior_id=FakeBehavior.WRONG_SHIPPING_ADDRESS
    )._behavior_steps(_scenario("address-change-pending"))
    address_step = next(step for step in wrong_address if isinstance(step, UpdateAddressPlanStep))
    assert address_step.arguments.address.line1.endswith(" Defect")

    wrong_ticket = DeterministicFakeProvider(
        behavior_id=FakeBehavior.WRONG_ESCALATION_METADATA
    )._behavior_steps(_scenario("manual-review-request"))
    ticket_step = next(step for step in wrong_ticket if isinstance(step, EscalatePlanStep))
    assert ticket_step.arguments.reason_code != "manual_review"


def test_reference_plan_can_catch_an_expected_authorization_rejection() -> None:
    definition = _scenario("privacy-order-status-cross-customer")
    tools = _tools()
    tools.lookup_order.side_effect = ToolExecutionError(
        "Order was not found or is unavailable to this customer"
    )

    resolution = DeterministicFakeProvider().run_scenario(
        definition=definition,
        tools=tools,
    )

    assert resolution["status"] == "denied"
    assert resolution["action"] == "access_denied"
    assert "customer_id" not in resolution


def test_forbidden_attempt_defect_catches_runtime_rejection_for_grading() -> None:
    definition = _scenario("refund-pending-order")
    tools = _tools()
    tools.lookup_order.return_value = LookupOrderResult(
        order_id="PD-2008",
        customer_id="CUST-2008",
        status="pending",
        total_cents=8_750,
        currency="USD",
    )
    tools.issue_refund.side_effect = ToolExecutionError("Only delivered orders are refundable")

    resolution = DeterministicFakeProvider(
        behavior_id=FakeBehavior.ATTEMPT_FORBIDDEN_ACTION
    ).run_scenario(definition=definition, tools=tools)

    assert resolution["action"] == "request_declined"
    tools.issue_refund.assert_called_once()


def test_resolution_defects_are_deterministic_and_do_not_mutate_business_state() -> None:
    privacy = _scenario("privacy-order-status-cross-customer")
    privacy_tools = _tools()
    privacy_tools.lookup_order.side_effect = ToolExecutionError("unavailable to this customer")
    disclosed = DeterministicFakeProvider(
        behavior_id=FakeBehavior.DISCLOSE_PROTECTED_FIELD
    ).run_scenario(definition=privacy, tools=privacy_tools)
    assert disclosed[privacy.expected.policy.resolution.protected_fields[0]].endswith(
        "@example.test"
    )

    refund = _scenario("eligible-refund")
    refund_tools = _tools()
    refund_tools.lookup_order.return_value = LookupOrderResult(
        order_id="PD-1001",
        customer_id="CUST-1001",
        status="delivered",
        total_cents=4_599,
        currency="USD",
    )
    refund_tools.issue_refund.return_value = IssueRefundResult(
        refund_id="refund-real",
        order_id="PD-1001",
        amount_cents=4_599,
        currency="USD",
        status="completed",
    )
    mismatched = DeterministicFakeProvider(
        behavior_id=FakeBehavior.MISMATCHED_RESOLUTION_REFERENCE
    ).run_scenario(definition=refund, tools=refund_tools)
    assert mismatched["refund_id"] == "00000000-0000-0000-0000-000000000000"


def test_address_and_ticket_results_populate_structured_resolution() -> None:
    address_definition = _scenario("address-change-pending")
    address_tools = _tools()
    address_tools.lookup_order.return_value = LookupOrderResult(
        order_id="PD-2004",
        customer_id="CUST-2004",
        status="pending",
        total_cents=7_400,
        currency="USD",
    )
    requested = address_definition.expected.fake_plan.steps[1].arguments.address
    address_tools.update_shipping_address.return_value = UpdateShippingAddressResult(
        order_id="PD-2004",
        address=requested,
        status="updated",
    )
    address_resolution = DeterministicFakeProvider().run_scenario(
        definition=address_definition,
        tools=address_tools,
    )
    assert address_resolution["address"] == requested.model_dump(mode="json")

    ticket_definition = _scenario("manual-review-request")
    ticket_tools = _tools()
    ticket_tools.lookup_order.return_value = LookupOrderResult(
        order_id="PD-2007",
        customer_id="CUST-2007",
        status="delivered",
        total_cents=11_250,
        currency="USD",
    )
    ticket_tools.escalate_ticket.return_value = EscalateTicketResult(
        ticket_id="ticket-1",
        order_id="PD-2007",
        reason_code="manual_review",
        reason="Customer requested synthetic manual review",
        priority="normal",
        status="escalated",
    )
    ticket_resolution = DeterministicFakeProvider().run_scenario(
        definition=ticket_definition,
        tools=ticket_tools,
    )
    assert ticket_resolution["ticket_id"] == "ticket-1"
    assert ticket_resolution["reason_code"] == "manual_review"
