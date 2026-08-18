"""Typed contracts for the ParcelDesk tools implemented in Phase 1."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ToolArguments(BaseModel):
    """Reject unknown arguments so provider mistakes fail loudly."""

    model_config = ConfigDict(extra="forbid")


class LookupOrderArgs(ToolArguments):
    order_id: str = Field(min_length=1, max_length=64)


class LookupOrderResult(BaseModel):
    order_id: str
    customer_id: str
    status: str
    total_cents: int
    currency: str


class IssueRefundArgs(ToolArguments):
    order_id: str = Field(min_length=1, max_length=64)
    amount_cents: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=500)


class IssueRefundResult(BaseModel):
    refund_id: str
    order_id: str
    amount_cents: int
    currency: str
    status: Literal["completed"]
    duplicate: bool = False
