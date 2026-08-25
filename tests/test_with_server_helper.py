from __future__ import annotations

import shlex
import socket
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "with_server.py"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def test_with_server_help_documents_required_flags() -> None:
    result = subprocess.run(
        [sys.executable, str(HELPER), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert "--server SERVER" in result.stdout
    assert "--port PORT" in result.stdout
    assert "Command to run after all servers are ready" in result.stdout


def test_with_server_runs_command_then_cleans_up() -> None:
    port = _free_port()
    result = subprocess.run(
        [
            sys.executable,
            str(HELPER),
            "--server",
            f"{shlex.quote(sys.executable)} -m http.server {port} --bind 127.0.0.1",
            "--port",
            str(port),
            "--timeout",
            "10",
            "--",
            sys.executable,
            "-c",
            (
                "from urllib.request import urlopen; "
                f"print(urlopen('http://127.0.0.1:{port}', timeout=5).status)"
            ),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "[with_server] ready: port" in combined
    assert "[with_server] stopping:" in combined
    assert "200" in combined
    assert not _port_open(port), f"helper left port {port} listening"

