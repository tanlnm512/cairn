# Survey: context-pack

**Created**: 2026-09-16 | **Baseline**: codex/docs-level-up-specs @ 1af3b35
The survey node's output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item S1: "Multi-hop structure is precomputed"
  evidence:   src/cairn/graph/dataflow.py:7-9 `Also owns the `transitive_edges` closure table: build_transitive_closure() materialises multi-hop caller→callee reachability to a fixed depth, and impact_from_closure() answers ancestor ("who reaches this symbol") queries from it in one indexed statement`; dataflow.py:28 `CLOSURE_MAX_DEPTH = 4`
  status:     DONE
  verify:     sed -n '1,30p' src/cairn/graph/dataflow.py
  gap:        closure is depth-capped at 4; no centrality ranking over it

item S2: "repo_map ranks by degree but has no task relevance or budget"
  evidence:   src/cairn/graph/repo_map.py:1 `"""Deterministic repository orientation projections over stored graph rows."""`; :10-11 `DEFAULT_CLUSTER_CAP = 16` / `DEFAULT_HUB_CAP = 3`; :21-22 `_SymbolRow(... incoming: int, outgoing: int)`
  status:     DONE
  verify:     sed -n '1,25p' src/cairn/graph/repo_map.py
  gap:        no semantic seeding, no token budget, no per-task selection

item S3: "Token accounting precedent is shared bench↔dashboard"
  evidence:   src/cairn/bench/agent_suite.py:58 `CHARS_PER_TOKEN = 4`; src/cairn/dashboard/tokenizer.py:11 `the zero-dependency chars/4 heuristic — the same ``CHARS_PER_TOKEN`` constant the bench suite uses, so bench and dashboard numbers stay comparable`
  status:     DONE
  verify:     grep -n "CHARS_PER_TOKEN" src/cairn/bench/agent_suite.py src/cairn/dashboard/tokenizer.py
  gap:        no pack-level budget fitter exists

item S4: "Semantic seed with lexical fallback exists"
  evidence:   src/cairn/graph/semantic.py imports `from .lexical import search_symbols, search_symbols_terms`; semantic.py:195 documents the `FTS fallback` path when vectors are absent; search_pipeline.py stages the hybrid pipeline
  status:     DONE
  verify:     grep -n "fallback" src/cairn/graph/semantic.py | head -5
  gap:        no composition of seed → expand → rank → fit into one output

item S5: "Compass + memory readers exist for pack enrichment"
  evidence:   src/cairn/mcp_server/tools_compass.py:34 `def get_compass(module: str) -> str:`; src/cairn/memory/promotion.py:242 `def search_memory(`
  status:     DONE
  verify:     grep -n "def get_compass" src/cairn/mcp_server/tools_compass.py
  gap:        not composed into any pack emitter
```

## Supporting evidence
No `cairn pack` command exists (src/cairn/cli/ has no pack module). The bench suite's
fit measurement would extend `bench/agent_suite.py`, whose task objects already carry
per-arm token/tool-call accounting.

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
