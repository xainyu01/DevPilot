"""Persist real Agent requests, results, usage, verification and approvals.

Revision ID: 0005_real_agent_runs
Revises: 0004_b8_remote_host_pairing
Create Date: 2026-07-31
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_real_agent_runs"
down_revision = "0004_b8_remote_host_pairing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("agent_runs")
    }
    additions = (
        sa.Column("request_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("usage_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("verification_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("changes_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("stop_reason", sa.String(length=100), nullable=True),
        sa.Column("provider_request_id", sa.String(length=200), nullable=True),
        sa.Column("pending_approval_json", sa.JSON(), nullable=True),
    )
    with op.batch_alter_table("agent_runs") as batch:
        for column in additions:
            if column.name not in run_columns:
                batch.add_column(column)
    approval_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("approvals")
    }
    if "request_json" not in approval_columns:
        with op.batch_alter_table("approvals") as batch:
            batch.add_column(
                sa.Column("request_json", sa.JSON(), nullable=False, server_default="{}")
            )


def downgrade() -> None:
    with op.batch_alter_table("approvals") as batch:
        batch.drop_column("request_json")
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_column("pending_approval_json")
        batch.drop_column("provider_request_id")
        batch.drop_column("stop_reason")
        batch.drop_column("changes_json")
        batch.drop_column("verification_json")
        batch.drop_column("usage_json")
        batch.drop_column("result_json")
        batch.drop_column("request_json")
