# Phase: Competitive Gap Analysis & Performance Hardening

- **Status:** planned (not started)
- **Date drafted:** 2026-08-15
- **Code state baseline:** v0.10.0 @ `7e90628` (main)
- **Companion docs:** [plan.md](plan.md) · [task.md](task.md)

## 1. Motivation

Cairn's stated positioning is "the verifiable memory of your codebase for AI
agents" (`README.md`). Two 2026 competitors now define the performance and
freshness expectations of that category, and both dwarf cairn's traction:

| | cairn | colbymchenry/codegraph | Graphify-Labs/graphify |
|---|---|---|---|
| Stars | ~small (pre-1.0 beta) | 66,419 (created 2026-01-18) | 106,301 (created 2026-04-03) |
| Storage | SQLite `.kg` (graph+FTS5+vec0+telemetry, one file) | SQLite `.codegraph/codegraph.db` (WAL+FTS5) | `graphify-out/graph.json` (512 MiB cap; optional Neo4j/Falkor push) |
| Parsing | Python tree-sitter, 14 langs, full reparse per file | Rust tree-sitter kernel (20 langs native) + portable engine, 34+ langs | Python tree-sitter, ~36-40 grammars |
| Freshness | boot-time catch-up only (`[watch]` extra unshipped by default) | OS-native watchers, ~300 ms save→sync, on by default | `--update`/`--watch`/git hooks + graph.json union merge driver |
| Semantic search | 3-stage hybrid (bge-m3 + BM25 + RRF) + optional CrossEncoder rerank | **none** (structural + FTS5 only) | none for code (LLM pass for docs/media only) |
| Published agent benchmarks | none (perf suite is internal) | 7-repo Claude study: 88% fewer tool calls, 53% faster, 62% fewer tokens | LOCOMO/LongMemEval memory benchmarks (recall@10 0.497 vs mem0 0.048) |
| Edge verifiability | exact/ambiguous/unresolved labels; 82% fuzzy false-positive study | heuristic-edge tags; known false-edge bugs (#1536, #1537, #1545) | EXTRACTED vs INFERRED confidence tags |

Sources: [codegraph README](https://github.com/colbymchenry/codegraph) ·
[graphify README](https://github.com/Graphify-Labs/graphify) · GitHub API
snapshot 2026-08-15. Competitor numbers are vendor-claimed unless an issue
number is cited.

### Where cairn leads (defend, don't rebuild)

1. **Verification contract**: resolution labels + the precise-vs-fuzzy study
   (`docs/benchmarks.md`: 82% of fuzzy hits on common names are collision
   noise). Codegraph ships the *opposite* — name-based resolution with known
   false-edge classes.
2. **Semantic layer**: full hybrid retrieval (bge-m3 vectors + BM25 + RRF
   fusion, `graph/semantic.py` `semantic_search`) plus optional rerank
   (`graph/reranker.py`). Neither competitor has local semantic search over
   code.
3. **Memory/tribal knowledge + deterministic critic**: no competitor ships an
   LLM-free critic gate or OKF knowledge tree.
4. **Degradation telemetry**: `ann_fallback`, `lock_contention`,
   `embed_flush_stalled` events with per-site once-warning
   (`telemetry/events.py`, `graph/schema.py` `note_contention`).

### Where cairn lags (this phase's scope)

| # | Gap | Evidence (cairn code) | Competitor bar |
|---|-----|----------------------|----------------|
| G-1 | Multi-hop queries re-query per visited node | `graph/traversal.py` `impact_analysis` runs recursive Python DFS calling `get_callers` once per visited symbol; `trace_flow` same pattern | codegraph: impact via pre-indexed graph, ~4 s single-file re-sync end-to-end |
| G-2 | A precomputed closure table exists but is **never read** | `graph/dataflow.py` `build_transitive_closure` writes `transitive_edges` (depth 3); repo-wide grep shows no query path reads it back — only the builder's own iterative self-join | n/a — pure internal dead weight (~write cost paid on every build/update for zero reads) |
| G-3 | Derived indexes fully rebuilt on every incremental update | `graph/incremental.py` `_rebuild_derived_indexes` rebuilds dataflow + full transitive closure after each `cairn update` | codegraph syncs only changed files (~0.3–0.4 s on 4k–27k-file repos) |
| G-4 | ANN index has no incremental sync | `graph/ann_index.py` `rebuild_index` docstring: "wholesale rebuild from the `embeddings` table rather than keeping the vec0 table incrementally in sync" | graph-rag competitors use Qdrant/pgvector with live upserts |
| G-5 | Every MCP tool call opens/closes a fresh SQLite connection | `mcp_server/tools_graph.py` tool bodies: `conn = _conn()` … `finally: conn.close()`; no pool | codegraph runs a resident daemon |
| G-6 | No live file watching by default | `graph/watcher.py` docstring: "A long-running `cairn serve` process does NOT see source edits made after it started"; `[watch]` extra (`watchdog>=3.0`) exists in `pyproject.toml` but serve boots only run boot-time catch-up | codegraph: native FSEvents/inotify watchers on by default, ~2 s debounce |
| G-7 | Full reparse even for one changed file | `parsers/python_parser.py` `parse` reads whole file, `self._parser.parse(source)` — no `old_tree` incremental API use anywhere | codegraph Rust kernel; graphify AST-only git-hook rebuilds |
| G-8 | No agent-effort benchmark (the metric category buyers compare on) | `docs/benchmarks.md` perf/scaling tables are explicit `_fill_` placeholders; `bench/perf_suite.py` measures latency, not agent tool-call/token counts | codegraph publishes 88%/53%/62% with methodology; graphify publishes LOCOMO/LongMemEval |

## 2. Items and "Done when"

Each item below appears verbatim in [plan.md](plan.md) and [task.md](task.md).

### PERF-1 — Serve multi-hop queries from the closure table

Make `transitive_edges` a first-class read path so `impact_analysis` and
`get_dataflow` answer from the precomputed matrix instead of per-node DFS.

- **Done when**: `impact_analysis` answers depth ≤ 3 queries from
  `transitive_edges` (falling back to DFS for deeper/fuzzy queries), and
  `bench/perf_suite.py` shows ≥ 3× lower p95 for `impact_analysis` on the
  default corpus with identical result sets (golden-file parity check).

### PERF-2 — Batched frontier traversal for the DFS fallback

Replace the one-query-per-visited-symbol loop with one batched `IN`-list query
per depth level.

- **Done when**: `traversal.py` `impact_analysis`/`trace_flow` issue at most
  `max_depth` queries per call (not one per visited symbol), verified by a
  query-counting test double, with golden-file result parity.

### PERF-3 — Incremental derived-index maintenance

Stop rebuilding dataflow + transitive closure from scratch on every
`cairn update`.

- **Done when**: `incremental.py` touches only rows whose transitive reach
  changed (delete-by-affected-source + re-extend), and `bench/scaling_suite.py`
  shows single-file update cost ≤ 10% of full-build cost at the 1000-file size
  point.

### PERF-4 — Incremental vec0 maintenance

Keep the sqlite-vec `vec0` tables in sync per embedding write instead of
wholesale rebuild.

- **Done when**: upserting one embedding updates the ANN index without a
  full `rebuild_index`, the rowid-stability contract documented in
  `graph/embeddings.py` (ON CONFLICT … DO UPDATE preserves rowid) still holds,
  and `ann_fallback` telemetry does not regress.

### PERF-5 — Connection reuse in the MCP server

Stop paying connect+PRAGMA+close per tool call in the stdio/SSE server.

- **Done when**: the server process reuses connections per workspace with
  correct invalidation on `CAIRN_DB` re-point, and the perf suite shows ≥ 20%
  lower p50 for `find_definition`/`search_symbols` in server-mode benchmarks,
  with `lock_contention` event count unchanged (no new writer contention).

### FRESH-1 — Live file watching as the default serve behavior

Ship the watcher (currently an optional `[watch]` extra that serve never
starts) so `cairn serve` sees edits without restart, matching codegraph's
default.

- **Done when**: with `watchdog` installed, `cairn serve` (stdio and SSE)
  debounces file saves (≤ 2 s) into `incremental.reindex_paths`, the
  staleness banner disappears for watched workspaces, and without `watchdog`
  behavior is exactly today's boot-time catch-up.

### FRESH-2 — Incremental tree-sitter reparse

Use tree-sitter's `parse(bytes, old_tree)` with cached trees instead of full
reparse per changed file.

- **Done when**: the parser layer caches the last tree per file (bounded LRU),
  reparses changed files incrementally, and a parse-parity test proves symbol
  extraction is byte-identical to full reparse on a mutation corpus.

### EVID-1 — Agent-effort benchmark harness

Publish an apples-to-apples agent-effort benchmark (tool calls, tokens, task
time) so cairn's claims are evidence, not positioning.

- **Done when**: `bench/` gains an agent-bench runner executing a fixed task
  set against a pinned repo snapshot with grep/read-only exploration as
  control, reporting tool-calls/tokens/time with medians over ≥ 3 runs, and
  `docs/benchmarks.md`'s `_fill_` placeholders for it are replaced with
  measured numbers and methodology.

## 3. Scope

**In scope:** the eight items above; benchmark docs; telemetry for new paths.

**Out of scope (explicitly):**
- Rewriting the parser layer in Rust/native kernel (codegraph parity play
  with unbounded surface; revisit only if G-7's incremental parse is
  insufficient).
- Adding vector-free GraphRAG, Leiden communities, or god-node detection
  (graphify features) — cairn's semantic layer already covers retrieval;
  community analytics is a separate roadmap decision.
- SCIP expansion, new languages, multi-repo federation.
- Any storage-backend change beyond SQLite (a Postgres backend and a
  cloud/multi-service decomposition were scoped in earlier phase drafts and
  removed from the roadmap on 2026-08-16; SQLite stays the only backend).

## 4. Non-goals & risks

| Risk | Mitigation |
|------|------------|
| Closure-table reads change result shapes (dedup/ordering) vs DFS | Golden-file parity tests before/after; keep DFS as fallback path for fuzzy/deep queries |
| `transitive_edges` is depth-3 only; deep impacts silently differ | Depth-aware routing: use table for ≤ 3, DFS beyond; `truncated` flag preserved |
| Watcher + writer contention on SQLite (single-writer `build_lock`) | Debounce + batch reindex under existing `build_lock`; reuse `note_contention` telemetry to detect regressions |
| Incremental tree edits shift tree-sitter node ids used by symbol extraction | Parity corpus test (FRESH-2) gating the switch; fallback to full parse on any mismatch |
| Connection reuse introduces cross-thread SQLite misuse (`check_same_thread` defaults) | Pool keyed by (workspace, thread) or a single connection + lock, matching the `_FLUSH_LOCK` serialization pattern already proven in `telemetry/sink.py` |
| Benchmark numbers invite unfavorable comparison before gaps close | Ship EVID-1 with honest methodology; sequence after PERF-1..3 land (see plan.md) |

## 5. Dependencies

- Standalone phase; **blocks nothing**. (Earlier drafts cross-linked
  cloud-simulation and postgres-backend phases — removed from the roadmap
  on 2026-08-16, so their gate relationships no longer exist.)
- FRESH-1 depends on `incremental.reindex_paths` remaining transactional
  (already true: explicit BEGIN/COMMIT).
