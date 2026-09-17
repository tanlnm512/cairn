# Tasks: cross-repo-federation

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.
**Before-audit**: passed @ 61be289 (2026-09-17: check.py 0 fail; suite 3610 green at 61be289; clean tree; branch feat/cross-repo-federation; 0/8 pre-done; C-01..C-04 reviewed)

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1 | 4 | 4 |
| 2 | 1 | 1 |
| 3 | 2 | 2 |
| 4 | 1 | 1 |
| **Σ** | 8 | 8 |

## Phase 1: Federated search end-to-end (FR-001, FR-004)
<!-- Checkpoint: a federated query over fixture stores (2+ indexed, plus 1
     missing, 1 locked, 1 unindexed) returns merged, per-repo-attributed
     results and names every dropped store; survey S1/S3 verify commands
     still pass. -->
- [x] T001 Build the federation core query path — fan-out, RRF fusion, per-repo attribution — in a new `src/cairn/graph/federation.py` (FR-001)
  - done 2026-09-17 — pytest tests/test_federation.py — core fan-out/RRF/attribution green (D-009/D-010 recorded)
  - `federated_search(query, ...) -> FederatedResult`: iterate `_load_registry()` (src/cairn/paths.py:177), resolve each workspace via `resolve_store(workspace)` (paths.py:355 — takes an explicit workspace argument), open a read-only connection per store, call the existing `semantic_search(conn, query, ...)` (src/cairn/graph/semantic.py:714 — already degrades to BM25-only with `provenance="bm25"` when a store lacks embeddings, so the FR-001 lexical fallback needs no new engine, D-007), tag every hit with its repo key, and merge per-store ranked lists with `rrf_fuse` (src/cairn/graph/fusion.py:13) — raw scores are never merged across stores (D-001).
  - Reject an empty or whitespace-only query before any store is touched (core half of TC-012).
  - Verify before implementing: `grep -n "def _load_registry\|def resolve_store" src/cairn/paths.py`
  - Pitfalls: read the registry only through `paths._load_registry()` — module attributes are import-time-bound and the hermetic fixture patches them (tests/test_hermetic_stores_root.py docstring); no eager `cairn.cli` imports (C-04).
  - Tests (C-02 failing-test-first): new tests/test_federation.py fixture suite (hermetic-store precedent: tests/test_hermetic_stores_root.py) — merged hits carry per-repo attribution (TC-001); a lexical-only store still contributes (TC-002).
- [x] T002 Add per-store state classification and the named dropped-store report to the federation core in `src/cairn/graph/federation.py` (FR-004) (after T001)
  - done 2026-09-17 — UnavailableStoreTests 4 TCs green — missing/locked/unindexed named, empty registry clear
  - Classify every registry entry `ok` / `missing` / `locked` / `unindexed` under the dashboard enumerator's never-raise, never-write contract (src/cairn/dashboard/workspaces.py:137 catches `sqlite3.OperationalError` for locked/corrupt stores); every dropped store is named in `FederatedResult` — never silently skipped (D-006); an empty registry returns an explicit no-stores result (TC-010).
  - Consumes from T001: the `federated_search(query, ...) -> FederatedResult` skeleton and its per-store iteration loop in `src/cairn/graph/federation.py`; exact `FederatedResult` field names per T001's dataclass (unknown — verify in T001's implementation).
  - Verify before implementing: `grep -n "OperationalError" src/cairn/dashboard/workspaces.py`
  - Tests: a missing (TC-007), unindexed (TC-008), and locked (TC-009) fixture store each appears named in the report while healthy stores still return attributed results.
- [x] T003 [P] Expose the read-only MCP tool `federated_search` — new `src/cairn/mcp_server/tools_federation.py` plus registration (FR-001) (after T002)
  - done 2026-09-17 — tool registered, 25-tool count swept across 17 files; count-coupled suites 65 passed
  - Thin adapter over T001/T002's `federated_search(query, ...) -> FederatedResult`; decorate with `@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, ...))` matching the existing read-only tools (`FastMCP("cairn")` singleton at src/cairn/mcp_server/_server_core.py:80); register by importing the module in `src/cairn/mcp_server/server.py` (D-003).
  - Bump `_EXPECTED_TOOL_COUNT` 24 → 25 at `src/cairn/mcp_server/server.py:56` — the `verify_tool_count` boot guard asserts at server start, so a forgotten bump fails boot, not tests; update all four exact-count pins found by session grep: tests/test_graft_parity_map.py:587, tests/test_ingest_compat.py:243, tests/test_status_resource_health.py:281, tests/test_mcp_wiki_tool.py:74; optionally extend tests/test_tool_annotations.py `_GRAPH_READ_ONLY_TOOLS` with `federated_search`.
  - Verify before implementing: `grep -n "_EXPECTED_TOOL_COUNT" src/cairn/mcp_server/server.py` and `grep -rn "_EXPECTED_TOOL_COUNT == 24" tests/`
  - Tests: the tool is registered, read-only-annotated, and returns the core's attributed results for a fixture query (agent half of TC-003) in the tests/test_federation.py suite.
- [x] T004 [P] Add the CLI command `cairn federated-search` to `src/cairn/cli/query.py` (FR-001) (after T002)
  - done 2026-09-17 — CLI federated-search; TC-001/002/003 proofs PASS
  - `cairn federated-search "QUERY" [--limit N] [--json]` delegating to T001's `federated_search` — no federation logic outside the core (D-008); prints merged attributed results with repo keys and names every dropped store from the T002 report; a blank query exits with a clear input error before querying (TC-012).
  - Place the command beside `def search(pattern, kind, db, as_json, refresh)`; follow the module's lazy-import style inside the command body.
  - Verify before implementing: `grep -B1 -A3 "^def " src/cairn/cli/query.py | grep "def "` (survey S4)
  - Tests: CLI returns the same merged, attributed results as the T003 tool for the same query (TC-003; needs the T003 tool to exist) in the tests/test_federation.py suite.

## Phase 2: Cross-repo ask (FR-003)
<!-- Checkpoint: `cairn ask --all-repos "<question>"` over fixture stores
     returns one answer with per-repo attribution; single-store `cairn ask`
     output unchanged. Verify: ask fixture tests +
     `grep -n "all-repos" src/cairn/cli/ask_context.py`. -->
- [x] T005 Add the default-off `--all-repos` flag to `cairn ask` in `src/cairn/cli/ask_context.py` (FR-003) (after T002)
  - done 2026-09-17 — ask --all-repos per-repo attribution; TC-006/TC-012 proofs PASS; default byte-identical
  - With the flag set: iterate reachable stores via the T001/T002 core helpers, run `route_query` once per store with that store's conn and knowledge bundle (`route_query(question, conn, bundle)`; imported lazily inside `ask` today — ask_context.py:16 and :22), and compose one answer with per-repo attributed sections in the existing output style; dropped stores are named in the answer (D-004).
  - Default off keeps the single-store ask path byte-identical (FR-005); `def ask(question, db, knowledge, as_json)` at ask_context.py:14 gains only the flag.
  - Verify before implementing: `grep -n "def ask\|route_query" src/cairn/cli/ask_context.py`
  - Plan checkpoint after: `grep -n "all-repos" src/cairn/cli/ask_context.py`
  - Tests: blank all-repos input is rejected before any store is queried (TC-012); ask over fixture stores attributes context per repo (TC-006) in the tests/test_federation.py FederatedAskTests class.

## Phase 3: Shared embedding backend (FR-002)
<!-- Checkpoint: with 2+ stores on a compatible shared backend, the
     federation serves them from one backend instance; a mixed-backend set
     still returns rank-fused attributed results. Verify: shared-backend
     fixture test + Phase 1 federation tests unchanged. -->
- [x] T006 Add the opt-in `--shared-embed` backend-sharing mode to the federation core in `src/cairn/graph/federation.py` (FR-002) (after T002)
  - done 2026-09-17 — shared-embed gate, one embed for stamp-compatible stores; default 2-embed pin green
  - Compatibility test: every reachable store's embedding model stamp is equal via `current_model(corpus)` (src/cairn/graph/embeddings.py:50; stamps `DEFAULT_LOCAL_MODEL = "BAAI/bge-m3"` at :39 and `HASH_MODEL = "hash-256-v1"` at :40); compatible stores get the query embedded once and the vector reused across them; otherwise each store embeds with its own backend and only ranks fuse (D-005). Default off: per-store vectors, rank-level fusion.
  - Verify before implementing: `grep -n "DEFAULT_LOCAL_MODEL\|HASH_MODEL\|def current_model" src/cairn/graph/embeddings.py`
  - Tests: with 2+ compatible stores the federation serves them from one shared backend instance with results attributed exactly as when each store serves its own (TC-004, EmbeddingBackendTests); a mixed-backend set still returns one rank-fused attributed ranking (TC-005 auto anchor).
- [x] T007 Wire `--shared-embed` through both federation surfaces (FR-002) (after T006)
  - done 2026-09-17 — --shared-embed wired CLI+MCP (schema-verified); TC-004 proof PASS
  - Add the option to `cairn federated-search` in `src/cairn/cli/query.py` (T004's command) and a matching parameter on the MCP `federated_search` tool in `src/cairn/mcp_server/tools_federation.py` (T003's tool); pass-through only — the stamp-gated decision lives in the T006 core signature once (D-008).
  - Tests: `--shared-embed` through each surface returns the same attributed results as the default mode on compatible stores (extends the T006 cases).

## Phase 4: Regression gate (FR-005)
<!-- Checkpoint: full existing suite plus all new suites green; survey
     verify commands S1-S4 pass; single-store commands produce unchanged
     output on a single-store workspace. -->
- [x] T008 Run the regression gate — full suite plus survey verify commands green, single-store behavior unchanged (FR-005)
  - done 2026-09-17 — full gate 3631 passed 0 failed; TC-011 SingleStoreBaselineTests green (0 registry iters on defaults)
  - `pytest` full run green including all new suites; behavior pins stay green: tests/test_fusion.py (RRF known-values, disjoint lists, scale-invariance, weights), tests/test_hermetic_stores_root.py, tests/test_cross_repo_namespaces.py, tests/test_dashboard_scale.py, and the four updated tool-count pins from T003.
  - Survey verify commands pass: `grep -n "REGISTRY_FILE\|def register_workspace" src/cairn/paths.py` (S1); `grep -n "def cross_repo_deps\|def _load_namespaces" src/cairn/graph/cross_repo.py` (S2); `grep -rn "RRF\|reciprocal" src/cairn/graph/fusion.py src/cairn/graph/semantic.py | head -5` (S3); `grep -B1 -A3 "^def " src/cairn/cli/query.py | grep "def "` (S4).
  - Single-store commands produce unchanged output on a single-store workspace; the structural `cross_repo_deps` path is untouched (tests/test_federation.py SingleStoreBaselineTests, TC-011).

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
