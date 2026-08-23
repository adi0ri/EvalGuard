"""PostgreSQL coverage for bounded same-attempt real-provider recovery."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

import evalguard.workers.jobs as execution_jobs
from evalguard.db.models import (
    CaseExecution,
    ExecutionAttempt,
    ExecutionDispatch,
    Experiment,
    ProviderTurn,
)
from evalguard.evaluation.prompts import DEFAULT_PROMPT_VERSION
from evalguard.evaluation.suite import definition_hash
from evalguard.workers.dispatch import create_dispatch
from evalguard.workers.jobs import recover_interrupted_execution
from evalguard.workers.queue import job_id_for_execution

pytestmark = pytest.mark.integration
SessionFactory = sessionmaker[Session]
NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
PROVIDER_CONFIG: dict[str, Any] = {
    "api": "responses",
    "model": "synthetic-model-v1",
    "max_turns": 5,
    "network_attempts_per_turn": 1,
}


class SeededDefinition(Protocol):
    suite_id: UUID
    case_id: UUID

    def prompt_id_for(self, version: str) -> UUID: ...


def _create_real_execution(
    session_factory: SessionFactory,
    definitions: SeededDefinition,
) -> UUID:
    with session_factory.begin() as session:
        experiment = Experiment(
            suite_id=definitions.suite_id,
            agent_prompt_id=definitions.prompt_id_for(DEFAULT_PROMPT_VERSION),
            provider="openai-responses",
            provider_model="synthetic-model-v1",
            provider_config=PROVIDER_CONFIG,
            provider_config_hash=definition_hash(PROVIDER_CONFIG),
            status="queued",
            idempotency_key=f"phase6-worker-{uuid4()}",
            trials=1,
            max_attempts=1,
            job_timeout_seconds=30,
            retry_backoff_seconds=0,
        )
        session.add(experiment)
        session.flush()
        execution = CaseExecution(
            experiment_id=experiment.id,
            case_id=definitions.case_id,
            trial_number=1,
            status="queued",
        )
        session.add(execution)
        session.flush()
        create_dispatch(session, execution.id, generation=1)
        return execution.id


def _turn_for_state(
    execution_id: UUID,
    state: str,
    *,
    attempt_number: int = 1,
    dispatch_generation: int = 1,
) -> ProviderTurn:
    common: dict[str, Any] = {
        "case_execution_id": execution_id,
        "attempt_number": attempt_number,
        "dispatch_generation": dispatch_generation,
        "sequence_number": 1,
        "client_request_id": f"phase6-request-{uuid4()}",
        "request_hash": definition_hash({"synthetic": "request"}),
        "request_payload": {"model": "synthetic-model-v1", "input": []},
        "prepared_at": NOW,
    }
    if state == "prepared":
        return ProviderTurn(status=state, **common)
    if state == "request_started":
        return ProviderTurn(
            status=state,
            request_started_at=NOW + timedelta(milliseconds=1),
            **common,
        )

    tool_calls: list[dict[str, Any]] = [
        {
            "call_id": "call-1",
            "name": "lookup_order",
            "arguments": {"order_id": "PD-SYNTHETIC"},
        }
    ]
    tool_outputs: list[dict[str, Any]] | None = []
    tools_completed_at: datetime | None = None
    completed_at: datetime | None = None
    if state == "tools_completed":
        tool_outputs = [
            {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": "{}",
            }
        ]
        tools_completed_at = NOW + timedelta(milliseconds=3)
        completed_at = tools_completed_at
    elif state == "final_response":
        tool_calls = []
        tool_outputs = None
        completed_at = NOW + timedelta(milliseconds=2)
    return ProviderTurn(
        status=state,
        provider_response_id="response-synthetic",
        response_model="synthetic-model-v1",
        response_payload={"status": "completed", "final_json": {}},
        tool_calls=tool_calls,
        tool_outputs=tool_outputs,
        request_started_at=NOW + timedelta(milliseconds=1),
        response_received_at=NOW + timedelta(milliseconds=2),
        tools_completed_at=tools_completed_at,
        completed_at=completed_at,
        **common,
    )


def _claim_initial(execution_id: UUID) -> execution_jobs.AttemptClaim:
    return execution_jobs._claim_attempt(
        execution_id,
        1,
        job_id_for_execution(execution_id, 1),
    )


@pytest.mark.parametrize(
    "durable_state",
    [None, "prepared", "response_received", "tools_completed", "final_response"],
)
def test_safe_durable_states_resume_the_same_attempt(
    session_factory: SessionFactory,
    seeded_suite: SeededDefinition,
    durable_state: str | None,
) -> None:
    execution_id = _create_real_execution(session_factory, seeded_suite)
    initial = _claim_initial(execution_id)
    assert initial.state == "claimed"
    assert initial.attempt_number == 1
    assert initial.delivery_kind == "attempt"

    if durable_state is not None:
        with session_factory.begin() as session:
            session.add(_turn_for_state(execution_id, durable_state))

    original_job_id = job_id_for_execution(execution_id, 1)
    assert recover_interrupted_execution(execution_id, 1, original_job_id) == "recovered"

    with session_factory() as session:
        execution = session.get(CaseExecution, execution_id)
        attempt = session.scalar(
            select(ExecutionAttempt).where(
                ExecutionAttempt.case_execution_id == execution_id,
                ExecutionAttempt.attempt_number == 1,
            )
        )
        resume = session.scalar(
            select(ExecutionDispatch).where(
                ExecutionDispatch.case_execution_id == execution_id,
                ExecutionDispatch.generation == 2,
            )
        )
        assert execution is not None
        assert attempt is not None
        assert resume is not None
        assert execution.status == "retrying"
        assert execution.attempt_count == 1
        assert execution.dispatch_generation == 2
        assert attempt.status == "resume_scheduled"
        assert attempt.recovery_count == 1
        assert attempt.dispatch_generation == 1
        assert attempt.job_id == original_job_id
        assert resume.delivery_kind == "resume"
        assert resume.attempt_number == 1

    resumed = execution_jobs._claim_attempt(
        execution_id,
        2,
        job_id_for_execution(execution_id, 2),
    )
    assert resumed.state == "claimed"
    assert resumed.attempt_number == 1
    assert resumed.dispatch_generation == 2
    assert resumed.delivery_kind == "resume"
    with session_factory() as session:
        execution = session.get(CaseExecution, execution_id)
        attempt = session.scalar(
            select(ExecutionAttempt).where(
                ExecutionAttempt.case_execution_id == execution_id,
                ExecutionAttempt.attempt_number == 1,
            )
        )
        assert execution is not None
        assert attempt is not None
        assert execution.status == "running"
        assert execution.attempt_count == 1
        assert attempt.status == "running"
        assert attempt.dispatch_generation == 1
        assert attempt.job_id == original_job_id


def test_request_started_recovery_is_indeterminate_and_terminal(
    session_factory: SessionFactory,
    seeded_suite: SeededDefinition,
) -> None:
    execution_id = _create_real_execution(session_factory, seeded_suite)
    assert _claim_initial(execution_id).state == "claimed"
    with session_factory.begin() as session:
        session.add(_turn_for_state(execution_id, "request_started"))

    job_id = job_id_for_execution(execution_id, 1)
    assert recover_interrupted_execution(execution_id, 1, job_id) == "failed"
    assert recover_interrupted_execution(execution_id, 1, job_id) == "fresh_or_locked"

    with session_factory() as session:
        execution = session.get(CaseExecution, execution_id)
        attempt = session.scalar(
            select(ExecutionAttempt).where(ExecutionAttempt.case_execution_id == execution_id)
        )
        turn = session.scalar(
            select(ProviderTurn).where(ProviderTurn.case_execution_id == execution_id)
        )
        dispatch_count = session.scalar(
            select(func.count())
            .select_from(ExecutionDispatch)
            .where(ExecutionDispatch.case_execution_id == execution_id)
        )
        assert execution is not None
        assert attempt is not None
        assert turn is not None
        assert execution.status == "failed"
        assert execution.dispatch_generation == 1
        assert execution.last_failure_kind == "provider_outcome_indeterminate"
        assert attempt.status == "failed"
        assert attempt.recovery_count == 0
        assert attempt.failure_kind == "provider_outcome_indeterminate"
        assert turn.status == "indeterminate"
        assert turn.retryable is False
        assert turn.error_kind == "provider_outcome_indeterminate"
        assert dispatch_count == 1


def test_same_attempt_worker_recovery_is_bounded_at_three_resumes(
    session_factory: SessionFactory,
    seeded_suite: SeededDefinition,
) -> None:
    execution_id = _create_real_execution(session_factory, seeded_suite)
    assert _claim_initial(execution_id).state == "claimed"

    for generation in range(1, 4):
        job_id = job_id_for_execution(execution_id, generation)
        assert recover_interrupted_execution(execution_id, generation, job_id) == "recovered"
        resumed = execution_jobs._claim_attempt(
            execution_id,
            generation + 1,
            job_id_for_execution(execution_id, generation + 1),
        )
        assert resumed.state == "claimed"
        assert resumed.attempt_number == 1

    final_job_id = job_id_for_execution(execution_id, 4)
    assert recover_interrupted_execution(execution_id, 4, final_job_id) == "failed"

    with session_factory() as session:
        execution = session.get(CaseExecution, execution_id)
        attempts = list(
            session.scalars(
                select(ExecutionAttempt).where(ExecutionAttempt.case_execution_id == execution_id)
            ).all()
        )
        dispatches = list(
            session.scalars(
                select(ExecutionDispatch)
                .where(ExecutionDispatch.case_execution_id == execution_id)
                .order_by(ExecutionDispatch.generation)
            ).all()
        )
        assert execution is not None
        assert execution.status == "failed"
        assert execution.attempt_count == 1
        assert execution.dispatch_generation == 4
        assert execution.last_failure_kind == "worker_recovery_exhausted"
        assert len(attempts) == 1
        assert attempts[0].status == "failed"
        assert attempts[0].recovery_count == 3
        assert attempts[0].failure_kind == "worker_recovery_exhausted"
        assert [dispatch.generation for dispatch in dispatches] == [1, 2, 3, 4]
        assert [dispatch.delivery_kind for dispatch in dispatches] == [
            "attempt",
            "resume",
            "resume",
            "resume",
        ]


def test_abandoned_preclaim_deliveries_stay_on_one_real_provider_attempt(
    session_factory: SessionFactory,
    seeded_suite: SeededDefinition,
) -> None:
    execution_id = _create_real_execution(session_factory, seeded_suite)
    first_job_id = job_id_for_execution(execution_id, 1)
    assert recover_interrupted_execution(execution_id, 1, first_job_id) == "recovered"
    second_job_id = job_id_for_execution(execution_id, 2)
    assert recover_interrupted_execution(execution_id, 2, second_job_id) == "recovered"

    with session_factory() as session:
        execution = session.get(CaseExecution, execution_id)
        attempts = list(
            session.scalars(
                select(ExecutionAttempt).where(ExecutionAttempt.case_execution_id == execution_id)
            ).all()
        )
        current_dispatch = session.scalar(
            select(ExecutionDispatch).where(
                ExecutionDispatch.case_execution_id == execution_id,
                ExecutionDispatch.generation == 3,
            )
        )
        assert execution is not None
        assert current_dispatch is not None
        assert execution.status == "retrying"
        assert execution.attempt_count == 1
        assert execution.dispatch_generation == 3
        assert len(attempts) == 1
        assert attempts[0].status == "resume_scheduled"
        assert attempts[0].recovery_count == 2
        assert attempts[0].dispatch_generation == 1
        assert attempts[0].job_id == first_job_id
        assert current_dispatch.delivery_kind == "resume"
        assert current_dispatch.attempt_number == 1


def test_resume_delivery_routes_to_real_executor_with_current_generation_fence(
    session_factory: SessionFactory,
    seeded_suite: SeededDefinition,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution_id = _create_real_execution(session_factory, seeded_suite)
    assert _claim_initial(execution_id).state == "claimed"
    assert (
        recover_interrupted_execution(
            execution_id,
            1,
            job_id_for_execution(execution_id, 1),
        )
        == "recovered"
    )
    calls: list[tuple[UUID, int, int]] = []

    def capture_real_execution(
        case_execution_id: UUID,
        *,
        expected_attempt_number: int,
        expected_dispatch_generation: int,
        provider: object | None = None,
    ) -> dict[str, Any]:
        del provider
        calls.append(
            (
                case_execution_id,
                expected_attempt_number,
                expected_dispatch_generation,
            )
        )
        return {"execution_id": str(case_execution_id), "status": "synthetic-real"}

    monkeypatch.setattr(execution_jobs, "execute_openai_case", capture_real_execution)
    result = execution_jobs.run_case_execution(str(execution_id), 2)

    assert result == {"execution_id": str(execution_id), "status": "synthetic-real"}
    assert calls == [(execution_id, 1, 2)]
