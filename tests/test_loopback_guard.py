"""Ops gate: state-changing requests are refused from non-loopback peers."""

import pytest
from fastapi.testclient import TestClient

import server
from server import app


@pytest.mark.parametrize("host,expected", [
    ("127.0.0.1", True), ("::1", True), ("localhost", True), ("testclient", True),
    ("192.0.2.10", False), ("2001:db8::1", False), ("", False), (None, False), ("not-an-ip", False),
])
def test_peer_is_loopback(host, expected):
    assert server._peer_is_loopback(host) is expected


def test_remote_post_is_refused():
    client = TestClient(app, client=("192.0.2.10", 50000))
    resp = client.post("/api/projects/anything/bootstrap")
    assert resp.status_code == 403
    assert "loopback" in resp.json()["detail"]


def test_remote_get_still_allowed():
    client = TestClient(app, client=("192.0.2.10", 50000))
    assert client.get("/").status_code == 200


def test_remote_post_allowed_with_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("CLOSURE_ALLOW_REMOTE_MUTATIONS", "1")
    monkeypatch.setenv("CLOSURE_PROJECT_ROOTS", "/nonexistent-root")
    client = TestClient(app, client=("192.0.2.10", 50000))
    assert client.post("/api/projects/anything/bootstrap").status_code == 404


def test_loopback_post_allowed(monkeypatch):
    monkeypatch.setenv("CLOSURE_PROJECT_ROOTS", "/nonexistent-root")
    client = TestClient(app, client=("127.0.0.1", 50000))
    assert client.post("/api/projects/anything/bootstrap").status_code == 404
