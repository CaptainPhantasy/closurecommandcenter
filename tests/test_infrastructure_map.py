"""Tests for the Infrastructure Cartography embed (Step 13).

Verifies the vendored infrastructure-map.html file is present, served by the
StaticFiles mount at /static/infrastructure-map.html, and renders the expected
diagram content (sanity checks against known headings + provider cards).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


# Bypass conftest's autouse server-required fixture for in-process tests.
@pytest.fixture(scope="module", autouse=True)
def _ensure_server_up():  # type: ignore[no-redef]
    yield


@pytest.fixture
def client() -> TestClient:
    return TestClient(server.app)


VENDORED_MAP = ROOT / "static" / "infrastructure-map.html"


def test_vendored_file_exists() -> None:
    """The vendored map must be checked in alongside the project."""
    assert VENDORED_MAP.is_file(), (
        f"missing {VENDORED_MAP} — run scripts/refresh-infrastructure-map.sh"
    )


def test_vendored_file_size() -> None:
    """Sanity-check the privacy-safe placeholder is not empty."""
    size = VENDORED_MAP.stat().st_size
    assert 200 < size < 10_000, f"unexpected size: {size}"


def test_vendored_file_has_doctype() -> None:
    head = VENDORED_MAP.read_text()[:64]
    assert head.lower().startswith("<!doctype html>")


def test_static_endpoint_serves_map(client: TestClient) -> None:
    resp = client.get("/static/infrastructure-map.html")
    assert resp.status_code == 200
    assert "html" in resp.headers.get("content-type", "").lower()


def test_map_contains_private_data_boundary(client: TestClient) -> None:
    """The checked-in map must direct operators to local data without embedding it."""
    body = client.get("/static/infrastructure-map.html").text
    assert "No private topology is embedded" in body
    assert "INFRA_MAP_SOURCE" in body


def test_index_html_native_mount_present(client: TestClient) -> None:
    """The System Map tab must have a native mount div instead of an iframe."""
    body = client.get("/").text
    assert 'id="system-map-mount"' in body
    assert "/static/infrastructure-map.html" in body
    # Tab label should be System Map (Dashboard-native naming)
    assert "System Map" in body


def test_refresh_script_present() -> None:
    """The vendor refresh script is part of the project."""
    script = ROOT / "scripts" / "refresh-infrastructure-map.sh"
    assert script.is_file()
    # Must be executable (0o755)
    assert script.stat().st_mode & 0o111, "refresh script is not executable"


def test_docs_present() -> None:
    """Publication-safe operator guidance is present without local governance files."""
    doc = ROOT / "docs" / "infrastructure-map.md"
    assert not doc.exists()
    assert (ROOT / "README.md").is_file()
    assert (ROOT / "scripts" / "refresh-infrastructure-map.sh").is_file()

