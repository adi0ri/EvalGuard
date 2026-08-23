"""Add immutable agent prompts and multi-trial experiment identity.

Revision ID: 20260822_0008
Revises: 20260822_0007
Create Date: 2026-08-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260822_0008"
down_revision: str | None = "20260822_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_prompts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column("is_published", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_prompts")),
        sa.UniqueConstraint("name", "version", name="uq_agent_prompts_name_version"),
    )
    op.execute(
        """
        CREATE FUNCTION guard_agent_prompt_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'UPDATE'
               AND OLD.is_published = FALSE
               AND NEW.is_published = TRUE
               AND OLD.id IS NOT DISTINCT FROM NEW.id
               AND OLD.name IS NOT DISTINCT FROM NEW.name
               AND OLD.version IS NOT DISTINCT FROM NEW.version
               AND OLD.description IS NOT DISTINCT FROM NEW.description
               AND OLD.instructions IS NOT DISTINCT FROM NEW.instructions
               AND OLD.definition_hash IS NOT DISTINCT FROM NEW.definition_hash
               AND OLD.created_at IS NOT DISTINCT FROM NEW.created_at THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'published agent prompts are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER agent_prompts_immutable
        BEFORE UPDATE OR DELETE ON agent_prompts
        FOR EACH ROW EXECUTE FUNCTION guard_agent_prompt_mutation()
        """
    )

    op.add_column(
        "experiments",
        sa.Column("agent_prompt_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "experiments",
        sa.Column("trials", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    # Phase 0-4 API experiments contain trial 1 only. Deriving the value from
    # retained execution rows also makes a 0008 -> 0007 -> 0008 round trip recover
    # the correct count for Phase 5 multi-trial experiments.
    op.execute(
        """
        UPDATE experiments AS experiment
        SET trials = observed.maximum_trial
        FROM (
            SELECT experiment_id, MAX(trial_number) AS maximum_trial
            FROM case_executions
            GROUP BY experiment_id
        ) AS observed
        WHERE experiment.id = observed.experiment_id
        """
    )
    op.create_foreign_key(
        op.f("fk_experiments_agent_prompt_id_agent_prompts"),
        "experiments",
        "agent_prompts",
        ["agent_prompt_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_experiments_agent_prompt_id",
        "experiments",
        ["agent_prompt_id"],
    )
    op.create_check_constraint(
        op.f("ck_experiments_trials_range"),
        "experiments",
        "trials BETWEEN 1 AND 3",
    )
    op.create_check_constraint(
        op.f("ck_case_executions_trial_number_range"),
        "case_executions",
        "trial_number BETWEEN 1 AND 3",
    )
    op.execute(
        """
        CREATE FUNCTION guard_experiment_prompt_trials_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.agent_prompt_id IS DISTINCT FROM NEW.agent_prompt_id
               OR OLD.trials IS DISTINCT FROM NEW.trials THEN
                RAISE EXCEPTION 'experiment prompt and trial identity is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER experiments_prompt_trials_immutable
        BEFORE UPDATE OF agent_prompt_id, trials ON experiments
        FOR EACH ROW EXECUTE FUNCTION guard_experiment_prompt_trials_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION guard_case_execution_trial_number()
        RETURNS trigger AS $$
        DECLARE
            configured_trials integer;
        BEGIN
            SELECT trials INTO configured_trials
            FROM experiments
            WHERE id = NEW.experiment_id
            FOR KEY SHARE;

            IF configured_trials IS NOT NULL
               AND NEW.trial_number > configured_trials THEN
                RAISE EXCEPTION 'case execution trial_number exceeds experiment trials';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER case_executions_trial_insert_guard
        BEFORE INSERT ON case_executions
        FOR EACH ROW EXECUTE FUNCTION guard_case_execution_trial_number()
        """
    )
    op.execute(
        """
        CREATE TRIGGER case_executions_trial_update_guard
        BEFORE UPDATE OF experiment_id, trial_number ON case_executions
        FOR EACH ROW EXECUTE FUNCTION guard_case_execution_trial_number()
        """
    )
    op.alter_column("experiments", "trials", server_default=None)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS case_executions_trial_update_guard ON case_executions")
    op.execute("DROP TRIGGER IF EXISTS case_executions_trial_insert_guard ON case_executions")
    op.execute("DROP FUNCTION IF EXISTS guard_case_execution_trial_number()")
    op.execute("DROP TRIGGER IF EXISTS experiments_prompt_trials_immutable ON experiments")
    op.execute("DROP FUNCTION IF EXISTS guard_experiment_prompt_trials_mutation()")

    op.drop_constraint(
        op.f("ck_case_executions_trial_number_range"),
        "case_executions",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_experiments_trials_range"),
        "experiments",
        type_="check",
    )
    op.drop_index("ix_experiments_agent_prompt_id", table_name="experiments")
    op.drop_constraint(
        op.f("fk_experiments_agent_prompt_id_agent_prompts"),
        "experiments",
        type_="foreignkey",
    )
    op.drop_column("experiments", "trials")
    op.drop_column("experiments", "agent_prompt_id")

    op.execute("DROP TRIGGER agent_prompts_immutable ON agent_prompts")
    op.execute("DROP FUNCTION guard_agent_prompt_mutation()")
    op.drop_table("agent_prompts")
