"""Keyless contract tests for the single-attempt OpenAI Responses adapter."""

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

from evalguard.providers.openai_responses import (
    DEFAULT_OPENAI_MODEL,
    MAX_OUTPUT_TOKENS,
    FunctionTool,
    OpenAIProviderConfigurationError,
    OpenAIProviderProtocolError,
    OpenAIProviderRequestError,
    OpenAIResponsesProvider,
    create_openai_client,
    function_call_output,
    strict_json_schema,
)


class Dumpable:
    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value

    def model_dump(self, *, mode: str, exclude_none: bool) -> dict[str, Any]:
        assert mode == "json"
        assert exclude_none is False
        return deepcopy(self.value)


class ScriptedResponses:
    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(deepcopy(kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class ScriptedClient:
    def __init__(self, *outcomes: Any, max_retries: int = 0) -> None:
        self.max_retries = max_retries
        self.responses = ScriptedResponses(*outcomes)


TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "order_id": {"type": "string"},
        "address": {
            "type": "object",
            "properties": {
                "line1": {"type": "string"},
                "line2": {"type": "string", "default": None},
            },
            "required": ["line1"],
        },
    },
    "required": ["order_id"],
}

FINAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["resolved", "denied"]},
        "action": {"type": "string"},
    },
    "required": ["status"],
}


def _tool() -> FunctionTool:
    return FunctionTool(
        name="update_shipping_address",
        description="Update the selected order shipping address.",
        parameters=TOOL_SCHEMA,
    )


def test_strict_schema_requires_every_property_and_uses_explicit_null() -> None:
    source = deepcopy(TOOL_SCHEMA)

    strict = strict_json_schema(source)

    assert source == TOOL_SCHEMA
    assert strict["additionalProperties"] is False
    assert strict["required"] == ["order_id", "address"]
    address = strict["properties"]["address"]
    assert address["type"] == ["object", "null"]
    assert address["additionalProperties"] is False
    assert address["required"] == ["line1", "line2"]
    assert address["properties"]["line2"] == {"type": ["string", "null"]}


@pytest.mark.parametrize("keyword", ["allOf", "oneOf"])
def test_strict_schema_rejects_unsupported_composition(keyword: str) -> None:
    with pytest.raises(OpenAIProviderConfigurationError, match=keyword):
        strict_json_schema(
            {
                "type": "object",
                keyword: [{"type": "object", "properties": {}}],
                "properties": {},
            }
        )


def test_one_stateless_turn_builds_strict_request_and_extracts_call_usage() -> None:
    response_output = [
        Dumpable(
            {
                "type": "reasoning",
                "id": "rs_unit_1",
                "encrypted_content": "encrypted-unit-fixture",
                "summary": [],
            }
        ),
        Dumpable(
            {
                "type": "function_call",
                "id": "fc_unit_1",
                "call_id": "call_unit_1",
                "name": "update_shipping_address",
                "arguments": '{"order_id":"PD-1","address":{"line1":"1 Test Way","line2":null}}',
                "status": "completed",
            }
        ),
    ]
    response = SimpleNamespace(
        id="resp_unit_1",
        _request_id="req_unit_1",
        model="gpt-5.6-luna-2026-08-01",
        status="completed",
        output=response_output,
        usage=Dumpable(
            {
                "input_tokens": 120,
                "input_tokens_details": {"cached_tokens": 20},
                "output_tokens": 30,
                "output_tokens_details": {"reasoning_tokens": 5},
                "total_tokens": 150,
            }
        ),
        error=None,
        incomplete_details=None,
    )
    client = ScriptedClient(response)
    provider = OpenAIResponsesProvider(client=client)
    input_items = [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "Synthetic customer request"}],
        }
    ]

    prepared = provider.prepare_request(
        instructions="Follow ParcelDesk policy and use tools.",
        input_items=input_items,
        tools=[_tool()],
        final_schema_name="parceldesk_resolution",
        final_schema=FINAL_SCHEMA,
    )
    persisted = deepcopy(prepared)

    assert client.responses.calls == []

    turn = provider.send_prepared(prepared)

    assert len(client.responses.calls) == 1
    assert prepared == persisted
    request = deepcopy(client.responses.calls[0])
    assert request.pop("timeout") == 45.0
    assert request == prepared
    assert request["model"] == DEFAULT_OPENAI_MODEL
    assert request["store"] is False
    assert request["stream"] is False
    assert request["background"] is False
    assert request["parallel_tool_calls"] is False
    assert request["reasoning"] == {"effort": "low"}
    assert request["max_output_tokens"] == 1024
    assert request["truncation"] == "disabled"
    assert request["service_tier"] == "default"
    assert request["include"] == ["reasoning.encrypted_content"]
    assert "previous_response_id" not in request
    assert request["input"] == input_items
    assert request["tools"][0]["strict"] is True
    assert request["tools"][0]["parameters"]["additionalProperties"] is False
    assert request["text"]["format"]["type"] == "json_schema"
    assert request["text"]["format"]["strict"] is True
    assert request["text"]["format"]["schema"]["required"] == ["status", "action"]
    assert request["text"]["format"]["schema"]["properties"]["action"]["type"] == [
        "string",
        "null",
    ]

    assert turn.response_id == "resp_unit_1"
    assert turn.request_id == "req_unit_1"
    assert turn.model == "gpt-5.6-luna-2026-08-01"
    assert tuple(item["type"] for item in turn.output_items) == (
        "reasoning",
        "function_call",
    )
    assert turn.function_calls[0].call_id == "call_unit_1"
    assert turn.function_calls[0].arguments == {
        "order_id": "PD-1",
        "address": {"line1": "1 Test Way", "line2": None},
    }
    assert turn.final_json is None
    assert turn.usage is not None
    assert turn.usage.input_tokens == 120
    assert turn.usage.output_tokens == 30
    assert turn.usage.total_tokens == 150
    assert turn.usage.cached_input_tokens == 20
    assert turn.usage.reasoning_output_tokens == 5
    assert turn.usage.raw["total_tokens"] == 150

    tool_output = function_call_output(
        turn.function_calls[0].call_id,
        {"status": "updated", "order_id": "PD-1"},
    )
    assert tool_output == {
        "type": "function_call_output",
        "call_id": "call_unit_1",
        "output": '{"order_id":"PD-1","status":"updated"}',
    }
    assert [*turn.output_items, tool_output][-1] == tool_output


def test_persisted_request_tampering_is_rejected_before_network() -> None:
    client = ScriptedClient()
    provider = OpenAIResponsesProvider(client=client)
    prepared = provider.prepare_request(
        instructions="Use tools.",
        input_items=[{"role": "user", "content": "Synthetic request"}],
        tools=[_tool()],
        final_schema_name="parceldesk_resolution",
        final_schema=FINAL_SCHEMA,
    )

    unsafe_store = deepcopy(prepared)
    unsafe_store["store"] = True
    with pytest.raises(OpenAIProviderConfigurationError):
        provider.send_prepared(unsafe_store)

    stateful = deepcopy(prepared)
    stateful["previous_response_id"] = "resp_not_allowed"
    with pytest.raises(OpenAIProviderConfigurationError):
        provider.send_prepared(stateful)

    incomplete_strict_schema = deepcopy(prepared)
    incomplete_strict_schema["tools"][0]["parameters"]["required"] = ["order_id"]
    with pytest.raises(OpenAIProviderConfigurationError):
        provider.send_prepared(incomplete_strict_schema)

    assert client.responses.calls == []


def test_jsonb_reordered_schema_properties_remain_sendable() -> None:
    response = {
        "id": "resp_jsonb_order",
        "model": DEFAULT_OPENAI_MODEL,
        "status": "completed",
        "output": [],
        "usage": {
            "input_tokens": 1,
            "output_tokens": 1,
            "total_tokens": 2,
        },
    }
    client = ScriptedClient(response)
    provider = OpenAIResponsesProvider(client=client)
    prepared = provider.prepare_request(
        instructions="Use tools.",
        input_items=[{"role": "user", "content": "Synthetic request"}],
        tools=[_tool()],
        final_schema_name="parceldesk_resolution",
        final_schema=FINAL_SCHEMA,
    )
    jsonb_round_trip = json.loads(json.dumps(prepared, sort_keys=True))

    provider.send_prepared(jsonb_round_trip)

    assert len(client.responses.calls) == 1


def test_structured_final_json_and_safe_provider_failure_metadata_are_extracted() -> None:
    completed = {
        "id": "resp_unit_final",
        "model": "gpt-5.6-luna-2026-08-01",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "id": "msg_unit_final",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": '{"status":"resolved","action":"refund_issued"}',
                        "annotations": [],
                    }
                ],
            }
        ],
        "usage": None,
        "error": None,
        "incomplete_details": None,
    }
    failed = {
        "id": "resp_unit_failed",
        "model": "gpt-5.6-luna-2026-08-01",
        "status": "failed",
        "output": [],
        "usage": None,
        "error": {
            "code": "server_error",
            "message": "sensitive provider detail must not be retained",
        },
        "incomplete_details": None,
    }
    client = ScriptedClient(completed, failed)
    provider = OpenAIResponsesProvider(client=client, reasoning_effort="none")
    kwargs = {
        "instructions": "Use tools and return a resolution.",
        "input_items": [{"role": "user", "content": "Synthetic request"}],
        "tools": [_tool()],
        "final_schema_name": "parceldesk_resolution",
        "final_schema": FINAL_SCHEMA,
    }

    final_turn = provider.create_turn(**kwargs)
    failed_turn = provider.create_turn(**kwargs)

    assert client.responses.calls[0]["reasoning"] == {"effort": "none"}
    assert final_turn.final_json == {
        "status": "resolved",
        "action": "refund_issued",
    }
    assert final_turn.function_calls == ()
    assert failed_turn.status == "failed"
    assert failed_turn.failure_code == "server_error"
    assert failed_turn.output_items == ()
    assert "sensitive" not in repr(failed_turn)


def test_transport_errors_are_redacted_and_never_retried() -> None:
    secret = "unit-secret-never-echo@example.test"
    client = ScriptedClient(RuntimeError(f"request body and credential: {secret}"))
    provider = OpenAIResponsesProvider(client=client)

    with pytest.raises(OpenAIProviderRequestError) as captured:
        provider.create_turn(
            instructions="Use tools.",
            input_items=[{"role": "user", "content": "Synthetic request"}],
            tools=[_tool()],
            final_schema_name="parceldesk_resolution",
            final_schema=FINAL_SCHEMA,
        )

    assert len(client.responses.calls) == 1
    assert captured.value.kind == "unexpected"
    assert secret not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_malformed_outputs_fail_closed_without_echoing_provider_text() -> None:
    sensitive_text = "malformed unit output with private@example.test"
    request_secret = "request-secret-not-evidence@example.test"
    response = {
        "id": "resp_unit_malformed",
        "_request_id": "req_unit_malformed",
        "model": "gpt-5.6-luna-2026-08-01",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": sensitive_text}],
            }
        ],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 4,
            "total_tokens": 14,
        },
    }
    provider = OpenAIResponsesProvider(client=ScriptedClient(response))

    with pytest.raises(OpenAIProviderProtocolError) as captured:
        provider.create_turn(
            instructions=f"Use tools. {request_secret}",
            input_items=[{"role": "user", "content": "Synthetic request"}],
            tools=[_tool()],
            final_schema_name="parceldesk_resolution",
            final_schema=FINAL_SCHEMA,
        )

    assert sensitive_text not in str(captured.value)
    assert request_secret not in str(captured.value)
    evidence = captured.value.evidence
    assert evidence is not None
    assert evidence.as_json() == {
        "request_id": "req_unit_malformed",
        "response_id": "resp_unit_malformed",
        "model": "gpt-5.6-luna-2026-08-01",
        "status": "completed",
        "output": response["output"],
        "usage": response["usage"],
    }
    assert request_secret not in repr(evidence)
    assert set(evidence.as_json()) == {
        "request_id",
        "response_id",
        "model",
        "status",
        "output",
        "usage",
    }


@pytest.mark.parametrize(
    "usage",
    [
        {
            "input_tokens": 10,
            "input_tokens_details": {"cached_tokens": 11},
            "output_tokens": 4,
            "total_tokens": 14,
        },
        {
            "input_tokens": 10,
            "output_tokens": 4,
            "output_tokens_details": {"reasoning_tokens": 5},
            "total_tokens": 14,
        },
        {"input_tokens": 10, "output_tokens": 4, "total_tokens": 13},
        {
            "input_tokens": 2_147_483_648,
            "output_tokens": 0,
            "total_tokens": 2_147_483_648,
        },
    ],
)
def test_relationally_invalid_usage_is_protocol_evidence(
    usage: dict[str, Any],
) -> None:
    response = {
        "id": "resp_invalid_usage",
        "model": DEFAULT_OPENAI_MODEL,
        "status": "completed",
        "output": [],
        "usage": usage,
    }
    provider = OpenAIResponsesProvider(client=ScriptedClient(response))

    with pytest.raises(OpenAIProviderProtocolError) as captured:
        provider.create_turn(
            instructions="Use tools.",
            input_items=[{"role": "user", "content": "Synthetic request"}],
            tools=[_tool()],
            final_schema_name="parceldesk_resolution",
            final_schema=FINAL_SCHEMA,
        )

    assert captured.value.evidence is not None
    assert captured.value.evidence.usage == usage


@pytest.mark.parametrize(
    ("model", "call_id"),
    [
        ("m" * 201, "call_ok"),
        (DEFAULT_OPENAI_MODEL, "c" * 101),
    ],
)
def test_provider_identifiers_cannot_exceed_persisted_column_bounds(
    model: str,
    call_id: str,
) -> None:
    response = {
        "id": "resp_identifier_bound",
        "model": model,
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "call_id": call_id,
                "name": "update_shipping_address",
                "arguments": '{"order_id":"PD-1","address":{"line1":"1 Test Way","line2":null}}',
            }
        ],
        "usage": None,
    }
    provider = OpenAIResponsesProvider(client=ScriptedClient(response))

    with pytest.raises(OpenAIProviderProtocolError):
        provider.create_turn(
            instructions="Use tools.",
            input_items=[{"role": "user", "content": "Synthetic request"}],
            tools=[_tool()],
            final_schema_name="parceldesk_resolution",
            final_schema=FINAL_SCHEMA,
        )


def test_retry_and_cost_bounds_are_configuration_invariants() -> None:
    response = {
        "id": "resp_unused",
        "model": "gpt-5.6-luna-2026-08-01",
        "status": "completed",
        "output": [],
        "usage": None,
    }

    with pytest.raises(OpenAIProviderConfigurationError, match="max_retries=0"):
        OpenAIResponsesProvider(client=ScriptedClient(response, max_retries=2))
    with pytest.raises(OpenAIProviderConfigurationError, match="max_output_tokens"):
        OpenAIResponsesProvider(
            client=ScriptedClient(response),
            max_output_tokens=MAX_OUTPUT_TOKENS + 1,
        )
    with pytest.raises(OpenAIProviderConfigurationError, match=DEFAULT_OPENAI_MODEL):
        OpenAIResponsesProvider(client=ScriptedClient(response), model="unapproved-model")


def test_client_factory_receives_retry_free_options_without_network() -> None:
    captured: dict[str, Any] = {}
    sentinel_client = object()

    def factory(**kwargs: Any) -> object:
        captured.update(kwargs)
        return sentinel_client

    client = create_openai_client(
        api_key="unit-test-placeholder",
        timeout_seconds=12.5,
        client_factory=factory,
    )

    assert client is sentinel_client
    assert captured == {
        "api_key": "unit-test-placeholder",
        "max_retries": 0,
        "timeout": 12.5,
    }
