"""Tests for capture-time session-bookkeeping triage.

`capture_memory` routes captures whose title/body matches session-state
patterns (`T\\d{3}` task IDs, branch refs, dated progress counts) to the
`raw` tier regardless of the score-derived placement, and tags them with a
`memory_triage` extension so forced placements stay auditable.

Fixtures are real titles from the live store: 4 session-bookkeeping titles
(must be force-routed) and 5 durable-knowledge titles (must not be).
"""
from __future__ import annotations

import sqlite3

import pytest

from cairn.graph.schema import _apply_schema
from cairn.memory.promotion import capture_memory
from cairn.memory.store import tier_for_score
from cairn.okf.bundle import OKFBundle


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def bundle(tmp_path):
    return OKFBundle(str(tmp_path / "knowledge"))


# Session-bookkeeping titles (task IDs, branch refs, progress counts,
# dated counts) that must land in the raw tier on capture.
BOOKKEEPING_TITLES = [
    "T007 pins get_repo_head display seam for T008",
    "agent_runtime arch-review improvements landed on feature/arch-review-improvements",
    "polaris compass campaign: 240 source-module compasses done, 157 test tasks left pending",
    "agent_runtime comment-trim house style extended repo-wide (2026-09-01, 4 commits)",
]

# Durable-knowledge titles that must keep their score-derived tier.
DURABLE_TITLES = [
    "Never evict numpy from sys.modules mid-process",
    "Kotlin grammar is the vendored fwcd tree-sitter build (cairn._tree_sitter_kotlin)",
    "Registry-bypass probe: test a parser port before the loader flips",
    "Test seams bind fakes at the consumer module's namespace",
    "pip --target dir shared across interpreter ABIs corrupts unrepairably",
]


@pytest.mark.parametrize("title", BOOKKEEPING_TITLES)
def test_capture_routes_session_bookkeeping_to_raw(db, bundle, title):
    """A bookkeeping-shaped capture lands in raw at capture quality 0.9.

    At that confidence the score alone would place the memory in a higher
    tier, so landing in raw proves the placement was forced, not scored.
    """
    result = capture_memory(
        db,
        bundle,
        type_="decision",
        title=title,
        body="Session progress note recorded during the working session.",
        confidence=0.9,
    )
    assert result["tier"] == "raw"
    assert result["path"].startswith("memory/raw/")
    assert result["concept"].extensions["memory_triage"] == "session-bookkeeping"


@pytest.mark.parametrize("title", DURABLE_TITLES)
def test_capture_keeps_durable_knowledge_scored_tier(db, bundle, title):
    """A durable capture keeps the score-derived tier and no triage tag.

    The body contains a progress-count sentence ("3 callers remaining"),
    which bodies legitimately do; the loose progress-count pattern applies
    to the title only, so this must not trip the triage.
    """
    result = capture_memory(
        db,
        bundle,
        type_="pattern",
        title=title,
        body="Durable practice that applies across sessions; 3 callers remaining after the refactor.",
        confidence=0.9,
    )
    assert result["tier"] == tier_for_score(result["signals"]["score"])
    assert result["concept"].extensions.get("memory_triage") is None
