# Survey: scip-indexing-v2

**Created**: 2026-09-20 | **Baseline**: 0.21.0 @ f6dd3cb
The survey node's output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

Context: the prior SCIP subsystem was removed at commit 0a956fa ("refactor(indexing):
remove SCIP subsystem; tree-sitter-only indexing with higher exact rate"). Its code is
read this session only via `git show 0a956fa^:<path>`; citations to it are marked
"hist" (historical line numbers in the parent of 0a956fa) and are not living files.
The removal spec's as-built docset lives on disk at
`specs/archive/2026-09-11-remove-scip-exact-rate/` (spec/tech-spec/survey).

## Items

```
item FR-001: "import configured SCIP indexes as an edges-only overlay"
  evidence:   `rg -nw scip src/ tests/ pyproject.toml docs/` → zero matches (exit 1).
              `git show 0a956fa --stat`: "src/cairn/parsers/_scip_pb2.py | 111 ---",
              95 files changed — importer/indexers/stub/extra/CLI all removed.
  status:     TODO
  verify:     rg -nw scip src/ tests/ pyproject.toml docs/ ; echo $?   # → 1
  gap:        no importer, no [scip] extra, no build-path hook exists; the whole
              ingestion path must be reintroduced (history precedent only).

item FR-002: "per-file authority: covered file's tree-sitter call/reference edges replaced"
  evidence:   current per-file replace machinery is tree-sitter-only —
              src/cairn/graph/incremental.py:reindex_paths:19; incremental_update
              docstring (src/cairn/graph/incremental.py:incremental_update:343):
              "Uses `git diff` to find changed source files, deletes their old
              symbols/edges, and re-parses + inserts them". History shows the old
              model was name-keyed symbol merge, not per-file: 0a956fa^ scip_importer.py
              hist:329 `_merge_scip_defs_into_tree_sitter`; 0a956fa^ builder.py
              (hist 558-645, read this session) reverted whole languages on
              `added > 0 and merged == 0`.
  status:     TODO
  verify:     git show 0a956fa^:src/cairn/parsers/scip_importer.py | rg -n "_merge_scip_defs_into_tree_sitter"   # → hist:329
  gap:        no covered-file edge-replacement path; edges carry no provenance column
              to key per-file authority on (schema.py:50 edges table has only
              id/source_id/target_id/target_name/kind/line/column).

item FR-003: "position join; out-of-workspace targets tagged unresolved"
  evidence:   the resolution vocabulary exists — src/cairn/graph/resolver.py:resolve_edge:204;
              resolver.py:219-220: "of ``'exact'``, ``'ambiguous'``, ``'unresolved'``.
              When ``ambiguous`` or ``unresolved``, ``target_id`` is ``None``.";
              resolver.py:244: `return None, "unresolved"  # external/stdlib call`.
              Persisted: schema.py:459 `EDGE_RESOLUTION_MIGRATION = "ALTER TABLE edges
              ADD COLUMN resolution TEXT"` (in the applied MIGRATIONS list at
              schema.py:527). No position-join code: `rg -in "join.rate|anomal" src/cairn/`
              → exit 1; history's join was name-keyed (FR-002 evidence).
  status:     PARTIAL
  verify:     rg -n "def resolve_edge" src/cairn/graph/resolver.py   # → 204
  gap:        the occurrence-range → containing-tree-sitter-symbol join does not exist
              anywhere; only the target-side resolution vocabulary it must reuse exists.

item FR-004: "per-document repo attribution via shared path normalization"
  evidence:   the shared repo-relative identity exists —
              src/cairn/graph/scanner.py:repository_id:177,
              src/cairn/graph/scanner.py:discover_repos:240,
              src/cairn/graph/scanner.py:resolve_repo_path:272; the incremental path
              rides it (incremental.py:370-377:
              `[scanner_mod.repository_id(r) for r in scanner_mod.discover_repos(workspace)]`).
              History shows the failure mode this FR fixes — 0a956fa^ builder.py
              (read this session, hist ~578-583):
              `repo_for_scip = next((str(_r) for _r in repos_seen), "default")` with the
              comment "for multi-repo the index is still imported under one id".
  status:     PARTIAL
  verify:     rg -n "def repository_id" src/cairn/graph/scanner.py   # → 177
  gap:        no importer exists to attribute per document; the identity substrate it
              must share does exist and is multi-repo aware.

item FR-005: "anomalous position-join rate → retain tree-sitter edges, record observably"
  evidence:   `rg -in "join.rate|anomal" src/cairn/` → zero matches (exit 1). The only
              historical precedent is the coarse per-language fallback, not a rate gate —
              specs/archive/2026-09-11-remove-scip-exact-rate/tech-spec.md: "Revert-to-
              pure-SCIP ... trigger `added > 0 and merged == 0` (612)".
  status:     TODO
  verify:     rg -in "join.rate|anomal" src/cairn/ ; echo $?   # → 1
  gap:        no per-document join-rate measurement, no anomaly gate, no observable
              anomaly record; the threshold is recorded nowhere yet (tech-spec.md is
              the unfilled template).

item FR-006: "protobuf runtime absent/mismatched → succeed via tree-sitter, [scip] hint"
  evidence:   current tree has nothing to guard: `rg -nw scip src/` → exit 1. Degrade
              precedent verbatim in history — 0a956fa^ scip_importer.py hist 43-53:
              "# --- protobuf availability ---" ... "except Exception:" ("A missing
              runtime OR a protobuf runtime older than the stub's gencode ... both
              degrade the same way"); hist:60 `def scip_available() -> bool: """True iff
              the protobuf runtime + vendored stub import cleanly."""`; 0a956fa^
              builder.py ImportError arm: "SCIP indexes configured but [scip] extra not
              installed; using tree-sitter fallback".
  status:     TODO
  verify:     git show 0a956fa^:src/cairn/parsers/scip_importer.py | sed -n '43,66p'
  gap:        no [scip] install hint, no observable fallback record, and no import hook
              exist in the current build path.

item FR-007: "configured-but-absent index generated exactly once, bounded timeout"
  evidence:   removed module held the full mechanism — 0a956fa^ scip_indexers.py:
              hist:34 `_INDEX_TIMEOUT_S = 30 * 60`; hist:214 "never rebuild an existing
              index."; hist:230 `timeout=_INDEX_TIMEOUT_S,`; `try_generate_index`
              docstring: "**Never raises** -- a missing/failing/timeout indexer logs
              (visible under ``-v``) and returns ``False``". The config hook that drove
              it (0a956fa^ builder.py hist 415-460, `if cfg.scip:` →
              `from ..parsers.scip_indexers import try_generate_index`) was removed too.
  status:     TODO
  verify:     git show 0a956fa^:src/cairn/parsers/scip_indexers.py | rg -n "_INDEX_TIMEOUT_S|never rebuild"
  gap:        no generation trigger, no run-once gate, no timeout anywhere in the tree.

item FR-008: "generation failure → observable record on the skip/fallback surface, hint under -v"
  evidence:   precedent — 0a956fa^ scip_indexers.py: `except (subprocess.SubprocessError,
              OSError) as e:` → log + `return False`; per-tool install hints (e.g.
              scip-python: "It's an npm package (@sourcegraph/scip-python), not pip").
              Current observable skip surface is scanner-owned only — schema.py:171
              `CREATE TABLE IF NOT EXISTS skipped_files (` (reasons 'default_skip' |
              'gitignored' | 'config_exclude' | 'size_cap'), reported by
              src/cairn/graph/stats.py:get_stats:10 (skipped_by_reason, stats.py:61-68)
              and `cairn stats` (src/cairn/cli/core.py:stats:456, display at 486-488).
  status:     TODO
  verify:     rg -n "CREATE TABLE IF NOT EXISTS skipped_files" src/cairn/graph/schema.py   # → 171
  gap:        generation failures have no surface: no registry exists to fail, and
              skipped_files carries no indexer-failure reason.

item FR-009: "registry ships swift, java, kotlin, typescript, python, go, rust"
  evidence:   removed registry matched the FR list exactly — 0a956fa^ scip_indexers.py
              `_KNOWN_INDEXERS: Dict[str, IndexerSpec]` with keys swift, java, kotlin
              (both java+kotlin via scip-java), typescript, python, go, rust (via
              `["rust-analyzer", "scip", repo, "--output", out]`);
              `git show 0a956fa^:src/cairn/parsers/scip_indexers.py | rg -c 'language="'`
              → 7; per-language independent degradation via try_generate_index.
  status:     TODO
  verify:     git show 0a956fa^:src/cairn/parsers/scip_indexers.py | rg -c 'language="'   # → 7
  gap:        the registry is gone from the tree entirely (file removed @ 0a956fa).

item FR-010: "cairn update reindexes covered files via tree-sitter, provenance flips observably"
  evidence:   the update path already reindexes changed files via tree-sitter and never
              invokes an indexer (none exists) —
              src/cairn/graph/incremental.py:incremental_update:343 over
              src/cairn/graph/incremental.py:reindex_paths:19. A symbol-level
              provenance column exists: schema.py:487 `SYMBOL_SOURCE_MIGRATION =
              "ALTER TABLE symbols ADD COLUMN source TEXT"` (comment 483-484:
              "Provenance column on symbols: 'tree_sitter'"), written as the literal
              'tree_sitter' at repository.py:31 in the symbols INSERT. No scip
              provenance value and no observable flip reporting exist (`rg -nw scip src/`
              → exit 1).
  status:     PARTIAL
  verify:     rg -n "tree_sitter" src/cairn/graph/repository.py   # → 31
  gap:        provenance lives on symbols only (no edges.source); there is no scip value
              to flip FROM and no observation surface for the flip.

item FR-011: "transitive-closure redesign within budget at ~5x edges, byte-identical results"
  evidence:   current closure is a full-wipe SQL matrix build —
              src/cairn/graph/dataflow.py:build_transitive_closure:218 ("Precompute
              multi-hop call graph edges into transitive_edges matrix table"), with
              `cur.execute("DELETE FROM transitive_edges")` (dataflow.py:239) then a
              per-depth extension loop `for d in range(1, max_depth):` (dataflow.py:256).
              Incremental maintenance exists: src/cairn/graph/dataflow.py:maintain_transitive_closure:314
              ("re-derives only ``affected_source_ids``"), driven by
              src/cairn/graph/incremental.py:_maintain_derived_indexes:685 behind the
              pre-state check `if pre["closure_built"] and pre["dataflow_built"]:` (incremental.py:407,
              pre-state from src/cairn/graph/incremental.py:_capture_derived_prestate:519);
              fallback full rebuild at src/cairn/graph/incremental.py:_rebuild_derived_indexes:437.
  status:     TODO
  verify:     uv run --no-sync pytest tests/test_dataflow_transitive_closure.py -q   # → 2 passed
  gap:        no redesign, no recorded budget (tech-spec.md is the unfilled template);
              the byte-identical parity gate precedent exists and passes:
              tests/test_incremental_derived.py:test_maintain_transitive_closure_matches_full_rebuild:747
              (`uv run --no-sync pytest tests/test_incremental_derived.py -k "matches_full_rebuild or drops_rows" -q`
              → 2 passed, 60 deselected).

item FR-012: "closure budget enforced as a scaling-suite gate (D-###)"
  evidence:   measurement machinery exists — src/cairn/bench/perf_suite.py:run_perf_suite:64
              times the closure as its own op: perf_suite.py:177
              `report.ops.append(OpTiming(name="build.derived.closure", timing=closure_timing))`;
              CI runs bench advisory-only — .github/workflows/ci.yml:310
              "# --- Bench: advisory perf baseline comparison" (job invokes
              `cairn bench --suite perf` at ci.yml:408). No enforcing budget gate, and
              tech-spec.md holds no D-### decisions (unfilled template).
  status:     PARTIAL
  verify:     rg -n "build.derived.closure" src/cairn/bench/perf_suite.py .github/workflows/ci.yml
  gap:        the gate is advisory, not enforcing; the budget number and its D-### do
              not exist yet.

item FR-013: "stats reports SCIP provenance within the resolution-share surface"
  evidence:   the resolution-share surface exists (landed by the 2026-09-18
              indexing-exact-rate spec) — stats.py:51-55:
              `stats["resolution"] = {"exact": 0, "ambiguous": 0, "unresolved": 0}` ...
              `stats["exact_share"] = stats["resolution"]["exact"] / pool if pool else 0.0`
              (stats.py:54), counted from `SELECT resolution, COUNT(*) ... FROM edges`
              (stats.py:42-45); rendered by `cairn stats` (src/cairn/cli/core.py:stats:456,
              stats.py surface printed at core.py:470-474, exact_share at core.py:474).
              It is global-only: no per-language split, no provenance dimension
              (get_stats tables are by_repo/by_kind only).
  status:     PARTIAL
  verify:     rg -n "exact_share" src/cairn/graph/stats.py src/cairn/cli/core.py
  gap:        no scip provenance breakdown and no per-language exact-share anywhere on
              the surface.

item FR-014: "zero false-exact edges; index/resolver disagreement counted"
  evidence:   no scip edges exist to disagree (`rg -nw scip src/ tests/` → exit 1), and
              no disagreement counter exists. The retrieval ground-truth harness that
              measured the removal spec's "zero false-exact" claim exists —
              src/cairn/eval.py:load_ground_truth:124, src/cairn/eval.py:evaluate_l1_query:246.
  status:     TODO
  verify:     rg -n "def load_ground_truth" src/cairn/eval.py   # → 124
  gap:        both the edges and the disagreement-counting mechanism are absent; only
              the measurement harness precedent exists.

item FR-015: "cairn import-scip CLI + cairn config echoes the scip key"
  evidence:   `rg -in "import.scip" src/cairn/cli/` → exit 1. Config echo has no scip
              key: src/cairn/cli/core.py:config:156 echoes store paths/embed/namespaces
              only; the config parser knows repo_namespaces and taint keys
              (src/cairn/graph/config.py:CairnConfig:11, src/cairn/graph/config.py:load_config:46;
              `_REPO_NAMESPACES_KEY = "repo_namespaces"` at config.py:38,
              `_TAINT_KEY = "taint"` at config.py:40; `rg -n "scip" src/cairn/graph/config.py`
              → exit 1). History: `import-scip` lived in src/cairn/cli/hooks_viz.py
              (removed; 0a956fa stat: "src/cairn/cli/hooks_viz.py | 24 --").
  status:     TODO
  verify:     rg -in "import.scip" src/cairn/cli/ ; echo $?   # → 1
  gap:        both the command and the config key must be reintroduced.

item FR-016: "end-to-end SCIP toolchain guide at docs/scip.md"
  evidence:   `ls docs/`: README.md architecture.md assets benchmarks.md cli-reference.md
              configuration.md diagrams indexing.md knowledge-and-memory.md mcp-tools.md
              proposal-level-up.md release-checklist.md retrieval.md review-checklist.md —
              `test ! -e docs/scip.md` → "docs/scip.md ABSENT"; word "scip" appears
              nowhere in docs/ (rg exit 1).
  status:     TODO
  verify:     test ! -e docs/scip.md && echo ABSENT
  gap:        the guide does not exist; the removed docs/scip.md content (per-indexer
              table incl. npm-not-pip scip-python, macOS-only scip-swift) survives only
              in history (0a956fa stat: "docs/indexing.md | 16 +-").

item FR-017: "[scip] extra (protobuf runtime + vendored stub + regen script) as D-###"
  evidence:   pyproject.toml extras are watch (68), test (69), dev (70), semantic (109),
              ann (119), otlp (128), ingest (141), bench (153) — `rg -n "^scip"
              pyproject.toml` → exit 1. The removed pieces per `git show 0a956fa --stat`:
              "pyproject.toml | 12 --", "scripts/regen_scip_pb2.sh | 97 ---",
              "src/cairn/parsers/_scip_pb2.py | 111 ---", "src/cairn/graph/config.py | 9 +-" .
              tech-spec.md holds no C-03 (or any D-###) — unfilled template.
  status:     TODO
  verify:     rg -n "^scip" pyproject.toml ; echo $?   # → 1
  gap:        extra, stub, regen script, and the recorded decision all absent.
```

## Supporting evidence

Build pipeline (where an overlay hook would slot):
- `src/cairn/cli/core.py:build:258` → `builder.build_graph(...)` (core.py:322) → derived
  indexes best-effort (core.py:356-373: `build_dataflow_index` + `build_transitive_closure`,
  failure captured as `df_error` and surfaced "dataflow skipped:" at core.py:436);
  `src/cairn/graph/builder.py:build_graph:452` wraps `src/cairn/graph/builder.py:_build_graph_impl:269`.
  Build summary panel prints the resolution mix: core.py:422-432
  ("edges resolved: ... exact (%) ... ambiguous ... unresolved").
- History's hybrid hook ran post-resolve, pre-backup (0a956fa^ builder.py hist 553-560:
  "runs AFTER _resolve_all ... BEFORE backup_to"), with `conn.rollback()` on a bad index
  ("Don't fail the build over a bad SCIP index", hist ~594-598) — the removal tech-spec
  (on disk, specs/archive/2026-09-11-remove-scip-exact-rate/tech-spec.md) records the
  same anchor points with hist line numbers.

Closure machinery (FR-011/012/AC4/AC5 load-bearing):
- `src/cairn/graph/dataflow.py:build_transitive_closure:218` — seeds only STRUCTURAL_EDGE_KINDS,
  extends per depth; unresolved-name extension only when the name maps to exactly one
  symbol (dataflow.py:284-303, "Ambiguous names are skipped (left unextended)").
- `src/cairn/graph/dataflow.py:maintain_transitive_closure:314` — per-source restricted
  re-derivation with an explicit parity argument in its docstring; read-side
  `closure_available` / `impact_from_closure` at dataflow.py:565 / dataflow.py:580.
- Incremental driver: `src/cairn/graph/incremental.py:_capture_derived_prestate:519`
  (pre-state key `"closure_built": _has_rows("transitive_edges")` at incremental.py:629),
  `_maintain_derived_indexes` at incremental.py:685, `_rebuild_derived_indexes` at
  incremental.py:437.
- Parity/collision regression tests (byte-identical gate precedent):
  tests/test_dataflow_transitive_closure.py:test_name_collision_no_spurious_edges:16 and
  tests/test_dataflow_transitive_closure.py:test_transitive_closure_respects_resolution:120
  (`uv run --no-sync pytest tests/test_dataflow_transitive_closure.py -q` → 2 passed);
  tests/test_incremental_derived.py:test_maintain_transitive_closure_matches_full_rebuild:747
  and ..._drops_rows_of_deleted_sources:784 (→ 2 passed, 60 deselected).

Resolution vocabulary and stats (FR-003/013/014):
- `src/cairn/graph/resolver.py:resolve_edge:204` returns `(target_id, label)` over
  `'exact' | 'ambiguous' | 'unresolved'`; repo-level rollup `resolve_repo_edges`
  (resolver.py:391) builds `stats = {"exact": 0, "ambiguous": 0, "unresolved": 0}` (resolver.py:418).
- `src/cairn/graph/stats.py:get_stats:10` — resolution counts (stats.py:42-45),
  exact_share/ambiguous_share (stats.py:54-55), skipped_total/skipped_by_reason (stats.py:58-68);
  rendered by `src/cairn/cli/core.py:stats:456` (resolution line 470-475, skipped 477-488).

Schema provenance/skip surface (FR-001/002/006/008/010):
- edges table (schema.py:50): id, source_id, target_id, target_name, kind, line, column —
  no provenance column in CREATE TABLE; `edges.resolution` rides
  EDGE_RESOLUTION_MIGRATION (schema.py:459, applied in MIGRATIONS at schema.py:527);
  `symbols.source` rides SYMBOL_SOURCE_MIGRATION (schema.py:487), written 'tree_sitter'
  (repository.py:31); skipped_files (schema.py:171) with reason CHECK-free TEXT
  ('default_skip' | 'gitignored' | 'config_exclude' | 'size_cap' per comment); transitive_edges (schema.py:430).

Multi-repo identity (FR-004):
- `src/cairn/graph/scanner.py:repository_id:177`, `discover_repos:240`,
  `resolve_repo_path:272`; consumed by incremental_update (incremental.py:370-385) and
  by files.repo_id rows during build.

Bench/CI (FR-012):
- `src/cairn/bench/perf_suite.py:run_perf_suite:64` times the closure standalone
  (perf_suite.py:164-179, op name "build.derived.closure"); CI bench job is advisory
  (.github/workflows/ci.yml:310 comment; `cairn bench --suite perf` at ci.yml:408).

Ground truth (FR-014):
- `src/cairn/eval.py:load_ground_truth:124`, `evaluate_l1_query:246` — the harness behind
  the removal commit's "retrieval ground truth unchanged (558/558 + 234/234)" claim
  (0a956fa commit message; number re-verified as a claim, not re-run here).

Removed subsystem inventory (all evidence via `git show 0a956fa^:<path>`, read this session):
- src/cairn/parsers/scip_importer.py (817 lines): scip_available hist:60, _install_hint
  hist:65, _merge_scip_defs_into_tree_sitter hist:329, import_scip_file hist:789,
  protobuf try/except degrade hist 43-61.
- src/cairn/parsers/scip_indexers.py (248 lines): _INDEX_TIMEOUT_S = 30*60 hist:34,
  _KNOWN_INDEXERS (7 entries: swift, java, kotlin, typescript, python, go, rust),
  try_generate_index (never raises; "never rebuild an existing index." hist:214).
- 0a956fa^ builder.py: config/auto-gen hook hist ~415-460; import+merge+revert hook hist
  ~553-645 incl. `repo_for_scip = next((str(_r) for _r in repos_seen), "default")` (the
  single-repo-id attribution FR-004 replaces) and the `added > 0 and merged == 0`
  per-language revert (the opaque-USR failure FR-002/FR-005 replace).
- scripts/regen_scip_pb2.sh (97 lines), vendored _scip_pb2.py (111 lines), pyproject
  [scip] extra (12 lines), config.py scip key (9 lines), cli hooks_viz import-scip
  (24 lines) — all in `git show 0a956fa --stat`.
- As-built failure-mode record on disk: specs/archive/2026-09-11-remove-scip-exact-rate/
  (tech-spec.md "Revert-to-pure-SCIP ... trigger `added > 0 and merged == 0`";
  spec.md Why of the current spec cites this docset).

Config surface (FR-015/017):
- `src/cairn/graph/config.py:CairnConfig:11` / `load_config:46`; keys
  repo_namespaces (config.py:38) and taint (config.py:40); no scip key (rg exit 1).

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
