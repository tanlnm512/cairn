# Survey: taint-tracking

**Created**: 2026-09-16 | **Baseline**: codex/docs-level-up-specs @ 1af3b35
The survey node's output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item S1: "Precomputed dataflow index for public symbols exists"
  evidence:   src/cairn/graph/dataflow.py:1-2 `"""Precomputed dataflow index for public/exported symbols. Materialises within-repo impact chains and cross-repo consumer repos for each public symbol into the `dataflow` table.`; schema.py:319 `CREATE TABLE IF NOT EXISTS dataflow (`
  status:     DONE
  verify:     head -12 src/cairn/graph/dataflow.py
  gap:        chains carry no taint labels; no source/sink classification

item S2: "Multi-hop reachability answers in one indexed statement"
  evidence:   src/cairn/graph/dataflow.py:230 `"""Precompute multi-hop call graph edges into transitive_edges matrix table.`; schema.py:396 `CREATE TABLE IF NOT EXISTS transitive_edges (` with distance-filtering indexes (:403-408)
  status:     DONE
  verify:     grep -n "transitive_edges" src/cairn/graph/schema.py | head -3
  gap:        reachability is call-graph-level; closure capped at CLOSURE_MAX_DEPTH = 4 (dataflow.py:28)

item S3: "Resolution labels gate traversal precision today"
  evidence:   src/cairn/graph/schema.py post-`edges` comment: `edges.resolution column tracks HOW an edge target was resolved` (exact/ambiguous/unresolved); blast + impact_analysis expose precise-default / `--fuzzy` opt-in (cli/blast.py `--fuzzy` flag)
  status:     DONE
  verify:     grep -n "resolution" src/cairn/graph/schema.py | head -3
  gap:        no taint propagation pass consumes these structures

item S4: "explore/blast are the warning surfaces"
  evidence:   src/cairn/graph/blast.py:471 `def compute_blast(`; src/cairn/mcp_server/tools_graph.py:432 `def explore(query: str) -> str:` (renders source, call paths, blast radius)
  status:     DONE
  verify:     grep -n "def explore" src/cairn/mcp_server/tools_graph.py
  gap:        neither renders any security annotation
```

## Supporting evidence
No taint/source/sink code exists anywhere (`grep -rn "taint" src/cairn/` → none).
The work is a new pass over stored edges plus config; per proposal §6.3 no new
runtime dependency is required (pure graph traversal).

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
