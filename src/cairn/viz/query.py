"""Visualization query layer: gather nodes/edges by scope.

Scopes: symbol, neighbors, module, impact, repo, deps. Returns a uniform
{nodes, edges, metadata} dict consumed by the mermaid/dot/json generators.
"""
from __future__ import annotations

import sqlite3
from typing import Dict, Sequence


def get_symbol_graph(conn: sqlite3.Connection, name: str, depth: int = 1) -> Dict:
    """A symbol + its immediate callers and callees."""
    cur = conn.cursor()
    nodes = {}
    edges = []

    # The focal symbol.
    focal = cur.execute(
        "SELECT s.name, s.kind, f.path, f.repo_id FROM symbols s "
        "JOIN files f ON s.file_id = f.id WHERE s.name = ? LIMIT 1",
        (name,),
    ).fetchone()
    if not focal:
        return _empty("symbol", name)
    _add_node(nodes, focal["name"], focal["kind"], focal["path"], focal["repo_id"])

    # Callers (1-hop).
    for r in cur.execute(
        "SELECT s.name AS caller, s.kind AS ckind, f.path, f.repo_id "
        "FROM edges e JOIN symbols s ON e.source_id = s.id "
        "JOIN files f ON s.file_id = f.id "
        "WHERE e.target_id IN (SELECT id FROM symbols WHERE name = ?) LIMIT 30",
        (name,),
    ).fetchall():
        _add_node(nodes, r["caller"], r["ckind"], r["path"], r["repo_id"])
        edges.append({"source": r["caller"], "target": name, "kind": "calls"})

    # Callees (1-hop).
    for r in cur.execute(
        "SELECT COALESCE(t.name, e.target_name) AS callee, t.kind, f.path, f.repo_id, "
        "e.target_id AS resolved FROM edges e JOIN symbols s ON e.source_id = s.id "
        "LEFT JOIN symbols t ON e.target_id = t.id "
        "LEFT JOIN files f ON t.file_id = f.id WHERE s.name = ? LIMIT 30",
        (name,),
    ).fetchall():
        if r["callee"]:
            _add_node(nodes, r["callee"], r["kind"] or "external", r["path"], r["repo_id"])
            edges.append({"source": name, "target": r["callee"], "kind": "calls"})

    return {"nodes": list(nodes.values()), "edges": edges,
            "metadata": {"scope": "symbol", "symbol": name, "node_count": len(nodes), "edge_count": len(edges)}}


# Per focal row per direction, mirroring get_symbol_graph's scope caps.
_NEIGHBOR_CAP = 30


def get_symbol_neighbors(conn: sqlite3.Connection,
                         names: Sequence[str],
                         depth: int = 1) -> Dict:
    """Each requested symbol (all its same-name rows) + 1-hop callers/callees.

    Generalizes get_symbol_graph's callers/callees blocks to a name set:
    every resolved same-name symbol row contributes its own neighborhood,
    capped at _NEIGHBOR_CAP per row per direction; metadata.truncated is
    True iff any direction hit its cap. ``depth`` is accepted but clamped
    to 1 — the signature is depth-ready (D-002), the behavior is 1-hop
    per action. Empty or blank ``names`` yield an empty graph, never an
    error; names with no symbol rows appear only in metadata.requested.
    """
    requested = list(dict.fromkeys(n for n in names if n and n.strip()))
    cur = conn.cursor()
    nodes = {}
    edges = []
    truncated = False
    if requested:
        placeholders = ",".join("?" * len(requested))
        focal_rows = cur.execute(
            f"SELECT s.id AS sid, s.name, s.kind, f.path, f.repo_id FROM symbols s "
            f"JOIN files f ON s.file_id = f.id WHERE s.name IN ({placeholders})",
            requested,
        ).fetchall()
        for focal in focal_rows:
            _add_node(nodes, focal["name"], focal["kind"], focal["path"], focal["repo_id"])
            # Callers (1-hop), keyed to this focal row; fetch cap+1 to flag truncation.
            callers = cur.execute(
                "SELECT s.name AS caller, s.kind AS ckind, f.path, f.repo_id "
                "FROM edges e JOIN symbols s ON e.source_id = s.id "
                "JOIN files f ON s.file_id = f.id "
                f"WHERE e.target_id = ? LIMIT {_NEIGHBOR_CAP + 1}",
                (focal["sid"],),
            ).fetchall()
            if len(callers) > _NEIGHBOR_CAP:
                truncated = True
                callers = callers[:_NEIGHBOR_CAP]
            for r in callers:
                _add_node(nodes, r["caller"], r["ckind"], r["path"], r["repo_id"])
                edges.append({"source": r["caller"], "target": focal["name"], "kind": "calls"})
            # Callees (1-hop); unresolved targets fall back to kind "external".
            callees = cur.execute(
                "SELECT COALESCE(t.name, e.target_name) AS callee, t.kind, f.path, f.repo_id "
                "FROM edges e LEFT JOIN symbols t ON e.target_id = t.id "
                "LEFT JOIN files f ON t.file_id = f.id "
                f"WHERE e.source_id = ? LIMIT {_NEIGHBOR_CAP + 1}",
                (focal["sid"],),
            ).fetchall()
            if len(callees) > _NEIGHBOR_CAP:
                truncated = True
                callees = callees[:_NEIGHBOR_CAP]
            for r in callees:
                if r["callee"]:
                    _add_node(nodes, r["callee"], r["kind"] or "external", r["path"], r["repo_id"])
                    edges.append({"source": focal["name"], "target": r["callee"], "kind": "calls"})
    return {"nodes": list(nodes.values()), "edges": edges,
            "metadata": {"scope": "neighbors", "requested": requested,
                         "node_count": len(nodes), "edge_count": len(edges),
                         "truncated": truncated}}


def get_impact_graph(conn: sqlite3.Connection, name: str, max_depth: int = 3) -> Dict:
    """Recursive caller tree for impact analysis."""
    from ..graph.queries import impact_analysis

    result = impact_analysis(conn, name, max_depth=max_depth)
    nodes = {}
    edges = []
    _add_node(nodes, name, "focus", "", "")
    for r in result["impacted"]:
        _add_node(nodes, r["symbol"], "caller", r["file"], r["repo"])
        edges.append({"source": r["symbol"], "target": name if r["depth"] == 0 else _parent(result, r), "kind": "calls"})
    return {"nodes": list(nodes.values()), "edges": edges,
            "metadata": {"scope": "impact", "symbol": name, "depth": max_depth,
                         "node_count": len(nodes), "edge_count": len(edges), "total": result["total"]}}


def get_deps_graph(conn: sqlite3.Connection) -> Dict:
    """Cross-repo dependency map."""
    from ..graph.queries import cross_repo_deps

    cur = conn.cursor()
    repos = [r["id"] for r in cur.execute("SELECT id FROM repos").fetchall()]
    nodes = {}
    edges = []
    for repo in repos:
        _add_node(nodes, repo, "repo", "", repo)
        deps = cross_repo_deps(conn, repo)
        for d in deps["dependencies"]:
            edges.append({"source": repo, "target": d["repo"], "kind": "depends", "label": d["evidence"]})
    return {"nodes": list(nodes.values()), "edges": edges,
            "metadata": {"scope": "deps", "node_count": len(nodes), "edge_count": len(edges)}}


def get_repo_graph(conn: sqlite3.Connection, repo: str, max_nodes: int = 30) -> Dict:
    """Module structure of a repo with symbol counts."""
    from ..graph.queries import group_by_top_level

    buckets = group_by_top_level(conn, repo)
    nodes = {}
    edges = []
    for key, count in buckets[:max_nodes]:
        label = f"{key} ({count})"
        _add_node(nodes, label, "module", "", repo)
    # No edges between modules at this granularity; show as a flat cluster.
    return {"nodes": list(nodes.values()), "edges": edges,
            "metadata": {"scope": "repo", "repo": repo, "node_count": len(nodes)}}


_MODULE_CAP = 50
_MODULE_EDGE_CAP = 100


def get_module_graph(
    conn: sqlite3.Connection, module: str = "", include_tests: bool = False
) -> Dict:
    """Symbols in a module + their internal edges, curated for usefulness.

    An empty ``module`` (the dashboard's default landing view) matches every
    path, so "first 50 by rowid" used to fill the canvas with an arbitrary
    sample — dominated by whichever files the indexer touched first (in
    practice, tests). Candidates are instead ranked by degree (fan-in +
    fan-out of any edges) then name, so the cap lands on the symbols that
    carry the module's structure. Tests are excluded by default (the
    ``is_test_symbol`` heuristics from the impact layer); ``include_tests``
    opts back in. ``metadata.tests_included`` and ``metadata.truncated``
    report both choices honestly.
    """
    from ..graph.tests import is_test_symbol

    cur = conn.cursor()
    nodes = {}
    edges = []
    rows = cur.execute(
        "SELECT s.name, s.kind, f.path, f.repo_id, s.qualified_name, "
        "(SELECT COUNT(*) FROM edges e WHERE e.target_id = s.id) + "
        "(SELECT COUNT(*) FROM edges e WHERE e.source_id = s.id) AS degree "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE f.path LIKE ? "
        "ORDER BY degree DESC, s.name ASC",
        (f"%{module}%",),
    ).fetchall()
    kept = 0
    eligible = 0
    for r in rows:
        if not include_tests and is_test_symbol(
            r["path"], r["name"], r["qualified_name"] or ""
        )["is_test"]:
            continue
        eligible += 1
        if eligible > _MODULE_CAP:
            continue
        _add_node(nodes, r["name"], r["kind"], r["path"], r["repo_id"])
        kept += 1
    # Internal edges.
    if nodes:
        names = list(nodes.keys())
        placeholders = ",".join("?" * len(names))
        rows = cur.execute(
            f"SELECT s1.name AS src, COALESCE(s2.name, e.target_name) AS tgt, e.kind "
            f"FROM edges e JOIN symbols s1 ON e.source_id = s1.id "
            f"LEFT JOIN symbols s2 ON e.target_id = s2.id "
            f"WHERE s1.name IN ({placeholders}) LIMIT {_MODULE_EDGE_CAP}",
            names,
        ).fetchall()
        for r in rows:
            if r["tgt"] in nodes:
                edges.append({"source": r["src"], "target": r["tgt"], "kind": r["kind"]})
    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "metadata": {
            "scope": "module",
            "module": module,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "tests_included": include_tests,
            "truncated": eligible > _MODULE_CAP,
        },
    }


# --- helpers -------------------------------------------------------------

def _add_node(store: dict, name: str, kind: str, file: str, repo: str):
    if name and name not in store:
        store[name] = {"id": name, "kind": kind, "file": file, "repo": repo}


def _empty(scope: str, name: str) -> Dict:
    return {"nodes": [], "edges": [], "metadata": {"scope": scope, "symbol": name, "node_count": 0, "edge_count": 0}}


def _parent(impact_result: dict, entry: dict) -> str:
    """Best-effort parent for an impact entry (used for edge target)."""
    return entry.get("symbol", "")
