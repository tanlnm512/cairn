# Survey: ui-dashboard-graph-nav

**Created**: 2026-08-20 | **Baseline**: cairn-intel 0.12.1 @ `d781383`
Phase-A output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item Q1: "Graph navigation is form-only; app.js wires no interactions beyond pan/zoom/drag"
  evidence: src/cairn/dashboard/templates/graph.html — form with scope/
    focus/repo/depth inputs and an Apply button; "scope queries are capped,
    so a count at the cap means the graph was truncated"
  evidence: src/cairn/dashboard/static/app.js:48-55 — vis.Network options:
    "interaction: { dragNodes: true, dragView: true, zoomView: true }" —
    no select/click listeners
  status: TODO
  verify: grep -n "\.on(" src/cairn/dashboard/static/app.js
  gap: no search, no node actions, no layout choice (FR-001..FR-004's delta)

item Q2: "Symbol scope serves the 1-hop neighborhood: focus + callers + callees, capped"
  evidence: src/cairn/viz/query.py:12 — "def get_symbol_graph(conn:
    sqlite3.Connection, name: str, depth: int = 1) -> Dict:"
  evidence: src/cairn/viz/query.py — callers query "WHERE e.target_id IN
    (SELECT id FROM symbols WHERE name = ?) LIMIT 30" and callees query
    with "LIMIT 30"; return metadata carries node_count/edge_count
  status: DONE
  verify: sed -n 12,53p src/cairn/viz/query.py
  gap: search-to-focus composes this as-is; nothing beyond 1-hop exists

item Q3: "get_symbol_graph's depth parameter is dead — the body never uses it"
  evidence: awk over the function body (def line to the impact def) shows
    `depth` only in the signature; queries are fixed 1-hop
  status: TODO
  verify: awk '/^def get_symbol_graph/,/^def get_impact_graph/' src/cairn/viz/query.py | grep -c depth
  gap: FR-003's multi-hop expansion needs traversal that does not exist —
    wire depth or add a recursive walk (spec's named new piece)

item Q4: "Ambiguous names resolve by silent LIMIT 1 today"
  evidence: src/cairn/viz/query.py — focal lookup "WHERE s.name = ? LIMIT 1"
    (first match wins, no disambiguation surface)
  status: TODO
  verify: grep -n "LIMIT 1" src/cairn/viz/query.py | head -3
  gap: FR-002's candidate list needs a name-lookup query returning matches
    (symbol name + file + kind), which exists nowhere in the dashboard layer

item Q5: "Impact scope already walks depth — the recursive precedent to reuse"
  evidence: src/cairn/viz/query.py:55 — "def get_impact_graph(conn:
    sqlite3.Connection, name: str, max_depth: int = 3) -> Dict:" with
    max_depth used 3 times in the body
  status: DONE
  verify: awk '/^def get_impact_graph/,/^def get_deps_graph/' src/cairn/viz/query.py | grep -c max_depth
  gap: its walk is impact-shaped (callers-out); expansion is
    callers+callees — extract/share the walk, don't copy it

item Q6: "Graph view renders honest truncated counts — the FR-005 pattern to keep"
  evidence: src/cairn/dashboard/templates/graph.html — "{{ graph.metadata.get(
    \"node_count\", graph.nodes | length) }} nodes ... {% if
    graph.metadata.get(\"total\") is not none %}, {{ graph.metadata.total }}
    in the full result {% endif %}"
  status: DONE
  verify: grep -n "node_count\|metadata.total" src/cairn/dashboard/templates/graph.html
  gap: expansion must update shown counts and keep the truncation notice true

item Q7: "Symbol-name lookup machinery that exists (FTS) — candidate source"
  evidence: src/cairn/graph/schema.py:112 — "-- same pattern
    build_runs/tool_metrics used" near the FTS block; the MCP layer's
    search_symbols rides FTS5 (AGENTS.md tool-quirks table documents its
    matching behavior)
  status: DONE
  verify: grep -rn "fts\|FTS" src/cairn/graph/schema.py | head -5
  gap: the dashboard data layer has no symbol-name search yet — a
    candidates query (exact-name matches with file/kind) is the minimal new
    surface; FTS prefix search is optional garnish

item Q8: "Project scale — the friction number"
  evidence: store query this session — symbols total: 2674 (repos: cairn),
    files: 253, edges: 15120
  status: DONE
  verify: sqlite3 ~/.cairn/71e4dcfee8d29b5a/.kg "SELECT COUNT(*) FROM symbols"
  gap: None — ~2,700 symbols grounds the spec's friction claim
```

## Supporting evidence

```
viz/query.py callers (verified by grep this session):
- get_graph in src/cairn/dashboard/data.py:105-126 dispatches all five
  scopes; the CLI viz commands also consume viz_query — any signature
  change to get_symbol_graph must keep its (conn, name, depth) shape or
  update those callers in the same change

vis-network layout capability (verified in the vendored bundle):
- vis-network.min.js ships hierarchical layout among its built-in layout
  options; no extra dependency is needed for FR-004's toggle
```

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
