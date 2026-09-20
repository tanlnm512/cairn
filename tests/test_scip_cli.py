"""The `cairn import-scip` command, the scip echo in `cairn config`, and the
scip provenance rendering in `cairn stats` / the build summary panel.

Hermeticity (CONSTITUTION C-04): `cairn.cli` is imported lazily inside the
tests; every store lives under tmp_path via the suite's `_hermetic_env`
fixture. The overlay entry itself (`cairn.parsers.scip_importer`) is stubbed
via sys.modules for the wiring test — the real importer's behavior is covered
by tests/test_scip_import.py and TC-022.
"""
from __future__ import annotations

import json
import sys
import types

from cairn.graph.schema import get_db


def _invoke(args):
    """Run the cairn CLI in-process (lazy import per C-04)."""
    from click.testing import CliRunner

    from cairn.cli import main

    return CliRunner().invoke(main, args, catch_exceptions=False)


def _write_scip_config(ws, payload):
    (ws / "cairn.json").write_text(json.dumps(payload), encoding="utf-8")


# --------------------------------------------------------------------------
# cairn config echoes the scip key
# --------------------------------------------------------------------------
def test_config_json_echoes_configured_scip_indexes(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    _write_scip_config(ws, {"scip": {"indexes": {"python": "index.scip"}}})
    monkeypatch.chdir(ws)

    result = _invoke(["config", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["scip"] == {"indexes": {"python": "index.scip"}}


def test_config_json_carries_scip_key_when_unconfigured(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.chdir(ws)

    result = _invoke(["config", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["scip"] == {}


def test_config_echoes_scip_indexes_after_namespaces(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    _write_scip_config(ws, {"scip": {"indexes": {"python": "index.scip"}}})
    monkeypatch.chdir(ws)

    result = _invoke(["config"])

    assert result.exit_code == 0, result.output
    assert "python -> index.scip" in result.output
    assert result.output.index("repo_namespaces") < result.output.index("scip indexes")


def test_config_echoes_none_configured_without_scip_key(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.chdir(ws)

    result = _invoke(["config"])

    assert result.exit_code == 0, result.output
    assert "(none configured" in result.output


# --------------------------------------------------------------------------
# cairn import-scip requires a built store
# --------------------------------------------------------------------------
def test_import_scip_help_exits_zero():
    result = _invoke(["import-scip", "--help"])

    assert result.exit_code == 0


def test_import_scip_missing_db_errors_without_creating_it(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.chdir(ws)
    idx = tmp_path / "index.scip"
    idx.write_bytes(b"")
    missing_db = tmp_path / "nope.db"

    result = _invoke(
        ["import-scip", str(idx), "--db", str(missing_db), "--workspace", str(ws)]
    )

    assert result.exit_code != 0
    assert "cairn build" in result.output
    assert not missing_db.exists(), "the gate must fire before get_db creates a store"


def test_import_scip_empty_db_errors(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.chdir(ws)
    idx = tmp_path / "index.scip"
    idx.write_bytes(b"")
    empty_db = tmp_path / "empty.db"
    get_db(str(empty_db)).close()  # schema applied, zero symbols

    result = _invoke(
        ["import-scip", str(idx), "--db", str(empty_db), "--workspace", str(ws)]
    )

    assert result.exit_code != 0
    assert "no symbols" in result.output


def test_import_scip_invokes_overlay_entry(
    caller_callee_ws, caller_callee_db, tmp_path, monkeypatch
):
    db_path = str(tmp_path / "graph.db")
    caller_callee_db(db_path).close()
    idx = tmp_path / "index.scip"
    idx.write_bytes(b"")

    calls = {}
    stub = types.ModuleType("cairn.parsers.scip_importer")

    def import_scip_file(conn, scip_path, workspace):
        calls["argv"] = (scip_path, workspace)
        calls["symbols"] = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        return {"edges": 3, "disagreements": 1}

    stub.import_scip_file = import_scip_file
    monkeypatch.setitem(sys.modules, "cairn.parsers.scip_importer", stub)

    result = _invoke(
        ["import-scip", str(idx), "--db", db_path, "--workspace", str(caller_callee_ws)]
    )

    assert result.exit_code == 0, result.output
    assert calls["argv"] == (str(idx), str(caller_callee_ws))
    assert calls["symbols"] > 0
    assert "3" in result.output and "1" in result.output


# --------------------------------------------------------------------------
# Provenance rendering (FR-013): `cairn stats` resolution-share block and the
# build summary panel's scip line. Zero-value degradation: no scip edges ->
# no extra output anywhere.
# --------------------------------------------------------------------------
def _seed_edge_db(db_path, edges):
    """Full-schema store with one python file, two symbols, and edge rows of
    (id, source_id, target_id, target_name, kind, resolution, source)."""
    conn = get_db(str(db_path))
    conn.execute(
        "INSERT INTO repos (id, name, path, language) VALUES ('r1', 'r1', '.', 'python')"
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) "
        "VALUES ('f1', 'r1', 'a.py', 'python')"
    )
    conn.executemany(
        "INSERT INTO symbols (id, file_id, name, kind) VALUES (?,?,?,?)",
        [("s1", "f1", "main", "function"), ("s2", "f1", "helper", "function")],
    )
    conn.executemany(
        "INSERT INTO edges (id, source_id, target_id, target_name, kind, "
        "resolution, source) VALUES (?,?,?,?,?,?,?)",
        edges,
    )
    conn.commit()
    conn.close()


def test_stats_renders_scip_provenance_and_language_share(tmp_path):
    db = tmp_path / "graph.db"
    _seed_edge_db(
        db,
        [
            ("e1", "s1", "s2", None, "calls", "exact", None),
            ("e2", "s1", None, "Helper", "calls", "ambiguous", "scip"),
            ("e3", "s1", "s2", None, "calls", "exact", "scip"),
            ("e4", "s1", "s2", None, "references", "exact", "scip"),
        ],
    )

    result = _invoke(["stats", "--db", str(db)])

    assert result.exit_code == 0, result.output
    out = result.output.lower()
    assert "edge sources" in out
    assert "1 tree-sitter / 3 scip" in out
    assert "python 75%" in out
    # The resolution-share block itself stays intact.
    assert "3 exact / 1 ambiguous / 0 unresolved" in result.output


def test_stats_stays_silent_without_scip_edges(tmp_path):
    db = tmp_path / "graph.db"
    _seed_edge_db(db, [("e1", "s1", "s2", None, "calls", "exact", None)])

    result = _invoke(["stats", "--db", str(db)])

    assert result.exit_code == 0, result.output
    assert "scip" not in result.output.lower()
    assert "edge sources" not in result.output


def _run_stubbed_build(tmp_path, monkeypatch, summary):
    """`cairn build` with build_graph stubbed to persist an empty store and
    return the given summary — the renderer's contract, not the builder's."""
    from cairn.graph import builder as builder_mod

    def fake_build_graph(**kwargs):
        from cairn.graph.schema import get_db as _get_db

        _get_db(str(kwargs["db_path"])).close()
        return summary

    monkeypatch.setattr(builder_mod, "build_graph", fake_build_graph)
    return _invoke([
        "build", "--db", str(tmp_path / "graph.db"), "--workspace", str(tmp_path / "ws"),
    ])


def test_build_summary_renders_scip_line_when_edges_exist(tmp_path, monkeypatch):
    summary = {
        "repos": 1, "files": 2, "symbols": 4, "edges": 9, "imports": 0,
        "skipped": 0, "resolution": {"exact": 5, "ambiguous": 2, "unresolved": 2},
        "scip": {"edges": 5, "disagreements": 2},
    }

    result = _run_stubbed_build(tmp_path, monkeypatch, summary)

    assert result.exit_code == 0, result.output
    assert "scip" in result.output.lower()
    assert "5 edges (2 disagreements)" in result.output


def test_build_summary_omits_scip_line_without_scip_edges(tmp_path, monkeypatch):
    base = {
        "repos": 1, "files": 2, "symbols": 4, "edges": 9, "imports": 0,
        "skipped": 0, "resolution": {"exact": 5, "ambiguous": 2, "unresolved": 2},
    }

    result_absent = _run_stubbed_build(tmp_path, monkeypatch, dict(base))
    assert result_absent.exit_code == 0, result_absent.output
    assert "scip" not in result_absent.output.lower()

    (tmp_path / "zero").mkdir()
    result_zero = _run_stubbed_build(
        tmp_path, monkeypatch, {**base, "scip": {"edges": 0, "disagreements": 0}}
    )
    assert result_zero.exit_code == 0, result_zero.output
    assert "scip" not in result_zero.output.lower()
