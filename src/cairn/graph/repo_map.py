"""Deterministic repository orientation projections over stored graph rows."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable


DEFAULT_CLUSTER_CAP = 16
DEFAULT_HUB_CAP = 3
DEFAULT_HOTSPOT_CAP = 12
_DOMINANT_SEGMENT_SHARE = 0.60


@dataclass(frozen=True)
class _SymbolRow:
    repo: str
    path: str
    qualified_name: str
    incoming: int
    outgoing: int


@dataclass(frozen=True)
class _Projection:
    symbols: list[_SymbolRow]
    repos: set[str]
    paths: dict[str, set[str]]


def _validate_cap(name: str, value: int) -> None:
    if value < 1:
        raise ValueError(f"{name} must be at least 1")


def _projection(conn: sqlite3.Connection) -> _Projection:
    rows = conn.execute(
        """
        SELECT
            repos.id AS repo_id,
            files.id AS file_id,
            files.path AS path,
            symbols.id AS symbol_id,
            COALESCE(NULLIF(symbols.qualified_name, ''), symbols.name)
                AS qualified_name,
            (
                SELECT COUNT(*)
                FROM edges AS outgoing
                WHERE outgoing.source_id = symbols.id
            ) AS outgoing_edges,
            (
                SELECT COUNT(*)
                FROM edges AS incoming
                WHERE incoming.target_id = symbols.id
            ) AS incoming_edges
        FROM repos
        LEFT JOIN files ON files.repo_id = repos.id
        LEFT JOIN symbols ON symbols.file_id = files.id
        ORDER BY repos.id, files.path, qualified_name, symbols.id
        """
    ).fetchall()
    repos = {row["repo_id"] for row in rows}
    paths: dict[str, set[str]] = {
        repo_id: set() for repo_id in repos
    }
    symbols = [
        _SymbolRow(
            repo=row["repo_id"],
            path=row["path"] or "",
            qualified_name=row["qualified_name"] or "",
            incoming=row["incoming_edges"] or 0,
            outgoing=row["outgoing_edges"] or 0,
        )
        for row in rows
        if row["symbol_id"] is not None
    ]
    for row in rows:
        if row["file_id"] is not None:
            paths[row["repo_id"]].add(row["path"])
    return _Projection(symbols=symbols, repos=repos, paths=paths)


def _cluster_path(path: str, split_dominant_segment: bool) -> str:
    parts = path.split("/")
    if len(parts) == 1:
        return "."
    depth = 2 if split_dominant_segment and len(parts) > 2 else 1
    return "/".join(parts[:depth])


def _cluster_key(path: str, dominant_segment: str | None) -> str:
    is_dominant = dominant_segment is not None and path.startswith(
        f"{dominant_segment}/"
    )
    return _cluster_path(path, is_dominant)


def _dominant_segment(paths: Iterable[str]) -> str | None:
    paths = list(paths)
    if not paths:
        return None
    counts: dict[str, int] = {}
    for path in paths:
        segment = path.split("/", 1)[0]
        counts[segment] = counts.get(segment, 0) + 1
    segment, count = max(counts.items(), key=lambda item: (item[1], item[0]))
    return segment if count / len(paths) > _DOMINANT_SEGMENT_SHARE else None


def _clusters(
    symbols: list[_SymbolRow],
    paths: set[str],
    hub_cap: int,
    cluster_cap: int,
) -> tuple[list[dict], int]:
    dominant = _dominant_segment(paths)
    grouped: dict[str, list[_SymbolRow]] = {}
    for symbol in symbols:
        grouped.setdefault(_cluster_key(symbol.path, dominant), []).append(symbol)
    file_groups: dict[str, set[str]] = {}
    for path in paths:
        file_groups.setdefault(_cluster_key(path, dominant), set()).add(path)

    ordered: list[dict] = []
    for cluster_path in sorted(
        set(grouped) | set(file_groups),
        key=lambda path: (-len(grouped.get(path, [])), path),
    ):
        members = grouped.get(cluster_path, [])
        hubs = sorted(
            (symbol for symbol in members if symbol.incoming > 0),
            key=lambda symbol: (-symbol.incoming, symbol.path, symbol.qualified_name),
        )[:hub_cap]
        ordered.append(
            {
                "path": cluster_path,
                "files": len(file_groups.get(cluster_path, set())),
                "symbols": len(members),
                "edges": sum(symbol.outgoing for symbol in members),
                "hubs": [
                    {
                        "qualified_name": symbol.qualified_name,
                        "path": symbol.path,
                        "incoming": symbol.incoming,
                    }
                    for symbol in hubs
                ],
                "dropped_hubs": max(0, _hub_count(members) - hub_cap),
            }
        )
    dropped = max(0, len(ordered) - cluster_cap)
    return ordered[:cluster_cap], dropped


def _hub_count(members: list[_SymbolRow]) -> int:
    return sum(symbol.incoming > 0 for symbol in members)


def build_repo_map(
    conn: sqlite3.Connection,
    *,
    cluster_cap: int = DEFAULT_CLUSTER_CAP,
    hub_cap: int = DEFAULT_HUB_CAP,
    hotspot_cap: int = DEFAULT_HOTSPOT_CAP,
) -> dict:
    """Return a deterministic, capped orientation for every stored repository."""
    _validate_cap("cluster_cap", cluster_cap)
    _validate_cap("hub_cap", hub_cap)
    _validate_cap("hotspot_cap", hotspot_cap)

    projection = _projection(conn)
    rows = projection.symbols
    by_repo: dict[str, list[_SymbolRow]] = {}
    for repo_id in projection.repos:
        by_repo[repo_id] = []
    for row in rows:
        by_repo.setdefault(row.repo, []).append(row)

    scopes = []
    for repo_id in sorted(by_repo):
        clusters, dropped_clusters = _clusters(
            by_repo[repo_id], projection.paths[repo_id], hub_cap, cluster_cap
        )
        scopes.append(
            {
                "repo": repo_id,
                "clusters": clusters,
                "dropped_clusters": dropped_clusters,
            }
        )

    hotspots = sorted(
        (row for row in rows if row.incoming > 0),
        key=lambda row: (
            -row.incoming,
            row.repo,
            row.path,
            row.qualified_name,
        ),
    )[:hotspot_cap]
    hotspot_count = sum(row.incoming > 0 for row in rows)
    return {
        "repos": scopes,
        "hotspots": [
            {
                "repo": row.repo,
                "qualified_name": row.qualified_name,
                "path": row.path,
                "incoming": row.incoming,
            }
            for row in hotspots
        ],
        "dropped_hotspots": max(0, hotspot_count - hotspot_cap),
    }
