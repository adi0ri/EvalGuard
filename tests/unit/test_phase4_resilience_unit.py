"""Focused unit coverage for Phase 4 failure and step-budget semantics."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from rq.timeouts import JobTimeoutException
from sqlalchemy.orm import Session

from evalguard.domain.tool_schemas import LookupOrderArgs, LookupOrderResult
from evalguard.domain.tools import ToolExecutionError, ToolRuntime
from evalguard.evaluation.resilience import (
    FailureKind,
    FaultMode,
    InjectedPermanentError,
    InjectedRetryableError,
    InjectedTimeoutError,
    InjectedWorkerInterruption,
    MaximumToolStepsExceeded,
    StepBudget,
    classify_failure,
)


def test_phase4_migration_preserves_legacy_timeout_and_requeues_retrying_rows() -> None:
    migration_path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "20260822_0007_add_phase4_resilience_state.py"
    )
    spec = importlib.util.spec_from_file_location("phase4_migration_under_test", migration_path)
    assert spec is not None
    assert spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    migration.op = MagicMock()
    migration.op.f.side_effect = lambda name: name

    migration.upgrade()

    timeout_column = next(
        call.args[1]
        for call in migration.op.add_column.call_args_list
        if call.args[0] == "experiments" and call.args[1].name == "job_timeout_seconds"
    )
    assert timeout_column.server_default is not None
    assert str(timeout_column.server_default.arg) == "300"
    executed_sql = "\n".join(str(call.args[0]) for call in migration.op.execute.call_args_list)
    assert "WHERE status IN ('running', 'retrying')" in executed_sql
    assert "WHERE status IN ('queued', 'enqueue_failed')" in executed_sql


@pytest.mark.parametrize(
    ("error", "expected_kind", "expected_retryable"),
    [
        (
            InjectedRetryableError(FaultMode.TRANSIENT_BEFORE_PROVIDER, 1),
            FailureKind.INJECTED_TRANSIENT.value,
            True,
        ),
        (
            InjectedTimeoutError(FaultMode.TIMEOUT, 1),
            FailureKind.TIMEOUT.value,
            True,
        ),
        (JobTimeoutException("synthetic timeout"), FailureKind.TIMEOUT.value, True),
        (
            InjectedWorkerInterruption(attempt_number=1),
            FailureKind.WORKER_INTERRUPTION.value,
            True,
        ),
        (
            InjectedPermanentError(FaultMode.PERMANENT_BEFORE_PROVIDER, 1),
            FailureKind.INJECTED_PERMANENT.value,
            False,
        ),
        (
            MaximumToolStepsExceeded(limit=2, attempted_steps=3),
            FailureKind.MAXIMUM_TOOL_STEPS.value,
            False,
        ),
        (RuntimeError("unknown"), FailureKind.UNEXPECTED.value, False),
    ],
)
def test_failure_classifier_is_conservative_and_stable(
    error: BaseException,
    expected_kind: str,
    expected_retryable: bool,
) -> None:
    classification = classify_failure(error)

    assert classification.kind == expected_kind
    assert classification.retryable is expected_retryable


def test_step_budget_allows_exact_limit_and_rejects_n_plus_one() -> None:
    budget = StepBudget(limit=3)

    assert [budget.consume(), budget.consume(), budget.consume()] == [1, 2, 3]
    assert budget.attempted_steps == 3

    with pytest.raises(MaximumToolStepsExceeded) as raised:
        budget.consume()

    assert raised.value.limit == 3
    assert raised.value.attempted_steps == 4
    assert budget.attempted_steps == 4


def test_tool_runtime_counts_cached_replays_and_errors_before_n_plus_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ToolRuntime(MagicMock(spec=Session), uuid4(), max_tool_steps=2)
    arguments = LookupOrderArgs(order_id="PD-STEP-BUDGET")
    replayed = LookupOrderResult(
        order_id=arguments.order_id,
        customer_id="CUST-STEP-BUDGET",
        status="delivered",
        total_cents=4599,
        currency="USD",
    )

    monkeypatch.setattr(runtime, "_lock_execution", lambda: None)

    def replay_or_error(tool_call_id: str, *_: object) -> LookupOrderResult:
        if tool_call_id == "cached-success":
            return replayed
        raise ToolExecutionError("persisted synthetic tool error")

    monkeypatch.setattr(runtime, "_replay_cached", replay_or_error)

    assert runtime.lookup_order(arguments, tool_call_id="cached-success") == replayed
    assert runtime.steps_attempted == 1

    with pytest.raises(ToolExecutionError, match="persisted synthetic tool error"):
        runtime.lookup_order(arguments, tool_call_id="cached-error")
    assert runtime.steps_attempted == 2

    with pytest.raises(MaximumToolStepsExceeded) as raised:
        runtime.lookup_order(arguments, tool_call_id="never-reaches-replay")
    assert raised.value.limit == 2
    assert raised.value.attempted_steps == 3
    assert runtime.steps_attempted == 3
