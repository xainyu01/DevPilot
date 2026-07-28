"""Create B3 conversation, memory, context and durable run tables."""

from __future__ import annotations

from alembic import op

from packages.persistence.models import Base

revision = "0001_b3_data_memory"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The first revision is generated from the single source-of-truth model
    # metadata.  Later revisions should use explicit Alembic operations.
    Base.metadata.create_all(op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind())
