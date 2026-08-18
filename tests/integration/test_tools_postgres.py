"""Transactional and execution-isolation coverage against PostgreSQL."""

from collections.abc import Callable
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from evalguard.db.models import Refund, ToolInvocation, TraceEvent
from evalguard.domain.tool_schemas import IssueRefundArgs, LookupOrderArgs
from evalguard.domain.tools import ToolExecutionError, ToolRuntime

pytestmark = pytest.mark.integration


class ForcedRollback(RuntimeError):
    """Test-only error raised after the state-changing tool has flushed."""


def test_refund_and_its_evidence_roll_back_as_one_transaction(
    session_factory: sessionmaker[Session],
    execution_factory: Callable[..., UUID],
) -> None:
    execution_id = execution_factory(materialize_state=True)

    with pytest.raises(ForcedRollback), session_factory.begin() as session:
        runtime = ToolRuntime(session, execution_id)
        result = runtime.issue_refund(
            IssueRefundArgs(
                order_id="PD-1001",
                amount_cents=4599,
                reason="Verify transaction rollback",
            ),
            tool_call_id="rollback-refund-1",
        )
        assert result.status == "completed"
        assert session.scalar(select(func.count()).select_from(Refund)) == 1
        raise ForcedRollback

    with session_factory() as verification_session:
        assert verification_session.scalar(select(func.count()).select_from(Refund)) == 0
        assert verification_session.scalar(select(func.count()).select_from(ToolInvocation)) == 0
        assert verification_session.scalar(select(func.count()).select_from(TraceEvent)) == 0


def test_tool_runtime_cannot_read_or_refund_another_executions_order(
    session_factory: sessionmaker[Session],
    execution_factory: Callable[..., UUID],
) -> None:
    owner_execution_id = execution_factory(
        materialize_state=True,
        order_external_id="PD-PRIVATE",
        customer_external_id="CUST-OWNER",
    )
    other_execution_id = execution_factory(materialize_state=False)

    with session_factory() as session:
        owner_runtime = ToolRuntime(session, owner_execution_id)
        owner_order = owner_runtime.lookup_order(
            LookupOrderArgs(order_id="PD-PRIVATE"),
            tool_call_id="owner-lookup-1",
        )
        assert owner_order.customer_id == "CUST-OWNER"
        session.rollback()

    with session_factory() as session:
        other_runtime = ToolRuntime(session, other_execution_id)
        with pytest.raises(ToolExecutionError, match="not found in this execution"):
            other_runtime.lookup_order(
                LookupOrderArgs(order_id="PD-PRIVATE"),
                tool_call_id="cross-execution-lookup-1",
            )
        session.rollback()

    with session_factory() as session:
        other_runtime = ToolRuntime(session, other_execution_id)
        with pytest.raises(ToolExecutionError, match="not found in this execution"):
            other_runtime.issue_refund(
                IssueRefundArgs(
                    order_id="PD-PRIVATE",
                    amount_cents=4599,
                    reason="Must not cross execution ownership",
                ),
                tool_call_id="cross-execution-refund-1",
            )
        session.rollback()

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Refund)) == 0
