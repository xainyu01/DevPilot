"""Persist one-time remote-host pairing digests for B8 recovery.

Revision ID: 0004_b8_remote_host_pairing
Revises: 0003_b7_teams
Create Date: 2026-07-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_b8_remote_host_pairing"
down_revision = "0003_b7_teams"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "remote_host_pairings",
        sa.Column("host_id", sa.String(length=64), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["host_id"], ["remote_hosts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("host_id"),
    )


def downgrade() -> None:
    op.drop_table("remote_host_pairings")
