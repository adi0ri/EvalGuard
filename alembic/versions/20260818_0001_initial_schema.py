"""Create Phase 0/1 evaluation and ParcelDesk tables.

Revision ID: 20260818_0001
Revises: None
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260818_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_suites",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_evaluation_suites"),
        sa.UniqueConstraint("name", "version", name="uq_evaluation_suites_name_version"),
    )
    op.create_table(
        "evaluation_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("suite_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column("fixture", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expected", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["suite_id"],
            ["evaluation_suites.id"],
            name="fk_evaluation_cases_suite_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_evaluation_cases"),
        sa.UniqueConstraint("suite_id", "key", name="uq_evaluation_cases_suite_key"),
    )
    op.create_index("ix_evaluation_cases_suite_id", "evaluation_cases", ["suite_id"])
    op.create_table(
        "experiments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("suite_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["suite_id"],
            ["evaluation_suites.id"],
            name="fk_experiments_suite_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_experiments"),
        sa.UniqueConstraint("idempotency_key", name="uq_experiments_idempotency_key"),
    )
    op.create_index("ix_experiments_suite_status", "experiments", ["suite_id", "status"])
    op.create_table(
        "case_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trial_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("final_resolution", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["evaluation_cases.id"],
            name="fk_case_executions_case_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.id"],
            name="fk_case_executions_experiment_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_case_executions"),
        sa.UniqueConstraint(
            "experiment_id",
            "case_id",
            "trial_number",
            name="uq_case_executions_experiment_case_trial",
        ),
    )
    op.create_index("ix_case_executions_case_id", "case_executions", ["case_id"])
    op.create_index(
        "ix_case_executions_experiment_status", "case_executions", ["experiment_id", "status"]
    )
    op.create_table(
        "trace_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("case_execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_execution_id"],
            ["case_executions.id"],
            name="fk_trace_events_execution",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_trace_events"),
        sa.UniqueConstraint(
            "case_execution_id", "sequence_number", name="uq_trace_events_execution_sequence"
        ),
    )
    op.create_index(
        "ix_trace_events_execution_type", "trace_events", ["case_execution_id", "event_type"]
    )
    op.create_table(
        "tool_invocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_call_id", sa.String(length=100), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["case_execution_id"],
            ["case_executions.id"],
            name="fk_tool_invocations_execution",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tool_invocations"),
        sa.UniqueConstraint(
            "case_execution_id", "tool_call_id", name="uq_tool_invocations_execution_call"
        ),
    )
    op.create_index(
        "ix_tool_invocations_execution_tool", "tool_invocations", ["case_execution_id", "tool_name"]
    )
    op.create_table(
        "grader_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("grader_name", sa.String(length=100), nullable=False),
        sa.Column("criterion", sa.String(length=100), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_execution_id"],
            ["case_executions.id"],
            name="fk_grader_results_execution",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_grader_results"),
        sa.UniqueConstraint(
            "case_execution_id",
            "grader_name",
            "criterion",
            name="uq_grader_results_execution_grader_criterion",
        ),
    )
    op.create_index(
        "ix_grader_results_execution_passed", "grader_results", ["case_execution_id", "passed"]
    )
    op.create_table(
        "customers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_execution_id"],
            ["case_executions.id"],
            name="fk_customers_execution",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_customers"),
        sa.UniqueConstraint("id", "case_execution_id", name="uq_customers_id_execution"),
        sa.UniqueConstraint(
            "case_execution_id", "external_id", name="uq_customers_execution_external"
        ),
    )
    op.create_index("ix_customers_case_execution_id", "customers", ["case_execution_id"])
    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("total_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_execution_id"],
            ["case_executions.id"],
            name="fk_orders_execution",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id", "case_execution_id"],
            ["customers.id", "customers.case_execution_id"],
            name="fk_orders_customer_execution",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_orders"),
        sa.UniqueConstraint("id", "case_execution_id", name="uq_orders_id_execution"),
        sa.UniqueConstraint(
            "case_execution_id", "external_id", name="uq_orders_execution_external"
        ),
    )
    op.create_index("ix_orders_case_execution_id", "orders", ["case_execution_id"])
    op.create_table(
        "refunds",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("tool_call_id", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_execution_id"],
            ["case_executions.id"],
            name="fk_refunds_execution",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["order_id", "case_execution_id"],
            ["orders.id", "orders.case_execution_id"],
            name="fk_refunds_order_execution",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["case_execution_id", "tool_call_id"],
            ["tool_invocations.case_execution_id", "tool_invocations.tool_call_id"],
            name="fk_refunds_tool_invocation",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_refunds"),
        sa.UniqueConstraint("case_execution_id", "order_id", name="uq_refunds_execution_order"),
        sa.UniqueConstraint(
            "case_execution_id", "tool_call_id", name="uq_refunds_execution_tool_call"
        ),
    )
    op.create_index("ix_refunds_case_execution_id", "refunds", ["case_execution_id"])

    op.execute(
        """
        CREATE FUNCTION prevent_evaluation_definition_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'versioned evaluation definitions are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER evaluation_suites_immutable
        BEFORE UPDATE OR DELETE ON evaluation_suites
        FOR EACH ROW EXECUTE FUNCTION prevent_evaluation_definition_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER evaluation_cases_immutable
        BEFORE UPDATE OR DELETE ON evaluation_cases
        FOR EACH ROW EXECUTE FUNCTION prevent_evaluation_definition_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS evaluation_cases_immutable ON evaluation_cases")
    op.execute("DROP TRIGGER IF EXISTS evaluation_suites_immutable ON evaluation_suites")
    op.execute("DROP FUNCTION IF EXISTS prevent_evaluation_definition_mutation()")
    op.drop_table("refunds")
    op.drop_table("orders")
    op.drop_table("customers")
    op.drop_table("grader_results")
    op.drop_table("tool_invocations")
    op.drop_table("trace_events")
    op.drop_table("case_executions")
    op.drop_table("experiments")
    op.drop_table("evaluation_cases")
    op.drop_table("evaluation_suites")
