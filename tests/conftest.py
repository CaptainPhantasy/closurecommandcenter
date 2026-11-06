"""Shared pytest fixtures for TCC tests."""

from __future__ import annotations

import os
import pytest

from _helpers import base_url, is_server_up, ARTIFACTS_DIR


@pytest.fixture(autouse=True)
def _skip_live_server_tests_when_server_down(request):
    """Skip only tests marked live_server when the launchd-managed app is down."""
    if request.node.get_closest_marker("live_server") is None:
        return
    if not is_server_up(timeout=2.0):
        pytest.skip(
            f"TCC server not reachable at {base_url()} — start it before running live-server tests"
        )


@pytest.fixture(scope="session")
def tcc_base() -> str:
    return base_url()


@pytest.fixture(scope="session")
def headed() -> bool:
    return os.environ.get("HEADED", "0") == "1"


@pytest.fixture
def artifacts_dir(request) -> str:
    """Per-test artifacts directory. Cleaned at fixture teardown only on success."""
    name = request.node.name.replace("[", "_").replace("]", "").replace("/", "_")
    target = ARTIFACTS_DIR / name
    target.mkdir(parents=True, exist_ok=True)
    return str(target)



def pytest_configure(config):
    config.addinivalue_line(
        "markers", "live_server: needs the Kernel server running at TCC_BASE_URL (skipped when down)"
    )
