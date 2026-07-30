"""SQLAlchemy-backed persistence for B3.

The package is the database adapter boundary.  Domain contracts remain in
``packages.contracts`` and can be used without importing this package.
"""

from .backup import backup_sqlite_database
from .database import Database, default_database_url
from .models import Base
from .repositories import (
    ApprovalRepository,
    AuditRepository,
    CheckpointRepository,
    MemoryRepository,
    ProjectRepository,
    RepositoryProfileRepository,
    RuleRepository,
    RunRepository,
    SessionRepository,
    TeamRepository,
    WorkflowRepository,
)
from .runtime_adapters import PersistentApprovalStore

__all__ = [
    "ApprovalRepository",
    "AuditRepository",
    "Base",
    "backup_sqlite_database",
    "CheckpointRepository",
    "Database",
    "MemoryRepository",
    "ProjectRepository",
    "PersistentApprovalStore",
    "RepositoryProfileRepository",
    "RuleRepository",
    "RunRepository",
    "SessionRepository",
    "TeamRepository",
    "WorkflowRepository",
    "default_database_url",
]
