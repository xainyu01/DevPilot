"""CodeAssist CLI for local development and handover operations."""

import json
import os
import socket
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import typer
from rich.console import Console
from rich.table import Table

from packages.handover_agent import HandoverAgent
from packages.persistence import backup_sqlite_database, default_database_url

app = typer.Typer(help="CodeAssist 2.0 command line interface.")
auth_app = typer.Typer(help="Obtain a short-lived JWT for authenticated API commands.")
database_app = typer.Typer(help="Create safe local database recovery snapshots.")
handover_app = typer.Typer(help="Generate and inspect pause/resume handover documents.")
project_app = typer.Typer(help="Manage projects through the local API.")
session_app = typer.Typer(help="Manage sessions and send messages through the local API.")
workflow_app = typer.Typer(help="Inspect and start development workflows through the local API.")
app.add_typer(handover_app, name="handover")
app.add_typer(auth_app, name="auth")
app.add_typer(database_app, name="database")
app.add_typer(project_app, name="project")
app.add_typer(session_app, name="session")
app.add_typer(workflow_app, name="workflow")
console = Console()


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address."),
    port: int = typer.Option(8000, min=1, max=65535, help="Bind port."),
    open_browser: bool = typer.Option(
        True,
        "--open-browser/--no-open-browser",
        help="Open the local Web workbench after startup.",
    ),
) -> None:
    """Start FastAPI and optionally open the Vite Web workbench."""
    try:
        _assert_port_available(host, port)
    except OSError as exc:
        console.print(f"[red]Cannot start local Web service on {host}:{port}: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    browser_url = f"http://{'127.0.0.1' if host in {'0.0.0.0', '::'} else host}:{port}"
    console.print(f"Web workbench: {browser_url}")
    browser_timer = None
    if open_browser:
        import webbrowser
        from threading import Timer

        browser_timer = Timer(0.8, webbrowser.open, args=(browser_url,))
        browser_timer.daemon = True
        browser_timer.start()

    try:
        _run_uvicorn(host, port)
    except OSError as exc:
        if browser_timer is not None:
            browser_timer.cancel()
        console.print(f"[red]Local Web service failed to start on {host}:{port}: {exc}[/red]")
        raise typer.Exit(code=1) from exc


def _assert_port_available(host: str, port: int) -> None:
    """Fail early with a clear message instead of hiding a bind error in Uvicorn logs."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((host, port))


def _run_uvicorn(host: str, port: int) -> None:
    import uvicorn

    uvicorn.run("apps.api.main:app", host=host, port=port, reload=False)


@app.command()
def progress() -> None:
    """Show the current implementation progress."""
    agent = HandoverAgent.from_workspace()
    snapshot = agent.progress
    table = Table(title=f"CodeAssist progress: {snapshot.overall_percent}%")
    table.add_column("Batch")
    table.add_column("Status")
    table.add_column("Progress")
    table.add_column("Scope")
    for batch in snapshot.batches:
        table.add_row(batch.id, batch.status, f"{batch.percent}%", batch.title)
    console.print(table)
    console.print(f"Current batch: {snapshot.current_batch}")
    console.print(f"Next action: {snapshot.next_action}")


@handover_app.command("write")
def write_handover(
    reason: str = typer.Option("requested", help="Why the handover was generated."),
    output: Path | None = typer.Option(None, help="Optional output path under the docs folder."),
) -> None:
    """Write a handover document for pausing or transferring the task."""
    agent = HandoverAgent.from_workspace()
    target = agent.write_handover(reason=reason, output_path=output)
    console.print(f"Handover written to: {target}")


@handover_app.command("preview")
def preview_handover(
    reason: str = typer.Option("requested", help="Why the handover is being previewed."),
) -> None:
    """Print a handover document without writing it."""
    agent = HandoverAgent.from_workspace()
    console.print(agent.render_handover(reason=reason))


@app.command()
def doctor() -> None:
    """Check the B1 workspace and dependency policy."""
    root = Path(__file__).resolve().parents[2]
    checks = {
        "pyproject.toml": (root / "pyproject.toml").is_file(),
        "docs/progress.json": (root / "docs" / "progress.json").is_file(),
        "docs/": (root / "docs").is_dir(),
        "uv.lock": (root / "uv.lock").is_file(),
    }
    for name, passed in checks.items():
        console.print(f"{'[green]PASS[/green]' if passed else '[red]FAIL[/red]'} {name}")
    if not all(checks.values()):
        raise typer.Exit(code=1)


@database_app.command("backup")
def backup_database(
    destination: Path = typer.Argument(..., help="New SQLite snapshot path; must not exist."),
    database_url: str | None = typer.Option(
        None,
        help="Optional sqlite:/// URL; defaults to CODEASSIST_DATABASE_URL or the local database.",
    ),
) -> None:
    """Write a non-destructive consistent snapshot of a file-backed SQLite database."""
    root = Path(__file__).resolve().parents[2]
    effective_url = database_url or os.environ.get("CODEASSIST_DATABASE_URL")
    try:
        target = backup_sqlite_database(
            effective_url or default_database_url(root), destination
        )
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"SQLite backup written to: {target}")


def _api_request(
    method: str,
    path: str,
    *,
    payload: dict[str, object] | None = None,
    api_url: str,
) -> object:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    access_token = os.environ.get("CODEASSIST_ACCESS_TOKEN")
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    request = Request(
        f"{api_url.rstrip('/')}{path}",
        data=body,
        method=method,
        headers=headers,
    )
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - user-selected local endpoint
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise typer.BadParameter(f"API returned {exc.code}: {detail}") from exc
    except URLError as exc:
        raise typer.BadParameter(f"Cannot reach API at {api_url}: {exc.reason}") from exc


def _print_json(value: object) -> None:
    console.print_json(json.dumps(value, ensure_ascii=False, default=str))


@auth_app.command("login")
def login(
    username: str = typer.Argument(..., help="One of the configured CodeAssist user IDs."),
    password: str = typer.Option(..., prompt=True, hide_input=True, help="Account password."),
    api_url: str = typer.Option("http://127.0.0.1:8000"),
) -> None:
    """Print a signed JWT for use as ``CODEASSIST_ACCESS_TOKEN`` in this shell."""
    _print_json(
        _api_request(
            "POST",
            "/api/v1/auth/login",
            payload={"username": username, "password": password},
            api_url=api_url,
        )
    )


@project_app.command("list")
def list_projects(api_url: str = typer.Option("http://127.0.0.1:8000")) -> None:
    """List available projects."""
    _print_json(_api_request("GET", "/api/v1/projects", api_url=api_url))


@project_app.command("add")
def add_project(
    name: str,
    root_path: Path,
    api_url: str = typer.Option("http://127.0.0.1:8000"),
) -> None:
    """Register a repository as a project."""
    _print_json(
        _api_request(
            "POST",
            "/api/v1/projects",
            payload={"name": name, "root_path": str(root_path)},
            api_url=api_url,
        )
    )


@project_app.command("scan")
def scan_project(project_id: str, api_url: str = typer.Option("http://127.0.0.1:8000")) -> None:
    """Scan and index a project repository."""
    _print_json(
        _api_request(
            "POST", f"/api/v1/projects/{project_id}/repository/scan", api_url=api_url
        )
    )


@session_app.command("list")
def list_sessions(
    project_id: str | None = typer.Option(None),
    api_url: str = typer.Option("http://127.0.0.1:8000"),
) -> None:
    """List project sessions."""
    suffix = f"?project_id={project_id}" if project_id else ""
    _print_json(_api_request("GET", f"/api/v1/sessions{suffix}", api_url=api_url))


@session_app.command("create")
def create_session(
    thread_id: str,
    project_id: str | None = typer.Option(None),
    title: str | None = typer.Option(None),
    api_url: str = typer.Option("http://127.0.0.1:8000"),
) -> None:
    """Create a conversation session."""
    _print_json(
        _api_request(
            "POST",
            "/api/v1/sessions",
            payload={"thread_id": thread_id, "project_id": project_id, "title": title},
            api_url=api_url,
        )
    )


@session_app.command("chat")
def chat(
    session_id: str,
    text: str,
    provider: str = typer.Option("fake"),
    model: str = typer.Option("fake-model"),
    api_url: str = typer.Option("http://127.0.0.1:8000"),
) -> None:
    """Send a message and print the deterministic or configured model response."""
    _print_json(
        _api_request(
            "POST",
            f"/api/v1/sessions/{session_id}/runs",
            payload={
                "provider": provider,
                "model": model,
                "message": {"role": "user", "content": [{"type": "text", "text": text}]},
            },
            api_url=api_url,
        )
    )


@workflow_app.command("list")
def list_workflows(
    project_id: str | None = typer.Option(None), api_url: str = typer.Option("http://127.0.0.1:8000")
) -> None:
    """List development workflows."""
    suffix = f"?project_id={project_id}" if project_id else ""
    _print_json(_api_request("GET", f"/api/v1/workflows{suffix}", api_url=api_url))
