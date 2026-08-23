"""Key-safe, stateless OpenAI Responses API transport primitives.

This module deliberately owns only one network turn. Durable orchestration, tool
execution, claim fencing, and retries belong outside the adapter so an ambiguous
network outcome can never silently repeat a state-changing tool call.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal, Self

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

OPENAI_RESPONSES_PROVIDER = "openai-responses"
DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"
DEFAULT_MAX_OUTPUT_TOKENS = 1024
MAX_OUTPUT_TOKENS = 4096
MAX_PERSISTED_TOKEN_COUNT = 2_147_483_647
DEFAULT_TIMEOUT_SECONDS = 45.0

ReasoningEffort = Literal["none", "low"]
_NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_SAFE_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{1,255}\Z")
_SAFE_CALL_ID_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{1,100}\Z")
_SAFE_MODEL_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{1,200}\Z")
_RESPONSE_STATUSES = frozenset(
    {"completed", "failed", "in_progress", "cancelled", "queued", "incomplete"}
)


class OpenAIProviderConfigurationError(ValueError):
    """The local adapter configuration cannot satisfy its safety contract."""


class OpenAIProviderProtocolError(RuntimeError):
    """The provider response does not match the evidence contract."""

    def __init__(
        self,
        message: str,
        *,
        evidence: ResponsesEvidenceEnvelope | None = None,
    ) -> None:
        self.evidence = evidence
        super().__init__(message)


class OpenAIProviderRequestError(RuntimeError):
    """A redacted network/API failure from the single permitted SDK attempt."""

    def __init__(
        self,
        *,
        kind: str,
        status_code: int | None = None,
        request_id: str | None = None,
    ) -> None:
        self.kind = kind
        self.status_code = status_code
        self.request_id = request_id
        qualifiers = [kind]
        if status_code is not None:
            qualifiers.append(f"status={status_code}")
        if request_id is not None:
            qualifiers.append(f"request_id={request_id}")
        super().__init__("OpenAI request failed (" + "; ".join(qualifiers) + ")")


@dataclass(frozen=True)
class FunctionTool:
    """One strict Responses function tool definition."""

    name: str
    description: str
    parameters: Mapping[str, Any]


@dataclass(frozen=True)
class FunctionCall:
    """One normalized function request emitted by the model."""

    call_id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str


@dataclass(frozen=True)
class TokenUsage:
    """Measured provider token usage; absent usage is represented by ``None``."""

    input_tokens: int
    output_tokens: int
    total_tokens: int
    cached_input_tokens: int | None
    reasoning_output_tokens: int | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class ResponsesEvidenceEnvelope:
    """Sanitized purchased response fields retained after protocol failure."""

    request_id: str | None
    response_id: str | None
    model: str | None
    status: str | None
    output: tuple[Any, ...] | None
    usage: dict[str, Any] | None

    def as_json(self) -> dict[str, Any]:
        """Return a detached JSON-ready representation for durable evidence."""

        return {
            "request_id": self.request_id,
            "response_id": self.response_id,
            "model": self.model,
            "status": self.status,
            "output": deepcopy(list(self.output)) if self.output is not None else None,
            "usage": deepcopy(self.usage),
        }


@dataclass(frozen=True)
class ResponsesTurn:
    """Normalized evidence returned from one stateless Responses call."""

    response_id: str
    request_id: str | None
    model: str
    status: str
    output_items: tuple[dict[str, Any], ...]
    function_calls: tuple[FunctionCall, ...]
    final_json: dict[str, Any] | None
    usage: TokenUsage | None
    failure_code: str | None
    incomplete_reason: str | None


def create_openai_client(
    *,
    api_key: str,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    client_factory: Callable[..., Any] = OpenAI,
) -> Any:
    """Construct the SDK client without retaining or logging the supplied key here."""

    if (
        not api_key
        or api_key != api_key.strip()
        or any(ord(character) < 32 for character in api_key)
    ):
        raise OpenAIProviderConfigurationError("A non-empty OpenAI API key is required")
    if timeout_seconds <= 0:
        raise OpenAIProviderConfigurationError("The OpenAI timeout must be positive")
    return client_factory(
        api_key=api_key,
        max_retries=0,
        timeout=timeout_seconds,
    )


def _allows_null(schema: Mapping[str, Any]) -> bool:
    schema_type = schema.get("type")
    if schema_type == "null":
        return True
    if isinstance(schema_type, list) and "null" in schema_type:
        return True
    if schema.get("const", object()) is None:
        return True
    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and None in enum_values:
        return True
    for keyword in ("anyOf",):
        variants = schema.get(keyword)
        if isinstance(variants, list) and any(
            isinstance(variant, Mapping) and _allows_null(variant) for variant in variants
        ):
            return True
    return False


def _make_nullable(schema: dict[str, Any]) -> dict[str, Any]:
    if _allows_null(schema):
        return schema
    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        schema["type"] = [schema_type, "null"]
        return schema
    if isinstance(schema_type, list):
        schema["type"] = [*schema_type, "null"]
        return schema
    return {"anyOf": [schema, {"type": "null"}]}


def _stricten_schema(schema: dict[str, Any]) -> None:
    schema.pop("default", None)
    unsupported_composition = next(
        (keyword for keyword in ("allOf", "oneOf") if keyword in schema),
        None,
    )
    if unsupported_composition is not None:
        raise OpenAIProviderConfigurationError(
            f"Strict OpenAI schemas do not support {unsupported_composition} composition"
        )
    properties = schema.get("properties")
    is_object = (
        properties is not None
        or schema.get("type") == "object"
        or (isinstance(schema.get("type"), list) and "object" in schema["type"])
    )
    if is_object:
        if properties is None:
            properties = {}
            schema["properties"] = properties
        if not isinstance(properties, dict) or not all(
            isinstance(name, str) and isinstance(value, dict) for name, value in properties.items()
        ):
            raise OpenAIProviderConfigurationError(
                "Strict JSON Schema object properties must be named schema objects"
            )
        original_required = schema.get("required", [])
        if not isinstance(original_required, list) or not all(
            isinstance(name, str) for name in original_required
        ):
            raise OpenAIProviderConfigurationError(
                "Strict JSON Schema required must be a string array"
            )
        originally_required = set(original_required)
        for property_name, property_schema in list(properties.items()):
            _stricten_schema(property_schema)
            if property_name not in originally_required:
                properties[property_name] = _make_nullable(property_schema)
        schema["required"] = list(properties)
        schema["additionalProperties"] = False

    for definitions_key in ("$defs", "definitions"):
        definitions = schema.get(definitions_key)
        if definitions is not None:
            if not isinstance(definitions, dict):
                raise OpenAIProviderConfigurationError(
                    "Strict JSON Schema definitions must be an object"
                )
            for definition in definitions.values():
                if not isinstance(definition, dict):
                    raise OpenAIProviderConfigurationError(
                        "Strict JSON Schema definitions must contain schema objects"
                    )
                _stricten_schema(definition)

    items = schema.get("items")
    if isinstance(items, dict):
        _stricten_schema(items)
    elif isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                raise OpenAIProviderConfigurationError(
                    "Strict JSON Schema tuple items must be schema objects"
                )
            _stricten_schema(item)

    for keyword in ("anyOf",):
        variants = schema.get(keyword)
        if variants is not None:
            if not isinstance(variants, list) or not all(
                isinstance(variant, dict) for variant in variants
            ):
                raise OpenAIProviderConfigurationError(
                    f"Strict JSON Schema {keyword} must contain schema objects"
                )
            for variant in variants:
                _stricten_schema(variant)


def strict_json_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Copy and normalize a schema for strict Responses tools/output.

    Every object rejects unknown keys and requires every declared property. Fields
    that were optional by omission become required nullable fields, preserving the
    semantic distinction through an explicit JSON ``null``.
    """

    normalized = _normalize_mapping(schema, error_kind="configuration")
    _stricten_schema(normalized)
    return normalized


def function_call_output(call_id: str, output: Mapping[str, Any]) -> dict[str, Any]:
    """Build a deterministic stateless input item for one persisted tool result."""

    if _safe_call_id(call_id) is None:
        raise OpenAIProviderConfigurationError("Function call IDs must be safe identifiers")
    normalized_output = _normalize_mapping(output, error_kind="configuration")
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": json.dumps(normalized_output, sort_keys=True, separators=(",", ":")),
    }


class OpenAIResponsesProvider:
    """Make exactly one retry-free, non-stored Responses API request per call."""

    name = OPENAI_RESPONSES_PROVIDER

    def __init__(
        self,
        *,
        client: Any,
        model: str = DEFAULT_OPENAI_MODEL,
        reasoning_effort: ReasoningEffort = "low",
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if getattr(client, "max_retries", None) != 0:
            raise OpenAIProviderConfigurationError("The OpenAI SDK client must set max_retries=0")
        if model != DEFAULT_OPENAI_MODEL:
            raise OpenAIProviderConfigurationError(
                f"The approved OpenAI model is {DEFAULT_OPENAI_MODEL}"
            )
        if reasoning_effort not in {"none", "low"}:
            raise OpenAIProviderConfigurationError("OpenAI reasoning effort must be none or low")
        if not 1 <= max_output_tokens <= MAX_OUTPUT_TOKENS:
            raise OpenAIProviderConfigurationError(
                f"OpenAI max_output_tokens must be between 1 and {MAX_OUTPUT_TOKENS}"
            )
        if timeout_seconds <= 0:
            raise OpenAIProviderConfigurationError("The OpenAI timeout must be positive")
        self._client = client
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.max_output_tokens = max_output_tokens
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_api_key(
        cls,
        *,
        api_key: str,
        reasoning_effort: ReasoningEffort = "low",
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> Self:
        client = create_openai_client(
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        )
        return cls(
            client=client,
            reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
        )

    def prepare_request(
        self,
        *,
        instructions: str,
        input_items: Sequence[Mapping[str, Any]],
        tools: Sequence[FunctionTool],
        final_schema_name: str,
        final_schema: Mapping[str, Any],
        final_schema_description: str = "ParcelDesk structured final resolution",
    ) -> dict[str, Any]:
        """Build the exact JSON request DTO that must be committed before send."""

        if not instructions.strip():
            raise OpenAIProviderConfigurationError("OpenAI instructions must not be empty")
        if not input_items:
            raise OpenAIProviderConfigurationError("OpenAI input must not be empty")
        if not tools:
            raise OpenAIProviderConfigurationError("At least one function tool is required")
        if _NAME_PATTERN.fullmatch(final_schema_name) is None:
            raise OpenAIProviderConfigurationError("The final schema name is invalid")
        if not final_schema_description.strip():
            raise OpenAIProviderConfigurationError("The final schema description must not be empty")

        normalized_input = [
            _normalize_mapping(item, error_kind="configuration") for item in input_items
        ]
        tool_payloads: list[dict[str, Any]] = []
        seen_tool_names: set[str] = set()
        for tool in tools:
            if _NAME_PATTERN.fullmatch(tool.name) is None or tool.name in seen_tool_names:
                raise OpenAIProviderConfigurationError("Function tool names must be unique")
            if not tool.description.strip():
                raise OpenAIProviderConfigurationError(
                    "Function tool descriptions must not be empty"
                )
            seen_tool_names.add(tool.name)
            tool_payloads.append(
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": strict_json_schema(tool.parameters),
                    "strict": True,
                }
            )

        request: dict[str, Any] = {
            "model": self.model,
            "instructions": instructions,
            "input": normalized_input,
            "tools": tool_payloads,
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": final_schema_name,
                    "description": final_schema_description,
                    "schema": strict_json_schema(final_schema),
                    "strict": True,
                },
                "verbosity": "low",
            },
            "reasoning": {"effort": self.reasoning_effort},
            "max_output_tokens": self.max_output_tokens,
            "store": False,
            "stream": False,
            "background": False,
            "truncation": "disabled",
            "service_tier": "default",
            "include": ["reasoning.encrypted_content"],
        }
        return request

    def send_prepared(self, request: Mapping[str, Any]) -> ResponsesTurn:
        """Validate and send one previously persisted request DTO exactly once."""

        prepared = self.validate_prepared(request)

        response: Any | None = None
        request_error: OpenAIProviderRequestError | None = None
        try:
            # Timeout is an SDK transport option, deliberately excluded from the
            # durable Responses API payload. Every API field is passed unchanged.
            response = self._client.responses.create(
                **prepared,
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            request_error = _redacted_request_error(exc)
        if request_error is not None:
            # Raise outside the handler so the original exception is not retained as
            # context and cannot leak its request body, headers, or credential text.
            raise request_error from None
        if response is None:
            raise OpenAIProviderProtocolError("OpenAI returned no response object")
        return _extract_turn_with_evidence(response)

    def validate_prepared(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Purely validate a durable request before crossing the network send fence."""

        return _validate_prepared_request(
            request,
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            max_output_tokens=self.max_output_tokens,
        )

    def create_turn(
        self,
        *,
        instructions: str,
        input_items: Sequence[Mapping[str, Any]],
        tools: Sequence[FunctionTool],
        final_schema_name: str,
        final_schema: Mapping[str, Any],
        final_schema_description: str = "ParcelDesk structured final resolution",
    ) -> ResponsesTurn:
        """Convenience wrapper for callers that do not need a durable send fence."""

        request = self.prepare_request(
            instructions=instructions,
            input_items=input_items,
            tools=tools,
            final_schema_name=final_schema_name,
            final_schema=final_schema,
            final_schema_description=final_schema_description,
        )
        return self.send_prepared(request)


def _safe_identifier(value: Any) -> str | None:
    if isinstance(value, str) and _SAFE_IDENTIFIER_PATTERN.fullmatch(value) is not None:
        return value
    return None


def _safe_call_id(value: Any) -> str | None:
    if isinstance(value, str) and _SAFE_CALL_ID_PATTERN.fullmatch(value) is not None:
        return value
    return None


def _safe_model(value: Any) -> str | None:
    if isinstance(value, str) and _SAFE_MODEL_PATTERN.fullmatch(value) is not None:
        return value
    return None


def _safe_status_code(value: Any) -> int | None:
    return value if isinstance(value, int) and 100 <= value <= 599 else None


def _redacted_request_error(exc: Exception) -> OpenAIProviderRequestError:
    status_code = _safe_status_code(getattr(exc, "status_code", None))
    request_id = _safe_identifier(getattr(exc, "request_id", None))
    if isinstance(exc, AuthenticationError) or status_code in {401, 403}:
        kind = "authentication"
    elif isinstance(exc, RateLimitError) or status_code == 429:
        kind = "rate_limit"
    elif isinstance(exc, APITimeoutError):
        kind = "timeout"
    elif isinstance(exc, APIConnectionError):
        kind = "connection"
    elif isinstance(exc, APIStatusError) or status_code is not None:
        kind = "api_status"
    else:
        kind = "unexpected"
    return OpenAIProviderRequestError(
        kind=kind,
        status_code=status_code,
        request_id=request_id,
    )


def _normalize_json(value: Any, *, error_kind: Literal["configuration", "protocol"]) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        value = model_dump(mode="json", exclude_none=False)
    try:
        serialized = json.dumps(value, allow_nan=False, separators=(",", ":"))
        return json.loads(serialized)
    except (TypeError, ValueError):
        if error_kind == "configuration":
            raise OpenAIProviderConfigurationError(
                "OpenAI request values must be JSON serializable"
            ) from None
        raise OpenAIProviderProtocolError(
            "OpenAI response values must be JSON serializable"
        ) from None


def _normalize_mapping(
    value: Mapping[str, Any] | Any,
    *,
    error_kind: Literal["configuration", "protocol"],
) -> dict[str, Any]:
    normalized = _normalize_json(deepcopy(value), error_kind=error_kind)
    if not isinstance(normalized, dict) or not all(isinstance(key, str) for key in normalized):
        if error_kind == "configuration":
            raise OpenAIProviderConfigurationError("OpenAI request objects must be mappings")
        raise OpenAIProviderProtocolError("OpenAI response items must be objects")
    return normalized


_PREPARED_REQUEST_KEYS = frozenset(
    {
        "model",
        "instructions",
        "input",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "text",
        "reasoning",
        "max_output_tokens",
        "store",
        "stream",
        "background",
        "truncation",
        "service_tier",
        "include",
    }
)
_FUNCTION_TOOL_KEYS = frozenset({"type", "name", "description", "parameters", "strict"})
_TEXT_KEYS = frozenset({"format", "verbosity"})
_TEXT_FORMAT_KEYS = frozenset({"type", "name", "description", "schema", "strict"})


def _require_exact_keys(
    value: Mapping[str, Any],
    expected: frozenset[str],
    *,
    label: str,
) -> None:
    if frozenset(value) != expected:
        raise OpenAIProviderConfigurationError(
            f"The prepared OpenAI {label} has an invalid field set"
        )


def _require_nonempty_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OpenAIProviderConfigurationError(
            f"The prepared OpenAI {label} must be a non-empty string"
        )
    return value


def _validate_strict_prepared_schema(schema: Any, *, label: str) -> None:
    if not isinstance(schema, dict):
        raise OpenAIProviderConfigurationError(
            f"The prepared OpenAI {label} must be a JSON Schema object"
        )

    unsupported_composition = next(
        (keyword for keyword in ("allOf", "oneOf") if keyword in schema),
        None,
    )
    if unsupported_composition is not None:
        raise OpenAIProviderConfigurationError(
            f"The prepared OpenAI {label} uses unsupported {unsupported_composition} composition"
        )

    properties = schema.get("properties")
    schema_type = schema.get("type")
    is_object = (
        properties is not None
        or schema_type == "object"
        or (isinstance(schema_type, list) and "object" in schema_type)
    )
    if is_object:
        if not isinstance(properties, dict) or not all(
            isinstance(name, str) and isinstance(value, dict) for name, value in properties.items()
        ):
            raise OpenAIProviderConfigurationError(
                f"The prepared OpenAI {label} has invalid object properties"
            )
        if schema.get("additionalProperties") is not False:
            raise OpenAIProviderConfigurationError(
                f"The prepared OpenAI {label} must reject additional properties"
            )
        required = schema.get("required")
        if (
            not isinstance(required, list)
            or not all(isinstance(name, str) for name in required)
            or len(required) != len(properties)
            or set(required) != set(properties)
        ):
            raise OpenAIProviderConfigurationError(
                f"The prepared OpenAI {label} must require every object property"
            )
        for property_schema in properties.values():
            _validate_strict_prepared_schema(property_schema, label=label)

    for definitions_key in ("$defs", "definitions"):
        definitions = schema.get(definitions_key)
        if definitions is not None:
            if not isinstance(definitions, dict):
                raise OpenAIProviderConfigurationError(
                    f"The prepared OpenAI {label} has invalid definitions"
                )
            for definition in definitions.values():
                _validate_strict_prepared_schema(definition, label=label)

    items = schema.get("items")
    if isinstance(items, dict):
        _validate_strict_prepared_schema(items, label=label)
    elif isinstance(items, list):
        for item in items:
            _validate_strict_prepared_schema(item, label=label)
    elif items is not None:
        raise OpenAIProviderConfigurationError(
            f"The prepared OpenAI {label} has invalid item schemas"
        )

    for keyword in ("anyOf",):
        variants = schema.get(keyword)
        if variants is not None:
            if not isinstance(variants, list):
                raise OpenAIProviderConfigurationError(
                    f"The prepared OpenAI {label} has invalid {keyword} schemas"
                )
            for variant in variants:
                _validate_strict_prepared_schema(variant, label=label)


def _validate_prepared_request(
    request: Mapping[str, Any],
    *,
    model: str,
    reasoning_effort: ReasoningEffort,
    max_output_tokens: int,
) -> dict[str, Any]:
    prepared = _normalize_mapping(request, error_kind="configuration")
    _require_exact_keys(prepared, _PREPARED_REQUEST_KEYS, label="request")

    if prepared["model"] != model or model != DEFAULT_OPENAI_MODEL:
        raise OpenAIProviderConfigurationError("The prepared OpenAI model is invalid")
    _require_nonempty_string(prepared["instructions"], label="instructions")

    input_items = prepared["input"]
    if (
        not isinstance(input_items, list)
        or not input_items
        or not all(isinstance(item, dict) for item in input_items)
    ):
        raise OpenAIProviderConfigurationError(
            "The prepared OpenAI input must be a non-empty object array"
        )

    tools = prepared["tools"]
    if not isinstance(tools, list) or not tools:
        raise OpenAIProviderConfigurationError(
            "The prepared OpenAI tools must be a non-empty array"
        )
    tool_names: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict):
            raise OpenAIProviderConfigurationError(
                "The prepared OpenAI function tools must be objects"
            )
        _require_exact_keys(tool, _FUNCTION_TOOL_KEYS, label="function tool")
        name = tool["name"]
        if not isinstance(name, str) or _NAME_PATTERN.fullmatch(name) is None or name in tool_names:
            raise OpenAIProviderConfigurationError(
                "The prepared OpenAI function tool names must be unique"
            )
        if tool["type"] != "function" or tool["strict"] is not True:
            raise OpenAIProviderConfigurationError(
                "The prepared OpenAI function tool contract is invalid"
            )
        _require_nonempty_string(tool["description"], label="function tool description")
        _validate_strict_prepared_schema(
            tool["parameters"],
            label="function tool schema",
        )
        tool_names.add(name)

    if prepared["tool_choice"] != "auto" or prepared["parallel_tool_calls"] is not False:
        raise OpenAIProviderConfigurationError("The prepared OpenAI tool-call controls are invalid")

    text = prepared["text"]
    if not isinstance(text, dict):
        raise OpenAIProviderConfigurationError("The prepared OpenAI text contract is invalid")
    _require_exact_keys(text, _TEXT_KEYS, label="text contract")
    if text["verbosity"] != "low":
        raise OpenAIProviderConfigurationError("The prepared OpenAI verbosity is invalid")
    output_format = text["format"]
    if not isinstance(output_format, dict):
        raise OpenAIProviderConfigurationError("The prepared OpenAI output format is invalid")
    _require_exact_keys(output_format, _TEXT_FORMAT_KEYS, label="output format")
    schema_name = output_format["name"]
    if not isinstance(schema_name, str) or _NAME_PATTERN.fullmatch(schema_name) is None:
        raise OpenAIProviderConfigurationError("The prepared OpenAI output schema name is invalid")
    if output_format["type"] != "json_schema" or output_format["strict"] is not True:
        raise OpenAIProviderConfigurationError(
            "The prepared OpenAI structured-output contract is invalid"
        )
    _require_nonempty_string(
        output_format["description"],
        label="output schema description",
    )
    _validate_strict_prepared_schema(
        output_format["schema"],
        label="output schema",
    )

    if prepared["reasoning"] != {"effort": reasoning_effort}:
        raise OpenAIProviderConfigurationError(
            "The prepared OpenAI reasoning configuration is invalid"
        )
    token_limit = prepared["max_output_tokens"]
    if (
        not isinstance(token_limit, int)
        or isinstance(token_limit, bool)
        or token_limit != max_output_tokens
        or not 1 <= token_limit <= MAX_OUTPUT_TOKENS
    ):
        raise OpenAIProviderConfigurationError("The prepared OpenAI output-token bound is invalid")
    if any(prepared[name] is not False for name in ("store", "stream", "background")):
        raise OpenAIProviderConfigurationError(
            "The prepared OpenAI persistence or streaming controls are invalid"
        )
    if prepared["truncation"] != "disabled" or prepared["service_tier"] != "default":
        raise OpenAIProviderConfigurationError("The prepared OpenAI delivery controls are invalid")
    if prepared["include"] != ["reasoning.encrypted_content"]:
        raise OpenAIProviderConfigurationError(
            "The prepared OpenAI stateless-reasoning contract is invalid"
        )
    return prepared


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _safe_response_field(value: Any, name: str) -> Any:
    try:
        return _field(value, name)
    except Exception:
        return None


def _safe_normalized_output(value: Any) -> tuple[Any, ...] | None:
    if not isinstance(value, (list, tuple)):
        return None
    normalized: list[Any] = []
    try:
        for item in value:
            normalized.append(_normalize_json(item, error_kind="protocol"))
    except Exception:
        return None
    return tuple(normalized)


def _safe_normalized_usage(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        return _normalize_mapping(value, error_kind="protocol")
    except Exception:
        return None


def _response_evidence(response: Any) -> ResponsesEvidenceEnvelope:
    """Select only safe response fields; never retain request or transport state."""

    return ResponsesEvidenceEnvelope(
        request_id=_safe_identifier(_safe_response_field(response, "_request_id")),
        response_id=_safe_identifier(_safe_response_field(response, "id")),
        model=_safe_model(_safe_response_field(response, "model")),
        status=_safe_identifier(_safe_response_field(response, "status")),
        output=_safe_normalized_output(_safe_response_field(response, "output")),
        usage=_safe_normalized_usage(_safe_response_field(response, "usage")),
    )


def _nonnegative_token(value: Any, *, required: bool) -> int | None:
    if value is None and not required:
        return None
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        or value > MAX_PERSISTED_TOKEN_COUNT
    ):
        raise OpenAIProviderProtocolError("OpenAI usage contains an invalid token count")
    return value


def _extract_usage(usage: Any) -> TokenUsage | None:
    if usage is None:
        return None
    raw = _normalize_mapping(usage, error_kind="protocol")
    input_tokens = _nonnegative_token(raw.get("input_tokens"), required=True)
    output_tokens = _nonnegative_token(raw.get("output_tokens"), required=True)
    total_tokens = _nonnegative_token(raw.get("total_tokens"), required=True)
    assert input_tokens is not None
    assert output_tokens is not None
    assert total_tokens is not None
    input_details = raw.get("input_tokens_details")
    output_details = raw.get("output_tokens_details")
    cached_tokens = (
        _nonnegative_token(input_details.get("cached_tokens"), required=False)
        if isinstance(input_details, dict)
        else None
    )
    reasoning_tokens = (
        _nonnegative_token(output_details.get("reasoning_tokens"), required=False)
        if isinstance(output_details, dict)
        else None
    )
    if cached_tokens is not None and cached_tokens > input_tokens:
        raise OpenAIProviderProtocolError("OpenAI cached input tokens exceed total input tokens")
    if reasoning_tokens is not None and reasoning_tokens > output_tokens:
        raise OpenAIProviderProtocolError("OpenAI reasoning tokens exceed total output tokens")
    if total_tokens < input_tokens + output_tokens:
        raise OpenAIProviderProtocolError("OpenAI total tokens are below input plus output tokens")
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cached_input_tokens=cached_tokens,
        reasoning_output_tokens=reasoning_tokens,
        raw=raw,
    )


def _extract_function_calls(
    output_items: tuple[dict[str, Any], ...],
) -> tuple[FunctionCall, ...]:
    calls: list[FunctionCall] = []
    call_ids: set[str] = set()
    for item in output_items:
        if item.get("type") != "function_call":
            continue
        call_id = _safe_call_id(item.get("call_id"))
        name = item.get("name")
        raw_arguments_value = item.get("arguments")
        if call_id is None or not isinstance(name, str) or _NAME_PATTERN.fullmatch(name) is None:
            raise OpenAIProviderProtocolError("OpenAI returned an invalid function call identity")
        if call_id in call_ids:
            raise OpenAIProviderProtocolError("OpenAI returned a duplicate function call ID")
        if isinstance(raw_arguments_value, str):
            raw_arguments = raw_arguments_value
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError:
                raise OpenAIProviderProtocolError(
                    "OpenAI returned invalid function arguments"
                ) from None
        elif isinstance(raw_arguments_value, dict):
            arguments = raw_arguments_value
            raw_arguments = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
        else:
            raise OpenAIProviderProtocolError("OpenAI returned invalid function arguments")
        if not isinstance(arguments, dict) or not all(isinstance(key, str) for key in arguments):
            raise OpenAIProviderProtocolError("OpenAI function arguments must be an object")
        calls.append(
            FunctionCall(
                call_id=call_id,
                name=name,
                arguments=_normalize_mapping(arguments, error_kind="protocol"),
                raw_arguments=raw_arguments,
            )
        )
        call_ids.add(call_id)
    if len(calls) > 1:
        raise OpenAIProviderProtocolError(
            "OpenAI returned multiple calls while parallel tool calls were disabled"
        )
    return tuple(calls)


def _extract_final_json(output_items: tuple[dict[str, Any], ...]) -> dict[str, Any] | None:
    output_texts: list[str] = []
    for item in output_items:
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            raise OpenAIProviderProtocolError("OpenAI returned invalid message content")
        for part in content:
            if not isinstance(part, dict):
                raise OpenAIProviderProtocolError("OpenAI returned invalid message content")
            if part.get("type") == "output_text":
                text = part.get("text")
                if not isinstance(text, str) or not text:
                    raise OpenAIProviderProtocolError("OpenAI returned invalid structured output")
                output_texts.append(text)
    if not output_texts:
        return None
    if len(output_texts) != 1:
        raise OpenAIProviderProtocolError("OpenAI returned multiple structured outputs")
    try:
        parsed = json.loads(output_texts[0])
    except json.JSONDecodeError:
        raise OpenAIProviderProtocolError("OpenAI returned invalid structured output") from None
    if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
        raise OpenAIProviderProtocolError("OpenAI structured output must be an object")
    return _normalize_mapping(parsed, error_kind="protocol")


def _extract_safe_code(value: Any) -> str | None:
    if isinstance(value, Mapping):
        return _safe_identifier(value.get("code"))
    return _safe_identifier(getattr(value, "code", None))


def _extract_incomplete_reason(value: Any) -> str | None:
    if isinstance(value, Mapping):
        return _safe_identifier(value.get("reason"))
    return _safe_identifier(getattr(value, "reason", None))


def _extract_turn(response: Any) -> ResponsesTurn:
    response_id = _safe_identifier(_field(response, "id"))
    model = _safe_model(_field(response, "model"))
    status = _field(response, "status")
    if response_id is None:
        raise OpenAIProviderProtocolError("OpenAI returned an invalid response ID")
    if model is None:
        raise OpenAIProviderProtocolError("OpenAI returned an invalid model identity")
    if not isinstance(status, str) or status not in _RESPONSE_STATUSES:
        raise OpenAIProviderProtocolError("OpenAI returned an invalid response status")
    raw_output = _field(response, "output")
    if not isinstance(raw_output, (list, tuple)):
        raise OpenAIProviderProtocolError("OpenAI returned invalid output items")
    output_items = tuple(_normalize_mapping(item, error_kind="protocol") for item in raw_output)
    for item in output_items:
        if not isinstance(item.get("type"), str):
            raise OpenAIProviderProtocolError("OpenAI output items require a type")

    function_calls = _extract_function_calls(output_items)
    final_json = _extract_final_json(output_items)
    if function_calls and final_json is not None:
        raise OpenAIProviderProtocolError(
            "OpenAI returned both a function call and a final structured result"
        )
    return ResponsesTurn(
        response_id=response_id,
        request_id=_safe_identifier(_field(response, "_request_id")),
        model=model,
        status=status,
        output_items=output_items,
        function_calls=function_calls,
        final_json=final_json,
        usage=_extract_usage(_field(response, "usage")),
        failure_code=_extract_safe_code(_field(response, "error")),
        incomplete_reason=_extract_incomplete_reason(_field(response, "incomplete_details")),
    )


def _extract_turn_with_evidence(response: Any) -> ResponsesTurn:
    protocol_message: str | None = None
    try:
        return _extract_turn(response)
    except OpenAIProviderProtocolError as exc:
        protocol_message = str(exc)

    assert protocol_message is not None
    raise OpenAIProviderProtocolError(
        protocol_message,
        evidence=_response_evidence(response),
    ) from None
