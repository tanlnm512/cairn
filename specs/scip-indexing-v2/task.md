# Tasks: scip-indexing-v2

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.
**Before-audit**: pending — the orchestrator writes `passed @ <sha>` here

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1     | 4     | 0    |
| 2     | 7     | 0    |
| 3     | 3     | 0    |
| 4     | 4     | 0    |
| 5     | 3     | 0    |
| **Σ** | 21    | 0    |

## Phase 1: Closure redesign at 5× edges (FR-011, FR-012)
<!-- Checkpoint (plan.md "After Phase 1"): existing parity gates green and the
     enforcing scaling gate in place with its budget recorded as D-008 —
     `uv run --no-sync pytest tests/test_dataflow_transitive_closure.py -q` and
     `uv run --no-sync pytest tests/test_incremental_derived.py -k "matches_full_rebuild or drops_rows" -q`. -->
- [ ] T001 [P] Add the shared closure core `_closure_rows(conn, max_depth, restrict_sources=None)` and rewrite `build_transitive_closure` (src/cairn/graph/dataflow.py:218) as read-once → in-memory level loop → single DELETE (dataflow.py:239) + one sorted executemany (FR-011; D-007)
  - Read pass: structural edges, symbol-name map, unique-name map per the current seed/extension queries (dataflow.py:262-303); keep `idx_transitive_distance`; first-wins per PK (source_id, target_name, distance) reproduces INSERT OR IGNORE exactly — row set byte-identical (AC5).
  - Proof anchor (green today, survey FR-011 verify): `uv run --no-sync pytest tests/test_dataflow_transitive_closure.py -q`.
- [ ] T002 (after T001) Rewrite `maintain_transitive_closure` (dataflow.py:314) as the same core restricted to the affected source set after its DELETE; incremental drivers `_maintain_derived_indexes` (incremental.py:685), `_rebuild_derived_indexes` (incremental.py:437), `_capture_derived_prestate` (incremental.py:519) stay call-compatible (FR-011)
  - Interface consumed: `_closure_rows` from T001 with `restrict_sources` bound to the affected ids, returning the same row list shape.
  - Proof anchor (survey FR-011): `uv run --no-sync pytest tests/test_incremental_derived.py -k "matches_full_rebuild or drops_rows" -q` (2 passed, 60 deselected today).
- [ ] T003 [P] Add the enforcing closure-budget gate: closure op in `run_scaling_suite` (src/cairn/bench/scaling_suite.py) with deterministic 5× structural-edge synthesis (each structural edge replicated 4× with fresh ids, cyclically-shifted targets over sorted symbol ids — hub fan-out preserved) and a wall/peak-memory assertion per D-008; new pytest gate at the 1000-file point; CI wiring in .github/workflows/ci.yml (FR-012; D-008)
  - Keep the perf-suite op name `build.derived.closure` unchanged (tests/test_bench.py pins report shape); the 5000-file point stays advisory bench.
  - Proof anchor (survey FR-012): `rg -n "build.derived.closure" src/cairn/bench/perf_suite.py .github/workflows/ci.yml`.
  - Survey FR-012 PARTIAL — gap: "the gate is advisory, not enforcing"; D-008 supplies the provisional numbers (60 s wall / 512 MB peak at the 1000-file point).
- [ ] T004 (after T003) Re-pin D-008 from measurement: run the new gate on the implementation branch at the 1000-file point and append the measured wall/peak-RSS values to tech-spec D-008 before merge (FR-012)
  - Append-only per the D-### rule; D-008 Consequences marks the provisional numbers "not from a run — hence the mandatory re-pin".

## Phase 2: SCIP ingestion core — edges-only overlay (FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-017)
<!-- Checkpoint (plan.md "After Phase 2"): on a fixture workspace with a committed
     opaque-USR index, every covered file's call/reference edges are index-sourced
     and exact with no duplicate symbol population (AC1/AC2); protobuf-absent build
     succeeds via tree-sitter with an observable fallback record —
     `uv run --no-sync pytest tests/test_scip_import.py -q` and
     `rg -n "^scip" pyproject.toml` showing the extra. -->
- [ ] T005 [P] Vendor the protobuf runtime surface: new `src/cairn/parsers/_scip_pb2.py` stub, new `scripts/regen_scip_pb2.sh` recording the protoc/proto versions that produced the stub, and the `[scip]` extra in pyproject.toml floor-pinning protobuf, with protobuf also added to the test extra so CI covers the importer (FR-017; D-005)
  - Proof anchor (survey FR-017): `rg -n "^scip" pyproject.toml` exits 1 today.
- [ ] T006 [P] Add `EDGE_SOURCE_MIGRATION` ("ALTER TABLE edges ADD COLUMN source TEXT") appended to `MIGRATIONS` (src/cairn/graph/schema.py:526) beside `EDGE_RESOLUTION_MIGRATION` (schema.py:459); NULL reads as tree_sitter everywhere (FR-001; D-010)
  - Additive ALTER TABLE only — invisible to the FTS5 triggers by the SYMBOL_SOURCE_MIGRATION argument (schema.py:483-487); no backfill needed.
- [ ] T007 [P] Add the scip config key: `_SCIP_KEY` beside `_TAINT_KEY` (src/cairn/graph/config.py:40), a `scip` dict field on `CairnConfig` (config.py:11) with an `is_default` term in `load_config` (config.py:46), parsed via `_as_dict` then a string-dict `indexes` — malformed warns and never breaks the build (FR-015, FR-001; D-011)
  - Key shape: {"scip": {"indexes": {language: index path}}} per D-011; the `cairn config` echo itself is Phase 5 (T019).
  - Proof anchor (survey FR-015): `rg -n "scip" src/cairn/graph/config.py` exits 1 today.
- [ ] T008 (after T005, T006) Land the importer core in new `src/cairn/parsers/scip_importer.py`: `scip_available()` guarding ImportError AND protobuf.runtime_version.VersionError, index parse, line-level innermost-containment position join onto tree-sitter symbols, occurrence classification (target kind Function/Method → calls, else references), in-workspace target → resolution exact / otherwise target_id NULL + unresolved, validate-then-write edges-only INSERT with source='scip' — plus new tests/test_scip_import.py and the fixture family root tests/fixtures/scip-indexing/ with provision.sh, covered/, covered-off/, opaque/, opaque-off/ (FR-001, FR-003, FR-006)
  - Interfaces consumed: the `_scip_pb2` stub from T005; the `edges.source` column from T006. Never write symbols; never join by symbol-name or USR string; definition occurrences (role bit 0x1) are skipped; language comes from the matched files row, never Document.language.
  - Survey FR-003 PARTIAL — gap: "the occurrence-range → containing-tree-sitter-symbol join does not exist anywhere"; the target-side vocabulary it reuses is resolver.py:204/219-220/244.
- [ ] T009 (after T008) Per-file authority and per-document repo attribution in scip_importer.py: DELETE the covered file's calls/references keyed on symbols.file_id then INSERT the index's edges; each document's relative_path matched to a files row through the scanner identity substrate (`repository_id`, `discover_repos`, `resolve_repo_path` in src/cairn/graph/scanner.py) — plus the multirepo/ fixture (two repos, one committed index) (FR-002, FR-004)
  - Survey FR-004 PARTIAL — gap: "no importer exists to attribute per document; the identity substrate it must share does exist and is multi-repo aware"; never assign one repo id to a whole index (the 0a956fa^ builder.py hist ~578-583 failure).
  - Files outside every index keep tree-sitter edges untouched.
- [ ] T010 (after T009) Anomaly gate and disagreement counters in scip_importer.py: per document, joined/total occurrences below the 0.5 threshold (D-003) retains that file's tree-sitter edges and records a skipped_files row reason scip_join_anomaly; before each covered-file DELETE, match incoming scip edges to tree-sitter edges by (kind, line) and count disagreements and upgrades in the import record — plus drifted/ and drifted-off/ fixtures (FR-005, FR-014)
  - Skip reasons are importer-owned additive values on the CHECK-free reason TEXT (schema.py:171), aggregated by skipped_by_reason (stats.py:61-68) per D-012.
  - Proof anchor (survey FR-005): `rg -in "join.rate|anomal" src/cairn/` exits 1 today.
- [ ] T011 (after T010, T007) Wire the overlay into the build: hook in src/cairn/graph/builder.py between the `_resolve_all` result (builder.py:393) and `backup_to` (builder.py:434), driven by the scip config key; when `scip_available()` is false the build succeeds via tree-sitter with the `[scip]` install hint and a skipped_files row reason scip_runtime_missing; the importer returns an import-record dict (FR-006, FR-001)
  - Validate-then-write: a corrupt index aborts before any edge is written — never conn.rollback() on the live build connection; the overlay touches only calls/references (imports/contains/decorates stay tree-sitter-only).
  - Precedent read (survey FR-006): `git show 0a956fa^:src/cairn/parsers/scip_importer.py` hist 43-61.

## Phase 3: Auto-generation registry (FR-007, FR-008, FR-009)
<!-- Checkpoint (plan.md "After Phase 3"): configured-but-absent + fake binary on
     PATH → generated exactly once and imported (AC6); binary missing / nonzero
     exit / timeout → build succeeds via tree-sitter with an observable record and
     the install hint under -v (AC7) — `uv run --no-sync pytest tests/test_scip_indexers.py -q`
     with binaries faked (CI never runs real indexers, spec Assumptions). -->
- [ ] T012 [P] Restore the registry in new `src/cairn/parsers/scip_indexers.py`: an IndexerSpec per language carrying argv template, env (rust via RA_SCIPOUT), and install-hint string; `_KNOWN_INDEXERS` with exactly swift, java, kotlin (via scip-java), typescript, python, go, rust; `try_generate_index` never raises, attempts once per build, never rebuilds an existing index file, and bounds the subprocess at `_INDEX_TIMEOUT_S = 30*60` — plus new tests/test_scip_indexers.py and the autogen/ and seven-langs/ fixtures with the stub bin/scip-python and bin-fail/ variants (FR-007, FR-009)
  - Proof anchor (survey FR-009): `git show 0a956fa^:src/cairn/parsers/scip_indexers.py | rg -c 'language="'` → 7.
- [ ] T013 (after T012) Failure-mode observability in scip_indexers.py: missing binary, nonzero exit, timeout, and SubprocessError/OSError map to skipped_files reasons scip_gen_missing_binary, scip_gen_nonzero_exit, scip_gen_timeout, scip_gen_os_error; install hints log under -v; each registry entry degrades independently via per-language try/except (FR-008, FR-009)
  - Survey FR-008 gap: generation failures have no surface today — no registry exists to fail and skipped_files carries no indexer-failure reason.
  - Proof anchor (survey FR-008): `rg -n "CREATE TABLE IF NOT EXISTS skipped_files" src/cairn/graph/schema.py` → 171.
- [ ] T014 (after T013) Generation trigger in src/cairn/graph/builder.py, same config-hook region as the overlay: a configured-but-absent index generates exactly once via `try_generate_index`, then imports through the Phase-2 overlay entry; an existing index file is never rebuilt; skip rows land via `_record_skips` (builder.py:62) (FR-007, FR-008)
  - Interfaces consumed: `try_generate_index` from T012 (returns bool, never raises); the overlay entry and import-record dict from T011.

## Phase 4: Incremental honesty and measurement (FR-010, FR-013, FR-014)
<!-- Checkpoint (plan.md "After Phase 4"): editing a covered file then
     `cairn update` leaves that file's edges tree-sitter-sourced with the flip
     observable (AC8), and the next full build restores index edges; stats shows
     per-language exact-share uplift index-on vs off on the same tree; the
     ground-truth harness reports zero false-exact conversions with disagreements
     counted (AC3) — `uv run --no-sync pytest tests/test_incremental_scip_provenance.py -q`. -->
- [ ] T015 [P] Provenance plumbing for `cairn update`: `insert_edges` (src/cairn/graph/repository.py:45) writes source='tree_sitter' explicitly (mirroring the symbols INSERT at repository.py:31); the update's delete-and-reparse of changed files then flips provenance observably with no indexer invoked — plus new tests/test_incremental_scip_provenance.py and the update/ fixture (FR-010; D-010)
  - Survey FR-010 PARTIAL — gap: "provenance lives on symbols only (no edges.source); there is no scip value to flip FROM and no observation surface for the flip" — the column landed in T006; this task writes it on the update path and observes the flip.
  - Proof anchor (survey FR-010): `rg -n "tree_sitter" src/cairn/graph/repository.py` → 31 today (symbols INSERT only).
- [ ] T016 [P] Sibling stats keys in `get_stats` (src/cairn/graph/stats.py:10): stats edge_sources ({tree_sitter, scip} counts) and exact_share_by_language (pool = calls/references edges joined through symbols→files for language), both behind the same sqlite3.OperationalError → zero-default guard the resolution query uses (stats.py:40-52) (FR-013; D-009)
  - Never add keys inside stats resolution — exact-dict-pinned at tests/test_status_resource_health.py:408; minimal-schema DBs get zero/empty defaults, never exceptions.
  - Survey FR-013 PARTIAL — gap: "no scip provenance breakdown and no per-language exact-share anywhere on the surface".
- [ ] T017 (after T016) Render provenance in the CLI: scip provenance and per-language uplift inside the existing resolution-share block (src/cairn/cli/core.py stats render at core.py:470-475) and a `scip: n edges (d disagreements)` line in the build summary panel (core.py:422-432) when scip edges exist (FR-013)
  - Interface consumed: the two sibling keys from T016.
- [ ] T018 (after T017) Zero-false-exact measurement: A/B evaluation of index-on vs index-off builds on the same tree via the ground-truth harness (src/cairn/eval.py `load_ground_truth`:124, `evaluate_l1_query`:246); disagreements and upgrades read from T010's import-record counters; pin the eval report/json shape TC-021 needs (FR-014)
  - Where the index's binding and the resolver's would disagree, the edge follows the index and the disagreement is counted — never silently resolved either way.

## Phase 5: CLI surface and docs (FR-015, FR-016)
<!-- Checkpoint (plan.md "After Phase 5"): `cairn import-scip` lands edges under
     the same overlay rules on a built DB (AC9); config echo shows the key; guide
     exists — `test -e docs/scip.md && echo PRESENT`,
     `uv run --no-sync cairn config` echoing the scip key,
     `uv run --no-sync cairn import-scip --help` exit 0. End-of-plan integration
     (T021): full build of the scaling corpus with an index on — closure within
     the recorded budget at real ~5× edge volume, exact-share uplift visible,
     zero false-exact. -->
- [ ] T019 [P] CLI surface in src/cairn/cli/core.py: a `cairn import-scip` subcommand beside `build` (core.py:258) that opens the store DB, requires it built, and runs the same overlay entry under the same rules; `cairn config` (core.py:156) echoes the resolved scip indexes after the repo_namespaces block (core.py:229-246) (FR-015; D-011)
  - Proof anchor (survey FR-015): `rg -in "import.scip" src/cairn/cli/` exits 1 today. Tests use tmp_path stores, never the real ~/.cairn (C-04); no eager cairn.cli imports in test modules.
- [ ] T020 [P] Write docs/scip.md: the end-to-end toolchain guide — per-indexer install matrix (npm-not-pip scip-python, macOS-only scip-swift + homebrew taps, rust via RA_SCIPOUT, kotlin via scip-java with the archived scip-kotlin caveat), configuration, generation semantics, fallback behavior, and the overlay model (FR-016)
  - Proof anchor (survey FR-016): `test ! -e docs/scip.md && echo ABSENT` today.
- [ ] T021 (after T004, T019, T020) End-of-plan integration (the joint US1+US2 demo): heavy/ and heavy-off/ fixtures (~5× tree-sitter call-edge volume) and a full build of the scaling corpus with an index on — closure within the re-pinned D-008 budget at real ~5× SCIP edge volume, exact-share uplift visible index-on vs off, zero false-exact against the ground truth (FR-011, FR-013, FR-014)
  - Verify per plan.md: Phase 1's gate re-run plus Phase 4's stats comparison; fixture scale must finish in seconds (TC-018).

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
