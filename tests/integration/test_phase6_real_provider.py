"""PostgreSQL-backed Phase 6 provider orchestration and API/worker coverage."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

import evalguard.evaluation.real_executor as real_executor
from evalguard.core.config import get_settings
from evalguard.db.models import (
    CaseExecution,
    EvaluationCase,
    ExecutionAttempt,
    Experiment,
    ProviderTurn,
    Refund,
    ToolInvocation,
    TraceEvent,
)
from evalguard.evaluation.prompts import DEFAULT_PROMPT_VERSION
from evalguard.evaluation.real_executor import (
    ProviderOutcomeIndeterminateError,
    ProviderResponseInvalidError,
    execute_openai_case,
)
from evalguard.providers.configuration import (
    build_openai_provider_config,
    provider_config_hash,
)
from evalguard.providers.openai_responses import (
    OpenAIProviderConfigurationError,
    OpenAIResponsesProvider,
)
from evalguard.workers.dispatch import create_dispatch
from evalguard.workers.jobs import recover_interrupted_execution, run_case_execution
from evalguard.workers.queue import job_id_for_execution

pytestmark = pytest.mark.integration


class DynamicResponses:
    def __init__(
        self,
        outcome: Callable[[int, dict[str, Any]], Any],
        lock_probe: Callable[[], None] | None = None,
    ) -> None:
        self._outcome = outcome
        self._lock_probe = lock_probe
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        if self._lock_probe is not None:
            self._lock_probe()
        captured = deepcopy(kwargs)
        self.calls.append(captured)
        return self._outcome(len(self.calls), captured)


class DynamicClient:
    def __init__(
        self,
        outcome: Callable[[int, dict[str, Any]], Any],
        lock_probe: Callable[[], None] | None = None,
    ) -> None:
        self.max_retries = 0
        self.responses = DynamicResponses(outcome, lock_probe)


def _usage() -> dict[str, Any]:
    return {
        "input_tokens": 100,
        "input_tokens_details": {"cached_tokens": 10},
        "output_tokens": 20,
        "output_tokens_details": {"reasoning_tokens": 2},
        "total_tokens": 120,
    }


def _response(
    number: int,
    output: list[dict[str, Any]],
    *,
    status: str = "completed",
) -> dict[str, Any]:
    return {
        "id": f"resp_phase6_{number}",
        "_request_id": f"req_phase6_{number}",
        "model": "gpt-5.6-luna-2026-08-01",
        "status": status,
        "output": output,
        "usage": _usage(),
        "error": None,
        "incomplete_details": None,
    }


def _function_response(
    number: int,
    *,
    call_id: str,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    return _response(
        number,
        [
            {
                "type": "function_call",
                "id": f"fc_phase6_{number}",
                "call_id": call_id,
                "name": name,
                "arguments": json.dumps(arguments, sort_keys=True, separators=(",", ":")),
                "status": "completed",
            }
        ],
    )


def _refund_script(number: int, request: dict[str, Any]) -> dict[str, Any]:
    if number == 1:
        return _function_response(
            number,
            call_id="call_phase6_lookup",
            name="lookup_order",
            arguments={"order_id": "PD-1001"},
        )
    if number == 2:
        return _function_response(
            number,
            call_id="call_phase6_refund",
            name="issue_refund",
            arguments={
                "order_id": "PD-1001",
                "amount_cents": 4599,
                "reason": "Synthetic damaged item refund",
            },
        )
    if number != 3:
        raise AssertionError("The refund script permits exactly three provider turns")
    outputs = [item for item in request["input"] if item.get("type") == "function_call_output"]
    refund_output = json.loads(outputs[-1]["output"])
    refund = refund_output["result"]
    final = {
        "status": "resolved",
        "action": "refund_issued",
        "message": "The eligible synthetic refund was issued.",
        "order_id": refund["order_id"],
        "refund_id": refund["refund_id"],
        "amount_cents": refund["amount_cents"],
        "currency": refund["currency"],
    }
    return _response(
        number,
        [
            {
                "type": "message",
                "id": "msg_phase6_final",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(final, sort_keys=True, separators=(",", ":")),
                        "annotations": [],
                    }
                ],
            }
        ],
    )


def _provider(
    outcome: Callable[[int, dict[str, Any]], Any],
    lock_probe: Callable[[], None] | None = None,
) -> tuple[OpenAIResponsesProvider, DynamicClient]:
    client = DynamicClient(outcome, lock_probe)
    return (
        OpenAIResponsesProvider(
            client=client,
            reasoning_effort="none",
            max_output_tokens=1200,
            timeout_seconds=45.0,
        ),
        client,
    )


def _claimed_execution(
    session_factory: sessionmaker[Session],
    seeded_suite: Any,
    *,
    provider_config: dict[str, Any] | None = None,
) -> UUID:
    config = provider_config or build_openai_provider_config(get_settings())
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        experiment = Experiment(
            suite_id=seeded_suite.suite_id,
            agent_prompt_id=seeded_suite.prompt_id_for(DEFAULT_PROMPT_VERSION),
            provider="openai-responses",
            provider_model=str(config["model"]),
            provider_config=config,
            provider_config_hash=provider_config_hash(config),
            status="running",
            idempotency_key=f"phase6-{uuid4()}",
            trials=1,
            max_attempts=1,
            job_timeout_seconds=300,
            max_tool_steps=8,
        )
        session.add(experiment)
        session.flush()
        execution = CaseExecution(
            experiment_id=experiment.id,
            case_id=seeded_suite.case_id,
            trial_number=1,
            status="running",
            attempt_count=1,
            dispatch_generation=1,
            started_at=now,
            lease_expires_at=now + timedelta(seconds=302),
        )
        session.add(execution)
        session.flush()
        session.add(
            ExecutionAttempt(
                case_execution_id=execution.id,
                attempt_number=1,
                dispatch_generation=1,
                job_id=job_id_for_execution(execution.id, 1),
                status="running",
                step_count=0,
                recovery_count=0,
                started_at=now,
                lease_expires_at=now + timedelta(seconds=302),
            )
        )
        create_dispatch(session, execution.id, generation=1)
        execution_id = execution.id
    return execution_id


def test_real_provider_submission_is_disabled_by_default(
    client: TestClient,
    seeded_suite: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del seeded_suite
    monkeypatch.setattr(get_settings(), "real_provider_enabled", False)

    response = client.post(
        "/api/v1/experiments",
        headers={"Idempotency-Key": "phase6-disabled"},
        json={
            "suite_name": "parceldesk-refunds",
            "suite_version": "1.0.0",
            "provider": "openai-responses",
            "trials": 1,
            "max_attempts": 1,
            "job_timeout_seconds": 300,
        },
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "The bounded real-provider track is disabled"}


def test_real_provider_api_worker_flow_records_usage_and_keeps_state_isolated(
    client: TestClient,
    session_factory: sessionmaker[Session],
    seeded_suite: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "real_provider_enabled", True)

    request_payload = {
        "suite_name": "parceldesk-refunds",
        "suite_version": "2.0.0",
        "provider": "openai-responses",
        "trials": 1,
        "max_attempts": 1,
        "job_timeout_seconds": 300,
        "max_tool_steps": 8,
    }
    first = client.post(
        "/api/v1/experiments",
        headers={"Idempotency-Key": "phase6-api-worker-1"},
        json=request_payload,
    )
    replay = client.post(
        "/api/v1/experiments",
        headers={"Idempotency-Key": "phase6-api-worker-1"},
        json=request_payload,
    )
    conflict = client.post(
        "/api/v1/experiments",
        headers={"Idempotency-Key": "phase6-api-worker-2"},
        json=request_payload,
    )
    missing_key = client.post("/api/v1/experiments", json=request_payload)
    assert first.status_code == replay.status_code == 202
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    assert missing_key.status_code == 422

    experiment_id = UUID(first.json()["id"])
    with session_factory() as session:
        execution_ids = dict(
            session.execute(
                select(EvaluationCase.key, CaseExecution.id)
                .join(CaseExecution, CaseExecution.case_id == EvaluationCase.id)
                .where(CaseExecution.experiment_id == experiment_id)
            ).all()
        )
    first_execution_id = execution_ids["eligible-refund"]
    isolated_execution_id = next(
        execution_id
        for case_key, execution_id in execution_ids.items()
        if case_key != "eligible-refund"
    )

    def assert_network_has_no_execution_lock() -> None:
        with session_factory.begin() as probe:
            locked = probe.scalar(
                select(CaseExecution)
                .where(CaseExecution.id == first_execution_id)
                .with_for_update(nowait=True)
            )
            assert locked is not None

    provider, scripted_client = _provider(
        _refund_script,
        lock_probe=assert_network_has_no_execution_lock,
    )
    monkeypatch.setattr(real_executor, "_safe_provider", lambda _: provider)

    result = run_case_execution(str(first_execution_id), dispatch_generation=1)

    assert result == {
        "execution_id": str(first_execution_id),
        "status": "completed",
        "passed": True,
        "replayed": False,
    }
    assert len(scripted_client.responses.calls) == 3
    detail = client.get(f"/api/v1/executions/{first_execution_id}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["provider"] == "openai-responses"
    assert payload["provider_model"] == "gpt-5.6-luna"
    assert [turn["status"] for turn in payload["provider_turns"]] == [
        "tools_completed",
        "tools_completed",
        "final_response",
    ]
    assert all(turn["request_payload"]["store"] is False for turn in payload["provider_turns"])
    assert payload["attempts"][0]["step_count"] == 2

    summary = client.get(f"/api/v1/experiments/{experiment_id}/summary")
    assert summary.status_code == 200
    summary_payload = summary.json()
    assert summary_payload["result_label"] == (
        "real-provider evaluation on synthetic ParcelDesk cases"
    )
    assert summary_payload["token_usage_available"] is True
    assert summary_payload["cost_available"] is True
    assert summary_payload["provider_usage"] == {
        "request_count": 3,
        "response_count": 3,
        "input_tokens": 300,
        "cached_input_tokens": 30,
        "output_tokens": 60,
        "reasoning_tokens": 6,
        "total_tokens": 360,
        "estimated_cost_usd": "0.000129",
        "returned_models": ["gpt-5.6-luna-2026-08-01"],
        "latency": summary_payload["provider_usage"]["latency"],
    }

    with session_factory() as session:
        first_refunds = session.scalars(
            select(Refund).where(Refund.case_execution_id == first_execution_id)
        ).all()
        isolated_refunds = session.scalars(
            select(Refund).where(Refund.case_execution_id == isolated_execution_id)
        ).all()
        assert len(first_refunds) == 1
        assert isolated_refunds == []


@pytest.mark.parametrize(
    "durable_state",
    ["prepared", "response_received", "final_response"],
)
def test_same_attempt_resume_continues_from_the_durable_provider_prefix(
    session_factory: sessionmaker[Session],
    seeded_legacy_suite: Any,
    monkeypatch: pytest.MonkeyPatch,
    durable_state: str,
) -> None:
    execution_id = _claimed_execution(session_factory, seeded_legacy_suite)
    provider, client = _provider(_refund_script)

    def prepare_turn() -> ProviderTurn:
        return real_executor._prepare_turn(
            session_factory,
            execution_id,
            expected_attempt_number=1,
            expected_dispatch_generation=1,
            provider=provider,
        )

    def receive_turn(*, execute_tool: bool) -> str:
        turn = prepare_turn()
        request = real_executor._start_turn(
            session_factory,
            execution_id,
            turn.id,
            expected_attempt_number=1,
            expected_dispatch_generation=1,
            provider=provider,
        )
        response = provider.send_prepared(request)
        status = real_executor._persist_response(
            session_factory,
            execution_id,
            turn.id,
            response,
            latency_ms=0,
            expected_attempt_number=1,
            expected_dispatch_generation=1,
        )
        if execute_tool:
            assert status == "response_received"
            real_executor._execute_next_tool(
                session_factory,
                execution_id,
                turn.id,
                expected_attempt_number=1,
                expected_dispatch_generation=1,
            )
            return "tools_completed"
        return status

    if durable_state == "prepared":
        assert prepare_turn().status == "prepared"
    elif durable_state == "response_received":
        assert receive_turn(execute_tool=False) == "response_received"
    else:
        assert receive_turn(execute_tool=True) == "tools_completed"
        assert receive_turn(execute_tool=True) == "tools_completed"
        assert receive_turn(execute_tool=False) == "final_response"

    assert (
        recover_interrupted_execution(
            execution_id,
            1,
            job_id_for_execution(execution_id, 1),
        )
        == "recovered"
    )
    monkeypatch.setattr(real_executor, "_safe_provider", lambda _: provider)

    result = run_case_execution(str(execution_id), dispatch_generation=2)

    assert result == {
        "execution_id": str(execution_id),
        "status": "completed",
        "passed": True,
        "replayed": False,
    }
    assert len(client.responses.calls) == 3
    with session_factory() as session:
        execution = session.get(CaseExecution, execution_id)
        attempt = session.scalar(
            select(ExecutionAttempt).where(
                ExecutionAttempt.case_execution_id == execution_id,
                ExecutionAttempt.attempt_number == 1,
            )
        )
        turns = session.scalars(
            select(ProviderTurn)
            .where(ProviderTurn.case_execution_id == execution_id)
            .order_by(ProviderTurn.sequence_number)
        ).all()
        refunds = session.scalars(
            select(Refund).where(Refund.case_execution_id == execution_id)
        ).all()
        assert execution is not None
        assert attempt is not None
        assert execution.status == "completed"
        assert execution.attempt_count == 1
        assert execution.dispatch_generation == 2
        assert attempt.status == "succeeded"
        assert attempt.recovery_count == 1
        assert [turn.status for turn in turns] == [
            "tools_completed",
            "tools_completed",
            "final_response",
        ]
        assert all(turn.dispatch_generation == 1 for turn in turns)
        assert len(refunds) == 1


def test_request_unknown_outcome_is_never_sent_twice(
    session_factory: sessionmaker[Session],
    seeded_legacy_suite: Any,
) -> None:
    execution_id = _claimed_execution(session_factory, seeded_legacy_suite)

    def fail_after_send(_: int, __: dict[str, Any]) -> Any:
        raise RuntimeError("unsafe transport detail that must be redacted")

    provider, client = _provider(fail_after_send)
    with pytest.raises(ProviderOutcomeIndeterminateError):
        execute_openai_case(
            execution_id,
            expected_attempt_number=1,
            expected_dispatch_generation=1,
            provider=provider,
            session_factory=session_factory,
        )
    with pytest.raises(ProviderResponseInvalidError):
        execute_openai_case(
            execution_id,
            expected_attempt_number=1,
            expected_dispatch_generation=1,
            provider=provider,
            session_factory=session_factory,
        )

    assert len(client.responses.calls) == 1
    with session_factory() as session:
        turn = session.scalar(
            select(ProviderTurn).where(ProviderTurn.case_execution_id == execution_id)
        )
        assert turn is not None
        assert turn.status == "indeterminate"
        assert turn.retryable is False
        assert "unsafe transport detail" not in str(turn.safe_error_message)


def test_late_response_is_superseded_evidence_without_business_effects(
    session_factory: sessionmaker[Session],
    seeded_legacy_suite: Any,
) -> None:
    execution_id = _claimed_execution(session_factory, seeded_legacy_suite)

    def late_refund_call(number: int, _: dict[str, Any]) -> dict[str, Any]:
        assert number == 1
        return _function_response(
            number,
            call_id="call_phase6_late_refund",
            name="issue_refund",
            arguments={
                "order_id": "PD-1001",
                "amount_cents": 4599,
                "reason": "Synthetic late response that must not execute",
            },
        )

    provider, client = _provider(late_refund_call)
    turn = real_executor._prepare_turn(
        session_factory,
        execution_id,
        expected_attempt_number=1,
        expected_dispatch_generation=1,
        provider=provider,
    )
    request = real_executor._start_turn(
        session_factory,
        execution_id,
        turn.id,
        expected_attempt_number=1,
        expected_dispatch_generation=1,
        provider=provider,
    )
    with session_factory() as session:
        started_turn = session.get(ProviderTurn, turn.id)
        assert started_turn is not None
        request_identity = {
            "client_request_id": started_turn.client_request_id,
            "request_hash": started_turn.request_hash,
            "request_payload": deepcopy(started_turn.request_payload),
            "prepared_at": started_turn.prepared_at,
            "request_started_at": started_turn.request_started_at,
        }

    assert (
        recover_interrupted_execution(
            execution_id,
            1,
            job_id_for_execution(execution_id, 1),
        )
        == "failed"
    )
    with session_factory() as session:
        indeterminate_turn = session.get(ProviderTurn, turn.id)
        assert indeterminate_turn is not None
        assert indeterminate_turn.status == "indeterminate"
        terminal_evidence = {
            "completed_at": indeterminate_turn.completed_at,
            "retryable": indeterminate_turn.retryable,
            "error_kind": indeterminate_turn.error_kind,
            "safe_error_message": indeterminate_turn.safe_error_message,
        }

    late_response = provider.send_prepared(request)
    assert (
        real_executor._persist_response(
            session_factory,
            execution_id,
            turn.id,
            late_response,
            latency_ms=17,
            expected_attempt_number=1,
            expected_dispatch_generation=1,
        )
        == "superseded"
    )

    conflicting_response = replace(
        late_response,
        request_id="req_phase6_conflicting",
        response_id="resp_phase6_conflicting",
    )
    assert (
        real_executor._persist_response(
            session_factory,
            execution_id,
            turn.id,
            conflicting_response,
            latency_ms=999,
            expected_attempt_number=1,
            expected_dispatch_generation=1,
        )
        == "superseded"
    )
    assert len(client.responses.calls) == 1

    with session_factory() as session:
        execution = session.get(CaseExecution, execution_id)
        attempt = session.scalar(
            select(ExecutionAttempt).where(
                ExecutionAttempt.case_execution_id == execution_id,
                ExecutionAttempt.attempt_number == 1,
            )
        )
        persisted_turn = session.get(ProviderTurn, turn.id)
        refunds = session.scalars(
            select(Refund).where(Refund.case_execution_id == execution_id)
        ).all()
        invocations = session.scalars(
            select(ToolInvocation).where(ToolInvocation.case_execution_id == execution_id)
        ).all()
        traces = session.scalars(
            select(TraceEvent)
            .where(TraceEvent.case_execution_id == execution_id)
            .order_by(TraceEvent.sequence_number)
        ).all()

        assert execution is not None
        assert attempt is not None
        assert persisted_turn is not None
        assert execution.status == "failed"
        assert execution.final_resolution is None
        assert attempt.status == "failed"
        assert attempt.step_count == 0
        assert persisted_turn.status == "superseded"
        assert persisted_turn.provider_request_id == "req_phase6_1"
        assert persisted_turn.provider_response_id == "resp_phase6_1"
        assert persisted_turn.response_model == "gpt-5.6-luna-2026-08-01"
        assert persisted_turn.response_payload is not None
        assert persisted_turn.response_payload["response_id"] == "resp_phase6_1"
        assert persisted_turn.tool_calls == [
            {
                "call_id": "call_phase6_late_refund",
                "name": "issue_refund",
                "arguments": {
                    "order_id": "PD-1001",
                    "amount_cents": 4599,
                    "reason": "Synthetic late response that must not execute",
                },
            }
        ]
        assert persisted_turn.tool_outputs == []
        assert persisted_turn.input_tokens == 100
        assert persisted_turn.cached_input_tokens == 10
        assert persisted_turn.output_tokens == 20
        assert persisted_turn.reasoning_tokens == 2
        assert persisted_turn.total_tokens == 120
        assert persisted_turn.estimated_cost_usd_micros == 43
        assert persisted_turn.latency_ms == 17
        assert {
            "client_request_id": persisted_turn.client_request_id,
            "request_hash": persisted_turn.request_hash,
            "request_payload": persisted_turn.request_payload,
            "prepared_at": persisted_turn.prepared_at,
            "request_started_at": persisted_turn.request_started_at,
        } == request_identity
        assert {
            "completed_at": persisted_turn.completed_at,
            "retryable": persisted_turn.retryable,
            "error_kind": persisted_turn.error_kind,
            "safe_error_message": persisted_turn.safe_error_message,
        } == terminal_evidence
        assert refunds == []
        assert invocations == []
        assert not {trace.event_type for trace in traces}.intersection(
            {"tool_call", "tool_result", "tool_error", "tool_replay"}
        )
        superseded_traces = [
            trace for trace in traces if trace.event_type == "provider_response_superseded"
        ]
        assert len(superseded_traces) == 1
        assert superseded_traces[0].payload["late_after_indeterminate"] is True
        assert superseded_traces[0].payload["business_effects_applied"] is False
        assert superseded_traces[0].payload["provider_response_id"] == "resp_phase6_1"


def test_malformed_purchased_response_keeps_usage_and_safe_evidence(
    session_factory: sessionmaker[Session],
    seeded_legacy_suite: Any,
) -> None:
    config = deepcopy(build_openai_provider_config(get_settings()))
    config["pricing"].update(
        {
            "input_usd": "2.00",
            "cached_input_usd": "0.20",
            "output_usd": "12.00",
        }
    )
    execution_id = _claimed_execution(
        session_factory,
        seeded_legacy_suite,
        provider_config=config,
    )

    def malformed(_: int, __: dict[str, Any]) -> dict[str, Any]:
        return _response(
            1,
            [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "not-json private@example.test",
                        }
                    ],
                }
            ],
        )

    provider, client = _provider(malformed)
    with pytest.raises(ProviderResponseInvalidError):
        execute_openai_case(
            execution_id,
            expected_attempt_number=1,
            expected_dispatch_generation=1,
            provider=provider,
            session_factory=session_factory,
        )

    assert len(client.responses.calls) == 1
    with session_factory() as session:
        turn = session.scalar(
            select(ProviderTurn).where(ProviderTurn.case_execution_id == execution_id)
        )
        assert turn is not None
        assert turn.status == "response_invalid"
        assert turn.provider_response_id == "resp_phase6_1"
        assert turn.response_model == "gpt-5.6-luna-2026-08-01"
        assert turn.input_tokens == 100
        assert turn.output_tokens == 20
        # 90 uncached input * $2/M + 10 cached * $0.20/M +
        # 20 output * $12/M = 422 microdollars from the persisted snapshot.
        assert turn.estimated_cost_usd_micros == 422
        assert "private@example.test" not in str(turn.safe_error_message)


def test_relationally_invalid_usage_keeps_response_without_ledger_rollback(
    session_factory: sessionmaker[Session],
    seeded_legacy_suite: Any,
) -> None:
    execution_id = _claimed_execution(session_factory, seeded_legacy_suite)

    def invalid_usage(_: int, __: dict[str, Any]) -> dict[str, Any]:
        response = _response(1, [])
        response["usage"] = {
            "input_tokens": 10,
            "output_tokens": 4,
            "total_tokens": 13,
        }
        return response

    provider, client = _provider(invalid_usage)
    with pytest.raises(ProviderResponseInvalidError):
        execute_openai_case(
            execution_id,
            expected_attempt_number=1,
            expected_dispatch_generation=1,
            provider=provider,
            session_factory=session_factory,
        )

    assert len(client.responses.calls) == 1
    with session_factory() as session:
        turn = session.scalar(
            select(ProviderTurn).where(ProviderTurn.case_execution_id == execution_id)
        )
        assert turn is not None
        assert turn.status == "response_invalid"
        assert turn.response_payload is not None
        assert turn.response_payload["usage"]["total_tokens"] == 13
        assert turn.input_tokens is None
        assert turn.estimated_cost_usd_micros is None


def test_unapproved_returned_model_is_evidence_but_not_priced_or_executed(
    session_factory: sessionmaker[Session],
    seeded_legacy_suite: Any,
) -> None:
    execution_id = _claimed_execution(session_factory, seeded_legacy_suite)

    def mismatched_model(_: int, __: dict[str, Any]) -> dict[str, Any]:
        response = _function_response(
            1,
            call_id="call_model_mismatch",
            name="lookup_order",
            arguments={"order_id": "PD-1001"},
        )
        response["model"] = "gpt-4.1-2026-08-01"
        return response

    provider, client = _provider(mismatched_model)
    with pytest.raises(ProviderResponseInvalidError):
        execute_openai_case(
            execution_id,
            expected_attempt_number=1,
            expected_dispatch_generation=1,
            provider=provider,
            session_factory=session_factory,
        )

    assert len(client.responses.calls) == 1
    with session_factory() as session:
        turn = session.scalar(
            select(ProviderTurn).where(ProviderTurn.case_execution_id == execution_id)
        )
        assert turn is not None
        assert turn.status == "response_invalid"
        assert turn.response_model == "gpt-4.1-2026-08-01"
        assert turn.input_tokens == 100
        assert turn.estimated_cost_usd_micros is None
        assert (
            session.scalar(select(Refund).where(Refund.case_execution_id == execution_id)) is None
        )


def test_local_request_validation_precedes_the_network_send_fence(
    session_factory: sessionmaker[Session],
    seeded_legacy_suite: Any,
) -> None:
    execution_id = _claimed_execution(session_factory, seeded_legacy_suite)

    def must_not_send(_: int, __: dict[str, Any]) -> Any:
        raise AssertionError("The invalid prepared request must never reach the client")

    client = DynamicClient(must_not_send)

    class InvalidPreparedProvider(OpenAIResponsesProvider):
        def prepare_request(self, **kwargs: Any) -> dict[str, Any]:
            request = super().prepare_request(**kwargs)
            request["store"] = True
            return request

    provider = InvalidPreparedProvider(
        client=client,
        reasoning_effort="none",
        max_output_tokens=1200,
        timeout_seconds=45.0,
    )

    with pytest.raises(OpenAIProviderConfigurationError):
        execute_openai_case(
            execution_id,
            expected_attempt_number=1,
            expected_dispatch_generation=1,
            provider=provider,
            session_factory=session_factory,
        )

    assert client.responses.calls == []
    with session_factory() as session:
        turn = session.scalar(
            select(ProviderTurn).where(ProviderTurn.case_execution_id == execution_id)
        )
        assert turn is not None
        assert turn.status == "prepared"
        assert turn.request_started_at is None
