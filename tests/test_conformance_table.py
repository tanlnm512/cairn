"""The committed parser conformance table matches the golden fixtures."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_conformance_table_is_fresh():
    """A stale docs/indexing.md table fails --check until regenerated."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "gen_conformance_table.py"),
         "--check"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, (
        f"conformance table stale:\n{result.stdout}\n{result.stderr}\n"
        "regenerate: .venv/bin/python scripts/gen_conformance_table.py"
    )
