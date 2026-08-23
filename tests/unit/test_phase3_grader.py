"""Pure deterministic Phase 3 grader regression coverage."""

from dataclasses import replace
from typing import Any

import pytest

from evalguard.evaluation.grader import (
    CriterionResult,
    InvocationSnapshot,
    RefundSnapshot,
    ScenarioEvidence,
    ScenarioOrderSnapshot,
    TicketSnapshot,
    TraceSnapshot,
    grade_scenario,
)
from evalguard.evaluation.scenarios import PHASE3_SCENARIOS, ScenarioDefinition


def _scenario(key: str) -> ScenarioDefinition:
    return next(row for row in PHASE3_SCENARIOS if row.key == key)


def _invocation_result(
    definition: ScenarioDefinition,
    expected: Any,
    refund_ids: dict[str, str],
    ticket_ids: dict[str, str],
) -> dict[str, Any]:
    if expected.status == "failed":
        return {"error": f"Synthetic rejection: {expected.error_contains}"}
    order = next(
        row for row in definition.fixture.orders if row.external_id == expected.arguments.order_id
    )
    if expected.tool_name == "lookup_order":
        return {
            "order_id": order.external_id,
            "customer_id": order.customer_external_id,
            "status": order.status,
            "total_cents": order.total_cents,
            "currency": order.currency,
        }
    if expected.tool_name == "issue_refund":
        return {
            "refund_id": refund_ids[order.external_id],
            "order_id": order.external_id,
            "amount_cents": expected.arguments.amount_cents,
            "currency": order.currency,
            "status": "completed",
            "duplicate": bool(expected.duplicate),
        }
    if expected.tool_name == "update_shipping_address":
        return {
            "order_id": order.external_id,
            "address": expected.arguments.address.model_dump(mode="json"),
            "status": "updated",
            "duplicate": bool(expected.duplicate),
        }
    return {
        "ticket_id": ticket_ids[order.external_id],
        "order_id": order.external_id,
        "reason_code": expected.arguments.reason_code,
        "reason": expected.arguments.reason,
        "priority": expected.arguments.priority,
        "status": "escalated",
        "duplicate": bool(expected.duplicate),
    }


def _evidence(definition: ScenarioDefinition) -> ScenarioEvidence:
    policy = definition.expected.policy
    refund_ids = {row.order_id: f"refund-{row.order_id}" for row in policy.refunds}
    ticket_ids = {row.order_id: f"ticket-{row.order_id}" for row in policy.tickets}
    final_addresses = {row.external_id: row.shipping_address for row in definition.fixture.orders}
    for expected in policy.addresses:
        if expected.mode == "equals":
            final_addresses[expected.order_id] = expected.address

    orders = tuple(
        ScenarioOrderSnapshot(
            order_id=row.external_id,
            customer_id=row.customer_external_id,
            status=row.status,
            total_cents=row.total_cents,
            currency=row.currency,
            shipping_address=final_addresses[row.external_id],
        )
        for row in definition.fixture.orders
    )
    refunds = tuple(
        RefundSnapshot(
            refund_id=refund_ids[row.order_id],
            order_id=row.order_id,
            amount_cents=row.amount_cents,
            currency=row.currency,
            status=row.status,
        )
        for row in policy.refunds
    )
    owner_by_order = {
        row.external_id: row.customer_external_id for row in definition.fixture.orders
    }
    tickets = tuple(
        TicketSnapshot(
            ticket_id=ticket_ids[row.order_id],
            order_id=row.order_id,
            customer_id=owner_by_order[row.order_id],
            reason_code=row.reason_code,
            reason=row.reason,
            priority=row.priority,
            status=row.status,
        )
        for row in policy.tickets
    )
    invocations = tuple(
        InvocationSnapshot(
            tool_call_id=row.tool_call_id,
            tool_name=row.tool_name,
            arguments=row.arguments.model_dump(mode="json"),
            status=row.status,
            result=_invocation_result(definition, row, refund_ids, ticket_ids),
        )
        for row in policy.tool_invocations
    )

    trace_events: list[TraceSnapshot] = [
        TraceSnapshot(1, "provider_request", {"scenario_key": definition.key})
    ]
    for invocation in invocations:
        trace_events.append(
            TraceSnapshot(
                len(trace_events) + 1,
                "tool_call",
                {
                    "tool_call_id": invocation.tool_call_id,
                    "tool_name": invocation.tool_name,
                    "arguments": invocation.arguments,
                },
            )
        )
        trace_events.append(
            TraceSnapshot(
                len(trace_events) + 1,
                "tool_result" if invocation.status == "completed" else "tool_error",
                {
                    "tool_call_id": invocation.tool_call_id,
                    "tool_name": invocation.tool_name,
                },
            )
        )
    plan_call_ids = [step.tool_call_id for step in definition.expected.fake_plan.steps]
    replay_call_ids = [
        call_id for index, call_id in enumerate(plan_call_ids) if call_id in plan_call_ids[:index]
    ]
    invocation_by_id = {row.tool_call_id: row for row in invocations}
    for call_id in replay_call_ids:
        invocation = invocation_by_id[call_id]
        trace_events.append(
            TraceSnapshot(
                len(trace_events) + 1,
                "tool_replay",
                {
                    "tool_call_id": invocation.tool_call_id,
                    "tool_name": invocation.tool_name,
                    "arguments": invocation.arguments,
                    "result": invocation.result,
                },
            )
        )
    trace_events.append(
        TraceSnapshot(
            len(trace_events) + 1,
            "provider_response",
            {"scenario_key": definition.key},
        )
    )

    resolution: dict[str, Any] = {
        "status": policy.resolution.status,
        "action": policy.resolution.action,
        "synthetic": True,
    }
    if policy.resolution.order_id is not None:
        resolution["order_id"] = policy.resolution.order_id
    if policy.refunds:
        expected = policy.refunds[0]
        resolution.update(
            {
                "refund_id": refund_ids[expected.order_id],
                "amount_cents": expected.amount_cents,
                "currency": expected.currency,
            }
        )
    if policy.tickets:
        expected_ticket = policy.tickets[0]
        resolution.update(
            {
                "ticket_id": ticket_ids[expected_ticket.order_id],
                "reason_code": expected_ticket.reason_code,
                "priority": expected_ticket.priority,
            }
        )
    equals_address = next(
        (row.address for row in policy.addresses if row.mode == "equals"),
        None,
    )
    if equals_address is not None:
        resolution["address"] = equals_address.model_dump(mode="json")
    if policy.outcome == "status_provided":
        target = next(
            row for row in definition.fixture.orders if row.external_id == policy.order_id
        )
        resolution.update(
            {
                "order_status": target.status,
                "total_cents": target.total_cents,
                "currency": target.currency,
            }
        )

    return ScenarioEvidence(
        fixture=definition.fixture,
        expectation=policy,
        orders=orders,
        refunds=refunds,
        tickets=tickets,
        invocations=invocations,
        trace_events=tuple(trace_events),
        final_resolution=resolution,
        category=definition.expected.category,
    )


def _result(
    outcomes: tuple[CriterionResult, ...],
    grader_name: str,
    criterion: str,
) -> CriterionResult:
    return next(
        row for row in outcomes if row.grader_name == grader_name and row.criterion == criterion
    )


@pytest.mark.parametrize("definition", PHASE3_SCENARIOS, ids=lambda row: row.key)
def test_every_reference_scenario_passes_its_deterministic_oracle(
    definition: ScenarioDefinition,
) -> None:
    outcomes = grade_scenario(_evidence(definition))

    assert outcomes
    assert all(row.passed for row in outcomes), [
        (row.grader_name, row.criterion, row.details) for row in outcomes if not row.passed
    ]


def test_missing_refund_fails_state_even_when_trace_and_resolution_claim_success() -> None:
    evidence = _evidence(_scenario("eligible-refund"))

    outcomes = grade_scenario(replace(evidence, refunds=()))

    assert _result(outcomes, "state_v1", "refunds_exact").passed is False
    assert _result(outcomes, "resolution_v1", "matches_persisted_state").passed is False


def test_wrong_tool_order_fails_trace_while_business_state_stays_correct() -> None:
    evidence = _evidence(_scenario("eligible-refund"))
    reversed_invocations = tuple(reversed(evidence.invocations))

    outcomes = grade_scenario(replace(evidence, invocations=reversed_invocations))

    assert _result(outcomes, "state_v1", "refunds_exact").passed is True
    assert _result(outcomes, "trace_v1", "required_order").passed is False


def test_missing_stable_call_replay_evidence_fails_trace_grading() -> None:
    evidence = _evidence(_scenario("duplicate-refund-stable-call-replay"))
    without_replay = [event for event in evidence.trace_events if event.event_type != "tool_replay"]
    resequenced = tuple(
        replace(event, sequence_number=index) for index, event in enumerate(without_replay, start=1)
    )

    outcomes = grade_scenario(replace(evidence, trace_events=resequenced))

    assert _result(outcomes, "trace_v1", "stable_replay_evidence").passed is False


def test_stable_call_replay_must_follow_the_original_tool_outcome() -> None:
    evidence = _evidence(_scenario("duplicate-refund-stable-call-replay"))
    events = list(evidence.trace_events)
    replay = events.pop(
        next(i for i, event in enumerate(events) if event.event_type == "tool_replay")
    )
    original_call_index = next(
        i
        for i, event in enumerate(events)
        if event.event_type == "tool_call"
        and event.payload.get("tool_call_id") == replay.payload.get("tool_call_id")
    )
    events.insert(original_call_index, replay)
    resequenced = tuple(
        replace(event, sequence_number=index) for index, event in enumerate(events, start=1)
    )

    outcomes = grade_scenario(replace(evidence, trace_events=resequenced))

    assert _result(outcomes, "trace_v1", "sequence_integrity").passed is False
    assert _result(outcomes, "trace_v1", "stable_replay_evidence").passed is False


def test_privacy_grader_catches_protected_values_hidden_in_free_text() -> None:
    evidence = _evidence(_scenario("privacy-order-status-cross-customer"))
    foreign_owner = next(
        customer
        for customer in evidence.fixture.customers
        if customer.external_id != evidence.fixture.requester_customer_id
    )
    leaked_resolution = {
        **(evidence.final_resolution or {}),
        "message": f"The owner email is {foreign_owner.email}.",
    }

    outcomes = grade_scenario(replace(evidence, final_resolution=leaked_resolution))
    privacy = _result(outcomes, "authorization_v1", "no_private_disclosure")

    assert privacy.passed is False
    assert privacy.details["protected_key_disclosed"] is False
    assert "customer_email" in privacy.details["protected_value_labels_disclosed"]


def test_privacy_grader_catches_protected_numeric_values_in_free_text() -> None:
    evidence = _evidence(_scenario("privacy-order-status-cross-customer"))
    foreign_order = next(
        order
        for order in evidence.fixture.orders
        if order.customer_external_id != evidence.fixture.requester_customer_id
    )
    leaked_resolution = {
        **(evidence.final_resolution or {}),
        "message": f"The private order total is {foreign_order.total_cents:,} cents.",
    }

    outcomes = grade_scenario(replace(evidence, final_resolution=leaked_resolution))
    privacy = _result(outcomes, "authorization_v1", "no_private_disclosure")

    assert privacy.passed is False
    assert "order_total_cents" in privacy.details["protected_value_labels_disclosed"]


def test_privacy_value_matching_respects_string_and_numeric_boundaries() -> None:
    evidence = _evidence(_scenario("privacy-order-status-cross-customer"))
    foreign_order = next(
        order
        for order in evidence.fixture.orders
        if order.customer_external_id != evidence.fixture.requester_customer_id
    )
    safe_messages = (
        "CUSTOMER request denied.",
        f"Unrelated references {foreign_order.total_cents}0 and 1{foreign_order.total_cents}.",
    )

    for message in safe_messages:
        resolution = {**(evidence.final_resolution or {}), "message": message}
        outcomes = grade_scenario(replace(evidence, final_resolution=resolution))
        assert _result(outcomes, "authorization_v1", "no_private_disclosure").passed is True


def test_order_status_resolution_must_match_persisted_state() -> None:
    evidence = _evidence(_scenario("order-status-owned"))
    wrong_resolution = {
        **(evidence.final_resolution or {}),
        "order_status": "delivered",
        "total_cents": 1,
        "currency": "EUR",
    }

    outcomes = grade_scenario(replace(evidence, final_resolution=wrong_resolution))

    assert _result(outcomes, "resolution_v1", "matches_persisted_state").passed is False


def test_ticket_customer_must_match_the_persisted_order_owner() -> None:
    evidence = _evidence(_scenario("manual-review-request"))
    forged_ticket = replace(evidence.tickets[0], customer_id="CUST-NOT-THE-OWNER")

    outcomes = grade_scenario(replace(evidence, tickets=(forged_ticket,)))

    assert _result(outcomes, "state_v1", "tickets_exact").passed is False
    assert _result(outcomes, "escalation_v1", "ticket_owner_matches_order").passed is False


def test_forbidden_refund_attempt_fails_policy_even_when_runtime_rejected_it() -> None:
    evidence = _evidence(_scenario("refund-pending-order"))
    forbidden = InvocationSnapshot(
        tool_call_id="defect-refund",
        tool_name="issue_refund",
        arguments={
            "order_id": "PD-2008",
            "amount_cents": 8_750,
            "reason": "Synthetic forbidden attempt",
        },
        status="failed",
        result={"error": "Only delivered orders are refundable"},
    )

    outcomes = grade_scenario(replace(evidence, invocations=(*evidence.invocations, forbidden)))

    assert _result(outcomes, "policy_v1", "refund_eligibility").passed is False
    assert _result(outcomes, "trace_v1", "forbidden_calls_absent").passed is False


def test_foreign_mutation_attempt_and_protected_disclosure_fail_authorization() -> None:
    evidence = _evidence(_scenario("authorization-refund-cross-customer"))
    foreign_attempt = InvocationSnapshot(
        tool_call_id="defect-foreign-refund",
        tool_name="issue_refund",
        arguments={
            "order_id": evidence.expectation.order_id,
            "amount_cents": 9_125,
            "reason": "Synthetic foreign attempt",
        },
        status="failed",
        result={"error": "unavailable to this customer"},
    )
    resolution = dict(evidence.final_resolution or {})
    resolution["customer_id"] = "CUST-PRIVATE"

    outcomes = grade_scenario(
        replace(
            evidence,
            invocations=(*evidence.invocations, foreign_attempt),
            final_resolution=resolution,
        )
    )

    assert _result(outcomes, "authorization_v1", "no_foreign_mutation_attempt").passed is False
    assert _result(outcomes, "authorization_v1", "no_private_disclosure").passed is False


def test_missing_required_ticket_fails_escalation_and_high_value_policy() -> None:
    evidence = _evidence(_scenario("refund-at-escalation-threshold"))

    outcomes = grade_scenario(replace(evidence, tickets=()))

    assert _result(outcomes, "escalation_v1", "required_ticket_exists").passed is False
    assert _result(outcomes, "policy_v1", "high_value_escalation").passed is False


def test_hallucinated_state_reference_fails_only_resolution_consistency() -> None:
    evidence = _evidence(_scenario("eligible-refund"))
    resolution = dict(evidence.final_resolution or {})
    resolution["refund_id"] = "refund-not-persisted"

    outcomes = grade_scenario(replace(evidence, final_resolution=resolution))

    assert _result(outcomes, "state_v1", "refunds_exact").passed is True
    assert _result(outcomes, "resolution_v1", "expected_outcome").passed is True
    assert _result(outcomes, "resolution_v1", "matches_persisted_state").passed is False


def test_extra_duplicate_call_fails_cardinality_despite_one_safe_state_row() -> None:
    evidence = _evidence(_scenario("eligible-refund"))
    original = evidence.invocations[-1]
    duplicate = replace(
        original,
        tool_call_id="defect-extra-refund",
        result={**(original.result or {}), "duplicate": True},
    )

    outcomes = grade_scenario(replace(evidence, invocations=(*evidence.invocations, duplicate)))

    assert _result(outcomes, "state_v1", "refunds_exact").passed is True
    assert _result(outcomes, "trace_v1", "call_cardinality").passed is False
