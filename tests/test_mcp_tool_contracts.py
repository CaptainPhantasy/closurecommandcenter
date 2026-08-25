from pathlib import Path

MCP_SERVER = Path("modules/agent-execution/source/src/mcp/server.ts")


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

