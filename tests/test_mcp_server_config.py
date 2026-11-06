from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MCP_SERVER = ROOT / "modules" / "agent-execution" / "source" / "src" / "mcp" / "server.ts"

# The ATerm MCP adapter (modules/agent-execution) is vendored locally and is
# not part of the published repository; skip instead of failing when absent.
pytestmark = pytest.mark.skipif(
    not MCP_SERVER.is_file(),
    reason="modules/agent-execution MCP adapter source not present in this checkout",
)


def test_mcp_defaults_to_kernel_port_11527():
    content = MCP_SERVER.read_text()
    assert "http://localhost:11527" in content
    assert "http://localhost:9600" not in content


def test_mcp_does_not_hardcode_local_dev_token():
    content = MCP_SERVER.read_text()
    assert "local-dev" not in content
    assert "ATERM_TOKEN" in content

