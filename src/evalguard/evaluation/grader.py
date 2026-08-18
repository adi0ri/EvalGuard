"""Pure deterministic grading for the eligible-refund scenario."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RefundSnapshot:
    refund_id: str
    order_id: str
    amount_cents: int
    currency: str
    status: str


@dataclass(frozen=True)
class RefundEvidence:
    expected_order_id: str
    expected_amount_cents: int
    expected_tool_names: tuple[str, ...]
    expected_resolution_status: str
    refunds: tuple[RefundSnapshot, ...]
    ordered_tool_names: tuple[str, ...]
    final_resolution: dict[str, Any] | None


@dataclass(frozen=True)
class CriterionResult:
    criterion: str
    passed: bool
    message: str
    details: dict[str, Any]


def grade_eligible_refund(evidence: RefundEvidence) -> tuple[CriterionResult, ...]:
    """Grade database state, tool order, and the structured final resolution."""

    matching_refunds = [
        refund
        for refund in evidence.refunds
        if refund.order_id == evidence.expected_order_id and refund.status == "completed"
    ]
    exactly_one = len(evidence.refunds) == 1 and len(matching_refunds) == 1
    refund_created = CriterionResult(
        criterion="refund_created",
        passed=exactly_one,
        message=(
            "Exactly one completed refund exists for the expected order."
            if exactly_one
            else "Expected exactly one completed refund for the expected order."
        ),
        details={
            "total_refund_count": len(evidence.refunds),
            "matching_refund_count": len(matching_refunds),
        },
    )

    amount_matches = (
        exactly_one and matching_refunds[0].amount_cents == evidence.expected_amount_cents
    )
    refund_amount = CriterionResult(
        criterion="refund_amount_matches",
        passed=amount_matches,
        message=(
            "The refund amount matches the scenario expectation."
            if amount_matches
            else "The completed refund is absent or has the wrong amount."
        ),
        details={
            "expected_amount_cents": evidence.expected_amount_cents,
            "actual_amount_cents": (matching_refunds[0].amount_cents if exactly_one else None),
        },
    )

    expected_tools = evidence.expected_tool_names
    correct_tool_order = evidence.ordered_tool_names == expected_tools
    tool_order = CriterionResult(
        criterion="required_tool_order",
        passed=correct_tool_order,
        message=(
            "The provider looked up the order before issuing the refund."
            if correct_tool_order
            else "The required lookup_order then issue_refund sequence was not observed."
        ),
        details={
            "expected": list(expected_tools),
            "actual": list(evidence.ordered_tool_names),
        },
    )

    resolution = evidence.final_resolution or {}
    matching_refund = matching_refunds[0] if exactly_one else None
    structured_resolution_matches = (
        matching_refund is not None
        and resolution.get("status") == evidence.expected_resolution_status
        and resolution.get("action") == "refund_issued"
        and resolution.get("order_id") == evidence.expected_order_id
        and resolution.get("refund_id") == matching_refund.refund_id
        and resolution.get("amount_cents") == matching_refund.amount_cents
        and resolution.get("amount_cents") == evidence.expected_amount_cents
        and resolution.get("currency") == matching_refund.currency
    )
    final_resolution = CriterionResult(
        criterion="structured_final_resolution",
        passed=structured_resolution_matches,
        message=(
            "The structured final resolution describes the expected refund."
            if structured_resolution_matches
            else "The structured final resolution does not match the expected refund."
        ),
        details={
            "resolution": resolution,
            "persisted_refund_id": matching_refund.refund_id if matching_refund else None,
        },
    )

    return refund_created, refund_amount, tool_order, final_resolution
