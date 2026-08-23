"""Aggregate experiment lifecycle state from all of its case executions."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from evalguard.db.models import CaseExecution, Experiment


def reconcile_experiment_status(
    session: Session,
    experiment_id: UUID,
    *,
    completed_at: datetime | None = None,
) -> Experiment | None:
    """Lock and derive an experiment's status from every case execution.

    Phase 3 runs many cases concurrently. A single finished case therefore cannot
    decide the parent experiment's terminal status.
    """

    experiment = session.scalar(
        select(Experiment).where(Experiment.id == experiment_id).with_for_update()
    )
    if experiment is None:
        return None

    session.flush()
    statuses = tuple(
        session.scalars(
            select(CaseExecution.status).where(CaseExecution.experiment_id == experiment_id)
        ).all()
    )
    if not statuses:
        return experiment

    terminal_statuses = {"completed", "failed", "enqueue_failed"}
    if any(status not in terminal_statuses for status in statuses):
        experiment.status = "running"
        experiment.completed_at = None
    elif any(status in {"failed", "enqueue_failed"} for status in statuses):
        experiment.status = "failed"
        experiment.completed_at = completed_at or datetime.now(UTC)
    else:
        experiment.status = "completed"
        experiment.completed_at = completed_at or datetime.now(UTC)

    return experiment
