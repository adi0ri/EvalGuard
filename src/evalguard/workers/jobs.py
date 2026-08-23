"""RQ job entry points and permanent failure recording."""

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from evalguard.core.config import get_settings
from evalguard.core.logging import configure_logging
from evalguard.db.models import CaseExecution, TraceEvent
from evalguard.db.session import session_scope
from evalguard.evaluation.executor import execute_case
from evalguard.evaluation.lifecycle import reconcile_experiment_status

logger = logging.getLogger(__name__)


def _record_started(case_execution_id: UUID) -> None:
    """Commit observable running state before the evaluation transaction begins."""

    with session_scope() as session:
        execution = session.scalar(
            select(CaseExecution).where(CaseExecution.id == case_execution_id).with_for_update()
        )
        if execution is None or execution.status == "completed":
            return
        execution.status = "running"
        if execution.started_at is None:
            execution.started_at = datetime.now(UTC)
        reconcile_experiment_status(session, execution.experiment_id)


def _record_failure(case_execution_id: UUID, error: Exception) -> None:
    with session_scope() as session:
        execution = session.scalar(
            select(CaseExecution).where(CaseExecution.id == case_execution_id).with_for_update()
        )
        if execution is None or execution.status == "completed":
            return
        failure_time = datetime.now(UTC)
        execution.status = "failed"
        execution.started_at = execution.started_at or failure_time
        execution.completed_at = failure_time
        execution.error_message = f"{type(error).__name__}: {error}"[:1000]
        reconcile_experiment_status(
            session,
            execution.experiment_id,
            completed_at=failure_time,
        )
        current_sequence = session.scalar(
            select(func.coalesce(func.max(TraceEvent.sequence_number), 0)).where(
                TraceEvent.case_execution_id == case_execution_id
            )
        )
        session.add(
            TraceEvent(
                case_execution_id=case_execution_id,
                sequence_number=int(current_sequence or 0) + 1,
                event_type="execution_failed",
                payload={
                    "error_type": type(error).__name__,
                    "message": str(error)[:500],
                    "retryable": False,
                },
            )
        )


def run_case_execution(case_execution_id: str) -> dict[str, Any]:
    """RQ-importable wrapper around the transactional evaluator."""

    settings = get_settings()
    configure_logging(settings.log_level)
    execution_uuid = UUID(case_execution_id)
    try:
        _record_started(execution_uuid)
        with session_scope() as session:
            result = execute_case(session, execution_uuid)
    except Exception as exc:
        _record_failure(execution_uuid, exc)
        logger.exception(
            "Case execution failed",
            extra={"event": "case_execution_failed", "execution_id": case_execution_id},
        )
        raise
    logger.info(
        "Case execution completed",
        extra={"event": "case_execution_completed", "execution_id": case_execution_id},
    )
    return result
