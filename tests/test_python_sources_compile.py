"""Every tracked Python source must byte-compile.

Guards the class of defect reported in issue #10 / PR #22 (a mis-indented
block that would make a module unimportable). Compilation needs no
third-party dependencies, so this runs without a server or a venv.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _tracked_python_files() -> list[Path]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "*.py"],
        check=True, capture_output=True, text=True,
    ).stdout
    return [ROOT / p for p in out.splitlines() if p]


class PythonSourcesCompile(unittest.TestCase):
    def test_all_tracked_python_files_compile(self) -> None:
        files = _tracked_python_files()
        self.assertTrue(files, "no tracked .py files found")
        failures = []
        for path in files:
            try:
                compile(path.read_text(encoding="utf-8"), str(path), "exec")
            except SyntaxError as exc:  # includes IndentationError / TabError
                failures.append(f"{path.relative_to(ROOT)}:{exc.lineno}: {exc.msg}")
        self.assertEqual(failures, [], "\n".join(failures))

    def test_log_activity_body_is_inside_if_task(self) -> None:
        import ast

        src = (ROOT / "modules/intelligence/workflow.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "log_activity"
        )
        guard = next(n for n in fn.body if isinstance(n, ast.If))
        # ActivityEntry construction, append, and persistence all sit under `if task:`
        self.assertGreaterEqual(len(guard.body), 3)


if __name__ == "__main__":
    unittest.main()
