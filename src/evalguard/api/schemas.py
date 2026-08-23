"""Public request and response contracts for the EvalGuard API."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from evalguard.evaluation.resilience import FaultMode
from evalguard.evaluation.suite import PHASE3_SUITE_NAME, PHASE3_SUITE_VERSION


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FaultInjectionCreate(ApiModel):
    """One deterministic execution fault, scoped to a selected case."""

    case_key: str = Field(min_length=1, max_length=100)
    mode: FaultMode
    fail_attempts: int = Field(default=1, ge=1, le=5)


class ExperimentCreate(ApiModel):
    suite_name: str = PHASE3_SUITE_NAME
    suite_version: str = PHASE3_SUITE_VERSION
    provider: Literal["deterministic-fake"] = "deterministic-fake"
    trials: Literal[1] = 1
    max_attempts: int = Field(default=3, ge=1, le=5)
    job_timeout_seconds: int = Field(default=30, ge=1, le=300)
    max_tool_steps: int = Field(default=8, ge=1, le=100)
    retry_backoff_seconds: int = Field(default=1, ge=0, le=300)
    fault_injection: FaultInjectionCreate | None = None


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
    attempt_count: int


class ExperimentDetail(ApiModel):
    id: UUID
    suite_id: UUID
    suite_name: str
    suite_version: str
    provider: str
    status: str
    idempotency_key: str | None
    max_attempts: int
    job_timeout_seconds: int
    max_tool_steps: int
    retry_backoff_seconds: int
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


class ExecutionAttemptRead(ApiModel):
    attempt_number: int
    dispatch_generation: int
    job_id: str
    status: str
    retryable: bool | None
    failure_kind: str | None
    error_message: str | None
    step_count: int
    started_at: datetime
    lease_expires_at: datetime
    completed_at: datetime | None


class ExecutionDispatchRead(ApiModel):
    generation: int
    job_id: str
    status: str
    attempt_count: int
    available_at: datetime
    last_attempt_at: datetime | None
    dispatched_at: datetime | None
    last_error_kind: str | None


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
    attempt_count: int
    max_attempts: int
    job_timeout_seconds: int
    max_tool_steps: int
    retry_backoff_seconds: int
    dispatch_generation: int
    lease_expires_at: datetime | None
    last_failure_kind: str | None
    last_failure_retryable: bool | None
    final_resolution: dict[str, Any] | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    customers: list[CustomerRead] = Field(default_factory=list)
    orders: list[OrderRead] = Field(default_factory=list)
    refunds: list[RefundRead] = Field(default_factory=list)
    support_tickets: list[SupportTicketRead] = Field(default_factory=list)
    attempts: list[ExecutionAttemptRead] = Field(default_factory=list)
    dispatches: list[ExecutionDispatchRead] = Field(default_factory=list)
    trace_events: list[TraceEventRead] = Field(default_factory=list)
    tool_invocations: list[ToolInvocationRead] = Field(default_factory=list)
    grader_results: list[GraderResultRead] = Field(default_factory=list)
