"""The rust-migration golden harnesses must own disjoint fixture trees."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts" / "rust_migration"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import flow_golden  # noqa: E402
import parity  # noqa: E402


def test_flow_golden_writes_no_parity_fixture():
    """flow_golden fixture paths never live under parity's mcp tree.

    parity --record rewrites every json under its out-dir, so a flow fixture
    placed there drifts on the next parity record and --check can never pass.
    """
    for path in flow_golden._fixture_paths().values():
        assert parity.DEFAULT_OUT_DIR not in path.parents, str(path)
