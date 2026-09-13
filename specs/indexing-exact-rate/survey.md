# Survey: indexing-exact-rate

**Created**: 2026-09-13 | **Baseline**: 0.20.1 @ 6e8033e (`feat/indexing-exact-rate`, converge re-survey)
CONVERGE RUN: rewritten in place against the IMPLEMENTED tree. HEAD is the docset
commit 6e8033e; the working tree carries the plan's uncommitted implementation
(git status: 18 modified sources/tests + untracked `cairn.json` and
`tests/test_update_path_embedding.py`). This file supersedes the ac09d4b
baseline survey (preserved in git history). Every code citation below was
re-grepped and every verify command re-run in the converge session; statuses
flipped GAP to DONE-IN-CODE where the implementation satisfies the item.

## Items

```
item FR-001a: "Scanner Layer C exclusion machinery (cairn.json exclude/include) exists and works"
  evidence:   src/cairn/graph/config.py:load_config:70 (reads cairn.json at workspace/repo root;
              exclude/include/repo_namespaces/ingest keys, dataclass src/cairn/graph/config.py:CairnConfig:32);
              src/cairn/graph/scanner.py:_build_config_spec:323 compiles cfg.exclude/cfg.include into
              pathspec gitignore PathSpecs (repo-root-relative); src/cairn/graph/scanner.py:classify_file:376
              runs Layer C at lines 419-420 — `if exclude_spec is not None and _match_root_relative(...):
              return False, REASON_CONFIG_EXCLUDE` (constant at scanner.py line 128); skips persisted by
              src/cairn/graph/builder.py:_record_skips:160 (INSERT INTO skipped_files, call site
              builder.py line 455, whose log line names config-exclude); skipped_files table at
              src/cairn/graph/schema.py:171. include overrides A/B/C but NOT Layer D
              (classify_file docstring lines 385-388; enforced at lines 395-402 — an included
              path still hits _is_minified and the size cap). Live proof on the implemented
              tree: the converge build (repo venv, pinned store, cairn.json present at root)
              recorded skipped_by_reason config_exclude|179 and zero symbols under both
              excluded prefixes (SQL outputs under FR-006b).
  status:     DONE-IN-CODE
  verify:     sqlite3 /tmp/cairn-conv-A.THMFf1/acc48fd52e4c6e8d/.kg "SELECT reason, COUNT(*)
              FROM skipped_files GROUP BY reason;"   # config_exclude|179 among the rows
  gap:        —

item FR-001b: "cairn.json exists at repo root with the exclusion globs the build validated"
  evidence:   `cat cairn.json` (this session):
                {"exclude": ["src/cairn/dashboard/static/chunks/", "benchmarks/datasource/"]}
              — exactly the globs build B validated at ac09d4b (zero symbols under both
              prefixes, 7,467 "other" symbols then). Exclusion behavior re-proven on the
              implemented tree by the converge build: files 439, symbols 7,478, noise under
              either prefix 0, config_exclude|179 (FR-006b). Git state this session:
              `git status --short -- cairn.json` → `?? cairn.json` — the file is present but
              UNTRACKED; the commit lands with the implementation batch (agents never
              commit; the tree is this survey's surface).
  status:     DONE-IN-CODE   (was GAP at the ac09d4b baseline)
  verify:     cd /Users/tanle/Projects/cairn && cat cairn.json && git status --short -- cairn.json
  gap:        —   (the git commit itself is the orchestrator's; code state is complete)

item FR-001c: "Noise symbols in the CURRENT live DB + why Layer D's minified skip misses them"
  evidence:   live DB /Users/tanle/.cairn/acc48fd52e4c6e8d/.kg (sqlite3, converge session —
              the store still holds the stale-binary pre-exclusion build):
                symbols by area (JOIN files):  chunks|10409|103   datasource|2823|76   other|7064|430
                totals: 20296 symbols / 609 files
              → noise = 13,232 symbols / 179 files — byte-identical to the ac09d4b count
              (re-counted, matches). The chunks are `chunk-*.mjs`/`railroad-*.mjs` under
              src/cairn/dashboard/static/chunks/mermaid.esm.min/; Layer D misses them because
              src/cairn/graph/scanner.py:_is_minified:365 matches a FILENAME marker (docstring
              lines 366-368) — the `.min` here is a DIRECTORY component, and the files are
              206-line pretty-printed ESM chunks under MAX_FILE_SIZE (scanner.py:123). The
              implemented tree no longer has this problem: the converge build (cairn.json
              active) indexes 0 symbols under both prefixes (FR-006b); a live-store rebuild
              picks that up on next `cairn build`.
  status:     DONE-IN-CODE   (measurement established; explains why the config exclude is needed)
  verify:     sqlite3 /Users/tanle/.cairn/acc48fd52e4c6e8d/.kg "SELECT CASE WHEN f.path LIKE
              'src/cairn/dashboard/static/chunks/%' THEN 'chunks' WHEN f.path LIKE
              'benchmarks/datasource/%' THEN 'datasource' ELSE 'other' END a, COUNT(*),
              COUNT(DISTINCT f.id) FROM symbols s JOIN files f ON s.file_id=f.id GROUP BY a;"
  gap:        —

item FR-001d: "bench/eval corpus discovery reads from DISK, not the graph — exclusion is safe"
  evidence:   src/cairn/bench/datasource.py:default_manifest_path:414 locates
              benchmarks/datasource/manifest.json via cwd then source-tree parents (docstring
              lines 415-417); default_baselines_root:433 same two-candidate precedence.
              scripts/verify_ground_truth.py:build_fresh_graph:188 COPIES the yarl snapshot
              to a throwaway workspace and build_graph's the copy (the copy, never the
              committed tree, receives the .git marker). scripts/verify_datasource.py reads
              DEFAULT_MANIFEST from disk (line 87). Bench suites build synthetic corpora
              into throwaway DBs (src/cairn/bench/corpus.py:generate_corpus:22). Empirical
              re-run in the converge session FROM the cairn.json-carrying tree root:
                .venv/bin/python -c "from cairn.bench.datasource import default_manifest_path;
                p=default_manifest_path(); print(p, p.is_file() if p else None)"
              → /Users/tanle/Projects/cairn/benchmarks/datasource/manifest.json True
  status:     DONE-IN-CODE   (spec assumption VERIFIED)
  verify:     cd /Users/tanle/Projects/cairn && .venv/bin/python -c "from cairn.bench.datasource
              import default_manifest_path; p=default_manifest_path(); print(p, p.is_file())"
  gap:        —

item FR-002: "WHEN `cairn update` creates/changes symbols and a backend is available, it embeds them before reporting completion"
  evidence:   Production call site landed in reindex_paths POST-COMMIT:
              src/cairn/graph/incremental.py:reindex_paths:24 collects the NEW symbol ids per
              file via insert_parsed_file's name_to_symbol_ids (lines 239, 242-245), COMMITs
              the reindex at line 256, then — per the in-code comment at lines 257-260
              ("embed_symbols self-commits its batches: keep it after the COMMIT above"; this
              ordering is tech-spec D-002) — gated at line 268 `if embeddings_available():`,
              calls `embedded_symbols += embed_symbols(conn, new_ids)["embedded"]` at line
              270. Single-file path covered: _reindex_file:879 delegates to reindex_paths at
              line 891. The return dict (line 348, docstring line 31) is
              `{"reindexed", "deleted", "embedded_symbols", "deferred_embeds", "errors"}`;
              incremental_update:352 consumes it (call site line 396) and passes
              deferred_embeds through its own return dict (lines 430-436). embed_symbols
              itself: src/cairn/graph/embeddings.py:embed_symbols:1551 ("the per-upsert ANN
              sync seam", docstring lines 1557-1578; syncs vec0 in-transaction lines
              1657-1658; returns {model, embedded, skipped, ann_synced} lines 1665-1670).
              Outside incremental.py, embed_symbols callers are tests only
              (tests/test_ann_incremental.py:112 et al.). Tests:
              tests/test_update_path_embedding.py:test_reindex_embeds_changed_file_symbols:68
              asserts the re-created symbols are embedded AND the embedded_symbols count is
              reported (lines 118-122); tests/test_signal_persistence.py:93 pins the return
              dict exactly: `{"reindexed": 1, "deleted": 0, "embedded_symbols": 2,
              "deferred_embeds": 0, "errors": []}`. Both green in the converge run (66
              passed across the six cited test files).
  status:     DONE-IN-CODE   (was GAP at the ac09d4b baseline — seam had zero production callers)
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest
              tests/test_update_path_embedding.py tests/test_signal_persistence.py -q
  gap:        —

item FR-003: "IF no backend is available, `cairn update` succeeds and surfaces the deferred embeds (log/doctor-observable), never fails"
  evidence:   Degrade legs in reindex_paths (incremental.py lines 261-275): closed gate →
              `deferred_embeds += len(new_ids)` (lines 274-275); embed failure → logger.debug
              + defer (lines 271-273); neither raises. WARN per pass at lines 342-347:
              "Deferred embedding %d symbol(s): no usable embedding backend this pass; run
              `cairn embed` once a backend is reachable". CLI surface: src/cairn/cli/update.py
              reads `result.get("deferred_embeds")` at line 74 and display.warning's at lines
              75-78; the update exits 0. Count passes through incremental_update's return
              (incremental.py line 435). Doctrine precedents re-anchored: gate
              src/cairn/graph/embeddings.py:embeddings_available:94; in-repo degrade shape
              src/cairn/knowledge/ingest/executor.py:execute_manifest:10 lines 63-66
              (embedded=None carried in the summary, line 88); warn-once pair
              warn_hash_fallback_once (embeddings.py:632) and warn_ann_fallback_once
              (ann_index.py:69). Test: tests/test_update_path_embedding.py:
              test_reindex_without_backend_succeeds_and_defers_observably:146 asserts success
              + one WARN + deferred_embeds count (lines 182-186) — green in the converge run.
  status:     DONE-IN-CODE   (was GAP at the ac09d4b baseline — no deferred-embed surface)
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest
              tests/test_update_path_embedding.py -q
  gap:        —

item FR-004: "`cairn embed` builds embeddings_mv by default; --no-multivector restores single-vector"
  evidence:   CLI flag pair: src/cairn/cli/embed.py lines 161-165 —
              `"--multivector/--no-multivector", default=True,` help: "Also embed name-only
              and docstring-only vectors (embeddings_mv). --no-multivector stores one vector
              per symbol (the single-vector build)." Library default flipped:
              src/cairn/graph/embeddings.py:embed_all:1404 takes `multivector: bool = True`
              (line 1411; docstring lines 1427-1433: False performs ZERO embeddings_mv
              writes, base flow byte-identical, D-006/TC-020); mv pass gated at lines
              1527-1531 (`_embed_mv_kinds(...) if multivector else None`), summary carries
              "mv_embedded" only when on (lines 1546-1547); producer
              _embed_mv_kinds:1267 ("default on; False opts out", lines 1278-1279) over
              MV_KINDS = ("name", "docstring") at embeddings.py line 292. Schema comment now
              default-on (src/cairn/graph/schema.py lines 221-223), embeddings_mv table at
              line 227, model index at 238. ANN: flagless embed rebuilds BOTH indexes —
              cli/embed.py lines 307-316 rebuild_index(source="embeddings_mv") under the
              flag; converge build printed "ANN index rebuilt: 7,478 vectors" AND "MV ANN
              index rebuilt: 10,941 vectors". Bench pins (D-003, timed ops):
              src/cairn/bench/perf_suite.py:160, agent_suite.py:607, scaling_suite.py:92 all
              `embed_all(..., multivector=False)` with the pin comment. Rewritten pins
              (C-02): tests/test_ann_vecmv.py:test_opt_out_build_never_creates_vecmv_table:85
              (renamed from test_default_build_never_creates_vecmv_table),
              tests/test_embeddings_mv.py:test_default_embed_all_writes_mv_rows_and_reports_mv_embedded:149
              (renamed from the flag-off pin) and
              test_explicit_opt_out_restores_single_vector_build:168,
              test_cli_multivector_flag_wires_to_embed_all:463 (docstring now covers the
              flagless default). QUERY SIDE UNTOUCHED (D-003): `git diff HEAD --stat --
              src/cairn/graph/semantic.py` → empty (converge session);
              src/cairn/graph/semantic.py:RetrievalParams:343 multivector field at line 456 (None never
              reads embeddings_mv); query-side pins keep their names —
              tests/test_multivector_query.py:210, :221, :465. All green in the converge
              run (66 passed).
  status:     DONE-IN-CODE   (was PARTIAL at the ac09d4b baseline — machinery built, default off)
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest
              tests/test_embeddings_mv.py tests/test_ann_vecmv.py
              tests/test_multivector_query.py tests/test_ann_incremental.py -q
  gap:        —

item FR-005: "`cairn stats` reports edge resolution shares (exact / ambiguous / unresolved)"
  evidence:   src/cairn/graph/stats.py:get_stats:17 adds the resolution block (lines 43-62):
              GROUP BY resolution over `kind IN ('calls','references')` only (lines 49-52),
              best-effort for pre-migration DBs (OperationalError → note_contention +
              zero defaults, lines 55-58), then `stats["resolution"]` dict (line 58), pool =
              exact+ambiguous (line 60), `exact_share`/`ambiguous_share` (lines 61-62).
              stats["edges_resolved"] keeps its all-kind target_id semantics (lines 40-42).
              Re-export path for tests: src/cairn/graph/queries.py:16. CLI renders it:
              src/cairn/cli/core.py:stats:449 prints `edges  N (M resolved)` (line 462) and
              the new row (lines 463-468): `resolution  X exact / Y ambiguous / Z unresolved
              (P% / Q% of pool)`. Live proof: the converge build's `cairn stats` printed
              `resolution  15,030 exact / 4,803 ambiguous / 17,530 unresolved (76% / 24% of
              pool)` (full output under FR-006b). Dedicated tests:
              tests/test_status_resource_health.py:TestStatsResolutionShares (class at line
              359; test_resolution_counts_and_pool_shares:371 asserts counts scoped to the
              pool and shares over exact+ambiguous, lines 410-417;
              test_minimal_schema_defaults_resolution_to_zero:419 asserts the degrade path,
              lines 443-445) — 13 passed in the converge run.
  status:     DONE-IN-CODE   (was GAP at the ac09d4b baseline — no resolution shares)
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest
              tests/test_status_resource_health.py -q   # 13 passed
  gap:        —

item FR-006a: "Acceptance pool measured on the implemented tree (committed exclusion active) — headline numbers"
  evidence:   T012 procedure run in the converge session (repo venv binary, pinned store,
              never the installed 0.20.0):
                T=$(mktemp -d /tmp/cairn-conv-A.XXXXXX)     # → /tmp/cairn-conv-A.THMFf1
                cd /Users/tanle/Projects/cairn && CAIRN_HOME=$T .venv/bin/cairn build
                CAIRN_HOME=$T .venv/bin/cairn embed
              Build summary: repos 1, files 439, symbols 7,478, edges 45,472, skipped
              59,215; rail "edges resolved: 15,120 exact (33%) · 4,806 ambiguous · 18,518
              unresolved" (all kinds; `cairn stats` on the same DB after derived edges reads
              15,030/4,803/17,530 — the FR-006 metric below is direct SQL, stable).
              Embed summary: embedded 7,478, skipped 0, reaped 0, model BAAI/bge-m3, mv
              vectors 10,941. DB /tmp/cairn-conv-A.THMFf1/acc48fd52e4c6e8d/.kg — pool SQL:
                SELECT ROUND(100.0*SUM(CASE WHEN resolution='exact' THEN 1 ELSE 0 END)/COUNT(*),2),
                COUNT(*) FROM edges WHERE kind IN ('calls','references')
                AND resolution IN ('exact','ambiguous');
              → 75.78 | 19833. Split: calls ambiguous|4803, exact|14596, unresolved|16359;
              references exact|434, unresolved|1171; references ambiguous 0 rows.
              → MEASURED: exact 15,030 / ambiguous 4,803 / pool 19,833 = 75.78% exact,
              24.22% ambiguous. D-001 gate (≥75.00 exact / ≤25 ambiguous): PASS, 0.78pp
              margin. Comparison-only baselines recorded in task.md lines 236-238 (claims,
              not this tree): 71.16% tree-without-exclusion and 75.81% projection, both
              measured at ac09d4b; the implemented tree drifted +47 pool edges / +11
              symbols since (files 438→439) and still clears the gate.
  status:     DONE-IN-CODE   (acceptance measured on the implemented tree)
  verify:     sqlite3 /tmp/cairn-conv-A.THMFf1/acc48fd52e4c6e8d/.kg "SELECT
              ROUND(100.0*SUM(CASE WHEN resolution='exact' THEN 1 ELSE 0 END)/COUNT(*),2),
              COUNT(*) FROM edges WHERE kind IN ('calls','references') AND resolution IN
              ('exact','ambiguous');"   # → 75.78|19833
  gap:        —

item FR-006b: "Embedding-coverage leg (100% after a flagless embed) + exclusion effect on the implemented tree"
  evidence:   Same converge store /tmp/cairn-conv-A.THMFf1/acc48fd52e4c6e8d/.kg, after the
              flagless `cairn embed` (FR-004 default):
                coverage: SELECT ROUND(100.0*(SELECT COUNT(*) FROM embeddings)/(SELECT
                COUNT(*) FROM symbols),2), ... → 100.0 | 7478 embeddings | 7478 symbols
                (GROUP BY model: BAAI/bge-m3|7478 — one model, fully covered)
                mv rows: SELECT COUNT(*) FROM embeddings_mv → 10941
                noise: symbols JOIN files under either excluded prefix → 0
                skipped_by_reason: gitignored|32606, default_skip|26425 (mostly .venv —
                DEFAULT_SKIP_DIRS at scanner.py:102), config_exclude|179 (FR-001a),
                minified_asset|5 (MAX_FILE_SIZE/minified markers at scanner.py:123)
              Stats-surface cross-check (AC2 after): `CAIRN_HOME=$T .venv/bin/cairn stats`
              prints `resolution  15,030 exact / 4,803 ambiguous / 17,530 unresolved (76% /
              24% of pool)` — same shares the pool SQL gives at 2-digit rounding (75.78→76,
              24.22→24). Live-DB comparison (re-counted this session): the stale store sits
              at 10,880/20,296 (53.6%) — the implemented tree's update+embed path is what
              closes that gap (FR-002/FR-003).
  status:     DONE-IN-CODE   (acceptance measured on the implemented tree)
  verify:     sqlite3 /tmp/cairn-conv-A.THMFf1/acc48fd52e4c6e8d/.kg "SELECT
              ROUND(100.0*(SELECT COUNT(*) FROM embeddings)/(SELECT COUNT(*) FROM
              symbols),2);"   # → 100.0
  gap:        —

item CONV: "Test conventions for this spec's items (post-implementation names)"
  evidence:   C-02 failing-test-first exemplar: tests/test_workflow_audit_fixes.py:
              test_incremental_repairs_incoming_edges:52 (docstring states the pre-fix
              failure; the same pattern now in tests/test_update_path_embedding.py:68 —
              "Before the fix: ... the return dict exposes no embedded_symbols count").
              RENAMED since the baseline (re-anchored): test_ann_vecmv.py:85 →
              test_opt_out_build_never_creates_vecmv_table; test_embeddings_mv.py:149 →
              test_default_embed_all_writes_mv_rows_and_reports_mv_embedded, :168 →
              test_explicit_opt_out_restores_single_vector_build. NEW file:
              tests/test_update_path_embedding.py (FR-002/FR-003 behavior). Re-pinned
              numbers: tests/test_signal_persistence.py:93 expects embedded_symbols=2 /
              deferred_embeds=0 in the reindex_paths return dict;
              tests/test_ann_incremental.py:196-203 reap pin now `reaped == 3` (1 base + 2
              mv rows, FR-004 default). Stats pins moved to a dedicated class:
              tests/test_status_resource_health.py:359-445. Isolation gotchas:
              tests/conftest.py points HOME/CAIRN_HOME into per-test tmp sandboxes (lines
              47, 64-70) and re-points paths.py's derived stores layout as a group (lines
              81-83); canonical runner `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test
              pytest "$TESTPATH" -q`. OUT-of-suite measurements pin CAIRN_HOME to a mktemp
              dir and use the repo venv binary (converge build above; the live ~/.cairn
              store is bound to this workspace and held the stale-binary build).
  status:     DONE-IN-CODE   (conventions documented against post-implementation names)
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest
              tests/test_update_path_embedding.py tests/test_embeddings_mv.py -q
  gap:        —
```

## Supporting evidence

- **Entry chain for measurements**: repo venv resolves the tree — `.venv/bin/python -c
  "import tomllib; ..."` reads pyproject version 0.20.1 (converge session); `.venv/bin/cairn`
  is the editable install of THIS tree and ran the converge build/embed/stats (the stale
  installed `~/.local/bin/cairn` 0.20.0 was never invoked).
- **Workspace/store resolution**: workspaces.json maps "/Users/tanle/Projects/cairn" →
  acc48fd52e4c6e8d; the converge build with CAIRN_HOME pinned to /tmp/cairn-conv-A.THMFf1
  recreated the SAME store key under it (key is path-derived), matching the ac09d4b
  observation. discover_repos treats the workspace root as the single repo
  (src/cairn/graph/scanner.py:discover_repos:161).
- **Update-path consumer inventory** (post-implementation): embed_symbols has EXACTLY ONE
  production caller — incremental.py:270 (post-COMMIT, D-002). reindex_paths callers:
  incremental_update (incremental.py:396), watcher catch-up _do_catch_up
  (src/cairn/graph/watcher.py:131), `cairn sync` (src/cairn/cli/system.py:594),
  _reindex_file (incremental.py:891), tests. incremental_update callers:
  src/cairn/cli/update.py:51, watcher _update_pass (watcher.py:506), tests. The watcher
  path therefore inherits the embed/defer behavior through the same seam — no separate
  wiring exists or is needed.
- **Degradation surfaces** (FR-003): the update-path defer WARN (incremental.py:342-347)
  + CLI display.warning (update.py:75-78) + deferred_embeds in both return dicts
  (incremental.py:348, :435); warn-once precedents warn_hash_fallback_once
  (embeddings.py:632) / warn_ann_fallback_once (ann_index.py:69); doctor checks
  unchanged by this spec. `cairn embed` still hard-exits on an unavailable backend —
  intentionally NOT mirrored on the update path (spec FR-003).
- **Schema facts** (re-checked): symbols has NO path column (file_id → files.path is
  repo-relative; all converge queries JOIN files); edges.resolution ∈ {exact, ambiguous,
  unresolved}; embeddings_mv is additive (plain CREATE TABLE IF NOT EXISTS, schema.py
  lines 221-227, no MIGRATIONS entry); base embeddings PK unchanged.
- **Build-rail vs stats delta**: the build summary rail (15,120 exact / 4,806 ambiguous)
  and the later `cairn stats` read (15,030 / 4,803) differ because derived/materialized
  edges land after the rail prints; both agree within noise, and the FR-006 metric is the
  direct pool SQL (75.78 | 19833), stable across both reads.
- **Live-DB vs implemented-tree deltas** (both re-counted this session): live store
  20,296 symbols / 609 files, noise 13,232 (stale-binary build, pre-exclusion — a rebuild
  with the now-present cairn.json drops it, measured 0 in the converge build);
  implemented tree 7,478 symbols / 439 files, coverage 100.0%, mv rows 10,941.
- **Context drift vs specs/context/**: none material — pyproject still 0.20.1; test
  runner, markers, and isolation conventions held. Per-area anchors that moved since the
  context stamp are re-anchored above (scanner constants DEFAULT_SKIP_DIRS:102 /
  MAX_FILE_SIZE:123; schema.py mv comment now default-on at 221-223).

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
