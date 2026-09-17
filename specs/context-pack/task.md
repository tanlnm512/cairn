# Tasks: context-pack

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.
**Before-audit**: passed @ b26db94 (2026-09-17: check.py 0 fail; suite 3534 green at b26db94; clean tree; branch feat/context-pack; 0/11 pre-done; C-01..C-04 reviewed)

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1 | 4 | 4 |
| 2 | 2 | 2 |
| 3 | 3 | 3 |
| 4 | 2 | 2 |
| **Σ** | 11 | 11 |

## Phase 1: Pack skeleton (FR-001, FR-005)
<!-- Checkpoint: `cairn pack --task "<real task>" --budget 2000` emits a single
     markdown block with reported count ≤ budget; second run is byte-identical
     (`diff`); manual relevance eyeball on one bench task. -->
- [x] T001 [P] Create the pack pipeline module: item model, cost hook, seed stage — `src/cairn/pack.py` (FR-001, FR-005)
  - done 2026-09-17 — pytest tests/test_pack.py — 49 passed incl. seed-union fix round (fit 1.00, D-012)
  - New file composing existing surfaces only; edits nothing existing; zero new dependencies (D-001, D-011).
  - Item model: one frozen dataclass per content block — fields `kind`, `rank`, rendered text, token cost. Kind vocabulary fixed here: `source`, `blast-radius`, `compass`, `memory` — covering all FR-003 content kinds so later stages stay content-kind-agnostic (plan M1 contract; consumed by T002, T003, T005, T006, T009, T010).
  - Entry point `build_pack(conn, bundle, task, budget)` — the CLI and bench arm both call it. Empty or whitespace-only task is rejected with a clear message and non-zero exit (TC-009).
  - Seed: embeddings-present probe picks `semantic_search(conn, task, limit=N, rerank=False)` — rerank pinned off so no env-gated cross-encoder runs in the pack path (D-002); absent rows fall back to `search_symbols(conn, task, limit=N)` (S4; TC-002).
  - Cost hook: per-item `estimate_tokens` from `cairn.dashboard.tokenizer` (tokenizer.py:65; shared chars/4 precedent, D-007).
  - Verify-first: `grep -n "fallback" src/cairn/graph/semantic.py | head -5`; `grep -n "def estimate_tokens" src/cairn/dashboard/tokenizer.py`.
- [x] T002 Add capped one-hop precise expansion to the pipeline (after T001) — `src/cairn/pack.py` (FR-001)
  - done 2026-09-17 — pytest tests/test_pack.py — expansion tests green (precise one-hop, cap 8)
  - Union each seed with its precise 1-hop `get_callers` (traversal.py:68) + `get_callees` (traversal.py:124) neighbors, per-seed capped, joined back to symbol rows. One hop only: multi-hop reach enters via the rank stage (T005) and the per-symbol depth-2 summary (T007), both closure reads (D-003).
  - Consumes from T001: the `build_pack` stage sequence and the item model (kind, rank, text, cost).
  - Tests: TC-003 — the block reaches definitions the task text never names, via caller/callee links.
- [x] T003 Add the markdown emitter and header token accounting (after T002) — `src/cairn/pack.py` (FR-001, FR-005)
  - done 2026-09-17 — pytest tests/test_pack.py — emitter fixed-point header accounting green
  - One markdown block: header (task, budget, `active_tokenizer_mode()` at tokenizer.py:60, token count), symbol sections in rank order. Sorted keys/order throughout; budget checks round up (D-007); no LLM, no network.
  - Determinism contract is per-build, not per-rebuild: env-gated retrieval is pinned in T001; a near-tied seed may swap between graph rebuilds.
  - Consumes from T001: item model + cost hook; from T002: the expanded pool.
  - Tests: TC-001 groundwork — single block, reported token count greater than 0.
- [x] T004 Add the `cairn pack` CLI command and register it (after T003) — `src/cairn/cli/pack.py` new + one import line in `src/cairn/cli/__init__.py` (FR-001, FR-005)
  - done 2026-09-17 — pytest tests/test_cli_pack.py — 6 passed; TC-009/010 usage errors proven
  - Click `@main.command()` module mirroring the `src/cairn/cli/map.py` shape: `--task`, `--budget`, `--db`, `--knowledge`, `--json`; opens the DB read-only, closes in `finally`; delegates to `build_pack(conn, bundle, task, budget)`.
  - Registration is import-side-effect only in the `cli/__init__.py` import block — a missed line means a silently absent command.
  - Verify-first: `sed -n '1,70p' src/cairn/cli/map.py`. Verify: `cairn pack --help`; `cairn --help` lists pack; `cairn pack --task "fix the retry backoff in ApiFactory" --budget 2000` twice, `diff` byte-identical (phase checkpoint).
  - Tests: TC-001, TC-011 (deterministic, offline, no LLM — dead-proxy rerun), TC-013 (concurrent runs identical).

## Phase 2: Rank + budget fit (FR-002, FR-004)
<!-- Checkpoint: `cairn pack --budget 50` (impossible) degrades gracefully with
     dropped counts; output order follows centrality — spot-check the top symbol
     via `cairn knowledge impact` (exact invocation unknown — verify). -->
- [x] T005 [P] Rank the expanded symbol set by structural centrality — `src/cairn/pack.py` (FR-002)
  - done 2026-09-17 — pytest tests/test_pack.py — closure-gated ranking; real-store order verified 0 violations
  - `centrality(symbol) = COUNT(DISTINCT source_id) FROM transitive_edges WHERE target_id = ?`, one indexed statement per candidate — covered by `idx_transitive_target_id` (schema.py:407) — gated on `closure_available()` (dataflow.py:576). Closure absent → fallback to direct in-degree from `edges` (D-004; no schema or builder change — plan A2 holds).
  - Order contract consumed by T006: `(-is_seed, -centrality, qualified_name)` — task relevance first, then structural weight; fully deterministic.
  - Verify-first: `grep -n "idx_transitive_target_id" src/cairn/graph/schema.py`; `grep -n "def closure_available" src/cairn/graph/dataflow.py`; `cairn knowledge impact --help` (CLI form of impact analysis — unknown — verify, plan A4).
  - Tests: TC-007 — the widely-relied-upon item outranks the peripheral one.
- [x] T006 Add the budget fitter with centrality-ordered graceful degradation (after T005) — `src/cairn/pack.py` (FR-004)
  - done 2026-09-17 — pytest tests/test_pack.py — fitter tests; TC-007/008/010 proofs PASS
  - Render every block, cost each with `estimate_tokens` (round up, conservative); reserve compass + memory sections first, then admit symbol blocks in T005's rank order until the budget is exhausted; the tail — lowest centrality — drops first; report kept and dropped counts per class: symbols / compass blocks / memories (D-009; AC2).
  - Non-positive budget (`--budget 0`, negative) is an input error: clear message naming the invalid budget, non-zero exit (TC-010).
  - Consumes from T005: the `(-is_seed, -centrality, qualified_name)` order; from T001: the per-item cost hook.
  - Verify: `cairn pack --task "retry backoff policy with callers and past mistakes" --budget 50` exits 0, reports a dropped count greater than zero, block well-formed with no cut-off fragments (phase checkpoint).
  - Tests: TC-007, TC-008, TC-010.

## Phase 3: Enrichment content (FR-003)
<!-- Checkpoint: pack on a module with compass + memory coverage shows all four
     FR-003 content kinds within budget; byte-identical rerun re-checked. -->
- [x] T007 [P] Create the enrichment module: source trimmer + per-symbol blast-radius line — `src/cairn/pack_enrich.py` (FR-003)
  - done 2026-09-17 — pytest tests/test_pack_enrich.py — source trimmer + blast line green
  - New file; reads existing surfaces only; imports no pack module and never `cairn.mcp_server` (D-001, D-006; wave-B file disjointness with phase 2).
  - Trimmer: slice stored `line_start`/`line_end` spans (schema.py:42), per-symbol line cap, deterministic cut — signature plus key body, never a full dump — following the `_read_source_spans` slice-and-cap pattern at `src/cairn/graph/explore.py:21` (TC-006).
  - Blast-radius one-liner: `impact_analysis(conn, name, max_depth=2, seed_id=<row id>, limit=K)` (traversal.py:161) — depth 2 is inside index-mode eligibility, so it rides the closure when built and falls back to DFS otherwise (S1); `seed_id` pins the exact symbol row — without it a common name resolves ambiguously; cap the render or one hub symbol's line eats the budget (D-005).
  - Interface consumed by T009 — plain strings plus kind labels from T001's vocabulary (`source`, `blast-radius`): `render_source(conn, symbol_id, line_cap) -> str`; `blast_radius_line(conn, qualified_name, symbol_id, limit) -> str`.
  - Verify-first: `grep -n "line_start" src/cairn/graph/schema.py`; `sed -n '21,95p' src/cairn/graph/explore.py`; `grep -n "def impact_analysis" src/cairn/graph/traversal.py`.
- [x] T008 Add compass-excerpt and top-3 memory picks to the enrichment module (after T007) — `src/cairn/pack_enrich.py` (FR-003)
  - done 2026-09-17 — pytest tests/test_pack_enrich.py — 21 passed incl. compass/memory picks
  - Compass excerpts read from OKFBundle — the read pattern behind `get_compass` (tools_compass.py:34) — deduped per module, keyed by the symbol's file path prefix (D-006, D-008).
  - Top-3 memories via `search_memory(conn, bundle, task)` (promotion.py:242); kind label `memory`.
  - Empty coverage → sections omitted, never rendered empty — FR-003's "where present".
  - Interface consumed by T009: `compass_excerpt(bundle, file_path) -> str | None`; `memory_picks(conn, bundle, task, k=3) -> list[str]`.
  - Verify-first: `grep -n "def get_compass" src/cairn/mcp_server/tools_compass.py`; `grep -n "def search_memory" src/cairn/memory/promotion.py`.
  - Tests: TC-005 — zero memories → no memories section, other kinds unaffected.
- [x] T009 Wire enrichment into the pipeline and emitter; wave-C integration of rank × enrichment (after T006 + T008) — `src/cairn/pack.py` (FR-003, FR-004)
  - done 2026-09-17 — pytest 90 green across pack suites; TC-004/005/006 proofs PASS; offline determinism
  - Stage 4 calls the T007/T008 renders and wraps them as items via T001's item model; emitter gains the pack-level sections after the symbol sections: `## Compass` (deduped per module) and `## Memories` — omitted when empty (D-008).
  - Integration contract: T006's fit loop must consume enriched item costs — enrichment reserved first, symbol blocks admitted in rank order within budget (plan wave-B → wave-C).
  - Consumes: from T006 the fit loop and dropped-count report; from T007 `render_source` / `blast_radius_line`; from T008 `compass_excerpt` / `memory_picks`.
  - Verify: run against a workspace with compass + memory coverage — all four FR-003 content kinds appear within budget; absent sections omitted; byte-identical rerun (phase checkpoint).
  - Tests: TC-004 (all four kinds for a well-connected symbol), TC-005, TC-006.

## Phase 4: Bench fit-rate arm (scope item; measures FR-001, FR-002, FR-004)
<!-- Checkpoint: bench run reports fit rate ≥0.85 on in-budget packs and token
     fraction vs the grep baseline (runner registered near
     `src/cairn/cli/bench.py:190`; exact invocation unknown — verify). -->
- [x] T010 [P] Add the bench fit-rate task pair to the agent suite — `src/cairn/bench/agent_suite.py` (FR-001, FR-004)
  - done 2026-09-17 — pytest tests/test_agent_suite.py — 7-task suite with fit block; bench pins green
  - Additive 7th task pair (D-010): cairn arm calls `build_pack(conn, bundle, task, budget)` from `src/cairn/pack.py` within a fixed budget; control arm stays the grep/read loop; fit = seeded target symbols present in the in-budget pack (spec target ≥0.85). Task objects already carry per-arm token/tool-call accounting.
  - Additive label only: `compare_agent_reports` (agent_suite.py:521) skips labels absent from the baseline, so existing compare gates are unaffected; keep the suite's pinning discipline (rerank off, hash backend) intact.
  - Consumes from phase 1: the runnable pack pipeline (T001–T004 complete — phase order).
  - Verify-first: `sed -n '521,545p' src/cairn/bench/agent_suite.py`; `sed -n '1,15p' src/cairn/bench/agent_suite.py`.
  - Verify: run the agent suite; the report carries the pack arm's tokens.
- [x] T011 Run the assembled bench arm and record the fit-rate numbers (after T009 + T010) — measurement run over `src/cairn/bench/agent_suite.py` as registered near `src/cairn/cli/bench.py:190`; no product file changes (FR-001, FR-002, FR-004)
  - done 2026-09-17 — tech-spec § T011 table — fit 1.00 post-fix (was 0.50), 99.7% token reduction, deterministic ×2
  - Wave-C final measurement on the assembled pipeline — rank (T005) + enrichment (T009) + fit (T006); fit-rate numbers are only meaningful on the whole pipeline (plan M2×M3 → M4-final).
  - Report must carry: pack-arm fit rate (target ≥0.85 on in-budget packs), token fraction vs the grep baseline, and the tokenizer mode alongside the numbers (D-007 comparability).
  - Verify-first: `sed -n '186,196p' src/cairn/cli/bench.py` (runner registration; exact invocation unknown — verify).
  - Tests: TC-012 (at-scale timeliness, manual standing verify; audits run the bounded TC-001).

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `- [x]` done + proof note:
      `done <date> — <test/command that proves it>`
- Dropped: `- [ ] ~~T004~~ dropped <date> (D-###)` — never delete the line;
  dropped tasks stay visible with the decision that killed them
- `[P]` = parallelizable (default — no shared files, no upstream task);
  chained tasks note `(after T###)` and name the exact interface they
  consume from their upstream — symbols, signatures, file formats; serial
  runs need a reason, parallel runs need none
- Fix rounds append `(fix <n>/5)` to the entry — the cap survives resume
  only if the count lives here, in the status holder. From round 2 on, an
  implementer's scratch note (what was tried, why it failed) may live at
  `notes/T###.md` — the one file an implementer may write under specs/,
  never read by check.py, never counted as status
- Every task cites its FR-###; tasks with no FR are scope creep — fix the
  spec first
