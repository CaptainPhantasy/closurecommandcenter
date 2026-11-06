"""Tests for Bootstrap & Finisher dispatch endpoints (BETA-06)."""

import shutil

import pytest

from fastapi.testclient import TestClient

from server import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Point project discovery at a throwaway root so the suite does not
    depend on macOS /Volumes paths or run make in a real project."""
    project = tmp_path / "control-center"
    project.mkdir()
    (project / "Makefile").write_text(
        "bootstrap:\n\t@echo bootstrapped\n\ntest:\n\t@echo finisher-ok\n"
    )
    monkeypatch.setenv("CLOSURE_PROJECT_ROOTS", str(tmp_path))
    return TestClient(app)


requires_make = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed on this host")


class TestBootstrap:
    def test_bootstrap_nonexistent_project_returns_404(self, client):
        resp = client.post("/api/projects/DOES_NOT_EXIST_XYZ/bootstrap")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    @requires_make
    def test_bootstrap_control_center(self, client):
        """The control-center project exists and has a Makefile."""
        resp = client.post("/api/projects/control-center/bootstrap")
        assert resp.status_code == 200
        data = resp.json()
        assert data["project"] == "control-center"
        assert "path" in data
        assert "command" in data

    @requires_make
    def test_bootstrap_returns_project_path(self, client):
        resp = client.post("/api/projects/control-center/bootstrap")
        data = resp.json()
        assert "control-center" in data["path"]


class TestFinisher:
    def test_finisher_nonexistent_project_returns_404(self, client):
        resp = client.post("/api/projects/DOES_NOT_EXIST_XYZ/finisher")
        assert resp.status_code == 404

    @requires_make
    def test_finisher_control_center(self, client):
        resp = client.post("/api/projects/control-center/finisher")
        assert resp.status_code == 200
        data = resp.json()
        assert data["project"] == "control-center"
        assert isinstance(data["results"], list)
        assert len(data["results"]) > 0
        # Should have run 'make test' or similar
        assert "command" in data["results"][0]
        assert "returncode" in data["results"][0]

    @requires_make
    def test_finisher_results_include_command_output(self, client):
        resp = client.post("/api/projects/control-center/finisher")
        data = resp.json()
        result = data["results"][0]
        assert "output" in result or "error" in result

