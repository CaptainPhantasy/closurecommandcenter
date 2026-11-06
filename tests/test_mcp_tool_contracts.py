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


def test_mcp_exposes_single_surface_tools():
    content = MCP_SERVER.read_text()
    for name in ["aterm_status", "aterm_record", "aterm_checkpoint", "aterm_verify", "aterm_batch", "aterm_automate"]:
        assert f'"{name}"' in content
    assert '"aterm_replay"' not in content


def test_mcp_tools_forward_to_api_do_actions():
    content = MCP_SERVER.read_text()
    for action in ['action: "status"', 'action: "record"', 'action: "checkpoint"', 'action: "verify"', 'action: "batch"', 'action: "automate"']:
        assert action in content
    assert 'action: "replay"' not in content

