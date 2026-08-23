"""PostgreSQL-backed summary, comparison, and report behavior."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from rq import Queue
from sqlalchemy import delete
from sqlalchemy.orm import Session

from evalguard.db.models import (
    CaseExecution,
    ExecutionFaultPlan,
    Experiment,
    GraderResult,
    TraceEvent,
)
from evalguard.evaluation.analysis import (
    ExperimentComparisonConflictError,
    analyze_experiment,
    compare_experiments,
)
from evalguard.evaluation.prompts import PROMPT_NAME, PROMPT_V1_VERSION, PROMPT_V2_VERSION

pytestmark = pytest.mark.integration

PROMPT_V1_HASH = "08724bf556610d9563f27189eb5b298e62a1ce57952885f481c5f8cf93f5ab69"
PROMPT_V2_HASH = "f93d1378329f1156219063cebabfde0cd65e44d08c382541ed600c05140038ad"


def _submit(
    client: TestClient,
    *,
    prompt_version: str,
    trials: int,
    key: str,
) -> dict[str, object]:
    response = client.post(
        "/api/v1/experiments",
        json={
            "provider": "deterministic-fake",
            "prompt_name": PROMPT_NAME,
            "prompt_version": prompt_version,
            "trials": trials,
        },
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 202
    return response.json()


def test_running_summaries_compare_provisionally_and_render_raw_links(
    client: TestClient,
    seeded_suite: object,
    db_session: Session,
    rq_queue: Queue,
) -> None:
    definitions = seeded_suite
    baseline = _submit(
        client,
        prompt_version=PROMPT_V1_VERSION,
        trials=3,
        key="phase5-provisional-baseline",
    )
    candidate = _submit(
        client,
        prompt_version=PROMPT_V2_VERSION,
        trials=3,
        key="phase5-provisional-candidate",
    )
    assert len(rq_queue.job_ids) == 150

    baseline_id = baseline["id"]
    candidate_id = candidate["id"]
    summary_response = client.get(f"/api/v1/experiments/{baseline_id}/summary")
    assert summary_response.status_code == 200
    summary = summary_response.json()
    assert summary["prompt"] == {
        "name": PROMPT_NAME,
        "version": PROMPT_V1_VERSION,
        "definition_hash": PROMPT_V1_HASH,
    }
    assert summary["execution_counts"] == {
        "expected": 75,
        "terminal": 0,
        "completed": 0,
        "infrastructure_failed": 0,
        "nonterminal": 75,
        "graded": 0,
        "passing": 0,
        "criterion_failing": 0,
        "ungraded_terminal": 0,
    }
    assert summary["strict_pass_rate"] == {
        "numerator": 0,
        "denominator": 75,
        "value": 0.0,
    }
    assert summary["grading_coverage"]["value"] == 0.0
    assert summary["stability_counts"] == {
        "stable_pass": 0,
        "stable_fail": 0,
        "unstable": 0,
        "incomplete": 25,
    }
    assert summary["quality_comparison_ready"] is False
    assert summary["token_usage_available"] is False
    assert summary["cost_available"] is False
    assert summary["fault_injection_present"] is False
    assert summary["grid_integrity"] == {
        "expected_coordinates": 75,
        "observed_executions": 75,
        "missing_coordinates": 0,
        "unexpected_coordinates": 0,
        "duplicate_coordinates": 0,
        "valid": True,
    }

    comparison_response = client.get(f"/api/v1/experiments/{baseline_id}/compare/{candidate_id}")
    assert comparison_response.status_code == 200
    comparison = comparison_response.json()
    assert comparison["quality_comparison_ready"] is False
    assert comparison["strict_pass_rate"] == {
        "baseline": 0.0,
        "candidate": 0.0,
        "delta": None,
    }
    assert comparison["mean_run_duration_ms"] == {
        "baseline": None,
        "candidate": None,
        "delta": None,
    }
    assert comparison["total_attempts"] == {
        "baseline": None,
        "candidate": None,
        "delta": None,
    }
    assert comparison["baseline"]["prompt"]["definition_hash"] == PROMPT_V1_HASH
    assert comparison["candidate"]["prompt"]["definition_hash"] == PROMPT_V2_HASH
    assert len(comparison["cases"]) == 25
    assert all(row["classification"] == "provisional" for row in comparison["cases"])

    report_response = client.get(f"/reports/experiments/{baseline_id}/compare/{candidate_id}")
    assert report_response.status_code == 200
    assert report_response.headers["content-type"].startswith("text/html")
    assert "Synthetic infrastructure validation" in report_response.text
    assert "Comparison is provisional" in report_response.text
    assert PROMPT_V1_HASH in report_response.text
    assert PROMPT_V2_HASH in report_response.text
    assert report_response.text.count('href="/api/v1/executions/') == 150

    incompatible = _submit(
        client,
        prompt_version=PROMPT_V2_VERSION,
        trials=1,
        key="phase5-incompatible-candidate",
    )
    conflict = client.get(f"/api/v1/experiments/{baseline_id}/compare/{incompatible['id']}")
    same_run = client.get(f"/api/v1/experiments/{baseline_id}/compare/{baseline_id}")
    assert conflict.status_code == 409
    assert "trials" in conflict.json()["detail"]
    assert same_run.status_code == 409

    same_prompt = _submit(
        client,
        prompt_version=PROMPT_V1_VERSION,
        trials=3,
        key="phase5-same-prompt-candidate",
    )
    same_prompt_conflict = client.get(
        f"/api/v1/experiments/{baseline_id}/compare/{same_prompt['id']}"
    )
    assert same_prompt_conflict.status_code == 409
    assert "distinct prompt versions and hashes" in same_prompt_conflict.json()["detail"]

    null_prompt = Experiment(
        suite_id=definitions.suite_id,  # type: ignore[attr-defined]
        agent_prompt_id=None,
        provider="deterministic-fake",
        status="queued",
        trials=3,
    )
    db_session.add(null_prompt)
    db_session.flush()
    db_session.add_all(
        [
            CaseExecution(
                experiment_id=null_prompt.id,
                case_id=case_id,
                trial_number=trial_number,
                status="queued",
            )
            for case_id in definitions.case_ids_by_key.values()  # type: ignore[attr-defined]
            for trial_number in range(1, 4)
        ]
    )
    db_session.commit()
    null_prompt_conflict = client.get(f"/api/v1/experiments/{baseline_id}/compare/{null_prompt.id}")
    assert null_prompt_conflict.status_code == 409
    assert "published prompt identities" in null_prompt_conflict.json()["detail"]

    candidate_execution_id = UUID(str(candidate["execution_ids"][0]))
    db_session.add(
        ExecutionFaultPlan(
            case_execution_id=candidate_execution_id,
            mode="transient_before_provider",
            fail_attempts=1,
        )
    )
    db_session.commit()
    fault_conflict = client.get(f"/api/v1/experiments/{baseline_id}/compare/{candidate_id}")
    assert fault_conflict.status_code == 409
    assert "Fault-injected" in fault_conflict.json()["detail"]

    unknown = uuid4()
    assert client.get(f"/api/v1/experiments/{unknown}/summary").status_code == 404
    assert client.get(f"/reports/experiments/{unknown}/compare/{candidate_id}").status_code == 404


def test_stability_compares_criterion_vectors_and_ungraded_completion_never_passes(
    db_session: Session,
    seeded_suite: object,
) -> None:
    definitions = seeded_suite
    now = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
    experiment = Experiment(
        suite_id=definitions.suite_id,  # type: ignore[attr-defined]
        agent_prompt_id=definitions.prompt_id_for(PROMPT_V1_VERSION),  # type: ignore[attr-defined]
        provider="deterministic-fake",
        status="completed",
        trials=3,
        completed_at=now + timedelta(seconds=3),
    )
    db_session.add(experiment)
    db_session.flush()
    executions: list[CaseExecution] = []
    for trial in (1, 2, 3):
        execution = CaseExecution(
            experiment_id=experiment.id,
            case_id=definitions.case_id,  # type: ignore[attr-defined]
            trial_number=trial,
            status="completed",
            created_at=now,
            started_at=now + timedelta(milliseconds=trial * 10),
            completed_at=now + timedelta(milliseconds=100 + trial * 10),
        )
        db_session.add(execution)
        executions.append(execution)
    db_session.flush()

    vectors = ((True, False), (False, True), (True, False))
    for execution, vector in zip(executions, vectors, strict=True):
        for criterion, passed in zip(("alpha", "beta"), vector, strict=True):
            db_session.add(
                GraderResult(
                    case_execution_id=execution.id,
                    grader_name="synthetic_vector_v1",
                    criterion=criterion,
                    passed=passed,
                    message="Synthetic criterion vector fixture",
                    details={},
                )
            )
        db_session.add(
            TraceEvent(
                case_execution_id=execution.id,
                sequence_number=1,
                event_type="grader_completed",
                payload={
                    "grader_names": ["synthetic_vector_v1"],
                    "passed": all(vector),
                    "criteria": [
                        {
                            "grader_name": "synthetic_vector_v1",
                            "criterion": criterion,
                            "passed": passed,
                        }
                        for criterion, passed in zip(("alpha", "beta"), vector, strict=True)
                    ],
                },
            )
        )
    db_session.commit()

    summary = analyze_experiment(db_session, experiment.id)
    assert summary.grid_integrity.expected_coordinates == 75
    assert summary.grid_integrity.observed_executions == 3
    assert summary.grid_integrity.missing_coordinates == 72
    assert summary.grid_integrity.valid is False
    assert summary.execution_counts.expected == 75
    assert summary.execution_counts.nonterminal == 72
    assert summary.quality_comparison_ready is False
    assert summary.execution_counts.passing == 0
    assert summary.execution_counts.criterion_failing == 3
    assert summary.stability_counts.unstable == 1
    assert summary.stability_counts.incomplete == 24
    eligible_case = next(row for row in summary.cases if row.case_key == "eligible-refund")
    assert eligible_case.stability == "unstable"
    assert summary.timing.run_duration.observed_count == 3
    assert summary.timing.run_duration.minimum_ms == 100
    assert summary.timing.run_duration.median_ms == 100
    assert summary.timing.run_duration.p95_ms == 100

    ungraded = Experiment(
        suite_id=definitions.suite_id,  # type: ignore[attr-defined]
        agent_prompt_id=definitions.prompt_id_for(PROMPT_V2_VERSION),  # type: ignore[attr-defined]
        provider="deterministic-fake",
        status="completed",
        trials=1,
        completed_at=now,
    )
    db_session.add(ungraded)
    db_session.flush()
    db_session.add(
        CaseExecution(
            experiment_id=ungraded.id,
            case_id=definitions.case_id,  # type: ignore[attr-defined]
            trial_number=1,
            status="completed",
            created_at=now,
            started_at=now,
            completed_at=now,
        )
    )
    db_session.commit()

    ungraded_summary = analyze_experiment(db_session, ungraded.id)
    assert ungraded_summary.execution_counts.passing == 0
    assert ungraded_summary.execution_counts.ungraded_terminal == 1
    assert ungraded_summary.strict_pass_rate == type(ungraded_summary.strict_pass_rate)(
        numerator=0,
        denominator=25,
        value=0.0,
    )
    assert ungraded_summary.is_fully_graded is False
    assert ungraded_summary.quality_comparison_ready is False
    assert ungraded_summary.grid_integrity.missing_coordinates == 24
    assert ungraded_summary.stability_counts.incomplete == 25

    with pytest.raises(
        ExperimentComparisonConflictError,
        match="case/trial grid is incomplete or invalid",
    ):
        compare_experiments(db_session, experiment.id, ungraded.id)


def test_missing_one_recorded_criterion_cannot_make_a_full_grid_pass(
    db_session: Session,
    seeded_suite: object,
) -> None:
    definitions = seeded_suite
    now = datetime(2026, 8, 22, 13, 0, tzinfo=UTC)
    experiments: list[Experiment] = []
    target_execution: CaseExecution | None = None

    for prompt_version in (PROMPT_V1_VERSION, PROMPT_V2_VERSION):
        experiment = Experiment(
            suite_id=definitions.suite_id,  # type: ignore[attr-defined]
            agent_prompt_id=definitions.prompt_id_for(prompt_version),  # type: ignore[attr-defined]
            provider="deterministic-fake",
            status="completed",
            trials=1,
            completed_at=now,
        )
        db_session.add(experiment)
        db_session.flush()
        experiments.append(experiment)

        for case_key, case_id in definitions.case_ids_by_key.items():  # type: ignore[attr-defined]
            execution = CaseExecution(
                experiment_id=experiment.id,
                case_id=case_id,
                trial_number=1,
                status="completed",
                created_at=now,
                started_at=now,
                completed_at=now,
            )
            db_session.add(execution)
            db_session.flush()

            criteria = [("kept_pass", True)]
            if prompt_version == PROMPT_V2_VERSION and case_key == "eligible-refund":
                criteria.append(("deleted_failure", False))
                target_execution = execution
            for criterion, passed in criteria:
                db_session.add(
                    GraderResult(
                        case_execution_id=execution.id,
                        grader_name="synthetic_completeness_v1",
                        criterion=criterion,
                        passed=passed,
                        message="Synthetic completeness fixture",
                        details={},
                    )
                )
            db_session.add(
                TraceEvent(
                    case_execution_id=execution.id,
                    sequence_number=1,
                    event_type="grader_completed",
                    payload={
                        "grader_names": ["synthetic_completeness_v1"],
                        "passed": all(passed for _, passed in criteria),
                        "criteria": [
                            {
                                "grader_name": "synthetic_completeness_v1",
                                "criterion": criterion,
                                "passed": passed,
                            }
                            for criterion, passed in criteria
                        ],
                    },
                )
            )

    assert target_execution is not None
    db_session.commit()
    baseline, candidate = experiments
    complete_candidate = analyze_experiment(db_session, candidate.id)
    assert complete_candidate.is_fully_graded is True
    assert complete_candidate.strict_pass_rate.numerator == 24

    db_session.execute(
        delete(GraderResult).where(
            GraderResult.case_execution_id == target_execution.id,
            GraderResult.criterion == "deleted_failure",
        )
    )
    db_session.commit()

    incomplete_candidate = analyze_experiment(db_session, candidate.id)
    assert incomplete_candidate.strict_pass_rate.numerator == 24
    assert incomplete_candidate.execution_counts.graded == 24
    assert incomplete_candidate.execution_counts.ungraded_terminal == 1
    assert incomplete_candidate.is_fully_graded is False
    assert incomplete_candidate.quality_comparison_ready is False

    comparison = compare_experiments(db_session, baseline.id, candidate.id)
    assert comparison.quality_comparison_ready is False
    assert comparison.strict_pass_rate.baseline == 1.0
    assert comparison.strict_pass_rate.candidate == 0.96
    assert comparison.strict_pass_rate.delta is None
