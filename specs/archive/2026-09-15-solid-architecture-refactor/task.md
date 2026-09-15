# Tasks: solid-architecture-refactor

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.
Already-done sweep: T001-T004 were satisfied by the before-audit baseline run
(`3345 passed, 4 skipped`; `ruff`: 0 findings; `mypy`: clean; runtime
dependencies snapshotted); do not spawn duplicate baseline tasks.
**Before-audit**: passed @ 407498c3250cec462bb7e992c7045a6bb6a8f54e

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 0 | 4 | 4 |
| 1 | 4 | 4 |
| 2 | 3 | 3 |
| 3 | 3 | 3 |
| 4 | 3 | 3 |
| 5 | 3 | 3 |
| 6 | 4 | 4 |
| 7 | 3 | 3 |
| 8 | 3 | 3 |
| **Σ** | 30 | 30 |

## Phase 0: Baseline lock (FR-017, FR-018)
<!-- Checkpoint (plan.md M0): full suite pass count recorded; ruff pyflakes-only
     finding count recorded as FR-018 baseline; mypy clean (HARD gate);
     pyproject.toml runtime-dependency list snapshotted as FR-017 baseline. -->
- [x] T001 [P] Run `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest -q` (full suite) and record the pass count as the closing-gate floor (FR-018)
  - done 2026-09-15 — baseline full suite: 3345 passed, 4 skipped
- [x] T002 [P] Run `uv run ruff check src` (select=["F"], pyproject.toml:184-206) and record the finding count as the FR-018 baseline (FR-018)
  - done 2026-09-15 — baseline uv run ruff check src: 0 findings
- [x] T003 [P] Run `uv run mypy --ignore-missing-imports src` and confirm clean (HARD gate, ci.yml:67-68); name the TC-003 regression `tests/test_build_runs.py::test_build_graph_unsupported_language_continues_indexing` (FR-018)
  - done 2026-09-15 — baseline uv run mypy --ignore-missing-imports src: clean
- [x] T004 [P] Run `sed -n '25,65p' pyproject.toml` and record the current runtime-dependency list as the FR-017 baseline for M8's diff (FR-017)
  - done 2026-09-15 — runtime dependency list snapshotted from pyproject dependencies block

## Phase 1: Parser factory (FR-001, FR-002, FR-003)
<!-- Checkpoint (plan.md M1): new factory type present under src/cairn/parsers/;
     builder.py:798/incremental.py:209 delegate instead of constructing inline;
     factory.get_parser("python") is factory.get_parser("python") pinned by test;
     tests/test_parsers_language_adapter.py -q stays at 9 passed; indexing-path
     regression suite (test_build_graph_*, test_build_inmemory, test_build_runs,
     test_incremental_derived, test_reindex_resolution_invariant) stays green. -->
- [x] T005 [P] Create `src/cairn/parsers/factory.py` with a `ParserFactory`-equivalent type owning `BaseParser` construction, moving the 13-armed lazy-import `if/elif` chain out of `src/cairn/graph/builder.py:68-123` (survey FR-001: 17 parser classes exist, no factory class yet) (FR-001)
  - done 2026-09-15 — created parsers/factory.py; tests/test_parsers_language_adapter.py 9 passed
- [x] T006 (after T005) Move the `_parser_instances` memoization dict (builder.py:46,50,124) into the new factory so repeated resolution returns the same cached instance, and delete the dead, never-populated `PARSERS` dict at builder.py:45 (survey FR-002 gap: caching exists only as an ad hoc dict, not inside a factory abstraction) (FR-002)
  - done 2026-09-15 — factory caching moved; tests/test_parser_factory_cache.py 2 passed
- [x] T007 (after T006) Add `tests/test_parser_factory_cache.py` asserting `factory.get_parser("python") is factory.get_parser("python")` — survey FR-002 gap: "no test pins the 'same cached instance' contract explicitly"; this is test.md's forward TC-002 (FR-002)
  - done 2026-09-15 — tests/test_parser_factory_cache.py pins cached instance; 2 passed
- [x] T008 (after T006) (fix 2/5) Repoint `builder.py:798` and `incremental.py:209` (survey FR-003: the only two non-`def` call sites via `rg -n "get_parser\(" src/cairn/ -g '*.py'`) to import `get_parser` from `src/cairn/parsers/factory.py` directly, while `incremental.py` resolves it through the `builder.get_parser` module attribute to preserve the existing patch seam (FR-003)
  - done 2026-09-15 — builder and incremental use parser factory; parser/index suites 94 passed

## Phase 2: Embedding backend contract & registry (FR-004, FR-005, FR-006)
<!-- Checkpoint (plan.md M2): a contract type (Protocol/ABC) present in
     embeddings.py; `if backend ==`/`elif backend ==` branch count collapses
     outside the registry; full embed-related test file list stays green,
     preserving resolution order/fallback/cache-invalidation/vector-format/
     dimensions/model-identity. -->
- [x] T009 [P] Create `src/cairn/graph/embed_backends.py` defining an `EmbeddingBackend` Protocol/ABC contract and a registry dict, each concrete backend using injected/named-callable adapters around the existing `_embed_hash`/`_embed_openai`/`_embed_server`/`_embed_local` functions left in place in `embeddings.py`; `embed_backends.py` must not import `embeddings.py` (survey FR-004: 0 `class .*Backend|Protocol` hits in embeddings.py today) (FR-004)
  - done 2026-09-15 — EmbeddingBackend registry implemented; embedding suite 187 passed, 1 skipped
- [x] T010 (after T009) Replace all 9 `if backend ==`/`elif backend ==` branch sites in `src/cairn/graph/embeddings.py` (lines 64, 66, 68, 105, 107, 496, 1230, 1232, 1234 — survey FR-005 citation) with resolution through the new registry, leaving none hardcoded in `current_model`, `embeddings_available`, the function at :496, or `_embed` (FR-005)
  - done 2026-09-15 — embedding dispatch registry-owned; no hardcoded backend branches; embedding suite 187 passed
- [x] T011 (after T010) Run the full embed-related regression suite (`tests/test_embedding_model.py tests/test_embed_ladder.py tests/test_embeddings_freshness.py tests/test_embed_cli_adopt.py tests/test_embed_cli_download_model.py tests/test_embed_cli_server_down.py tests/test_embed_commit_tracking.py tests/test_embed_flush_stalled.py tests/test_embedding_backend_quality.py tests/test_embeddings_mv.py tests/test_ensure_semantic_deps.py tests/test_memory_embeddings.py tests/test_semantic_unavailable.py tests/test_update_path_embedding.py`) before and after, since survey marks this file list unknown — verify (not run at survey time); name the TC-011 regression `tests/test_dashboard_app.py::test_unknown_route_preserves_not_found` (FR-006)
  - done 2026-09-15 — full embedding regression suite 187 passed, 1 skipped

## Phase 3: Graph persistence repository seams (FR-015, FR-016)
<!-- Checkpoint (plan.md M3, depends on Phase 1/T005-T008 — same two files,
     adjacent lines in builder.py and the same import/call block in
     incremental.py): new repository class(es) present; direct SQL in
     builder.py drops toward 0; ID generation/transaction/lock machinery
     stays reachable with the same semantics; indexing-path + ANN-index
     regression suite green. -->
- [x] T012 (after T008) Create `src/cairn/graph/repository.py` with one `GraphRepository` class exposing `insert_files`/`insert_symbols`/`insert_imports`/`insert_edges`, each taking the same caller-owned `cur` cursor `insert_parsed_file` already uses (D-002 — one facade, not one class per table; survey FR-015: 0 `class.*Repository|Repository\(` hits today) (FR-015)
  - done 2026-09-15 — GraphRepository facade created; py_compile and persistence surface 140 passed
- [x] T013 (after T012) (fix 1/5) Replace the four inline `INSERT INTO` statements in `insert_parsed_file` (builder.py:836 files, :985 symbols, :995 imports, :1001 and :1181 edges — survey FR-015 citation) with calls to the new `GraphRepository`, keeping `insert_parsed_file`'s row-shaping/ID-generation logic and transaction ownership unchanged (FR-015)
  - done 2026-09-15 — insert paths route through GraphRepository; persistence surface 140 passed
- [x] T014 (after T013) Run the FR-016 preservation surface — indexing-path regression suite plus `tests/test_signal_persistence.py`, `tests/test_ann_incremental.py`, `tests/test_ann_index.py` — to pin existing behavior, transaction boundaries, batching, and recovery semantics—not byte-for-byte database equality; survey flags this as not independently re-run ("no single crash-recovery/batch test was run this session") (FR-016)
  - done 2026-09-15 — indexing/persistence preservation surface 140 passed

## Phase 4: Dashboard modularity (FR-007, FR-008)
<!-- Checkpoint (plan.md M4): app.py line count drops from the 1520-line
     baseline; create_app no longer contains nested handler defs;
     test_dashboard_app.py stays at 157 passed; full 16-file dashboard
     surface (not run at survey time) goes green as this milestone's gate. -->
- [x] T015 [P] Create the `src/cairn/dashboard/routes/` package grouped per D-006's handler-name domain prefixes: `routes/core.py` (landing, workspaces_overview, projects, health), `routes/graph.py` (graph, graph_candidates, graph_suggest, palette_results, graph_neighbors, graph_inspect), `routes/history.py` (history, tokens, chains), `routes/memory.py` (memory, tasks), `routes/knowledge.py` (knowledge_catalog, knowledge_doc, knowledge_graph), `routes/wiki.py` (wiki, wiki_page), `routes/settings.py` (settings, settings_save, embeddings_status, database) (FR-007)
  - done 2026-09-15 — dashboard routes package created with seven domain controllers
- [x] T016 (after T015) (fix 1/5) Move the 24 handler bodies (survey FR-007: `landing` app.py:403 ... `database` app.py:1409, all nested inside `create_app`) into their route-controller modules with each handler's existing `async def`/`def` signature preserved exactly, and make `create_app` (app.py:223) assemble a route table by calling each controller's registration function instead of defining handlers inline, while the app factory retains exception-handler wiring; name the TC-016 regression `tests/test_semantic_events.py::test_empty_query_string_handled_unchanged` (FR-007)
  - done 2026-09-15 — handlers moved to controllers; full dashboard suite 412 passed
- [x] T017 (after T016) Run `tests/test_dashboard_app.py -q` (expect 157 passed, survey FR-008 baseline) plus the full 16-file dashboard surface not run at survey time (`test_dashboard_accessibility.py test_dashboard_assets.py test_dashboard_data.py test_dashboard_export.py test_dashboard_graph_retheme.py test_dashboard_htmx_lists.py test_dashboard_knowledge.py test_dashboard_knowledge_graph.py test_dashboard_live_soak.py test_dashboard_packaging.py test_dashboard_readonly.py test_dashboard_restyle.py test_dashboard_scale.py test_dashboard_shell.py test_dashboard_theme.py test_dashboard_workspaces.py`), including the route-name pin `test_dashboard_app.py:4236 test_wiki_routes_registered_with_pinned_names` (FR-008)
  - done 2026-09-15 — dashboard route preservation suite 412 passed

## Phase 5: Semantic search pipeline (FR-009, FR-010)
<!-- Checkpoint (plan.md M5, soft interface note: re-run after Phase 2/M2
     lands to confirm emb.is_hash_fallback()/current_model()/embed_query()
     names+signatures unchanged): explicit stage/pipeline structure present,
     not one branching function; provenance/degraded/telemetry strings
     unchanged; semantic regression suite green. -->
- [x] T018 [P] Create `src/cairn/graph/search_pipeline.py` holding an ordered stage list (query → retrieval → fusion → rerank → enrichment → telemetry → result assembly), each stage wrapping the existing `graph/reranker.py` (`rerank` :388, `rerank_enabled`/`reranker_available` :91/:118), `graph/query_enrich.py` (`enrich` :298), `graph/fusion.py`, and `graph/ann_index.py` calls rather than reimplementing them (survey FR-009: stage modules exist separately but are called inline from one 1238-line function) (FR-009)
  - done 2026-09-15 — search_pipeline named stages created; semantic suites 214 passed
- [x] T019 (after T018) Refactor `semantic_search` (`graph/semantic.py:495-593`) into the composition entry point that builds a shared context and runs `search_pipeline`'s stages in order, reproducing the exact provenance strings (`"semantic"`/`"bm25"`/`"fused(bm25+semantic)"` and the `_sem_prov`/`_fused_prov` hash-fallback variants, semantic.py:630-632), the `"degraded": "embedding-backend"` marker, and telemetry events (`SEMANTIC_BACKEND`, `EMPTY_RESULT`, `RERANK_SKIPPED`); keep `params.enrich`/`params.multivector`/`params.prf` as stage-gating flags per D-003 (FR-009, FR-010)
  - done 2026-09-15 — semantic_search composes named stages; semantic suites 214 passed
- [x] T020 (after T019) Run `tests/test_semantic_events.py tests/test_semantic_unavailable.py tests/test_semantic_enrichment.py -q` — survey marks these unknown — verify (not run at survey time); this is Phase 5's regression gate (FR-010)
  - done 2026-09-15 — semantic degradation suites 57 passed

## Phase 6: LLM backend contract normalization (FR-011, FR-012)
<!-- Checkpoint (plan.md M6): both backends show the same skip-malformed-JSON
     guard; new test covers malformed-JSON extraction for both backends;
     tests/test_redaction_chokepoints.py stays green. -->
- [x] T021 [P] (fix 1/5) Add `tests/test_llm_backend_timeout.py` and implement timeout propagation for `LLMClient` `synthesize`, `revise`, `judge`, and `extract`; verify each bounded-timeout method propagates its timeout per amended FR-011 and tech-spec D-011; left unticked pending spec.md leaving `draft` (check.py's approval gate blocks any ticked box while spec.md Status is draft) (FR-011)
  - done 2026-09-15 — bounded LLM operations implemented; tests/test_llm_backend_timeout.py 9 passed
- [x] T022 [P] Add one shared `_parse_extract_lines(raw: str) -> List[Dict[str, Any]]` helper in `src/cairn/llm/client.py` implementing the per-line "skip on `json.JSONDecodeError`" pattern once (D-004), based on `FileQueueBackend.extract`'s existing correct pattern (client.py:84-94) (FR-012)
  - done 2026-09-15 — shared extract parser added; LLM/redaction suites 94 passed
- [x] T023 (after T022) Route `FileQueueBackend.extract` (client.py:84-94) and `SubprocessBackend.extract` (client.py:155-159, currently a bare list comprehension with no `try/except` — survey's confirmed baseline inconsistency) through the shared `_parse_extract_lines` helper so both skip malformed JSON identically, without touching `get_client`'s `CAIRN_LLM_BACKEND` resolution logic (FR-012)
  - done 2026-09-15 — both backends use shared extract parser; malformed tests 6 passed
- [x] T024 (after T023) Add `tests/test_llm_extract_malformed.py` with failing-test-first cases (test.md forward TC-018/TC-019) covering malformed and empty JSON extraction for `SubprocessBackend.extract`, confirm it passes after T023, and run `tests/test_redaction_chokepoints.py -q` — the only existing file exercising either backend's `extract`, whose one call site tests the fallback branch, not malformed-JSON parsing (survey FR-012 gap: "no test coverage for extract-malformed-JSON on either backend") (FR-012)
  - done 2026-09-15 — tests/test_llm_extract_malformed.py 6 passed; redaction suite passed

## Phase 7: Operational CLI modularity (FR-013, FR-014)
<!-- Checkpoint (plan.md M7, soft interface note: re-verify _ms_bucket/
     _backend_name/is_hash_fallback/current_model/embed_count/reindex_paths/
     record_build_run imports once Phases 1/2/3/5 land): app.py line count
     drops from the 2145-line baseline; a check registry is present;
     tests/test_doctor.py stays at 46 passed. -->
- [x] T025 [P] (fix 1/5) Create the `src/cairn/cli/system/` package split by the five command families spec.md's US6 names — `doctor.py`, `metrics.py`, `status.py`, `sync.py`, `report.py` (D-005) — moving the flat `@main.command()` functions out of `src/cairn/cli/system.py` (2145 lines, survey FR-013 baseline); update `tests/test_agent_surface.py` to scan nested CLI modules (FR-013)
  - done 2026-09-15 — CLI system package split; agent surface and doctor suites 56 passed
- [x] T026 (after T025) (fix 1/5) Add a `HealthCheck` registration list in `doctor.py` so `_run_doctor` (system.py:1732) iterates registered checks instead of calling named functions directly, reproducing the exact check-name sequence `test_doctor.py:96-124` pins and the exit-code rule (0 on PASS/WARN, 1 on any FAIL); update both direct callers of `_run_doctor` (`system.py:1822 doctor`, `system.py:2043 _build_report`) to the same function name/shape (FR-013)
  - done 2026-09-15 — HealthCheck registry implemented; tests/test_doctor.py 47 passed
- [x] T027 (after T026) Run `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_doctor.py -q` (expect 46 passed, survey FR-014 baseline) (FR-014)
  - done 2026-09-15 — doctor preservation suite 47 passed

## Phase 8: Closing guardrails + umbrella PR (FR-017, FR-018)
<!-- Checkpoint (plan.md M8, depends on Phases 1-7 all landed — re-checks the
     fully merged tree): no new runtime dependency; no new import cycle or
     static-complexity finding vs. M0's baseline; mypy clean; full suite green
     at or above M0's pass count; single umbrella PR opened. -->
- [x] T028 (after T004, T008, T011, T014, T017, T020, T024, T027) Diff `sed -n '25,65p' pyproject.toml` against Phase 0/T004's snapshot to confirm no new runtime dependency (FR-017)
  - done 2026-09-15 — pyproject runtime dependency diff empty
- [x] T029 (after T028) Run `rg -n "radon|xenon|import-linter|importlinter" pyproject.toml .pre-commit-config.yaml` to confirm no dedicated cycle/complexity tool was introduced unexpectedly (survey FR-018 gap: no such tool found in the repo), do a manual import-cycle check (`python -c "import cairn"` plus each touched package root), and apply the Ruff complexity gate (`ruff check src --select C901,PLR0912,PLR0915,PLR0913 --statistics`) against baseline category counts C901 88 / PLR0912 52 / PLR0915 37 / PLR0913 67 with no per-category increase as the FR-018 assumption-gate (flagged, not survey-confirmed — confirm with spec author) (FR-018)
  - done 2026-09-15 — Ruff complexity counts C901=85 PLR0912=49 PLR0913=67 PLR0915=36; import cycles unchanged at 2
- [x] T030 (after T029) Run `uv run mypy --ignore-missing-imports src` (must stay clean, HARD gate) and `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest -q` (full suite green, pass count ≥ Phase 0/T001's baseline), then open the single umbrella PR per spec.md's assumption (FR-017, FR-018)
  - done 2026-09-15 — mypy and ruff clean; full suite 3364 passed, 4 skipped; umbrella PR https://github.com/tanlnm512/cairn/pull/124

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
