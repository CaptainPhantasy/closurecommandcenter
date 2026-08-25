import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from fastapi.testclient import TestClient

from server import app


def test_workspace_file_batch_and_diff_contract(tmp_path):
    client = TestClient(app)
    root = tmp_path / "landing"

    created = client.post(
        "/api/v1/workspaces",
        json={"name": "landing", "root": str(root), "createIfMissing": True},
    )
    assert created.status_code == 201, created.text
    workspace = created.json()["workspace"]
    assert workspace["id"].startswith("ws_")
    assert workspace["root"] == str(root)

    batch = client.post(
        f"/api/v1/workspaces/{workspace['id']}/files:batch",
        json={
            "atomic": True,
            "operations": [
                {"op": "mkdir", "path": "src"},
                {"op": "write", "path": "src/index.html", "content": "<h1>Floyd</h1>"},
            ],
        },
    )
    assert batch.status_code == 200, batch.text
    assert batch.json()["applied"] is True
    assert (root / "src" / "index.html").read_text() == "<h1>Floyd</h1>"

    content = client.get(
        f"/api/v1/workspaces/{workspace['id']}/files/content",
        params={"path": "src/index.html"},
    )
    assert content.status_code == 200
    assert content.json()["content"] == "<h1>Floyd</h1>"

    diff = client.post(
        f"/api/v1/workspaces/{workspace['id']}/files:diff",
        json={"operations": [{"op": "write", "path": "src/index.html", "content": "<h1>Floyd Vision</h1>"}]},
    )
    assert diff.status_code == 200
    assert "Floyd Vision" in diff.json()["diff"]


def test_job_preview_browser_deployment_quality_contract(tmp_path):
    client = TestClient(app)
    root = tmp_path / "landing"
    root.mkdir()

    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "landing-ops", "root": str(root)},
    ).json()["workspace"]

    job = client.post(
        f"/api/v1/workspaces/{workspace['id']}/jobs",
        json={"command": "python -c 'print(42)'", "timeoutMs": 30000},
    )
    assert job.status_code == 202, job.text
    job_id = job.json()["job"]["id"]
    status = client.get(f"/api/v1/jobs/{job_id}")
    assert status.status_code == 200
    assert status.json()["job"]["status"] in {"running", "succeeded"}
    logs = client.get(f"/api/v1/jobs/{job_id}/logs")
    assert logs.status_code == 200
    assert "items" in logs.json()

    preview = client.post(
        f"/api/v1/workspaces/{workspace['id']}/preview",
        json={"script": "dev", "port": 11527, "host": "127.0.0.1", "waitFor": {"url": "/", "status": 200, "timeoutMs": 10}},
    )
    assert preview.status_code == 202
    assert preview.json()["preview"]["url"] == "http://127.0.0.1:11527"

    browser = client.post("/api/v1/browser/sessions", json={"headless": True})
    assert browser.status_code == 201
    browser_id = browser.json()["browserSession"]["id"]
    nav = client.post(
        f"/api/v1/browser/sessions/{browser_id}/navigate",
        json={"url": "http://127.0.0.1:11527", "timeoutMs": 10},
    )
    assert nav.status_code == 200
    assert nav.json()["page"]["url"] == "http://127.0.0.1:11527"
    snapshot = client.get(f"/api/v1/browser/sessions/{browser_id}/snapshot")
    assert snapshot.status_code == 200
    assert "snapshot" in snapshot.json()

    deployment = client.post(
        f"/api/v1/workspaces/{workspace['id']}/deployments",
        json={"provider": "static", "environment": "preview", "buildCommand": "python -c 'print(ready)'", "outputDirectory": "."},
    )
    assert deployment.status_code == 202
    assert deployment.json()["deployment"]["status"] in {"queued", "building", "ready"}

    gate = client.post(
        f"/api/v1/workspaces/{workspace['id']}/quality-gates",
        json={"gates": [{"type": "command", "name": "smoke", "command": "python -c 'print(42)'"}]},
    )
    assert gate.status_code == 202
    gate_id = gate.json()["run"]["id"]
    gate_status = client.get(f"/api/v1/quality-gates/{gate_id}")
    assert gate_status.status_code == 200
    assert gate_status.json()["run"]["status"] in {"running", "passed"}


class _CdpVersionHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/json/version":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({"Browser": "Floyd Vision", "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/browser/test"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def test_live_browser_session_discovers_floyd_vision_cdp(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), _CdpVersionHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("FLOYD_VISION_CDP_URL", f"http://127.0.0.1:{server.server_port}")
        client = TestClient(app)

        created = client.post("/api/v1/browser/sessions", json={"headless": True, "requireLive": True})

        assert created.status_code == 201, created.text
        session = created.json()["browserSession"]
        assert session["mode"] == "live"
        assert session["cdp"]["Browser"] == "Floyd Vision"
        assert session["cdp"]["webSocketDebuggerUrl"] == "ws://127.0.0.1/devtools/browser/test"
    finally:
        server.shutdown()
        server.server_close()


def test_live_browser_session_reports_blocker_when_floyd_vision_unavailable(monkeypatch):
    monkeypatch.setenv("FLOYD_VISION_CDP_URL", "http://127.0.0.1:9")
    client = TestClient(app)

    created = client.post("/api/v1/browser/sessions", json={"headless": True, "requireLive": True})

    assert created.status_code == 503
    assert created.json()["detail"]["error"]["code"] == "floyd_vision_unavailable"

