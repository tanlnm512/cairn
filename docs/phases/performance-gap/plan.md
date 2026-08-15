# Plan: Competitive Gap Analysis & Performance Hardening

Companion: [spec.md](spec.md) · [task.md](task.md). "Done when" wording is
owned by spec.md and repeated verbatim in task.md.

## 0. Evidence base (Phase-A survey, 2026-08-15)

All citations below were produced by the code survey on v0.10.0 @ `7e90628`:

- `graph/traversal.py` — `impact_analysis` = recursive Python DFS, one
  `get_callers` SQL query per visited symbol, `max_depth=10`, `limit=500`;
  `trace_flow` same pattern with `find_definition` per callee.
- `graph/dataflow.py` — `build_transitive_closure` iteratively fills
  `transitive_edges` to depth 3; **no read path queries it** (only the
  builder's own self-join). `build_dataflow_index` capped at
  `max_symbols=2000`.
- `graph/incremental.py` — `_rebuild_derived_indexes` fully rebuilds dataflow
  + closure after each update; `reindex_paths` is one BEGIN/COMMIT txn per
  file set.
- `graph/ann_index.py` — `rebuild_index` is wholesale; vec0 "has no replace
  semantics" per its own docstring.
- `graph/embeddings.py` — upsert chosen specifically to preserve rowid
  (vec0 keys on `embeddings.rowid`).
- `mcp_server/tools_graph.py` — every tool: `conn = _conn()` … `finally:
  conn.close()`.
- `graph/watcher.py` — boot-time catch-up only; `pyproject.toml` ships
  `watch = ["watchdog>=3.0"]` unused by serve.
- `parsers/python_parser.py` `parse` — full-file read + `parse(source)`, no
  `old_tree`.
- `docs/benchmarks.md` — measured: 1,942 symbols / 11,595 edges / ~4 s build /
  34% exact-pinned on cairn's own repo; perf & scaling tables are `_fill_`
  placeholders; doctrine: "Build times dominate at scale; query times are
  sub-millisecond once the graph is built."
- `bench/perf_suite.py` `run_perf_suite`, `bench/scaling_suite.py`
  (sizes 100–5000, tracemalloc), `bench --compare` regression gate (exit 2 at
  >15%).

## 1. Implementation options

### Option A — Depth-first internals (algorithmic wins only)

Land PERF-1..5 + EVID-1. No freshness work.

- **Pros:** smallest blast radius (pure query-path changes); every item has a
  measurable gate; no new runtime deps; directly attacks the two structural
  inefficiencies the survey found (per-node DFS, dead closure table) and the
  per-call connection cost.
- **Cons:** leaves the biggest *perceived* gap vs codegraph (stale graph until
  restart) untouched; EVID-1 published without freshness parity invites an
  unfavorable benchmark story.
- **Cost:** ~2–3 weeks; no new dependencies.

### Option B — Freshness-first (parity play)

Land FRESH-1 + FRESH-2 + PERF-3 first (watcher, incremental parse,
incremental derived indexes), then internals.

- **Pros:** attacks codegraph's headline differentiator (always-fresh, ~300 ms
  save→sync); user-visible immediately; FRESH-1 is mostly wiring existing
  pieces (`watchdog` extra + `incremental.reindex_paths` + debounce).
- **Cons:** watcher value is capped by PERF-3 — today every watched edit
  triggers a full derived-index rebuild, so freshness work without PERF-3
  multiplies write amplification; FRESH-2 (incremental tree reparse) touches
  every parser and carries the highest regression risk of the whole phase;
  query-latency wins (the closure table) are deferred.
- **Cost:** ~3–4 weeks; adds a runtime dependency (`watchdog`) to default
  installs or an extra.

### Option C — Evidence-first (benchmark, then optimize what it flags)

Build EVID-1 + fill the `_fill_` benchmark tables before touching code; let
the numbers rank PERF/FRESH items.

- **Pros:** eliminates guesswork; produces the marketing asset competitors
  already have; aligns with cairn's verification-first identity (measure,
  then claim).
- **Cons:** the survey already ranks the bottlenecks with high confidence —
  the dead closure table and per-node DFS are proven inefficiencies
  regardless of what a benchmark says; delays user-visible fixes by a full
  benchmark-build cycle; agent-effort benchmarks are noisy and expensive to
  make rigorous (codegraph's methodology took them 7 repos × 4 runs).
- **Cost:** ~1–2 weeks for the harness alone, before any optimization.

## 2. Recommendation: **Option A, immediately followed by FRESH-1 and PERF-3 (A→B hybrid), with EVID-1 interleaved after the first wins**

Rationale:

1. PERF-1 is nearly free value: the closure table is already being **paid for
   on every build and never read**. Routing reads to it is the highest
   ratio of impact-to-risk in the whole phase, and it hardens the exact
   multi-hop story (`impact_analysis`, `get_dataflow`) that agents hit.
2. Freshness without PERF-3 is write-amplification (each watched save → full
   derived rebuild). Sequencing PERF-3 before FRESH-1 makes the watcher both
   cheap and safe. This is the same dependency codegraph solved with
   file-level sync; cairn's equivalent is affected-source-only maintenance.
3. EVID-1 lands mid-phase (after PERF-1..3) so the published numbers include
   the improvements, and its control-arm methodology (grep/read-only
   baseline) is honest from day one.
4. FRESH-2 (incremental tree reparse) is explicitly last and gated on a
   parity corpus: it is the only item whose failure mode is *wrong query
   results*, not just slow ones. If PERF-3 + FRESH-1 bring single-file update
   cost low enough, FRESH-2 may be deferred out of this phase entirely.

## 3. Sequencing (chosen path)

```
Milestone 1 — Query-path wins (week 1-2)
  PERF-1  read impact from transitive_edges (depth<=3) + parity goldens
  PERF-2  batched frontier DFS fallback + query-count test
  PERF-5  server connection reuse + contention guard
  → gate: perf_suite p95 impact ≥3x better; --compare within 15% on the rest

Milestone 2 — Write-path wins (week 2-3)
  PERF-3  incremental derived-index maintenance
  PERF-4  incremental vec0 maintenance (rowid contract test first)
  → gate: scaling_suite single-file update ≤10% of full build @1000 files

Milestone 3 — Freshness parity (week 3-4)
  FRESH-1 watcher on by default (stdio+SSE), staleness banner logic updated
  → gate: save→query-visible ≤2s debounce; no new lock_contention events

Milestone 4 — Evidence (week 4-5)
  EVID-1  agent-effort benchmark + fill docs/benchmarks.md placeholders
  FRESH-2 incremental tree reparse (only if parity corpus green; may defer)
```

Each milestone ends with `cairn update` + `record_memory` for learnings
(workspace convention), and the whole phase ships behind the standard
contribution workflow (branch → pre-commit → PR with audit checklist).

## 4. Verification commands (per milestone gate)

- Perf gates: `uv run cairn bench --save baseline.json` (on main) then
  `uv run cairn bench --compare baseline.json` (exit 0 within 15%) and
  `uv run cairn bench --suite scaling --sizes 100,1000` (PERF-3 gate).
- Parity gates (PERF-1/2, FRESH-2): golden-file tests
  `uv run pytest tests/test_traversal_parity.py -q` (new; see task.md).
- Contention gate (PERF-5, FRESH-1): run the SSE server + concurrent
  `cairn update` and assert zero new `lock_contention` rows in `events`.
- Full suite: `uv run --extra dev pre-commit run --all-files` then
  `uv run pytest -q`.
