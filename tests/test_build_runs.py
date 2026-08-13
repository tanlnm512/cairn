"""T09: build-run instrumentation persists one ``build_runs`` row per pass.

Covers the ``kind='build'`` path end-to-end (the richest row -- counts,
resolution mix, phase timings) and the ``record_build_run`` helper that the
embed/sync/incremental entry points share (verifying unspecified columns stay
NULL rather than being forced to a sentinel). Also pins the spec invariant
that a telemetry write can never fail a build (5.4/5.6 -- analytics, not
correctness).
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path


from cairn.graph.builder import build_graph, record_build_run


FIXTURE_FILES = {
    "Simple.kt": (
        "class Simple {\n"
        "    fun doWork() {}\n"
        "}\n"
    ),
}


def _make_fixture(tmp_path: Path, name: str) -> str:
    """Minimal workspace: one git repo with a single Kotlin source file."""
    workspace = tmp_path / name
    repo = workspace / "demo"
    (repo / ".git").mkdir(parents=True)
    for fname, contents in FIXTURE_FILES.items():
        (repo / fname).write_text(contents)
    return str(workspace)


def _build_runs_rows(db_path: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT * FROM build_runs ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def test_build_graph_writes_build_runs_row(tmp_path):
    """A full build persists exactly one kind='build' row with accurate counts.

    The row's count/resolution columns must match the returned summary (the
    single source of truth the CLI prints), phase_timings is non-empty JSON,
    and session_id resolves via the CAIRN_SESSION env default.
    """
    workspace = _make_fixture(tmp_path, "build_run")
    db_path = str(tmp_path / "build.db")

    summary = build_graph(workspace=workspace, db_path=db_path, verbose=False)

    rows = _build_runs_rows(db_path)
    assert len(rows) == 1, f"expected 1 build_runs row, got {len(rows)}"
    row = rows[0]

    assert row["kind"] == "build"
    # Count columns mirror the summary dict the CLI displays.
    assert row["repos"] == summary["repos"]
    assert row["files"] == summary["files"]
    assert row["symbols"] == summary["symbols"]
    assert row["edges"] == summary["edges"]
    assert row["parse_errors"] == summary["parse_errors"]
    assert row["skipped"] == summary["skipped"]
    # Resolution mix straight from the resolver stats.
    res = summary["resolution"]
    assert row["resolution_exact"] == res["exact"]
    assert row["resolution_ambiguous"] == res["ambiguous"]
    assert row["resolution_unresolved"] == res["unresolved"]
    # Phase timings captured from the progress callbacks (in-memory build ->
    # all five phases fire). Each is a non-negative second value.
    assert row["phase_timings"] is not None
    phases = json.loads(row["phase_timings"])
    assert {"scan", "parse", "insert", "resolve", "persist"} <= set(phases)
    for v in phases.values():
        assert v >= 0
    # Duration recorded; started_at is an ISO timestamp.
    assert row["duration_s"] is not None and row["duration_s"] >= 0
    assert row["started_at"] and "T" in row["started_at"]
    # session_id defaults from the env when CAIRN_SESSION is unset.
    assert row["session_id"] == os.environ.get("CAIRN_SESSION", "unknown")


def test_build_run_telemetry_failure_does_not_break_build(tmp_path, monkeypatch):
    """A failed build_runs insert must never propagate (spec 5.4/5.6).

    Force get_db (used only by record_build_run within the builder module) to
    raise; the build itself must still return its summary and persist the
    graph to disk.
    """
    import cairn.graph.builder as builder_mod

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("simulated lock contention")

    # get_db is referenced only by record_build_run in builder.py (build_graph
    # uses init_db / get_build_db), so patching it isolates the telemetry path.
    monkeypatch.setattr(builder_mod, "get_db", boom)

    workspace = _make_fixture(tmp_path, "no_fail")
    db_path = str(tmp_path / "nofail.db")

    summary = build_graph(workspace=workspace, db_path=db_path, verbose=False)
    # Build succeeded and the graph persisted despite the telemetry fault.
    assert summary["files"] > 0
    assert Path(db_path).exists()


def test_record_build_run_leaves_unspecified_columns_null(tmp_path):
    """embed/sync/incremental pass only the columns they have; the rest stay NULL.

    This is the helper those three entry points share, exercised directly so
    the non-build kinds are covered without heavy git/embedding fixtures.
    """
    db_path = str(tmp_path / "embed.db")
    # Ensure the schema (incl. build_runs) exists at the target path.
    conn = sqlite3.connect(db_path)
    try:
        from cairn.graph.schema import _apply_schema
        _apply_schema(conn)
        conn.commit()
    finally:
        conn.close()

    record_build_run(
        db_path,
        "embed",
        started_at=1_700_000_000.0,
        duration_s=2.5,
        symbols=42,
        skipped=3,
    )

    rows = _build_runs_rows(db_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "embed"
    assert row["symbols"] == 42
    assert row["skipped"] == 3
    assert row["duration_s"] == 2.5
    # Columns the embed path doesn't populate must be NULL, not a sentinel.
    assert row["repos"] is None
    assert row["files"] is None
    assert row["edges"] is None
    assert row["phase_timings"] is None
    assert row["resolution_exact"] is None
    assert row["parse_errors"] is None


def test_two_builds_yield_two_rows(tmp_path):
    """Successive single-repo builds accumulate history (spec G2 'build history').

    Uses ``repo_filter`` (the on-disk live-write path) rather than a full
    workspace rebuild: a full rebuild atomically swaps the whole DB via
    ``backup_to`` and so resets analytics history -- the same pre-existing
    characteristic that already resets ``tool_metrics``. The live-write paths
    (single-repo build, incremental update, sync, embed) all accumulate.
    """
    workspace = _make_fixture(tmp_path, "trend")
    db_path = str(tmp_path / "trend.db")

    build_graph(workspace=workspace, repo_filter="demo", db_path=db_path, verbose=False)
    build_graph(workspace=workspace, repo_filter="demo", db_path=db_path, verbose=False)

    rows = _build_runs_rows(db_path)
    assert len(rows) == 2
    assert {r["kind"] for r in rows} == {"build"}
