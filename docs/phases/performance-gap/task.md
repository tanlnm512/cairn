# Tasks: Competitive Gap Analysis & Performance Hardening

Companion: [spec.md](spec.md) · [plan.md](plan.md). Status reflects code
state on v0.10.0 @ `7e90628` (surveyed 2026-08-15), not intent.

## Burndown

| Status | Count |
|--------|-------|
| done | 7 |
| partial | 0 |
| todo | 16 |
| **total** | **23** |

Milestone 1 (query-path wins) landed on `feat/perf-query-path-wins`
(2026-08-15): P1.1-P1.4, P2.1-P2.2, P5.1. Measured: impact_analysis
20.9 -> 0.1 ms p50 (-99.5%); MCP read-conn path 0.826 -> 0.071 ms
(-91.5%).

---

## PERF-1 — Serve multi-hop queries from the closure table

Done when: `impact_analysis` answers depth ≤ 3 queries from
`transitive_edges` (falling back to DFS for deeper/fuzzy queries), and
`bench/perf_suite.py` shows ≥ 3× lower p95 for `impact_analysis` on the
default corpus with identical result sets (golden-file parity check).

- [ ] **P1.1 — Baseline golden files [DONE 2b8de38: handcrafted resolved-edge corpus; synthetic generator's attribute-chain calls never resolve so precise impact pinned empty].** Capture current `impact_analysis` +
      `get_dataflow` outputs for a fixed corpus (reuse `bench/corpus.py`
      seeded generator) into golden files.
      verify: `uv run pytest tests/test_traversal_parity.py -q` (new test
      comparing live output to goldens) passes on unmodified main.
- [ ] **P1.2 — Read-path routing [DONE e2aeafa: impact_from_closure + auto routing; closure depth 4; kind-filtered seeds; idx_transitive_target_id].** In `graph/traversal.py`, route
      depth ≤ 3, resolution=exact queries to a new `transitive_edges` reader
      (sibling of `build_transitive_closure` in `graph/dataflow.py`); keep
      DFS fallback for fuzzy or depth > 3.
      verify: golden-file parity test green; `impact_analysis` docstring
      documents the routing rule.
- [ ] **P1.3 — Depth-truncation honesty [DONE e2aeafa: index depths = shortest distance - 1 (direct caller = 0); truncation exact via limit+1 fetch; tested].** Ensure `truncated` flag semantics
      hold when served from the table (depth-3 cap vs `max_depth=10` DFS).
      verify: unit test asserting `truncated=True` for a depth-5 chain
      queried at the table's limit, `False` from DFS fallback.
- [ ] **P1.4 — Perf gate [DONE 2a9a194: -99.5% p50 vs main baseline, ~200x > 3x gate; no op regressed >15%].** Extend `bench/perf_suite.py` query battery with
      an impact-heavy scenario; record before/after.
      verify: `uv run cairn bench --save baseline.json` on main, then on the
      branch `uv run cairn bench --compare baseline.json` shows ≥3× p95
      improvement on `impact_analysis`; no other op regresses >15%.

## PERF-2 — Batched frontier traversal for the DFS fallback

Done when: `traversal.py` `impact_analysis`/`trace_flow` issue at most
`max_depth` queries per call (not one per visited symbol), verified by a
query-counting test double, with golden-file result parity.

- [ ] **P2.1 — Frontier batching [DONE b133a10 WITH DEVIATION: per-distinct-name memoization instead of level-batched IN-lists -- frontier batching changes DFS visit order/depths (no exact parity possible); memo is order-preserving. Query-count test pins 3 queries vs 21+].** Rewrite `traverse` to collect each depth
      level's frontier and resolve it with one `IN`-list query (extend
      `get_callers` with a names-list variant).
      verify: query-counting connection double asserts ≤ `max_depth` queries
      for a 50-caller chain.
- [ ] **P2.2 — Parity + limits [DONE b133a10: goldens green through memoization; limit/truncated semantics unchanged].** Ordering/dedup identical to golden files;
      `limit=500` truncation preserved.
      verify: golden-file parity test green; `uv run pytest tests/ -q -k
      traversal`.

## PERF-3 — Incremental derived-index maintenance

Done when: `incremental.py` touches only rows whose transitive reach changed
(delete-by-affected-source + re-extend), and `bench/scaling_suite.py` shows
single-file update cost ≤ 10% of full-build cost at the 1000-file size point.

- [ ] **P3.1 — Affected-set computation.** Given changed file ids → symbol
      ids → reverse-reachable source set (one closure query), delete only
      those rows from `dataflow` + `transitive_edges`, then re-extend from
      the changed symbols using `build_transitive_closure`'s per-depth
      self-join restricted to the affected set.
      verify: unit test — update touching file X leaves untouched sources'
      `dataflow` rows row-identical (compare before/after snapshots).
- [ ] **P3.2 — Scaling gate.**
      verify: `uv run cairn bench --suite scaling --sizes 1000` with a
      scripted single-file edit shows update ≤ 10% of full build time.
- [ ] **P3.3 — Correctness sweep.** After N random single-file updates,
      rebuild-from-scratch in a scratch DB and diff `dataflow` +
      `transitive_edges` — must be identical.
      verify: property test (seeded, like `bench/corpus.py`).

## PERF-4 — Incremental vec0 maintenance

Done when: upserting one embedding updates the ANN index without a full
`rebuild_index`, the rowid-stability contract documented in
`graph/embeddings.py` (ON CONFLICT … DO UPDATE preserves rowid) still holds,
and `ann_fallback` telemetry does not regress.

- [ ] **P4.1 — Spike: vec0 replace semantics.** Determine whether
      `vec0` upsert-by-rowid works on the pinned `sqlite-vec>=0.1.0`
      (its docstring claims "no replace semantics"); if not, evaluate
      delete+insert per rowid.
      verify: spike test script + decision note in
      `graph/ann_index.py` docstring; `record_memory(type="decision")`.
- [ ] **P4.2 — Sync-on-write.** Hook the ANN maintenance into the embedding
      upsert path (`graph/embeddings.py`) behind the existing
      `ann_backend_enabled()` gate; wholesale `rebuild_index` remains the
      fallback/recovery path.
      verify: embed one new symbol → `ann_query` returns it without any
      full rebuild; `tests/test_contention_visibility.py` still green.
- [ ] **P4.3 — Recovery.** `cairn doctor` detects drift between
      `embeddings` and vec0 row counts and schedules `rebuild_index`.
      verify: doctor unit test with a deliberately-drifted fixture.

## PERF-5 — Connection reuse in the MCP server

Done when: the server process reuses connections per workspace with correct
invalidation on `CAIRN_DB` re-point, and the perf suite shows ≥ 20% lower p50
for `find_definition`/`search_symbols` in server-mode benchmarks, with
`lock_contention` event count unchanged (no new writer contention).

- [ ] **P5.1 — Read-connection cache [DONE 66bb969: thread-local per-(thread,path) pool, inode-swap reopen, env kill-switch; 11.7x on conn+query path, beats the 20% gate].** Thread-local (or lock-guarded single)
      connection cache in `mcp_server/_server_core.py` `_conn()`, keyed by
      resolved db path; invalidate on path change; honor read-only mode.
      verify: server-mode bench (new harness mode in `bench/perf_suite.py`)
      p50 ≥20% better; monkeypatch test re-points `CAIRN_DB` and asserts
      fresh connection.
- [ ] **P5.2 — Contention guard.** Read connections stay read-only
      (`mode=ro` where applicable); writer paths keep using `_rw_conn()` +
      `build_lock`.
      verify: concurrent `cairn update` + 100 tool calls → zero new
      `lock_contention` events.

## FRESH-1 — Live file watching as the default serve behavior

Done when: with `watchdog` installed, `cairn serve` (stdio and SSE) debounces
file saves (≤ 2 s) into `incremental.reindex_paths`, the staleness banner
disappears for watched workspaces, and without `watchdog` behavior is exactly
today's boot-time catch-up.

- [ ] **F1.1 — Watcher service.** New module (extend `graph/watcher.py`)
      starting a `watchdog` observer when importable; debounce ≤2 s; calls
      `incremental.reindex_paths` under `build_lock`; skips when
      `CAIRN_READ_ONLY`. Graceful no-op (log once) when `watchdog` missing.
      verify: integration test with a temp workspace — save file → poll DB
      until new symbol visible (≤5 s); no-watchdog path asserts boot
      catch-up unchanged.
- [ ] **F1.2 — Wire into serve.** Start/stop in both stdio and SSE paths of
      `mcp_server/server.py` `run()`; update the staleness banner logic in
      `_server_core` to reflect watcher liveness.
      verify: `uv run pytest tests/ -q -k watcher or serve`.
- [ ] **F1.3 — Docs + extra.** Promote `watchdog` guidance in README /
      docs/configuration.md (`[watch]` extra stays optional; behavior is
      additive).
      verify: docs grep for watcher section; `uv run --extra watch pytest -q`.

## FRESH-2 — Incremental tree-sitter reparse

Done when: the parser layer caches the last tree per file (bounded LRU),
reparses changed files incrementally, and a parse-parity test proves symbol
extraction is byte-identical to full reparse on a mutation corpus.

- [ ] **F2.1 — Tree cache.** Per-file `Tree` cache keyed by content hash in
      `parsers/_registry.py` (alongside `get_parser`'s lru_cache); bounded.
      verify: cache unit tests; memory ceiling asserted in
      `bench/scaling_suite.py` peak-memory column (no >15% growth).
- [ ] **F2.2 — Incremental parse + parity corpus.** `parse(bytes, old_tree)`
      path; mutation corpus (edits applied to seeded corpus files) with
      byte-identical symbol extraction vs full reparse.
      verify: parity test green across ≥3 languages (python, typescript,
      kotlin — the parsers with most usage).
- [ ] **F2.3 — Gate decision.** If PERF-1..4 already meet the phase's perf
      gates, record decision to defer F2.3+ to a follow-up.
      verify: `record_memory(type="decision")` note linked from
      docs/benchmarks.md update.

## EVID-1 — Agent-effort benchmark harness

Done when: `bench/` gains an agent-bench runner executing a fixed task set
against a pinned repo snapshot with grep/read-only exploration as control,
reporting tool-calls/tokens/time with medians over ≥ 3 runs, and
`docs/benchmarks.md`'s `_fill_` placeholders for it are replaced with
measured numbers and methodology.

- [ ] **E1.1 — Task set + harness.** 5–8 representative tasks (find symbol,
      blast radius, flow trace, hybrid search) executed via MCP tool calls
      against a pinned snapshot; control arm = grep/read-only tool loop;
      medians over ≥3 seeded runs.
      verify: `uv run python -m cairn bench agent --help` runs; output JSON
      includes tool_calls/tokens/wall_time per task per arm.
- [ ] **E1.2 — Publish.** Replace the `_fill_` agent-effort placeholders in
      `docs/benchmarks.md` with numbers + methodology paragraph (name the
      control arm, seed count, and repo snapshot hash).
      verify: `grep -c "_fill_" docs/benchmarks.md` decreases; docs render.
- [ ] **E1.3 — Record learnings.**
      `cairn memory record decision "agent-effort benchmark methodology"`.

---

## Post-phase hygiene (applies at every milestone)

- `cairn update` after each merged milestone; `cairn doctor` must exit 0.
- `record_memory` for each spike decision (P4.1, F2.3) and any fallback path
  touched.
