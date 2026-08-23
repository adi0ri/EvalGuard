"""PostgreSQL coverage for immutable provider identity and paid-turn evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from evalguard.db.models import (
    CaseExecution,
    ExecutionAttempt,
    ExecutionDispatch,
    Experiment,
    ProviderTurn,
)
from evalguard.evaluation.prompts import DEFAULT_PROMPT_VERSION

pytestmark = pytest.mark.integration
ExecutionFactory = Callable[..., UUID]

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
PROVIDER_CONFIG = {
    "max_output_tokens": 512,
    "reasoning_effort": "minimal",
    "store": False,
}
PROVIDER_CONFIG_HASH = hashlib.sha256(
    json.dumps(PROVIDER_CONFIG, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
REQUEST_HASH = hashlib.sha256(b"synthetic persisted request").hexdigest()


class SeededDefinition(Protocol):
    suite_id: UUID
    case_id: UUID

    def prompt_id_for(self, version: str) -> UUID: ...


def _create_real_attempt(
    session: Session,
    definitions: SeededDefinition,
) -> tuple[Experiment, CaseExecution, ExecutionAttempt]:
    experiment = Experiment(
        suite_id=definitions.suite_id,
        agent_prompt_id=definitions.prompt_id_for(DEFAULT_PROMPT_VERSION),
        provider="openai-responses",
        provider_model="synthetic-model-v1",
        provider_config=PROVIDER_CONFIG,
        provider_config_hash=PROVIDER_CONFIG_HASH,
        status="running",
        idempotency_key=f"phase6-ledger-{uuid4()}",
        trials=1,
    )
    session.add(experiment)
    session.flush()
    execution = CaseExecution(
        experiment_id=experiment.id,
        case_id=definitions.case_id,
        trial_number=1,
        status="running",
        attempt_count=1,
        dispatch_generation=1,
        started_at=NOW,
    )
    session.add(execution)
    session.flush()
    attempt = ExecutionAttempt(
        case_execution_id=execution.id,
        attempt_number=1,
        dispatch_generation=1,
        job_id=f"phase6-ledger-{uuid4()}",
        status="running",
        step_count=0,
        started_at=NOW,
        lease_expires_at=NOW + timedelta(minutes=1),
    )
    session.add(attempt)
    session.commit()
    return experiment, execution, attempt


def _prepared_turn(execution_id: UUID, *, sequence_number: int) -> ProviderTurn:
    return ProviderTurn(
        case_execution_id=execution_id,
        attempt_number=1,
        dispatch_generation=1,
        sequence_number=sequence_number,
        status="prepared",
        client_request_id=f"client-request-{uuid4()}",
        request_hash=REQUEST_HASH,
        request_payload={
            "model": "synthetic-model-v1",
            "input": [{"role": "user", "content": "synthetic request"}],
        },
        prepared_at=NOW,
    )


def _start_turn(session: Session, turn: ProviderTurn) -> None:
    turn.status = "request_started"
    turn.request_started_at = NOW + timedelta(milliseconds=1)
    session.commit()


def test_legacy_fake_identity_stays_null_and_cannot_create_paid_turns(
    db_session: Session,
    execution_factory: ExecutionFactory,
) -> None:
    execution_id = execution_factory(materialize_state=False)
    execution = db_session.get(CaseExecution, execution_id)
    assert execution is not None
    experiment = db_session.get(Experiment, execution.experiment_id)
    assert experiment is not None
    assert experiment.provider == "deterministic-fake"
    assert experiment.provider_model is None
    assert experiment.provider_config is None
    assert experiment.provider_config_hash is None

    execution.status = "running"
    execution.attempt_count = 1
    db_session.add(
        ExecutionAttempt(
            case_execution_id=execution.id,
            attempt_number=1,
            dispatch_generation=1,
            job_id=f"fake-ledger-{uuid4()}",
            status="running",
            started_at=NOW,
            lease_expires_at=NOW + timedelta(minutes=1),
        )
    )
    db_session.commit()
    db_session.add(_prepared_turn(execution.id, sequence_number=1))
    with pytest.raises(DBAPIError, match="provider turns require a configured provider identity"):
        db_session.commit()
    db_session.rollback()
    assert db_session.scalar(select(func.count()).select_from(ProviderTurn)) == 0


def test_provider_identity_is_complete_and_immutable(
    db_session: Session,
    seeded_suite: SeededDefinition,
) -> None:
    partial = Experiment(
        suite_id=seeded_suite.suite_id,
        agent_prompt_id=seeded_suite.prompt_id_for(DEFAULT_PROMPT_VERSION),
        provider="openai-responses",
        provider_model="synthetic-model-v1",
        status="queued",
    )
    db_session.add(partial)
    with pytest.raises(IntegrityError, match="ck_experiments_provider_identity_complete"):
        db_session.commit()
    db_session.rollback()

    experiment, _, _ = _create_real_attempt(db_session, seeded_suite)
    with pytest.raises(DBAPIError, match="experiment provider identity is immutable"):
        db_session.execute(
            update(Experiment)
            .where(Experiment.id == experiment.id)
            .values(provider_model="synthetic-model-v2")
        )
        db_session.commit()
    db_session.rollback()


def test_tool_outputs_are_an_append_only_persisted_prefix(
    db_session: Session,
    seeded_suite: SeededDefinition,
) -> None:
    _, execution, _ = _create_real_attempt(db_session, seeded_suite)
    turn = _prepared_turn(execution.id, sequence_number=1)
    db_session.add(turn)
    db_session.commit()
    db_session.add(_prepared_turn(execution.id, sequence_number=2))
    with pytest.raises(IntegrityError, match="uq_provider_turns_execution_open"):
        db_session.commit()
    db_session.rollback()
    turn = db_session.get(ProviderTurn, turn.id)
    assert turn is not None
    _start_turn(db_session, turn)

    turn.status = "response_received"
    turn.provider_request_id = "provider-request-1"
    turn.provider_response_id = "provider-response-1"
    turn.response_model = "synthetic-model-v1-2026-08-23"
    turn.response_payload = {"id": "provider-response-1", "output": ["two tool calls"]}
    turn.tool_calls = [
        {"call_id": "call-1", "name": "lookup_order", "arguments": {"order_id": "PD-1"}},
        {"call_id": "call-2", "name": "escalate_ticket", "arguments": {"order_id": "PD-1"}},
    ]
    turn.tool_outputs = []
    turn.input_tokens = 100
    turn.cached_input_tokens = 20
    turn.output_tokens = 30
    turn.reasoning_tokens = 5
    turn.total_tokens = 130
    turn.estimated_cost_usd_micros = 425
    turn.latency_ms = 240
    turn.http_status_code = 200
    turn.response_received_at = NOW + timedelta(milliseconds=241)
    db_session.commit()

    first_output = {"call_id": "call-1", "output": {"status": "delivered"}}
    second_output = {"call_id": "call-2", "output": {"status": "open"}}
    turn.tool_outputs = [first_output, second_output]
    with pytest.raises(DBAPIError, match="persisted provider turn evidence is immutable"):
        db_session.commit()
    db_session.rollback()

    turn = db_session.get(ProviderTurn, turn.id)
    assert turn is not None
    turn.tool_outputs = [first_output]
    db_session.commit()

    turn.tool_outputs = [first_output, second_output]
    turn.status = "tools_completed"
    turn.tools_completed_at = NOW + timedelta(milliseconds=300)
    turn.completed_at = NOW + timedelta(milliseconds=300)
    db_session.commit()
    assert turn.tool_outputs == [first_output, second_output]

    with pytest.raises(DBAPIError, match="provider turn request identity is immutable"):
        db_session.execute(
            update(ProviderTurn).where(ProviderTurn.id == turn.id).values(request_hash="f" * 64)
        )
        db_session.commit()
    db_session.rollback()


def test_resume_dispatch_is_bound_to_the_same_logical_attempt(
    db_session: Session,
    seeded_suite: SeededDefinition,
) -> None:
    _, execution, attempt = _create_real_attempt(db_session, seeded_suite)
    wrong_origin = _prepared_turn(execution.id, sequence_number=1)
    wrong_origin.dispatch_generation = 2
    db_session.add(wrong_origin)
    with pytest.raises(DBAPIError, match="dispatch generation does not match its attempt"):
        db_session.commit()
    db_session.rollback()

    initial = ExecutionDispatch(
        case_execution_id=execution.id,
        generation=1,
        job_id=f"attempt-dispatch-{uuid4()}",
        status="dispatched",
        available_at=NOW,
    )
    db_session.add(initial)
    db_session.commit()
    assert initial.delivery_kind == "attempt"
    assert initial.attempt_number is None

    attempt.status = "resume_scheduled"
    attempt.recovery_count = 1
    resume = ExecutionDispatch(
        case_execution_id=execution.id,
        generation=2,
        job_id=f"resume-dispatch-{uuid4()}",
        delivery_kind="resume",
        attempt_number=attempt.attempt_number,
        status="pending",
        available_at=NOW,
    )
    db_session.add(resume)
    db_session.commit()
    assert resume.attempt_number == 1
    assert attempt.recovery_count == 1

    invalid = ExecutionDispatch(
        case_execution_id=execution.id,
        generation=3,
        job_id=f"invalid-dispatch-{uuid4()}",
        delivery_kind="attempt",
        attempt_number=attempt.attempt_number,
        status="pending",
        available_at=NOW,
    )
    db_session.add(invalid)
    with pytest.raises(IntegrityError, match="ck_execution_dispatches_delivery_identity_coherent"):
        db_session.commit()
    db_session.rollback()


def test_invalid_and_late_responses_retain_paid_evidence(
    db_session: Session,
    seeded_suite: SeededDefinition,
) -> None:
    _, execution, _ = _create_real_attempt(db_session, seeded_suite)

    invalid = _prepared_turn(execution.id, sequence_number=1)
    db_session.add(invalid)
    db_session.commit()
    _start_turn(db_session, invalid)
    invalid.status = "response_invalid"
    invalid.provider_request_id = "provider-request-invalid"
    invalid.response_payload = {"id": "provider-response-invalid", "output": "mixed schema"}
    invalid.input_tokens = 50
    invalid.output_tokens = 10
    invalid.total_tokens = 60
    invalid.estimated_cost_usd_micros = 175
    invalid.latency_ms = 180
    invalid.http_status_code = 200
    invalid.retryable = False
    invalid.error_kind = "invalid_provider_response"
    invalid.safe_error_message = "provider response failed the stored schema contract"
    invalid.response_received_at = NOW + timedelta(milliseconds=181)
    invalid.completed_at = NOW + timedelta(milliseconds=181)
    db_session.commit()
    assert invalid.response_payload["id"] == "provider-response-invalid"
    assert invalid.provider_response_id is None
    assert invalid.response_model is None
    assert invalid.estimated_cost_usd_micros == 175

    late = _prepared_turn(execution.id, sequence_number=2)
    db_session.add(late)
    db_session.commit()
    _start_turn(db_session, late)
    late.status = "superseded"
    late.provider_request_id = "provider-request-late"
    late.provider_response_id = "provider-response-late"
    late.response_model = "synthetic-model-v1-2026-08-23"
    late.response_payload = {"id": "provider-response-late", "output": "final"}
    late.tool_calls = []
    late.tool_outputs = []
    late.input_tokens = 40
    late.output_tokens = 8
    late.total_tokens = 48
    late.estimated_cost_usd_micros = 125
    late.latency_ms = 160
    late.http_status_code = 200
    late.response_received_at = NOW + timedelta(milliseconds=161)
    late.completed_at = NOW + timedelta(milliseconds=161)
    db_session.commit()
    assert late.tool_calls == []
    assert late.tool_outputs == []
    assert late.response_payload["id"] == "provider-response-late"
    assert late.estimated_cost_usd_micros == 125

    db_session.delete(late)
    with pytest.raises(DBAPIError, match="provider turns are durable and cannot be deleted"):
        db_session.commit()
    db_session.rollback()


def test_indeterminate_turn_accepts_late_response_as_superseded_evidence(
    db_session: Session,
    seeded_suite: SeededDefinition,
) -> None:
    _, execution, _ = _create_real_attempt(db_session, seeded_suite)
    turn = _prepared_turn(execution.id, sequence_number=1)
    db_session.add(turn)
    db_session.commit()
    _start_turn(db_session, turn)

    turn.status = "indeterminate"
    turn.retryable = False
    turn.error_kind = "request_outcome_unknown"
    turn.safe_error_message = "provider request outcome was not observable"
    turn.completed_at = NOW + timedelta(seconds=1)
    db_session.commit()

    turn.status = "superseded"
    turn.provider_request_id = "provider-request-after-timeout"
    turn.response_payload = {"output": "late malformed response without an id"}
    turn.tool_calls = []
    turn.tool_outputs = []
    turn.input_tokens = 20
    turn.output_tokens = 4
    turn.total_tokens = 24
    turn.estimated_cost_usd_micros = 60
    turn.latency_ms = 1_100
    turn.http_status_code = 200
    turn.response_received_at = NOW + timedelta(milliseconds=1_100)
    db_session.commit()

    assert turn.provider_response_id is None
    assert turn.response_model is None
    assert turn.error_kind == "request_outcome_unknown"
    assert turn.response_payload["output"] == "late malformed response without an id"
