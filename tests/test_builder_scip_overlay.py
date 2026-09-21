"""Builder-side SCIP overlay wiring (FR-001, FR-006, FR-007, FR-008): the
build applies configured indexes between edge resolution and persistence,
generates a configured-but-absent index exactly once via the registry,
degrades without the protobuf runtime, and never fails the build on a corrupt
index or a failed generation.

Hermeticity (CONSTITUTION C-04): fixture workspaces are copied into tmp_path
with a bare .git marker and built into tmp DBs -- never the real ~/.cairn.
PATH is pinned to the fixture's stub-bin dir (or an empty dir for the
missing-binary arm); runtime-absence is simulated by monkeypatching
scip_available, so the degradation test also runs without the [scip] extra.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from cairn.graph.builder import build_graph
from cairn.parsers.scip_importer import scip_available

FIXTURES = Path(__file__).parent / "fixtures" / "scip-indexing"

needs_scip = pytest.mark.skipif(not scip_available(), reason="[scip] extra not installed")


@pytest.fixture(autouse=True)
def _hash_embedder(hash_backend):
    """Pin the dep-free hash embedder so builds stay deterministic and offline."""


def _materialize(name: str, tmp_path: Path) -> Path:
    ws = tmp_path / name
    shutil.copytree(FIXTURES / name, ws, ignore=shutil.ignore_patterns(".git"))
    (ws / ".git").mkdir()
    return ws


def _build(name: str, tmp_path: Path, verbose: bool = False) -> tuple[Path, str, dict]:
    ws = _materialize(name, tmp_path)
    db = str(tmp_path / f"{name}.db")
    summary = build_graph(workspace=str(ws), db_path=db, verbose=verbose)
    return ws, db, summary


def _count(db: str, sql: str) -> int:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def _fetch(db: str, sql: str) -> list:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


_IN_INDEX = "f.path like '%in_index.py'"

_CALLREF = (
    "select count(*) from edges e join symbols s on e.source_id=s.id "
    "join files f on s.file_id=f.id where "
    f"{_IN_INDEX} and e.kind in ('calls','references')"
)


# ---------------------------------------------------------------------------
# Overlay wiring (TC-001 full-build intent)
# ---------------------------------------------------------------------------
@needs_scip
def test_build_applies_overlay_and_reports_scip_summary(tmp_path):
    """The wired build lands index-sourced exact edges for the covered file,
    replaces its tree-sitter calls/references, and carries the aggregated
    import record on the summary."""
    ws, db, summary = _build("covered", tmp_path)

    scip_report = summary["scip"]
    assert {"edges", "disagreements", "upgrades"} <= set(scip_report)
    assert scip_report["edges"] > 0
    assert scip_report["edges"] == _count(
        db, "select count(*) from edges where source='scip'"
    )

    assert (
        _count(
            db,
            "select count(*) from edges e join symbols s on e.source_id=s.id "
            "join files f on s.file_id=f.id where "
            f"{_IN_INDEX} and e.kind in ('calls','references') "
            "and e.source='scip' and e.resolution='exact'",
        )
        > 0
    )
    # Per-file authority through the build: no non-scip calls/references remain.
    assert _count(db, f"{_CALLREF} and coalesce(e.source,'') != 'scip'") == 0
    # The overlay touches calls/references only.
    assert (
        _count(
            db,
            "select count(*) from edges where kind in ('contains','imports') "
            "and coalesce(source,'') != 'tree_sitter'",
        )
        == 0
    )


# ---------------------------------------------------------------------------
# Config-read degradation prints like every other (no silent swallows)
# ---------------------------------------------------------------------------
def test_config_load_failure_logs_and_skips_overlay(tmp_path, monkeypatch, capsys):
    """A failing config read degrades loudly: the overlay returns None and the
    failure prints even when verbose is off (the docstring's _log contract)."""
    from cairn.graph import config as config_mod
    from cairn.graph.builder import _apply_scip_overlay

    def boom(_workspace):
        raise RuntimeError("scip section unreadable")

    monkeypatch.setattr(config_mod, "load_config", boom)

    result = _apply_scip_overlay(sqlite3.connect(":memory:"), str(tmp_path), {}, verbose=False)

    assert result is None
    assert "[scip] config load failed" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Runtime-missing degradation (FR-006)
# ---------------------------------------------------------------------------
def test_build_without_scip_runtime_succeeds_tree_sitter_only(
    tmp_path, monkeypatch, capsys
):
    """With the protobuf runtime unavailable, the build succeeds via
    tree-sitter, logs the [scip] install hint (non-verbose — FR-006's hint
    is not -v-gated), and records a scip_runtime_missing skip row."""
    from cairn.parsers import scip_importer

    monkeypatch.setattr(scip_importer, "scip_available", lambda: False)

    ws, db, summary = _build("covered", tmp_path, verbose=False)

    assert _count(db, "select count(*) from edges where source='scip'") == 0
    assert _count(db, _CALLREF) > 0
    skips = _fetch(
        db,
        "select repo_id, path from skipped_files where reason = 'scip_runtime_missing'",
    )
    assert skips and skips[0][0] and skips[0][1] == "index.scip"
    out = capsys.readouterr().out
    assert "[scip]" in out
    assert "runtime unavailable" in out


# ---------------------------------------------------------------------------
# Join-anomaly notice (FR-005): non-verbose builds print the anomaly line
# ---------------------------------------------------------------------------
@needs_scip
def test_join_anomaly_prints_non_verbose_and_keeps_tree_sitter_edges(
    tmp_path, capsys
):
    """A below-threshold document retains its tree-sitter edges (skip row)
    and the build prints the anomaly notice without -v."""
    ws, db, summary = _build("drifted", tmp_path, verbose=False)

    out = capsys.readouterr().out
    assert "join anomaly" in out
    assert _count(db, "select count(*) from edges where source='scip'") == 0
    assert _count(
        db,
        "select count(*) from edges e join symbols s on e.source_id=s.id "
        "join files f on s.file_id=f.id where f.path like '%app.py' "
        "and e.kind in ('calls','references')",
    ) > 0
    assert _count(
        db,
        "select count(*) from skipped_files where reason = 'scip_join_anomaly'",
    ) >= 1
    assert summary["scip"]["join_anomalies"] >= 1


# ---------------------------------------------------------------------------
# Corrupt index (validate-then-write; no rollback on the live connection)
# ---------------------------------------------------------------------------
@needs_scip
def test_corrupt_index_never_fails_the_build_or_writes_partial_edges(tmp_path):
    """Garbage bytes at the configured path abort before any edge is written:
    the build succeeds with edge totals identical to the index-off twin and a
    scip_import_failed skip row."""
    ws = _materialize("covered", tmp_path)
    (ws / "index.scip").write_bytes(b"\x12\x05ab")
    corrupt_db = str(tmp_path / "corrupt.db")
    build_graph(workspace=str(ws), db_path=corrupt_db)

    _, db_off, _ = _build("covered-off", tmp_path)

    assert _count(corrupt_db, "select count(*) from edges") == _count(
        db_off, "select count(*) from edges"
    )
    assert _count(corrupt_db, "select count(*) from edges where source='scip'") == 0
    assert (
        _count(
            corrupt_db,
            "select count(*) from skipped_files where reason = 'scip_import_failed'",
        )
        == 1
    )


# ---------------------------------------------------------------------------
# Generation trigger: absent index generated exactly once, then imported
# (TC-009; FR-007)
# ---------------------------------------------------------------------------
def _stub_path(ws: Path, monkeypatch, bindir: str = "bin") -> None:
    monkeypatch.setenv("PATH", f"{ws / bindir}{os.pathsep}{os.environ['PATH']}")


def test_absent_index_generated_exactly_once_then_imported(tmp_path, monkeypatch):
    """One build: the stub indexer runs exactly once, the index appears at the
    configured path, and its edges import; a second build reuses the existing
    index without regenerating."""
    ws = _materialize("autogen", tmp_path)
    _stub_path(ws, monkeypatch)
    db = str(tmp_path / "autogen.db")
    calls_log = ws / "bin" / "calls.log"

    summary = build_graph(workspace=str(ws), db_path=db)

    assert (ws / "index.scip").read_bytes() == (
        ws / "committed-index.scip"
    ).read_bytes()
    assert len(calls_log.read_text(encoding="utf-8").splitlines()) == 1
    assert summary["scip"]["edges"] > 0
    assert (
        _count(
            db,
            "select count(*) from edges where source='scip' and resolution='exact'",
        )
        > 0
    )

    summary = build_graph(workspace=str(ws), db_path=db)

    assert len(calls_log.read_text(encoding="utf-8").splitlines()) == 1
    assert summary["scip"]["edges"] > 0


def test_existing_index_is_never_rebuilt_by_the_build(tmp_path, monkeypatch):
    """With the index present, the build imports it and never invokes the
    stub (invocation counter absent) nor rewrites the file (mtime pinned)."""
    ws = _materialize("autogen", tmp_path)
    shutil.copy(ws / "committed-index.scip", ws / "index.scip")
    os.utime(ws / "index.scip", ns=(1_000_000_000_000, 1_000_000_000_000))
    mtime_ns = os.stat(ws / "index.scip").st_mtime_ns
    _stub_path(ws, monkeypatch)
    db = str(tmp_path / "autogen.db")

    summary = build_graph(workspace=str(ws), db_path=db)

    assert not (ws / "bin" / "calls.log").exists()
    assert os.stat(ws / "index.scip").st_mtime_ns == mtime_ns
    assert summary["scip"]["edges"] > 0


# ---------------------------------------------------------------------------
# Generation failures degrade observably, never fatally (TC-010 real arm:
# scip_gen_* skip rows + hint under -v; FR-008)
# ---------------------------------------------------------------------------
@needs_scip
def test_missing_binary_degrades_to_tree_sitter_with_skip_record(
    tmp_path, monkeypatch, capsys
):
    """Absent index and no indexer on PATH: tree-sitter build succeeds with a
    scip_gen_missing_binary row naming the configured index path and the
    install hint in the verbose output."""
    ws = _materialize("autogen", tmp_path)
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    db = str(tmp_path / "autogen.db")

    summary = build_graph(workspace=str(ws), db_path=db, verbose=True)

    assert summary["scip"]["edges"] == 0
    assert _count(db, "select count(*) from edges") > 0
    assert _count(db, "select count(*) from edges where source='scip'") == 0
    rows = _fetch(
        db,
        "select repo_id, path from skipped_files "
        "where reason = 'scip_gen_missing_binary'",
    )
    assert rows and rows[0][0] and rows[0][1] == "index.scip"
    assert "npm install" in capsys.readouterr().out


@needs_scip
def test_nonzero_exit_generation_degrades_to_tree_sitter_with_skip_record(
    tmp_path, monkeypatch, capsys
):
    """Absent index and a failing stub indexer: tree-sitter build succeeds
    with a scip_gen_nonzero_exit row and the install hint under -v."""
    ws = _materialize("autogen", tmp_path)
    _stub_path(ws, monkeypatch, bindir="bin-fail")
    db = str(tmp_path / "autogen.db")

    summary = build_graph(workspace=str(ws), db_path=db, verbose=True)

    assert not (ws / "index.scip").exists()
    assert summary["scip"]["edges"] == 0
    assert _count(db, "select count(*) from edges") > 0
    rows = _fetch(
        db,
        "select path from skipped_files where reason = 'scip_gen_nonzero_exit'",
    )
    assert rows and rows[0][0] == "index.scip"
    assert "npm install" in capsys.readouterr().out


@needs_scip
def test_one_languages_failed_generation_never_blocks_another(
    tmp_path, monkeypatch
):
    """Two configured languages, only one with a binary: python generates and
    imports while typescript's missing indexer lands as its own skip row."""
    ws = _materialize("autogen", tmp_path)
    (ws / "cairn.json").write_text(
        json.dumps(
            {"scip": {"indexes": {"python": "index.scip", "typescript": "ts-index.scip"}}}
        ),
        encoding="utf-8",
    )
    _stub_path(ws, monkeypatch)
    db = str(tmp_path / "autogen.db")

    summary = build_graph(workspace=str(ws), db_path=db)

    assert len((ws / "bin" / "calls.log").read_text(encoding="utf-8").splitlines()) == 1
    assert summary["scip"]["edges"] > 0
    assert _fetch(
        db,
        "select path, reason from skipped_files "
        "where reason = 'scip_gen_missing_binary'",
    ) == [("ts-index.scip", "scip_gen_missing_binary")]
