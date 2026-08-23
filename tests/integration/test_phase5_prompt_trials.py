"""PostgreSQL/API coverage for immutable prompts and three-trial creation."""

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from rq import Queue
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from evalguard.db.models import (
    AgentPrompt,
    CaseExecution,
    EvaluationCase,
    ExecutionDispatch,
    ExecutionFaultPlan,
    Experiment,
)
from evalguard.evaluation.prompts import (
    PROMPT_NAME,
    PROMPT_V1,
    PROMPT_V1_VERSION,
    PROMPT_V2,
    PROMPT_V2_VERSION,
    seed_agent_prompts,
)
from evalguard.evaluation.suite import SUITE_NAME, SUITE_VERSION
from evalguard.workers.dispatch import create_dispatch

pytestmark = pytest.mark.integration

PROMPT_V1_HASH = "08724bf556610d9563f27189eb5b298e62a1ce57952885f481c5f8cf93f5ab69"
PROMPT_V2_HASH = "f93d1378329f1156219063cebabfde0cd65e44d08c382541ed600c05140038ad"


def test_prompt_seed_is_exact_idempotent_and_database_immutable(db_session: Session) -> None:
    first = seed_agent_prompts(db_session)
    db_session.commit()
    first_ids = tuple(prompt.id for prompt in first)

    second = seed_agent_prompts(db_session)
    db_session.commit()

    assert tuple(prompt.id for prompt in second) == first_ids
    assert [(prompt.name, prompt.version) for prompt in second] == [
        (PROMPT_NAME, PROMPT_V1_VERSION),
        (PROMPT_NAME, PROMPT_V2_VERSION),
    ]
    assert [prompt.definition_hash for prompt in second] == [PROMPT_V1_HASH, PROMPT_V2_HASH]
    assert PROMPT_V1.definition_hash == PROMPT_V1_HASH
    assert PROMPT_V2.definition_hash == PROMPT_V2_HASH
    assert all(prompt.is_published for prompt in second)
    assert db_session.scalar(select(func.count()).select_from(AgentPrompt)) == 2

    with pytest.raises(DBAPIError, match="published agent prompts are immutable"):
        db_session.execute(
            update(AgentPrompt)
            .where(AgentPrompt.id == first_ids[0])
            .values(instructions="Synthetic drift that must never persist")
        )
        db_session.commit()
    db_session.rollback()


def test_three_trial_submission_creates_exact_grid_and_targets_one_fault_trial(
    client: TestClient,
    seeded_suite: object,
    db_session: Session,
    rq_queue: Queue,
) -> None:
    del seeded_suite
    payload = {
        "provider": "deterministic-fake",
        "prompt_name": PROMPT_NAME,
        "prompt_version": PROMPT_V1_VERSION,
        "trials": 3,
        "fault_injection": {
            "case_key": "eligible-refund",
            "trial_number": 2,
            "mode": "transient_before_provider",
            "fail_attempts": 1,
        },
    }
    response = client.post(
        "/api/v1/experiments",
        json=payload,
        headers={"Idempotency-Key": "phase5-three-trial-grid"},
    )

    assert response.status_code == 202
    accepted = response.json()
    experiment_id = UUID(accepted["id"])
    execution_ids = {UUID(value) for value in accepted["execution_ids"]}
    assert len(execution_ids) == 75
    assert len(rq_queue.job_ids) == 75

    db_session.expire_all()
    experiment = db_session.get(Experiment, experiment_id)
    assert experiment is not None
    assert experiment.trials == 3
    prompt = db_session.get(AgentPrompt, experiment.agent_prompt_id)
    assert prompt is not None
    assert prompt.version == PROMPT_V1_VERSION

    rows = db_session.execute(
        select(EvaluationCase.key, CaseExecution.trial_number, CaseExecution.id)
        .join(CaseExecution, CaseExecution.case_id == EvaluationCase.id)
        .where(CaseExecution.experiment_id == experiment_id)
        .order_by(EvaluationCase.key, CaseExecution.trial_number)
    ).all()
    assert {execution_id for _, _, execution_id in rows} == execution_ids
    assert len({case_key for case_key, _, _ in rows}) == 25
    assert all(
        [trial for key, trial, _ in rows if key == case_key] == [1, 2, 3]
        for case_key in {key for key, _, _ in rows}
    )
    dispatches = db_session.scalars(
        select(ExecutionDispatch).where(ExecutionDispatch.case_execution_id.in_(execution_ids))
    ).all()
    assert len(dispatches) == 75
    faults = db_session.execute(
        select(EvaluationCase.key, CaseExecution.trial_number, ExecutionFaultPlan)
        .join(CaseExecution, CaseExecution.case_id == EvaluationCase.id)
        .join(ExecutionFaultPlan, ExecutionFaultPlan.case_execution_id == CaseExecution.id)
        .where(CaseExecution.experiment_id == experiment_id)
    ).all()
    assert [(key, trial, fault.mode) for key, trial, fault in faults] == [
        ("eligible-refund", 2, "transient_before_provider")
    ]

    replay = client.post(
        "/api/v1/experiments",
        json=payload,
        headers={"Idempotency-Key": "phase5-three-trial-grid"},
    )
    assert replay.status_code == 202
    assert replay.json()["id"] == accepted["id"]
    assert replay.json()["execution_ids"] == accepted["execution_ids"]

    changed_prompt = client.post(
        "/api/v1/experiments",
        json={**payload, "prompt_version": PROMPT_V2_VERSION},
        headers={"Idempotency-Key": "phase5-three-trial-grid"},
    )
    changed_trials = client.post(
        "/api/v1/experiments",
        json={**payload, "trials": 2},
        headers={"Idempotency-Key": "phase5-three-trial-grid"},
    )
    changed_fault_trial = client.post(
        "/api/v1/experiments",
        json={
            **payload,
            "fault_injection": {**payload["fault_injection"], "trial_number": 1},
        },
        headers={"Idempotency-Key": "phase5-three-trial-grid"},
    )
    assert changed_prompt.status_code == 409
    assert changed_trials.status_code == 409
    assert changed_fault_trial.status_code == 409


def test_prompt_listing_and_fault_trial_validation(
    client: TestClient,
    seeded_suite: object,
) -> None:
    del seeded_suite
    response = client.get("/api/v1/prompts")

    assert response.status_code == 200
    prompts = response.json()
    assert [(prompt["name"], prompt["version"]) for prompt in prompts] == [
        (PROMPT_NAME, PROMPT_V1_VERSION),
        (PROMPT_NAME, PROMPT_V2_VERSION),
    ]
    assert [prompt["definition_hash"] for prompt in prompts] == [
        PROMPT_V1_HASH,
        PROMPT_V2_HASH,
    ]

    invalid = client.post(
        "/api/v1/experiments",
        json={
            "trials": 2,
            "fault_injection": {
                "case_key": "eligible-refund",
                "trial_number": 3,
                "mode": "transient_before_provider",
            },
        },
    )
    assert invalid.status_code == 422
    assert "trial_number must not exceed trials" in invalid.text


def test_legacy_null_prompt_replays_only_when_prompt_fields_are_omitted(
    client: TestClient,
    seeded_legacy_suite: object,
    db_session: Session,
) -> None:
    definitions = seeded_legacy_suite
    legacy_key = "phase5-legacy-null-prompt-replay"
    legacy_experiment = Experiment(
        suite_id=definitions.suite_id,  # type: ignore[attr-defined]
        agent_prompt_id=None,
        provider="deterministic-fake",
        status="queued",
        idempotency_key=legacy_key,
        trials=1,
    )
    db_session.add(legacy_experiment)
    db_session.flush()
    legacy_execution = CaseExecution(
        experiment_id=legacy_experiment.id,
        case_id=definitions.case_id,  # type: ignore[attr-defined]
        trial_number=1,
        status="queued",
    )
    db_session.add(legacy_execution)
    db_session.flush()
    create_dispatch(db_session, legacy_execution.id, generation=1)
    db_session.commit()

    legacy_wire_payload = {
        "suite_name": SUITE_NAME,
        "suite_version": SUITE_VERSION,
        "provider": "deterministic-fake",
        "trials": 1,
    }
    replay = client.post(
        "/api/v1/experiments",
        json=legacy_wire_payload,
        headers={"Idempotency-Key": legacy_key},
    )

    assert replay.status_code == 202
    assert replay.json()["id"] == str(legacy_experiment.id)
    assert replay.json()["execution_ids"] == [str(legacy_execution.id)]
    db_session.expire_all()
    persisted_legacy = db_session.get(Experiment, legacy_experiment.id)
    assert persisted_legacy is not None
    assert persisted_legacy.agent_prompt_id is None

    explicit_version = client.post(
        "/api/v1/experiments",
        json={**legacy_wire_payload, "prompt_version": PROMPT_V2_VERSION},
        headers={"Idempotency-Key": legacy_key},
    )
    explicit_name = client.post(
        "/api/v1/experiments",
        json={**legacy_wire_payload, "prompt_name": PROMPT_NAME},
        headers={"Idempotency-Key": legacy_key},
    )
    assert explicit_version.status_code == 409
    assert explicit_name.status_code == 409

    fresh = client.post(
        "/api/v1/experiments",
        json=legacy_wire_payload,
        headers={"Idempotency-Key": "phase5-new-default-prompt"},
    )
    assert fresh.status_code == 202
    fresh_experiment = db_session.get(Experiment, UUID(fresh.json()["id"]))
    assert fresh_experiment is not None
    assert fresh_experiment.agent_prompt_id == definitions.prompt_id_for(  # type: ignore[attr-defined]
        PROMPT_V2_VERSION
    )


def test_postgresql_rejects_out_of_range_trial_counts(
    db_session: Session,
    seeded_suite: object,
) -> None:
    suite = seeded_suite
    experiment = Experiment(
        suite_id=suite.suite_id,  # type: ignore[attr-defined]
        agent_prompt_id=suite.prompt_id_for(PROMPT_V1_VERSION),  # type: ignore[attr-defined]
        provider="deterministic-fake",
        status="queued",
        trials=4,
    )
    db_session.add(experiment)
    with pytest.raises(IntegrityError, match="ck_experiments_trials_range"):
        db_session.commit()
    db_session.rollback()


def test_postgresql_freezes_prompt_trial_identity_and_enforces_parent_trial_count(
    db_session: Session,
    seeded_suite: object,
) -> None:
    definitions = seeded_suite
    experiment = Experiment(
        suite_id=definitions.suite_id,  # type: ignore[attr-defined]
        agent_prompt_id=definitions.prompt_id_for(PROMPT_V1_VERSION),  # type: ignore[attr-defined]
        provider="deterministic-fake",
        status="queued",
        trials=1,
    )
    db_session.add(experiment)
    db_session.commit()
    experiment_id = experiment.id

    with pytest.raises(DBAPIError, match="experiment prompt and trial identity is immutable"):
        db_session.execute(
            update(Experiment)
            .where(Experiment.id == experiment_id)
            .values(
                agent_prompt_id=definitions.prompt_id_for(  # type: ignore[attr-defined]
                    PROMPT_V2_VERSION
                )
            )
        )
        db_session.commit()
    db_session.rollback()

    with pytest.raises(DBAPIError, match="experiment prompt and trial identity is immutable"):
        db_session.execute(
            update(Experiment).where(Experiment.id == experiment_id).values(trials=2)
        )
        db_session.commit()
    db_session.rollback()

    db_session.add(
        CaseExecution(
            experiment_id=experiment_id,
            case_id=definitions.case_id,  # type: ignore[attr-defined]
            trial_number=2,
            status="queued",
        )
    )
    with pytest.raises(DBAPIError, match="trial_number exceeds experiment trials"):
        db_session.commit()
    db_session.rollback()
