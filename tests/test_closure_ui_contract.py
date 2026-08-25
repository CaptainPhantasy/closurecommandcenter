"""Static contract tests for the shipped zero-build Closure Board."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).parent.parent


class ClosureUiContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (ROOT / "index.html").read_text()
        cls.server = (ROOT / "server.py").read_text()

    def test_shipped_navigation_contains_closure_board(self):
        self.assertIn('data-cb-page="closure"', self.html)
        self.assertIn('id="cb-page-closure"', self.html)
        self.assertIn("Closure Board", self.html)

    def test_board_renders_contract_lanes_and_blocker_truth(self):
        for label in ("Closed when", "Now", "Next", "Final", "TRUE BLOCKER", "APPARENT"):
            self.assertIn(label, self.html)

    def test_board_uses_namespaced_closure_api(self):
        for route in (
            "/api/closure/board",
            "/api/closure/projects",
            "/api/closure/self-test",
        ):
            self.assertIn(route, self.html)
        self.assertIn("projects closed", self.html)
        self.assertIn("from closure_control import closure_router", self.server)
        self.assertIn("app.include_router(closure_router)", self.server)

    def test_external_effect_controls_are_not_exposed_on_closure_board(self):
        page = self.html.split('id="cb-page-closure"', 1)[1].split("<!-- Page 2", 1)[0]
        self.assertNotIn("effects/reserve", page)
        self.assertNotIn("Dispatch Finisher", page)
        self.assertNotIn("Dispatch Bootstrap", page)


if __name__ == "__main__":
    unittest.main()
