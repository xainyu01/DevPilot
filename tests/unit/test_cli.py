from __future__ import annotations

import pytest
import typer

import apps.cli.main as cli


def test_serve_reports_uvicorn_startup_error(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "_assert_port_available", lambda host, port: None)

    def fail_to_start(host: str, port: int) -> None:
        raise OSError("address already in use")

    monkeypatch.setattr(cli, "_run_uvicorn", fail_to_start)

    with pytest.raises(typer.Exit) as error:
        cli.serve(host="127.0.0.1", port=8765, open_browser=False)

    assert error.value.exit_code == 1
    assert "failed to start" in capsys.readouterr().out.lower()


def test_port_probe_rejects_an_already_bound_port() -> None:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        port = occupied.getsockname()[1]
        with pytest.raises(OSError):
            cli._assert_port_available("127.0.0.1", port)
