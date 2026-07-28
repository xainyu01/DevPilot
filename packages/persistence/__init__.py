"""SQLAlchemy-backed persistence for B3.

The package is the database adapter boundary.  Domain contracts remain in
``packages.contracts`` and can be used without importing this package.
"""

from .database import Database, default_database_url
from .models import Base
from .repositories import (
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

__all__ = [
    "Base",
    "CheckpointRepository",
    "Database",
    "MemoryRepository",
    "ProjectRepository",
    "RepositoryProfileRepository",
    "RuleRepository",
    "RunRepository",
    "SessionRepository",
    "TeamRepository",
    "WorkflowRepository",
    "default_database_url",
]
