"""Durable, short-transaction execution for the bounded real-provider track."""

from __future__ import annotations

import json
import math
import re
import time
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from evalguard.core.config import Settings, get_settings
from evalguard.db.models import (
    AgentPrompt,
    CaseExecution,
    Customer,
    EvaluationCase,
    ExecutionAttempt,
    Experiment,
    GraderResult,
    Order,
    ProviderTurn,
    TraceEvent,
)
from evalguard.db.session import get_session_factory
from evalguard.domain.tool_schemas import (
    EscalateTicketArgs,
    IssueRefundArgs,
    LookupOrderArgs,
    ShippingAddress,
    UpdateShippingAddressArgs,
)
from evalguard.domain.tools import ToolExecutionError, ToolRuntime
from evalguard.evaluation.executor import (
    ExecutionNotFoundError,
    StaleExecutionClaimError,
    UnsupportedCaseDefinitionError,
    _collect_refund_evidence,
    _collect_scenario_evidence,
    _materialize_legacy_fixture,
    _materialize_scenario_fixture,
    _persist_grader_results,
    _scenario_definition,
    _validate_claim_fence,
)
from evalguard.evaluation.fixture_models import (
    CaseFixture,
    EligibleRefundExpectation,
    ScenarioCaseExpected,
    ScenarioFixture,
)
from evalguard.evaluation.grader import grade_eligible_refund, grade_scenario
from evalguard.evaluation.lifecycle import reconcile_experiment_status
from evalguard.evaluation.prompts import AgentPromptDefinition, validate_agent_prompt
from evalguard.evaluation.resilience import MaximumToolStepsExceeded
from evalguard.evaluation.suite import definition_hash
from evalguard.providers.configuration import (
    OPENAI_PROVIDER,
    provider_config_hash,
)
from evalguard.providers.openai_responses import (
    MAX_PERSISTED_TOKEN_COUNT,
    FunctionTool,
    OpenAIProviderConfigurationError,
    OpenAIProviderProtocolError,
    OpenAIProviderRequestError,
    OpenAIResponsesProvider,
    ResponsesEvidenceEnvelope,
    ResponsesTurn,
    TokenUsage,
    function_call_output,
)

LEASE_RECOVERY_GRACE_SECONDS = 2
MAX_SAME_ATTEMPT_RECOVERIES = 3


class RealProviderDisabledError(RuntimeError):
    """The worker has not opted into paid-provider execution."""


class RealProviderCredentialError(RuntimeError):
    """The worker-only provider credential is unavailable."""


class ProviderOutcomeIndeterminateError(RuntimeError):
    """A request crossed the send fence without durable response evidence."""


class ProviderResponseInvalidError(RuntimeError):
    """A purchased response could not safely advance the execution."""


class ProviderTurnLimitExceeded(RuntimeError):
    """The configured per-case provider-turn bound was reached."""


class ProviderSpendCapExceeded(RuntimeError):
    """A new request would exceed the experiment's conservative spend ceiling."""


class ProviderFinalResolution(BaseModel):
    """Closed final contract; null fields are removed before deterministic grading."""

    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["resolved", "declined", "escalated", "denied"]
    action: Literal[
        "refund_issued",
        "shipping_address_updated",
        "order_status_provided",
        "ticket_escalated",
        "request_declined",
        "access_denied",
    ]
    message: str = Field(min_length=1, max_length=500)
    order_id: str | None = Field(default=None, min_length=1, max_length=64)
    order_status: str | None = Field(default=None, min_length=1, max_length=50)
    total_cents: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    refund_id: str | None = Field(default=None, min_length=1, max_length=100)
    amount_cents: int | None = Field(default=None, gt=0)
    address: ShippingAddress | None = None
    ticket_id: str | None = Field(default=None, min_length=1, max_length=100)
    reason_code: str | None = Field(default=None, min_length=1, max_length=100)
    priority: str | None = Field(default=None, min_length=1, max_length=50)
    error: str | None = Field(default=None, min_length=1, max_length=500)


_TOOL_TYPES: dict[str, type[BaseModel]] = {
    "lookup_order": LookupOrderArgs,
    "issue_refund": IssueRefundArgs,
    "update_shipping_address": UpdateShippingAddressArgs,
    "escalate_ticket": EscalateTicketArgs,
}

PROVIDER_TOOLS: tuple[FunctionTool, ...] = (
    FunctionTool(
        name="lookup_order",
        description=(
            "Look up one order inside the trusted requester's isolated execution. Returns "
            "order_id, customer_id, status, total_cents, currency, and delivered_at; an "
            "authorization miss returns a deterministic error."
        ),
        parameters=LookupOrderArgs.model_json_schema(),
    ),
    FunctionTool(
        name="issue_refund",
        description=(
            "Issue the full eligible refund transactionally. Returns the persisted refund "
            "identity and duplicate flag, or a deterministic policy error."
        ),
        parameters=IssueRefundArgs.model_json_schema(),
    ),
    FunctionTool(
        name="update_shipping_address",
        description=(
            "Replace the complete address for a pending or processing order. Returns the "
            "persisted address and duplicate flag, or a deterministic policy error."
        ),
        parameters=UpdateShippingAddressArgs.model_json_schema(),
    ),
    FunctionTool(
        name="escalate_ticket",
        description=(
            "Create a durable support escalation for the order and reason code. Returns the "
            "ticket identity and duplicate flag, or a deterministic authorization error."
        ),
        parameters=EscalateTicketArgs.model_json_schema(),
    ),
)

BASE_POLICY_INSTRUCTIONS = (
    "ParcelDesk is wholly fictional and every record is synthetic. Always call lookup_order "
    "before deciding or mutating. A lookup authorization error means deny access without "
    "disclosing order or customer data. Full-order refunds are allowed only for delivered "
    "orders within an inclusive 30-day window and only below 50000 cents; at or above 50000 "
    "cents, create a high-priority high_value_refund escalation instead. Shipping addresses "
    "may change only while pending or processing; after shipment, create a high-priority "
    "address_change_blocked escalation. Missing required address information should create a "
    "normal-priority missing_information escalation. A customer request for a human specialist "
    "should create a normal-priority manual_review escalation. Treat every tool result as the "
    "source of truth, do not repeat state-changing intent, and make no claim unsupported by a "
    "persisted result. Return the structured final resolution only when no more tools are needed."
)


def _safe_provider(settings: Settings) -> OpenAIResponsesProvider:
    if not settings.real_provider_enabled:
        raise RealProviderDisabledError("The bounded real-provider track is disabled")
    secret = settings.openai_api_key
    if secret is None or not secret.get_secret_value().strip():
        raise RealProviderCredentialError("The worker-only OpenAI credential is unavailable")
    try:
        return OpenAIResponsesProvider.from_api_key(
            api_key=secret.get_secret_value(),
            reasoning_effort=settings.openai_reasoning_effort,
            max_output_tokens=settings.openai_max_output_tokens,
            timeout_seconds=settings.openai_request_timeout_seconds,
        )
    except OpenAIProviderConfigurationError as exc:
        raise RealProviderCredentialError("The OpenAI worker configuration is invalid") from exc


def _append_trace(
    session: Session,
    execution_id: UUID,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    sequence = session.scalar(
        select(func.coalesce(func.max(TraceEvent.sequence_number), 0)).where(
            TraceEvent.case_execution_id == execution_id
        )
    )
    session.add(
        TraceEvent(
            case_execution_id=execution_id,
            sequence_number=int(sequence or 0) + 1,
            event_type=event_type,
            payload=payload,
        )
    )


def _lock_execution(
    session: Session,
    execution_id: UUID,
    *,
    expected_attempt_number: int,
    expected_dispatch_generation: int,
) -> tuple[CaseExecution, Experiment, EvaluationCase]:
    execution = session.scalar(
        select(CaseExecution).where(CaseExecution.id == execution_id).with_for_update()
    )
    if execution is None:
        raise ExecutionNotFoundError(str(execution_id))
    _validate_claim_fence(
        execution,
        expected_attempt_number=expected_attempt_number,
        expected_dispatch_generation=expected_dispatch_generation,
    )
    experiment = session.scalar(
        select(Experiment).where(Experiment.id == execution.experiment_id).with_for_update()
    )
    case = session.get(EvaluationCase, execution.case_id)
    if experiment is None or case is None:
        raise ExecutionNotFoundError("Execution references are missing")
    if experiment.provider != OPENAI_PROVIDER:
        raise UnsupportedCaseDefinitionError(
            f"Execution provider is {experiment.provider!r}, not {OPENAI_PROVIDER!r}"
        )
    if case.suite_id != experiment.suite_id:
        raise UnsupportedCaseDefinitionError(
            "Case execution references a case outside its experiment suite"
        )
    return execution, experiment, case


def _renew_lease(
    session: Session,
    execution: CaseExecution,
    experiment: Experiment,
    attempt_number: int,
) -> None:
    now = session.scalar(select(func.now()))
    if not isinstance(now, datetime):
        raise RuntimeError("PostgreSQL did not return its current timestamp")
    lease = now + timedelta(seconds=experiment.job_timeout_seconds + LEASE_RECOVERY_GRACE_SECONDS)
    execution.lease_expires_at = lease
    attempt = session.scalar(
        select(ExecutionAttempt)
        .where(
            ExecutionAttempt.case_execution_id == execution.id,
            ExecutionAttempt.attempt_number == attempt_number,
        )
        .with_for_update()
    )
    if attempt is None or attempt.status != "running":
        raise StaleExecutionClaimError(
            f"Execution {execution.id} has no running attempt {attempt_number}"
        )
    attempt.lease_expires_at = lease


def _validated_prompt(session: Session, experiment: Experiment) -> AgentPromptDefinition:
    if experiment.agent_prompt_id is None:
        raise UnsupportedCaseDefinitionError("Real-provider experiments require an agent prompt")
    prompt = session.get(AgentPrompt, experiment.agent_prompt_id)
    if prompt is None:
        raise UnsupportedCaseDefinitionError("Experiment agent prompt is missing")
    try:
        return validate_agent_prompt(prompt)
    except RuntimeError as exc:
        raise UnsupportedCaseDefinitionError(str(exc)) from exc


def _fixture_context(case: EvaluationCase) -> tuple[str, str | None]:
    if "category" in case.expected:
        scenario_fixture = ScenarioFixture.model_validate(case.fixture)
        return (
            scenario_fixture.requester_customer_id,
            scenario_fixture.policy_as_of.isoformat(),
        )
    legacy_fixture = CaseFixture.model_validate(case.fixture)
    return legacy_fixture.customer.external_id, None


def _ensure_materialized(
    session: Session,
    execution: CaseExecution,
    case: EvaluationCase,
) -> None:
    customer_count = int(
        session.scalar(
            select(func.count(Customer.id)).where(Customer.case_execution_id == execution.id)
        )
        or 0
    )
    order_count = int(
        session.scalar(select(func.count(Order.id)).where(Order.case_execution_id == execution.id))
        or 0
    )
    if "category" in case.expected:
        fixture = ScenarioFixture.model_validate(case.fixture)
        expected_counts = (len(fixture.customers), len(fixture.orders))
        if (customer_count, order_count) == (0, 0):
            _scenario_definition(case)
            _materialize_scenario_fixture(session, execution, case)
        elif (customer_count, order_count) != expected_counts:
            raise UnsupportedCaseDefinitionError(
                "Persisted real-provider fixture is incomplete or unexpected"
            )
    else:
        if (customer_count, order_count) == (0, 0):
            _materialize_legacy_fixture(session, execution, case)
        elif (customer_count, order_count) != (1, 1):
            raise UnsupportedCaseDefinitionError(
                "Persisted legacy real-provider fixture is incomplete or unexpected"
            )


def _instructions(agent_prompt: AgentPromptDefinition) -> str:
    return f"{agent_prompt.instructions}\n\n{BASE_POLICY_INSTRUCTIONS}"


def _initial_input(case: EvaluationCase) -> list[dict[str, Any]]:
    requester_customer_id, policy_as_of = _fixture_context(case)
    trusted_context: dict[str, Any] = {"requester_customer_id": requester_customer_id}
    if policy_as_of is not None:
        trusted_context["policy_as_of"] = policy_as_of
    content = json.dumps(
        {"customer_request": case.prompt, "trusted_context": trusted_context},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return [{"role": "user", "content": [{"type": "input_text", "text": content}]}]


def _next_input(previous: ProviderTurn) -> list[dict[str, Any]]:
    request_input = previous.request_payload.get("input")
    response_payload = previous.response_payload or {}
    response_output = response_payload.get("output")
    tool_outputs = previous.tool_outputs
    if (
        not isinstance(request_input, list)
        or not isinstance(response_output, list)
        or not isinstance(tool_outputs, list)
    ):
        raise ProviderResponseInvalidError("Persisted provider turn history is incomplete")
    combined = [*request_input, *response_output, *tool_outputs]
    if not all(isinstance(item, dict) for item in combined):
        raise ProviderResponseInvalidError("Persisted provider history contains invalid items")
    return deepcopy(cast(list[dict[str, Any]], combined))


def _config_int(config: dict[str, Any], key: str) -> int:
    value = config.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise UnsupportedCaseDefinitionError(f"Provider config {key!r} is invalid")
    return value


def _config_decimal(config: dict[str, Any], key: str) -> Decimal:
    value = config.get(key)
    if not isinstance(value, str):
        raise UnsupportedCaseDefinitionError(f"Provider config {key!r} is invalid")
    try:
        return Decimal(value)
    except ArithmeticError as exc:
        raise UnsupportedCaseDefinitionError(f"Provider config {key!r} is invalid") from exc


def _pricing_config(config: dict[str, Any]) -> tuple[dict[str, Any], int]:
    pricing = config.get("pricing")
    if not isinstance(pricing, dict):
        raise UnsupportedCaseDefinitionError("Provider pricing config is invalid")
    unit_tokens = _config_int(pricing, "unit_tokens")
    if unit_tokens <= 0:
        raise UnsupportedCaseDefinitionError("Provider pricing token unit is invalid")
    for key in ("input_usd", "cached_input_usd", "output_usd"):
        rate = _config_decimal(pricing, key)
        if not rate.is_finite() or rate < 0:
            raise UnsupportedCaseDefinitionError(f"Provider pricing {key!r} is invalid")
    return pricing, unit_tokens


def _request_ceiling_micros(request: dict[str, Any], config: dict[str, Any]) -> int:
    pricing, unit_tokens = _pricing_config(config)
    payload_bytes = len(
        json.dumps(
            request,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
    )
    output_bound = request.get("max_output_tokens")
    if not isinstance(output_bound, int) or isinstance(output_bound, bool):
        raise UnsupportedCaseDefinitionError("Prepared max_output_tokens is invalid")
    # One serialized ASCII byte per possible input token is deliberately pessimistic.
    estimated_usd = (
        Decimal(payload_bytes) * _config_decimal(pricing, "input_usd")
        + Decimal(output_bound) * _config_decimal(pricing, "output_usd")
    ) / Decimal(unit_tokens)
    micro_usd = estimated_usd * Decimal(1_000_000)
    return int(math.ceil(micro_usd))


def _usage_cost_micros(usage: TokenUsage | None, config: dict[str, Any]) -> int | None:
    if usage is None:
        return None
    pricing, unit_tokens = _pricing_config(config)
    cached_tokens = usage.cached_input_tokens or 0
    uncached_tokens = usage.input_tokens - cached_tokens
    estimated_usd = (
        Decimal(uncached_tokens) * _config_decimal(pricing, "input_usd")
        + Decimal(cached_tokens) * _config_decimal(pricing, "cached_input_usd")
        + Decimal(usage.output_tokens) * _config_decimal(pricing, "output_usd")
    ) / Decimal(unit_tokens)
    return int(math.ceil(estimated_usd * Decimal(1_000_000)))


def _response_model_matches(configured_model: str | None, response_model: str | None) -> bool:
    if configured_model is None or response_model is None:
        return False
    return response_model == configured_model or (
        re.fullmatch(
            rf"{re.escape(configured_model)}-\d{{4}}-\d{{2}}-\d{{2}}",
            response_model,
        )
        is not None
    )


def _turn_reserved_micros(turn: ProviderTurn, config: dict[str, Any]) -> int:
    if turn.estimated_cost_usd_micros is not None:
        return turn.estimated_cost_usd_micros
    if turn.status == "superseded" and turn.request_started_at is None:
        return 0
    return _request_ceiling_micros(turn.request_payload, config)


def _reserve_spend(
    session: Session,
    experiment: Experiment,
    request: dict[str, Any],
) -> None:
    config = experiment.provider_config
    if config is None:
        raise UnsupportedCaseDefinitionError("Real-provider experiment has no provider config")
    turns = session.scalars(
        select(ProviderTurn)
        .join(CaseExecution, CaseExecution.id == ProviderTurn.case_execution_id)
        .where(CaseExecution.experiment_id == experiment.id)
        .with_for_update()
    ).all()
    reserved = sum(_turn_reserved_micros(turn, config) for turn in turns)
    proposed = _request_ceiling_micros(request, config)
    cap_micros = int(_config_decimal(config, "spend_cap_usd") * Decimal(1_000_000))
    if reserved + proposed > cap_micros:
        raise ProviderSpendCapExceeded(
            f"Provider spend ceiling would be exceeded: cap_usd={config['spend_cap_usd']}"
        )


def _verify_provider_identity(
    experiment: Experiment,
    provider: OpenAIResponsesProvider,
) -> dict[str, Any]:
    config = experiment.provider_config
    if (
        experiment.provider_model is None
        or config is None
        or experiment.provider_config_hash is None
    ):
        raise UnsupportedCaseDefinitionError("Real-provider identity is incomplete")
    if provider_config_hash(config) != experiment.provider_config_hash:
        raise UnsupportedCaseDefinitionError("Real-provider config hash does not match")
    if experiment.provider_model != provider.model or config.get("model") != provider.model:
        raise UnsupportedCaseDefinitionError("Prepared provider model differs from experiment")
    if config.get("reasoning_effort") != provider.reasoning_effort:
        raise UnsupportedCaseDefinitionError("Prepared reasoning effort differs from experiment")
    if config.get("max_output_tokens") != provider.max_output_tokens:
        raise UnsupportedCaseDefinitionError("Prepared output bound differs from experiment")
    if config.get("request_timeout_seconds") != str(provider.timeout_seconds):
        raise UnsupportedCaseDefinitionError("Prepared timeout differs from experiment")
    return config


def _prepare_turn(
    factory: sessionmaker[Session],
    execution_id: UUID,
    *,
    expected_attempt_number: int,
    expected_dispatch_generation: int,
    provider: OpenAIResponsesProvider,
) -> ProviderTurn:
    with factory.begin() as session:
        execution, experiment, case = _lock_execution(
            session,
            execution_id,
            expected_attempt_number=expected_attempt_number,
            expected_dispatch_generation=expected_dispatch_generation,
        )
        config = _verify_provider_identity(experiment, provider)
        _renew_lease(session, execution, experiment, expected_attempt_number)
        attempt = session.scalar(
            select(ExecutionAttempt).where(
                ExecutionAttempt.case_execution_id == execution.id,
                ExecutionAttempt.attempt_number == expected_attempt_number,
            )
        )
        if attempt is None:
            raise StaleExecutionClaimError("The provider attempt is missing")
        _ensure_materialized(session, execution, case)
        agent_prompt = _validated_prompt(session, experiment)
        previous = session.scalar(
            select(ProviderTurn)
            .where(ProviderTurn.case_execution_id == execution.id)
            .order_by(ProviderTurn.sequence_number.desc())
            .limit(1)
            .with_for_update()
        )
        if previous is None:
            sequence_number = 1
            input_items = _initial_input(case)
        else:
            if previous.status != "tools_completed":
                return previous
            sequence_number = previous.sequence_number + 1
            if sequence_number > _config_int(config, "max_turns"):
                raise ProviderTurnLimitExceeded(
                    f"Provider turn limit exceeded: limit={config['max_turns']}"
                )
            input_items = _next_input(previous)

        request = provider.prepare_request(
            instructions=_instructions(agent_prompt),
            input_items=input_items,
            tools=PROVIDER_TOOLS,
            final_schema_name="parceldesk_final_resolution",
            final_schema=ProviderFinalResolution.model_json_schema(),
        )
        _reserve_spend(session, experiment, request)
        turn = ProviderTurn(
            case_execution_id=execution.id,
            attempt_number=expected_attempt_number,
            # This is immutable logical-attempt provenance. The current resume
            # delivery generation remains the CaseExecution fence above.
            dispatch_generation=attempt.dispatch_generation,
            sequence_number=sequence_number,
            status="prepared",
            client_request_id=str(uuid4()),
            request_hash=definition_hash(request),
            request_payload=request,
        )
        session.add(turn)
        _append_trace(
            session,
            execution.id,
            "provider_request_prepared",
            {
                "provider": experiment.provider,
                "model": experiment.provider_model,
                "provider_config_hash": experiment.provider_config_hash,
                "turn": sequence_number,
                "client_request_id": turn.client_request_id,
                "request_hash": turn.request_hash,
            },
        )
        session.flush()
        return turn


def _start_turn(
    factory: sessionmaker[Session],
    execution_id: UUID,
    turn_id: UUID,
    *,
    expected_attempt_number: int,
    expected_dispatch_generation: int,
    provider: OpenAIResponsesProvider,
) -> dict[str, Any]:
    with factory.begin() as session:
        execution, experiment, _ = _lock_execution(
            session,
            execution_id,
            expected_attempt_number=expected_attempt_number,
            expected_dispatch_generation=expected_dispatch_generation,
        )
        _renew_lease(session, execution, experiment, expected_attempt_number)
        turn = session.scalar(
            select(ProviderTurn).where(ProviderTurn.id == turn_id).with_for_update()
        )
        if turn is None:
            raise ExecutionNotFoundError(f"Provider turn {turn_id} was not found")
        if turn.status == "request_started":
            raise ProviderOutcomeIndeterminateError(
                "A provider request was already started without a durable response"
            )
        if turn.status != "prepared":
            return deepcopy(turn.request_payload)
        if definition_hash(turn.request_payload) != turn.request_hash:
            raise OpenAIProviderConfigurationError(
                "Persisted OpenAI request hash does not match its payload"
            )
        # This is pure local validation. It must succeed before request_started is
        # committed so a configuration/schema failure cannot look like an unknown
        # network outcome during recovery.
        prepared = provider.validate_prepared(turn.request_payload)
        turn.status = "request_started"
        turn.request_started_at = datetime.now(UTC)
        _append_trace(
            session,
            execution.id,
            "provider_request_started",
            {
                "turn": turn.sequence_number,
                "client_request_id": turn.client_request_id,
                "request_hash": turn.request_hash,
            },
        )
        return prepared


def _response_payload(turn: ResponsesTurn) -> dict[str, Any]:
    return {
        "request_id": turn.request_id,
        "response_id": turn.response_id,
        "model": turn.model,
        "status": turn.status,
        "output": deepcopy(list(turn.output_items)),
        "final_json": deepcopy(turn.final_json),
        "failure_code": turn.failure_code,
        "incomplete_reason": turn.incomplete_reason,
        "usage": deepcopy(turn.usage.raw) if turn.usage is not None else None,
    }


def _usage_fields(
    usage: TokenUsage | None,
    config: dict[str, Any],
    *,
    estimate_cost: bool,
) -> dict[str, int | None]:
    if usage is None:
        return {
            "input_tokens": None,
            "cached_input_tokens": None,
            "output_tokens": None,
            "reasoning_tokens": None,
            "total_tokens": None,
            "estimated_cost_usd_micros": None,
        }
    return {
        "input_tokens": usage.input_tokens,
        "cached_input_tokens": usage.cached_input_tokens or 0,
        "output_tokens": usage.output_tokens,
        "reasoning_tokens": usage.reasoning_output_tokens or 0,
        "total_tokens": usage.total_tokens,
        "estimated_cost_usd_micros": (_usage_cost_micros(usage, config) if estimate_cost else None),
    }


def _usage_from_envelope(raw: dict[str, Any] | None) -> TokenUsage | None:
    """Recover measured usage when another response field failed protocol validation."""

    if raw is None:
        return None
    input_tokens = raw.get("input_tokens")
    output_tokens = raw.get("output_tokens")
    total_tokens = raw.get("total_tokens")
    if not all(
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
        and value <= MAX_PERSISTED_TOKEN_COUNT
        for value in (input_tokens, output_tokens, total_tokens)
    ):
        return None
    if cast(int, total_tokens) < cast(int, input_tokens) + cast(int, output_tokens):
        return None
    input_details = raw.get("input_tokens_details")
    output_details = raw.get("output_tokens_details")
    cached_tokens = input_details.get("cached_tokens") if isinstance(input_details, dict) else None
    reasoning_tokens = (
        output_details.get("reasoning_tokens") if isinstance(output_details, dict) else None
    )
    if cached_tokens is not None and (
        not isinstance(cached_tokens, int)
        or isinstance(cached_tokens, bool)
        or cached_tokens < 0
        or cached_tokens > cast(int, input_tokens)
    ):
        return None
    if reasoning_tokens is not None and (
        not isinstance(reasoning_tokens, int)
        or isinstance(reasoning_tokens, bool)
        or reasoning_tokens < 0
        or reasoning_tokens > cast(int, output_tokens)
    ):
        return None
    return TokenUsage(
        input_tokens=cast(int, input_tokens),
        output_tokens=cast(int, output_tokens),
        total_tokens=cast(int, total_tokens),
        cached_input_tokens=cached_tokens,
        reasoning_output_tokens=reasoning_tokens,
        raw=deepcopy(raw),
    )


def _validated_calls(turn: ResponsesTurn) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for call in turn.function_calls:
        argument_type = _TOOL_TYPES.get(call.name)
        if argument_type is None:
            raise ProviderResponseInvalidError("Provider requested an unknown function")
        try:
            validated = argument_type.model_validate(call.arguments, strict=True)
        except ValidationError as exc:
            raise ProviderResponseInvalidError(
                "Provider requested a function with invalid arguments"
            ) from exc
        calls.append(
            {
                "call_id": call.call_id,
                "name": call.name,
                "arguments": validated.model_dump(mode="json"),
            }
        )
    return calls


def _validate_final(turn: ResponsesTurn) -> dict[str, Any] | None:
    if turn.final_json is None:
        return None
    try:
        validated = ProviderFinalResolution.model_validate(turn.final_json, strict=True)
    except ValidationError as exc:
        raise ProviderResponseInvalidError(
            "Provider final resolution failed its persisted schema"
        ) from exc
    return validated.model_dump(mode="json", exclude_none=True)


def _set_usage(
    turn_row: ProviderTurn,
    usage: TokenUsage | None,
    config: dict[str, Any],
    *,
    estimate_cost: bool,
) -> None:
    fields = _usage_fields(usage, config, estimate_cost=estimate_cost)
    for name, value in fields.items():
        setattr(turn_row, name, value)


def _persist_invalid_envelope(
    factory: sessionmaker[Session],
    execution_id: UUID,
    turn_id: UUID,
    evidence: ResponsesEvidenceEnvelope,
    *,
    latency_ms: int,
    safe_message: str,
) -> None:
    with factory.begin() as session:
        execution = session.scalar(
            select(CaseExecution).where(CaseExecution.id == execution_id).with_for_update()
        )
        turn = session.scalar(
            select(ProviderTurn).where(ProviderTurn.id == turn_id).with_for_update()
        )
        if execution is None or turn is None or turn.status != "request_started":
            return
        experiment = session.get(Experiment, execution.experiment_id)
        if experiment is None or experiment.provider_config is None:
            raise ExecutionNotFoundError("Real-provider experiment config is missing")
        envelope = evidence.as_json()
        turn.status = "response_invalid"
        turn.provider_request_id = evidence.request_id
        turn.provider_response_id = evidence.response_id
        turn.response_model = evidence.model
        turn.response_payload = envelope
        turn.tool_calls = None
        _set_usage(
            turn,
            _usage_from_envelope(evidence.usage),
            experiment.provider_config,
            estimate_cost=_response_model_matches(
                experiment.provider_model,
                evidence.model,
            ),
        )
        turn.latency_ms = latency_ms
        turn.http_status_code = 200
        turn.retryable = False
        turn.error_kind = "provider_protocol"
        turn.safe_error_message = safe_message
        turn.response_received_at = datetime.now(UTC)
        turn.completed_at = turn.response_received_at
        _append_trace(
            session,
            execution.id,
            "provider_response_invalid",
            {
                "turn": turn.sequence_number,
                "client_request_id": turn.client_request_id,
                "provider_request_id": evidence.request_id,
                "provider_response_id": evidence.response_id,
                "response_model": evidence.model,
                "latency_ms": latency_ms,
                "error_kind": "provider_protocol",
            },
        )


def _persist_request_error(
    factory: sessionmaker[Session],
    execution_id: UUID,
    turn_id: UUID,
    error: OpenAIProviderRequestError,
    *,
    latency_ms: int,
) -> None:
    with factory.begin() as session:
        execution = session.scalar(
            select(CaseExecution).where(CaseExecution.id == execution_id).with_for_update()
        )
        turn = session.scalar(
            select(ProviderTurn).where(ProviderTurn.id == turn_id).with_for_update()
        )
        if execution is None or turn is None or turn.status != "request_started":
            return
        turn.status = (
            "indeterminate" if error.kind in {"timeout", "connection", "unexpected"} else "failed"
        )
        turn.provider_request_id = error.request_id
        turn.latency_ms = latency_ms
        turn.http_status_code = error.status_code
        turn.retryable = False
        turn.error_kind = f"provider_{error.kind}"[:50]
        turn.safe_error_message = str(error)[:1000]
        turn.completed_at = datetime.now(UTC)
        _append_trace(
            session,
            execution.id,
            "provider_request_failed",
            {
                "turn": turn.sequence_number,
                "client_request_id": turn.client_request_id,
                "provider_request_id": error.request_id,
                "latency_ms": latency_ms,
                "error_kind": turn.error_kind,
                "outcome_indeterminate": turn.status == "indeterminate",
                "retryable": False,
            },
        )


def _persist_response(
    factory: sessionmaker[Session],
    execution_id: UUID,
    turn_id: UUID,
    response: ResponsesTurn,
    *,
    latency_ms: int,
    expected_attempt_number: int,
    expected_dispatch_generation: int,
) -> str:
    invalid_reason: str | None = None
    calls: list[dict[str, Any]] = []
    final_resolution: dict[str, Any] | None = None
    try:
        if response.status != "completed" or response.failure_code is not None:
            raise ProviderResponseInvalidError("Provider response did not complete successfully")
        calls = _validated_calls(response)
        final_resolution = _validate_final(response)
        if bool(calls) == (final_resolution is not None):
            raise ProviderResponseInvalidError(
                "Provider response must contain either one function call or one final result"
            )
    except ProviderResponseInvalidError as exc:
        invalid_reason = str(exc)

    with factory.begin() as session:
        execution = session.scalar(
            select(CaseExecution).where(CaseExecution.id == execution_id).with_for_update()
        )
        turn = session.scalar(
            select(ProviderTurn).where(ProviderTurn.id == turn_id).with_for_update()
        )
        if execution is None or turn is None:
            raise ExecutionNotFoundError(str(execution_id))
        experiment = session.get(Experiment, execution.experiment_id)
        if experiment is None or experiment.provider_config is None:
            raise ExecutionNotFoundError("Real-provider experiment config is missing")
        prior_status = turn.status
        if prior_status not in {"request_started", "indeterminate"}:
            return turn.status
        late_after_indeterminate = prior_status == "indeterminate"
        response_model_matches = _response_model_matches(
            experiment.provider_model,
            response.model,
        )
        if not response_model_matches:
            invalid_reason = "Provider returned a model outside the approved experiment identity"
        is_current = (
            execution.status == "running"
            and execution.attempt_count == expected_attempt_number
            and execution.dispatch_generation == expected_dispatch_generation
        )
        now = datetime.now(UTC)
        if turn.provider_request_id is None:
            turn.provider_request_id = response.request_id
        turn.provider_response_id = response.response_id
        turn.response_model = response.model
        turn.response_payload = _response_payload(response)
        turn.tool_calls = calls
        if turn.latency_ms is None:
            turn.latency_ms = latency_ms
        if turn.http_status_code is None:
            turn.http_status_code = 200
        turn.response_received_at = now
        _set_usage(
            turn,
            response.usage,
            experiment.provider_config,
            estimate_cost=response_model_matches,
        )
        if late_after_indeterminate or not is_current:
            turn.status = "superseded"
            turn.tool_outputs = []
            if turn.completed_at is None:
                turn.completed_at = now
            _append_trace(
                session,
                execution.id,
                "provider_response_superseded",
                {
                    "turn": turn.sequence_number,
                    "client_request_id": turn.client_request_id,
                    "provider_request_id": turn.provider_request_id,
                    "provider_response_id": response.response_id,
                    "response_model": response.model,
                    "tool_names": [call["name"] for call in calls],
                    "has_final_resolution": final_resolution is not None,
                    "input_tokens": turn.input_tokens,
                    "cached_input_tokens": turn.cached_input_tokens,
                    "output_tokens": turn.output_tokens,
                    "reasoning_tokens": turn.reasoning_tokens,
                    "total_tokens": turn.total_tokens,
                    "estimated_cost_usd_micros": turn.estimated_cost_usd_micros,
                    "latency_ms": turn.latency_ms,
                    "late_after_indeterminate": late_after_indeterminate,
                    "business_effects_applied": False,
                },
            )
            return "superseded"
        if invalid_reason is not None:
            turn.status = "response_invalid"
            turn.tool_outputs = None
            turn.retryable = False
            turn.error_kind = "provider_response_invalid"
            turn.safe_error_message = invalid_reason[:1000]
            turn.completed_at = now
        elif calls:
            turn.status = "response_received"
            turn.tool_outputs = []
        else:
            turn.status = "final_response"
            turn.tool_outputs = None
            turn.completed_at = now
        _append_trace(
            session,
            execution.id,
            "provider_response",
            {
                "turn": turn.sequence_number,
                "client_request_id": turn.client_request_id,
                "provider_request_id": response.request_id,
                "provider_response_id": response.response_id,
                "response_model": response.model,
                "status": turn.status,
                "tool_names": [call["name"] for call in calls],
                "has_final_resolution": final_resolution is not None,
                "input_tokens": turn.input_tokens,
                "cached_input_tokens": turn.cached_input_tokens,
                "output_tokens": turn.output_tokens,
                "reasoning_tokens": turn.reasoning_tokens,
                "total_tokens": turn.total_tokens,
                "estimated_cost_usd_micros": turn.estimated_cost_usd_micros,
                "latency_ms": latency_ms,
            },
        )
        return turn.status


def _call_tool(runtime: ToolRuntime, call: dict[str, Any]) -> BaseModel:
    name = str(call["name"])
    call_id = str(call["call_id"])
    arguments = cast(dict[str, Any], call["arguments"])
    if name == "lookup_order":
        return runtime.lookup_order(
            LookupOrderArgs.model_validate(arguments, strict=True),
            tool_call_id=call_id,
        )
    if name == "issue_refund":
        return runtime.issue_refund(
            IssueRefundArgs.model_validate(arguments, strict=True),
            tool_call_id=call_id,
        )
    if name == "update_shipping_address":
        return runtime.update_shipping_address(
            UpdateShippingAddressArgs.model_validate(arguments, strict=True),
            tool_call_id=call_id,
        )
    if name == "escalate_ticket":
        return runtime.escalate_ticket(
            EscalateTicketArgs.model_validate(arguments, strict=True),
            tool_call_id=call_id,
        )
    raise ProviderResponseInvalidError("Persisted provider tool name is unsupported")


def _processed_tool_count(session: Session, execution_id: UUID) -> int:
    turns = session.scalars(
        select(ProviderTurn).where(ProviderTurn.case_execution_id == execution_id)
    ).all()
    return sum(len(turn.tool_outputs or []) for turn in turns)


def _execute_next_tool(
    factory: sessionmaker[Session],
    execution_id: UUID,
    turn_id: UUID,
    *,
    expected_attempt_number: int,
    expected_dispatch_generation: int,
) -> str:
    with factory.begin() as session:
        execution, experiment, case = _lock_execution(
            session,
            execution_id,
            expected_attempt_number=expected_attempt_number,
            expected_dispatch_generation=expected_dispatch_generation,
        )
        _renew_lease(session, execution, experiment, expected_attempt_number)
        turn = session.scalar(
            select(ProviderTurn).where(ProviderTurn.id == turn_id).with_for_update()
        )
        if turn is None:
            raise ExecutionNotFoundError(f"Provider turn {turn_id} was not found")
        if turn.status != "response_received":
            return turn.status
        calls = turn.tool_calls or []
        outputs = list(turn.tool_outputs or [])
        index = len(outputs)
        if index >= len(calls):
            raise ProviderResponseInvalidError("Provider turn has inconsistent tool progress")
        total_processed = _processed_tool_count(session, execution.id)
        if total_processed >= experiment.max_tool_steps:
            raise MaximumToolStepsExceeded(
                limit=experiment.max_tool_steps,
                attempted_steps=total_processed + 1,
            )
        actor_customer_id, _ = _fixture_context(case)
        runtime = ToolRuntime(
            session,
            execution.id,
            actor_customer_external_id=actor_customer_id,
            max_tool_steps=experiment.max_tool_steps - total_processed,
        )
        call = calls[index]
        if not isinstance(call, dict):
            raise ProviderResponseInvalidError("Persisted provider tool call is invalid")
        try:
            result = _call_tool(runtime, call)
            output: dict[str, Any] = {
                "ok": True,
                "result": result.model_dump(mode="json"),
            }
        except ToolExecutionError as exc:
            output = {
                "ok": False,
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc)[:500],
                },
            }
        call_id = call.get("call_id")
        if not isinstance(call_id, str):
            raise ProviderResponseInvalidError("Persisted provider call ID is invalid")
        outputs.append(function_call_output(call_id, output))
        turn.tool_outputs = outputs
        if len(outputs) == len(calls):
            completed = datetime.now(UTC)
            turn.status = "tools_completed"
            turn.tools_completed_at = completed
            turn.completed_at = completed
        attempt = session.scalar(
            select(ExecutionAttempt).where(
                ExecutionAttempt.case_execution_id == execution.id,
                ExecutionAttempt.attempt_number == expected_attempt_number,
            )
        )
        if attempt is not None:
            attempt.step_count = total_processed + 1
        return turn.status


def _latest_turn(
    factory: sessionmaker[Session],
    execution_id: UUID,
) -> tuple[UUID, str] | None:
    with factory() as session:
        turn = session.scalar(
            select(ProviderTurn)
            .where(ProviderTurn.case_execution_id == execution_id)
            .order_by(ProviderTurn.sequence_number.desc())
            .limit(1)
        )
        return None if turn is None else (turn.id, turn.status)


def _finalize(
    factory: sessionmaker[Session],
    execution_id: UUID,
    turn_id: UUID,
    *,
    expected_attempt_number: int,
    expected_dispatch_generation: int,
) -> dict[str, Any]:
    with factory.begin() as session:
        execution, experiment, case = _lock_execution(
            session,
            execution_id,
            expected_attempt_number=expected_attempt_number,
            expected_dispatch_generation=expected_dispatch_generation,
        )
        _renew_lease(session, execution, experiment, expected_attempt_number)
        turn = session.scalar(
            select(ProviderTurn).where(ProviderTurn.id == turn_id).with_for_update()
        )
        if turn is None or turn.status != "final_response":
            raise ProviderResponseInvalidError("No durable final provider response is available")
        raw_resolution = (turn.response_payload or {}).get("final_json")
        try:
            resolution = ProviderFinalResolution.model_validate(
                raw_resolution,
                strict=True,
            ).model_dump(mode="json", exclude_none=True)
        except ValidationError as exc:
            raise ProviderResponseInvalidError(
                "Persisted final provider resolution is invalid"
            ) from exc
        if session.scalar(
            select(func.count(GraderResult.id)).where(
                GraderResult.case_execution_id == execution.id
            )
        ):
            raise ProviderResponseInvalidError("Execution already has partial grader evidence")
        execution.final_resolution = resolution
        runtime = ToolRuntime(
            session,
            execution.id,
            max_tool_steps=experiment.max_tool_steps,
        )
        if "category" in case.expected:
            fixture = ScenarioFixture.model_validate(case.fixture)
            scenario_expectation = ScenarioCaseExpected.model_validate(case.expected)
            evidence = _collect_scenario_evidence(
                session,
                execution,
                fixture,
                scenario_expectation,
            )
            outcomes = grade_scenario(replace(evidence, provider_neutral=True))
        else:
            legacy_expectation = EligibleRefundExpectation.model_validate(case.expected)
            outcomes = grade_eligible_refund(
                _collect_refund_evidence(session, execution, legacy_expectation)
            )
        _persist_grader_results(session, execution, runtime, outcomes)
        completed = datetime.now(UTC)
        attempt = session.scalar(
            select(ExecutionAttempt)
            .where(
                ExecutionAttempt.case_execution_id == execution.id,
                ExecutionAttempt.attempt_number == expected_attempt_number,
            )
            .with_for_update()
        )
        if attempt is None or attempt.status != "running":
            raise StaleExecutionClaimError("The real-provider attempt is no longer running")
        attempt.status = "succeeded"
        attempt.step_count = _processed_tool_count(session, execution.id)
        attempt.completed_at = completed
        execution.status = "completed"
        execution.error_message = None
        execution.lease_expires_at = None
        execution.last_failure_kind = None
        execution.last_failure_retryable = None
        execution.completed_at = completed
        reconcile_experiment_status(session, experiment.id, completed_at=completed)
        return {
            "execution_id": str(execution.id),
            "status": "completed",
            "passed": all(outcome.passed for outcome in outcomes),
            "replayed": False,
        }


def execute_openai_case(
    case_execution_id: UUID,
    *,
    expected_attempt_number: int,
    expected_dispatch_generation: int,
    provider: OpenAIResponsesProvider | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> dict[str, Any]:
    """Run one case while committing every paid-call and tool boundary separately."""

    if expected_attempt_number < 1 or expected_dispatch_generation < 1:
        raise ValueError("Real-provider claim fence values must be positive")
    factory = session_factory or get_session_factory()
    selected_provider = provider

    for _ in range(100):
        latest = _latest_turn(factory, case_execution_id)
        if latest is None or latest[1] == "tools_completed":
            if selected_provider is None:
                selected_provider = _safe_provider(get_settings())
            turn = _prepare_turn(
                factory,
                case_execution_id,
                expected_attempt_number=expected_attempt_number,
                expected_dispatch_generation=expected_dispatch_generation,
                provider=selected_provider,
            )
            latest = (turn.id, turn.status)

        turn_id, status = latest
        if status == "prepared":
            if selected_provider is None:
                selected_provider = _safe_provider(get_settings())
            request = _start_turn(
                factory,
                case_execution_id,
                turn_id,
                expected_attempt_number=expected_attempt_number,
                expected_dispatch_generation=expected_dispatch_generation,
                provider=selected_provider,
            )
            started = time.perf_counter_ns()
            try:
                response = selected_provider.send_prepared(request)
            except OpenAIProviderRequestError as exc:
                latency_ms = max(0, round((time.perf_counter_ns() - started) / 1_000_000))
                _persist_request_error(
                    factory,
                    case_execution_id,
                    turn_id,
                    exc,
                    latency_ms=latency_ms,
                )
                raise ProviderOutcomeIndeterminateError(str(exc)) from None
            except OpenAIProviderProtocolError as exc:
                latency_ms = max(0, round((time.perf_counter_ns() - started) / 1_000_000))
                if exc.evidence is not None:
                    _persist_invalid_envelope(
                        factory,
                        case_execution_id,
                        turn_id,
                        exc.evidence,
                        latency_ms=latency_ms,
                        safe_message="Provider response failed protocol validation",
                    )
                raise ProviderResponseInvalidError(
                    "Provider response failed protocol validation"
                ) from None
            latency_ms = max(0, round((time.perf_counter_ns() - started) / 1_000_000))
            status = _persist_response(
                factory,
                case_execution_id,
                turn_id,
                response,
                latency_ms=latency_ms,
                expected_attempt_number=expected_attempt_number,
                expected_dispatch_generation=expected_dispatch_generation,
            )
            if status == "superseded":
                raise StaleExecutionClaimError("Provider response arrived after claim fencing")

        if status == "response_received":
            _execute_next_tool(
                factory,
                case_execution_id,
                turn_id,
                expected_attempt_number=expected_attempt_number,
                expected_dispatch_generation=expected_dispatch_generation,
            )
            continue
        if status == "final_response":
            return _finalize(
                factory,
                case_execution_id,
                turn_id,
                expected_attempt_number=expected_attempt_number,
                expected_dispatch_generation=expected_dispatch_generation,
            )
        if status == "request_started":
            raise ProviderOutcomeIndeterminateError(
                "A provider request crossed the send fence without a durable response"
            )
        if status in {"response_invalid", "failed", "indeterminate"}:
            raise ProviderResponseInvalidError(
                f"Provider turn is terminal and cannot continue: status={status}"
            )
        if status == "superseded":
            raise StaleExecutionClaimError("Provider turn was superseded by a newer claim")
        if status == "tools_completed":
            continue
        raise ProviderResponseInvalidError(f"Unsupported provider turn status: {status}")
    raise ProviderTurnLimitExceeded("Provider execution loop exceeded its internal safety bound")
