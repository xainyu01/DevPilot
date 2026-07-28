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


# TODO（后续 Linux/macOS 适配）：当前只声明平台能力，不实现具体平台探测逻辑。
@runtime_checkable
class PathResolver(Protocol):
    """Resolve user paths without leaking platform rules into domain code."""

    # TODO（后续平台适配）：由 Windows/Linux/macOS 适配器实现路径规范化。
    def resolve(self, path: str | Path, *, base_dir: Path) -> Path:
        ...


# TODO（后续 Linux/macOS 适配）：当前只保留受限进程执行契约。
@runtime_checkable
class ProcessRunner(Protocol):
    """Run a bounded process through a platform-owned implementation."""

    # TODO（后续平台适配）：实现超时、工作目录和输出限制，不在领域层直接调用 subprocess。
    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
    ) -> CompletedProcess[str]:
        ...


# TODO（后续桌面/平台适配）：当前只保留浏览器启动契约。
@runtime_checkable
class BrowserLauncher(Protocol):
    """Open a browser through the host platform, when permitted."""

    # TODO（后续平台适配）：实现各平台浏览器启动并返回明确成功/失败状态。
    def open(self, url: str) -> bool:
        ...


# TODO（后续 Linux/macOS 适配）：Shell 实现必须位于平台边缘并经过策略校验。
@runtime_checkable
class ShellRunner(Protocol):
    """Expose a named shell adapter instead of hard-coding a shell command."""

    # TODO（后续平台适配）：不要在领域核心中硬编码 PowerShell、bash 或 zsh 命令。
    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
    ) -> CompletedProcess[str]:
        ...


# TODO（后续平台适配）：当前只定义适配器组合，不提供 Linux/macOS 具体实例。
@runtime_checkable
class PlatformAdapter(Protocol):
    """Bundle the ports and capability declaration for one host platform."""

    # TODO（后续平台适配）：返回平台能力和未实现能力的结构化状态。
    @property
    def capabilities(self) -> PlatformCapabilities:
        ...

    # TODO（后续平台适配）：注入对应平台的路径解析器。
    @property
    def paths(self) -> PathResolver:
        ...

    # TODO（后续平台适配）：注入对应平台的进程执行器。
    @property
    def processes(self) -> ProcessRunner:
        ...

    # TODO（后续平台适配）：注入对应平台的浏览器启动器。
    @property
    def browser(self) -> BrowserLauncher:
        ...

    # TODO（后续平台适配）：注入经过策略限制的 Shell 执行器。
    @property
    def shell(self) -> ShellRunner:
        ...
