"""Unit coverage for validated ParcelDesk tool argument contracts."""

from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from evalguard.domain.tool_schemas import (
    EscalateTicketArgs,
    IssueRefundArgs,
    LookupOrderArgs,
    ShippingAddress,
    UpdateShippingAddressArgs,
)

ToolArgumentFactory = Callable[
    ...,
    LookupOrderArgs | IssueRefundArgs | UpdateShippingAddressArgs | EscalateTicketArgs,
]


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
        (
            UpdateShippingAddressArgs,
            {"order_id": "PD-1001"},
            ("address",),
            "missing",
        ),
        (
            UpdateShippingAddressArgs,
            {
                "order_id": "PD-1001",
                "address": {
                    "line1": "14 Cedar Lane",
                    "city": "Port Aurora",
                    "region": "CA",
                    "postal_code": "94107",
                    "country": "us",
                },
            },
            ("address", "country"),
            "string_pattern_mismatch",
        ),
        (
            EscalateTicketArgs,
            {
                "order_id": "PD-1001",
                "reason_code": "refund_everything",
                "reason": "Not an allowlisted reason",
                "priority": "high",
            },
            ("reason_code",),
            "literal_error",
        ),
        (
            EscalateTicketArgs,
            {
                "order_id": "PD-1001",
                "reason_code": "high_value_refund",
                "reason": "Requires review",
                "priority": "immediate",
            },
            ("priority",),
            "literal_error",
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
        "address-missing-address",
        "address-invalid-country",
        "escalation-invalid-reason-code",
        "escalation-invalid-priority",
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
    address = ShippingAddress(
        line1="14 Cedar Lane",
        line2="Suite 2",
        city="Port Aurora",
        region="CA",
        postal_code="94107",
        country="US",
    )
    address_update = UpdateShippingAddressArgs(order_id=lookup.order_id, address=address)
    escalation = EscalateTicketArgs(
        order_id=lookup.order_id,
        reason_code="high_value_refund",
        reason="The refund exceeds the synthetic self-service threshold.",
        priority="high",
    )

    assert lookup.model_dump() == {"order_id": "PD-1001"}
    assert refund.model_dump() == {
        "order_id": "PD-1001",
        "amount_cents": 4599,
        "reason": "Item arrived damaged",
    }
    assert address_update.model_dump() == {
        "order_id": "PD-1001",
        "address": {
            "line1": "14 Cedar Lane",
            "line2": "Suite 2",
            "city": "Port Aurora",
            "region": "CA",
            "postal_code": "94107",
            "country": "US",
        },
    }
    assert escalation.model_dump() == {
        "order_id": "PD-1001",
        "reason_code": "high_value_refund",
        "reason": "The refund exceeds the synthetic self-service threshold.",
        "priority": "high",
    }
