from pathlib import Path

MCP_SERVER = Path("modules/agent-execution/source/src/mcp/server.ts")


def test_mcp_defaults_to_kernel_port_11527():
    content = MCP_SERVER.read_text()
    assert "http://localhost:11527" in content
    assert "http://localhost:9600" not in content


def test_mcp_does_not_hardcode_local_dev_token():
    content = MCP_SERVER.read_text()
    assert "local-dev" not in content
    assert "ATERM_TOKEN" in content

