from fastapi.testclient import TestClient
from server import app


def test_agent_execution_nav_is_before_system_health():
    client = TestClient(app)
    html = client.get("/").text
    nav_start = html.index('<nav class="cb-nav"')
    nav_end = html.index("</nav>", nav_start)
    nav = html[nav_start:nav_end]
    assert nav.find("Agent Execution") < nav.find("System Health")
    assert nav.find("Terminal Console") < nav.find("Mac Cleanup")


def test_agent_execution_has_state_cards_and_status_fetch():
    client = TestClient(app)
    html = client.get("/").text
    assert "aterm-session-cards" in html
    assert "/api/aterm/status" in html

