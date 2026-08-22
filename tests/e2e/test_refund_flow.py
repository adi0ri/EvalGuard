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
