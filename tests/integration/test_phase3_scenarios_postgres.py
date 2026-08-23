"""PostgreSQL-backed execution and regression coverage for all Phase 3 cases."""

from collections.abc import Callable
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from evalguard.db.models import (
    CaseExecution,
    Customer,
    Experiment,
    GraderResult,
    Order,
    Refund,
    SupportTicket,
    TraceEvent,
)
from evalguard.evaluation.executor import UnsupportedCaseDefinitionError, execute_case
from evalguard.evaluation.scenarios import PHASE3_SCENARIOS
from evalguard.providers.fake import DeterministicFakeProvider, FakeBehavior

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("case_key", [scenario.key for scenario in PHASE3_SCENARIOS])
def test_reference_provider_passes_every_phase3_scenario(
    case_key: str,
    session_factory: sessionmaker[Session],
    execution_factory: Callable[..., UUID],
) -> None:
    execution_id = execution_factory(materialize_state=False, case_key=case_key)

    with session_factory.begin() as session:
        result = execute_case(session, execution_id)

    assert result == {
        "execution_id": str(execution_id),
        "status": "completed",
        "passed": True,
        "replayed": False,
    }
    with session_factory() as session:
        execution = session.get(CaseExecution, execution_id)
        assert execution is not None
        assert execution.status == "completed"
        grader_results = session.scalars(
            select(GraderResult).where(GraderResult.case_execution_id == execution_id)
        ).all()
        assert grader_results
        assert all(row.passed for row in grader_results)
        assert {row.grader_name for row in grader_results} == {
            "state_v1",
            "trace_v1",
            "policy_v1",
            "authorization_v1",
            "escalation_v1",
            "resolution_v1",
        }
        if case_key == "duplicate-refund-stable-call-replay":
            assert (
                session.scalars(
                    select(TraceEvent.event_type)
                    .where(TraceEvent.case_execution_id == execution_id)
                    .order_by(TraceEvent.sequence_number)
                )
                .all()
                .count("tool_replay")
                == 1
            )
            replay_grade = next(
                row
                for row in grader_results
                if row.grader_name == "trace_v1" and row.criterion == "stable_replay_evidence"
            )
            assert replay_grade.passed is True


@pytest.mark.parametrize(
    ("case_key", "behavior", "grader_name", "criterion"),
    [
        (
            "eligible-refund",
            FakeBehavior.CLAIM_SUCCESS_WITHOUT_ACTION,
            "state_v1",
            "refunds_exact",
        ),
        (
            "eligible-refund",
            FakeBehavior.WRONG_TOOL_ORDER,
            "trace_v1",
            "required_order",
        ),
        (
            "refund-pending-order",
            FakeBehavior.ATTEMPT_FORBIDDEN_ACTION,
            "policy_v1",
            "refund_eligibility",
        ),
        (
            "privacy-order-status-cross-customer",
            FakeBehavior.DISCLOSE_PROTECTED_FIELD,
            "authorization_v1",
            "no_private_disclosure",
        ),
        (
            "refund-at-escalation-threshold",
            FakeBehavior.OMIT_REQUIRED_ESCALATION,
            "escalation_v1",
            "required_ticket_exists",
        ),
        (
            "eligible-refund",
            FakeBehavior.MISMATCHED_RESOLUTION_REFERENCE,
            "resolution_v1",
            "matches_persisted_state",
        ),
        (
            "eligible-refund",
            FakeBehavior.WRONG_REFUND_AMOUNT,
            "policy_v1",
            "refund_amount_exact",
        ),
        (
            "address-change-pending",
            FakeBehavior.WRONG_SHIPPING_ADDRESS,
            "state_v1",
            "addresses_exact",
        ),
        (
            "manual-review-request",
            FakeBehavior.WRONG_ESCALATION_METADATA,
            "escalation_v1",
            "ticket_metadata_matches",
        ),
        (
            "duplicate-address-semantic-retry",
            FakeBehavior.REPEAT_STATE_CHANGE_NEW_CALL_ID,
            "trace_v1",
            "call_cardinality",
        ),
    ],
)
def test_intentional_fake_defects_are_caught_by_the_targeted_grader(
    case_key: str,
    behavior: FakeBehavior,
    grader_name: str,
    criterion: str,
    session_factory: sessionmaker[Session],
    execution_factory: Callable[..., UUID],
) -> None:
    execution_id = execution_factory(materialize_state=False, case_key=case_key)
    provider = DeterministicFakeProvider(behavior_id=behavior)

    with session_factory.begin() as session:
        result = execute_case(session, execution_id, provider=provider)

    assert result["status"] == "completed"
    assert result["passed"] is False
    with session_factory() as session:
        execution = session.get(CaseExecution, execution_id)
        assert execution is not None
        assert execution.status == "completed"
        targeted = session.scalar(
            select(GraderResult).where(
                GraderResult.case_execution_id == execution_id,
                GraderResult.grader_name == grader_name,
                GraderResult.criterion == criterion,
            )
        )
        assert targeted is not None
        assert targeted.passed is False


def test_phase3_business_state_is_isolated_between_executions(
    session_factory: sessionmaker[Session],
    execution_factory: Callable[..., UUID],
) -> None:
    first_id = execution_factory(materialize_state=False, case_key="eligible-refund")
    second_id = execution_factory(materialize_state=False, case_key="eligible-refund")

    with session_factory.begin() as session:
        execute_case(session, first_id)

    with session_factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Customer)
                .where(Customer.case_execution_id == second_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count()).select_from(Order).where(Order.case_execution_id == second_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(Refund)
                .where(Refund.case_execution_id == second_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(SupportTicket)
                .where(SupportTicket.case_execution_id == second_id)
            )
            == 0
        )

    with session_factory.begin() as session:
        execute_case(session, second_id)

    with session_factory() as session:
        refunds = session.scalars(
            select(Refund)
            .where(Refund.case_execution_id.in_((first_id, second_id)))
            .order_by(Refund.case_execution_id)
        ).all()
        assert len(refunds) == 2
        assert refunds[0].id != refunds[1].id
        assert {row.case_execution_id for row in refunds} == {first_id, second_id}


@pytest.mark.parametrize("execution_status", ["queued", "completed"])
def test_executor_rejects_a_case_outside_the_experiment_suite(
    session_factory: sessionmaker[Session],
    seeded_suite: object,
    seeded_legacy_suite: object,
    execution_status: str,
) -> None:
    with session_factory.begin() as session:
        experiment = Experiment(
            suite_id=seeded_legacy_suite.suite_id,  # type: ignore[attr-defined]
            provider="deterministic-fake",
            status="queued",
        )
        session.add(experiment)
        session.flush()
        execution = CaseExecution(
            experiment_id=experiment.id,
            case_id=seeded_suite.case_id,  # type: ignore[attr-defined]
            trial_number=1,
            status=execution_status,
        )
        session.add(execution)
        session.flush()

        with pytest.raises(
            UnsupportedCaseDefinitionError,
            match="outside its experiment suite",
        ):
            execute_case(session, execution.id)

        assert (
            session.scalar(
                select(func.count())
                .select_from(Customer)
                .where(Customer.case_execution_id == execution.id)
            )
            == 0
        )
