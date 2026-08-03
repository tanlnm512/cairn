"""Aggregate stats and directory/package tree rollups.

Read-only analytics over the graph (counts, by-kind, by-repo, per-directory
symbol buckets) -- distinct from the traversal and search halves.

Note: ``group_by_top_level`` is also imported by ``viz/query.py``, so it
stays public (no underscore).
"""
from __future__ import annotations

import sqlite3
from typing import List


def get_stats(conn: sqlite3.Connection) -> dict:
    """Aggregate counts for repos, files, symbols, edges, imports."""
    cur = conn.cursor()
    stats = {}
    stats["repos"] = cur.execute("SELECT COUNT(*) AS c FROM repos").fetchone()["c"]
    stats["files"] = cur.execute("SELECT COUNT(*) AS c FROM files").fetchone()["c"]
    stats["symbols"] = cur.execute("SELECT COUNT(*) AS c FROM symbols").fetchone()["c"]
    stats["edges"] = cur.execute("SELECT COUNT(*) AS c FROM edges").fetchone()["c"]
    stats["imports"] = cur.execute("SELECT COUNT(*) AS c FROM imports").fetchone()["c"]
    # Symbols by kind.
    stats["by_kind"] = {
        r["kind"]: r["c"]
        for r in cur.execute(
            "SELECT kind, COUNT(*) AS c FROM symbols GROUP BY kind ORDER BY c DESC"
        ).fetchall()
    }
    stats["by_repo"] = {
        r["repo_id"]: r["c"]
        for r in cur.execute(
            """SELECT f.repo_id, COUNT(*) AS c FROM symbols s
               JOIN files f ON s.file_id = f.id GROUP BY f.repo_id ORDER BY c DESC"""
        ).fetchall()
    }
    stats["edges_resolved"] = cur.execute(
        "SELECT COUNT(*) AS c FROM edges WHERE target_id IS NOT NULL"
    ).fetchone()["c"]
    # skipped-file counts by reason (best-effort -- the table may not exist on
    # DBs created before this migration, though get_db creates it).
    try:
        stats["skipped_total"] = cur.execute(
            "SELECT COUNT(*) AS c FROM skipped_files"
        ).fetchone()["c"]
        stats["skipped_by_reason"] = {
            r["reason"]: r["c"]
            for r in cur.execute(
                "SELECT reason, COUNT(*) AS c FROM skipped_files "
                "GROUP BY reason ORDER BY c DESC"
            ).fetchall()
        }
    except sqlite3.OperationalError:
        stats["skipped_total"] = 0
        stats["skipped_by_reason"] = {}
    return stats


def get_tree(conn: sqlite3.Connection, repo: str, prefix: str = "") -> List[sqlite3.Row]:
    """Return directory/package structure with symbol counts for a repo."""
    cur = conn.cursor()
    like = f"{repo}/{prefix}%" if prefix else "%"
    rows = cur.execute(
        """SELECT f.rel_path_no_repo AS rel, COUNT(s.id) AS symbols
           FROM (SELECT id, substr(path, ?) AS rel_path_no_repo FROM files WHERE repo_id = ?) f
           LEFT JOIN symbols s ON s.file_id = f.id
           GROUP BY f.id""",
        # path stored absolute; we trim the repo root prefix below instead.
        (0, repo),
    )
    # The above is approximate; do a simpler grouping by top-level package dir.
    return group_by_top_level(conn, repo)


def group_by_top_level(conn: sqlite3.Connection, repo: str) -> List[sqlite3.Row]:
    """Group a repo's symbols by their top-level source directory."""
    cur = conn.cursor()
    repo_row = cur.execute(
        "SELECT path FROM repos WHERE id = ?", (repo,)
    ).fetchone()
    if not repo_row:
        return []
    repo_root = repo_row["path"]
    rows = cur.execute(
        """SELECT f.path AS path, COUNT(s.id) AS symbols
           FROM files f LEFT JOIN symbols s ON s.file_id = f.id
           WHERE f.repo_id = ?
           GROUP BY f.id""",
        (repo,),
    ).fetchall()
    # Bucket by the first 2-3 path segments after repo root.
    buckets: dict[str, int] = {}
    for r in rows:
        rel = r["path"].replace(repo_root + "/", "", 1)
        parts = rel.split("/")
        # Use first meaningful module dir (skip src/main/java/...)
        key = "/".join(parts[:3]) if len(parts) >= 3 else parts[0]
        buckets[key] = buckets.get(key, 0) + r["symbols"]
    return sorted(buckets.items(), key=lambda x: -x[1])
