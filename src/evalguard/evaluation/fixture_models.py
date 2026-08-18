"""Validation for source-controlled scenario fixtures and expectations."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FixtureModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
