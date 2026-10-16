"""Regression tests for audit round 2 batch A3 (query/graph fix surfaces)."""
from __future__ import annotations

import json
import sqlite3

import pytest
from click.testing import CliRunner

from cairn.cli.main import main
from cairn.graph import cross_repo
from cairn.graph.dataflow import build_dataflow_index, get_dataflow
from cairn.graph.queries import trace_flow
from cairn.graph.schema import _apply_schema


def _seeded_store(tmp_path, drift: bool = False):
    """On-disk single-repo store with symbol alpha; drift=True spoils size/mtime."""
    ws = tmp_path / "ws"
    repo = ws / "r1"
    (repo / ".git").mkdir(parents=True)
    src = "def alpha():\n    return 1\n"
    (repo / "a.py").write_text(src)
    db = tmp_path / "g.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    conn.execute(
        "INSERT INTO repos (id, name, path, language) VALUES ('r1', 'r1', ?, 'python')",
        (str(repo),),
    )
    st = (repo / "a.py").stat()
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language, size, mtime) "
        "VALUES ('f1', 'r1', 'a.py', 'python', ?, ?)",
        (1 if drift else st.st_size, 1.0 if drift else st.st_mtime),
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, line_start) "
        "VALUES ('s1', 'f1', 'alpha', 'function', 1)"
    )
    conn.commit()
    conn.close()
    return ws, db


def _plain_store(tmp_path):
    """On-disk store with no workspace behind it (no refresh machinery)."""
    db = tmp_path / "g.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    conn.commit()
    conn.close()
    return db


def _files_size(db) -> object:
    check = sqlite3.connect(str(db))
    try:
        return check.execute("SELECT size FROM files WHERE id = 'f1'").fetchone()[0]
    finally:
        check.close()


# --- G080/G081: the query family honors CAIRN_READ_ONLY ----------------------


@pytest.mark.parametrize(
    "args, expected_exit",
    [
        (["def", "alpha"], 0),
        (["search", "alpha"], 0),
        (["impact", "alpha", "--json"], 1),
        (["deps", "r1"], 1),
    ],
    ids=["def", "search", "impact", "deps"],
)
def test_query_commands_never_write_under_read_only(
    tmp_path, monkeypatch, args, expected_exit
):
    ws, db = _seeded_store(tmp_path, drift=True)
    monkeypatch.setenv("CAIRN_WORKSPACE", str(ws))
    monkeypatch.setenv("CAIRN_READ_ONLY", "1")
    monkeypatch.delenv("CAIRN_NO_REFRESH", raising=False)

    result = CliRunner().invoke(main, [*args, "--db", str(db)])
    assert result.exit_code == expected_exit, result.output
    assert _files_size(db) == 1, "store was rewritten under CAIRN_READ_ONLY"


# --- G079: impact/deps --json exit nonzero on empty results ------------------


def test_impact_and_deps_json_empty_exit_nonzero(tmp_path, monkeypatch):
    ws, db = _seeded_store(tmp_path)
    monkeypatch.setenv("CAIRN_WORKSPACE", str(ws))
    runner = CliRunner()

    result = runner.invoke(main, ["impact", "alpha", "--json", "--db", str(db)])
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["total"] == 0

    result = runner.invoke(main, ["deps", "r1", "--json", "--db", str(db)])
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout) == {"dependencies": [], "dependents": []}


# --- G121: unbounded --depth is rejected at the CLI boundary -----------------


@pytest.mark.parametrize("depth", ["0", "-3", "101"])
def test_impact_depth_is_bounded(tmp_path, monkeypatch, depth):
    ws, db = _seeded_store(tmp_path)
    monkeypatch.setenv("CAIRN_WORKSPACE", str(ws))
    result = CliRunner().invoke(
        main, ["impact", "alpha", "--depth", depth, "--db", str(db)]
    )
    assert result.exit_code == 2, result.output
    assert "--depth must be between 1 and 100" in result.output


# --- G091: tree prints a truncation notice past 30 buckets -------------------


def test_tree_reports_truncation(tmp_path):
    db = _plain_store(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO repos (id, name, path, language) VALUES ('r1', 'r1', '/ws/r1', 'python')"
    )
    for i in range(35):
        conn.execute(
            "INSERT INTO files (id, repo_id, path, language) VALUES (?, 'r1', ?, 'python')",
            (f"f{i}", f"d{i:02d}/x.py"),
        )
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind) VALUES (?, ?, ?, 'function')",
            (f"s{i}", f"f{i}", f"sym{i}"),
        )
    conn.commit()
    conn.close()

    result = CliRunner().invoke(main, ["tree", "r1", "--db", str(db)])
    assert result.exit_code == 0, result.output
    assert "... and 5 more directories" in result.output


# --- G078: empty path patterns are rejected ----------------------------------


@pytest.mark.parametrize("from_pat, to_pat", [("", "x"), ("  ", "x"), ("x", "")])
def test_path_rejects_empty_patterns(tmp_path, from_pat, to_pat):
    db = _plain_store(tmp_path)
    result = CliRunner().invoke(
        main, ["path", "--db", str(db), "--from", from_pat, "--to", to_pat]
    )
    assert result.exit_code == 2, result.output
    assert "non-empty" in result.output


# --- G022: trace_flow scopes same-named symbols by resolved id ---------------


def test_trace_flow_scopes_same_named_symbols(fresh_db):
    fresh_db.execute(
        "INSERT INTO repos (id, name, path, language) VALUES ('r', 'r', '/r', 'python')"
    )
    fresh_db.executemany(
        "INSERT INTO files (id, repo_id, path, language) VALUES (?,?,?,?)",
        [("f1", "r", "a.py", "python"), ("f2", "r", "b.py", "python")],
    )
    # Insertion order pins both name-based fallbacks pre-fix: the entry seed
    # takes dup@a.py, the name-based defn lookup takes shared@b.py.
    fresh_db.executemany(
        "INSERT INTO symbols (id, file_id, name, kind) VALUES (?,?,?,?)",
        [
            ("s_d1", "f1", "dup", "function"),
            ("s_d2", "f2", "dup", "function"),
            ("s_h2", "f2", "shared", "function"),
            ("s_h1", "f1", "shared", "function"),
        ],
    )
    fresh_db.executemany(
        "INSERT INTO edges (id, source_id, target_id, target_name, kind, line, resolution) "
        "VALUES (?,?,?,?,?,?,?)",
        [
            ("e1", "s_d1", "s_h1", "shared", "calls", 1, "exact"),
            ("e2", "s_d2", "s_h2", "shared", "calls", 1, "exact"),
        ],
    )
    fresh_db.commit()

    res = trace_flow(fresh_db, "dup")
    chain = [(row["symbol"], row["file"]) for row in res["chain"]]
    assert res["total"] == 2
    assert chain == [("dup", "a.py"), ("shared", "a.py")]


# --- G107: dependent namespace matching is case-sensitive --------------------


def test_dependents_namespace_match_is_case_sensitive(fresh_db, monkeypatch):
    cross_repo._reset_namespaces_cache()
    monkeypatch.setenv(
        "CAIRN_REPO_NAMESPACES", json.dumps({"com.custom.sdk": "sdk"})
    )
    cur = fresh_db.cursor()
    cur.execute(
        "INSERT INTO repos (id, name, path, language) VALUES ('app', 'app', '/app', 'kotlin')"
    )
    cur.execute(
        "INSERT INTO repos (id, name, path, language) VALUES ('sdk', 'sdk', '/sdk', 'kotlin')"
    )
    cur.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES ('f1', 'app', '/app/A.kt', 'kotlin')"
    )
    cur.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES ('f2', 'app', '/app/B.kt', 'kotlin')"
    )
    cur.execute(
        "INSERT INTO imports (id, file_id, imported_path, line) VALUES ('i1', 'f1', 'com.custom.sdk.api', 1)"
    )
    cur.execute(
        "INSERT INTO imports (id, file_id, imported_path, line) VALUES ('i2', 'f2', 'COM.CUSTOM.SDK.api', 1)"
    )
    fresh_db.commit()

    deps = cross_repo.cross_repo_deps(fresh_db, "app")["dependencies"]
    assert [d["count"] for d in deps] == [1]  # exact-case import only

    dependents = cross_repo.cross_repo_deps(fresh_db, "sdk")["dependents"]
    assert [d["repo"] for d in dependents] == ["app"]
    assert [d["count"] for d in dependents] == [1]  # uppercase import never counts
    cross_repo._reset_namespaces_cache()


# --- G108: within_repo stays scoped to the row's repo ------------------------


def test_dataflow_within_repo_excludes_cross_repo_callers(fresh_db):
    fresh_db.executemany(
        "INSERT INTO repos (id, name, path, language) VALUES (?,?,?,?)",
        [("r1", "r1", "/ws/r1", "python"), ("r2", "r2", "/ws/r2", "python")],
    )
    fresh_db.executemany(
        "INSERT INTO files (id, repo_id, path, language) VALUES (?,?,?,?)",
        [("f_r1", "r1", "a.py", "python"), ("f_r2", "r2", "b.py", "python")],
    )
    fresh_db.executemany(
        "INSERT INTO symbols (id, file_id, name, kind) VALUES (?,?,?,?)",
        [
            ("s_b", "f_r1", "B", "function"),
            ("s_a", "f_r2", "A", "function"),
            ("s_c", "f_r2", "C", "function"),
        ],
    )
    # B (repo r1) and C (repo r2) both call A (repo r2).
    fresh_db.executemany(
        "INSERT INTO edges (id, source_id, target_id, target_name, kind, line, resolution) "
        "VALUES (?,?,?,?,?,?,?)",
        [
            ("e_b", "s_b", "s_a", "A", "calls", 1, "exact"),
            ("e_c", "s_c", "s_a", "A", "calls", 2, "exact"),
        ],
    )
    fresh_db.commit()

    build_dataflow_index(fresh_db)
    df = get_dataflow(fresh_db, "A")
    assert sorted(df["within_repo"]) == ["C"]


# --- G109: build count matches the distinct rows written ---------------------


def test_dataflow_build_count_dedupes_name_repo(fresh_db):
    fresh_db.execute(
        "INSERT INTO repos (id, name, path, language) VALUES ('r1', 'r1', '/ws/r1', 'python')"
    )
    fresh_db.executemany(
        "INSERT INTO files (id, repo_id, path, language) VALUES (?,?,?,?)",
        [("f1", "r1", "a.py", "python"), ("f2", "r1", "b.py", "python")],
    )
    fresh_db.executemany(
        "INSERT INTO symbols (id, file_id, name, kind) VALUES (?,?,?,?)",
        [("s1", "f1", "dup", "function"), ("s2", "f2", "dup", "function")],
    )
    fresh_db.commit()

    count = build_dataflow_index(fresh_db)
    rows = fresh_db.execute("SELECT COUNT(*) FROM dataflow").fetchone()[0]
    assert count == 1
    assert rows == 1


# --- G066/G067/G110: dataflow lookup text output -----------------------------


def _public_store(tmp_path, names=("pub_y",)):
    db = _plain_store(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO repos (id, name, path, language) VALUES ('r1', 'r1', '/ws/r1', 'python')"
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES ('f1', 'r1', 'a.py', 'python')"
    )
    for i, name in enumerate(names):
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind) VALUES (?, 'f1', ?, 'function')",
            (f"s{i}", name),
        )
    conn.commit()
    conn.close()
    return db


def test_dataflow_lookup_names_non_public_symbols(tmp_path):
    db = _public_store(tmp_path, names=("pub_y", "_priv"))
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    build_dataflow_index(conn)
    conn.close()
    runner = CliRunner()

    result = runner.invoke(main, ["dataflow", "lookup", "_priv", "--db", str(db)])
    assert result.exit_code == 1
    assert "public symbols only" in result.stderr

    result = runner.invoke(main, ["dataflow", "lookup", "no_such", "--db", str(db)])
    assert result.exit_code == 1
    assert "Run `cairn dataflow build` first" in result.stderr


def test_dataflow_lookup_text_surfaces_ambiguity(tmp_path):
    db = _plain_store(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    for rid in ("r1", "r2"):
        conn.execute(
            "INSERT INTO repos (id, name, path, language) VALUES (?, ?, ?, 'python')",
            (rid, rid, f"/ws/{rid}"),
        )
        conn.execute(
            "INSERT INTO files (id, repo_id, path, language) VALUES (?, ?, 'x.py', 'python')",
            (f"f_{rid}", rid),
        )
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind) VALUES (?, ?, 'pub_x', 'function')",
            (f"s_{rid}", f"f_{rid}"),
        )
    conn.commit()
    build_dataflow_index(conn)
    conn.close()

    result = CliRunner().invoke(main, ["dataflow", "lookup", "pub_x", "--db", str(db)])
    assert result.exit_code == 0, result.output
    assert "public in multiple repos" in result.output


def test_dataflow_lookup_text_reports_analysis_errors(tmp_path, monkeypatch):
    db = _public_store(tmp_path)

    def _boom(conn, name, max_depth=5):
        raise RuntimeError("synthetic impact failure")

    monkeypatch.setattr("cairn.graph.queries.impact_analysis", _boom)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    build_dataflow_index(conn)
    conn.close()

    result = CliRunner().invoke(main, ["dataflow", "lookup", "pub_y", "--db", str(db)])
    assert result.exit_code == 0, result.output
    assert "Analysis errors:" in result.stderr
    assert "synthetic impact failure" in result.stderr
