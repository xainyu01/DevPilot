"""Human-readable, policy-gated Markdown long-term memory."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Protocol
from uuid import uuid4

from packages.contracts import (
    MemoryEntry,
    MemoryScope,
    MemoryWriteResult,
    MemoryWriteStatus,
)

_MARKER = re.compile(r"^<!-- codeassist-memory (\{.*\}) -->$")
_SENSITIVE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:ghp|github_pat|xox[baprs])_[A-Za-z0-9_-]{12,}\b", re.I),
    re.compile(
        r"\b(?:api[_ -]?key|access[_ -]?token|auth[_ -]?token|password|passwd|secret)"
        r"\s*[:=]\s*\S+",
        re.I,
    ),
)


class MemoryRepositoryProtocol(Protocol):
    def save(self, entry: MemoryEntry, *, action: str) -> MemoryEntry:
        ...

    def soft_delete(self, memory_id: str, *, revision: int, content: str) -> None:
        ...


def contains_sensitive_data(text: str) -> bool:
    """Detect common credential-shaped values before writing durable memory."""
    return any(pattern.search(text) for pattern in _SENSITIVE_PATTERNS)


class LongTermMemoryStore:
    """Edit structured memory sections while keeping ``MEMORY.md`` readable."""

    def __init__(
        self,
        path: Path,
        *,
        owner_id: str = "local-user",
        scope: MemoryScope = MemoryScope.USER,
        repository: MemoryRepositoryProtocol | None = None,
    ) -> None:
        self.path = path.expanduser().resolve()
        self.owner_id = owner_id
        self.scope = scope
        self.repository = repository

    def list_entries(self, *, include_disabled: bool = True) -> list[MemoryEntry]:
        entries = self._read_entries()
        return entries if include_disabled else [entry for entry in entries if entry.enabled]

    def get(self, memory_id: str) -> MemoryEntry | None:
        return next((entry for entry in self._read_entries() if entry.id == memory_id), None)

    def add(
        self,
        *,
        key: str,
        content: str,
        source: str = "user",
    ) -> MemoryWriteResult:
        self._validate_content(content)
        entries = self._read_entries()
        if any(entry.key == key for entry in entries):
            raise ValueError(f"memory key already exists: {key}")
        now = datetime.now(UTC)
        entry = MemoryEntry(
            id=str(uuid4()),
            owner_id=self.owner_id,
            scope=self.scope,
            key=key,
            content=content.strip(),
            source=source,
            created_at=now,
            updated_at=now,
        )
        self._write_entries([*entries, entry])
        self._persist(entry, action="created")
        return MemoryWriteResult(status=MemoryWriteStatus.WRITTEN, entry=entry)

    def write_candidate(
        self,
        *,
        key: str,
        content: str,
        source: str = "agent",
        allow_sensitive: bool = False,
    ) -> MemoryWriteResult:
        """Apply the candidate → safety check → policy → write flow."""
        if contains_sensitive_data(content) and not allow_sensitive:
            return MemoryWriteResult(
                status=MemoryWriteStatus.BLOCKED_SENSITIVE,
                reason="candidate resembles a credential and was not written",
            )
        return self.add(key=key, content=content, source=source)

    def edit(self, memory_id: str, *, content: str, key: str | None = None) -> MemoryEntry:
        self._validate_content(content)
        entries = self._read_entries()
        current = self._require(entries, memory_id)
        now = datetime.now(UTC)
        updated = current.model_copy(
            update={
                "key": key or current.key,
                "content": content.strip(),
                "revision": current.revision + 1,
                "updated_at": now,
            }
        )
        self._write_entries([updated if item.id == memory_id else item for item in entries])
        self._persist(updated, action="edited")
        return updated

    def set_enabled(self, memory_id: str, enabled: bool) -> MemoryEntry:
        entries = self._read_entries()
        current = self._require(entries, memory_id)
        updated = current.model_copy(
            update={
                "enabled": enabled,
                "revision": current.revision + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        self._write_entries([updated if item.id == memory_id else item for item in entries])
        self._persist(updated, action="enabled" if enabled else "disabled")
        return updated

    def delete(self, memory_id: str) -> None:
        entries = self._read_entries()
        current = self._require(entries, memory_id)
        self._write_entries([item for item in entries if item.id != memory_id])
        if self.repository is not None:
            self.repository.soft_delete(
                memory_id,
                revision=current.revision + 1,
                content=current.content,
            )

    def enabled_text(self) -> str:
        return "\n\n".join(
            f"## {entry.key}\n{entry.content}"
            for entry in self.list_entries(include_disabled=False)
        )

    def _read_entries(self) -> list[MemoryEntry]:
        if not self.path.is_file():
            return []
        text = self.path.read_text(encoding="utf-8")
        lines = text.splitlines()
        entries: list[MemoryEntry] = []
        index = 0
        while index < len(lines):
            match = _MARKER.match(lines[index])
            if match is None:
                index += 1
                continue
            metadata: dict[str, Any] = json.loads(match.group(1))
            index += 1
            key = str(metadata.get("key", "memory"))
            if index < len(lines) and lines[index].startswith("## "):
                key = lines[index][3:].strip() or key
                index += 1
            content_lines: list[str] = []
            while index < len(lines) and _MARKER.match(lines[index]) is None:
                content_lines.append(lines[index])
                index += 1
            entries.append(
                MemoryEntry(
                    id=str(metadata["id"]),
                    owner_id=str(metadata.get("owner_id", self.owner_id)),
                    scope=metadata.get("scope", self.scope.value),
                    key=key,
                    content="\n".join(content_lines).strip(),
                    source=str(metadata.get("source", "user")),
                    enabled=bool(metadata.get("enabled", True)),
                    revision=int(metadata.get("revision", 1)),
                    created_at=_parse_datetime(metadata.get("created_at")),
                    updated_at=_parse_datetime(metadata.get("updated_at")),
                )
            )
        return entries

    def _write_entries(self, entries: list[MemoryEntry]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        sections = ["# CodeAssist Long-Term Memory", ""]
        for entry in entries:
            metadata = {
                "id": entry.id,
                "owner_id": entry.owner_id,
                "scope": entry.scope.value,
                "key": entry.key,
                "source": entry.source,
                "enabled": entry.enabled,
                "revision": entry.revision,
                "created_at": entry.created_at.isoformat(),
                "updated_at": entry.updated_at.isoformat(),
            }
            sections.extend(
                [
                    "<!-- codeassist-memory "
                    f"{json.dumps(metadata, ensure_ascii=False, sort_keys=True)} -->",
                    f"## {entry.key}",
                    entry.content.rstrip(),
                    "",
                ]
            )
        rendered = "\n".join(sections).rstrip() + "\n"
        with NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.path.parent, prefix=f".{self.path.name}.", delete=False
        ) as temporary:
            temporary.write(rendered)
            temporary_path = Path(temporary.name)
        temporary_path.replace(self.path)

    def _persist(self, entry: MemoryEntry, *, action: str) -> None:
        if self.repository is not None:
            self.repository.save(entry, action=action)

    @staticmethod
    def _require(entries: list[MemoryEntry], memory_id: str) -> MemoryEntry:
        for entry in entries:
            if entry.id == memory_id:
                return entry
        raise KeyError(f"memory entry not found: {memory_id}")

    @staticmethod
    def _validate_content(content: str) -> None:
        if not content.strip():
            raise ValueError("memory content cannot be empty")
        if contains_sensitive_data(content):
            raise ValueError("memory content resembles a credential and was blocked")


def _parse_datetime(value: Any) -> datetime:
    if value is None:
        return datetime.now(UTC)
    parsed = datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


__all__ = ["LongTermMemoryStore", "contains_sensitive_data"]
