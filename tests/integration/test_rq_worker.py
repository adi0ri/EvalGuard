"""RQ SimpleWorker coverage for the deterministic queued evaluation."""

from collections.abc import Callable
from uuid import UUID

import pytest
from redis import Redis
from rq import Queue, SimpleWorker
from rq.job import JobStatus
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from evalguard.db.models import (
    CaseExecution,
    Customer,
    Experiment,
    GraderResult,
    Order,
    Refund,
    SupportTicket,
    ToolInvocation,
    TraceEvent,
)
from evalguard.workers.jobs import run_case_execution
from evalguard.workers.queue import enqueue_execution

pytestmark = pytest.mark.integration


def test_simple_worker_persists_state_trace_and_grades(
    session_factory: sessionmaker[Session],
    execution_factory: Callable[..., UUID],
    redis_connection: Redis,
    rq_queue: Queue,
) -> None:
    execution_id = execution_factory(materialize_state=False)
    job = enqueue_execution(execution_id)
    worker = SimpleWorker([rq_queue], connection=redis_connection)

    worked = worker.work(burst=True, logging_level="WARNING", with_scheduler=False)

    assert worked is True
    assert job.get_status(refresh=True) == JobStatus.FINISHED
    with session_factory() as session:
        execution = session.get(CaseExecution, execution_id)
        assert execution is not None
        experiment = session.get(Experiment, execution.experiment_id)
        assert experiment is not None
        assert execution.status == "completed"
        assert experiment.status == "completed"
        assert execution.completed_at is not None
        assert experiment.completed_at is not None
        assert execution.final_resolution is not None
        assert execution.final_resolution["synthetic"] is True

        customers = session.scalars(
            select(Customer).where(Customer.case_execution_id == execution_id)
        ).all()
        orders = session.scalars(select(Order).where(Order.case_execution_id == execution_id)).all()
        refunds = session.scalars(
            select(Refund).where(Refund.case_execution_id == execution_id)
        ).all()
        assert [(customer.external_id, customer.email) for customer in customers] == [
            ("CUST-1001", "maya.chen@example.test")
        ]
        assert [(order.external_id, order.total_cents) for order in orders] == [("PD-1001", 4599)]
        assert orders[0].shipping_address_line1 is None
        assert [(refund.amount_cents, refund.status) for refund in refunds] == [(4599, "completed")]
        assert (
            session.scalars(
                select(SupportTicket).where(SupportTicket.case_execution_id == execution_id)
            ).all()
            == []
        )

        invocations = session.scalars(
            select(ToolInvocation)
            .where(ToolInvocation.case_execution_id == execution_id)
            .order_by(ToolInvocation.started_at, ToolInvocation.id)
        ).all()
        assert [invocation.tool_name for invocation in invocations] == [
            "lookup_order",
            "issue_refund",
        ]
        assert all(invocation.status == "completed" for invocation in invocations)

        traces = session.scalars(
            select(TraceEvent)
            .where(TraceEvent.case_execution_id == execution_id)
            .order_by(TraceEvent.sequence_number)
        ).all()
        assert [trace.sequence_number for trace in traces] == list(range(1, len(traces) + 1))
        assert [trace.event_type for trace in traces] == [
            "provider_request",
            "tool_call",
            "tool_result",
            "tool_call",
            "tool_result",
            "provider_response",
            "grader_completed",
        ]

        grader_results = session.scalars(
            select(GraderResult)
            .where(GraderResult.case_execution_id == execution_id)
            .order_by(GraderResult.criterion)
        ).all()
        assert {result.criterion for result in grader_results} == {
            "refund_created",
            "refund_amount_matches",
            "required_tool_order",
            "structured_final_resolution",
        }
        assert all(result.passed for result in grader_results)

    replay = run_case_execution(str(execution_id))
    assert replay == {
        "execution_id": str(execution_id),
        "status": "completed",
        "replayed": True,
    }
    with session_factory() as session:
        assert (
            len(
                session.scalars(
                    select(Refund).where(Refund.case_execution_id == execution_id)
                ).all()
            )
            == 1
        )


def test_failed_job_records_durable_terminal_state_and_trace(
    session_factory: sessionmaker[Session],
    execution_factory: Callable[..., UUID],
    redis_connection: Redis,
    rq_queue: Queue,
) -> None:
    execution_id = execution_factory(materialize_state=False)
    with session_factory.begin() as session:
        execution = session.get(CaseExecution, execution_id)
        assert execution is not None
        experiment = session.get(Experiment, execution.experiment_id)
        assert experiment is not None
        experiment.provider = "unsupported-provider"

    job = enqueue_execution(execution_id)
    worker = SimpleWorker([rq_queue], connection=redis_connection)
    assert worker.work(burst=True, logging_level="CRITICAL", with_scheduler=False) is True
    assert job.get_status(refresh=True) == JobStatus.FAILED

    with session_factory() as session:
        execution = session.get(CaseExecution, execution_id)
        assert execution is not None
        assert execution.status == "failed"
        assert execution.started_at is not None
        assert execution.completed_at is not None
        traces = session.scalars(
            select(TraceEvent).where(TraceEvent.case_execution_id == execution_id)
        ).all()
        assert [(trace.sequence_number, trace.event_type) for trace in traces] == [
            (1, "execution_failed")
        ]
        assert traces[0].payload == {
            "error_type": "UnsupportedProviderError",
            "message": "unsupported-provider",
            "retryable": False,
        }
