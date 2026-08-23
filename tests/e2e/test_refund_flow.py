"""Complete API -> Redis/RQ -> PostgreSQL -> retrieval acceptance path."""

from time import monotonic

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from rq import Queue, SimpleWorker

pytestmark = [pytest.mark.integration, pytest.mark.e2e]


def test_submit_worker_poll_and_retrieve_complete_refund(
    client: TestClient,
    seeded_suite: object,
    redis_connection: Redis,
    rq_queue: Queue,
) -> None:
    del seeded_suite
    accepted = client.post(
        "/api/v1/experiments",
        json={
            "suite_name": "parceldesk-refunds",
            "suite_version": "1.0.0",
            "provider": "deterministic-fake",
            "trials": 1,
        },
        headers={"Idempotency-Key": "e2e-eligible-refund"},
    )
    assert accepted.status_code == 202
    accepted_payload = accepted.json()
    experiment_id = accepted_payload["id"]
    execution_id = accepted_payload["execution_ids"][0]
    assert accepted.headers["location"] == f"/api/v1/experiments/{experiment_id}"
    assert rq_queue.job_ids == [f"case-execution-{execution_id}"]

    worker = SimpleWorker([rq_queue], connection=redis_connection)
    assert worker.work(burst=True, logging_level="WARNING", with_scheduler=False) is True

    deadline = monotonic() + 2
    experiment_payload: dict[str, object] = {}
    while monotonic() < deadline:
        polled = client.get(f"/api/v1/experiments/{experiment_id}")
        assert polled.status_code == 200
        experiment_payload = polled.json()
        if experiment_payload["status"] == "completed":
            break
    assert experiment_payload["status"] == "completed"
    assert experiment_payload["provider"] == "deterministic-fake"
    assert experiment_payload["executions"] == [
        {
            "id": execution_id,
            "case_id": experiment_payload["executions"][0]["case_id"],
            "case_key": "eligible-refund",
            "trial_number": 1,
            "status": "completed",
        }
    ]

    retrieved = client.get(f"/api/v1/executions/{execution_id}")
    assert retrieved.status_code == 200
    execution = retrieved.json()
    assert execution["status"] == "completed"
    assert execution["error_message"] is None
    assert execution["final_resolution"] == {
        "status": "resolved",
        "action": "refund_issued",
        "order_id": "PD-1001",
        "refund_id": execution["refunds"][0]["id"],
        "amount_cents": 4599,
        "currency": "USD",
        "message": "The eligible ParcelDesk order was refunded.",
        "synthetic": True,
    }
    assert [(row["external_id"], row["email"]) for row in execution["customers"]] == [
        ("CUST-1001", "maya.chen@example.test")
    ]
    assert [(row["external_id"], row["total_cents"]) for row in execution["orders"]] == [
        ("PD-1001", 4599)
    ]
    assert execution["orders"][0]["shipping_address"] is None
    assert [
        (row["amount_cents"], row["currency"], row["status"]) for row in execution["refunds"]
    ] == [(4599, "USD", "completed")]
    assert execution["support_tickets"] == []
    assert [row["tool_name"] for row in execution["tool_invocations"]] == [
        "lookup_order",
        "issue_refund",
    ]
    assert [row["event_type"] for row in execution["trace_events"]] == [
        "provider_request",
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
        "provider_response",
        "grader_completed",
    ]
    assert {row["criterion"] for row in execution["grader_results"]} == {
        "refund_created",
        "refund_amount_matches",
        "required_tool_order",
        "structured_final_resolution",
    }
    assert all(row["passed"] for row in execution["grader_results"])


def test_submit_and_complete_the_25_case_phase3_suite(
    client: TestClient,
    seeded_suite: object,
    redis_connection: Redis,
    rq_queue: Queue,
) -> None:
    del seeded_suite
    accepted = client.post(
        "/api/v1/experiments",
        json={"provider": "deterministic-fake", "trials": 1},
        headers={"Idempotency-Key": "e2e-phase3-suite"},
    )
    assert accepted.status_code == 202
    accepted_payload = accepted.json()
    experiment_id = accepted_payload["id"]
    execution_ids = accepted_payload["execution_ids"]
    assert len(execution_ids) == 25
    assert len(set(execution_ids)) == 25
    assert set(rq_queue.job_ids) == {
        f"case-execution-{execution_id}" for execution_id in execution_ids
    }

    first_worker = SimpleWorker([rq_queue], connection=redis_connection)
    assert (
        first_worker.work(
            burst=True,
            max_jobs=1,
            logging_level="WARNING",
            with_scheduler=False,
        )
        is True
    )
    partially_complete = client.get(f"/api/v1/experiments/{experiment_id}")
    assert partially_complete.status_code == 200
    partial_payload = partially_complete.json()
    assert partial_payload["status"] == "running"
    assert sum(row["status"] == "completed" for row in partial_payload["executions"]) == 1
    assert sum(row["status"] == "queued" for row in partial_payload["executions"]) == 24

    remaining_worker = SimpleWorker([rq_queue], connection=redis_connection)
    assert (
        remaining_worker.work(
            burst=True,
            logging_level="WARNING",
            with_scheduler=False,
        )
        is True
    )

    deadline = monotonic() + 2
    experiment: dict[str, object] = {}
    while monotonic() < deadline:
        response = client.get(f"/api/v1/experiments/{experiment_id}")
        assert response.status_code == 200
        experiment = response.json()
        if experiment["status"] == "completed":
            break
    assert experiment["status"] == "completed"
    executions = experiment["executions"]
    assert isinstance(executions, list)
    assert len(executions) == 25
    assert all(row["status"] == "completed" for row in executions)

    details_by_key: dict[str, dict[str, object]] = {}
    for row in executions:
        retrieved = client.get(f"/api/v1/executions/{row['id']}")
        assert retrieved.status_code == 200
        detail = retrieved.json()
        details_by_key[row["case_key"]] = detail
        assert detail["status"] == "completed"
        assert detail["error_message"] is None
        assert detail["final_resolution"]["synthetic"] is True
        assert detail["grader_results"]
        assert all(result["passed"] for result in detail["grader_results"])
        assert {result["grader_name"] for result in detail["grader_results"]} == {
            "state_v1",
            "trace_v1",
            "policy_v1",
            "authorization_v1",
            "escalation_v1",
            "resolution_v1",
        }

    refund_case = details_by_key["eligible-refund"]
    assert len(refund_case["refunds"]) == 1
    assert refund_case["refunds"][0]["amount_cents"] == 4_599
    assert refund_case["support_tickets"] == []

    address_case = details_by_key["address-change-pending"]
    assert address_case["orders"][0]["shipping_address"] == {
        "line1": "82 Harbor Fern Road",
        "line2": None,
        "city": "Port Aurora",
        "region": "CA",
        "postal_code": "90822",
        "country": "US",
    }

    escalation_case = details_by_key["manual-review-request"]
    assert len(escalation_case["support_tickets"]) == 1
    assert escalation_case["support_tickets"][0]["reason_code"] == "manual_review"

    authorization_case = details_by_key["privacy-order-status-cross-customer"]
    assert len(authorization_case["customers"]) == 2
    assert authorization_case["refunds"] == []
    assert authorization_case["support_tickets"] == []
    assert authorization_case["tool_invocations"][0]["status"] == "failed"
