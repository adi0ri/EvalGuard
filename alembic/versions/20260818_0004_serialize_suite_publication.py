"""Serialize suite publication and case membership changes.

Revision ID: 20260818_0004
Revises: 20260818_0003
Create Date: 2026-08-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260818_0004"
down_revision: str | None = "20260818_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION guard_evaluation_case_mutation()
        RETURNS trigger AS $$
        DECLARE
            locked_suite record;
            any_published boolean := FALSE;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                FOR locked_suite IN
                    SELECT id, is_published
                    FROM evaluation_suites
                    WHERE id = NEW.suite_id
                    ORDER BY id
                    FOR UPDATE
                LOOP
                    any_published := any_published OR locked_suite.is_published;
                END LOOP;
            ELSIF TG_OP = 'DELETE' THEN
                FOR locked_suite IN
                    SELECT id, is_published
                    FROM evaluation_suites
                    WHERE id = OLD.suite_id
                    ORDER BY id
                    FOR UPDATE
                LOOP
                    any_published := any_published OR locked_suite.is_published;
                END LOOP;
            ELSE
                FOR locked_suite IN
                    SELECT id, is_published
                    FROM evaluation_suites
                    WHERE id = OLD.suite_id OR id = NEW.suite_id
                    ORDER BY id
                    FOR UPDATE
                LOOP
                    any_published := any_published OR locked_suite.is_published;
                END LOOP;
            END IF;

            IF any_published THEN
                RAISE EXCEPTION 'cases in published evaluation suites are immutable';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION guard_evaluation_case_mutation()
        RETURNS trigger AS $$
        DECLARE
            old_suite_published boolean := FALSE;
            new_suite_published boolean := FALSE;
        BEGIN
            IF TG_OP IN ('UPDATE', 'DELETE') THEN
                SELECT is_published INTO old_suite_published
                FROM evaluation_suites
                WHERE id = OLD.suite_id;
            END IF;
            IF TG_OP IN ('INSERT', 'UPDATE') THEN
                SELECT is_published INTO new_suite_published
                FROM evaluation_suites
                WHERE id = NEW.suite_id;
            END IF;

            IF old_suite_published = FALSE AND new_suite_published = FALSE THEN
                IF TG_OP = 'DELETE' THEN
                    RETURN OLD;
                END IF;
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'cases in published evaluation suites are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
