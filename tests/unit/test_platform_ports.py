from pathlib import Path
from subprocess import CompletedProcess

from packages.platform import (
    BrowserLauncher,
    PathResolver,
    PlatformAdapter,
    PlatformCapabilities,
    ProcessRunner,
    ShellRunner,
)


class FakePaths:
    def resolve(self, path: str | Path, *, base_dir: Path) -> Path:
        candidate = Path(path)
        return (candidate if candidate.is_absolute() else base_dir / candidate).resolve()


class FakeProcesses:
    def run(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout_seconds: float,
    ) -> CompletedProcess[str]:
        return CompletedProcess(command, 0, "", "")


class FakeBrowser:
    def open(self, url: str) -> bool:
        return url.startswith("http")


class FakeShell(FakeProcesses):
    pass


class FakePlatform:
    capabilities = PlatformCapabilities(
        platform="test",
        path_style="posix",
        shell="test-shell",
        browser=True,
        process_runner=True,
    )
    paths = FakePaths()
    processes = FakeProcesses()
    browser = FakeBrowser()
    shell = FakeShell()


def test_platform_ports_are_implementable_without_os_specific_core_code(tmp_path: Path) -> None:
    platform = FakePlatform()

    assert isinstance(platform.paths, PathResolver)
    assert isinstance(platform.processes, ProcessRunner)
    assert isinstance(platform.browser, BrowserLauncher)
    assert isinstance(platform.shell, ShellRunner)
    assert isinstance(platform, PlatformAdapter)
    assert platform.paths.resolve("repo", base_dir=tmp_path) == (tmp_path / "repo").resolve()
    assert platform.capabilities.status == "available"
