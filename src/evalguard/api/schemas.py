"""Public request and response contracts for the EvalGuard API."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from evalguard.evaluation.suite import PHASE3_SUITE_NAME, PHASE3_SUITE_VERSION


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExperimentCreate(ApiModel):
    suite_name: str = PHASE3_SUITE_NAME
    suite_version: str = PHASE3_SUITE_VERSION
    provider: Literal["deterministic-fake"] = "deterministic-fake"
    trials: Literal[1] = 1


class SuiteSummary(ApiModel):
    id: UUID
    name: str
    version: str
    description: str
    definition_hash: str
    case_count: int


class ExperimentAccepted(ApiModel):
    id: UUID
    status: str
    execution_ids: list[UUID]


class ExecutionBrief(ApiModel):
    id: UUID
    case_id: UUID
    case_key: str
    trial_number: int
    status: str


class ExperimentDetail(ApiModel):
    id: UUID
    suite_id: UUID
    suite_name: str
    suite_version: str
    provider: str
    status: str
    idempotency_key: str | None
    created_at: datetime
    completed_at: datetime | None
    executions: list[ExecutionBrief]


class TraceEventRead(ApiModel):
    sequence_number: int
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


class ToolInvocationRead(ApiModel):
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None
    status: str


class GraderResultRead(ApiModel):
    grader_name: str
    criterion: str
    passed: bool
    message: str
    details: dict[str, Any]


class CustomerRead(ApiModel):
    external_id: str
    name: str
    email: str


class ShippingAddressRead(ApiModel):
    line1: str
    line2: str | None
    city: str
    region: str
    postal_code: str
    country: str


class OrderRead(ApiModel):
    external_id: str
    customer_id: UUID
    total_cents: int
    currency: str
    status: str
    shipping_address: ShippingAddressRead | None


class RefundRead(ApiModel):
    id: UUID
    order_id: UUID
    amount_cents: int
    currency: str
    status: str
    reason: str
    tool_call_id: str


class SupportTicketRead(ApiModel):
    id: UUID
    case_execution_id: UUID
    customer_id: UUID
    order_id: UUID
    reason_code: str
    reason: str
    priority: str
    status: str
    tool_call_id: str
    created_at: datetime
    escalated_at: datetime | None


class ExecutionDetail(ApiModel):
    id: UUID
    experiment_id: UUID
    case_id: UUID
    case_key: str
    trial_number: int
    status: str
    final_resolution: dict[str, Any] | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    customers: list[CustomerRead] = Field(default_factory=list)
    orders: list[OrderRead] = Field(default_factory=list)
    refunds: list[RefundRead] = Field(default_factory=list)
    support_tickets: list[SupportTicketRead] = Field(default_factory=list)
    trace_events: list[TraceEventRead] = Field(default_factory=list)
    tool_invocations: list[ToolInvocationRead] = Field(default_factory=list)
    grader_results: list[GraderResultRead] = Field(default_factory=list)
