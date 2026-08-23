"""Immutable, server-owned configuration for the bounded real-provider track."""

from decimal import Decimal
from typing import Any

from evalguard.core.config import Settings
from evalguard.evaluation.suite import definition_hash

OPENAI_PROVIDER = "openai-responses"
OPENAI_MODEL = "gpt-5.6-luna"
OPENAI_INPUT_USD_PER_MILLION = Decimal("0.20")
OPENAI_CACHED_INPUT_USD_PER_MILLION = Decimal("0.02")
OPENAI_OUTPUT_USD_PER_MILLION = Decimal("1.20")
OPENAI_PRICING_AS_OF = "2026-08-23"
OPENAI_PRICING_SOURCE = "https://developers.openai.com/api/docs/models/gpt-5.6-luna"


def build_openai_provider_config(settings: Settings) -> dict[str, Any]:
    """Build the exact non-secret request and pricing identity persisted per experiment."""

    if settings.openai_model != OPENAI_MODEL:
        raise RuntimeError(
            f"Unsupported OpenAI model {settings.openai_model!r}; "
            f"the verified pricing snapshot is for {OPENAI_MODEL!r}"
        )
    if settings.openai_request_timeout_seconds * settings.openai_max_turns > 270:
        raise RuntimeError(
            "OpenAI request timeout multiplied by max turns must not exceed 270 seconds"
        )
    return {
        "api": "responses",
        "model": settings.openai_model,
        "reasoning_effort": settings.openai_reasoning_effort,
        "max_output_tokens": settings.openai_max_output_tokens,
        "max_turns": settings.openai_max_turns,
        "request_timeout_seconds": str(settings.openai_request_timeout_seconds),
        "sdk_max_retries": 0,
        "network_attempts_per_turn": 1,
        "store": False,
        "parallel_tool_calls": False,
        "spend_cap_usd": str(settings.openai_max_experiment_cost_usd),
        "pricing": {
            "currency": "USD",
            "unit_tokens": 1_000_000,
            "input_usd": str(OPENAI_INPUT_USD_PER_MILLION),
            "cached_input_usd": str(OPENAI_CACHED_INPUT_USD_PER_MILLION),
            "output_usd": str(OPENAI_OUTPUT_USD_PER_MILLION),
            "as_of": OPENAI_PRICING_AS_OF,
            "source": OPENAI_PRICING_SOURCE,
        },
    }


def provider_config_hash(config: dict[str, Any]) -> str:
    """Hash only non-secret, canonical request identity."""

    return definition_hash(config)


def estimate_cost_usd(
    *,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
) -> Decimal:
    """Estimate standard API cost from the committed price snapshot, not billing data."""

    uncached_input_tokens = max(0, input_tokens - cached_input_tokens)
    micro = Decimal(1_000_000)
    return (
        Decimal(uncached_input_tokens) * OPENAI_INPUT_USD_PER_MILLION
        + Decimal(cached_input_tokens) * OPENAI_CACHED_INPUT_USD_PER_MILLION
        + Decimal(output_tokens) * OPENAI_OUTPUT_USD_PER_MILLION
    ) / micro
