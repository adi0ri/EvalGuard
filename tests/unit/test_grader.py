"""Unit coverage for the deterministic eligible-refund grader."""

from typing import Any

from evalguard.evaluation.grader import (
    CriterionResult,
    RefundEvidence,
    RefundSnapshot,
    grade_eligible_refund,
)


def _resolution(*, amount_cents: int = 4599, refund_id: str = "refund-1") -> dict[str, Any]:
    return {
        "status": "resolved",
        "action": "refund_issued",
        "order_id": "PD-1001",
        "refund_id": refund_id,
        "amount_cents": amount_cents,
        "currency": "USD",
    }


def _evidence(
    *,
    refunds: tuple[RefundSnapshot, ...],
    final_resolution: dict[str, Any] | None = None,
) -> RefundEvidence:
    return RefundEvidence(
        expected_order_id="PD-1001",
        expected_amount_cents=4599,
        expected_tool_names=("lookup_order", "issue_refund"),
        expected_resolution_status="resolved",
        refunds=refunds,
        ordered_tool_names=("lookup_order", "issue_refund"),
        final_resolution=_resolution() if final_resolution is None else final_resolution,
    )


def _by_criterion(results: tuple[CriterionResult, ...]) -> dict[str, CriterionResult]:
    return {result.criterion: result for result in results}


def test_eligible_refund_passes_every_deterministic_criterion() -> None:
    evidence = _evidence(
        refunds=(
            RefundSnapshot(
                refund_id="refund-1",
                order_id="PD-1001",
                amount_cents=4599,
                currency="USD",
                status="completed",
            ),
        )
    )

    results = grade_eligible_refund(evidence)

    assert [result.criterion for result in results] == [
        "refund_created",
        "refund_amount_matches",
        "required_tool_order",
        "structured_final_resolution",
    ]
    assert all(result.passed for result in results)
    assert _by_criterion(results)["refund_created"].details == {
        "total_refund_count": 1,
        "matching_refund_count": 1,
    }
    assert _by_criterion(results)["refund_amount_matches"].details == {
        "expected_amount_cents": 4599,
        "actual_amount_cents": 4599,
    }


def test_absent_refund_is_rejected_even_when_trace_and_resolution_claim_success() -> None:
    results = _by_criterion(grade_eligible_refund(_evidence(refunds=())))

    assert results["refund_created"].passed is False
    assert results["refund_created"].details == {
        "total_refund_count": 0,
        "matching_refund_count": 0,
    }
    assert results["refund_amount_matches"].passed is False
    assert results["refund_amount_matches"].details == {
        "expected_amount_cents": 4599,
        "actual_amount_cents": None,
    }
    assert results["required_tool_order"].passed is True
    assert results["structured_final_resolution"].passed is False


def test_wrong_refund_amount_is_rejected_from_persisted_state() -> None:
    evidence = _evidence(
        refunds=(
            RefundSnapshot(
                refund_id="refund-1",
                order_id="PD-1001",
                amount_cents=1200,
                currency="USD",
                status="completed",
            ),
        )
    )

    results = _by_criterion(grade_eligible_refund(evidence))

    assert results["refund_created"].passed is True
    assert results["refund_amount_matches"].passed is False
    assert results["refund_amount_matches"].details == {
        "expected_amount_cents": 4599,
        "actual_amount_cents": 1200,
    }
    assert results["required_tool_order"].passed is True
    assert results["structured_final_resolution"].passed is False


def test_unexpected_additional_refund_is_rejected() -> None:
    refunds = (
        RefundSnapshot("refund-1", "PD-1001", 4599, "USD", "completed"),
        RefundSnapshot("refund-2", "PD-OTHER", 500, "USD", "completed"),
    )

    results = _by_criterion(grade_eligible_refund(_evidence(refunds=refunds)))

    assert results["refund_created"].passed is False
    assert results["structured_final_resolution"].passed is False


def test_hallucinated_refund_id_is_rejected() -> None:
    refunds = (RefundSnapshot("refund-1", "PD-1001", 4599, "USD", "completed"),)

    results = _by_criterion(
        grade_eligible_refund(
            _evidence(refunds=refunds, final_resolution=_resolution(refund_id="not-persisted"))
        )
    )

    assert results["refund_created"].passed is True
    assert results["structured_final_resolution"].passed is False
