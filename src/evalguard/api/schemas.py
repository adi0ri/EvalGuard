"""Public request and response contracts for the EvalGuard API."""

from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evalguard.evaluation.prompts import DEFAULT_PROMPT_VERSION, PROMPT_NAME
from evalguard.evaluation.resilience import FaultMode
from evalguard.evaluation.suite import PHASE3_SUITE_NAME, PHASE3_SUITE_VERSION


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FaultInjectionCreate(ApiModel):
    """One deterministic execution fault, scoped to a selected case."""

    case_key: str = Field(min_length=1, max_length=100)
    trial_number: int = Field(default=1, ge=1, le=3)
    mode: FaultMode
    fail_attempts: int = Field(default=1, ge=1, le=5)


class ExperimentCreate(ApiModel):
    suite_name: str = PHASE3_SUITE_NAME
    suite_version: str = PHASE3_SUITE_VERSION
    prompt_name: str = PROMPT_NAME
    prompt_version: str = DEFAULT_PROMPT_VERSION
    provider: Literal["deterministic-fake", "openai-responses"] = "deterministic-fake"
    trials: int = Field(default=1, ge=1, le=3)
    max_attempts: int = Field(default=3, ge=1, le=5)
    job_timeout_seconds: int = Field(default=30, ge=1, le=300)
    max_tool_steps: int = Field(default=8, ge=1, le=100)
    retry_backoff_seconds: int = Field(default=1, ge=0, le=300)
    fault_injection: FaultInjectionCreate | None = None

    @model_validator(mode="after")
    def validate_fault_trial(self) -> Self:
        if self.fault_injection is not None and self.fault_injection.trial_number > self.trials:
            raise ValueError("fault_injection.trial_number must not exceed trials")
        if self.provider == "openai-responses":
            if self.trials != 1:
                raise ValueError("openai-responses experiments are limited to one trial")
            if self.max_attempts != 1:
                raise ValueError(
                    "openai-responses experiments require max_attempts=1 to prevent paid retries"
                )
            if self.job_timeout_seconds != 300:
                raise ValueError("openai-responses experiments require job_timeout_seconds=300")
            if self.fault_injection is not None:
                raise ValueError(
                    "deterministic fault injection is unavailable for openai-responses experiments"
                )
        return self


class SuiteSummary(ApiModel):
    id: UUID
    name: str
    version: str
    description: str
    definition_hash: str
    case_count: int


class AgentPromptSummary(ApiModel):
    id: UUID
    name: str
    version: str
    description: str
    instructions: str
    definition_hash: str


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


class PromptIdentityRead(ApiModel):
    name: str
    version: str
    definition_hash: str


class ExperimentDetail(ApiModel):
    id: UUID
    suite_id: UUID
    suite_name: str
    suite_version: str
    prompt: PromptIdentityRead | None
    provider: str
    provider_model: str | None
    provider_config_hash: str | None
    status: str
    idempotency_key: str | None
    trials: int
    max_attempts: int
    job_timeout_seconds: int
    max_tool_steps: int
    retry_backoff_seconds: int
    created_at: datetime
    completed_at: datetime | None
    executions: list[ExecutionBrief]


class RatioRead(ApiModel):
    numerator: int
    denominator: int
    value: float | None


class ExecutionCountsRead(ApiModel):
    expected: int
    terminal: int
    completed: int
    infrastructure_failed: int
    nonterminal: int
    graded: int
    passing: int
    criterion_failing: int
    ungraded_terminal: int


class DurationMetricRead(ApiModel):
    observed_count: int
    minimum_ms: int | None
    mean_ms: int | None
    median_ms: int | None
    p95_ms: int | None
    maximum_ms: int | None


class ProviderUsageRead(ApiModel):
    request_count: int
    response_count: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    total_tokens: int
    estimated_cost_usd: str | None
    returned_models: list[str]
    latency: DurationMetricRead


class TimingAnalysisRead(ApiModel):
    label: str
    queue_delay: DurationMetricRead
    run_duration: DurationMetricRead
    end_to_end_duration: DurationMetricRead


class TrialAnalysisRead(ApiModel):
    execution_id: UUID
    trial_number: int
    status: str
    attempt_count: int
    quality_passed: bool | None
    grader_criteria: int
    passed_criteria: int
    queue_delay_ms: int | None
    run_duration_ms: int | None
    end_to_end_duration_ms: int | None


class CaseAnalysisRead(ApiModel):
    case_id: UUID
    case_key: str
    category: str
    expected_trials: int
    terminal_trials: int
    graded_trials: int
    passing_trials: int
    strict_pass_rate: RatioRead
    stability: Literal["stable_pass", "stable_fail", "unstable", "incomplete"]
    trials: list[TrialAnalysisRead]


class GraderFamilyAnalysisRead(ApiModel):
    grader_name: str
    passed_criteria: int
    total_criteria: int
    pass_rate: RatioRead


class CriterionAnalysisRead(ApiModel):
    grader_name: str
    criterion: str
    passed: int
    total: int
    pass_rate: RatioRead


class CategoryAnalysisRead(ApiModel):
    category: str
    expected_executions: int
    terminal_executions: int
    graded_executions: int
    passing_executions: int
    strict_pass_rate: RatioRead
    grading_coverage: RatioRead


class StabilityCountsRead(ApiModel):
    stable_pass: int
    stable_fail: int
    unstable: int
    incomplete: int


class GridIntegrityRead(ApiModel):
    expected_coordinates: int
    observed_executions: int
    missing_coordinates: int
    unexpected_coordinates: int
    duplicate_coordinates: int
    valid: bool


class ExperimentSummaryRead(ApiModel):
    experiment_id: UUID
    status: str
    suite_id: UUID
    suite_name: str
    suite_version: str
    suite_definition_hash: str
    prompt: PromptIdentityRead | None
    prompt_is_published: bool | None
    provider: str
    provider_model: str | None
    provider_config_hash: str | None
    trials: int
    max_attempts: int
    job_timeout_seconds: int
    max_tool_steps: int
    retry_backoff_seconds: int
    created_at: datetime
    completed_at: datetime | None
    result_label: str
    is_terminal: bool
    is_fully_graded: bool
    quality_comparison_ready: bool
    token_usage_available: bool
    cost_available: bool
    provider_usage: ProviderUsageRead | None
    fault_injection_present: bool
    grid_integrity: GridIntegrityRead
    execution_counts: ExecutionCountsRead
    status_counts: dict[str, int]
    strict_pass_rate: RatioRead
    graded_pass_rate: RatioRead
    grading_coverage: RatioRead
    criterion_pass_rate: RatioRead
    total_attempts: int
    retry_attempts: int
    stability_counts: StabilityCountsRead
    stable_case_rate: RatioRead
    timing: TimingAnalysisRead
    grader_families: list[GraderFamilyAnalysisRead]
    criteria: list[CriterionAnalysisRead]
    categories: list[CategoryAnalysisRead]
    cases: list[CaseAnalysisRead]


class ValueDeltaRead(ApiModel):
    baseline: float | int | None
    candidate: float | int | None
    delta: float | int | None


class CaseComparisonRead(ApiModel):
    case_key: str
    category: str
    baseline_pass_rate: RatioRead
    candidate_pass_rate: RatioRead
    pass_rate_delta: float | None
    classification: Literal["improved", "regressed", "unchanged", "provisional"]


class GraderFamilyComparisonRead(ApiModel):
    grader_name: str
    baseline_pass_rate: RatioRead
    candidate_pass_rate: RatioRead
    pass_rate_delta: float | None


class ExperimentComparisonRead(ApiModel):
    baseline: ExperimentSummaryRead
    candidate: ExperimentSummaryRead
    quality_comparison_ready: bool
    strict_pass_rate: ValueDeltaRead
    criterion_pass_rate: ValueDeltaRead
    stable_case_rate: ValueDeltaRead
    mean_run_duration_ms: ValueDeltaRead
    total_attempts: ValueDeltaRead
    grader_families: list[GraderFamilyComparisonRead]
    cases: list[CaseComparisonRead]


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
    recovery_count: int
    started_at: datetime
    lease_expires_at: datetime
    completed_at: datetime | None


class ExecutionDispatchRead(ApiModel):
    generation: int
    delivery_kind: str
    attempt_number: int | None
    job_id: str
    status: str
    attempt_count: int
    available_at: datetime
    last_attempt_at: datetime | None
    dispatched_at: datetime | None
    last_error_kind: str | None


class ProviderTurnRead(ApiModel):
    sequence_number: int
    attempt_number: int
    dispatch_generation: int
    status: str
    client_request_id: str
    request_hash: str
    request_payload: dict[str, Any]
    provider_request_id: str | None
    provider_response_id: str | None
    response_model: str | None
    response_payload: dict[str, Any] | None
    tool_calls: list[dict[str, Any]] | None
    tool_outputs: list[dict[str, Any]] | None
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    total_tokens: int | None
    estimated_cost_usd_micros: int | None
    latency_ms: int | None
    retryable: bool | None
    error_kind: str | None
    safe_error_message: str | None
    prepared_at: datetime
    request_started_at: datetime | None
    response_received_at: datetime | None
    tools_completed_at: datetime | None
    completed_at: datetime | None


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
    provider: str
    provider_model: str | None
    provider_config_hash: str | None
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
    provider_turns: list[ProviderTurnRead] = Field(default_factory=list)
    trace_events: list[TraceEventRead] = Field(default_factory=list)
    tool_invocations: list[ToolInvocationRead] = Field(default_factory=list)
    grader_results: list[GraderResultRead] = Field(default_factory=list)
