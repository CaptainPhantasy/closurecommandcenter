"""Tests for WebSocket endpoints: /ws/pty, /ws/collab, /ws/events.

Requires the live Kernel server running on port 11527."""

import asyncio
import json

import pytest


def _ws_url(path: str, host: str = "localhost", port: int = 11527) -> str:
    return f"ws://{host}:{port}{path}"


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.mark.live_server
class TestWsPty:
    def test_pty_open_and_echo(self):
        import websockets

        async def _test():
            async with websockets.connect(_ws_url("/ws/pty")) as ws:
                await ws.send(
                    json.dumps({"type": "open", "cwd": "/tmp", "cols": 80, "rows": 24})
                )
                resp = await asyncio.wait_for(ws.recv(), timeout=5)
                msg = json.loads(resp)
                assert msg["type"] == "ready"
                assert "sessionId" in msg
                assert "pid" in msg
                assert isinstance(msg["pid"], int)
                # Send echo
                await ws.send(
                    json.dumps({"type": "in", "data": "echo WS_TEST_MARKER\n"})
                )
                await asyncio.sleep(1.5)
                output = ""
                for _ in range(15):
                    try:
                        resp = await asyncio.wait_for(ws.recv(), timeout=1)
                        msg = json.loads(resp)
                        if msg.get("type") == "out":
                            output += msg.get("data", "")
                    except asyncio.TimeoutError:
                        break
                assert "WS_TEST_MARKER" in output

        asyncio.run(_test())

    def test_pty_resize_accepted(self):
        import websockets

        async def _test():
            async with websockets.connect(_ws_url("/ws/pty")) as ws:
                await ws.send(
                    json.dumps({"type": "open", "cwd": "/tmp", "cols": 80, "rows": 24})
                )
                resp = await asyncio.wait_for(ws.recv(), timeout=5)
                assert json.loads(resp)["type"] == "ready"
                # Resize should not crash
                await ws.send(json.dumps({"type": "resize", "cols": 120, "rows": 40}))
                await asyncio.sleep(0.5)

        asyncio.run(_test())


@pytest.mark.live_server
class TestWsCollab:
    def test_fan_out(self):
        import websockets

        async def _test():
            async with (
                websockets.connect(_ws_url("/ws/collab?room=test-room")) as ws1,
                websockets.connect(_ws_url("/ws/collab?room=test-room")) as ws2,
            ):
                await ws1.send(
                    json.dumps(
                        {
                            "type": "hello",
                            "peer": {"id": "p1", "name": "Alice", "color": "#f00"},
                            "room": "test-room",
                        }
                    )
                )
                resp = await asyncio.wait_for(ws2.recv(), timeout=3)
                msg = json.loads(resp)
                assert msg["type"] == "hello"
                assert msg["peer"]["name"] == "Alice"

        asyncio.run(_test())


@pytest.mark.live_server
class TestWsEvents:
    def test_initial_sessions_list(self):
        import websockets

        async def _test():
            async with websockets.connect(_ws_url("/ws/events")) as ws:
                resp = await asyncio.wait_for(ws.recv(), timeout=5)
                msg = json.loads(resp)
                assert msg["type"] == "sessions_list"
                assert isinstance(msg["sessions"], list)

        asyncio.run(_test())

    def test_session_created_event(self):
        import websockets
        import urllib.request

        async def _test():
            async with websockets.connect(_ws_url("/ws/events")) as ws:
                # Consume initial sessions_list
                await asyncio.wait_for(ws.recv(), timeout=5)
                # Create a session via HTTP
                data = json.dumps(
                    {
                        "action": "create",
                        "session": "ws-event-test",
                        "command": "/bin/bash",
                    }
                ).encode()
                req = urllib.request.Request(
                    "http://localhost:11527/api/do",
                    data=data,
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req)
                # Read event
                resp = await asyncio.wait_for(ws.recv(), timeout=5)
                msg = json.loads(resp)
                assert msg["type"] == "session_created"
                assert msg["session"]["name"] == "ws-event-test"
                # Cleanup
                data = json.dumps(
                    {"action": "delete", "session": "ws-event-test"}
                ).encode()
                req = urllib.request.Request(
                    "http://localhost:11527/api/do",
                    data=data,
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req)

        asyncio.run(_test())

