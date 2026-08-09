"""Tests for the memory-recall STALE flag (Phase 3.1).

`recall_memory` already surfaces a `refs-verified=<fraction>` per result; Phase
3.1 adds a discrete `STALE` flag derived from that fraction -- firing when any
cited backtick ref no longer exists in the graph (fraction < 1.0). This is the
recall-side analog of the critic gate: silent drift surfaced loudly.

These tests exercise the flag end-to-end through the `recall_memory` MCP tool
(with its module-level connection helpers monkeypatched to a test DB/bundle).
"""
from __future__ import annotations

import sqlite3

import pytest

from cairn.graph.schema import _apply_schema
from cairn.memory.promotion import capture_memory
from cairn.mcp_server import tools_memory
from cairn.okf.bundle import OKFBundle


def _seed_symbol(conn: sqlite3.Connection) -> None:
    """One repo/file/symbol so a memory's backtick ref verifies initially."""
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r1', 'r1', '.')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (1, 'r1', 'src/auth.py', 'python')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES (1, 1, 'login', 'function', 'auth.login', 1, 10)"
    )
    conn.commit()


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    _seed_symbol(conn)
    yield conn
    conn.close()


@pytest.fixture
def bundle(tmp_path):
    return OKFBundle(str(tmp_path / "knowledge"))


def _recall(query: str, db, bundle) -> str:
    """Invoke recall_memory with monkeypatched module-level connection helpers.

    recall_memory calls `_conn()` directly (not as a context manager) and
    closes it with .close(), so the patch returns a fresh wrapper each call.
    To keep the test DB alive across the close, we hand out the same connection
    and make .close() a no-op.
    """
    class _TestConn:
        """Wraps the real conn so recall_memory's .close() doesn't kill the fixture."""
        def __getattr__(self, name):
            return getattr(db, name)
        def close(self):
            pass  # keep the fixture connection alive for the next recall

    # Patch the module-level helpers used by recall_memory.
    tools_memory._conn = lambda: _TestConn()
    tools_memory._bundle = lambda: bundle
    return tools_memory.recall_memory(query)


def test_recall_flags_stale_when_cited_symbol_deleted(db, bundle):
    """A memory citing a symbol that is later removed → STALE flag on recall."""
    # Record a memory backtick-citing `login` (which exists in the graph).
    capture_memory(
        db, bundle, type_="decision", title="auth login backoff",
        body="`login()` retries with exponential backoff. Why: flaky upstream.",
        confidence=0.8,
    )
    # Recall while the symbol still exists → no STALE.
    out = _recall("login", db, bundle)
    assert "[STALE]" not in out, out
    assert "refs-verified=1.0" in out, out

    # Delete the cited symbol (simulating a rename/removal + rebuild).
    db.execute("DELETE FROM symbols WHERE name = 'login'")
    db.commit()

    # Recall again → now STALE, fraction < 1.0.
    out = _recall("login", db, bundle)
    assert "[STALE]" in out, out
    assert "verify before relying" in out, out
    # The fraction dropped below 1.0.
    assert "refs-verified=0.0" in out, out


def test_recall_no_stale_flag_for_memory_without_refs(db, bundle):
    """A memory with no backtick refs scores 1.0 (neutral) → never STALE.

    Guards against false positives: prose-only memories must not be flagged.
    """
    capture_memory(
        db, bundle, type_="pattern", title="deploy on tuesdays",
        body="We deploy on Tuesdays. Why: low-traffic window.",
        confidence=0.7,
    )
    out = _recall("deploy", db, bundle)
    assert "[STALE]" not in out, out
    # Zero refs → surfaced as "n/a (0 refs)", NOT a misleading 1.0.
    assert "refs-verified=n/a (0 refs)" in out, out


def test_recall_no_stale_flag_for_real_refs(db, bundle):
    """A memory citing a symbol that still exists → no STALE."""
    capture_memory(
        db, bundle, type_="workaround", title="auth workaround",
        body="Call `login()` twice on 401. Why: token race.",
        confidence=0.7,
    )
    out = _recall("auth", db, bundle)
    assert "[STALE]" not in out, out


def test_recall_partial_stale_when_one_of_two_refs_gone(db, bundle):
    """Partial stale: 2 backtick refs, delete 1 → fraction 0.5 → STALE.

    Covers the 0 < fraction < 1 middle case (only 0.0 and 1.0 were tested
    before). The flag should fire on ANY stale ref, not just all-stale.
    """
    # Seed a second symbol so the memory can cite two.
    db.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES (2, 1, 'logout', 'function', 'auth.logout', 12, 20)"
    )
    db.commit()
    capture_memory(
        db, bundle, type_="pattern", title="auth pair",
        body="Use `login()` and `logout()` together. Why: session hygiene.",
        confidence=0.7,
    )
    # Both exist → no stale, fraction 1.0.
    out = _recall("auth", db, bundle)
    assert "[STALE]" not in out, out
    assert "refs-verified=1.0" in out, out
    # Delete one of the two → fraction 0.5 → stale.
    db.execute("DELETE FROM symbols WHERE name = 'logout'")
    db.commit()
    out = _recall("auth", db, bundle)
    assert "[STALE]" in out, out
    assert "refs-verified=0.5" in out, out


def test_recall_does_not_crash_when_verification_raises(db, bundle, monkeypatch):
    """If _graph_verification raises, recall must not crash and must not flag STALE.

    Validates the isinstance guard on the refs_verified='?' exception path.
    """
    capture_memory(
        db, bundle, type_="decision", title="auth login backoff",
        body="`login()` retries with backoff. Why: flaky upstream.",
        confidence=0.8,
    )

    def _boom(*a, **k):
        raise RuntimeError("simulated DB error")

    # Patch where recall_memory imports it from.
    import cairn.memory.scoring as scoring
    monkeypatch.setattr(scoring, "_graph_verification", _boom)
    out = _recall("login", db, bundle)
    # Did not crash; STALE not flagged (can't compute); '?' surfaced.
    assert "[STALE]" not in out, out
    assert "refs-verified=?" in out, out
