"""Taint source/sink registry: category -> exact call-name tables.

Names are matched exactly against the call name recorded on call edges;
for attribute calls that name is the attribute tail (``cursor.execute``
records ``execute``). Matching is conservative: no heuristics, no
framework-specific names. Workspace config overrides merge through
``build_registry``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Mapping, Optional

from .dataflow import CLOSURE_MAX_DEPTH
from .traversal import STRUCTURAL_EDGE_KINDS

DEFAULT_SOURCES: dict[str, set[str]] = {
    "http": {"urlopen"},
    "cli": {"input", "parse_args"},
    "env": {"getenv"},
    "file-read": {"open", "read_text", "read_bytes", "readlines", "readline"},
    "api-response": {"json", "loads"},
}

DEFAULT_SINKS: dict[str, set[str]] = {
    "sql": {"execute", "executemany", "executescript"},
    "shell": {"system", "popen", "Popen", "run", "check_call", "check_output"},
    "file-write": {"open", "write", "write_text", "write_bytes"},
    "network-send": {"send", "sendall", "sendto"},
    "eval-deserialize": {"eval", "exec", "load", "loads"},
}


@dataclass
class TaintRegistry:
    """Taint source and sink tables, each mapping category -> call names."""

    sources: dict[str, set[str]] = field(default_factory=dict)
    sinks: dict[str, set[str]] = field(default_factory=dict)

    def source_names(self) -> set[str]:
        """All source call names across categories, for exact-name matching."""
        return _flatten(self.sources)

    def sink_names(self) -> set[str]:
        """All sink call names across categories, for exact-name matching."""
        return _flatten(self.sinks)


def build_registry(
    source_overrides: Optional[Mapping[str, Iterable[str]]] = None,
    sink_overrides: Optional[Mapping[str, Iterable[str]]] = None,
) -> TaintRegistry:
    """Merge override categories into the default tables.

    An override category replaces the same-named default category (an
    empty override disables it); a new category extends the table. The
    defaults are copied, never mutated.
    """
    return TaintRegistry(
        sources=_merged(DEFAULT_SOURCES, source_overrides),
        sinks=_merged(DEFAULT_SINKS, sink_overrides),
    )


def _flatten(table: Mapping[str, Iterable[str]]) -> set[str]:
    names: set[str] = set()
    for category_names in table.values():
        names.update(category_names)
    return names


def _merged(
    defaults: Mapping[str, Iterable[str]],
    overrides: Optional[Mapping[str, Iterable[str]]],
) -> dict[str, set[str]]:
    merged = {category: set(names) for category, names in defaults.items()}
    for category, names in (overrides or {}).items():
        merged[category] = set(names)
    return merged


@dataclass(frozen=True)
class TaintHop:
    """One hop of a taint path: file, symbol name, and the resolution label
    of the edge that reached this hop (``exact`` for the entry hop)."""

    file: str
    symbol: str
    resolution: str


@dataclass(frozen=True)
class TaintPath:
    """A source-to-sink taint path, hops ordered from entry to sink."""

    hops: list[TaintHop]


# Seeding only follows call edges: a source/sink match is a call event, so
# class-hierarchy edges never seed or terminate a path.
_CALL_EDGE_KINDS: tuple[str, ...] = ("calls", "call")

# Frontier batch size for IN () clauses, under SQLite's 999 host-parameter limit.
_IN_CHUNK = 400


def find_paths(
    conn: sqlite3.Connection,
    registry: TaintRegistry,
    from_pattern: str,
    to_pattern: str,
    fuzzy: bool = False,
    max_depth: int = CLOSURE_MAX_DEPTH,
) -> list[TaintPath]:
    """Trace inter-procedural taint paths over the call graph.

    ``from_pattern``/``to_pattern`` each name a registry category token or a
    single exact call name; an unmatched pattern yields no paths. Entry
    symbols are functions whose call edges hit a source name; terminators are
    functions whose call edges hit a sink name. The walk is a depth-capped
    BFS over ``edges`` -- never the ``transitive_edges`` closure, which
    carries no resolution column. Only ``resolution='exact'`` edges are
    followed unless ``fuzzy`` also follows ambiguous/unresolved edges by
    their preserved target name. ``max_depth`` is the edge budget between
    entry and terminator, so a path carries at most ``max_depth + 1`` hops;
    the default cap is the analysis precision boundary. One shortest path is
    returned per (entry, sink) pair; a sink terminates its path and is never
    expanded through.
    """
    from_names = _pattern_names(from_pattern, registry.sources)
    to_names = _pattern_names(to_pattern, registry.sinks)
    if not from_names or not to_names:
        return []
    return _discover_paths(conn, from_names, to_names, fuzzy, max_depth)


def intersect_seeds(
    conn: sqlite3.Connection,
    registry: TaintRegistry,
    seeds: set[str],
    fuzzy: bool = False,
) -> list[TaintPath]:
    """Taint paths whose entry or sink symbol name is in ``seeds``.

    The explore/blast warning intersection: a changed or queried symbol on a
    path endpoint touches a known source-to-sink flow. Runs over the full
    default registry at the default depth cap.
    """
    if not seeds:
        return []
    paths = _discover_paths(
        conn,
        registry.source_names(),
        registry.sink_names(),
        fuzzy,
        CLOSURE_MAX_DEPTH,
    )
    return [
        path
        for path in paths
        if path.hops[0].symbol in seeds or path.hops[-1].symbol in seeds
    ]


def _pattern_names(pattern: str, table: Mapping[str, Iterable[str]]) -> set[str]:
    """Resolve a query pattern: category token, exact call name, or nothing."""
    if pattern in table:
        return set(table[pattern])
    return {pattern} if pattern in _flatten(table) else set()


def _discover_paths(
    conn: sqlite3.Connection,
    from_names: set[str],
    to_names: set[str],
    fuzzy: bool,
    max_depth: int,
) -> list[TaintPath]:
    entries = _symbols_calling(conn, from_names)
    terminator_ids = {row["id"] for row in _symbols_calling(conn, to_names)}
    if not entries or not terminator_ids:
        return []
    paths: list[TaintPath] = []
    for entry in entries:
        for hops in _paths_from_entry(conn, entry, terminator_ids, fuzzy, max_depth):
            paths.append(TaintPath(hops=hops))
    return paths


def _symbols_calling(conn: sqlite3.Connection, names: set[str]) -> list[sqlite3.Row]:
    """Symbols with a call edge to one of ``names``, in stable (file, name) order.

    The call name is the resolved symbol name on exact edges, the preserved
    attribute tail on ambiguous/unresolved edges.
    """
    name_ph = ",".join("?" * len(names))
    kind_ph = ",".join("?" * len(_CALL_EDGE_KINDS))
    return conn.execute(
        f"""
        SELECT DISTINCT s.id, s.name, f.path AS file
        FROM edges e
        JOIN symbols s ON s.id = e.source_id
        JOIN files f ON f.id = s.file_id
        LEFT JOIN symbols t ON t.id = e.target_id
        WHERE COALESCE(t.name, e.target_name) IN ({name_ph})
          AND e.kind IN ({kind_ph})
        ORDER BY f.path, s.name, s.id
        """,
        (*names, *_CALL_EDGE_KINDS),
    ).fetchall()


def _paths_from_entry(
    conn: sqlite3.Connection,
    entry: sqlite3.Row,
    terminator_ids: set[str],
    fuzzy: bool,
    max_depth: int,
) -> list[list[TaintHop]]:
    """Shortest hop chain per terminator reachable from one entry symbol.

    The entry hop is labeled ``exact``: registry matching is exact by
    construction. Each later hop carries the resolution of the edge that
    reached it.
    """
    paths: list[list[TaintHop]] = []
    # hop id -> (previous hop id or None, rendered hop)
    parent: dict[str, tuple[Optional[str], TaintHop]] = {
        entry["id"]: (None, TaintHop(entry["file"], entry["name"], "exact"))
    }
    visited = {entry["id"]}
    frontier = [entry["id"]]
    depth = 0

    def visit(row: sqlite3.Row, next_frontier: list[str]) -> None:
        hop_id = row["hop_id"]
        if hop_id in visited:
            return
        visited.add(hop_id)
        parent[hop_id] = (
            row["source_id"],
            TaintHop(row["hop_file"], row["hop_name"], row["resolution"]),
        )
        if hop_id in terminator_ids:
            paths.append(_hop_chain(hop_id, parent))
        else:
            next_frontier.append(hop_id)

    while frontier and depth < max_depth:
        next_frontier: list[str] = []
        for chunk in _chunked(frontier):
            for row in _exact_neighbors(conn, chunk):
                visit(row, next_frontier)
            if fuzzy:
                for row in _fuzzy_neighbors(conn, chunk):
                    visit(row, next_frontier)
        frontier = next_frontier
        depth += 1
    return paths


def _hop_chain(
    hop_id: str, parent: dict[str, tuple[Optional[str], TaintHop]]
) -> list[TaintHop]:
    """Walk parent links back to the entry and return entry-first hops."""
    hops: list[TaintHop] = []
    cursor: Optional[str] = hop_id
    while cursor is not None:
        previous, hop = parent[cursor]
        hops.append(hop)
        cursor = previous
    hops.reverse()
    return hops


def _exact_neighbors(
    conn: sqlite3.Connection, source_ids: list[str]
) -> list[sqlite3.Row]:
    """Resolved one-definition call edges leaving ``source_ids``."""
    id_ph = ",".join("?" * len(source_ids))
    kind_ph = ",".join("?" * len(STRUCTURAL_EDGE_KINDS))
    return conn.execute(
        f"""
        SELECT DISTINCT e.source_id, e.resolution,
               s.id AS hop_id, s.name AS hop_name, f.path AS hop_file
        FROM edges e
        JOIN symbols s ON s.id = e.target_id
        JOIN files f ON f.id = s.file_id
        WHERE e.source_id IN ({id_ph})
          AND e.resolution = 'exact'
          AND e.kind IN ({kind_ph})
        ORDER BY s.name, f.path, s.id
        """,
        (*source_ids, *STRUCTURAL_EDGE_KINDS),
    ).fetchall()


def _fuzzy_neighbors(
    conn: sqlite3.Connection, source_ids: list[str]
) -> list[sqlite3.Row]:
    """Ambiguous/unresolved edges hopped by their preserved target name.

    An ambiguous name hops to every same-repo definition (the fuzzy
    candidate list); an unresolved name has no definition and yields no hop.
    """
    id_ph = ",".join("?" * len(source_ids))
    kind_ph = ",".join("?" * len(STRUCTURAL_EDGE_KINDS))
    return conn.execute(
        f"""
        SELECT DISTINCT e.source_id, e.resolution,
               s.id AS hop_id, s.name AS hop_name, f.path AS hop_file
        FROM edges e
        JOIN symbols s ON s.name = e.target_name
        JOIN files f ON f.id = s.file_id
        WHERE e.source_id IN ({id_ph})
          AND e.resolution IN ('ambiguous', 'unresolved')
          AND e.kind IN ({kind_ph})
        ORDER BY s.name, f.path, s.id
        """,
        (*source_ids, *STRUCTURAL_EDGE_KINDS),
    ).fetchall()


def _chunked(ids: list[str]) -> Iterator[list[str]]:
    """Yield slices of at most ``_IN_CHUNK`` ids for batched IN () clauses."""
    for start in range(0, len(ids), _IN_CHUNK):
        yield ids[start : start + _IN_CHUNK]


def format_taint_warning(paths: list[TaintPath]) -> str:
    """Render taint paths as the warning text the explore/blast surfaces
    display verbatim.

    One line per path: ``Taint path: file:symbol [resolution-label] -> ...``;
    an empty list yields an empty string. The text never contains the
    ``degraded: rung`` substring.
    """
    return "\n".join(
        "Taint path: "
        + " -> ".join(
            f"{hop.file}:{hop.symbol} [{hop.resolution}]" for hop in path.hops
        )
        for path in paths
    )
