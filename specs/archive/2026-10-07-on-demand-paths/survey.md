# Survey: on-demand-paths

**Created**: 2026-10-04 | **Baseline**: 0.21.2 @ ae4b027
The survey node's output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item S1: "Closure materialization runs unconditionally in `cairn build` and `cairn import-scip`"
  evidence:   src/cairn/cli/core.py:build_transitive_closure — imported at :355, called at :368 and :372 (build), :493/:495 (import-scip)
  status:     DONE
  verify:     grep -n "build_transitive_closure" src/cairn/cli/core.py
  gap:        this is today's behavior; demotion (S2) changes it

item S2: "No opt-in flag exists; `cairn build` has no closure parameter"
  evidence:   src/cairn/cli/core.py:282 `def build(repo, workspace, db, verbose, staging, lsp):`
  status:     TODO
  verify:     grep -n "with-closure" src/cairn/cli/core.py   (no match = flag absent)
  gap:        add --with-closure to build + import-scip; demote both call sites to the flag arm

item S3: "Every non-impact closure reader has a verified live-graph fallback"
  evidence:   src/cairn/graph/traversal.py:224 `if can_use_closure and closure_available(conn):` (index mode only engages when materialized)
              src/cairn/pack.py:303 `weight = _closure_centrality if closure_available(conn) else _structural_indegree`
              src/cairn/skillgen/ranking.py:38 `def _rank_from_closure(` with :59 `def _rank_from_degrees(` fallback
  status:     DONE
  verify:     python3 -m pytest tests/test_traversal_parity.py -q
  gap:        none — closure-free stores already traverse live

item S4: "Incremental update skips closure maintenance when the closure is absent"
  evidence:   src/cairn/graph/incremental.py:465 `if pre["closure_built"] and pre["dataflow_built"]:`
              src/cairn/graph/incremental.py:653 `"closure_built": _has_rows("transitive_edges"),`
  status:     DONE
  verify:     grep -n "closure_built" src/cairn/graph/incremental.py
  gap:        none — demotion rides this existing probe

item S5: "Taint ships a reusable depth-capped BFS with parent-chain reconstruction and fuzzy tier"
  evidence:   src/cairn/graph/taint.py:209 `def _paths_from_entry(`, :261 `def _hop_chain(`, :275 `def _exact_neighbors(`, :297 `def _fuzzy_neighbors(`, :100 `_IN_CHUNK = 400`
  status:     DONE
  verify:     python3 -m pytest tests/ -k taint -q
  gap:        endpoints are registry-name-set parameterized; the path query needs a thin symbol-id-seeded variant

item S6: "Taint CLI establishes the endpoint-pattern contract to mirror"
  evidence:   src/cairn/cli/taint.py:17 `"--from",` :23 `"--to",` :39 `def taint(db, from_pattern, to_pattern, fuzzy, max_depth):`
  status:     DONE
  verify:     cairn taint --help
  gap:        `cairn path` does not exist yet (the point of this spec)

item S7: "No MCP path tool; tool count pinned at 25"
  evidence:   src/cairn/mcp_server/server.py:49 `_EXPECTED_TOOL_COUNT = 25` ; :156 `def verify_tool_count() -> None:`
  status:     TODO
  verify:     grep -n "_EXPECTED_TOOL_COUNT = " src/cairn/mcp_server/server.py
  gap:        add `path` tool in tools_graph.py; bump _EXPECTED_TOOL_COUNT 25→26

item S8: "impact DFS reports first-found depths — no min-depth tracking"
  evidence:   src/cairn/graph/traversal.py:261 `def traverse(sym_id: str, sym_name: str, depth: int):`
  status:     TODO
  verify:     grep -n "def traverse" src/cairn/graph/traversal.py   (inspect body: no min-distance map)
  gap:        track minimum distance per reached symbol so FR-005 holds on closure-free stores

item S9: "Behavior pins that must stay green or be explicitly updated"
  evidence:   tests/test_scaling_gate.py:69 `def _assert_closure_budget(point) -> None:` :89 `def test_closure_budget_gate_at_1000_files(...)`
              tests/test_traversal_parity.py:284 `def test_index_mode_covers_dfs_results(corpus_db):` :304 `def test_index_mode_depths_are_shortest_paths(corpus_db):` :316 `def test_index_mode_falls_back_on_seed_cycles(corpus_db):`
              tests/test_incremental_derived.py:702 `def test_maintain_transitive_closure_matches_full_rebuild(fresh_db):`
  status:     DONE
  verify:     python3 -m pytest tests/test_scaling_gate.py tests/test_traversal_parity.py tests/test_incremental_derived.py -q -m "not infra"
  gap:        parity pins re-point at the DFS min-depth contract when the closure is absent
```

## Supporting evidence

- Closure table: src/cairn/graph/schema.py:420 `CREATE TABLE IF NOT EXISTS transitive_edges (` — additive, indexes at :427/:428/:431.
- Depth bound: src/cairn/graph/dataflow.py:17 `CLOSURE_MAX_DEPTH = 4`; incremental maintenance src/cairn/graph/dataflow.py:497 `def maintain_transitive_closure(`; availability probe :602 `def closure_available(`.
- Structural edge kinds the path walk traverses: src/cairn/graph/traversal.py:12 `STRUCTURAL_EDGE_KINDS: Tuple[str, ...] = ("calls", "call", "extends", "implements")`.
- MCP tool surface: src/cairn/mcp_server/tools_graph.py carries the `@mcp.tool` defs (e.g. :432 `def explore(query: str) -> str:`); FastMCP singleton at src/cairn/mcp_server/_server_core.py:68.
- Closure internals (unchanged by this spec): src/cairn/graph/dataflow.py:389 `def _closure_rows(`, :293 `class _ScopedClosureGraph:`, :617 `def impact_from_closure(`, :680 `def closure_has_seed_cycle(`.

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
