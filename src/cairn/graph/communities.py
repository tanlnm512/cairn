"""Louvain subsystem partition writer over structural edges (communities tables)."""
from __future__ import annotations

import sqlite3
from typing import Callable, Dict, List, Optional, Tuple

from cairn.graph.repo_map import _dominant_segment
from cairn.graph.schema import get_db
from cairn.graph.traversal import STRUCTURAL_EDGE_KINDS

LOUVAIN_SEED = 0

DEFAULT_TOP_K = 10

# Weights must cover every STRUCTURAL_EDGE_KINDS entry; a kind added there
# without a weight here fails loudly instead of clustering unweighted.
STRUCTURAL_EDGE_WEIGHTS: Dict[str, float] = {
    "calls": 1.0,
    "call": 1.0,
    "extends": 2.0,
    "implements": 2.0,
}

_HUB_LABEL_PREFIX = "hub:"

_KIND_PLACEHOLDERS = ",".join("?" for _ in STRUCTURAL_EDGE_KINDS)


def probe_networkx() -> Callable:
    """Return ``louvain_communities``; ImportError with install hint when networkx is absent (call before any writable store open)."""
    try:
        from networkx.algorithms.community import louvain_communities
    except ImportError as exc:
        raise ImportError(
            "community detection needs the optional 'networkx' package; "
            "install it with the graph-analytics extra: "
            "pip install 'cairn[graph-analytics]'"
        ) from exc
    return louvain_communities


def refresh_communities(db_path: Optional[str] = None) -> int:
    """Probe, then open the store writable and full-refresh the communities tables; returns the community count."""
    probe_networkx()
    conn = get_db(db_path)
    try:
        return compute_communities(conn)
    finally:
        conn.close()


def compute_communities(conn: sqlite3.Connection) -> int:
    """Cluster structural edges with seeded Louvain and full-refresh both communities tables; returns the community count."""
    louvain_communities = probe_networkx()
    edge_rows = conn.execute(
        f"SELECT source_id, target_id, kind FROM edges "
        f"WHERE kind IN ({_KIND_PLACEHOLDERS})",
        STRUCTURAL_EDGE_KINDS,
    ).fetchall()
    cur = conn.cursor()
    # Zero-edge graphs persist nothing: full refresh still clears stale rows.
    if not edge_rows:
        cur.execute("DELETE FROM symbol_communities")
        cur.execute("DELETE FROM communities")
        conn.commit()
        return 0

    attrs = _load_member_attrs(conn)
    degree, projected = _project_edges(edge_rows)
    graph = _build_graph(attrs, projected)
    partition = louvain_communities(graph, seed=LOUVAIN_SEED)

    communities = [_describe(members, attrs, degree) for members in partition]
    communities.sort(key=lambda c: (-c["size"], c["label"]))

    # Full refresh: one DELETE per table, then one sorted executemany each.
    cur.execute("DELETE FROM symbol_communities")
    cur.execute("DELETE FROM communities")
    cur.executemany(
        "INSERT INTO communities (id, label, size) VALUES (?, ?, ?)",
        [
            (cid, c["label"], c["size"])
            for cid, c in enumerate(communities, start=1)
        ],
    )
    cur.executemany(
        "INSERT INTO symbol_communities (community_id, symbol_id, structural_degree) "
        "VALUES (?, ?, ?)",
        [
            (cid, symbol_id, degree[symbol_id])
            for cid, c in enumerate(communities, start=1)
            for symbol_id in c["member_ids"]
        ],
    )
    conn.commit()
    return len(communities)


def _load_member_attrs(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """Endpoint attributes (id, qualified_name, path) in sorted node order."""
    return conn.execute(
        f"""
        SELECT s.id, s.qualified_name, f.path
        FROM symbols s JOIN files f ON f.id = s.file_id
        WHERE s.id IN (
            SELECT source_id FROM edges WHERE kind IN ({_KIND_PLACEHOLDERS})
            UNION
            SELECT target_id FROM edges
            WHERE kind IN ({_KIND_PLACEHOLDERS}) AND target_id IS NOT NULL
        )
        ORDER BY s.qualified_name, s.id
        """,
        list(STRUCTURAL_EDGE_KINDS) + list(STRUCTURAL_EDGE_KINDS),
    ).fetchall()


def _project_edges(edge_rows) -> Tuple[Dict[str, int], Dict[Tuple[str, str], float]]:
    """Combined structural degree per endpoint and the undirected weighted
    projection (mirrored directed edges summed per pair).
    """
    degree: Dict[str, int] = {}
    projected: Dict[Tuple[str, str], float] = {}
    for row in edge_rows:
        source_id, target_id = row["source_id"], row["target_id"]
        degree[source_id] = degree.get(source_id, 0) + 1
        if target_id is None:
            continue
        degree[target_id] = degree.get(target_id, 0) + 1
        pair = (source_id, target_id) if source_id <= target_id else (target_id, source_id)
        projected[pair] = projected.get(pair, 0.0) + STRUCTURAL_EDGE_WEIGHTS[row["kind"]]
    return degree, projected


def _build_graph(attrs: List[sqlite3.Row], projected: Dict[Tuple[str, str], float]):
    """networkx graph with nodes in sorted order and sorted weighted edges."""
    import networkx as nx

    node_order: Dict[str, Tuple[str, str]] = {
        row["id"]: (row["qualified_name"], row["id"]) for row in attrs
    }
    graph = nx.Graph()
    for symbol_id in node_order:
        graph.add_node(symbol_id)
    for pair in sorted(projected, key=lambda p: (node_order[p[0]], node_order[p[1]])):
        graph.add_edge(pair[0], pair[1], weight=projected[pair])
    return graph


def _describe(
    members: set,
    attrs: List[sqlite3.Row],
    degree: Dict[str, int],
) -> dict:
    """One community record: sorted members, degree-ranked hubs, fact-derived label."""
    by_id = {row["id"]: row for row in attrs}
    infos = sorted(
        (by_id[symbol_id] for symbol_id in members),
        key=lambda r: (r["path"], r["qualified_name"], r["id"]),
    )
    hubs = sorted(
        infos,
        key=lambda r: (-degree[r["id"]], r["path"], r["qualified_name"]),
    )
    dominant = _dominant_segment(sorted(row["path"] for row in infos))
    label = (
        dominant
        if dominant is not None
        else _HUB_LABEL_PREFIX + hubs[0]["qualified_name"]
    )
    return {
        "label": label,
        "size": len(infos),
        "member_ids": [row["id"] for row in infos],
    }
