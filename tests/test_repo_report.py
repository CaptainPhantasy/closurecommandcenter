"""Tests for scripts/repo_report.py — the deterministic repository report populator.

Covers schema, critic rounds, gate detection, and fixture round-trip.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

# Make scripts/ importable
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import repo_report as rr  # noqa: E402

REQUIRED_TOP_LEVEL_FIELDS = {
    "project_name",
    "completion_percentage",
    "tech_stack",
    "complexity_score",
    "team_size_minimum",
    "go_to_market_timeline",
    "industry_vertical",
    "business_model",
    "technical_debt",
    "scalability_needs",
    "target_users",
    "key_features",
    "risks",
    "gate_statuses",
    "_evidence",
    "_critic_notes",
    "_verified",
    "_critic_rounds",
    "_last_verified",
    "_verified_by",
}


@pytest.fixture
def synthetic_python_project(tmp_path: Path) -> Path:
    """Create a minimal Python project for testing."""
    proj = tmp_path / "synth-project"
    proj.mkdir()
    (proj / "README.md").write_text(
        "# Synth Project\n\n## Overview\nA synthetic project for tests.\n\n## Quick Start\n- Run it.\n"
    )
    (proj / "FLOYD.md").write_text(
        "# synth-project — FLOYD.md\n**Version:** 1.0.0\n\n## Project-Specific Context\n\nSaaS for SMB customers.\nB2B model.\n"
    )
    (proj / "requirements.txt").write_text("fastapi==0.136.1\nuvicorn==0.46.0\npydantic==2.13.3\n")
    (proj / ".gitignore").write_text("# secrets\n.env\n.env.*\n")
    src = proj / "src"
    src.mkdir()
    (src / "main.py").write_text("def main():\n    # TODO: implement\n    pass\n")
    tests = proj / "tests"
    tests.mkdir()
    (tests / "test_main.py").write_text("def test_smoke():\n    assert 1 == 1\n")
    return proj


def test_schema_has_all_required_fields(synthetic_python_project: Path) -> None:
    report = rr.build_report(synthetic_python_project, critic_rounds=1)
    payload = asdict(report)
    missing = REQUIRED_TOP_LEVEL_FIELDS - set(payload.keys())
    assert not missing, f"missing required fields: {missing}"


def test_gate_statuses_canonical_keys(synthetic_python_project: Path) -> None:
    report = rr.build_report(synthetic_python_project, critic_rounds=1)
    assert set(report.gate_statuses.keys()) == set(rr.GATE_NAMES)
    for status in report.gate_statuses.values():
        assert status in rr.GATE_STATUSES


def test_completion_matches_passed_gates(synthetic_python_project: Path) -> None:
    report = rr.build_report(synthetic_python_project, critic_rounds=1)
    passed = sum(1 for s in report.gate_statuses.values() if s == "PASS")
    expected = round(passed / 7 * 100)
    assert report.completion_percentage == expected


def test_tech_stack_detects_python(synthetic_python_project: Path) -> None:
    report = rr.build_report(synthetic_python_project, critic_rounds=1)
    assert "Python" in report.tech_stack
    assert "FastAPI" in report.tech_stack


def test_business_model_from_floyd_md(synthetic_python_project: Path) -> None:
    report = rr.build_report(synthetic_python_project, critic_rounds=1)
    assert report.business_model == "B2B"


def test_security_gate_pass_when_env_in_gitignore(synthetic_python_project: Path) -> None:
    report = rr.build_report(synthetic_python_project, critic_rounds=1)
    assert report.gate_statuses["security"] == "PASS"


def test_automated_tests_gate_pass_when_tests_dir_populated(synthetic_python_project: Path) -> None:
    report = rr.build_report(synthetic_python_project, critic_rounds=1)
    assert report.gate_statuses["automated_tests"] == "PASS"


def test_critic_rounds_recorded(synthetic_python_project: Path) -> None:
    report = rr.build_report(synthetic_python_project, critic_rounds=3)
    assert report._critic_rounds == 3
    assert len(report._critic_notes) == 3


def test_team_size_rubric() -> None:
    # completion 14, complexity 3 → tier (0,30) base 4, complexity≤3 adjustment -1 → 3
    size, _ = rr.derive_team_size_minimum(14, 3)
    assert size == 3
    # completion 91, complexity 6 → tier (86,100) base 10, no adjustment → 10
    size, _ = rr.derive_team_size_minimum(91, 6)
    assert size == 10
    # completion 55, complexity 8 → tier (31,60) base 6, complexity≥8 +1 → 7
    size, _ = rr.derive_team_size_minimum(55, 8)
    assert size == 7


def test_completion_percentage_zero_when_no_gates_pass(tmp_path: Path) -> None:
    proj = tmp_path / "empty"
    proj.mkdir()
    (proj / "README.md").write_text("# Empty")
    report = rr.build_report(proj, critic_rounds=1)
    # No tests dir, no Makefile help, no gitignore → all UNKNOWN, 0% completion
    assert report.completion_percentage == 0


def test_json_roundtrip(synthetic_python_project: Path) -> None:
    report = rr.build_report(synthetic_python_project, critic_rounds=1)
    payload = report.to_json()
    parsed = json.loads(payload)
    assert parsed["project_name"] == "synth-project"
    assert parsed["_critic_rounds"] == 1


def test_external_fixture_floyd_harness_parses() -> None:
    """The captured floyd-harness fixture must be valid JSON with all fields."""
    fixture = ROOT / "scripts" / "fixtures" / "repo_report_floyd_harness.json"
    if not fixture.exists():
        pytest.skip(f"fixture not present at {fixture}")
    payload = json.loads(fixture.read_text())
    missing = REQUIRED_TOP_LEVEL_FIELDS - set(payload.keys())
    assert not missing, f"floyd-harness fixture missing: {missing}"
    assert payload["project_name"] == "floyd-harness"
    assert "Python" in payload["tech_stack"]


def test_external_fixture_floyd_docs_parses() -> None:
    """Floyd Docs has no manifest — populator must still produce valid JSON."""
    fixture = ROOT / "scripts" / "fixtures" / "repo_report_floyd_docs.json"
    if not fixture.exists():
        pytest.skip(f"fixture not present at {fixture}")
    payload = json.loads(fixture.read_text())
    missing = REQUIRED_TOP_LEVEL_FIELDS - set(payload.keys())
    assert not missing, f"Floyd Docs fixture missing: {missing}"
    # No-manifest projects should land at Unknown stack and not be _verified
    assert payload["tech_stack"] == ["Unknown"]

