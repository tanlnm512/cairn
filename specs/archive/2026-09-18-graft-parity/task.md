# Tasks: graft-parity

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.
**Before-audit**: passed @ 394304083460c9cdca2359d1861f2ad73454f350

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1 | 4 | 4 |
| 2 | 5 | 5 |
| 3 | 9 | 9 |
| 4 | 4 | 4 |
| 5 | 7 | 7 |
| **Σ** | 29 | 29 |

## Phase 1: Always-fresh query substrate (FR-001, FR-002)
<!-- Checkpoint (plan): all eight MCP graph tools and six CLI query commands
     probe (size, mtime) drift and call reindex_paths only for changed paths;
     CAIRN_NO_REFRESH=1, --no-refresh, and read-only mode return stored answers
     plus a probe-derived banner without the watcher extra. -->
- [x] T001 [P] Add failing freshness tests for all eight MCP graph tools and six CLI query commands — new tests/test_graft_parity_freshness.py; TC-001, TC-002, TC-003 (FR-001)
  - done 2026-09-16 — pytest tests/test_graft_parity_freshness.py -q -> 15 failed (expected red: 8 MCP tools, 6 CLI cmds, bounded-refresh guard)
  - Survey gap: probe exists (`_detect_changed`, src/cairn/graph/watcher.py:59) and `reindex_paths` exists (src/cairn/graph/incremental.py:24), but zero query-surface wiring (survey FR-001).
  - Tests must fail before implementation: edit a file after indexing, invoke every named tool/command, assert new-name resolution, old-name absence, and that only the drifted path is reindexed.
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_freshness.py -q`
  - Verify before implementing: `rg -n "_detect_changed|reindex_paths" src/cairn/mcp_server src/cairn/cli/query.py src/cairn/cli/ask_context.py` → 0 matches (survey FR-001).
- [x] T002 (after T001) Implement query-boundary refresh via one freshness service over `_detect_changed` + `reindex_paths` — src/cairn/graph/watcher.py, src/cairn/graph/incremental.py, src/cairn/mcp_server/_server_core.py, src/cairn/mcp_server/tools_graph.py, src/cairn/cli/query.py, src/cairn/cli/ask_context.py, src/cairn/cli/serve.py (FR-001)
  - done 2026-09-16 — pytest tests/test_graft_parity_freshness.py tests/test_watcher_service.py -q -> 26 passed
  - Consumes T001's failing tests as the contract; explicit per-boundary helper/decorator per D-001, never transport-dispatch interception. Preserve read-only connections, propagate refresh failures, CLI banners to stderr.
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_freshness.py tests/test_watcher_service.py -q`
- [x] T003 (after T002) Add failing opt-out/read-only staleness tests for `CAIRN_NO_REFRESH=1`, `--no-refresh`, read-only stores, watcher-free banners, and clean-tree quiet — tests/test_graft_parity_freshness.py; TC-004–TC-007 (FR-002)
  - done 2026-09-16 — pytest tests/test_graft_parity_freshness.py -q -> 3 failed, 16 passed (expected red: no_refresh_env/flag, read_only_store)
  - Survey gap: neither opt-out exists (`rg "CAIRN_NO_REFRESH|no.refresh" src/cairn` → 0) and `_staleness_banner` is pending_sync/watcher-only (survey FR-002).
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_freshness.py -q`
- [x] T004 (after T003) Implement probe-based no-refresh banners and read-only drift reporting — the Phase 1 file set above (FR-002)
  - done 2026-09-16 — pytest tests/test_graft_parity_freshness.py -q -> 19 passed; -k "freshness or staleness or no_refresh" -> 57 passed
  - Consumes T003's tests and T002's freshness service: disabled refresh and `_read_only_mode()` answer from storage and report `_detect_changed` results without watchdog. Keep structured output typed.
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_freshness.py -q && CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests -q -m "not infra" -k "freshness or staleness or no_refresh"`

## Phase 2: Diff blast radius (FR-003, FR-004)
<!-- Checkpoint (plan): hunk-intersecting seeds only, precise default with fuzzy
     opt-in, four formats, empty radius exit 0, missing-ref fetch-depth guidance. -->
- [x] T005 (after T004) Add failing blast tests for working-tree diff, hunk-vs-file seeding, and merge-base `--base` — new tests/test_graft_parity_blast.py; TC-008, TC-009, TC-010 (FR-003)
  - done 2026-09-16 — pytest tests/test_graft_parity_blast.py -q -> 4 failed (expected red: blast absent)
  - Survey gap: no `blast` command and no hunk/merge-base parsing (`rg '"blast"|hunk|unified|merge.base'` → 0; survey FR-003).
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_blast.py -q`
  - Verify before implementing: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_traversal_parity.py tests/test_incremental_derived.py -q` (survey baseline PASS 72).
- [x] T006 (after T005) Implement blast radius core — new src/cairn/graph/blast.py and src/cairn/cli/blast.py; reuse `impact_analysis` read-only (FR-003)
  - done 2026-09-16 — pytest tests/test_graft_parity_blast.py tests/test_traversal_parity.py -q -> 14 passed
  - Consumes T005 fixtures and Phase 1's refreshed graph. Per D-002: parse new-side hunks, intersect inclusive line_start/line_end spans, choose innermost symbols, de-duplicate ids, one multi-root reverse traversal.
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_blast.py tests/test_traversal_parity.py -q`
- [x] T007 (after T006) Add failing renderer/CLI-option tests for text|markdown|mermaid|json, precise default vs `--fuzzy`, empty-radius exit 0, and missing-base fetch-depth guidance — tests/test_graft_parity_blast.py; TC-011–TC-014 (FR-004)
  - done 2026-09-16 — pytest tests/test_graft_parity_blast.py -q -> 7 failed, 4 passed (expected red: renderers/options absent)
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_blast.py -q`
- [x] T008 (after T007) Implement blast renderers and CLI terminal cases — src/cairn/cli/blast.py plus renderer tests (FR-004)
  - done 2026-09-16 — pytest tests/test_graft_parity_blast.py -q -> 11 passed; -k blast -> 11 passed
  - Consumes T006's canonical radius/seed/truncation result object from `src/cairn/graph/blast.py`; renderer precedent `src/cairn/viz/renderers.py:to_mermaid`. Unknown `--base` must fail with fetch-depth guidance, never report empty.
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_blast.py -q && CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests -q -m "not infra" -k blast`
- [x] T009 (after T008) Register `cairn blast` and document it — src/cairn/cli/__init__.py, README.md, docs/cli-reference.md (FR-003, FR-004)
  - done 2026-09-16 — cairn blast --help exit 0; -k blast -> 11 passed; blast file guard -> 11 passed
  - Serial registry/docs owner only; no source behavior changes. Interface: the T006/T008 CLI options `--base`, `--format`, `--fuzzy`.
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests -q -m "not infra" -k blast`

## Phase 3: Orientation and audit surfaces (FR-005, FR-006, FR-007)
<!-- Checkpoint (plan): deterministic capped map with drop counts, body-free
     file_api with null degradation, enclosing-symbol grep with in-edge ranking,
     MCP inventory expects 24 tools. -->
- [x] T010 (after T004) Add failing repo-map tests — new tests/test_graft_parity_map.py; TC-015–TC-020 (FR-005)
  - done 2026-09-16 — pytest tests/test_graft_parity_map.py -q -> 7 failed (expected red: no map/repo_map)
  - Parallel with T012/T014 (disjoint files). Survey gap: no `repo_map` implementation (survey FR-005).
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_map.py -q`
- [x] T011 (after T010) Implement deterministic repo-map domain and CLI — new src/cairn/graph/repo_map.py, new src/cairn/cli/map.py; read-only reuse of src/cairn/graph/stats.py (FR-005)
  - done 2026-09-16 — pytest tests/test_graft_parity_map.py -q -> domain/CLI green (7 passed after T017 registration)
  - Per D-007: stored rows only, deterministic tie-breakers, fixed caps, dropped counts even when zero, zero LLM/network.
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_map.py -q`
- [x] T012 (after T004) Add failing file-api tests for full symbol coverage, no bodies, synthesized signatures, and null degradation — new tests/test_graft_parity_file_api.py; TC-021, TC-022, TC-023 (FR-006)
  - done 2026-09-16 — pytest tests/test_graft_parity_file_api.py -q -> 3 failed (expected red: module absent)
  - Parallel with T010/T014. Survey gap: no `file_api` and no signature text storage (survey FR-006).
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_file_api.py -q`
- [x] T013 (after T012) Implement signature synthesis service from stored parameters/return_type — src/cairn/graph/builder.py or a dedicated stored-row service; no schema migration (D-003) (FR-006)
  - done 2026-09-16 — pytest tests/test_graft_parity_file_api.py -q -> 3 passed
  - Interface for T017: kind, qualified_name, `signature: qualified_name(parameters) -> return_type` or null, line span; never body text.
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_file_api.py -q`
- [x] T014 (after T004) Add failing grep tests for enclosing-symbol groups, file-level fallback, in-edge ranking, `-i`/`--fixed`/`--in`, and truncation counts — new tests/test_graft_parity_grep.py; TC-024–TC-027 (FR-007)
  - done 2026-09-16 — pytest tests/test_graft_parity_grep.py -q -> 4 failed (expected red)
  - Parallel with T010/T012. Survey gap: token-based search only; no CLI grep (survey FR-007).
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_grep.py -q`
- [x] T015 (after T014) Implement graph grep domain and CLI — new src/cairn/graph/grep.py, new src/cairn/cli/grep.py (FR-007)
  - done 2026-09-16 — pytest tests/test_graft_parity_grep.py -q -> 4 passed
  - Per D-007: stored files/spans/edges only, innermost enclosing symbol, additive truncation counts.
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_grep.py -q`
- [x] T016 (after T011) (after T013) Add failing MCP dispatch tests for `repo_map` and `file_api` parity plus `_EXPECTED_TOOL_COUNT` 22→24 — tests/test_graft_parity_map.py, tests/test_graft_parity_file_api.py; TC-019, TC-021 (FR-005, FR-006)
  - done 2026-09-16 — pytest tests/test_graft_parity_map.py tests/test_graft_parity_file_api.py -q -> 3 failed registration (expected red; green after T017)
  - Serial because FR-001/002, FR-005, and FR-006 all touch tools_graph.py/_server_core.py/server.py (plan MCP inventory).
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_map.py tests/test_graft_parity_file_api.py -q`
- [x] T017 (after T016) Register MCP `repo_map` and `file_api` and set inventory to 24 — src/cairn/mcp_server/tools_graph.py, src/cairn/mcp_server/_server_core.py, src/cairn/mcp_server/server.py (FR-005, FR-006)
  - done 2026-09-16 — -k "repo_map or file_api or grep or tool_count" -> 16 passed (2 inventory pins -> T018); freshness gate 19 passed
  - Consumes T011's repo-map result shape and T013's signature record shape. One owned sequence; no parallel editor of these files.
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests -q -m "not infra" -k "repo_map or file_api or grep or tool_count"`
- [x] T018 (after T015) (after T017) Register `cairn map`/`cairn grep` and document map, file_api, grep, and the 24-tool inventory — src/cairn/cli/__init__.py, README.md, docs/cli-reference.md, docs/mcp-tools.md, AGENTS.md, src/cairn/agent_install/_common.py, src/cairn/mcp_server/__init__.py, tests/test_ingest_compat.py (FR-005, FR-006, FR-007; file set extended per D-011)
  - done 2026-09-16 — -k "repo_map or file_api or grep or tool_count" -> 18 passed; blast guard -> 11 passed
  - Serial registry/docs owner. Interface: T011 map JSON/text, T015 grep flags, T017 MCP tool schemas.
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests -q -m "not infra" -k "repo_map or file_api or grep or tool_count"`

## Phase 4: Durable knowledge and portable viz (FR-008, FR-009)
<!-- Checkpoint (plan): byte-identical ## Notes across writers and critic
     exclusion; one self-contained HTML export with no network assets. -->
- [x] T019 [P] Add failing Notes-preservation tests for regeneration, enrichment, byte identity, and critic exclusion — new tests/test_graft_parity_notes.py; TC-028, TC-029, TC-030 (FR-008)
  - done 2026-09-16 — pytest tests/test_graft_parity_notes.py tests/test_wiki_enrich.py -q -> 2 failed, 9 passed (expected red: regen-preserve, critic-exclusion)
  - Parallel with T021 (disjoint files). Survey gap: whole-body regen and full-body critic (survey FR-008).
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_notes.py tests/test_wiki_enrich.py -q`
- [x] T020 (after T019) Implement one shared Notes extract/splice helper and critic exclusion — src/cairn/wiki/generator.py, src/cairn/compass/generator.py, src/cairn/cli/wiki.py, src/cairn/compass/critic.py (FR-008)
  - done 2026-09-16 — pytest tests/test_graft_parity_notes.py tests/test_wiki_enrich.py -q -> 11 passed
  - Per D-008: exact level-2 section bounded by next same-or-higher heading or EOF; splice original bytes; critic receives Notes-free body; no markdown normalization.
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_notes.py tests/test_wiki_enrich.py -q`
- [x] T021 [P] Add failing self-contained HTML export tests for one-file output, no network asset references, and selected-scope rendering — new tests/test_graft_parity_viz.py; TC-031, TC-032 (FR-009)
  - done 2026-09-16 — pytest tests/test_graft_parity_viz.py -q -> 3 failed (expected red: no --export)
  - Parallel with T019. Survey gap: no `--export <file>.html` path (survey FR-009).
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_viz.py -q`
- [x] T022 (after T021) Implement `cairn viz --export <file>.html` — src/cairn/viz/renderers.py, src/cairn/viz/__init__.py, src/cairn/cli/hooks_viz.py; reuse dashboard static asset read-only (FR-009)
  - done 2026-09-16 — pytest tests/test_graft_parity_viz.py -q -> 3 passed; pre-commit passed
  - Inlined script JSON must escape HTML-sensitive characters; output must render the same selected graph scope as existing formats.
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_viz.py -q && CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests -q -m "not infra" -k "notes or viz_export or self_contained_html"`

## Phase 5: Precision and broader discovery (FR-010, FR-011, FR-012)
<!-- Checkpoint (plan): pyright upgrades only resolvable ambiguous edges and
     never downgrades exact; Rust edges unresolved; nested repos default-off with
     prefixed ids; worktree seeds from main checkout then refreshes drift. -->
- [x] T023 (after T013) Add failing pyright/LSP tests for exact upgrade, ambiguous handling, missing/failing no-op with notice, and no exact downgrade — new tests/test_graft_parity_lsp.py; TC-033–TC-036 (FR-010)
  - done 2026-09-16 — pytest tests/test_graft_parity_lsp.py -q -> 5 failed (expected red: module absent)
  - Serial after Phase 3 because FR-006 storage and FR-010 both end in graph storage/build files (plan). Use an injected fake JSON-RPC transport, not subprocess patching.
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_lsp.py -q`
- [x] T024 (after T023) Implement opt-in `cairn build --lsp` pyright ambiguous-edge pass — new src/cairn/graph/lsp.py, src/cairn/graph/builder.py, src/cairn/cli/core.py (FR-010)
  - done 2026-09-16 — pytest tests/test_graft_parity_lsp.py -q -> 5 passed; rust+incremental -> 65 passed; build-path coverage added in review fix (7 passed)
  - Per D-005: `pyright --stdio` only, flag-gated only, only Python `ambiguous` edges, upgrade only a unique definition mapping to one stored symbol; convert zero-based LSP lines to graph spans; enforce timeout/shutdown.
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_lsp.py -q`
- [x] T025 [P] Add failing Rust indexing tests for definitions/spans, name-based unresolved edges (never exact), and grammar-unavailable skip — new tests/test_graft_parity_rust.py; TC-037 (FR-011)
  - done 2026-09-16 — pytest tests/test_graft_parity_rust.py -q -> 3 failed (expected red)
  - Parallel with T023 (disjoint files). Survey gap: zero Rust grammar pins or parser wiring (survey FR-011).
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_rust.py -q`
- [x] T026 (after T025) Implement Rust generic tree-sitter tier, pinned grammar, registry/scanner wiring, and D-004 record reference — new src/cairn/parsers/generic_tree_sitter.py, new src/cairn/parsers/rust.py, src/cairn/parsers/_registry.py, src/cairn/parsers/factory.py, src/cairn/graph/scanner.py, src/cairn/graph/builder.py, src/cairn/graph/resolver.py, pyproject.toml (FR-011; file set extended per D-009, D-010)
  - done 2026-09-16 — pytest rust+traversal+incremental -> 75 passed; D-004 grep matched 3 records
  - Per D-004: pin `tree-sitter-rust==0.24.2`; skip `.rs` with parser-unavailable reason if the wheel cannot load; enforce unresolved-only Rust edges after normal resolution.
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_rust.py -q && grep -RniE 'D-[0-9]+' specs/graft-parity | grep -iE 'rust.*grammar|grammar.*rust'`
- [x] T027 (after T026) Add failing nested-repo/worktree tests for default-off exclusion, prefixed opt-in ids, uninitialized submodule skip, and seed-then-refresh without main-store mutation — new tests/test_graft_parity_repos.py; TC-039–TC-042 (FR-012)
  - done 2026-09-16 — pytest tests/test_graft_parity_repos.py -q -> 4 failed (expected red)
  - Serial after T026 because both edit src/cairn/graph/scanner.py (plan). Survey gap: immediate-children-only discovery, no worktree/.gitmodules code (survey FR-012).
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_repos.py -q`
- [x] T028 (after T027) Implement config-gated nested repos and worktree seeding — src/cairn/graph/config.py, new src/cairn/graph/worktree.py, src/cairn/graph/scanner.py (FR-012)
  - done 2026-09-16 — pytest repos+traversal+incremental -> 76 passed; -k subset -> 7 passed; public-path seeding added in review fix
  - Per D-006: default-off `include_nested_repos`, prefixed ids with longest-root resolution, path-list `discover_repos` compatibility for all existing callers, SQLite-backup copy into an independent worktree store, then Phase 1 `_detect_changed`/`reindex_paths` drift refresh; never mutate the main store.
  - Acceptance: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_graft_parity_repos.py tests/test_traversal_parity.py tests/test_incremental_derived.py -q && CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests -q -m "not infra" -k "lsp or pyright or rust or submodule or nested_repo or worktree"`
- [x] T029 (after T024) (after T028) Register/document `build --lsp`, nested-repo config, and worktree behavior; run final baseline — src/cairn/cli/__init__.py, README.md, docs/cli-reference.md, docs/architecture.md (FR-010, FR-011, FR-012)
  - done 2026-09-16 — full suite -> 3253 passed, 1 skipped (2026-09-16 closing audit); pre-commit --all-files passed
  - Serial registry/docs owner. Interfaces: T024 `--lsp` flag/notice, T026 Rust grammar dependency, T028 `include_nested_repos` key and worktree seeding behavior.
  - Acceptance: `uv run pytest -q -m "not infra"`

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
