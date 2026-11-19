"""Add cassettes table and replay linkage columns on runs.

Revision ID: 002_cassettes_and_replay_columns
Revises: 001_initial_schema
Create Date: 2026-11-19 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "002_cassettes_and_replay_columns"
down_revision: str | None = "001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Cassettes table
    op.create_table(
        "cassettes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("schema_version", sa.String(length=32), server_default="1.0", nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_cassettes_project_id", "cassettes", ["project_id"])
    op.create_index("ix_cassettes_source_run_id", "cassettes", ["source_run_id"])
    op.create_index("ix_cassettes_fingerprint", "cassettes", ["fingerprint"])
    op.create_index("ix_cassettes_project_created", "cassettes", ["project_id", "created_at"])

    # 2. Add replay columns to runs
    op.add_column(
        "runs",
        sa.Column(
            "replay_of_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "runs",
        sa.Column(
            "cassette_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cassettes.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "runs",
        sa.Column(
            "replay_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_index("ix_runs_replay_of", "runs", ["replay_of_run_id"])
    op.create_index("ix_runs_cassette_id", "runs", ["cassette_id"])


def downgrade() -> None:
    op.drop_index("ix_runs_cassette_id", table_name="runs")
    op.drop_index("ix_runs_replay_of", table_name="runs")
    op.drop_column("runs", "replay_policy")
    op.drop_column("runs", "cassette_id")
    op.drop_column("runs", "replay_of_run_id")

    op.drop_index("ix_cassettes_project_created", table_name="cassettes")
    op.drop_index("ix_cassettes_fingerprint", table_name="cassettes")
    op.drop_index("ix_cassettes_source_run_id", table_name="cassettes")
    op.drop_index("ix_cassettes_project_id", table_name="cassettes")
    op.drop_table("cassettes")
