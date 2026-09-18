"""Unit tests for the pack enrichment renders.

Covers the source trimmer (span slice, line cap, graceful empty renders), the
depth-2 blast-radius one-liner (seed_id pinning, depth counts, truncation),
the compass-excerpt picker (module-in-resource match, absent coverage) and
the top-k memory picks (ranking, cap, empty coverage).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cairn.okf.bundle import OKFBundle
from cairn.okf.concept import OKFConcept
from cairn.pack_enrich import (
    blast_radius_line,
    compass_excerpt,
    memory_picks,
    render_source,
)


def _numbered_lines(n: int) -> str:
    return "\n".join(f"line {i + 1}" for i in range(n)) + "\n"


def _seed_repo_and_file(
    conn: sqlite3.Connection, tmp_path: Path, file_id: str, text: str
) -> Path:
    """One repo + one file row; the stored path is absolute, which
    resolve_file_path passes through unchanged."""
    path = tmp_path / f"{file_id}.py"
    path.write_text(text)
    conn.execute(
        "INSERT INTO repos (id, name, path) VALUES (?, ?, ?)",
        (f"repo-{file_id}", file_id, str(tmp_path)),
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (?, ?, ?, ?)",
        (file_id, f"repo-{file_id}", str(path), "python"),
    )
    return path


def _seed_symbol(
    conn: sqlite3.Connection,
    sym_id: str,
    file_id: str,
    name: str,
    line_start: int | None,
    line_end: int | None,
) -> None:
    conn.execute(
        """INSERT INTO symbols (id, file_id, name, qualified_name, kind,
                                line_start, line_end)
           VALUES (?, ?, ?, ?, 'function', ?, ?)""",
        (sym_id, file_id, name, name, line_start, line_end),
    )


def _seed_call(
    conn: sqlite3.Connection, source_id: str, target_id: str, line: int = 1
) -> None:
    conn.execute(
        "INSERT INTO edges (id, source_id, target_id, target_name, kind, line,"
        " resolution) VALUES (?, ?, ?, ?, 'calls', ?, 'exact')",
        (f"e-{source_id}-{target_id}-{line}", source_id, target_id, target_id, line),
    )


def _bundle(tmp_path: Path, *concepts: OKFConcept) -> OKFBundle:
    bundle = OKFBundle(str(tmp_path / ".knowledge"))
    for concept in concepts:
        bundle.write_concept(concept)
    return bundle


def _compass(cid: str, title: str, resource: str | None, body: str) -> OKFConcept:
    return OKFConcept(
        type="Compass", concept_id=cid, title=title, resource=resource, body=body
    )


def _memory(
    cid: str, title: str, body: str, description: str | None = None
) -> OKFConcept:
    return OKFConcept(
        type="Memory", concept_id=cid, title=title, body=body, description=description
    )


# --- render_source ----------------------------------------------------------


def test_render_source_returns_verbatim_span(fresh_db, tmp_path):
    _seed_repo_and_file(fresh_db, tmp_path, "f1", _numbered_lines(5))
    _seed_symbol(fresh_db, "s1", "f1", "mid", 2, 4)
    assert render_source(fresh_db, "s1") == "line 2\nline 3\nline 4"


def test_render_source_trims_to_line_cap(fresh_db, tmp_path):
    _seed_repo_and_file(fresh_db, tmp_path, "f1", _numbered_lines(60))
    _seed_symbol(fresh_db, "s1", "f1", "big", 1, 60)
    out = render_source(fresh_db, "s1", line_cap=10)
    lines = out.split("\n")
    assert lines[0] == "line 1"
    assert len(lines) == 11
    assert lines[-1] == "... (+50 more lines trimmed)"
    assert "line 11" not in out


def test_render_source_clamps_cap_to_one(fresh_db, tmp_path):
    _seed_repo_and_file(fresh_db, tmp_path, "f1", _numbered_lines(60))
    _seed_symbol(fresh_db, "s1", "f1", "big", 1, 60)
    out = render_source(fresh_db, "s1", line_cap=0)
    lines = out.split("\n")
    assert lines[0] == "line 1"
    assert lines[-1] == "... (+59 more lines trimmed)"


def test_render_source_missing_file_returns_empty(fresh_db, tmp_path):
    gone = tmp_path / "gone.py"
    conn = fresh_db
    conn.execute(
        "INSERT INTO repos (id, name, path) VALUES ('r', 'r', ?)", (str(tmp_path),)
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES"
        " ('f1', 'r', ?, 'python')",
        (str(gone),),
    )
    _seed_symbol(conn, "s1", "f1", "ghost", 1, 3)
    assert render_source(conn, "s1") == ""


@pytest.mark.parametrize("start,end", [(None, 5), (5, 3)])
def test_render_source_invalid_span_returns_empty(fresh_db, tmp_path, start, end):
    _seed_repo_and_file(fresh_db, tmp_path, "f1", _numbered_lines(10))
    _seed_symbol(fresh_db, "s1", "f1", "broken", start, end)
    assert render_source(fresh_db, "s1") == ""


def test_render_source_null_end_renders_single_line(fresh_db, tmp_path):
    _seed_repo_and_file(fresh_db, tmp_path, "f1", _numbered_lines(10))
    _seed_symbol(fresh_db, "s1", "f1", "single", 5, None)
    assert render_source(fresh_db, "s1") == "line 5"


def test_render_source_unknown_symbol_returns_empty(fresh_db):
    assert render_source(fresh_db, "no-such-id") == ""


# --- blast_radius_line ------------------------------------------------------


def test_blast_radius_line_walks_two_hops(fresh_db, tmp_path):
    _seed_repo_and_file(fresh_db, tmp_path, "f1", _numbered_lines(12))
    _seed_symbol(fresh_db, "s-entry", "f1", "entry", 1, 2)
    _seed_symbol(fresh_db, "s-mid", "f1", "mid", 4, 5)
    _seed_symbol(fresh_db, "s-inner", "f1", "inner", 7, 8)
    _seed_symbol(fresh_db, "s-leaf", "f1", "leaf", 10, 11)
    _seed_call(fresh_db, "s-entry", "s-mid")
    _seed_call(fresh_db, "s-mid", "s-inner")
    _seed_call(fresh_db, "s-inner", "s-leaf")
    out = blast_radius_line(fresh_db, "leaf", "s-leaf")
    assert "\n" not in out
    assert "total=3" in out
    assert "depth 0: 1" in out
    assert "depth 1: 1" in out
    assert "depth 2: 1" in out
    assert "top: inner, mid, entry" in out
    assert "truncated" not in out


def test_blast_radius_line_seed_id_pins_exact_row(fresh_db, tmp_path):
    _seed_repo_and_file(fresh_db, tmp_path, "f1", _numbered_lines(6))
    _seed_repo_and_file(fresh_db, tmp_path, "f2", _numbered_lines(6))
    _seed_symbol(fresh_db, "s1", "f1", "handle", 1, 3)
    _seed_symbol(fresh_db, "s2", "f2", "handle", 1, 3)
    _seed_symbol(fresh_db, "s-caller", "f2", "caller2", 5, 6)
    _seed_call(fresh_db, "s-caller", "s2")
    out1 = blast_radius_line(fresh_db, "handle", "s1")
    assert "total=0" in out1
    assert "top:" not in out1
    out2 = blast_radius_line(fresh_db, "handle", "s2")
    assert "total=1" in out2
    assert "top: caller2" in out2


def test_blast_radius_line_reports_truncation(fresh_db, tmp_path):
    _seed_repo_and_file(fresh_db, tmp_path, "f1", _numbered_lines(10))
    _seed_symbol(fresh_db, "s-t", "f1", "target", 1, 2)
    for i, caller in enumerate(["c1", "c2", "c3"]):
        _seed_symbol(fresh_db, f"s-{caller}", "f1", caller, 4 + i * 2, 5 + i * 2)
        _seed_call(fresh_db, f"s-{caller}", "s-t", line=i + 1)
    out = blast_radius_line(fresh_db, "target", "s-t", limit=1)
    assert "total=1" in out
    assert out.endswith("; truncated")


def test_blast_radius_line_without_callers(fresh_db, tmp_path):
    _seed_repo_and_file(fresh_db, tmp_path, "f1", _numbered_lines(4))
    _seed_symbol(fresh_db, "s1", "f1", "lone", 1, 3)
    out = blast_radius_line(fresh_db, "lone", "s1")
    assert "total=0" in out
    assert "depth 0: 0, depth 1: 0, depth 2: 0" in out
    assert "top:" not in out
    assert "truncated" not in out


# --- compass_excerpt --------------------------------------------------------


def test_compass_excerpt_matches_module_resource(tmp_path):
    bundle = _bundle(
        tmp_path,
        _compass("compass/graph", "graph", "src/cairn/graph", "Graph layer guide."),
    )
    out = compass_excerpt(bundle, "src/cairn/graph/traversal.py")
    assert out == "# graph\n\nGraph layer guide."


def test_compass_excerpt_first_match_wins(tmp_path):
    bundle = _bundle(
        tmp_path,
        _compass("compass/other", "other", "src/other", "Other guide."),
        _compass("compass/graph", "graph", "src/cairn/graph", "Graph layer guide."),
    )
    out = compass_excerpt(bundle, "src/cairn/graph/traversal.py")
    assert out == "# graph\n\nGraph layer guide."


def test_compass_excerpt_no_coverage_returns_none(tmp_path):
    bundle = _bundle(
        tmp_path,
        _compass("compass/dashboard", "dashboard", "src/cairn/dashboard", "Dash."),
    )
    assert compass_excerpt(bundle, "src/cairn/graph/traversal.py") is None


def test_compass_excerpt_empty_bundle_returns_none(tmp_path):
    assert compass_excerpt(_bundle(tmp_path), "src/cairn/graph/traversal.py") is None


def test_compass_excerpt_ignores_concepts_without_resource(tmp_path):
    bundle = _bundle(tmp_path, _compass("compass/loose", "loose", None, "Body."))
    assert compass_excerpt(bundle, "compass/loose/file.py") is None


# --- memory_picks -----------------------------------------------------------


def test_memory_picks_rank_by_token_overlap(fresh_db, tmp_path):
    bundle = _bundle(
        tmp_path,
        _memory(
            "memory/tribal/retry-defaults",
            "Retry backoff policy",
            "ApiFactory retries use exponential backoff with jitter.",
            description="decision",
        ),
        _memory("memory/tribal/backoff-only", "Backoff policy", "Generic note."),
        _memory("memory/tribal/unrelated", "Dashboard theming", "CSS variables."),
    )
    picks = memory_picks(fresh_db, bundle, "retry backoff policy")
    assert len(picks) == 2
    assert picks[0] == "Retry backoff policy\ndecision"
    assert picks[1] == "Backoff policy"


def test_memory_picks_caps_at_k(fresh_db, tmp_path):
    bundle = _bundle(
        tmp_path,
        _memory("memory/tribal/m1", "Retry backoff policy defaults", "b1"),
        _memory("memory/tribal/m2", "Backoff policy", "b2"),
        _memory("memory/tribal/m3", "Retry helper", "b3"),
    )
    picks = memory_picks(fresh_db, bundle, "retry backoff policy", k=2)
    assert [p.split("\n")[0] for p in picks] == [
        "Retry backoff policy defaults",
        "Backoff policy",
    ]


def test_memory_picks_without_memories_returns_empty(fresh_db, tmp_path):
    assert memory_picks(fresh_db, _bundle(tmp_path), "retry backoff policy") == []


def test_memory_picks_no_matching_tokens_returns_empty(fresh_db, tmp_path):
    bundle = _bundle(
        tmp_path,
        _memory("memory/tribal/other", "Dashboard theming", "CSS variables."),
    )
    assert memory_picks(fresh_db, bundle, "retry backoff policy") == []
