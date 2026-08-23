"""Add Phase 4 retry, lease, dispatch, and fault-injection state.

Revision ID: 20260822_0007
Revises: 20260822_0006
Create Date: 2026-08-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260822_0007"
down_revision: str | None = "20260822_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "experiments",
        sa.Column(
            "max_attempts",
            sa.Integer(),
            server_default=sa.text("3"),
            nullable=False,
        ),
    )
    op.add_column(
        "experiments",
        sa.Column(
            "job_timeout_seconds",
            sa.Integer(),
            # Existing Phase 3 generation-1 jobs inherited the queue's 300-second
            # default. Preserve that policy for backfilled rows; new ORM-created
            # experiments use the Phase 4 application default of 30 seconds.
            server_default=sa.text("300"),
            nullable=False,
        ),
    )
    op.add_column(
        "experiments",
        sa.Column(
            "max_tool_steps",
            sa.Integer(),
            server_default=sa.text("8"),
            nullable=False,
        ),
    )
    op.add_column(
        "experiments",
        sa.Column(
            "retry_backoff_seconds",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_experiments_max_attempts_range"),
        "experiments",
        "max_attempts BETWEEN 1 AND 5",
    )
    op.create_check_constraint(
        op.f("ck_experiments_job_timeout_seconds_range"),
        "experiments",
        "job_timeout_seconds BETWEEN 1 AND 300",
    )
    op.create_check_constraint(
        op.f("ck_experiments_max_tool_steps_range"),
        "experiments",
        "max_tool_steps BETWEEN 1 AND 100",
    )
    op.create_check_constraint(
        op.f("ck_experiments_retry_backoff_seconds_range"),
        "experiments",
        "retry_backoff_seconds BETWEEN 0 AND 300",
    )

    op.add_column(
        "case_executions",
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "case_executions",
        sa.Column(
            "dispatch_generation",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    op.add_column(
        "case_executions",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "case_executions",
        sa.Column("last_failure_kind", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "case_executions",
        sa.Column("last_failure_retryable", sa.Boolean(), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_case_executions_attempt_count_nonnegative"),
        "case_executions",
        "attempt_count >= 0",
    )
    op.create_check_constraint(
        op.f("ck_case_executions_dispatch_generation_positive"),
        "case_executions",
        "dispatch_generation >= 1",
    )

    # Services are stopped while migrations run. Any pre-Phase-4 running row has
    # no durable lease and therefore cannot be distinguished from abandoned work.
    # Include retrying so a 0007 -> 0006 -> 0007 round trip cannot strand an
    # in-flight Phase 4 execution after its dispatch and attempt rows were dropped.
    op.execute(
        """
        UPDATE case_executions
        SET status = 'queued',
            lease_expires_at = NULL
        WHERE status IN ('running', 'retrying')
        """
    )

    op.create_table(
        "execution_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("dispatch_generation", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.String(length=150), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        sa.Column("failure_kind", sa.String(length=50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("step_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "attempt_number >= 1",
            name=op.f("ck_execution_attempts_attempt_number_positive"),
        ),
        sa.CheckConstraint(
            "dispatch_generation >= 1",
            name=op.f("ck_execution_attempts_dispatch_generation_positive"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'retry_scheduled', 'failed', 'interrupted')",
            name=op.f("ck_execution_attempts_status_allowed"),
        ),
        sa.CheckConstraint(
            "step_count >= 0",
            name=op.f("ck_execution_attempts_step_count_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["case_execution_id"],
            ["case_executions.id"],
            name="fk_execution_attempts_execution",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_execution_attempts"),
        sa.UniqueConstraint(
            "case_execution_id",
            "attempt_number",
            name="uq_execution_attempts_execution_number",
        ),
    )
    op.create_index(
        "ix_execution_attempts_execution_status",
        "execution_attempts",
        ["case_execution_id", "status"],
    )
    op.create_index(
        "ix_execution_attempts_status_lease",
        "execution_attempts",
        ["status", "lease_expires_at"],
    )

    op.create_table(
        "execution_dispatches",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.String(length=150), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_kind", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "generation >= 1",
            name=op.f("ck_execution_dispatches_generation_positive"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'dispatched')",
            name=op.f("ck_execution_dispatches_status_allowed"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_execution_dispatches_attempt_count_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["case_execution_id"],
            ["case_executions.id"],
            name="fk_execution_dispatches_execution",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_execution_dispatches"),
        sa.UniqueConstraint(
            "case_execution_id",
            "generation",
            name="uq_execution_dispatches_execution_generation",
        ),
        sa.UniqueConstraint("job_id", name="uq_execution_dispatches_job_id"),
    )
    op.create_index(
        "ix_execution_dispatches_due",
        "execution_dispatches",
        ["status", "available_at"],
    )

    op.create_table(
        "execution_fault_plans",
        sa.Column("case_execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mode", sa.String(length=50), nullable=False),
        sa.Column("fail_attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "mode IN ("
            "'transient_before_provider', "
            "'transient_after_tools', "
            "'timeout', "
            "'permanent_before_provider', "
            "'worker_interruption'"
            ")",
            name=op.f("ck_execution_fault_plans_mode_allowed"),
        ),
        sa.CheckConstraint(
            "fail_attempts >= 1",
            name=op.f("ck_execution_fault_plans_fail_attempts_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["case_execution_id"],
            ["case_executions.id"],
            name="fk_execution_fault_plans_execution",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("case_execution_id", name="pk_execution_fault_plans"),
    )

    # A deterministic UUID avoids requiring a database extension solely for this
    # one-time backfill. Generation 1 uses the pre-Phase-4 stable RQ job identity.
    op.execute(
        """
        INSERT INTO execution_dispatches (
            id,
            case_execution_id,
            generation,
            job_id,
            status,
            attempt_count,
            available_at,
            created_at,
            updated_at
        )
        SELECT
            CAST(md5('evalguard:execution-dispatch:' || id::text || '-1') AS uuid),
            id,
            1,
            'case-execution-' || id::text,
            'pending',
            0,
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        FROM case_executions
        WHERE status IN ('queued', 'enqueue_failed')
        """
    )

    # Temporary defaults were needed to backfill existing rows safely. Drop them so
    # new writes use the application defaults (including the new 30-second timeout).
    op.alter_column("experiments", "max_attempts", server_default=None)
    op.alter_column("experiments", "job_timeout_seconds", server_default=None)
    op.alter_column("experiments", "max_tool_steps", server_default=None)
    op.alter_column("experiments", "retry_backoff_seconds", server_default=None)
    op.alter_column("case_executions", "attempt_count", server_default=None)
    op.alter_column("case_executions", "dispatch_generation", server_default=None)


def downgrade() -> None:
    op.drop_table("execution_fault_plans")

    op.drop_index("ix_execution_dispatches_due", table_name="execution_dispatches")
    op.drop_table("execution_dispatches")

    op.drop_index("ix_execution_attempts_status_lease", table_name="execution_attempts")
    op.drop_index("ix_execution_attempts_execution_status", table_name="execution_attempts")
    op.drop_table("execution_attempts")

    op.drop_constraint(
        op.f("ck_case_executions_dispatch_generation_positive"),
        "case_executions",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_case_executions_attempt_count_nonnegative"),
        "case_executions",
        type_="check",
    )
    op.drop_column("case_executions", "last_failure_retryable")
    op.drop_column("case_executions", "last_failure_kind")
    op.drop_column("case_executions", "lease_expires_at")
    op.drop_column("case_executions", "dispatch_generation")
    op.drop_column("case_executions", "attempt_count")

    op.drop_constraint(
        op.f("ck_experiments_retry_backoff_seconds_range"),
        "experiments",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_experiments_max_tool_steps_range"),
        "experiments",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_experiments_job_timeout_seconds_range"),
        "experiments",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_experiments_max_attempts_range"),
        "experiments",
        type_="check",
    )
    op.drop_column("experiments", "retry_backoff_seconds")
    op.drop_column("experiments", "max_tool_steps")
    op.drop_column("experiments", "job_timeout_seconds")
    op.drop_column("experiments", "max_attempts")
