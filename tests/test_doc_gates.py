"""The committed docs stay consistent: relative links resolve and the conformance table matches the fixtures."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"


@pytest.mark.parametrize(
    "args",
    [
        ("check_doc_links.py",),
        ("gen_conformance_table.py", "--check"),
    ],
    ids=["doc-links", "conformance-table"],
)
def test_doc_gate_is_green(args):
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / args[0]), *args[1:]],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
