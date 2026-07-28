"""CodeAssist CLI for local development and handover operations."""

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from packages.handover_agent import HandoverAgent

app = typer.Typer(help="CodeAssist 2.0 command line interface.")
handover_app = typer.Typer(help="Generate and inspect pause/resume handover documents.")
app.add_typer(handover_app, name="handover")
console = Console()


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address."),
    port: int = typer.Option(8000, min=1, max=65535, help="Bind port."),
) -> None:
    """Start the FastAPI service."""
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
