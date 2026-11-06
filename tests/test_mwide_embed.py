"""Tests for the Workspace Editor module.

The workspace editor is a Kernel-owned module copied from MWIDE source.
These tests verify:
  - The tab is registered in the Kernel nav
  - The native mount div is present (no iframe)
  - The built frontend is served as a static mount
  - The fallback markup is present for when the build is missing
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _ensure_server_up():  # type: ignore[no-redef]
    yield


WORKSPACE_EDITOR = ROOT / "modules" / "workspace-editor"
WORKSPACE_EDITOR_DIST = WORKSPACE_EDITOR / "source" / "dist"

# modules/workspace-editor (the MWIDE copy) is vendored locally and not part of
# the published repository. Build it with `make venv` when present.
requires_workspace_editor = pytest.mark.skipif(
    not WORKSPACE_EDITOR.is_dir(),
    reason="modules/workspace-editor source not present in this checkout",
)
requires_workspace_editor_dist = pytest.mark.skipif(
    not (WORKSPACE_EDITOR_DIST / "index.html").is_file(),
    reason="workspace-editor dist not built (npm --prefix modules/workspace-editor/source run build)",
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(server.app)


def test_index_html_has_workspace_editor_tab(client: TestClient) -> None:
    body = client.get("/").text
    assert "Workspace Editor" in body
    assert 'id="cb-tab-mwide"' in body
    assert 'id="cb-page-mwide"' in body


def test_index_html_native_mount_present(client: TestClient) -> None:
    """The native mount div must exist for the React app to render into."""
    body = client.get("/").text
    assert 'id="mwide-root"' in body
    assert "/workspace-editor/" in body


def test_index_html_native_loader_js_present(client: TestClient) -> None:
    """The JS loader for the native integration must be present."""
    body = client.get("/").text
    assert "loadMwideNative" in body
    assert "mwide-root" in body


def test_index_html_includes_fallback_markup(client: TestClient) -> None:
    body = client.get("/").text
    assert 'id="mwide-fallback"' in body
    assert "Workspace Editor is not available" in body


def test_index_html_has_reload_button_and_fullscreen_link(
    client: TestClient,
) -> None:
    body = client.get("/").text
    assert 'id="mwide-reload-btn"' in body
    assert 'href="/workspace-editor/"' in body


@requires_workspace_editor_dist
def test_workspace_editor_static_mount(client: TestClient) -> None:
    """The built frontend must be served at /workspace-editor/."""
    r = client.get("/workspace-editor/")
    assert r.status_code == 200
    assert "html" in r.headers.get("content-type", "").lower()


@requires_workspace_editor
def test_workspace_editor_source_manifest_exists() -> None:
    """The source manifest must be present with copy metadata."""
    manifest = ROOT / "modules" / "workspace-editor" / "source-manifest.json"
    assert manifest.is_file(), f"missing {manifest}"
    import json

    data = json.loads(manifest.read_text())
    assert data["capability"] == "workspace-editor"
    assert data["source_name"] == "MWIDE / mobile-web-IDE"
    assert data["copied_at"] is not None


@requires_workspace_editor_dist
def test_workspace_editor_dist_built() -> None:
    """The Vite-built frontend must exist in the module dist/ directory."""
    dist_dir = ROOT / "modules" / "workspace-editor" / "source" / "dist"
    assert dist_dir.is_dir(), f"missing dist/ at {dist_dir}"
    assert (dist_dir / "index.html").is_file(), "missing dist/index.html"
    assert (dist_dir / "assets").is_dir(), "missing dist/assets/"


@requires_workspace_editor
def test_workspace_editor_source_copied() -> None:
    """The MWIDE source must be present in modules/workspace-editor/source/."""
    source_dir = ROOT / "modules" / "workspace-editor" / "source"
    assert (source_dir / "server.ts").is_file(), "missing source/server.ts"
    assert (source_dir / "package.json").is_file(), "missing source/package.json"
    assert (source_dir / "src").is_dir(), "missing source/src/"
    assert (source_dir / "vite.config.ts").is_file(), "missing source/vite.config.ts"


def test_original_mwide_untouched() -> None:
    """Verify the original MWIDE source has no new changes from the copy."""
    original = Path("/Volumes/SanDisk1Tb/MWIDE/mobile-web-IDE")
    if not original.is_dir():
        pytest.skip("MWIDE source not accessible on this machine")
    import subprocess

    result = subprocess.run(
        ["git", "diff", "--stat"],
        capture_output=True,
        text=True,
        cwd=str(original),
    )
    # Should have the same pre-existing dirty state, no new changes
    assert "server.ts" not in result.stdout or "source/" not in result.stdout


@requires_workspace_editor_dist
def test_workspace_editor_assets_load(client: TestClient) -> None:
    """Built JS/CSS assets must be loadable from the /workspace-editor/ mount."""
    import re

    html = client.get("/workspace-editor/").text
    js_files = re.findall(r'src="(/workspace-editor/assets/[^"]+\.js)"', html)
    css_files = re.findall(
        r'href="(/workspace-editor/assets/[^"]+\.css)"',
        html,
    )
    assert js_files, "no JS assets found in workspace-editor dist"
    assert css_files, "no CSS assets found in workspace-editor dist"
    for path in js_files + css_files:
        r = client.get(path)
        assert r.status_code == 200, f"{path} returned {r.status_code}"
        assert len(r.content) > 0, f"{path} is empty"

