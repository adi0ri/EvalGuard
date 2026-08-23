"""The complete 25-case × two-prompt × three-trial Phase 5 workflow."""

from collections import Counter
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from rq import Queue, SimpleWorker
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from evalguard.db.models import (
    CaseExecution,
    Customer,
    EvaluationSuite,
    ExecutionAttempt,
    ExecutionDispatch,
    GraderResult,
    Order,
    Refund,
    SupportTicket,
    ToolInvocation,
    TraceEvent,
)
from evalguard.evaluation.prompts import PROMPT_NAME, PROMPT_V1_VERSION, PROMPT_V2_VERSION
from evalguard.evaluation.suite import PHASE3_SUITE_DEFINITION_HASH

pytestmark = pytest.mark.e2e

PROMPT_V1_HASH = "08724bf556610d9563f27189eb5b298e62a1ce57952885f481c5f8cf93f5ab69"
PROMPT_V2_HASH = "f93d1378329f1156219063cebabfde0cd65e44d08c382541ed600c05140038ad"


def _submit_arm(client: TestClient, prompt_version: str, key: str) -> dict[str, object]:
    response = client.post(
        "/api/v1/experiments",
        json={
            "provider": "deterministic-fake",
            "prompt_name": PROMPT_NAME,
            "prompt_version": prompt_version,
            "trials": 3,
        },
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 202
    return response.json()


def test_two_prompt_three_trial_comparison_produces_150_raw_executions(
    client: TestClient,
    seeded_suite: object,
    redis_connection: Redis,
    rq_queue: Queue,
    session_factory: sessionmaker[Session],
) -> None:
    del seeded_suite
    baseline = _submit_arm(client, PROMPT_V1_VERSION, "phase5-e2e-prompt-v1")
    candidate = _submit_arm(client, PROMPT_V2_VERSION, "phase5-e2e-prompt-v2")
    baseline_id = UUID(str(baseline["id"]))
    candidate_id = UUID(str(candidate["id"]))
    baseline_execution_ids = {UUID(str(value)) for value in baseline["execution_ids"]}
    candidate_execution_ids = {UUID(str(value)) for value in candidate["execution_ids"]}

    assert len(baseline_execution_ids) == 75
    assert len(candidate_execution_ids) == 75
    assert baseline_execution_ids.isdisjoint(candidate_execution_ids)
    assert len(rq_queue.job_ids) == 150

    worker = SimpleWorker([rq_queue], connection=redis_connection)
    assert worker.work(burst=True, logging_level="WARNING", with_scheduler=False) is True

    baseline_detail = client.get(f"/api/v1/experiments/{baseline_id}")
    candidate_detail = client.get(f"/api/v1/experiments/{candidate_id}")
    assert baseline_detail.status_code == 200
    assert candidate_detail.status_code == 200
    for detail, version, prompt_hash in (
        (baseline_detail.json(), PROMPT_V1_VERSION, PROMPT_V1_HASH),
        (candidate_detail.json(), PROMPT_V2_VERSION, PROMPT_V2_HASH),
    ):
        assert detail["status"] == "completed"
        assert detail["trials"] == 3
        assert detail["prompt"] == {
            "name": PROMPT_NAME,
            "version": version,
            "definition_hash": prompt_hash,
        }
        assert len(detail["executions"]) == 75
        assert Counter(row["trial_number"] for row in detail["executions"]) == {
            1: 25,
            2: 25,
            3: 25,
        }
        assert all(row["status"] == "completed" for row in detail["executions"])
        assert all(row["attempt_count"] == 1 for row in detail["executions"])

    summaries = [
        client.get(f"/api/v1/experiments/{experiment_id}/summary")
        for experiment_id in (baseline_id, candidate_id)
    ]
    assert all(response.status_code == 200 for response in summaries)
    for response in summaries:
        summary = response.json()
        assert summary["quality_comparison_ready"] is True
        assert summary["grid_integrity"] == {
            "expected_coordinates": 75,
            "observed_executions": 75,
            "missing_coordinates": 0,
            "unexpected_coordinates": 0,
            "duplicate_coordinates": 0,
            "valid": True,
        }
        assert summary["execution_counts"] == {
            "expected": 75,
            "terminal": 75,
            "completed": 75,
            "infrastructure_failed": 0,
            "nonterminal": 0,
            "graded": 75,
            "passing": 75,
            "criterion_failing": 0,
            "ungraded_terminal": 0,
        }
        assert summary["strict_pass_rate"] == {
            "numerator": 75,
            "denominator": 75,
            "value": 1.0,
        }
        assert summary["criterion_pass_rate"] == {
            "numerator": 1521,
            "denominator": 1521,
            "value": 1.0,
        }
        assert summary["stability_counts"] == {
            "stable_pass": 25,
            "stable_fail": 0,
            "unstable": 0,
            "incomplete": 0,
        }
        assert summary["total_attempts"] == 75
        assert summary["retry_attempts"] == 0
        assert summary["timing"]["run_duration"]["observed_count"] == 75
        assert len(summary["grader_families"]) == 6
        assert len(summary["categories"]) == 5
        assert len(summary["cases"]) == 25

    comparison_response = client.get(f"/api/v1/experiments/{baseline_id}/compare/{candidate_id}")
    assert comparison_response.status_code == 200
    comparison = comparison_response.json()
    assert comparison["quality_comparison_ready"] is True
    assert comparison["strict_pass_rate"]["delta"] == 0.0
    assert comparison["criterion_pass_rate"]["delta"] == 0.0
    assert comparison["stable_case_rate"]["delta"] == 0.0
    assert comparison["total_attempts"] == {
        "baseline": 75.0,
        "candidate": 75.0,
        "delta": 0.0,
    }
    assert all(row["pass_rate_delta"] == 0.0 for row in comparison["grader_families"])
    assert all(row["classification"] == "unchanged" for row in comparison["cases"])

    report = client.get(f"/reports/experiments/{baseline_id}/compare/{candidate_id}")
    assert report.status_code == 200
    assert "Both experiments are complete and fully graded" in report.text
    assert report.text.count('href="/api/v1/executions/') == 150

    all_ids = baseline_execution_ids | candidate_execution_ids
    with session_factory() as session:
        suite = session.get(EvaluationSuite, comparison["baseline"]["suite_id"])
        assert suite is not None
        assert suite.definition_hash == PHASE3_SUITE_DEFINITION_HASH
        assert (
            session.scalar(
                select(func.count()).select_from(CaseExecution).where(CaseExecution.id.in_(all_ids))
            )
            == 150
        )
        assert (
            session.scalar(
                select(func.count(func.distinct(Customer.case_execution_id))).where(
                    Customer.case_execution_id.in_(all_ids)
                )
            )
            == 150
        )
        assert (
            session.scalar(
                select(func.count(func.distinct(Order.case_execution_id))).where(
                    Order.case_execution_id.in_(all_ids)
                )
            )
            == 150
        )
        expected_counts = {
            ExecutionAttempt: 150,
            ExecutionDispatch: 150,
            GraderResult: 3042,
            ToolInvocation: 264,
            TraceEvent: 984,
            Refund: 42,
            SupportTicket: 30,
        }
        for model, expected_count in expected_counts.items():
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.case_execution_id.in_(all_ids))
                )
                == expected_count
            )

        provider_requests = session.scalars(
            select(TraceEvent)
            .where(
                TraceEvent.case_execution_id.in_(all_ids),
                TraceEvent.event_type == "provider_request",
            )
            .order_by(TraceEvent.case_execution_id)
        ).all()
        assert len(provider_requests) == 150
        traced_versions = Counter(
            str(event.payload["agent_prompt"]["version"]) for event in provider_requests
        )
        traced_hashes = Counter(
            str(event.payload["agent_prompt"]["definition_hash"]) for event in provider_requests
        )
        assert traced_versions == {PROMPT_V1_VERSION: 75, PROMPT_V2_VERSION: 75}
        assert traced_hashes == {PROMPT_V1_HASH: 75, PROMPT_V2_HASH: 75}

    replay = _submit_arm(client, PROMPT_V2_VERSION, "phase5-e2e-prompt-v2")
    assert replay["id"] == candidate["id"]
    assert replay["execution_ids"] == candidate["execution_ids"]
    with session_factory() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(CaseExecution).where(CaseExecution.id.in_(all_ids))
            )
            == 150
        )
