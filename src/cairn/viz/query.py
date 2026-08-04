"""Visualization query layer: gather nodes/edges by scope.

Scopes: symbol, module, impact, repo, deps. Returns a uniform {nodes, edges,
metadata} dict consumed by the mermaid/dot/json generators.
"""
from __future__ import annotations

import sqlite3
from typing import Dict


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


def get_module_graph(conn: sqlite3.Connection, module: str) -> Dict:
    """All symbols in a module + their internal edges."""
    cur = conn.cursor()
    nodes = {}
    edges = []
    for r in cur.execute(
        "SELECT s.name, s.kind, f.path, f.repo_id FROM symbols s "
        "JOIN files f ON s.file_id = f.id WHERE f.path LIKE ? LIMIT 50",
        (f"%{module}%",),
    ).fetchall():
        _add_node(nodes, r["name"], r["kind"], r["path"], r["repo_id"])
    # Internal edges.
    if nodes:
        names = list(nodes.keys())
        placeholders = ",".join("?" * len(names))
        rows = cur.execute(
            f"SELECT s1.name AS src, COALESCE(s2.name, e.target_name) AS tgt, e.kind "
            f"FROM edges e JOIN symbols s1 ON e.source_id = s1.id "
            f"LEFT JOIN symbols s2 ON e.target_id = s2.id "
            f"WHERE s1.name IN ({placeholders}) LIMIT 100",
            names,
        ).fetchall()
        for r in rows:
            if r["tgt"] in nodes:
                edges.append({"source": r["src"], "target": r["tgt"], "kind": r["kind"]})
    return {"nodes": list(nodes.values()), "edges": edges,
            "metadata": {"scope": "module", "module": module, "node_count": len(nodes), "edge_count": len(edges)}}


# --- helpers -------------------------------------------------------------

def _add_node(store: dict, name: str, kind: str, file: str, repo: str):
    if name and name not in store:
        store[name] = {"id": name, "kind": kind, "file": file, "repo": repo}


def _empty(scope: str, name: str) -> Dict:
    return {"nodes": [], "edges": [], "metadata": {"scope": scope, "symbol": name, "node_count": 0, "edge_count": 0}}


def _parent(impact_result: dict, entry: dict) -> str:
    """Best-effort parent for an impact entry (used for edge target)."""
    # Simplification: link everything to the focal symbol; a fuller impl would
    # track the actual parent in the traversal.
    return entry.get("symbol", "")
