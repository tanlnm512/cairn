"""explore(): one-call "how does X work" aggregator.

A thin orchestrator that combines FTS5 + semantic seed search, 1-hop
caller/callee neighborhood, verbatim source spans, shallow blast radius, and
ambiguous-dispatch hops into a single answer.

Imports the other split modules (traversal, lexical, semantic) rather than
re-implementing them -- this is the integration layer.
"""
from __future__ import annotations

import logging
import os
import sqlite3

from .lexical import search_symbols
from .traversal import get_callers, get_callees, impact_analysis

logger = logging.getLogger(__name__)


def _read_source_spans(
    conn: sqlite3.Connection, symbol_ids, budget: int
) -> dict:
    """Read verbatim source spans for the given symbol ids, grouped by file.

    Joins symbols -> files, opens each file once, and slices
    ``[line_start-1 : line_end]`` for every symbol. Stops once cumulative line
    count hits ``budget`` (a soft cap — the file currently being read is
    finished before stopping so a single symbol's lines aren't truncated mid-
    span). Returns ``{file_path: [{"symbol", "kind", "line_start", "line_end",
    "repo", "lines": [str, ...]}]}``.

    Missing files / read errors degrade gracefully: the symbol's entry is
    omitted, never crashes the caller.
    """
    if not symbol_ids:
        return {}
    placeholders = ",".join("?" for _ in symbol_ids)
    rows = conn.execute(
        f"""SELECT s.id, s.name, s.kind, s.line_start, s.line_end,
                  f.path AS file_path, f.repo_id AS repo
           FROM symbols s JOIN files f ON s.file_id = f.id
           WHERE s.id IN ({placeholders})
           ORDER BY f.path, s.line_start""",
        tuple(symbol_ids),
    ).fetchall()

    # Group symbol metadata by file so each file is opened at most once.
    by_file: dict[str, list] = {}
    for r in rows:
        by_file.setdefault(r["file_path"], []).append(dict(r))

    out: dict[str, list] = {}
    used = 0
    for file_path, syms in by_file.items():
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                all_lines = fh.readlines()
        except OSError:
            continue  # file deleted/moved since index — skip silently
        entries = []
        for s in syms:
            ls = s["line_start"] or 0
            le = s["line_end"] or ls
            if ls < 1 or le < ls:
                continue
            span = all_lines[ls - 1 : le]
            entries.append(
                {
                    "symbol": s["name"],
                    "kind": s["kind"],
                    "line_start": ls,
                    "line_end": le,
                    "repo": s["repo"],
                    "lines": [line.rstrip("\n") for line in span],
                }
            )
            used += len(span)
        if entries:
            out[file_path] = entries
        if used >= budget:
            break  # soft cap reached — stop reading more files
    return out


def _ambiguous_dispatch(
    conn: sqlite3.Connection, target_names
) -> list:
    """Surface ``resolution='ambiguous'`` edges grouped by target.

    Ambiguous edges are intentionally left unresolved by the resolver (NULL
    ``target_id``) when more than one candidate existed — e.g. an interface
    call with several impls. These are invisible to precise ``get_callers``
    (which filters on resolved ``target_id``) and to ``impact_analysis``.
    This helper makes them queryable so ``explore`` can show "this call could
    dispatch to any of these impls" — information grep fundamentally lacks.

    Returns ``[{"dispatches_to": str, "candidates": [caller_name, ...]}]``,
    one entry per distinct ``target_name`` that has at least one ambiguous
    caller edge.
    """
    target_names = [t for t in target_names if t]
    if not target_names:
        return []
    placeholders = ",".join("?" for _ in target_names)
    rows = conn.execute(
        f"""SELECT e.target_name AS target,
                  GROUP_CONCAT(DISTINCT s.name) AS candidates
           FROM edges e JOIN symbols s ON e.source_id = s.id
           WHERE e.resolution = 'ambiguous'
             AND e.target_name IN ({placeholders})
           GROUP BY e.target_name
           ORDER BY e.target_name""",
        tuple(target_names),
    ).fetchall()
    out = []
    for r in rows:
        cands = [c for c in (r["candidates"] or "").split(",") if c]
        if cands:
            out.append({"dispatches_to": r["target"], "candidates": cands})
    return out


def explore(
    conn: sqlite3.Connection,
    query: str,
    max_nodes: int = 20,
    max_source_lines: int = 400,
) -> dict:
    """One-call answer to "how does X work": matching source + call paths +
    blast radius + ambiguous-dispatch hops.

    A thin orchestrator over existing query primitives:

    1. **Seed** via FTS5 search (bm25-ranked) — the most relevant symbols for
       ``query``.
    2. **Neighborhood** — for each seed, 1-hop callers + callees (precise
       resolution; ``resolution='exact'`` edges only, by default).
    3. **Source spans** — verbatim line-numbered source read from disk for
       seeds + neighbors, capped at ``max_source_lines`` total lines.
    4. **Blast radius** — shallow ``impact_analysis`` (depth 2) per seed;
       total count + top callers.
    5. **Ambiguous dispatch** — ``resolution='ambiguous'`` edges whose
       ``target_name`` matches a seed; the differentiator vs grep.

    Returns a dict with keys: ``seeds``, ``files``, ``call_paths``,
    ``blast_radius`` (per seed name), ``dispatch_hops``. The MCP tool layer
    renders this to plain text; the dict shape is stable for direct callers
    (CLI, tests).
    """
    # Local import to avoid an import cycle: semantic_search imports
    # search_symbols from this module's sibling (lexical), and at module load
    # time that's fine, but semantic_search itself is heavy (embeddings +
    # reranker + ann) and is only needed on the fusion path below.
    from .semantic import semantic_search

    # Step 1: seeds via FTS5 search.
    seed_rows = search_symbols(conn, query, limit=max_nodes)
    seeds = []
    seed_ids = set()
    seed_names = []
    for r in seed_rows:
        d = dict(r)
        seeds.append(
            {
                "id": d.get("id"),
                "name": d.get("name"),
                "kind": d.get("kind"),
                "qualified_name": d.get("qualified_name"),
                "line_start": d.get("line_start"),
                "file_path": d.get("file_path"),
                "repo": d.get("repo"),
            }
        )
        if d.get("id"):
            seed_ids.add(d["id"])
        if d.get("name"):
            seed_names.append(d["name"])

    # RRF Hybrid fusion or seed-only fallback.
    fusion_enabled = os.environ.get("CAIRN_FUSION", "1") != "0"
    if fusion_enabled or len(seeds) < 3:
        try:
            from cairn.graph import embeddings as emb

            if emb.embeddings_available() and emb.embed_count(conn) > 0:
                sem_rows = semantic_search(conn, query, limit=max_nodes)
                seen = set(seed_ids)
                for r in sem_rows:
                    if r["id"] in seen:
                        continue
                    seen.add(r["id"])
                    seeds.append(
                        {
                            "id": r["id"],
                            "name": r["name"],
                            "kind": r["kind"],
                            "qualified_name": r["qualified_name"],
                            "line_start": None,  # semantic/fused hits don't carry line spans
                            "file_path": r["file_path"],
                            "repo": r["repo"],
                        }
                    )
                    if r["id"]:
                        seed_ids.add(r["id"])
                    if r["name"]:
                        seed_names.append(r["name"])
        except Exception:
            logger.debug("semantic backend unavailable, FTS5-only", exc_info=True)
            pass  # semantic backend not wired — degrade silently to FTS5-only

    if not seeds:
        return {
            "seeds": [],
            "files": {},
            "call_paths": {"callers": [], "callees": []},
            "blast_radius": {},
            "dispatch_hops": [],
        }

    # Step 2: 1-hop neighborhood per seed.
    neighbor_ids = set()
    callers_out = []
    callees_out = []
    seen_caller_keys = set()
    seen_callee_keys = set()
    for seed in seeds:
        name = seed["name"]
        if not name:
            continue
        for c in get_callers(conn, name, limit=20):
            key = (c["caller_name"], name, c["file_path"], c["edge_line"])
            if key in seen_caller_keys:
                continue
            seen_caller_keys.add(key)
            callers_out.append(
                {
                    "from": c["caller_name"],
                    "to": name,
                    "kind": c["edge_kind"],
                    "file_path": c["file_path"],
                    "line": c["edge_line"],
                    "repo": c["repo"],
                    "resolution": c["resolution"],
                }
            )
            if c["caller_id"]:
                neighbor_ids.add(c["caller_id"])
        for c in get_callees(conn, name, limit=20):
            key = (name, c["callee_name"], c["file_path"], c["edge_line"])
            if key in seen_callee_keys:
                continue
            seen_callee_keys.add(key)
            callees_out.append(
                {
                    "from": name,
                    "to": c["callee_name"],
                    "kind": c["edge_kind"],
                    "file_path": c["file_path"],
                    "line": c["edge_line"],
                    "repo": c["repo"],
                    "resolution": c["resolution"],
                }
            )

    # Step 3: source spans for seeds + neighbors.
    source_ids = seed_ids | neighbor_ids
    files_out = _read_source_spans(conn, source_ids, max_source_lines)

    # Step 4: blast radius per seed (shallow depth 2).
    blast_out = {}
    for seed in seeds:
        name = seed["name"]
        if not name:
            continue
        blast = impact_analysis(conn, name, max_depth=2)
        # Top callers (deduped by name), at most 5.
        top = []
        seen_top = set()
        for entry in blast["impacted"]:
            sym = entry["symbol"]
            if sym and sym not in seen_top:
                seen_top.add(sym)
                top.append(sym)
            if len(top) >= 5:
                break
        repos = {entry["repo"] for entry in blast["impacted"] if entry.get("repo")}
        blast_out[name] = {
            "total": blast["total"],
            "repos": sorted(r for r in repos if r),
            "top_callers": top,
        }

    # Step 5: ambiguous dispatch hops for seed names.
    dispatch_hops = _ambiguous_dispatch(conn, set(seed_names))

    return {
        "seeds": seeds,
        "files": files_out,
        "call_paths": {"callers": callers_out, "callees": callees_out},
        "blast_radius": blast_out,
        "dispatch_hops": dispatch_hops,
    }
