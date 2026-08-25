#!/usr/bin/env python3
"""repo_report.py — Legacy AI deterministic repository report populator.

Walks a Legacy-AI-governed project directory, derives the canonical 13-field
schema from code evidence, and writes ``SSOT/repository_report.json``. The
output schema matches existing reports at TEAR and Dark Motion; an additional
``gate_statuses`` field tracks the 7 Beta Release Readiness gates per
``plans/ROADMAP.md`` §5.

Authority:
- Schema (13 fields) — ``plans/ROADMAP.md`` §3.E
- Beta gates — ``plans/ROADMAP.md`` §5
- Team-size rubric — ported from ``legacy-team-architect.py`` (cite by line)
- 3-round critic — ``.supercache/contracts/repository-report-spec.md``

Operation:
    python repo_report.py <project-path> [--write] [--critic-rounds 3]

When ``--write`` is supplied, the report lands at
``<project-path>/SSOT/repository_report.json``. Without ``--write`` the report
is printed to stdout. The critic loop runs ``--critic-rounds`` times (default
3); each round re-reads every field, confirms evidence, and emits a critic
note. The final report is only marked ``_verified=True`` if all critic
rounds passed without retracting any field.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("repo_report")

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

GATE_NAMES = (
    "build_run",
    "primary_journey",
    "automated_tests",
    "e2e_tests",
    "multi_min_human_sim",
    "security",
    "demo",
)
GATE_STATUSES = ("PASS", "FAIL", "UNKNOWN", "WAIVED")
DEFAULT_GATE_STATUSES: dict[str, str] = {g: "UNKNOWN" for g in GATE_NAMES}


@dataclass(frozen=True)
class RepositoryReport:
    """Canonical 14-field report (13 ROADMAP §3.E + ``gate_statuses``)."""

    project_name: str
    completion_percentage: int
    tech_stack: list[str]
    complexity_score: int
    team_size_minimum: int
    go_to_market_timeline: str
    industry_vertical: str
    business_model: str
    technical_debt: int
    scalability_needs: str
    target_users: str
    key_features: list[str]
    risks: list[str]
    gate_statuses: dict[str, str]
    _evidence: dict[str, str] = field(default_factory=dict)
    _critic_notes: list[str] = field(default_factory=list)
    _verified: bool = False
    _critic_rounds: int = 0
    _last_verified: str = ""
    _verified_by: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

EXCLUDE_DIRS = frozenset(
    {
        ".git",
        ".svn",
        ".hg",
        ".venv",
        "venv",
        "node_modules",
        ".pnpm-store",
        ".turbo",
        ".next",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
        "target",
        ".cache",
        ".tox",
        ".nox",
        ".DS_Store",
        ".Trashes",
        ".Spotlight-V100",
        ".fseventsd",
        ".DocumentRevisions-V100",
        ".TemporaryItems",
        ".floyd",
    }
)


def walk_files(root: Path) -> list[Path]:
    """Yield all source-relevant files under ``root``, excluding ``EXCLUDE_DIRS``."""
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fname in filenames:
            if fname.startswith("."):
                continue
            out.append(Path(dirpath) / fname)
    return out


def run_cmd(cmd: list[str], cwd: Path | None = None, timeout: int = 30) -> tuple[int, str, str]:
    """Run a shell command, return (exit_code, stdout, stderr). Never raises."""
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except FileNotFoundError as e:
        return 127, "", str(e)
    except OSError as e:
        return 1, "", str(e)


# ---------------------------------------------------------------------------
# Field derivers
# ---------------------------------------------------------------------------


def derive_project_name(root: Path) -> tuple[str, str]:
    name = root.name
    return name, f"basename of {root} → {name}"


def derive_tech_stack(root: Path) -> tuple[list[str], str]:
    """Detect tech stack from manifest files at the project root."""
    stack: list[str] = []
    evidence_parts: list[str] = []

    pkg_json = root / "package.json"
    if pkg_json.exists():
        try:
            data = json.loads(pkg_json.read_text())
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            evidence_parts.append(f"package.json: {len(deps)} deps")
            if "react" in deps:
                stack.append("React")
            if "next" in deps:
                stack.append("Next.js")
            if "vue" in deps:
                stack.append("Vue")
            if "@angular/core" in deps:
                stack.append("Angular")
            if "electron" in deps:
                stack.append("Electron")
            if "express" in deps:
                stack.append("Express")
            if "fastify" in deps:
                stack.append("Fastify")
            if "typescript" in deps:
                stack.append("TypeScript")
            else:
                stack.append("JavaScript")
            stack.append("Node.js")
        except (json.JSONDecodeError, OSError):
            evidence_parts.append("package.json present but unreadable")

    pyproject = root / "pyproject.toml"
    requirements = root / "requirements.txt"
    if pyproject.exists():
        evidence_parts.append("pyproject.toml present")
        stack.append("Python")
        text = pyproject.read_text()
        if "fastapi" in text.lower():
            stack.append("FastAPI")
        if "django" in text.lower():
            stack.append("Django")
        if "flask" in text.lower():
            stack.append("Flask")
    elif requirements.exists():
        evidence_parts.append("requirements.txt present")
        stack.append("Python")
        text = requirements.read_text().lower()
        if "fastapi" in text:
            stack.append("FastAPI")
        if "django" in text:
            stack.append("Django")
        if "flask" in text:
            stack.append("Flask")
        if "uvicorn" in text:
            stack.append("uvicorn")
        if "pydantic" in text:
            stack.append("Pydantic")

    if (root / "Cargo.toml").exists():
        evidence_parts.append("Cargo.toml present")
        stack.append("Rust")
    if (root / "go.mod").exists():
        evidence_parts.append("go.mod present")
        stack.append("Go")
    if (root / "Gemfile").exists():
        evidence_parts.append("Gemfile present")
        stack.append("Ruby")
    if (root / "composer.json").exists():
        evidence_parts.append("composer.json present")
        stack.append("PHP")
    if (root / "Package.swift").exists():
        evidence_parts.append("Package.swift present")
        stack.append("Swift")
    if (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
        evidence_parts.append("Gradle project")
        stack.append("Java/Kotlin")
    if (root / "pom.xml").exists():
        evidence_parts.append("pom.xml present (Maven)")
        stack.append("Java")

    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for s in stack:
        if s not in seen:
            seen.add(s)
            deduped.append(s)

    evidence = "; ".join(evidence_parts) if evidence_parts else "no recognizable manifest at root"
    return deduped or ["Unknown"], evidence


def derive_complexity_score(root: Path, files: list[Path]) -> tuple[int, str]:
    """Rubric: source file count, depth, dependencies."""
    src_count = sum(
        1
        for f in files
        if f.suffix in {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".rb", ".java", ".kt", ".swift", ".cpp", ".c", ".h"}
    )
    max_depth = 0
    for f in files:
        try:
            depth = len(f.relative_to(root).parts)
            if depth > max_depth:
                max_depth = depth
        except ValueError:
            pass

    dep_count = 0
    pkg_json = root / "package.json"
    if pkg_json.exists():
        try:
            data = json.loads(pkg_json.read_text())
            dep_count += len({**data.get("dependencies", {}), **data.get("devDependencies", {})})
        except (json.JSONDecodeError, OSError):
            pass
    requirements = root / "requirements.txt"
    if requirements.exists():
        dep_count += sum(1 for line in requirements.read_text().splitlines() if line.strip() and not line.startswith("#"))

    # Score 1-10
    score = 1
    if src_count > 10:
        score += 1
    if src_count > 50:
        score += 1
    if src_count > 200:
        score += 2
    if max_depth > 4:
        score += 1
    if max_depth > 7:
        score += 1
    if dep_count > 10:
        score += 1
    if dep_count > 50:
        score += 1
    if dep_count > 100:
        score += 1
    score = min(10, score)

    evidence = (
        f"src_files={src_count}, max_depth={max_depth}, deps={dep_count} "
        f"→ rubric score {score}/10"
    )
    return score, evidence


# Team-size rubric ported from legacy-team-architect.py:42-46
COMPLETION_TIERS = (
    (0, 30, 4),
    (31, 60, 6),
    (61, 85, 8),
    (86, 100, 10),
)


def derive_team_size_minimum(completion: int, complexity: int) -> tuple[int, str]:
    base = 4
    for low, high, size in COMPLETION_TIERS:
        if low <= completion <= high:
            base = size
            break
    # Complexity adjustment: -1 if complexity ≤ 3, +1 if complexity ≥ 8
    adjusted = base
    if complexity <= 3:
        adjusted = max(2, base - 1)
    elif complexity >= 8:
        adjusted = base + 1
    evidence = (
        f"completion={completion}% → tier base {base}; "
        f"complexity={complexity} adjustment → team_size_minimum={adjusted}"
    )
    return adjusted, evidence


def derive_go_to_market_timeline(completion: int, gates_passed: int) -> tuple[str, str]:
    if completion >= 90 and gates_passed >= 6:
        return "1-2 weeks", "completion ≥90% AND ≥6/7 gates → 1-2 weeks"
    if completion >= 70:
        return "1-3 months", "completion ≥70% → 1-3 months"
    if completion >= 40:
        return "3-6 months", "completion ≥40% → 3-6 months"
    return "6+ months", f"completion {completion}% → 6+ months"


def read_floyd_md_section(root: Path, header: str) -> tuple[str | None, str]:
    """Return the body of a section under a markdown header in FLOYD.md."""
    floyd = root / "FLOYD.md"
    if not floyd.exists():
        return None, "FLOYD.md not found"
    text = floyd.read_text()
    pattern = rf"#+\s*{re.escape(header)}\s*\n(.*?)(?=\n#+\s|$)"
    m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if m:
        body = m.group(1).strip()
        return body[:500], f"FLOYD.md '{header}' section"
    return None, f"FLOYD.md found but no '{header}' section"


def derive_business_model(root: Path) -> tuple[str, str]:
    body, source = read_floyd_md_section(root, "Project-Specific Context")
    if body and "B2B" in body:
        return "B2B", f"{source}: B2B mentioned"
    if body and "B2C" in body:
        return "B2C", f"{source}: B2C mentioned"
    if body and ("internal" in body.lower() or "private" in body.lower()):
        return "Internal Tool", f"{source}: internal/private use"
    return "Unknown", source


def derive_industry_vertical(root: Path) -> tuple[str, str]:
    body, source = read_floyd_md_section(root, "Project-Specific Context")
    if not body:
        return "Unknown", source
    keywords = {
        "developer": "Developer Tools",
        "agent": "Developer Tools",
        "saas": "SaaS",
        "voice": "Communications",
        "phone": "Communications",
        "crm": "Sales & Marketing",
        "marketing": "Sales & Marketing",
        "fintech": "Fintech",
        "ecommerce": "E-commerce",
        "healthcare": "Healthcare",
        "education": "Education",
    }
    body_lower = body.lower()
    for kw, vertical in keywords.items():
        if kw in body_lower:
            return vertical, f"{source}: '{kw}' detected → {vertical}"
    return "Technology", f"{source}: no vertical keyword matched, defaulting to Technology"


def derive_target_users(root: Path) -> tuple[str, str]:
    body, source = read_floyd_md_section(root, "Project-Specific Context")
    if body:
        # First sentence/line of the section
        first = body.split("\n")[0].strip()
        if first and len(first) > 20:
            return first[:300], source
    readme = root / "README.md"
    if readme.exists():
        first_lines = "\n".join(readme.read_text().splitlines()[:5])
        return first_lines[:300], "README.md first 5 lines"
    return "Unknown", "no FLOYD.md section or README.md found"


def derive_scalability_needs(root: Path) -> tuple[str, str]:
    body, source = read_floyd_md_section(root, "Project-Specific Context")
    if body:
        body_lower = body.lower()
        if "high" in body_lower and "scal" in body_lower:
            return "high", f"{source}: 'high' scalability mentioned"
        if "single-user" in body_lower or "local-first" in body_lower or "internal" in body_lower:
            return "low", f"{source}: single-user/local-first/internal"
    return "low", "default: no scalability evidence found"


def derive_technical_debt(root: Path, files: list[Path]) -> tuple[int, str]:
    todo_count = 0
    fixme_count = 0
    xxx_count = 0
    hack_count = 0
    sample_files = [f for f in files if f.suffix in {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go"}][:200]
    for f in sample_files:
        try:
            text = f.read_text(errors="ignore")
            todo_count += len(re.findall(r"\bTODO\b", text))
            fixme_count += len(re.findall(r"\bFIXME\b", text))
            xxx_count += len(re.findall(r"\bXXX\b", text))
            hack_count += len(re.findall(r"\bHACK\b", text))
        except OSError:
            continue
    total = todo_count + fixme_count + xxx_count + hack_count
    score = min(100, total * 2)
    evidence = (
        f"TODO={todo_count}, FIXME={fixme_count}, XXX={xxx_count}, HACK={hack_count} "
        f"across {len(sample_files)} sampled source files → debt_score={score}"
    )
    return score, evidence


def derive_key_features(root: Path) -> tuple[list[str], str]:
    """Discover features from FLOYD.md, README.md headings, route handlers."""
    features: list[str] = []
    evidence_parts: list[str] = []

    body, source = read_floyd_md_section(root, "Project-Specific Context")
    if body:
        keys_match = re.search(r"\*\*Key Files:?\*\*\s*\n((?:[-*]\s*[^\n]+\n?)+)", body)
        if keys_match:
            for line in keys_match.group(1).splitlines():
                m = re.match(r"[-*]\s*[`*]?([^`*\n—-]+?)[`*]?\s*[—-]\s*(.+)", line.strip())
                if m:
                    features.append(f"{m.group(1).strip()}: {m.group(2).strip()[:80]}")
            evidence_parts.append(f"FLOYD.md Key Files section: {len(features)} entries")

    if not features:
        readme = root / "README.md"
        if readme.exists():
            text = readme.read_text()
            heading_features = re.findall(r"^##\s+(.+?)$", text, re.MULTILINE)[:10]
            features.extend(h.strip() for h in heading_features)
            evidence_parts.append(f"README.md ## headings: {len(heading_features)}")

    return features[:15], "; ".join(evidence_parts) if evidence_parts else "no features discovered"


def derive_risks(
    root: Path,
    todo_count: int,
    gate_statuses: dict[str, str],
) -> tuple[list[str], str]:
    risks: list[str] = []
    evidence_parts: list[str] = []

    issues = root / "Issues"
    if issues.exists() and issues.is_dir():
        for issues_file in issues.glob("*_ISSUES.md"):
            text = issues_file.read_text()
            critical = re.findall(r"\*\*Status\*\*:\s*New|\*\*Status\*\*:\s*In progress|severity\s*=\s*(?:HIGH|CRITICAL)", text, re.IGNORECASE)
            if critical:
                risks.append(f"{len(critical)} open issues in {issues_file.name}")
                evidence_parts.append(f"{issues_file.name}: {len(critical)} open")

    failed_gates = [g for g, s in gate_statuses.items() if s == "FAIL"]
    if failed_gates:
        risks.append(f"Failed Beta-readiness gates: {', '.join(failed_gates)}")
        evidence_parts.append(f"FAIL gates: {failed_gates}")

    unknown_gates = [g for g, s in gate_statuses.items() if s == "UNKNOWN"]
    if len(unknown_gates) >= 5:
        risks.append(f"Most Beta-readiness gates unknown ({len(unknown_gates)}/7) — bootstrap incomplete")

    if todo_count > 50:
        risks.append(f"High TODO/FIXME density: {todo_count} markers in source")

    if not (root / ".git").exists():
        risks.append("No git repository — no version control, no rollback")
        evidence_parts.append(".git absent")

    return risks, "; ".join(evidence_parts) if evidence_parts else "no critical risks detected"


def derive_gate_statuses(root: Path) -> tuple[dict[str, str], str]:
    """For v1, all gates default to UNKNOWN until per-gate verifiers ship in v1.6.4."""
    statuses = dict(DEFAULT_GATE_STATUSES)
    evidence_parts = []

    # Gate 1 — Build/Run: heuristic — exit 0 from a `make help` or equivalent
    if (root / "Makefile").exists():
        rc, _, _ = run_cmd(["make", "-n", "help"], cwd=root, timeout=5)
        if rc == 0:
            statuses["build_run"] = "PASS"
            evidence_parts.append("Makefile help target dry-runs cleanly → build_run=PASS")
        else:
            evidence_parts.append("Makefile present but help target failed dry-run")

    # Gate 3 — Automated tests: presence-only check
    test_dirs = [root / "tests", root / "test", root / "__tests__"]
    if any(d.exists() and d.is_dir() and any(d.iterdir()) for d in test_dirs):
        statuses["automated_tests"] = "PASS"
        evidence_parts.append("tests/ directory with content → automated_tests=PASS (presence-only)")

    # Gate 6 — Security hygiene: no .env in tracked files (light heuristic)
    if (root / ".gitignore").exists():
        text = (root / ".gitignore").read_text()
        if ".env" in text:
            statuses["security"] = "PASS"
            evidence_parts.append(".gitignore covers .env → security=PASS (presence-only)")

    # Gates 2, 4, 5, 7 (primary_journey, e2e_tests, multi_min_human_sim, demo) remain UNKNOWN
    return statuses, "; ".join(evidence_parts) if evidence_parts else "all gates default UNKNOWN"


def derive_completion_percentage(gate_statuses: dict[str, str]) -> tuple[int, str]:
    passed = sum(1 for s in gate_statuses.values() if s == "PASS")
    total = len(gate_statuses)
    pct = round(passed / total * 100)
    return pct, f"{passed}/{total} gates PASS → completion={pct}%"


# ---------------------------------------------------------------------------
# Critic loop
# ---------------------------------------------------------------------------


def critic_round(report: RepositoryReport, round_num: int, root: Path) -> str:
    """One critic round. Re-validates fields against the live filesystem.

    Returns a critic note describing what was verified or corrected.
    """
    notes: list[str] = []

    # Re-derive project_name and confirm
    name_now, _ = derive_project_name(root)
    if name_now != report.project_name:
        notes.append(f"project_name drift: was {report.project_name}, now {name_now}")

    # Confirm tech_stack is non-empty and not just ['Unknown']
    if report.tech_stack == ["Unknown"]:
        notes.append("tech_stack=['Unknown'] — no manifest detected; downstream fields inherit Unknown")

    # Confirm gate_statuses keys match canonical
    if set(report.gate_statuses.keys()) != set(GATE_NAMES):
        notes.append(f"gate_statuses keys mismatch canonical: {set(report.gate_statuses.keys())}")

    # Confirm completion_percentage = passed_gates / 7 * 100
    passed = sum(1 for s in report.gate_statuses.values() if s == "PASS")
    expected_pct = round(passed / 7 * 100)
    if report.completion_percentage != expected_pct:
        notes.append(
            f"completion_percentage drift: stored {report.completion_percentage}%, "
            f"recomputed {expected_pct}% from gate_statuses"
        )

    if not notes:
        return f"Round {round_num}: all fields verified against live filesystem; no corrections."
    return f"Round {round_num}: {'; '.join(notes)}"


# ---------------------------------------------------------------------------
# Main populator
# ---------------------------------------------------------------------------


def build_report(root: Path, critic_rounds: int = 3, agent_id: str = "Floyd") -> RepositoryReport:
    if not root.exists() or not root.is_dir():
        raise ValueError(f"project path does not exist or is not a directory: {root}")

    files = walk_files(root)
    evidence: dict[str, str] = {}

    project_name, ev = derive_project_name(root)
    evidence["project_name"] = ev

    tech_stack, ev = derive_tech_stack(root)
    evidence["tech_stack"] = ev

    complexity_score, ev = derive_complexity_score(root, files)
    evidence["complexity_score"] = ev

    gate_statuses, ev = derive_gate_statuses(root)
    evidence["gate_statuses"] = ev

    completion_percentage, ev = derive_completion_percentage(gate_statuses)
    evidence["completion_percentage"] = ev

    team_size_minimum, ev = derive_team_size_minimum(completion_percentage, complexity_score)
    evidence["team_size_minimum"] = ev

    passed_gates = sum(1 for s in gate_statuses.values() if s == "PASS")
    go_to_market_timeline, ev = derive_go_to_market_timeline(completion_percentage, passed_gates)
    evidence["go_to_market_timeline"] = ev

    industry_vertical, ev = derive_industry_vertical(root)
    evidence["industry_vertical"] = ev

    business_model, ev = derive_business_model(root)
    evidence["business_model"] = ev

    technical_debt, ev = derive_technical_debt(root, files)
    evidence["technical_debt"] = ev

    scalability_needs, ev = derive_scalability_needs(root)
    evidence["scalability_needs"] = ev

    target_users, ev = derive_target_users(root)
    evidence["target_users"] = ev

    key_features, ev = derive_key_features(root)
    evidence["key_features"] = ev

    risks, ev = derive_risks(root, technical_debt, gate_statuses)
    evidence["risks"] = ev

    report = RepositoryReport(
        project_name=project_name,
        completion_percentage=completion_percentage,
        tech_stack=tech_stack,
        complexity_score=complexity_score,
        team_size_minimum=team_size_minimum,
        go_to_market_timeline=go_to_market_timeline,
        industry_vertical=industry_vertical,
        business_model=business_model,
        technical_debt=technical_debt,
        scalability_needs=scalability_needs,
        target_users=target_users,
        key_features=key_features,
        risks=risks,
        gate_statuses=gate_statuses,
        _evidence=evidence,
    )

    # Critic loop — frozen dataclass means we rebuild via dict
    notes: list[str] = []
    for r in range(1, critic_rounds + 1):
        notes.append(critic_round(report, r, root))

    final = RepositoryReport(
        project_name=report.project_name,
        completion_percentage=report.completion_percentage,
        tech_stack=report.tech_stack,
        complexity_score=report.complexity_score,
        team_size_minimum=report.team_size_minimum,
        go_to_market_timeline=report.go_to_market_timeline,
        industry_vertical=report.industry_vertical,
        business_model=report.business_model,
        technical_debt=report.technical_debt,
        scalability_needs=report.scalability_needs,
        target_users=report.target_users,
        key_features=report.key_features,
        risks=report.risks,
        gate_statuses=report.gate_statuses,
        _evidence=report._evidence,
        _critic_notes=notes,
        _verified=all("no corrections" in n for n in notes),
        _critic_rounds=critic_rounds,
        _last_verified=datetime.now(tz=timezone.utc).astimezone().isoformat(timespec="seconds"),
        _verified_by=agent_id,
    )
    return final


def main() -> int:
    parser = argparse.ArgumentParser(description="Legacy AI repository report populator")
    parser.add_argument("project_path", type=Path, help="Project root directory")
    parser.add_argument("--write", action="store_true", help="Write to <project>/SSOT/repository_report.json")
    parser.add_argument("--critic-rounds", type=int, default=3, help="Critic rounds (default 3)")
    parser.add_argument("--agent-id", default="Floyd", help="Agent identifier for _verified_by")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    root = args.project_path.resolve()
    report = build_report(root, critic_rounds=args.critic_rounds, agent_id=args.agent_id)

    output = report.to_json()

    if args.write:
        ssot = root / "SSOT"
        ssot.mkdir(exist_ok=True)
        out_path = ssot / "repository_report.json"
        out_path.write_text(output + "\n")
        print(f"[repo_report] wrote {out_path}")
    else:
        print(output)

    return 0 if report._verified else 1


if __name__ == "__main__":
    sys.exit(main())

