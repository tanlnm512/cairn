"""`cairn communities` sidecar: seeded Louvain partition over the store's structural edges (networkx)."""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple


def rust_bin() -> Optional[str]:
    """The engine binary: `CAIRN_RUST_BIN`, else `cairn` on PATH; None when neither resolves."""
    return os.environ.get("CAIRN_RUST_BIN") or shutil.which("cairn")


LOUVAIN_SEED = 0
DEFAULT_TOP_K = 10
STRUCTURAL_EDGE_KINDS: Tuple[str, ...] = ("calls", "call", "extends", "implements")
# Weights must cover every STRUCTURAL_EDGE_KINDS entry; a kind added there
# without a weight here fails loudly instead of clustering unweighted.
STRUCTURAL_EDGE_WEIGHTS: Dict[str, float] = {
    "calls": 1.0,
    "call": 1.0,
    "extends": 2.0,
    "implements": 2.0,
}
_DOMINANT_SEGMENT_SHARE = 0.60
_HUB_LABEL_PREFIX = "hub:"
_KIND_PLACEHOLDERS = ",".join("?" for _ in STRUCTURAL_EDGE_KINDS)
_HUB_SORT = (
    "ORDER BY sc.structural_degree DESC, f.path, s.qualified_name, sc.symbol_id "
    "LIMIT ?"
)


def probe_networkx() -> Callable:
    """Return ``louvain_communities``; ImportError with install hint when networkx is absent (call before any writable store open)."""
    try:
        from networkx.algorithms.community import louvain_communities
    except ImportError as exc:
        raise ImportError(
            "community detection needs the optional 'networkx' package; "
            "install it with the graph-analytics extra: "
            "pip install 'cairn-intel[graph-analytics]'"
        ) from exc
    return louvain_communities


def open_store(db_path: str, read_only: bool = False) -> sqlite3.Connection:
    """Open an existing store; the engine owns the schema, so a missing file is an error."""
    mode = "ro" if read_only else "rw"
    conn = sqlite3.connect(f"file:{db_path}?mode={mode}", uri=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def refresh_communities(db_path: str) -> int:
    """Probe, then open the store writable and full-refresh the communities tables; returns the community count."""
    probe_networkx()
    conn = open_store(db_path)
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
    projection (mirrored directed edges summed per pair)."""
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


def _dominant_segment(paths: Iterable[str]) -> Optional[str]:
    paths = list(paths)
    if not paths:
        return None
    counts: Dict[str, int] = {}
    for path in paths:
        segment = path.split("/", 1)[0]
        counts[segment] = counts.get(segment, 0) + 1
    segment, count = max(counts.items(), key=lambda item: (item[1], item[0]))
    return segment if count / len(paths) > _DOMINANT_SEGMENT_SHARE else None


def _describe(members: set, attrs: List[sqlite3.Row], degree: Dict[str, int]) -> dict:
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


# --- CLI ------------------------------------------------------------------------


def _hub_rows(conn, community_id: Optional[int], top_k: int) -> Sequence:
    """Top-K members by (-structural_degree, path, qualified_name); all communities when community_id is None."""
    where = "WHERE sc.community_id = ? " if community_id is not None else ""
    params = ((community_id,) if community_id is not None else ()) + (top_k,)
    return conn.execute(
        f"""
        SELECT s.qualified_name AS qualified_name,
               sc.structural_degree AS structural_degree
        FROM symbol_communities sc
        JOIN symbols s ON s.id = sc.symbol_id
        JOIN files f ON f.id = s.file_id
        {where}{_HUB_SORT}
        """,
        params,
    ).fetchall()


def _print_hubs(rows, indent: str) -> None:
    for row in rows:
        print(f"{indent}{row['qualified_name']} (degree {row['structural_degree']})")


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"{text} is not in the range x>=1")
    return value


def default_db_path() -> str:
    """The engine's resolved store path (`cairn config --db`); the binary owns store resolution."""
    binary = rust_bin()
    if binary is None:
        raise SystemExit("cairn communities: no engine binary found to resolve the store; pass --db")
    proc = subprocess.run([binary, "config", "--db"], capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise SystemExit(f"cairn communities: cannot resolve the store: {proc.stderr.strip()}")
    return proc.stdout.strip()


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Compute Louvain communities over structural edges, persist them, and print the hub summary."""
    parser = argparse.ArgumentParser(
        prog="cairn communities",
        description="Compute Louvain communities over structural edges and persist them.",
    )
    parser.add_argument("--db", default=None, help="SQLite DB path (default: the resolved store).")
    parser.add_argument(
        "--top-k", type=_positive_int, default=DEFAULT_TOP_K,
        help="Top-K hubs listed globally and per community (default: %(default)s).",
    )
    args = parser.parse_args(argv)
    db = args.db or default_db_path()
    try:
        count = refresh_communities(db)
    except ImportError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except sqlite3.OperationalError as exc:
        print(f"cairn communities: {exc} ({db})", file=sys.stderr)
        return 1

    if count == 0:
        print("no communities found")
        return 0

    conn = open_store(db, read_only=True)
    try:
        print(f"{count} communities")
        print("Global hubs:")
        _print_hubs(_hub_rows(conn, None, args.top_k), "  ")
        print("Communities:")
        rows = conn.execute("SELECT id, label, size FROM communities ORDER BY id").fetchall()
        for row in rows:
            print(f"  {row['id']}: {row['label']} ({row['size']} symbols)")
            _print_hubs(_hub_rows(conn, row["id"], args.top_k), "    ")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
