"""Unit coverage for strict ParcelDesk tool argument contracts."""

from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from evalguard.domain.tool_schemas import IssueRefundArgs, LookupOrderArgs

ToolArgumentFactory = Callable[..., LookupOrderArgs | IssueRefundArgs]


@pytest.mark.parametrize(
    ("factory", "payload", "location", "error_type"),
    [
        (LookupOrderArgs, {}, ("order_id",), "missing"),
        (LookupOrderArgs, {"order_id": ""}, ("order_id",), "string_too_short"),
        (
            LookupOrderArgs,
            {"order_id": "PD-1001", "customer_id": "CUST-1001"},
            ("customer_id",),
            "extra_forbidden",
        ),
        (
            IssueRefundArgs,
            {"order_id": "PD-1001", "amount_cents": 4599},
            ("reason",),
            "missing",
        ),
        (
            IssueRefundArgs,
            {"order_id": "PD-1001", "amount_cents": 0, "reason": "damaged"},
            ("amount_cents",),
            "greater_than",
        ),
        (
            IssueRefundArgs,
            {"order_id": "PD-1001", "amount_cents": "many", "reason": "damaged"},
            ("amount_cents",),
            "int_parsing",
        ),
        (
            IssueRefundArgs,
            {
                "order_id": "PD-1001",
                "amount_cents": 4599,
                "reason": "damaged",
                "currency": "USD",
            },
            ("currency",),
            "extra_forbidden",
        ),
    ],
    ids=[
        "lookup-missing-order-id",
        "lookup-blank-order-id",
        "lookup-extra-argument",
        "refund-missing-reason",
        "refund-nonpositive-amount",
        "refund-invalid-amount-type",
        "refund-extra-argument",
    ],
)
def test_invalid_tool_arguments_are_rejected(
    factory: ToolArgumentFactory,
    payload: dict[str, Any],
    location: tuple[str, ...],
    error_type: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        factory(**payload)

    errors = exc_info.value.errors()
    assert any(error["loc"] == location and error["type"] == error_type for error in errors)


def test_valid_tool_arguments_preserve_typed_values() -> None:
    lookup = LookupOrderArgs(order_id="PD-1001")
    refund = IssueRefundArgs(
        order_id=lookup.order_id,
        amount_cents=4599,
        reason="Item arrived damaged",
    )

    assert lookup.model_dump() == {"order_id": "PD-1001"}
    assert refund.model_dump() == {
        "order_id": "PD-1001",
        "amount_cents": 4599,
        "reason": "Item arrived damaged",
    }
