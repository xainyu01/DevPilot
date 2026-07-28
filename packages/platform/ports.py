"""Platform boundaries for future Linux and macOS adapters.

The domain layer depends on these ports rather than importing PowerShell,
POSIX commands, OS-specific path rules, or GUI APIs. B6 supplies the contract;
platform-specific implementations remain TODO until their runtime validation
is scheduled.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from subprocess import CompletedProcess
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class PlatformCapabilities:
    """Advertised capabilities; unavailable features must be explicit."""

    platform: str
    path_style: str
    shell: str
    browser: bool
    process_runner: bool
    status: str = "available"
    notes: tuple[str, ...] = ()


@runtime_checkable
class PathResolver(Protocol):
    """Resolve user paths without leaking platform rules into domain code."""

    def resolve(self, path: str | Path, *, base_dir: Path) -> Path:
        ...


@runtime_checkable
class ProcessRunner(Protocol):
    """Run a bounded process through a platform-owned implementation."""

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
    ) -> CompletedProcess[str]:
        ...


@runtime_checkable
class BrowserLauncher(Protocol):
    """Open a browser through the host platform, when permitted."""

    def open(self, url: str) -> bool:
        ...


@runtime_checkable
class ShellRunner(Protocol):
    """Expose a named shell adapter instead of hard-coding a shell command."""

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
    ) -> CompletedProcess[str]:
        ...


@runtime_checkable
class PlatformAdapter(Protocol):
    """Bundle the ports and capability declaration for one host platform."""

    @property
    def capabilities(self) -> PlatformCapabilities:
        ...

    @property
    def paths(self) -> PathResolver:
        ...

    @property
    def processes(self) -> ProcessRunner:
        ...

    @property
    def browser(self) -> BrowserLauncher:
        ...

    @property
    def shell(self) -> ShellRunner:
        ...
