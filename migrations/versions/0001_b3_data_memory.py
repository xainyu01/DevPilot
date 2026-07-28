"""Create B3 conversation, memory, context and durable run tables."""

from __future__ import annotations

from alembic import op

from packages.persistence.models import Base

revision = "0001_b3_data_memory"
down_revision = None
branch_labels = None
depends_on = None


_B3_TABLES = (
    "projects",
    "conversation_sessions",
    "messages",
    "content_blocks",
    "session_summaries",
    "memory_entries",
    "memory_revisions",
    "project_rules",
    "agent_runs",
    "run_events",
    "checkpoints",
    "approvals",
    "audit_logs",
)


def upgrade() -> None:
    # Keep this historical revision stable when later batches add tables to
    # the model metadata.  Later revisions use explicit Alembic operations.
    tables = [Base.metadata.tables[name] for name in _B3_TABLES]
    Base.metadata.create_all(op.get_bind(), tables=tables)


def downgrade() -> None:
    tables = [Base.metadata.tables[name] for name in reversed(_B3_TABLES)]
    Base.metadata.drop_all(op.get_bind(), tables=tables)
