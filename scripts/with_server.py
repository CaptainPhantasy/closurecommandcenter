#!/usr/bin/env python3
"""Run one or more local web servers for the lifetime of a command.

Examples:
    python scripts/with_server.py --server "npm run dev" --port 5173 -- python smoke.py

    python scripts/with_server.py \
      --server "cd backend && python server.py" --port 3000 \
      --server "cd frontend && npm run dev" --port 5173 \
      -- python smoke.py
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class ServerSpec:
    command: str
    port: int


@dataclass
class StartedServer:
    spec: ServerSpec
    process: subprocess.Popen[str]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start local server command(s), wait for their ports, run a command, then clean up.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/with_server.py --server \"npm run dev\" --port 5173 -- python smoke.py\n"
            "  python scripts/with_server.py --server \"python server.py\" --port 11527 -- pytest tests/test_workflows_ui.py -v\n"
        ),
    )
    parser.add_argument(
        "--server",
        action="append",
        required=True,
        help="Shell command that starts a server. Repeat once per server.",
    )
    parser.add_argument(
        "--port",
        action="append",
        type=int,
        required=True,
        help="Port that the matching --server command must listen on. Repeat once per server.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host used for readiness checks (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=45.0,
        help="Seconds to wait for each server port before failing (default: 45).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.25,
        help="Seconds between readiness probes (default: 0.25).",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command to run after all servers are ready. Prefix with -- to separate helper args.",
    )
    args = parser.parse_args(argv)

    if len(args.server) != len(args.port):
        parser.error("--server and --port must be provided the same number of times")

    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("missing command after --")

    if args.timeout <= 0:
        parser.error("--timeout must be > 0")
    if args.interval <= 0:
        parser.error("--interval must be > 0")

    return args


def port_is_open(host: str, port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_port(host: str, started: StartedServer, timeout: float, interval: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_is_open(host, started.spec.port):
            print(
                f"[with_server] ready: port {started.spec.port} for `{started.spec.command}`",
                flush=True,
            )
            return
        if started.process.poll() is not None:
            raise RuntimeError(
                f"server exited before port {started.spec.port} became ready: "
                f"command=`{started.spec.command}` exit={started.process.returncode}"
            )
        time.sleep(interval)
    raise TimeoutError(
        f"timed out after {timeout:.1f}s waiting for port {started.spec.port}: "
        f"command=`{started.spec.command}`"
    )


def start_servers(specs: list[ServerSpec], cwd: Path) -> list[StartedServer]:
    started: list[StartedServer] = []
    for spec in specs:
        print(f"[with_server] starting: `{spec.command}` (port {spec.port})", flush=True)
        proc = subprocess.Popen(
            spec.command,
            cwd=str(cwd),
            shell=True,
            stdin=subprocess.DEVNULL,
            stdout=sys.stdout,
            stderr=sys.stderr,
            text=True,
            start_new_session=True,
        )
        started.append(StartedServer(spec=spec, process=proc))
    return started


def stop_servers(started: list[StartedServer], graceful_timeout: float = 5.0) -> None:
    for server in reversed(started):
        proc = server.process
        if proc.poll() is not None:
            continue
        print(f"[with_server] stopping: `{server.spec.command}`", flush=True)
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except OSError:
            proc.terminate()

    deadline = time.monotonic() + graceful_timeout
    for server in reversed(started):
        proc = server.process
        if proc.poll() is not None:
            continue
        remaining = max(0.0, deadline - time.monotonic())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            print(f"[with_server] killing: `{server.spec.command}`", flush=True)
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                proc.kill()


def run_command(command: list[str]) -> int:
    print(f"[with_server] running: {' '.join(command)}", flush=True)
    try:
        completed = subprocess.run(command)
    except FileNotFoundError as exc:
        print(f"[with_server] command not found: {exc.filename}", file=sys.stderr, flush=True)
        return 127
    return completed.returncode


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    cwd = Path.cwd()
    specs = [ServerSpec(command=command, port=port) for command, port in zip(args.server, args.port)]

    for spec in specs:
        if port_is_open(args.host, spec.port):
            print(
                f"[with_server] refusing to start: {args.host}:{spec.port} already accepts connections",
                file=sys.stderr,
                flush=True,
            )
            return 2

    started: list[StartedServer] = []
    try:
        started = start_servers(specs, cwd)
        for server in started:
            wait_for_port(args.host, server, args.timeout, args.interval)
        return run_command(args.command)
    except KeyboardInterrupt:
        print("[with_server] interrupted", file=sys.stderr, flush=True)
        return 130
    except Exception as exc:
        print(f"[with_server] error: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        stop_servers(started)


if __name__ == "__main__":
    raise SystemExit(main())

