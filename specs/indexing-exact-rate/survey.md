# Survey: indexing-exact-rate

**Created**: 2026-09-13 | **Baseline**: 0.20.1 @ ac09d4b (`feat/indexing-exact-rate`)
The survey node's output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item FR-001a: "Scanner Layer C exclusion machinery (cairn.json exclude/include) exists and works"
  evidence:   src/cairn/graph/config.py:load_config:70 (reads cairn.json at workspace/repo root;
              exclude/include/repo_namespaces/ingest keys, dataclass src/cairn/graph/config.py:CairnConfig:32);
              src/cairn/graph/scanner.py:_build_config_spec:323 compiles cfg.exclude/cfg.include into
              pathspec gitignore PathSpecs (repo-root-relative); src/cairn/graph/scanner.py:classify_file:376
              runs Layer C at lines 418-420 — `if exclude_spec is not None and _match_root_relative(...):
              return False, REASON_CONFIG_EXCLUDE` (constant at scanner.py line 128); skips persisted by
              src/cairn/graph/builder.py:_record_skips:160 (INSERT INTO skipped_files, call site builder.py
              line 455); skipped_files table + reason indexes at src/cairn/graph/schema.py lines 171-181.
              include overrides A/B/C but NOT Layer D (classify_file docstring lines 385-388).
  status:     DONE-IN-CODE
  verify:     build B below: `skipped_files` gains reason='config_exclude' x179 (103 chunks + 76
              datasource files); noise symbols under both paths = 0 (SQL in FR-006b)
  gap:        —

item FR-001b: "No cairn.json is committed anywhere in the repo"
  evidence:   glob `**/cairn.json` → "No files found matching pattern"; `git ls-files -- cairn.json`
              → empty (run this session). Existing tests only write cairn.json into tmp_path
              (tests/test_cross_repo_namespaces.py:24 e.g. test_config_parses_repo_namespaces).
  status:     GAP
  verify:     git ls-files -- 'cairn.json'   # empty output
  gap:        commit cairn.json at repo root with exclude globs
              ["src/cairn/dashboard/static/chunks/", "benchmarks/datasource/"] — exact shapes validated
              by build B (zero symbols under both prefixes, zero effect on the rest: 7,467 "other"
              symbols identical to build A's non-noise count)

item FR-001c: "Noise symbols in the CURRENT live DB + why Layer D's minified skip misses them"
  evidence:   live DB /Users/tanle/.cairn/acc48fd52e4c6e8d/.kg (sqlite3, this session):
                symbols by area (JOIN files):  chunks|10409|103   datasource|2823|76   other|7064|430
                totals: 20296 symbols / 609 files
              → noise = 13,232 symbols / 179 files (10,409 + 2,823). SPEC CLAIM "~11k" is REFUTED
              (it is ~13.2k, both in the live DB and in the tree-built DB A below — same areas).
              The chunks are 103 `chunk-*.mjs`/`railroad-*.mjs` files under
              src/cairn/dashboard/static/chunks/mermaid.esm.min/ (find, this session); Layer D misses
              them because src/cairn/graph/scanner.py:_is_minified:365 matches a FILENAME marker
              ("*.min.js et al", lines 366-369) — the `.min` here is a DIRECTORY component, and the
              files are 206-line pretty-printed ESM chunks, under MAX_FILE_SIZE (scanner.py line 123).
  status:     DONE-IN-CODE   (measurement established; explains why a config exclude is needed)
  verify:     sqlite3 /Users/tanle/.cairn/acc48fd52e4c6e8d/.kg "SELECT CASE WHEN f.path LIKE
              'src/cairn/dashboard/static/chunks/%' THEN 'chunks' WHEN f.path LIKE
              'benchmarks/datasource/%' THEN 'datasource' ELSE 'other' END a, COUNT(*),
              COUNT(DISTINCT f.id) FROM symbols s JOIN files f ON s.file_id=f.id GROUP BY a;"
  gap:        —

item FR-001d: "bench/eval corpus discovery reads from DISK, not the graph — exclusion is safe"
  evidence:   src/cairn/bench/datasource.py:default_manifest_path:414 locates
              benchmarks/datasource/manifest.json via cwd then source-tree parents (docstring lines
              415-422); scripts/verify_ground_truth.py:build_fresh_graph:188 COPIES the yarl snapshot
              to a throwaway workspace, adds the .git marker on the COPY, and build_graph's it fresh
              (lines 211-216; defaults at lines 96-97); scripts/verify_datasource.py reads
              DEFAULT_MANIFEST from disk (line 87) and budgets the tree bytes (lines 100-106);
              bench suites build their own synthetic corpora into throwaway DBs
              (src/cairn/bench/corpus.py:generate_corpus:22; build_graph calls at
              bench/perf_suite.py line 54, bench/agent_suite.py line 599, bench/scaling_suite.py
              line 84). Empirical: in the cairn.json-carrying copy, default_manifest_path() and
              default_baselines_root() both resolve (run this session, output below).
  status:     DONE-IN-CODE   (spec assumption VERIFIED)
  verify:     cd "$CORPUS_COPY" && python -c "from cairn.bench.datasource import
              default_manifest_path; print(default_manifest_path())"  # → .../benchmarks/datasource/manifest.json (is_file()=True)
  gap:        —

item FR-002: "Incremental embedding seam exists but has ZERO production callers"
  evidence:   src/cairn/graph/embeddings.py:embed_symbols:1549 — "(Re-)embed specific symbols now --
              the per-upsert ANN sync seam" (docstring lines 1555-1563; syncs vec0 in-transaction,
              idempotent by content_hash, returns {model, embedded, skipped, ann_synced}).
              `grep -rn embed_symbols src/cairn | grep -v "def embed_symbols"` → only the
              note_contention string at src/cairn/graph/embeddings.py line 1661 (+ pycache noise).
              Production embed callers are embed_all only: cli/embed.py line 292,
              bench/{perf_suite.py:159, agent_suite.py:606, scaling_suite.py:91}. Callers of
              embed_symbols exist ONLY in tests (tests/test_ann_incremental.py:
              test_embed_symbols_new_symbol_visible_without_rebuild:112 and siblings at lines
              134/168/298/321/343/367; tests/test_alias_gate.py lines 216/287;
              tests/test_chunk_variants.py line 237; tests/test_semantic_events.py line 347).
              Update-path hook points (all funnel into reindex_paths):
                - src/cairn/graph/incremental.py:reindex_paths:24 — insert_parsed_file(cur, ...,
                  name_to_symbol_ids, ...) at lines 232-235 collects the NEW symbol ids per file;
                  COMMIT at line 246; changed_names registered at lines 256-258.
                - src/cairn/graph/incremental.py:incremental_update:315 (return dict lines 394-398:
                  repos_scanned/files_reindexed/files_deleted/errors) calls reindex_paths.
                - src/cairn/cli/update.py:update:17 — single-file path _reindex_file
                  (src/cairn/graph/incremental.py:_reindex_file:841) at lines 25-26, else
                  incremental_update at lines 50-51.
                - src/cairn/graph/watcher.py:_update_pass:440 (class FileWatcherService at
                  src/cairn/graph/watcher.py:FileWatcherService:194) marks pending_sync rows
                  (lines 487-491) then calls incremental_update (lines 504-506).
              NOTE: reindex_paths already DELETES the old embeddings of changed files before the
              symbol delete (incremental.py lines 146-175, incl. vec0 row cleanup via
              delete_index_rows) — so an update today leaves changed symbols UNEMBEDDED until a
              manual `cairn embed`; the wiring lands naturally after COMMIT at line 246 using
              name_to_symbol_ids.values().
  status:     GAP
  verify:     grep -rn "embed_symbols" src/cairn | grep -v "def embed_symbols"   # → only line 1661 string
  gap:        call embed_symbols(new ids from name_to_symbol_ids) on the update path (reindex_paths
              post-COMMIT; single-file _reindex_file path), gated on embeddings_available()

item FR-003: "Backend-availability gate + degrade-not-fail doctrine exist; no deferred-embed surface on update"
  evidence:   src/cairn/graph/embeddings.py:embeddings_available:94 ("True iff an embedding backend
              can be loaded right now"); in-repo degrade precedent to mirror:
              src/cairn/knowledge/ingest/executor.py:execute_manifest:10 lines 63-66 —
              `embedded: int | None = None; if emb.embeddings_available(): summary =
              emb.embed_knowledge(...); embedded = summary["embedded"]` (report carries embedded=None
              when deferred). Warn-once doctrine: src/cairn/graph/embeddings.py:warn_hash_fallback_once:632
              and src/cairn/graph/ann_index.py:warn_ann_fallback_once:69. CONTRAST: the embed CLI
              hard-exits when unavailable (src/cairn/cli/embed.py:_exit_backend_unavailable:11,
              gated at lines 209/227) — FR-003's update path must NOT copy that. Doctor today has no
              symbol-embedding-coverage check: src/cairn/cli/system.py:_check_ann:787 compares vec0
              rows vs embeddings rows only ("ANN index stale ... recent symbols invisible", lines
              842-880); src/cairn/cli/system.py:_check_embeddings:763 reports backend identity
              (hash-fallback WARN, lines 778-784). Precedent for a coverage hint:
              src/cairn/graph/embeddings.py:unembedded_memory_hint:1906 (memory tier — same shape
              for symbols is missing). Doctor check sequence pinned at system.py lines 1764-1774
              (src/cairn/cli/system.py:_run_doctor:1734).
  status:     GAP
  verify:     grep -rn "embeddings_available" src/cairn | head   # gate exists at 9 call sites, none on update path
  gap:        deferred-embed observability: log line (warn-once doctrine) + a doctor/stats-visible
              count of symbols without embeddings under the current model

item FR-004: "Multivector machinery fully built but OPT-IN (default off)"
  evidence:   src/cairn/cli/embed.py:embed:179 — `--multivector` is_flag at lines 160-166, help
              verbatim: "Also embed name-only and docstring-only vectors (embeddings_mv, FR-005). Off
              by default: default builds store one vector per symbol, byte-identical to before."
              (confirmed live: `cairn embed --help` prints it). Producers:
              src/cairn/graph/embeddings.py:_embed_mv_kinds:1267 (per-kind _chunk_hash staleness,
              lines 1276-1281), MV_KINDS = ("name", "docstring") at embeddings.py line 292;
              src/cairn/graph/embeddings.py:embed_all:1403 takes multivector: bool = False
              (line 1410; doc lines 1426-1433 promise ZERO embeddings_mv writes when False; mv call
              gated at lines 1525-1528). ANN: src/cairn/graph/ann_index.py:rebuild_index:216 takes
              source ("embeddings"|"embeddings_mv", closed set lines 156-160; _SOURCE_PREFIX at
              line 146 → vecmv prefixed with the model stamp); CLI wires the mv rebuild only under the flag
              (cli/embed.py lines 313-317). embeddings_mv table: schema.py lines 227-238 (3-col PK
              symbol_id+model+vector_kind). QUERY side is a separate opt-in:
              src/cairn/graph/semantic.py:RetrievalParams:343 field multivector at line 456
              (None/False never reads embeddings_mv) — FR-004 flips the BUILD default only; the
              query-side default is its own decision for tech/plan. Live DB: embeddings_mv = 0 rows
              (sqlite3, this session); fresh build A: 0 rows.
  status:     PARTIAL
  verify:     cd /Users/tanle/Projects/cairn && CAIRN_HOME=/tmp/cairn-survey-A.H1ehk4 .venv/bin/cairn
              embed --help | sed -n '1,30p'   # shows opt-in --multivector wording
  gap:        flip default to on + add --no-multivector opt-out; SUPERSEDE the flag-off pinning
              tests (C-02 failing-test-first): tests/test_embeddings_mv.py:
              test_flag_off_writes_zero_mv_rows_and_keeps_summary_shape:148,
              test_flag_off_base_table_identical_to_flag_on_base_table:165,
              test_cli_multivector_flag_wires_to_embed_all:455;
              tests/test_ann_vecmv.py:test_default_build_never_creates_vecmv_table:85,
              test_cli_multivector_rebuilds_both_indexes:279;
              tests/test_multivector_query.py:test_flag_off_param_shapes_byte_identical:210,
              test_flag_off_never_reads_embeddings_mv:221, test_ann_flag_off_shapes_and_sql:462
              (query-side flag-off tests stay valid — they pin RetrievalParams, not the build default)

item FR-005: "stats reports edge totals + resolved count, NO resolution shares"
  evidence:   src/cairn/graph/stats.py:get_stats:17 returns repos/files/symbols/edges/imports/
              by_kind/by_repo + `stats["edges_resolved"] = ... WHERE target_id IS NOT NULL`
              (lines 40-42) + skipped_total/skipped_by_reason (lines 43-58). CLI:
              src/cairn/cli/core.py:stats:449 prints `edges  N (M resolved)` (line 462) —
              confirmed live: `cairn stats` output contains no "exact" (grep -ci exact → 0).
              The build command's summary rail ALREADY prints exact/ambiguous/unresolved (seen in
              builds A/B below), so the resolution data exists at build time; the shares need one
              GROUP BY over edges.resolution (pool SQL in FR-006a runs today).
  status:     GAP
  verify:     CAIRN_HOME=/tmp/cairn-survey-A.H1ehk4 .venv/bin/cairn stats | grep -ci exact   # → 0
  gap:        add resolution-share keys to get_stats (GROUP BY kind IN ('calls','references'),
              resolution) + render in stats CLI beside edge totals

item FR-006a: "BASELINE from THIS tree (throwaway graph, no cairn.json) — headline numbers"
  evidence:   Commands (run this session, verbatim):
                T1=$(mktemp -d /tmp/cairn-survey-A.XXXXXX)          # → /tmp/cairn-survey-A.H1ehk4
                cd /Users/tanle/Projects/cairn && CAIRN_HOME=$T1 .venv/bin/cairn build
              Build summary: repos 1, files 617, symbols 20,699, edges 124,811, skipped 59,036,
              "edges resolved: 44,179 exact (35%) · 18,156 ambiguous · 42,405 unresolved" (all
              kinds; note `cairn stats` on the same DB later reads edges=127,621 / 67,060 resolved —
              post-build derived/materialized edges; the FR-006 metric below is direct SQL, stable).
              DB: /tmp/cairn-survey-A.H1ehk4/acc48fd52e4c6e8d/.kg — SQL verbatim:
                SELECT kind, resolution, COUNT(*) FROM edges WHERE kind IN ('calls','references')
                GROUP BY kind, resolution;
                  calls|ambiguous|17629      calls|exact|42966      calls|unresolved|39953
                  references|ambiguous|20    references|exact|580  references|unresolved|1302
                SELECT ROUND(100.0*SUM(CASE WHEN resolution='exact' THEN 1 ELSE 0 END)/COUNT(*),2),
                COUNT(*) FROM edges WHERE kind IN ('calls','references') AND resolution IN
                ('exact','ambiguous');   →  71.16 | 61195
              Noise areas identical to live DB: chunks|10409|103, datasource|2823|76, other|7467|438.
              skipped_by_reason: gitignored|32606, default_skip|26425 (mostly .venv contents —
              DEFAULT_SKIP_DIRS at scanner.py lines 102-119 incl. vendor/build/.venv), minified|5.
              → TREE BASELINE: exact 43,546 / ambiguous 17,649 / pool 61,195 = 71.16% exact.
              SPEC CLAIM "59.6% exact (39,140/65,728)" REFUTED as the tree's number: it matches the
              STALE-BINARY live DB exactly (39,140/26,588/36,229 re-counted there this session) but
              the tree (post receiver-inference, tree-sitter-only) resolves to 71.16%. Spec's own
              Assumption #4 anticipated this; the re-baselined starting point is 71.16%, not 59.6%.
              Pool definition is one SQL query (spec Assumption #1 VERIFIED — query above).
  status:     DONE-IN-CODE   (baseline established)
  verify:     sqlite3 /tmp/cairn-survey-A.H1ehk4/acc48fd52e4c6e8d/.kg "SELECT ROUND(100.0*SUM(CASE
              WHEN resolution='exact' THEN 1 ELSE 0 END)/COUNT(*),2), COUNT(*) FROM edges WHERE kind
              IN ('calls','references') AND resolution IN ('exact','ambiguous');"   # → 71.16|61195
  gap:        —

item FR-006b: "Exclusion projection (same tree copy + cairn.json) — FR-001's measured effect"
  evidence:   Commands (run this session, verbatim):
                C=$(mktemp -d /tmp/cairn-survey-copy.XXXXXX)   # → /tmp/cairn-survey-copy.PuMN5i
                rsync -a --exclude='.venv' /Users/tanle/Projects/cairn/ "$C"/
                printf '{"exclude": ["src/cairn/dashboard/static/chunks/", "benchmarks/datasource/"]}'\
                  > "$C/cairn.json"
                T2=$(mktemp -d /tmp/cairn-survey-B.XXXXXX)     # → /tmp/cairn-survey-B.YTMZNl
                cd "$C" && CAIRN_HOME=$T2 /Users/tanle/Projects/cairn/.venv/bin/cairn build
              Build summary: files 438 (617−179), symbols 7,467 (= build A's "other" count exactly),
              edges 45,372. DB /tmp/cairn-survey-B.YTMZNl/c4c4a0509505d258/.kg — SQL verbatim:
                calls|ambiguous|4786   calls|exact|14566   calls|unresolved|16318
                references|exact|434   references|unresolved|1171   (references ambiguous: 0 rows)
                pool query (as in FR-006a) → 75.81 | 19786
                noise symbols under either excluded prefix: 0
                skipped_by_reason: gitignored|32606, default_skip|1659, config_exclude|179
                (103 under chunks/, 76 under datasource/), minified_asset|5
              → PROJECTION: exact 15,000 / ambiguous 4,786 / pool 19,786 = 75.81% exact.
              FR-006 TARGET VERDICT: exclusion alone moves 71.16% → 75.81% (+4.65pp). The ≥95%
              exact target is NOT reachable by exclusion + existing machinery (75.81% measured);
              per spec Risk #1 this is D-### adjudication material (re-derive the target or add
              resolution work), never a silent audit failure. Embedding-coverage leg: fresh builds
              have 0 embeddings (build does not embed); live DB has 10,880/20,296 (53.6%, all model
              BAAI/bge-m3) — re-counted this session, matches the spec claim exactly.
  status:     DONE-IN-CODE   (projection established)
  verify:     sqlite3 /tmp/cairn-survey-B.YTMZNl/c4c4a0509505d258/.kg "SELECT reason, COUNT(*) FROM
              skipped_files GROUP BY reason;"   # config_exclude|179
  gap:        —

item CONV: "Test conventions for the C-02/C-04 items in this spec"
  evidence:   C-02 failing-test-first exemplar: tests/test_workflow_audit_fixes.py:
              test_incremental_repairs_incoming_edges:52 — docstring states the pre-fix failure
              ("Before the fix: reindex deletes b.kt's symbols ... never re-resolves the incoming
              edge"), fixture builds a two-file workspace, asserts the post-fix invariant; the file
              header names the fix branch (fix/workflow-audit-findings). Same pattern:
              tests/test_reindex_resolution_invariant.py (H8) and tests/test_incremental_derived.py:
              _run_update:395 (asserts errors==[] then compares vs fresh rebuild).
              Layout (one file per area, class-grouped, behavior-named): scanner/config →
              tests/test_gitignore_and_router_fixes.py, tests/test_cross_repo_namespaces.py,
              tests/test_ingest_config.py; embed/mv → tests/test_embeddings_mv.py,
              tests/test_ann_vecmv.py, tests/test_multivector_query.py,
              tests/test_ann_incremental.py; stats → asserted indirectly via
              tests/test_status_resource_health.py (get_stats minimal-schema fixture, line ~242);
              update/watcher → tests/test_workflow_audit_fixes.py, tests/test_watcher_service.py,
              tests/test_incremental_derived.py. C-04 isolation gotchas touching verify commands:
              hermetic suite fixture points HOME/CAIRN_HOME into per-test tmp sandboxes
              (tests/conftest.py lines 47-67); canonical runner
              `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest "$TESTPATH" -q`; no eager
              cairn.cli/cairn.mcp_server imports in test modules; CLI-level tests use
              click CliRunner against tmp DBs (tests/test_embeddings_mv.py:
              test_cli_multivector_flag_wires_to_embed_all:455). OUT-of-suite measurements (like
              builds A/B here) must pin CAIRN_HOME to a mktemp dir — the live ~/.cairn store is
              bound to workspace /Users/tanle/Projects/cairn and may be written by the stale
              installed daemon.
  status:     DONE-IN-CODE   (conventions documented)
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_embeddings_mv.py -q
  gap:        —
```

## Supporting evidence

- **Entry chain for measurements**: repo venv resolves the tree — `uv run python -c "import cairn"
  → 0.20.1 /Users/tanle/Projects/cairn/src/cairn/__init__.py`; `.venv/bin/cairn` is the same
  editable install (used for builds A/B so the measuring binary is THIS tree, not the stale
  ~/.local/bin/cairn 0.20.0).
- **Workspace/store resolution**: workspaces.json maps "/Users/tanle/Projects/cairn" →
  acc48fd52e4c6e8d; the temp-CAIRN_HOME build recreated the same store key under
  /tmp/cairn-survey-A.H1ehk4/ (key is path-derived). discover_repos treats the workspace root as
  the single repo (src/cairn/graph/scanner.py:discover_repos:161; live repos table: one row
  `cairn|cairn|.`); no nested .git (vendor/tree-sitter-kotlin has none — checked).
- **Update-path consumer inventory** (for FR-002 wiring): reindex_paths callers =
  incremental_update (src/cairn/graph/incremental.py:incremental_update:315), watcher catch-up
  `_do_catch_up` (src/cairn/graph/watcher.py line 131), tests; incremental_update callers =
  src/cairn/cli/update.py:update:17 (line 51), src/cairn/graph/watcher.py:_update_pass:440 (line
  506), cli sync, tests. pending_sync table
  (schema.py lines 189-197) is the watcher's staleness marker, cleared by reindex_paths (lines
  198-207, 240-245) — an existing observable channel FR-003 could piggyback, but it tracks FILES,
  not embeds.
- **Degradation surfaces** (FR-003 doctrine): warn_hash_fallback_once (embeddings.py:632),
  warn_ann_fallback_once (ann_index.py:69), telemetry once-per-class events (telemetry/events.py
  lines 184-188), doctor checks `_check_embeddings`/`_check_ann` (system.py:763/787), MCP health
  block degradations list (_server_core.py lines 342-349). `cairn embed` hard-exits on unavailable
  backend (src/cairn/cli/embed.py:_exit_backend_unavailable:11) — do NOT mirror on update.
- **Schema facts**: symbols has NO path column (file_id → files.path is repo-relative; live+temp
  queries JOIN files); edges.resolution ∈ {exact, ambiguous, unresolved} with invariant exact ⇒
  target_id set (tests/test_reindex_resolution_invariant.py header).
- **Context drift vs specs/context/**: pyproject version is now 0.20.1 (context/tech.md last
  stamped 0.20.0); version read this session via tomllib. specs/context/ files otherwise held
  (test runner, markers, doctor sequence re-verified where cited above).
- **Live-DB vs tree-DB deltas** (both re-counted this session): symbols 20,296/609 files (live,
  possibly stale-binary) vs 20,699/617 (tree) — the tree indexes 8 more files; edge split moved
  59.56% → 71.16% pool share. Noise symbol counts are IDENTICAL in both (10,409/2,823).

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
