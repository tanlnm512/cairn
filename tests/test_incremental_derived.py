"""Incremental derived-index maintenance must equal a full rebuild.

After any edit sequence through ``incremental_update``, both derived tables
must match a fresh ``build_graph`` + full derived build on the final tree --
exactly for the closure, and for dataflow modulo the DFS depth-frontier zone
(where either enumeration order is a valid builder output, since first-visit
depth depends on edge insertion order and a maintained DB's rowids legitimately
differ from a fresh rebuild's). The corpus is handcrafted and receiver-free:
incoming-edge repair resolves by bare name without receiver info, so
incremental parity of the edges table itself holds only for that shape.
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

from cairn.graph.builder import build_graph
from cairn.graph import dataflow as dataflow_mod
from cairn.graph.dataflow import (
    CLOSURE_MAX_DEPTH,
    build_dataflow_index,
    build_transitive_closure,
    maintain_dataflow_index,
    maintain_transitive_closure,
)
from cairn.graph.incremental import incremental_update
from cairn.graph.schema import _apply_schema, get_db


@pytest.fixture(autouse=True)
def _hash_embedder(hash_backend):
    """incremental_update re-embeds changed symbols; pin the dep-free hash
    embedder so runs are deterministic and need no model download."""


# 9 files. Call shapes: a 9-hop chain (apex -> ... -> base_a), fan-in into
# base_a/high_e, a same-file 2-cycle (cyc_x/cyc_y), an ambiguous duplicate
# (twin_dup defined in f07 AND f09; f06 calls it with no import -> stays
# unresolved-until-one-duplicate-disappears), and a call to a name with no
# definition anywhere (top_h -> nowhere_defined_fn, permanently unresolved).
DERIVED_SOURCES: dict[str, str] = {
    "f01.py": (
        "def base_a():\n"
        "    return 1\n"
        "\n"
        "\n"
        "def base_b():\n"
        "    return base_a()\n"
    ),
    "f02.py": (
        "from f01 import base_b\n"
        "\n"
        "\n"
        "def mid_c():\n"
        "    return base_b()\n"
        "\n"
        "\n"
        "def mid_d():\n"
        "    return mid_c() + 1\n"
    ),
    "f03.py": (
        "from f02 import mid_d\n"
        "\n"
        "\n"
        "def high_e():\n"
        "    return mid_d()\n"
        "\n"
        "\n"
        "def high_f():\n"
        "    return high_e() * 2\n"
        "\n"
        "\n"
        "class Engine:\n"
        "    def run(self):\n"
        "        return high_f()\n"
    ),
    "f04.py": (
        "from f03 import high_e\n"
        "from f09 import twin_dup\n"
        "\n"
        "\n"
        "def top_g():\n"
        "    return high_e() + twin_dup()\n"
        "\n"
        "\n"
        "def top_h():\n"
        "    return nowhere_defined_fn()\n"
    ),
    "f05.py": (
        "from f04 import top_g\n"
        "\n"
        "\n"
        "def caller_chain_1():\n"
        "    return top_g()\n"
        "\n"
        "\n"
        "def caller_chain_2():\n"
        "    return caller_chain_1()\n"
    ),
    "f06.py": (
        "from f05 import caller_chain_2\n"
        "\n"
        "\n"
        "def caller_chain_3():\n"
        "    caller_chain_2()\n"
        "    return dup_ambiguous_caller()\n"
        "\n"
        "\n"
        "def dup_ambiguous_caller():\n"
        "    # twin_dup with NO import: two definitions exist (f07, f09), so the\n"
        "    # resolver leaves this edge ambiguous/unresolved. Deleting one of\n"
        "    the duplicates must flip it to exact and ripple through the whole\n"
        "    caller chain's closure rows -- the classic affected-set hole.\n"
        "    return twin_dup()\n"
    ),
    "f07.py": (
        "def twin_dup():\n"
        "    return 20\n"
        "\n"
        "\n"
        "def cyc_x():\n"
        "    return cyc_y()\n"
        "\n"
        "\n"
        "def cyc_y():\n"
        "    return cyc_x()\n"
    ),
    "f08.py": (
        "from f06 import caller_chain_3\n"
        "\n"
        "\n"
        "def apex():\n"
        "    return caller_chain_3()\n"
    ),
    "f09.py": (
        "from f01 import base_a\n"
        "\n"
        "\n"
        "def twin_dup():\n"
        "    return base_a()\n"
    ),
}

BASE_FILES = sorted(DERIVED_SOURCES)


# ---------------------------------------------------------------------------
# Build / compare helpers
# ---------------------------------------------------------------------------


def _write(path, text: str, tick: float) -> None:
    """Write a file and pin its mtime to ``tick``.

    incremental_update's stat fallback compares stored mtime with a 0.5s
    tolerance; spacing every scripted write by 2s makes change detection
    deterministic instead of wall-clock-lucky.
    """
    path.write_text(text, encoding="utf-8")
    os.utime(path, (tick, tick))


def _build_full(repo_dir, db_path) -> None:
    """Full build INCLUDING both derived indexes (mirrors `cairn build`)."""
    build_graph(workspace=str(repo_dir), db_path=str(db_path))
    conn = get_db(str(db_path))
    try:
        build_dataflow_index(conn)
        build_transitive_closure(conn)
    finally:
        conn.close()


def _closure_rows(conn: sqlite3.Connection) -> list[tuple]:
    """Closure rows as stable label tuples (symbol ids differ across builds).

    (source name, source file, target_name, (target name, target file) | None,
    distance) -- the identity of a row a full rebuild would produce.
    """
    rows = conn.execute(
        """
        SELECT s.name AS src_name, f.path AS src_file,
               t.target_name AS tname,
               ts.name AS tgt_name, tf.path AS tgt_file,
               t.distance AS distance
        FROM transitive_edges t
        JOIN symbols s ON s.id = t.source_id
        JOIN files f ON s.file_id = f.id
        LEFT JOIN symbols ts ON ts.id = t.target_id
        LEFT JOIN files tf ON tf.id = ts.file_id
        """
    ).fetchall()
    out = []
    for r in rows:
        tgt = (r["tgt_name"], r["tgt_file"]) if r["tgt_name"] is not None else None
        out.append((r["src_name"], r["src_file"], r["tname"], tgt, r["distance"]))
    return sorted(out)


# impact_analysis (the dataflow payload source) runs at max_depth=5: a caller
# is listed iff its DFS first-visit depth is <= 5, and first-visit depth is
# enumeration-order dependent on diamond graphs. A maintained DB's edge rowids
# legitimately differ from a fresh rebuild's, so the dataflow arbiter compares
# only what ANY enumeration order must agree on: names whose longest simple
# path to the seed is short enough must be present in both, names whose
# shortest path is too long must be absent from both, and only the zone in
# between may differ.
_DFS_MAX_HOPS = 6  # max_depth=5 means paths of up to 6 caller->seed edges


def _distance_classes(conn: sqlite3.Connection, names: set[str]) -> dict[str, tuple[set, set]]:
    """Per row-name: (must_names, may_names) for within_repo comparisons.

    must_names: caller names connected to the seed by <= _DFS_MAX_HOPS edges
    on EVERY simple path (any DFS order emits them). may_names: caller names
    connected on SOME path of <= _DFS_MAX_HOPS edges (some DFS order can emit
    them). Everything else must never appear.
    """
    from cairn.graph.traversal import STRUCTURAL_EDGE_KINDS

    sym_rows = conn.execute("SELECT id, name FROM symbols").fetchall()
    id2name = {r["id"]: r["name"] for r in sym_rows}
    kind_ph = ",".join("?" for _ in STRUCTURAL_EDGE_KINDS)
    edge_rows = conn.execute(
        f"SELECT source_id, target_id FROM edges "
        f"WHERE target_id IS NOT NULL AND kind IN ({kind_ph})",
        tuple(STRUCTURAL_EDGE_KINDS),
    ).fetchall()
    callees: dict[str, list[str]] = {}
    for r in edge_rows:
        callees.setdefault(r["source_id"], []).append(r["target_id"])

    out: dict[str, tuple[set, set]] = {}
    for name in sorted(names):
        seeds = {sid for sid, n in id2name.items() if n == name}
        must: set[str] = set()
        may: set[str] = set()
        for sid, n in id2name.items():
            if n == name:
                continue  # within_repo excludes the row's own name

            def _longest(cur: str, on_path: frozenset) -> int:
                # Longest simple path cur -> any seed (-1 if none) over
                # resolved structural edges. Cycles are cut by on_path; the
                # corpus is tiny so the recursion is cheap. NB: a dead-end
                # recursion returns -1 and must NOT be folded in (1 + -1 = 0
                # would fabricate a zero-length "path").
                best = -1
                for nxt in callees.get(cur, ()):
                    if nxt in seeds:
                        best = max(best, 1)
                    elif nxt not in on_path:
                        sub = _longest(nxt, on_path | {nxt})
                        if sub >= 0:
                            best = max(best, 1 + sub)
                return best

            longest = _longest(sid, frozenset({sid}))
            if longest < 0:
                continue  # cannot reach any seed: must never be listed

            # Shortest edge-count to any seed via plain BFS (the emission
            # depth of a caller with path length k is k-1, hence the <=6
            # threshold against impact_analysis's max_depth=5).
            shortest = None
            frontier, seen, dist = [sid], {sid}, 0
            while frontier and shortest is None:
                nxt = []
                for cur in frontier:
                    for t in callees.get(cur, ()):
                        if t in seeds:
                            shortest = dist + 1
                            break
                        if t not in seen:
                            seen.add(t)
                            nxt.append(t)
                    if shortest is not None:
                        break
                frontier = nxt
                dist += 1

            if longest <= _DFS_MAX_HOPS:
                must.add(n)
            if shortest is not None and shortest <= _DFS_MAX_HOPS:
                may.add(n)
        out[name] = (must, may)
    return out


def _assert_dataflow_frontier_parity(conn_m, conn_f, ctx: str) -> None:
    """Compare maintained vs fresh dataflow modulo DFS enumeration order.

    Row keys and repos must match exactly (that catches affected-set holes in
    which rows are created/deleted). within_repo lists must agree on every
    name outside the order-dependent frontier zone (see _DFS_MAX_HOPS).
    cross_repo must match exactly (name-set derived, order-free).
    """
    def _raw(conn):
        rows = conn.execute("SELECT symbol, repo, within_repo, cross_repo FROM dataflow").fetchall()
        return {
            r["symbol"]: (r["repo"], set(json.loads(r["within_repo"] or "[]")),
                          tuple(sorted(json.loads(r["cross_repo"] or "[]"))))
            for r in rows
        }

    m, f = _raw(conn_m), _raw(conn_f)
    assert set(m) == set(f), (
        f"[{ctx}] dataflow row keys diverge: only-in-maintained={sorted(set(m) - set(f))} "
        f"only-in-fresh={sorted(set(f) - set(m))}"
    )
    classes = _distance_classes(conn_m, set(m))
    for name in sorted(m):
        repo_m, within_m, cross_m = m[name]
        repo_f, within_f, cross_f = f[name]
        assert repo_m == repo_f, f"[{ctx}] dataflow repo mismatch for {name}"
        assert cross_m == cross_f, f"[{ctx}] dataflow cross_repo mismatch for {name}"
        must, may = classes[name]
        assert must <= within_m and must <= within_f, (
            f"[{ctx}] dataflow({name}) missing a must-present caller "
            f"(affected-set hole or lost row): missing-in-maintained={sorted(must - within_m)} "
            f"missing-in-fresh={sorted(must - within_f)}"
        )
        assert within_m <= may and within_f <= may, (
            f"[{ctx}] dataflow({name}) lists a caller that cannot reach it "
            f"(stale row): extra-in-maintained={sorted(within_m - may)} "
            f"extra-in-fresh={sorted(within_f - may)}"
        )


def _assert_derived_parity(db_maintained: str, db_fresh: str, ctx: str) -> None:
    a, b = get_db(db_maintained), get_db(db_fresh)
    try:
        rows_a, rows_b = _closure_rows(a), _closure_rows(b)
        _assert_dataflow_frontier_parity(a, b, ctx)
    finally:
        a.close()
        b.close()

    def _first_diffs(x, y, n=12):
        missing = [r for r in y if r not in set(x)][:n]
        extra = [r for r in x if r not in set(y)][:n]
        return f"missing-from-maintained={missing} extra-in-maintained={extra}"

    assert rows_a == rows_b, (
        f"[{ctx}] transitive_edges diverge from a full rebuild.\n"
        f"{_first_diffs(rows_a, rows_b)}"
    )


def _run_update(repo_dir, db_path, ctx: str) -> dict:
    result = incremental_update(workspace=str(repo_dir), db_path=str(db_path))
    assert result["errors"] == [], f"[{ctx}] incremental_update errors: {result['errors']}"
    return result


def _make_corpus(root) -> tuple:
    """Materialize DERIVED_SOURCES under ``root/<repo>`` with a .git marker."""
    repo = root / "derrepo"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()
    tick = time.time() + 86400
    for i, fname in enumerate(BASE_FILES):
        _write(repo / fname, DERIVED_SOURCES[fname], tick + 2 * i)
    return repo, tick + 2 * len(BASE_FILES)


# ---------------------------------------------------------------------------
# Deterministic scenario tests (one edit each, full parity check)
# ---------------------------------------------------------------------------


def _scenario(tmp_path, mutate, ctx):
    """Build the corpus, apply one mutation, incremental_update, compare to a
    fresh full rebuild of the final tree."""
    repo, next_tick = _make_corpus(tmp_path)
    db = str(tmp_path / "main.kg")
    _build_full(repo, db)

    next_tick = mutate(repo, next_tick)
    res = _run_update(repo, db, ctx)
    assert res["files_reindexed"] + res["files_deleted"] >= 1, f"[{ctx}] change not detected"

    fresh_root = tmp_path / "fresh"
    fresh_root.mkdir()
    shutil.copytree(repo, fresh_root / repo.name)
    fresh_db = str(tmp_path / "fresh.kg")
    _build_full(fresh_root / repo.name, fresh_db)

    _assert_derived_parity(db, fresh_db, ctx)


def test_body_edit_parity(tmp_path):
    """A body-only edit (no symbol changes) still round-trips identically."""

    def mutate(repo, tick):
        _write(repo / "f03.py", DERIVED_SOURCES["f03.py"] + "\n# tweak\n", tick)
        return tick + 2

    _scenario(tmp_path, mutate, "body-edit")


def test_add_file_parity(tmp_path):
    """A new file importing an existing symbol gains correct closure+dataflow
    rows for itself AND for the symbols it reaches."""

    def mutate(repo, tick):
        _write(
            repo / "extra_1.py",
            "from f02 import mid_c\n\n\ndef extra_fn_1():\n    return mid_c() + 7\n",
            tick,
        )
        return tick + 2

    _scenario(tmp_path, mutate, "add-file")


def test_delete_file_flips_ambiguity_parity(tmp_path):
    """Deleting one of two same-named symbols re-resolves an ambiguous edge
    elsewhere; the whole caller chain's closure rows and the reached symbols'
    dataflow rows must ripple. This is the incoming-repair affected-set hole."""

    def mutate(repo, tick):
        (repo / "f07.py").unlink()
        return tick + 2

    _scenario(tmp_path, mutate, "delete-f07-ambiguity-flip")


def test_rename_across_callers_parity(tmp_path):
    """Renaming base_b (defined in f01, imported+called in f02) must update
    every dataflow row that lists the old or new name, and the closure rows of
    mid_c (whose edge was retargeted)."""

    def mutate(repo, tick):
        f01 = re.sub(r"\bbase_b\b", "renamed_1", DERIVED_SOURCES["f01.py"])
        f02 = re.sub(r"\bbase_b\b", "renamed_1", DERIVED_SOURCES["f02.py"])
        _write(repo / "f01.py", f01, tick)
        _write(repo / "f02.py", f02, tick + 2)
        return tick + 4

    _scenario(tmp_path, mutate, "rename-across-callers")


def test_delete_file_with_importers_parity(tmp_path):
    """Deleting f06 breaks f08's import: apex's edge goes unresolved on both
    sides, and every stale closure/dataflow row referencing f06's symbols must
    disappear."""

    def mutate(repo, tick):
        (repo / "f06.py").unlink()
        return tick + 2

    _scenario(tmp_path, mutate, "delete-f06-with-importer")


def test_symbol_added_to_existing_file_parity(tmp_path):
    """Appending a new function (with an unresolved call) to an existing file
    seeds fresh closure rows for a new symbol."""

    def mutate(repo, tick):
        _write(
            repo / "f04.py",
            DERIVED_SOURCES["f04.py"] + "\n\ndef spawned_1():\n    return nowhere_defined_fn() + 3\n",
            tick,
        )
        return tick + 2

    _scenario(tmp_path, mutate, "spawn-in-file")


# ---------------------------------------------------------------------------
# Property sweep: random sequences of edits, parity after the whole sequence
# ---------------------------------------------------------------------------


# Functions that can be renamed by the scripted editor: (key, files-to-rewrite,
# initial name). The current name is tracked per sequence.
_RENAME_PLAN = [
    ("base_b", ("f01.py", "f02.py")),
    ("high_f", ("f03.py",)),
    ("caller_chain_1", ("f05.py",)),
    ("mid_d", ("f02.py", "f03.py")),
]


def _run_random_sequence(repo, db, rng, seq_len: int, ctx: str) -> None:
    tick = time.time() + 86400 + rng.randrange(1000) * 2
    extras: list[str] = []
    deleted: set[str] = set()
    current_names = {key: key for key, _files in _RENAME_PLAN}
    rename_idx = 0

    for step in range(seq_len):
        op = rng.choices(
            ["body", "spawn", "add", "del", "rename"],
            weights=[3, 2, 2, 2, 1],
            k=1,
        )[0]

        if op == "body":
            candidates = [f for f in BASE_FILES if f not in deleted]
            fname = rng.choice(candidates)
            path = repo / fname
            _write(path, path.read_text(encoding="utf-8") + f"\n# tweak {step}\n", tick)

        elif op == "spawn":
            candidates = [f for f in BASE_FILES if f not in deleted]
            fname = rng.choice(candidates)
            path = repo / fname
            _write(
                path,
                path.read_text(encoding="utf-8")
                + f"\n\ndef spawned_{step}():\n    return nowhere_defined_fn() + {step}\n",
                tick,
            )

        elif op == "add":
            fname = f"extra_{step}.py"
            _write(
                repo / fname,
                f"from f02 import mid_c\n\n\ndef extra_fn_{step}():\n    return mid_c() + {step}\n",
                tick,
            )
            extras.append(fname)

        elif op == "del":
            # Extras first, then base files whose deletion exercises importer
            # breakage and the ambiguity flip. f01/f02 are never deleted so
            # the rename plan's definitions stay valid.
            pool = extras[:] + [f for f in ("f07.py", "f06.py", "f08.py") if f not in deleted]
            if not pool:
                continue
            fname = rng.choice(pool)
            (repo / fname).unlink()
            deleted.add(fname)
            if fname in extras:
                extras.remove(fname)

        elif op == "rename":
            key, files = _RENAME_PLAN[rename_idx % len(_RENAME_PLAN)]
            rename_idx += 1
            cur = current_names[key]
            new = f"renamed_{step}"
            live = [f for f in files if f not in deleted and (repo / f).exists()]
            # Only rename while the defining file survives; skipped otherwise.
            if live and all(re.search(rf"\b{re.escape(cur)}\b", (repo / f).read_text(encoding="utf-8")) for f in live):
                for f in live:
                    p = repo / f
                    _write(p, re.sub(rf"\b{re.escape(cur)}\b", new, p.read_text(encoding="utf-8")), tick)
                current_names[key] = new

        tick += 2
        _run_update(repo, db, f"{ctx} step={step} op={op}")


@pytest.mark.parametrize("seed", range(50))
def test_property_parity_random_sequences(tmp_path, seed):
    """The arbiter (P3.3): 50 seeded random edit sequences, an
    incremental_update after every edit, then a row-for-row diff of both
    derived tables against a full rebuild of the final tree."""
    repo, _tick = _make_corpus(tmp_path)
    db = str(tmp_path / "main.kg")
    _build_full(repo, db)

    rng = random.Random(seed)
    _run_random_sequence(repo, db, rng, seq_len=6, ctx=f"seed={seed}")

    fresh_root = tmp_path / "fresh"
    fresh_root.mkdir()
    shutil.copytree(repo, fresh_root / repo.name)
    fresh_db = str(tmp_path / "fresh.kg")
    _build_full(fresh_root / repo.name, fresh_db)

    _assert_derived_parity(db, fresh_db, f"seed={seed}")


# ---------------------------------------------------------------------------
# Dispatch behavior: maintenance, not wipe+rebuild; full-rebuild fallback
# ---------------------------------------------------------------------------


def test_update_uses_incremental_maintenance_not_full_rebuild(tmp_path, monkeypatch):
    """A normal update must go through the maintain_* functions; the full
    build functions must NOT run (they would wipe and rebuild everything)."""
    from cairn.graph import dataflow as dataflow_mod

    repo, _tick = _make_corpus(tmp_path)
    db = str(tmp_path / "main.kg")
    _build_full(repo, db)

    called = {"closure": 0, "dataflow": 0}

    def _spy_closure(conn, *a, **k):
        called["closure"] += 1
        return build_transitive_closure(conn, *a, **k)

    def _spy_dataflow(conn, *a, **k):
        called["dataflow"] += 1
        return build_dataflow_index(conn, *a, **k)

    monkeypatch.setattr(dataflow_mod, "build_transitive_closure", _spy_closure)
    monkeypatch.setattr(dataflow_mod, "build_dataflow_index", _spy_dataflow)

    _write(repo / "f03.py", DERIVED_SOURCES["f03.py"] + "\n# tweak\n", time.time() + 86400)
    _run_update(repo, db, "maintenance-dispatch")

    assert called == {"closure": 0, "dataflow": 0}, (
        f"incremental_update took the FULL rebuild path: {called}"
    )


def test_update_falls_back_to_full_rebuild_when_derived_never_built(tmp_path, monkeypatch):
    """An empty dataflow table has no pre-state to compute an affected set
    from, so the update must fall back to the full rebuild -- and a store
    with a closure keeps its closure leg in that fallback."""
    from cairn.graph import dataflow as dataflow_mod

    repo, _tick = _make_corpus(tmp_path)
    db = str(tmp_path / "main.kg")
    _build_full(repo, db)

    conn = get_db(db)
    try:
        conn.execute("DELETE FROM dataflow")
        conn.commit()
    finally:
        conn.close()

    called = {"closure": 0, "dataflow": 0}

    def _spy_closure(conn, *a, **k):
        called["closure"] += 1
        return build_transitive_closure(conn, *a, **k)

    def _spy_dataflow(conn, *a, **k):
        called["dataflow"] += 1
        return build_dataflow_index(conn, *a, **k)

    monkeypatch.setattr(dataflow_mod, "build_transitive_closure", _spy_closure)
    monkeypatch.setattr(dataflow_mod, "build_dataflow_index", _spy_dataflow)

    _write(repo / "f03.py", DERIVED_SOURCES["f03.py"] + "\n# tweak\n", time.time() + 86400)
    _run_update(repo, db, "fallback-dispatch")

    assert called == {"closure": 1, "dataflow": 1}, (
        f"expected the full-rebuild fallback, got {called}"
    )
    conn = get_db(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM transitive_edges").fetchone()[0] > 0
        assert conn.execute("SELECT COUNT(*) FROM dataflow").fetchone()[0] > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Unit-level tests of the maintain_* functions themselves
# ---------------------------------------------------------------------------


def _seed_mini_graph(conn):
    """files/symbols/edges: s1->s2->s3->s4, s5->s1, s2->s5 (a small cycle-free
    web plus one extra caller). All edges resolved, kind 'calls'."""
    conn.execute(
        "INSERT INTO repos (id, name, path, language) VALUES ('r1', 'r1', '/tmp/r1', 'python')"
    )
    conn.executemany(
        "INSERT INTO files (id, repo_id, path, language) VALUES (?, 'r1', ?, 'python')",
        [("fa", "a.py"), ("fb", "b.py")],
    )
    conn.executemany(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind) VALUES (?,?,?,?,?)",
        [
            ("s1", "fa", "sym_one", "sym_one", "function"),
            ("s2", "fa", "sym_two", "sym_two", "function"),
            ("s3", "fb", "sym_three", "sym_three", "function"),
            ("s4", "fb", "sym_four", "sym_four", "function"),
            ("s5", "fb", "sym_five", "sym_five", "function"),
        ],
    )
    conn.executemany(
        "INSERT INTO edges (id, source_id, target_id, kind, resolution) VALUES (?,?,?,?, 'exact')",
        [
            ("e1", "s1", "s2", "calls"),
            ("e2", "s2", "s3", "calls"),
            ("e3", "s3", "s4", "calls"),
            ("e4", "s5", "s1", "calls"),
            ("e5", "s2", "s5", "calls"),
        ],
    )
    conn.commit()


def test_maintain_transitive_closure_matches_full_rebuild(fresh_db):
    """After mutating edges, maintaining just the affected sources yields the
    same rows as wiping and running the full builder on the same graph."""
    _seed_mini_graph(fresh_db)
    build_transitive_closure(fresh_db)

    # Mutation: s2 stops calling s3 and now calls s4 directly.
    fresh_db.execute("DELETE FROM edges WHERE id = 'e2'")
    fresh_db.execute(
        "INSERT INTO edges (id, source_id, target_id, kind, resolution) "
        "VALUES ('e6', 's2', 's4', 'calls', 'exact')"
    )
    fresh_db.commit()

    # Affected set as _maintain_derived_indexes would compute it: s2 (changed
    # edges), s1/s5 (they reach s2), and nothing else needs re-derivation.
    maintain_transitive_closure(fresh_db, {"s1", "s2", "s5"})
    maintained = _closure_rows(fresh_db)

    # Fresh full rebuild of the same mutated graph.
    ref = sqlite3.connect(":memory:")
    ref.row_factory = sqlite3.Row
    _apply_schema(ref)
    _seed_mini_graph(ref)
    ref.execute("DELETE FROM edges WHERE id = 'e2'")
    ref.execute(
        "INSERT INTO edges (id, source_id, target_id, kind, resolution) "
        "VALUES ('e6', 's2', 's4', 'calls', 'exact')"
    )
    ref.commit()
    build_transitive_closure(ref)
    expected = _closure_rows(ref)
    ref.close()

    assert maintained == expected


def _seed_random_closure_graph(conn, rng):
    """A randomized web exercising the closure's tie-break inputs: names with
    global counts 1/2/3, every structural kind plus a noise kind, resolved /
    dangling / name-only / name+id / NULL target shapes, cycles included."""
    from cairn.graph.traversal import STRUCTURAL_EDGE_KINDS

    pool = (
        [f"uniq_{i}" for i in range(12)]
        + [f"twin_{i}" for i in range(6)] * 2
        + [f"tri_{i}" for i in range(4)] * 3
    )
    conn.execute(
        "INSERT INTO repos (id, name, path, language) VALUES ('r1', 'r1', '/tmp/r1', 'python')"
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES ('fa', 'r1', 'a.py', 'python')"
    )
    n_symbols = 36
    ids = [f"sym_{i}" for i in range(n_symbols)]
    names = [pool[i % len(pool)] for i in range(n_symbols)]
    rng.shuffle(names)
    conn.executemany(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind) "
        "VALUES (?, 'fa', ?, ?, 'function')",
        [(sid, name, name) for sid, name in zip(ids, names)],
    )
    kinds = sorted(STRUCTURAL_EDGE_KINDS) + ["http_call"]
    rows = []
    for i in range(90):
        shape = rng.random()
        if shape < 0.5:  # resolved id (real resolved edges carry no target_name)
            tid, tname = rng.choice(ids), None
        elif shape < 0.65:  # dangling id with a name fallback (never NULL+NULL)
            tid, tname = f"ghost_{rng.randrange(5)}", rng.choice(pool)
        elif shape < 0.85:  # name-only
            tid, tname = None, rng.choice(pool)
        elif shape < 0.95:  # resolvable id with a competing target_name
            tid, tname = rng.choice(ids), rng.choice(pool)
        else:  # NULL/NULL
            tid, tname = None, None
        rows.append((f"edge_{i}", rng.choice(ids), tid, tname, rng.choice(kinds)))
    conn.executemany(
        "INSERT INTO edges (id, source_id, target_id, target_name, kind) "
        "VALUES (?,?,?,?,?)",
        rows,
    )
    conn.commit()


def _clear_graph_tables(conn):
    for table in ("edges", "symbols", "files", "repos"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()


def test_scoped_closure_rows_match_full_rows_exactly(fresh_db):
    """_closure_rows with restrict_sources must equal the full row list
    filtered to those sources -- exact list parity (order included) over
    randomized graphs, the contract the scoped read path must never break."""
    for seed in range(20):
        rng = random.Random(seed)
        _clear_graph_tables(fresh_db)
        _seed_random_closure_graph(fresh_db, rng)
        full = dataflow_mod._closure_rows(fresh_db, CLOSURE_MAX_DEPTH)
        sources = sorted({r[0] for r in full})
        assert sources, f"seed {seed}: no closure rows"
        halves = [
            sources[:1],
            sources,
            rng.sample(sources, max(1, len(sources) // 2)),
            sources[: max(1, len(sources) // 3)],
            ["ghost_id"],
        ]
        for restrict in halves:
            scoped = dataflow_mod._closure_rows(
                fresh_db, CLOSURE_MAX_DEPTH, restrict_sources=restrict
            )
            expected = [r for r in full if r[0] in set(restrict)]
            assert scoped == expected, (
                f"seed {seed}: restrict sample starting {restrict[:3]} diverged"
            )


def test_maintain_after_targeted_delete_matches_full_rebuild(fresh_db):
    """After a targeted DELETE of a mid-chain symbol, maintaining only the
    deleted source plus its ancestors must leave exactly the rows a fresh full
    rebuild produces -- whole-table, so unaffected sources survive untouched."""
    _seed_mini_graph(fresh_db)
    build_transitive_closure(fresh_db)

    fresh_db.execute("DELETE FROM edges WHERE source_id = 's3' OR target_id = 's3'")
    fresh_db.execute("DELETE FROM symbols WHERE id = 's3'")
    fresh_db.commit()
    maintain_transitive_closure(fresh_db, {"s1", "s2", "s3", "s5"})

    ref = sqlite3.connect(":memory:")
    ref.row_factory = sqlite3.Row
    _apply_schema(ref)
    _seed_mini_graph(ref)
    ref.execute("DELETE FROM edges WHERE source_id = 's3' OR target_id = 's3'")
    ref.execute("DELETE FROM symbols WHERE id = 's3'")
    ref.commit()
    build_transitive_closure(ref)
    expected = _closure_rows(ref)
    ref.close()

    assert _closure_rows(fresh_db) == expected


def test_maintain_dataflow_index_upserts_and_deletes(fresh_db):
    """Affected names with a remaining public symbol are recomputed; names
    with none left lose their row (a full rebuild would not write one)."""
    _seed_mini_graph(fresh_db)

    # Pre-existing rows: one for a symbol that still exists, one for a name
    # that has no symbol anymore.
    fresh_db.execute(
        "INSERT INTO dataflow (symbol, repo, within_repo, cross_repo, updated) "
        "VALUES ('sym_one', 'r1', '[]', '[]', 0)"
    )
    fresh_db.execute(
        "INSERT INTO dataflow (symbol, repo, within_repo, cross_repo, updated) "
        "VALUES ('gone_name', 'r1', '[]', '[]', 0)"
    )
    fresh_db.commit()

    n = maintain_dataflow_index(fresh_db, {"sym_one", "gone_name"})

    row = fresh_db.execute(
        "SELECT within_repo FROM dataflow WHERE symbol = 'sym_one'"
    ).fetchone()
    # sym_one's callers: s5 (direct) and s2 (s2 -> s5 -> s1, within depth 5).
    assert row is not None
    assert sorted(json.loads(row["within_repo"])) == ["sym_five", "sym_two"]
    gone = fresh_db.execute(
        "SELECT COUNT(*) FROM dataflow WHERE symbol = 'gone_name'"
    ).fetchone()[0]
    assert gone == 0
    assert n >= 1


def test_maintain_dataflow_index_deletes_row_when_symbol_becomes_private(fresh_db):
    """A rename to a _private name makes the symbol non-public; the stale row
    must go, exactly like a full build would omit it."""
    _seed_mini_graph(fresh_db)
    fresh_db.execute(
        "INSERT INTO dataflow (symbol, repo, within_repo, cross_repo, updated) "
        "VALUES ('sym_two', 'r1', '[]', '[]', 0)"
    )
    fresh_db.execute("UPDATE symbols SET name = '_sym_two' WHERE id = 's2'")
    fresh_db.commit()

    maintain_dataflow_index(fresh_db, {"sym_two"})

    assert fresh_db.execute(
        "SELECT COUNT(*) FROM dataflow WHERE symbol = 'sym_two'"
    ).fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Range-scoped updates (FR-001): --diff-ref A..B through incremental_update
# ---------------------------------------------------------------------------


def _git(args, cwd: Path) -> None:
    """Run a git command in ``cwd`` with a pinned hermetic committer identity."""
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, env=env,
        capture_output=True, text=True,
    )


def _committed_repo(root) -> Path:
    """A real git repo under ``root`` whose first commit holds a.py + b.py."""
    repo = root / "gitrepo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    (repo / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (repo / "b.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
    _git(["add", "."], repo)
    _git(["commit", "-qm", "first"], repo)
    return repo


def _indexed_paths(db_path: str) -> set[str]:
    conn = get_db(db_path)
    try:
        return {r["path"] for r in conn.execute("SELECT path FROM files")}
    finally:
        conn.close()


def test_range_update_reindexes_exactly_the_commit_range(tmp_path):
    """diff_ref scopes detection to the git range: exactly the committed
    files reindex even though the worktree holds other noise (uncommitted
    edit + untracked file stay out)."""
    repo = _committed_repo(tmp_path)
    db = str(tmp_path / "range.kg")
    build_graph(workspace=str(repo), db_path=db)

    (repo / "a.py").write_text("def alpha():\n    return 11\n", encoding="utf-8")
    (repo / "c.py").write_text("def gamma():\n    return 3\n", encoding="utf-8")
    _git(["add", "."], repo)
    _git(["commit", "-qm", "second"], repo)
    # Worktree noise the range must ignore.
    (repo / "b.py").write_text("def beta():\n    return 22\n", encoding="utf-8")
    (repo / "d.py").write_text("def delta():\n    return 4\n", encoding="utf-8")

    res = incremental_update(workspace=str(repo), db_path=db, diff_ref="HEAD~1..HEAD")

    assert res["errors"] == [], res["errors"]
    assert res["files_reindexed"] == 2, res
    paths = _indexed_paths(db)
    assert "b.py" in paths and "d.py" not in paths
    conn = get_db(db)
    try:
        assert conn.execute("SELECT 1 FROM symbols WHERE name = 'gamma'").fetchone()
    finally:
        conn.close()


def test_fresh_store_range_update_indexes_all_files(tmp_path):
    """A never-built store's first ranged update indexes every committed file
    including the first: nothing drops into parse_errors on a missing repos
    row."""
    repo = _committed_repo(tmp_path)
    db = str(tmp_path / "fresh.kg")
    empty_tree = subprocess.run(
        ["git", "hash-object", "-t", "tree", "/dev/null"],
        cwd=str(repo), capture_output=True, text=True, check=True,
    ).stdout.strip()

    res = incremental_update(workspace=str(repo), db_path=db, diff_ref=f"{empty_tree}..HEAD")

    assert res["errors"] == [], res["errors"]
    assert res["files_reindexed"] == 2, res
    assert _indexed_paths(db) == {"a.py", "b.py"}
    conn = get_db(db)
    try:
        assert conn.execute("SELECT 1 FROM parse_errors").fetchone() is None
        assert conn.execute("SELECT COUNT(*) AS c FROM symbols").fetchone()["c"] > 0
    finally:
        conn.close()


def test_flag_absent_update_keeps_worktree_and_untracked_detection(tmp_path):
    """No diff_ref: today's signal -- worktree diff vs HEAD plus untracked
    files -- is unchanged."""
    repo = _committed_repo(tmp_path)
    db = str(tmp_path / "wt.kg")
    build_graph(workspace=str(repo), db_path=db)

    (repo / "a.py").write_text("def alpha():\n    return 22\n", encoding="utf-8")
    (repo / "e.py").write_text("def epsilon():\n    return 5\n", encoding="utf-8")

    res = incremental_update(workspace=str(repo), db_path=db)

    assert res["errors"] == [], res["errors"]
    assert res["files_reindexed"] == 2, res
    assert "e.py" in _indexed_paths(db)


def test_range_mode_git_failure_returns_empty_not_stat_fallback(tmp_path):
    """A failed range diff returns [] rather than falling through to the stat
    fallback: off-range worktree noise must not widen the span."""
    from cairn.graph.incremental import _changed_source_files

    repo = _committed_repo(tmp_path)
    db = str(tmp_path / "badrange.kg")
    build_graph(workspace=str(repo), db_path=db)
    (repo / "a.py").write_text("def alpha():\n    return 33\n", encoding="utf-8")

    conn = get_db(db)
    try:
        changed = _changed_source_files(repo, conn=conn, diff_ref="no-such-ref..HEAD")
    finally:
        conn.close()

    assert changed == []


def test_cli_update_rejects_unresolvable_diff_ref_endpoint(tmp_path):
    """An unresolvable --diff-ref endpoint is a clean CLI error before any
    scan -- no traceback, no silent zero-file update."""
    from click.testing import CliRunner

    repo = _committed_repo(tmp_path)

    from cairn.cli.main import main

    result = CliRunner().invoke(
        main,
        ["update", "--workspace", str(repo), "--db", str(tmp_path / "bad.kg"),
         "--diff-ref", "no-such-ref..HEAD"],
        catch_exceptions=False,
    )

    assert result.exit_code == 1, result.output
    assert "no-such-ref" in result.output
    assert "Traceback" not in result.output


def test_cli_update_forwards_diff_ref_to_incremental_update(tmp_path, monkeypatch):
    """A valid --diff-ref reaches incremental_update as the diff_ref keyword."""
    from click.testing import CliRunner

    import cairn.graph.incremental as incremental_mod

    repo = _committed_repo(tmp_path)
    (repo / "a.py").write_text("def alpha():\n    return 2\n", encoding="utf-8")
    _git(["add", "."], repo)
    _git(["commit", "-qm", "second"], repo)

    captured: dict = {}

    def _spy(**kwargs):
        captured.update(kwargs)
        return {"repos_scanned": 1, "files_reindexed": 0, "files_deleted": 0,
                "errors": [], "deferred_embeds": 0}

    monkeypatch.setattr(incremental_mod, "incremental_update", _spy)

    from cairn.cli.main import main

    result = CliRunner().invoke(
        main,
        ["update", "--workspace", str(repo), "--db", str(tmp_path / "cli.kg"),
         "--diff-ref", "HEAD~1..HEAD"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert captured.get("diff_ref") == "HEAD~1..HEAD", captured
