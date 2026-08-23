"""PostgreSQL coverage for Phase 3 multi-case lifecycle aggregation."""

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from evalguard.db.models import CaseExecution, Experiment
from evalguard.evaluation.lifecycle import reconcile_experiment_status

pytestmark = pytest.mark.integration


def test_experiment_completes_only_after_every_execution_is_terminal(
    db_session: Session,
    seeded_suite: object,
) -> None:
    suite_id = seeded_suite.suite_id  # type: ignore[attr-defined]
    case_id = seeded_suite.case_id  # type: ignore[attr-defined]
    experiment = Experiment(
        suite_id=suite_id,
        provider="deterministic-fake",
        status="queued",
        idempotency_key=f"lifecycle-{uuid4()}",
    )
    db_session.add(experiment)
    db_session.flush()
    executions = [
        CaseExecution(
            experiment_id=experiment.id,
            case_id=case_id,
            trial_number=trial,
            status="queued",
        )
        for trial in range(1, 4)
    ]
    db_session.add_all(executions)
    db_session.flush()

    executions[0].status = "completed"
    reconcile_experiment_status(db_session, experiment.id)
    assert experiment.status == "running"
    assert experiment.completed_at is None

    executions[1].status = "completed"
    reconcile_experiment_status(db_session, experiment.id)
    assert experiment.status == "running"
    assert experiment.completed_at is None

    executions[2].status = "completed"
    reconcile_experiment_status(db_session, experiment.id)
    assert experiment.status == "completed"
    assert experiment.completed_at is not None


def test_terminal_infrastructure_failure_wins_after_remaining_work_finishes(
    db_session: Session,
    seeded_suite: object,
) -> None:
    suite_id = seeded_suite.suite_id  # type: ignore[attr-defined]
    case_id = seeded_suite.case_id  # type: ignore[attr-defined]
    experiment = Experiment(
        suite_id=suite_id,
        provider="deterministic-fake",
        status="running",
        idempotency_key=f"lifecycle-failure-{uuid4()}",
    )
    db_session.add(experiment)
    db_session.flush()
    executions = [
        CaseExecution(
            experiment_id=experiment.id,
            case_id=case_id,
            trial_number=trial,
            status=status,
        )
        for trial, status in ((1, "failed"), (2, "running"))
    ]
    db_session.add_all(executions)
    db_session.flush()

    reconcile_experiment_status(db_session, experiment.id)
    assert experiment.status == "running"
    assert experiment.completed_at is None

    executions[1].status = "completed"
    reconcile_experiment_status(db_session, experiment.id)
    assert experiment.status == "failed"
    assert experiment.completed_at is not None

    persisted_statuses = tuple(
        db_session.scalars(
            select(CaseExecution.status).where(CaseExecution.experiment_id == experiment.id)
        ).all()
    )
    assert sorted(persisted_statuses) == ["completed", "failed"]
