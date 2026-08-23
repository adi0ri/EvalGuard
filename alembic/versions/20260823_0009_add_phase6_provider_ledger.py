"""Add immutable provider identity and durable provider-turn evidence.

Revision ID: 20260823_0009
Revises: 20260822_0008
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260823_0009"
down_revision: str | None = "20260822_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "experiments",
        sa.Column("provider_model", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "experiments",
        sa.Column(
            "provider_config",
            postgresql.JSONB(astext_type=sa.Text(), none_as_null=True),
            nullable=True,
        ),
    )
    op.add_column(
        "experiments",
        sa.Column("provider_config_hash", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_experiments_provider_identity_complete"),
        "experiments",
        "(provider = 'deterministic-fake' AND provider_model IS NULL "
        "AND provider_config IS NULL AND provider_config_hash IS NULL) OR "
        "(provider = 'openai-responses' AND provider_model IS NOT NULL "
        "AND provider_config IS NOT NULL AND provider_config_hash IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_experiments_provider_model_nonempty"),
        "experiments",
        "provider_model IS NULL OR char_length(provider_model) > 0",
    )
    op.create_check_constraint(
        op.f("ck_experiments_provider_config_object"),
        "experiments",
        "provider_config IS NULL OR jsonb_typeof(provider_config) = 'object'",
    )
    op.create_check_constraint(
        op.f("ck_experiments_provider_config_hash_format"),
        "experiments",
        "provider_config_hash IS NULL OR provider_config_hash ~ '^[0-9a-f]{64}$'",
    )
    op.create_index(
        "ix_experiments_provider_identity",
        "experiments",
        ["provider", "provider_model", "provider_config_hash"],
    )
    op.create_index(
        "uq_experiments_single_openai_run",
        "experiments",
        ["provider"],
        unique=True,
        postgresql_where=sa.text("provider = 'openai-responses'"),
    )
    op.execute(
        """
        CREATE FUNCTION guard_experiment_provider_identity_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.provider IS DISTINCT FROM NEW.provider
               OR OLD.provider_model IS DISTINCT FROM NEW.provider_model
               OR OLD.provider_config IS DISTINCT FROM NEW.provider_config
               OR OLD.provider_config_hash IS DISTINCT FROM NEW.provider_config_hash THEN
                RAISE EXCEPTION 'experiment provider identity is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER experiments_provider_identity_immutable
        BEFORE UPDATE OF provider, provider_model, provider_config, provider_config_hash
        ON experiments
        FOR EACH ROW EXECUTE FUNCTION guard_experiment_provider_identity_mutation()
        """
    )

    op.drop_constraint(
        op.f("ck_execution_attempts_status_allowed"),
        "execution_attempts",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_execution_attempts_status_allowed"),
        "execution_attempts",
        "status IN ("
        "'running', 'succeeded', 'retry_scheduled', 'resume_scheduled', "
        "'failed', 'interrupted'"
        ")",
    )
    op.add_column(
        "execution_attempts",
        sa.Column(
            "recovery_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_execution_attempts_recovery_count_nonnegative"),
        "execution_attempts",
        "recovery_count >= 0",
    )
    op.alter_column("execution_attempts", "recovery_count", server_default=None)

    op.add_column(
        "execution_dispatches",
        sa.Column(
            "delivery_kind",
            sa.String(length=20),
            server_default=sa.text("'attempt'"),
            nullable=False,
        ),
    )
    op.add_column(
        "execution_dispatches",
        sa.Column("attempt_number", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_execution_dispatches_execution_attempt",
        "execution_dispatches",
        "execution_attempts",
        ["case_execution_id", "attempt_number"],
        ["case_execution_id", "attempt_number"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        op.f("ck_execution_dispatches_delivery_kind_allowed"),
        "execution_dispatches",
        "delivery_kind IN ('attempt', 'resume')",
    )
    op.create_check_constraint(
        op.f("ck_execution_dispatches_attempt_number_positive"),
        "execution_dispatches",
        "attempt_number IS NULL OR attempt_number >= 1",
    )
    op.create_check_constraint(
        op.f("ck_execution_dispatches_delivery_identity_coherent"),
        "execution_dispatches",
        "(delivery_kind = 'attempt' AND attempt_number IS NULL) OR "
        "(delivery_kind = 'resume' AND attempt_number IS NOT NULL)",
    )
    op.create_index(
        "ix_execution_dispatches_execution_attempt",
        "execution_dispatches",
        ["case_execution_id", "attempt_number"],
    )
    op.alter_column("execution_dispatches", "delivery_kind", server_default=None)

    op.create_table(
        "provider_turns",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("dispatch_generation", sa.Integer(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("client_request_id", sa.String(length=100), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "request_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("provider_request_id", sa.String(length=255), nullable=True),
        sa.Column("provider_response_id", sa.String(length=255), nullable=True),
        sa.Column("response_model", sa.String(length=200), nullable=True),
        sa.Column(
            "response_payload",
            postgresql.JSONB(astext_type=sa.Text(), none_as_null=True),
            nullable=True,
        ),
        sa.Column(
            "tool_calls",
            postgresql.JSONB(astext_type=sa.Text(), none_as_null=True),
            nullable=True,
        ),
        sa.Column(
            "tool_outputs",
            postgresql.JSONB(astext_type=sa.Text(), none_as_null=True),
            nullable=True,
        ),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd_micros", sa.BigInteger(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("http_status_code", sa.Integer(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        sa.Column("error_kind", sa.String(length=50), nullable=True),
        sa.Column("safe_error_message", sa.Text(), nullable=True),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tools_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "attempt_number >= 1",
            name=op.f("ck_provider_turns_attempt_number_positive"),
        ),
        sa.CheckConstraint(
            "dispatch_generation >= 1",
            name=op.f("ck_provider_turns_dispatch_generation_positive"),
        ),
        sa.CheckConstraint(
            "sequence_number >= 1",
            name=op.f("ck_provider_turns_sequence_number_positive"),
        ),
        sa.CheckConstraint(
            "status IN ("
            "'prepared', 'request_started', 'response_received', 'tools_completed', "
            "'final_response', 'response_invalid', 'failed', 'indeterminate', 'superseded'"
            ")",
            name=op.f("ck_provider_turns_status_allowed"),
        ),
        sa.CheckConstraint(
            "char_length(client_request_id) > 0",
            name=op.f("ck_provider_turns_client_request_id_nonempty"),
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_provider_turns_request_hash_format"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(request_payload) = 'object'",
            name=op.f("ck_provider_turns_request_payload_object"),
        ),
        sa.CheckConstraint(
            "response_payload IS NULL OR jsonb_typeof(response_payload) = 'object'",
            name=op.f("ck_provider_turns_response_payload_object"),
        ),
        sa.CheckConstraint(
            "tool_calls IS NULL OR jsonb_typeof(tool_calls) = 'array'",
            name=op.f("ck_provider_turns_tool_calls_array"),
        ),
        sa.CheckConstraint(
            "tool_outputs IS NULL OR jsonb_typeof(tool_outputs) = 'array'",
            name=op.f("ck_provider_turns_tool_outputs_array"),
        ),
        sa.CheckConstraint(
            "provider_request_id IS NULL OR char_length(provider_request_id) > 0",
            name=op.f("ck_provider_turns_provider_request_id_nonempty"),
        ),
        sa.CheckConstraint(
            "provider_response_id IS NULL OR char_length(provider_response_id) > 0",
            name=op.f("ck_provider_turns_provider_response_id_nonempty"),
        ),
        sa.CheckConstraint(
            "response_model IS NULL OR char_length(response_model) > 0",
            name=op.f("ck_provider_turns_response_model_nonempty"),
        ),
        sa.CheckConstraint(
            "error_kind IS NULL OR char_length(error_kind) > 0",
            name=op.f("ck_provider_turns_error_kind_nonempty"),
        ),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name=op.f("ck_provider_turns_input_tokens_nonnegative"),
        ),
        sa.CheckConstraint(
            "cached_input_tokens IS NULL OR cached_input_tokens >= 0",
            name=op.f("ck_provider_turns_cached_input_tokens_nonnegative"),
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name=op.f("ck_provider_turns_output_tokens_nonnegative"),
        ),
        sa.CheckConstraint(
            "reasoning_tokens IS NULL OR reasoning_tokens >= 0",
            name=op.f("ck_provider_turns_reasoning_tokens_nonnegative"),
        ),
        sa.CheckConstraint(
            "total_tokens IS NULL OR total_tokens >= 0",
            name=op.f("ck_provider_turns_total_tokens_nonnegative"),
        ),
        sa.CheckConstraint(
            "cached_input_tokens IS NULL OR input_tokens IS NULL "
            "OR cached_input_tokens <= input_tokens",
            name=op.f("ck_provider_turns_cached_input_tokens_bounded"),
        ),
        sa.CheckConstraint(
            "reasoning_tokens IS NULL OR output_tokens IS NULL "
            "OR reasoning_tokens <= output_tokens",
            name=op.f("ck_provider_turns_reasoning_tokens_bounded"),
        ),
        sa.CheckConstraint(
            "total_tokens IS NULL OR input_tokens IS NULL OR output_tokens IS NULL "
            "OR total_tokens >= input_tokens + output_tokens",
            name=op.f("ck_provider_turns_total_tokens_bounded"),
        ),
        sa.CheckConstraint(
            "estimated_cost_usd_micros IS NULL OR estimated_cost_usd_micros >= 0",
            name=op.f("ck_provider_turns_estimated_cost_usd_micros_nonnegative"),
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name=op.f("ck_provider_turns_latency_ms_nonnegative"),
        ),
        sa.CheckConstraint(
            "http_status_code IS NULL OR http_status_code BETWEEN 100 AND 599",
            name=op.f("ck_provider_turns_http_status_code_range"),
        ),
        sa.CheckConstraint(
            "response_payload IS NULL OR http_status_code IS NULL "
            "OR http_status_code BETWEEN 200 AND 299",
            name=op.f("ck_provider_turns_response_http_success"),
        ),
        sa.CheckConstraint(
            "(input_tokens IS NULL AND cached_input_tokens IS NULL "
            "AND output_tokens IS NULL AND reasoning_tokens IS NULL "
            "AND total_tokens IS NULL AND estimated_cost_usd_micros IS NULL) "
            "OR response_payload IS NOT NULL",
            name=op.f("ck_provider_turns_usage_requires_response"),
        ),
        sa.CheckConstraint(
            "(status = 'prepared' "
            "AND provider_request_id IS NULL AND provider_response_id IS NULL "
            "AND response_model IS NULL AND response_payload IS NULL "
            "AND tool_calls IS NULL AND tool_outputs IS NULL "
            "AND input_tokens IS NULL AND cached_input_tokens IS NULL "
            "AND output_tokens IS NULL AND reasoning_tokens IS NULL "
            "AND total_tokens IS NULL AND estimated_cost_usd_micros IS NULL "
            "AND latency_ms IS NULL AND http_status_code IS NULL "
            "AND retryable IS NULL AND error_kind IS NULL "
            "AND safe_error_message IS NULL AND request_started_at IS NULL "
            "AND response_received_at IS NULL "
            "AND tools_completed_at IS NULL AND completed_at IS NULL) "
            "OR (status = 'request_started' "
            "AND provider_request_id IS NULL AND provider_response_id IS NULL "
            "AND response_model IS NULL AND response_payload IS NULL "
            "AND tool_calls IS NULL AND tool_outputs IS NULL "
            "AND input_tokens IS NULL AND cached_input_tokens IS NULL "
            "AND output_tokens IS NULL AND reasoning_tokens IS NULL "
            "AND total_tokens IS NULL AND estimated_cost_usd_micros IS NULL "
            "AND latency_ms IS NULL AND http_status_code IS NULL "
            "AND retryable IS NULL AND error_kind IS NULL "
            "AND safe_error_message IS NULL AND request_started_at IS NOT NULL "
            "AND response_received_at IS NULL "
            "AND tools_completed_at IS NULL AND completed_at IS NULL) "
            "OR (status = 'response_received' "
            "AND provider_response_id IS NOT NULL AND response_model IS NOT NULL "
            "AND response_payload IS NOT NULL AND request_started_at IS NOT NULL "
            "AND response_received_at IS NOT NULL "
            "AND tool_calls IS NOT NULL AND jsonb_array_length(tool_calls) > 0 "
            "AND tool_outputs IS NOT NULL "
            "AND jsonb_array_length(tool_outputs) <= jsonb_array_length(tool_calls) "
            "AND retryable IS NULL AND error_kind IS NULL "
            "AND safe_error_message IS NULL AND tools_completed_at IS NULL "
            "AND completed_at IS NULL) "
            "OR (status = 'tools_completed' "
            "AND provider_response_id IS NOT NULL AND response_model IS NOT NULL "
            "AND response_payload IS NOT NULL AND request_started_at IS NOT NULL "
            "AND response_received_at IS NOT NULL "
            "AND tool_calls IS NOT NULL AND jsonb_array_length(tool_calls) > 0 "
            "AND tool_outputs IS NOT NULL "
            "AND jsonb_array_length(tool_outputs) = jsonb_array_length(tool_calls) "
            "AND retryable IS NULL AND error_kind IS NULL "
            "AND safe_error_message IS NULL AND tools_completed_at IS NOT NULL "
            "AND completed_at IS NOT NULL) "
            "OR (status = 'final_response' "
            "AND provider_response_id IS NOT NULL AND response_model IS NOT NULL "
            "AND response_payload IS NOT NULL AND request_started_at IS NOT NULL "
            "AND response_received_at IS NOT NULL "
            "AND tool_calls IS NOT NULL AND jsonb_array_length(tool_calls) = 0 "
            "AND tool_outputs IS NULL AND retryable IS NULL AND error_kind IS NULL "
            "AND safe_error_message IS NULL AND tools_completed_at IS NULL "
            "AND completed_at IS NOT NULL) "
            "OR (status = 'response_invalid' "
            "AND response_payload IS NOT NULL AND request_started_at IS NOT NULL "
            "AND response_received_at IS NOT NULL "
            "AND tool_outputs IS NULL AND retryable = FALSE AND error_kind IS NOT NULL "
            "AND safe_error_message IS NOT NULL AND tools_completed_at IS NULL "
            "AND completed_at IS NOT NULL) "
            "OR (status IN ('failed', 'indeterminate') "
            "AND provider_response_id IS NULL AND response_model IS NULL "
            "AND response_payload IS NULL AND tool_calls IS NULL AND tool_outputs IS NULL "
            "AND input_tokens IS NULL AND cached_input_tokens IS NULL "
            "AND output_tokens IS NULL AND reasoning_tokens IS NULL "
            "AND total_tokens IS NULL AND estimated_cost_usd_micros IS NULL "
            "AND retryable IS NOT NULL AND error_kind IS NOT NULL "
            "AND safe_error_message IS NOT NULL AND request_started_at IS NOT NULL "
            "AND response_received_at IS NULL "
            "AND tools_completed_at IS NULL AND completed_at IS NOT NULL) "
            "OR (status = 'superseded' "
            "AND tools_completed_at IS NULL AND completed_at IS NOT NULL "
            "AND ((provider_response_id IS NULL AND response_model IS NULL "
            "AND response_payload IS NULL AND tool_calls IS NULL "
            "AND tool_outputs IS NULL AND input_tokens IS NULL "
            "AND cached_input_tokens IS NULL AND output_tokens IS NULL "
            "AND reasoning_tokens IS NULL AND total_tokens IS NULL "
            "AND estimated_cost_usd_micros IS NULL AND latency_ms IS NULL "
            "AND http_status_code IS NULL AND provider_request_id IS NULL "
            "AND retryable IS NULL AND error_kind IS NULL "
            "AND safe_error_message IS NULL "
            "AND request_started_at IS NULL AND response_received_at IS NULL) "
            "OR (response_payload IS NOT NULL AND tool_calls IS NOT NULL "
            "AND tool_outputs IS NOT NULL "
            "AND jsonb_array_length(tool_outputs) <= jsonb_array_length(tool_calls) "
            "AND ((retryable IS NULL AND error_kind IS NULL "
            "AND safe_error_message IS NULL) "
            "OR (retryable IS NOT NULL AND error_kind IS NOT NULL "
            "AND safe_error_message IS NOT NULL)) "
            "AND request_started_at IS NOT NULL AND response_received_at IS NOT NULL)))",
            name=op.f("ck_provider_turns_state_coherent"),
        ),
        sa.ForeignKeyConstraint(
            ["case_execution_id", "attempt_number"],
            ["execution_attempts.case_execution_id", "execution_attempts.attempt_number"],
            name="fk_provider_turns_execution_attempt",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_turns")),
        sa.UniqueConstraint(
            "case_execution_id",
            "sequence_number",
            name="uq_provider_turns_execution_sequence",
        ),
        sa.UniqueConstraint(
            "client_request_id",
            name="uq_provider_turns_client_request_id",
        ),
    )
    op.create_index(
        "ix_provider_turns_execution_attempt",
        "provider_turns",
        ["case_execution_id", "attempt_number", "dispatch_generation"],
    )
    op.create_index(
        "ix_provider_turns_status_prepared_at",
        "provider_turns",
        ["status", "prepared_at"],
    )
    op.create_index(
        "uq_provider_turns_execution_open",
        "provider_turns",
        ["case_execution_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('prepared', 'request_started', 'response_received')"),
    )
    op.execute(
        """
        CREATE FUNCTION guard_provider_turn_experiment_identity()
        RETURNS trigger AS $$
        DECLARE
            has_provider_identity boolean;
            claimed_dispatch_generation integer;
        BEGIN
            SELECT experiment.provider_model IS NOT NULL
                   AND experiment.provider_config IS NOT NULL
                   AND experiment.provider_config_hash IS NOT NULL,
                   attempt.dispatch_generation
            INTO has_provider_identity, claimed_dispatch_generation
            FROM case_executions AS execution
            JOIN experiments AS experiment ON experiment.id = execution.experiment_id
            JOIN execution_attempts AS attempt
              ON attempt.case_execution_id = execution.id
             AND attempt.attempt_number = NEW.attempt_number
            WHERE execution.id = NEW.case_execution_id;

            IF has_provider_identity IS DISTINCT FROM TRUE THEN
                RAISE EXCEPTION 'provider turns require a configured provider identity';
            END IF;
            IF claimed_dispatch_generation IS DISTINCT FROM NEW.dispatch_generation THEN
                RAISE EXCEPTION 'provider turn dispatch generation does not match its attempt';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER provider_turns_experiment_identity_guard
        BEFORE INSERT ON provider_turns
        FOR EACH ROW EXECUTE FUNCTION guard_provider_turn_experiment_identity()
        """
    )
    op.execute(
        """
        CREATE FUNCTION guard_provider_turn_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.case_execution_id IS DISTINCT FROM NEW.case_execution_id
               OR OLD.attempt_number IS DISTINCT FROM NEW.attempt_number
               OR OLD.dispatch_generation IS DISTINCT FROM NEW.dispatch_generation
               OR OLD.sequence_number IS DISTINCT FROM NEW.sequence_number
               OR OLD.client_request_id IS DISTINCT FROM NEW.client_request_id
               OR OLD.request_hash IS DISTINCT FROM NEW.request_hash
               OR OLD.request_payload IS DISTINCT FROM NEW.request_payload
               OR OLD.prepared_at IS DISTINCT FROM NEW.prepared_at THEN
                RAISE EXCEPTION 'provider turn request identity is immutable';
            END IF;

            IF OLD.status = 'prepared'
               AND NEW.status NOT IN ('request_started', 'superseded') THEN
                RAISE EXCEPTION 'invalid provider turn status transition';
            ELSIF OLD.status = 'request_started'
               AND NEW.status NOT IN (
                   'response_received', 'final_response', 'response_invalid',
                   'failed', 'indeterminate', 'superseded'
               ) THEN
                RAISE EXCEPTION 'invalid provider turn status transition';
            ELSIF OLD.status = 'response_received'
               AND NEW.status NOT IN ('response_received', 'tools_completed', 'superseded') THEN
                RAISE EXCEPTION 'invalid provider turn status transition';
            ELSIF OLD.status = 'indeterminate' AND NEW.status != 'superseded' THEN
                RAISE EXCEPTION 'terminal provider turns are immutable';
            ELSIF OLD.status NOT IN (
                'prepared', 'request_started', 'response_received', 'indeterminate'
            ) THEN
                RAISE EXCEPTION 'terminal provider turns are immutable';
            END IF;

            IF OLD.status = 'request_started'
               AND NEW.status = 'response_received'
               AND NEW.tool_outputs IS DISTINCT FROM '[]'::jsonb THEN
                RAISE EXCEPTION 'a newly persisted provider response has no tool outputs';
            END IF;

            IF OLD.status = 'response_received'
               AND (
                   OLD.provider_request_id IS DISTINCT FROM NEW.provider_request_id
                   OR OLD.provider_response_id IS DISTINCT FROM NEW.provider_response_id
                   OR OLD.response_model IS DISTINCT FROM NEW.response_model
                   OR OLD.response_payload IS DISTINCT FROM NEW.response_payload
                   OR OLD.tool_calls IS DISTINCT FROM NEW.tool_calls
                   OR OLD.input_tokens IS DISTINCT FROM NEW.input_tokens
                   OR OLD.cached_input_tokens IS DISTINCT FROM NEW.cached_input_tokens
                   OR OLD.output_tokens IS DISTINCT FROM NEW.output_tokens
                   OR OLD.reasoning_tokens IS DISTINCT FROM NEW.reasoning_tokens
                   OR OLD.total_tokens IS DISTINCT FROM NEW.total_tokens
                   OR OLD.estimated_cost_usd_micros
                        IS DISTINCT FROM NEW.estimated_cost_usd_micros
                   OR OLD.latency_ms IS DISTINCT FROM NEW.latency_ms
                   OR OLD.http_status_code IS DISTINCT FROM NEW.http_status_code
                   OR OLD.request_started_at IS DISTINCT FROM NEW.request_started_at
                   OR OLD.response_received_at IS DISTINCT FROM NEW.response_received_at
               ) THEN
                RAISE EXCEPTION 'persisted provider response evidence is immutable';
            END IF;

            IF (OLD.provider_request_id IS NOT NULL
                    AND OLD.provider_request_id IS DISTINCT FROM NEW.provider_request_id)
               OR (OLD.provider_response_id IS NOT NULL
                    AND OLD.provider_response_id IS DISTINCT FROM NEW.provider_response_id)
               OR (OLD.response_model IS NOT NULL
                    AND OLD.response_model IS DISTINCT FROM NEW.response_model)
               OR (OLD.response_payload IS NOT NULL
                    AND OLD.response_payload IS DISTINCT FROM NEW.response_payload)
               OR (OLD.tool_calls IS NOT NULL
                    AND OLD.tool_calls IS DISTINCT FROM NEW.tool_calls)
               OR (OLD.tool_outputs IS NOT NULL
                    AND OLD.tool_outputs IS DISTINCT FROM NEW.tool_outputs
                    AND NOT (
                        OLD.status = 'response_received'
                        AND NEW.status IN ('response_received', 'tools_completed')
                        AND jsonb_array_length(NEW.tool_outputs)
                            = jsonb_array_length(OLD.tool_outputs) + 1
                        AND NEW.tool_outputs
                            - (jsonb_array_length(NEW.tool_outputs) - 1) = OLD.tool_outputs
                    ))
               OR (OLD.input_tokens IS NOT NULL
                    AND OLD.input_tokens IS DISTINCT FROM NEW.input_tokens)
               OR (OLD.cached_input_tokens IS NOT NULL
                    AND OLD.cached_input_tokens IS DISTINCT FROM NEW.cached_input_tokens)
               OR (OLD.output_tokens IS NOT NULL
                    AND OLD.output_tokens IS DISTINCT FROM NEW.output_tokens)
               OR (OLD.reasoning_tokens IS NOT NULL
                    AND OLD.reasoning_tokens IS DISTINCT FROM NEW.reasoning_tokens)
               OR (OLD.total_tokens IS NOT NULL
                    AND OLD.total_tokens IS DISTINCT FROM NEW.total_tokens)
               OR (OLD.estimated_cost_usd_micros IS NOT NULL
                    AND OLD.estimated_cost_usd_micros
                        IS DISTINCT FROM NEW.estimated_cost_usd_micros)
               OR (OLD.latency_ms IS NOT NULL
                    AND OLD.latency_ms IS DISTINCT FROM NEW.latency_ms)
               OR (OLD.http_status_code IS NOT NULL
                    AND OLD.http_status_code IS DISTINCT FROM NEW.http_status_code)
               OR (OLD.retryable IS NOT NULL
                    AND OLD.retryable IS DISTINCT FROM NEW.retryable)
               OR (OLD.error_kind IS NOT NULL
                    AND OLD.error_kind IS DISTINCT FROM NEW.error_kind)
               OR (OLD.safe_error_message IS NOT NULL
                    AND OLD.safe_error_message IS DISTINCT FROM NEW.safe_error_message)
               OR (OLD.request_started_at IS NOT NULL
                    AND OLD.request_started_at IS DISTINCT FROM NEW.request_started_at)
               OR (OLD.response_received_at IS NOT NULL
                    AND OLD.response_received_at IS DISTINCT FROM NEW.response_received_at)
               OR (OLD.tools_completed_at IS NOT NULL
                    AND OLD.tools_completed_at IS DISTINCT FROM NEW.tools_completed_at)
               OR (OLD.completed_at IS NOT NULL
                    AND OLD.completed_at IS DISTINCT FROM NEW.completed_at) THEN
                RAISE EXCEPTION 'persisted provider turn evidence is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER provider_turns_mutation_guard
        BEFORE UPDATE ON provider_turns
        FOR EACH ROW EXECUTE FUNCTION guard_provider_turn_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_provider_turn_delete()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'provider turns are durable and cannot be deleted';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER provider_turns_delete_guard
        BEFORE DELETE ON provider_turns
        FOR EACH ROW EXECUTE FUNCTION reject_provider_turn_delete()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS provider_turns_delete_guard ON provider_turns")
    op.execute("DROP FUNCTION IF EXISTS reject_provider_turn_delete()")
    op.execute("DROP TRIGGER IF EXISTS provider_turns_mutation_guard ON provider_turns")
    op.execute("DROP FUNCTION IF EXISTS guard_provider_turn_mutation()")
    op.execute("DROP TRIGGER IF EXISTS provider_turns_experiment_identity_guard ON provider_turns")
    op.execute("DROP FUNCTION IF EXISTS guard_provider_turn_experiment_identity()")
    op.drop_table("provider_turns")

    op.execute("DROP INDEX IF EXISTS ix_execution_dispatches_execution_attempt")
    op.execute(
        "ALTER TABLE execution_dispatches "
        "DROP CONSTRAINT IF EXISTS ck_execution_dispatches_delivery_identity_coherent"
    )
    op.execute(
        "ALTER TABLE execution_dispatches "
        "DROP CONSTRAINT IF EXISTS ck_execution_dispatches_attempt_number_positive"
    )
    op.execute(
        "ALTER TABLE execution_dispatches "
        "DROP CONSTRAINT IF EXISTS ck_execution_dispatches_delivery_kind_allowed"
    )
    op.execute(
        "ALTER TABLE execution_dispatches "
        "DROP CONSTRAINT IF EXISTS fk_execution_dispatches_execution_attempt"
    )
    op.execute("ALTER TABLE execution_dispatches DROP COLUMN IF EXISTS attempt_number")
    op.execute("ALTER TABLE execution_dispatches DROP COLUMN IF EXISTS delivery_kind")

    op.execute(
        "ALTER TABLE execution_attempts "
        "DROP CONSTRAINT IF EXISTS ck_execution_attempts_recovery_count_nonnegative"
    )
    op.execute("ALTER TABLE execution_attempts DROP COLUMN IF EXISTS recovery_count")
    op.drop_constraint(
        op.f("ck_execution_attempts_status_allowed"),
        "execution_attempts",
        type_="check",
    )
    op.execute(
        "UPDATE execution_attempts SET status = 'interrupted' WHERE status = 'resume_scheduled'"
    )
    op.create_check_constraint(
        op.f("ck_execution_attempts_status_allowed"),
        "execution_attempts",
        "status IN ('running', 'succeeded', 'retry_scheduled', 'failed', 'interrupted')",
    )

    op.execute("DROP TRIGGER IF EXISTS experiments_provider_identity_immutable ON experiments")
    op.execute("DROP FUNCTION IF EXISTS guard_experiment_provider_identity_mutation()")
    op.execute("DROP INDEX IF EXISTS uq_experiments_single_openai_run")
    op.drop_index("ix_experiments_provider_identity", table_name="experiments")
    op.drop_constraint(
        op.f("ck_experiments_provider_config_hash_format"),
        "experiments",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_experiments_provider_config_object"),
        "experiments",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_experiments_provider_model_nonempty"),
        "experiments",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_experiments_provider_identity_complete"),
        "experiments",
        type_="check",
    )
    op.drop_column("experiments", "provider_config_hash")
    op.drop_column("experiments", "provider_config")
    op.drop_column("experiments", "provider_model")
