"""Typed contracts for the ParcelDesk tools implemented through Phase 6."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ToolArguments(BaseModel):
    """Reject unknown arguments so provider mistakes fail loudly."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ToolResult(BaseModel):
    """Keep persisted and replayed tool results strict and closed as well."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class LookupOrderArgs(ToolArguments):
    order_id: str = Field(min_length=1, max_length=64)


class LookupOrderResult(ToolResult):
    order_id: str
    customer_id: str
    status: str
    total_cents: int
    currency: str
    delivered_at: datetime | None = None


class IssueRefundArgs(ToolArguments):
    order_id: str = Field(min_length=1, max_length=64)
    amount_cents: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=500)


class IssueRefundResult(ToolResult):
    refund_id: str
    order_id: str
    amount_cents: int
    currency: str
    status: Literal["completed"]
    duplicate: bool = False


class ShippingAddress(ToolArguments):
    """A complete, normalized postal address accepted by ParcelDesk."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
    )

    line1: str = Field(min_length=1, max_length=200)
    line2: str | None = Field(default=None, min_length=1, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    region: str = Field(min_length=1, max_length=100)
    postal_code: str = Field(min_length=1, max_length=32)
    country: str = Field(min_length=2, max_length=2, pattern=r"^[A-Z]{2}$")


class UpdateShippingAddressArgs(ToolArguments):
    order_id: str = Field(min_length=1, max_length=64)
    address: ShippingAddress


class UpdateShippingAddressResult(ToolResult):
    order_id: str
    address: ShippingAddress
    status: Literal["updated"]
    duplicate: bool = False


EscalationReasonCode = Literal[
    "high_value_refund",
    "address_change_blocked",
    "missing_information",
    "manual_review",
]
TicketPriority = Literal["normal", "high", "urgent"]


class EscalateTicketArgs(ToolArguments):
    order_id: str = Field(min_length=1, max_length=64)
    reason_code: EscalationReasonCode
    reason: str = Field(min_length=1, max_length=500)
    priority: TicketPriority


class EscalateTicketResult(ToolResult):
    ticket_id: str
    order_id: str
    reason_code: EscalationReasonCode
    reason: str
    priority: TicketPriority
    status: Literal["escalated"]
    duplicate: bool = False
