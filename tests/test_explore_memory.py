"""Tests for explore's tribal-memory section and reference recording.

Covers: the section header (populated and ``(none)``), the 3-entry cap
rendering title + "How to apply" line only, and ``memory_refs`` rows recorded
for exactly the rendered memories, including under concurrent explore calls.
"""
from __future__ import annotations

import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

from cairn.graph.schema import _apply_schema
from cairn.okf.bundle import OKFBundle
from cairn.okf.concept import OKFConcept


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Schema'd file DB with one indexed symbol + knowledge dir, wired
    through CAIRN_DB/CAIRN_KNOWLEDGE so no test touches the real ~/.cairn."""
    monkeypatch.delenv("CAIRN_READ_ONLY", raising=False)
    db_path = tmp_path / "graph.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r1', 'r1', '.')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (1, 'r1', 'src/loader.py', 'python')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES (1, 1, 'numpy_loader', 'function', 'loader.numpy_loader', 1, 10)"
    )
    conn.commit()
    conn.close()
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    monkeypatch.setenv("CAIRN_DB", str(db_path))
    monkeypatch.setenv("CAIRN_KNOWLEDGE", str(knowledge))
    return OKFBundle(str(knowledge)), db_path


def _write_tribal(bundle: OKFBundle, title: str, body: str, slug: str) -> str:
    """Write one tribal memory concept; returns its concept_id."""
    concept_id = f"memory/tribal/{slug}"
    bundle.write_concept(
        OKFConcept(
            type="Tribal-mistake",
            title=title,
            description=title,
            body=body,
            concept_id=concept_id,
            extensions={"memory_tier": "tribal", "memory_type": "mistake"},
        )
    )
    return concept_id


def _tribal_section_lines(out: str) -> list[str]:
    """Return the body lines of the Tribal memory section (header excluded)."""
    lines = out.splitlines()
    start = next(
        i for i, ln in enumerate(lines) if ln.startswith("=== Tribal memory")
    )
    end = start + 1
    while end < len(lines) and not lines[end].startswith("=== "):
        end += 1
    return lines[start + 1 : end]


def _ref_rows(db_path) -> list[sqlite3.Row]:
    """Read memory_refs through a fresh connection (proves persistence)."""
    fresh = sqlite3.connect(db_path)
    fresh.row_factory = sqlite3.Row
    try:
        return fresh.execute(
            "SELECT memory_path, session_id, context FROM memory_refs"
        ).fetchall()
    finally:
        fresh.close()


# ---------------------------------------------------------------------------
# TC-001 — matching tribal memory is surfaced
# ---------------------------------------------------------------------------


def test_explore_surfaces_matching_tribal_memory(env):
    from cairn.mcp_server import tools_graph

    bundle, _ = env
    _write_tribal(
        bundle,
        "Never evict numpy from sys.modules mid-process",
        "Why: C extensions break if unloaded mid-run.\n"
        "How to apply: keep numpy imported until the interpreter exits.",
        "never-evict-numpy",
    )
    out = tools_graph.explore("numpy_loader")
    assert "=== Tribal memory (1) ===" in out
    assert "Never evict numpy from sys.modules mid-process" in out
    assert (
        "How to apply: keep numpy imported until the interpreter exits." in out
    )


# ---------------------------------------------------------------------------
# TC-002 — "(none)" when nothing matches
# ---------------------------------------------------------------------------


def test_explore_reports_none_when_no_tribal_memory_matches(env):
    from cairn.mcp_server import tools_graph

    out = tools_graph.explore("numpy_loader")
    assert "=== Tribal memory (0) ===" in out
    lines = out.splitlines()
    idx = next(i for i, ln in enumerate(lines) if ln.startswith("=== Tribal memory"))
    assert lines[idx + 1].strip() == "(none)"


# ---------------------------------------------------------------------------
# TC-003 — a surfaced memory's reference is recorded
# ---------------------------------------------------------------------------


def test_explore_records_reference_for_surfaced_memory(env):
    from cairn.mcp_server import tools_graph

    bundle, db_path = env
    concept_id = _write_tribal(
        bundle,
        "Never evict numpy from sys.modules mid-process",
        "Why: C extensions break if unloaded mid-run.\n"
        "How to apply: keep numpy imported until the interpreter exits.",
        "never-evict-numpy",
    )
    query = "numpy_loader"
    out = tools_graph.explore(query)
    assert "=== Tribal memory (1) ===" in out

    rows = _ref_rows(db_path)
    assert len(rows) == 1
    # concept_id for disk-read concepts is the absolute bundle path (matching
    # what recall_memory's ref rows store).
    assert rows[0]["memory_path"].endswith(concept_id)
    assert rows[0]["context"] == query
    assert rows[0]["session_id"] == f"mcp-{os.getpid()}-{date.today().isoformat()}"


# ---------------------------------------------------------------------------
# TC-004 — capped at 3 entries, title + "How to apply" only; refs only for
# the rendered memories
# ---------------------------------------------------------------------------


def test_explore_caps_section_and_records_only_rendered_memories(env):
    from cairn.mcp_server import tools_graph

    bundle, db_path = env
    for i in range(1, 5):
        _write_tribal(
            bundle,
            f"numpy hazard {i}",
            f"Why: hazard {i}.\nHow to apply: apply fix {i}.",
            f"numpy-hazard-{i}",
        )
    out = tools_graph.explore("numpy_loader")

    assert "=== Tribal memory (3) ===" in out
    section = _tribal_section_lines(out)
    titles = [
        ln.strip() for ln in section if ln.startswith("  ") and not ln.startswith("    ")
    ]
    assert len(titles) == 3
    assert all(ln.startswith("    How to apply: ") for ln in section if "How to apply:" in ln)
    assert not any("Why:" in ln for ln in section)

    shown = set(titles)
    assert len(shown) == 3
    rows = _ref_rows(db_path)
    assert len(rows) == 3
    for row in rows:
        assert bundle.read_concept(row["memory_path"]).title in shown


# ---------------------------------------------------------------------------
# TC-005 — concurrent explore calls don't corrupt reference recording
# ---------------------------------------------------------------------------


def test_concurrent_explore_calls_record_both_references(env):
    from cairn.mcp_server import tools_graph

    bundle, db_path = env
    _write_tribal(
        bundle,
        "Never evict numpy from sys.modules mid-process",
        "Why: C extensions break if unloaded mid-run.\n"
        "How to apply: keep numpy imported until the interpreter exits.",
        "never-evict-numpy",
    )
    query = "numpy_loader"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outs = list(pool.map(lambda _: tools_graph.explore(query), range(2)))
    assert all("=== Tribal memory (1) ===" in o for o in outs)

    rows = _ref_rows(db_path)
    assert len(rows) == 2
    assert {r["context"] for r in rows} == {query}
