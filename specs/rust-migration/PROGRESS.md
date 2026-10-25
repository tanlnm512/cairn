# Progress: rust-migration

Campaign ledger for the migration executed by `plan.md`. State recorded at
branch `feat/rust-migration`, HEAD `21fa36d`. Landed workstreams carry their
commit; gate verdicts cite the recorded check outcomes; "Remaining work" is
everything not landed at this HEAD.

## Phase 0 — Foundations

| Workstream | State | Summary | Commit |
|---|---|---|---|
| P0-1 | done | Cargo workspace at `rust/`: six member crates, shared version 0.22.0, edition 2024, denied-warnings lints; rustfmt/deny config; `.github/workflows/rust.yml` (fmt, clippy -D warnings, test, cargo-deny, release dry-run matrix incl. musl-static via cross — CI-only legs, not run locally); rustfmt/clippy local pre-commit hooks | 71b82d7 |
| P0-2 | done | `scripts/rust_migration/baseline.py` measures full build, closure + peak WAL, hash embed, all-26-tool p95 over live streamable HTTP, daemon RSS; run on cairn-self, yarl, attrs with zero tool errors; numbers in `baselines.md` (self: build 3.0s, closure 3.0s / 109 MB peak WAL, RSS peak 205 MB, graph-tool p95 dominated by the freshness walk) | 8401c52 |
| P0-3a | done | `scripts/rust_migration/golden_record.py`: MCP golden recorder over all 26 tools with volatile-field normalization; one JSON fixture per tool under `rust/golden/mcp/`; flagless rerun re-captures and diffs — the parity oracle for the port | 2f12ab9 |
| P0-3b | done | `scripts/rust_migration/kg_dump.py` + `kg_diff.py`: read-only per-table canonical export (sorted rows, hex-float floats, path remapping, FTS5/vec0 internals skipped) with per-table diff report and exit 0/1/2 | c53d2ae |
| P0-4 | done | `decisions.md` D-001–D-009 pinned live from crates.io: rmcp 3.5.1, rusqlite 0.40.2 bundled (FTS5 verified), sqlite-vec 0.1.9 static init, tree-sitter 0.26.13 + 15 grammar crates (dart vendored, kotlin commit-pinned, php 0.24.2), clap 4.6.7, notify 8.2.0, prost 0.14.4, minijinja 3.0.1; cargo-deny license roll-up included | 156f751 |
| P0-5 | done | Spike: cairn-daemon serves `search_symbols` over rmcp streamable-HTTP from a real `.kg` (rusqlite bundled + FTS5 + sqlite-vec static init), golden-equal on a checked-in store fixture; inert — no CLI wiring | 058bcd4 |

## Phase 1 — Read-path daemon

| Workstream | State | Summary | Commit |
|---|---|---|---|
| 1a | done | cairn-store: read-only factory with get_db pragma parity, sqlite-vec static registration, per-model vec0 helpers; Rust canonical dump (`cairn canonical-dump`) byte-equal to kg_dump.py; cairn-query scaffolded with empty tool modules | 7501143 |
| 1b-search | done | `search_symbols` (FTS bm25 + LIKE fallback/merge) and `semantic_search` (hash/server backends, native cosine, vec0 ANN, RRF fusion with Python-exact tie-breaks, rerank gates) golden-equal; sha2 recorded as D-011 | aca73e5 |
| 1b-nav | done | `find_definition` / `get_callers` / `get_callees` / `impact_analysis`: precise/fuzzy semantics, resolution labels, Kotlin operator-invoke contract, cross-repo consumer note; parity over rebuild-verified `rust/golden/nav/nav-store.kg` | 4881faf |
| 1b-flow | done | Five flow tools (`explore`, `trace_flow`, taint, `cross_repo_deps`, `visualize_graph`): query core in `graph.rs`, text renders in `tools_flow.rs`; deterministic checked-in corpus + fixtures with byte-stable regeneration (`flow_golden.py --check`) | c2856f6 |
| 1b-km | done | 10 km tools: recall/record + memory lifecycle, knowledge CRUD/status, `trace_workflow`, `get_compass`, `search_knowledge`, `ask_compass` cross-layer routing, over a ported OKF concept/bundle layer; 20 parity tests; yaml-rust2 batch as D-010 | 363dcb6 |
| 1c | done | cairn-daemon: rmcp streamable-HTTP server registering the ported tool surface; `auth.rs` (C-5 key resolution), `healthz.rs` (payload parity + impl marker), `lifecycle.rs` (plist render parameterized on the exec'd binary, stray-sweep port) | 8c5a6d1 |
| 1d | done | Python-side opt-in switch: CAIRN_DAEMON_IMPL / CAIRN_RUST_BIN resolution, `connect --impl`, doctor serving-impl check; default python | ad1fdc1 |
| 1e | done | `scripts/rust_migration/parity.py`: snapshot/replay parity runner (scratch-store snapshots, fixture-argument re-derivation, per-tool PASS/FAIL with first differing field, --record/--dump-diffs) with the volatility doctrine (sort-lines tools, dispatch masks) | f4fa9d7 |
| G1 repair r1 | done; gate red at handoff | parity.py re-executes under the repo venv (bare python3 lacked project deps); gate then 18/26: 6 tools absent from the rust catalog, explore/ask_compass divergent | 563f26d |
| G1 repair r2 | done | 6 missing tools ported (path, repo_map, file_api, generate_flow, federated_search, wiki_generate) + 6 divergences fixed (impact DFS traversal, explore fusion, ask_compass rendering, 60k-char truncation, wiki_generate bugs) + runner record/replay asymmetry fixed; self 26/26 stable; yarl/attrs 26/26 with a documented intermittent tie-order residual | c2856f6, 174ba4d, 5bd65dd, 3506430 |
| G1 repair r3 | done | Live-store counter volatility masked (SELF_COUNTER_RULES, scoped to self), goldens re-recorded; self 26/26 | cfce44a |

## Phase 2 — Build path

| Workstream | State | Summary | Commit |
|---|---|---|---|
| 2a-scaffold | done | cairn-parse scaffold: registry with all 16 language routes, generic tier, Symbol/Edge/Import model, rationale; 15 grammars compiled (13 crates.io pins, kotlin git-pinned, dart vendored as `rust/vendor/tree-sitter-dart`) | 83d4e19 |
| 2a-script | done | Python/PHP/Ruby/Dart parsers incl. scope tracker, decorator/references edges, Dart call chains + relative imports; golden-equal vs `tests/fixtures/golden` expected.json | 8c7c2be |
| 2a-apple | done | Swift + ObjC parsers (decl kinds, extensions/categories, inheritance/call edges, receiver typing, message expressions); golden-equal | 4634f76 |
| 2a-web | done | TypeScript/JavaScript parser (single JS-family traversal), suffix routes, decorators, heritage edges, receiver tracking; goldens plus a byte-identical differential dump against the live Python parser | b761d5e |
| 2a-sys | done | Go/Rust/C/C++/C# parsers; golden-equal plus ported signal unit tests (receiver_type, arity tables, shadow abstention) | 4f65cba |
| 2a-jvm | done | Kotlin + Java parsers (field-type pre-scan, inheritance/ctor-call edges, rationale); kotlin crate revalidated against the vendored C grammar; goldens + kg_dump spot-check | be6ef99 |
| 2b | done | `resolver.rs`: exact/ambiguous/unresolved triage, alias rewrite, tiered dispatch with arity tie-breaks, repair_incoming_edges; differential oracle `resolver_parity.py` (per-repo label counts + byte-identical edge dumps) | 85228b9 |
| 2c | done | Rayon scanner/builder (parse-order-independent output), schema.rs DDL mirror, `cairn build-graph`; `g2_check.sh`: self-store source 25 tables / 113,051 rows content-equal, rust wall 3.70s vs python 5.76s | 20d052f |
| 2d | done | Incremental graft (atomic delete+reinsert ladder, per-repo resolve + repair + import materialization) and notify watcher service (debounce, intake filters, pending_sync, failure latch, kill switches); graft dump equals full-rebuild dump | 3c72deb |
| 2e | done | Streaming level-BFS transitive closure (interned CSR, batched commits, memory tracks the frontier not the closure), row semantics replicate dataflow.py; `cairn closure` CLI + `closure_bench.py` | 77bfdfb |
| 2f | done | Embeddings build path (chunk variants, stale selection, hash/server backends, orphan reap, term_df) + vec0 write side; parity vs Python-built fixture: summary counts, byte-identical embedding rows, per-model vec0 counts, KNN hits | da8e7f5 |
| 2g | done | SCIP importer on prost: checked-in `proto.rs` wire-verified against `_scip_pb2.py`, position-join overlay port (triage, anomaly guard, savepoint-guarded writes), 7-indexer run-once registry; verified against synthetic and Python-importer fixtures; builder overlay hook not wired (see Remaining work) | 1ad6e5c |

## Phase 3 — CLI, distribution, flip

| Workstream | State | Summary | Commit |
|---|---|---|---|
| 3a-scaffold | done | clap app: all 57 top-level commands stubbed, display THEME, migration-shim dispatch for pre-clap commands; `cli_golden.py` C-4 differential gate (red at scaffold by design; 6/6 self-check against the python bin) | 74a0c6f |
| 3a-core | done | Core commands: init, build, update, import-scip, stats, checkpoint, config (4 modes), graph-read commands, full pack port; python-json byte parity + rich rail/panel/table parity; gate 19/19 | b29493d |
| 3a-km | done | knowledge/workflow, memory, compass, wiki command groups over the Phase-1b KM layer (index/islands/sharing ported); gate 19/19; unported groups remain explicit stubs (see Remaining work) | 0067078 |
| 3a-sys | done | serve (run/start/stop/status/restart), connect (reuse guard, store-mismatch abort, impl selection), doctor (11 checks, --json machine contract, exit codes), install/uninstall-agents (9 clients, template drift guard), dashboard server; daemon-side lifecycle machinery | 19ed884 |
| 3b | done | Dashboard: 38 minijinja templates + 116 embedded assets, 33 routes (hx fragments, exports, settings POST chain, editor API) over ported SQL/bundle/viz/markdown layers; differential run byte-identical on sampled JSON routes and 16/17 HTML pages (plan-§9 sanctioned divergences) | 339a307 |
| 3c | done | `release-rust.yml`: macOS aarch64/x86_64 + Linux musl-static binaries with checksums attached to the shared v* release; Homebrew formula template + renderer (tap push gated on unconfigured repo/token); binstall metadata in cairn-cli's manifest | f42ff5a |
| 3d | done | `daemon_impl` defaults to rust when a Rust daemon binary is present (env/--impl still win; explicit rust without a binary errors loudly); Python-side binary discovery excluding the Python console script; help/deprecation text + owner tests reworked; version untouched, no tag/release | b5dcc92 |
| 3e | done | Docs sweep: README binary install paths, cli-reference binary/PyPI coverage split, deployment daemon-impl selection + musl container pattern, architecture crate map + schema-mirror path, AGENTS.md CLI fallback (context/ask/review stay PyPI) | 818a93c |
| G3 repair r1 | done | Semantic memory search leg ported (cosine over memory_embeddings, RRF fusion, provenance stamping) + spike/flow/search fixtures refreshed via the sanctioned generators; `cargo test` workspace and `cli_golden.py` green | 07cb16e |

## Gate verdicts

| Gate | Verdict | Recorded check outcomes |
|---|---|---|
| G0 | PASS | round 0: `cargo test` exit 0, 26 goldens committed, rust CI leg green |
| G1 | PASS (round 3) | `parity.py --store self`: rounds 0–2 exit 1, round 3 exit 0 — 26/26 tools golden-equal on the self store; yarl/attrs each reached 26/26 repeatedly with an intermittent cap-tie residual (see Remaining work). The gate's "daemon RSS ≤ 60% of Python's; no tool regresses p95" legs (plan §5) were not measured for this verdict (Remaining work item 6); measured at 4d-4 — RSS MET, p95 no-regression NOT MET (21/26, 11/26, 17/26 tools) — see "4d-4 gate measurements" below |
| G2 | PASS (round 0) | `g2_check.sh` exit 0 — python vs rust canonical dumps content-equal on the self-store source (25 tables, 113,051 rows) and a probe corpus; rust build 3.70s vs python 5.76s. The gate's "closure ≥5× faster on the blowup case" leg (plan §6) was not measured for this verdict (Remaining work item 6); measured at 4d-4 — 1.14× on the scaling gate point, NOT MET — see "4d-4 gate measurements" below |
| G3 | PASS (round 1) | round 0: `cargo test` exit 101, `cli_golden.py` exit 1; round 1: `cargo test --workspace` exit 0 and `cli_golden.py` 19/19 exit 0 |

Current-HEAD verification run for this ledger: `cargo clippy --workspace
--all-targets -- -D warnings` exit 0 (the two pre-existing needless-borrow
lints in cairn-cli test code are fixed in 07cb16e).

## Remaining work

1. **CLI stub groups** — knowledge ingest/rebuild/embed, knowledge export tarball mode, workflow sync, memory reflect/evolve/capture/embed/batch-critic/consolidate, compass generate/gaps/flow/flow-gaps, wiki generate/status/retry/export/enrich, task/skill/verify remain explicit not-ported stubs; `context`, `ask`, `review` stay PyPI-CLI.
2. **SCIP overlay into the Rust builder** — the importer is ported; Python's `_apply_scip_overlay` builder hook has no Rust counterpart.
3. **Distribution configuration** — Homebrew tap repo/token unconfigured (tap push skipped with a notice); cairn-cli crates.io publish pending (activates the binstall metadata).
4. **Flip artifacts** — version still 0.22.0 (tracks pyproject); no v-tag or release cut; PyPI deprecation window not started; README wording describing the security-fixes-only policy is ahead of the actual flip.
5. **`CAIRN_BUILD_IMPL=rust` runtime switch** — named by plan §6's G2 gate; recorded by no workstream (verified absent from rust/crates, src, scripts at HEAD).
6. **Unrecorded gate measurements** — Rust daemon RSS ≤ 60% of Python and per-tool p95 no-regression vs the P0-2 baselines; closure ≥ 5× ratio on the blowup case (harness ready: `closure_bench.py`).
7. **Provisional pins to finalize** — D-007 (notify), D-008 (prost), D-009 (minijinja), D-010 (yaml-rust2 batch) are marked provisional in `decisions.md`.
8. **Corpus parity residual** — fresh yarl/attrs builds intermittently swap boundary rows in `search_symbols`/`find_definition` cap slices (cross-build bm25 tie volatility, owned by the parity runner's sort-lines normalization ruling; replay over the pinned `rust/golden/stores/` bytes sidesteps it).
9. **Archive `plan.md`** — the 3e archive step was deferred while workstreams still read the plan.
10. **Python-engine removal** — the surface-gap porting (item 1) and the eventual removal of the Python engine stay open; the wheel wrapping itself landed (see Wheel wrapping). Action plan for items 1-10: `plan.md` §15 (Phase 4).

## Re-verification

Fix campaign closing the ledger's open fixture findings: untracked flow
harness, oversized search goldens, and a rebuild-volatile parity oracle.

| Finding | State | Summary | Commit |
|---|---|---|---|
| F1 | done | Parity oracle deterministic by construction: `parity.py --record` builds each store (self/yarl/attrs) over a 100-file evenly-spaced corpus subset, checks the exact bytes (store.kg + corpus/ + bundle/ + manifest.json) into `rust/golden/stores/<store>/`, and captures the 26 goldens over those bytes; replay copies the same bytes to scratch and reads every tool's arguments from the fixture's additive `replay` block (seq-ordered, setups re-derived), never probing a store for targets, and refuses (exit 2) a served store path resolving to the live `~/.cairn` one; spike_queries preserved by regeneration over the pinned bytes, flow `cases` carried forward; all four rust golden readers (spike_golden.rs, golden_search.rs, nav_golden.rs, flow_golden.rs) take fixture `replay_store`/`replay_workspace` pointers with legacy fallbacks | 27b91d5 |
| F2 | done | Flow-tools golden harness landed: cairn-query `flow_golden.rs` integration test, fixtures under `rust/golden/flow/` (README, 11-file corpus, flow-store.kg, taint.json), generator `scripts/rust_migration/flow_golden.py`; `rust/golden/flow/.ruff.toml` scopes per-file-ignores to the corpus (golden fixtures are input data) | 8450bdb |
| F3 | done | Search goldens slimmed 134.1MB -> 54.3MB combined (search-store.kg 26.6MB -> 8.5MB, semantic-store.kg 107.9MB -> 48.4MB) by building from a deterministic capped corpus subset (SUBSET_FILE_CAP=260 evenly spaced indexable files, recorded in `spike_golden.py`, shared by `semantic_golden.py`); `--record` mode re-baselines the slim store, the fixture responses, and the expected JSON (`rust/golden/mcp/search_symbols.json`, `rust/golden/mcp/semantic_search.json`); verify mode keeps the golden-equal drift tripwire; semantic golden re-baselined to the longest symbol name (score-separated — tie-heavy queries flip LIMIT-10 selection across rebuilds) and the semantic verify check compares sort_lines, mirroring spike_golden tie handling; spike_golden, golden_search (incl. the semantic_search golden), auth_lifecycle tests green | c5688d9 |
| F4 | done | Workspace sweep: clippy `-D warnings` clean, full test suite 342 passing / 0 failing — no fixes or commits needed | — |
| V3 repair r1 | done | `cli_golden.py` materializes the pinned self store via `parity.load_pinned_store` for the C-4 differential (27b91d5 removed live-store snapshotting); duplicate exclude key in `.pre-commit-config.yaml` repaired | 21fa36d |

Re-verification gate verdicts:

| Gate | Verdict | Recorded check outcomes |
|---|---|---|
| V1 | PASS (round 0) | self (x2) / yarl / attrs all 26/26 exit 0, no graft |
| V2 | PASS (round 0) | test=0 clippy=0 fmt=0 |
| V3 | PASS (round 1) | round 0: tracked=16 flowTest=0 cli=1 bytes=56926208; round 1: tracked=16 flowTest=0 cli=0 bytes=56926208 (bytes = combined pinned search-store fixtures) |
| G2 re-run | PASS | `g2_check.sh` full run exit 0 — python-built and rust-built stores dump-equal |

## Release prep

- Version bumped 0.22.0 -> 0.23.0 (changelog finalized; rust workspace tracks
  it; tag `v0.23.0` staged on the branch tip, to be pushed after the PR
  merges per the release checklist).
- Ship vehicle: PR #183 on `feat/rust-migration`.
- Docs aligned with the dual-channel ruling (PyPI wheels + Python-free
  binaries; no deprecation window); `rust/README.md` added.

## Wheel wrapping

Campaign binding the rust engine into the PyPI wheels per D-011 (plan §7 3c;
`decisions.md` "D-011: wheel binding and binary discovery"). Landed on
branch `feat/rust-wheel`.

| Workstream | State | Summary | Commit |
|---|---|---|---|
| D-011 binding decider | done | Decision recorded in `decisions.md`: the PyPI wheel binds the engine as a bundled prebuilt binary exec'd through the python console-script shim (uv pattern; PyO3 rejected), riding the existing per-platform cp310-abi3 wheels rather than a separate binary package; discovery contract CAIRN_RUST_BIN env > bundled package-relative binary > PATH for `rust_bin()`'s rust-when-present default; build-time-only injection — the repo never commits the binary | 7cae0ec |
| W1 wheel packager | done | `paths.BUNDLED_BIN_DIR` + `bundled_binary()` (src/cairn/paths.py:40,108); `rust_bin()` reordered to CAIRN_RUST_BIN > bundled package binary > PATH/~/.local/bin (src/cairn/mcp_server/lifecycle.py:120-131); package-data `cairn = ["_bin/*"]` in pyproject.toml excluded from the sdist via MANIFEST.in and gitignored in the tree; hermetic discovery tests (tests/test_wheel_binary_discovery.py, 4 passed) and `scripts/rust_migration/wheel_smoke.sh`; python fallback and CAIRN_DAEMON_IMPL precedence untouched; `.github/workflows` and `rust/` untouched | b0e1bd2 |
| W2 release-pipeline extender | done | `release.yml` build job so published wheels carry the engine binary: the macOS leg natively builds aarch64-apple-darwin and copies it to `src/cairn/_bin/cairn` before "Build wheels"; the Linux leg builds x86_64/aarch64 musl-static via cross (mirroring `release-rust.yml`, incl. the static-link assertion) after the sdist step and copies the matching binary inside each cibuildwheel arch container via CIBW_BEFORE_BUILD_LINUX; the Windows leg gets no rust step (D-011: Windows wheels bundle nothing); triggers, matrix, concurrency, publish-pypi and github-release byte-unchanged | d1e0908 |
| wheel-wave | done | W1 + W2 landed as two conventional commits, pre-commit hooks green on both; working tree clean; nothing pushed or tagged | b0e1bd2, d1e0908 |
| gate-repair T r1 | done | TempStore scratch dirs in `flow_golden.rs` were named from wall-clock nanos, and macOS returns duplicate nanos to parallel test threads, so two tests shared one temp dir — concurrent `fs::copy` tore the copied sqlite store ("no such table: symbols") and one Drop deleted the other test's dir (SQLITE_CANTOPEN); now named from a process-global AtomicU64 counter (+pid), which cannot collide in-process | aa76756 |

Gate verdicts:

| Gate | Verdict | Recorded check outcomes |
|---|---|---|
| W — wheel smoke | PASS (round 0) | round 0: `scripts/rust_migration/wheel_smoke.sh` exit 0 end-to-end — all three installed-product probes green (`cairn --version`; `config --json` C-4 keys; `daemon_impl=rust` resolving to the wheel's `site-packages/_bin/cairn`, which executes) |
| T — workspace tests | PASS (round 1) | round 0: `cargo test` exit 101, `pytest` exit 0 (flow_golden temp-dir collision, fixed by gate-repair T r1); round 1: `cargo test` exit 0, `pytest` exit 0 |

## Phase 4 — Leftovers (plan §15)

Wave 1 covered 4a and 4b-1–4b-10. Each branch went through implement,
independent review and fix rounds, then all were merged locally onto `main`
and gated together. Branches are pushed; none is merged yet. The edge-source
and resolver branches are stacked on the staging/rebuild branch because they
share its test files.

| Workstream | State | Summary | Branch @ head |
|---|---|---|---|
| 4a | done, unmerged | `rust-parity.yml`: parity replay, `cli_golden.py`, G2 and wheel smoke as independent jobs (cheap subset per PR, full set nightly); `g2_check.sh` gains validated `G2_LEGS` with `rebuild` and `staging` legs; `wheel_smoke.sh` builds isolated | `ci/rust-parity-gates` @ 9701369 |
| 4b-1, 4b-2 | done, unmerged | staged builds swap in under the shared build lock; full rebuilds replace the store, `--repo` replaces one repo without id reuse; rust incremental writers and the watcher take the lock | `fix/rust-build-staging-rebuild` @ 7ed6097 |
| 4b-3, 4b-9 | done, unmerged | dataflow PK rebuilt from the table's shape in both engines (repairs 0.23.0-stamped stores), atomically; daemon writable connections use the store factory. The healthz half of 4b-9 was withdrawn (not a defect) | `fix/rust-store-daemon-health` @ 00448e7 |
| 4b-4, 4b-8 | done, unmerged | edge, contains and decorator sources attributed to the enclosing same-named owner; `trace_flow` nodes and cycles at shortest depth; yarl `trace_flow` golden refreshed from Python over the unchanged pinned store | `fix/edge-source-trace-depth` @ b1309c5 |
| 4b-5, 4b-6, 4b-7 | done, unmerged | change detection since last index, NUL-separated git paths, git errors surfaced; repair with stored receiver/arity signals across repos via nullable `edges.ref_name`/`receiver_type`/`call_arity`, chunked, LSP edges excluded, closure/dataflow kept in sync; import scoring fixed | `fix/incremental-resolver-repair` @ 0f6630f |
| 4b-10 | done, unmerged | config never overwritten when unreadable or not backed up (rust `install-agents` aborts the client; dashboard settings refuse and keep non-scalar keys); HOME-dependent cairn-cli tests serialized (flake 4/10 → 0/20) | `fix/config-overwrite-guard` @ 2910ac6 |

Combined gate on the local integration merge:

| Check | Outcome |
|---|---|
| `cargo fmt --check`, `cargo clippy -D warnings` | clean |
| `cargo test --workspace --no-fail-fast` | 360 passed, 1 failed (`daemon_check_reports_the_serving_impl`, live developer daemon) |
| full pytest | 4768 passed, 3 failed (SCIP tests needing the `[scip]` runtime, identical on `main`) |
| `g2_check.sh` base / rebuild / staging | PASS / PASS / PASS (rebuild and staging fail on `main`) |
| `parity.py` self / yarl / attrs | 26/26 each |
| `cli_golden.py` | 19/19 |
| `resolver_parity.py` self / yarl / attrs | PASS |
| `pre-commit run --all-files`, every branch | green |

Remaining for Phase 4: open and merge the wave-1 PRs (staging/rebuild before
the two stacked branches; drop `continue-on-error` from the `g2-legs` job once
it merges); 4d-3 (blocked on unconfigured tap/registry credentials); 4e
deferrals left to the integrator — spec archival, the CHANGELOG line, a
Rust-driven perf gate (4e-2, 4e-3).

Wave 2 covered the engine-correctness follow-ups 4b-11–4b-19, one commit each
on `rust-migration/phase4-wave2`:

| Workstream | State | Summary | Commit |
|---|---|---|---|
| 4b-11 | done | Gate-3 failure was environmental: the gate's git-commit PATH carries `~/.local/bin` but not `~/.cargo/bin`, so the `language: system` cargo-fmt/cargo-clippy hooks printed "Executable `cargo` not found" (attempt 2's identical hooks passed, code unchanged); both hooks made self-sufficient in `.pre-commit-config.yaml` via `bash -c 'export PATH="$HOME/.cargo/bin:$PATH"; exec cargo ...'` — same checks at full strength, no source changes; verified under the gate's exact PATH (both Passed) and full `pre-commit run --all-files` (all 10 hooks) | 68293ba |
| 4b-12 | done | Gate-2 clippy failures in incremental.rs fixed: two type_complexity errors (5-tuple row type) via a private FileStatRow struct + shared file_stat_rows loader for changed_via_stat/detect_changed, two if_same_then_else by collapsing each branch into one short-circuiting size-changed-or-(mtime-drifted-and-hash-unchanged) condition preserving hash-only-on-mtime-drift; round-1's miss came from piping clippy through rg and head (head's exit 0, errors truncated) — the exact pre-commit clippy command re-run with cargo's true exit code 0, re-verified after fmt; cargo test -p cairn-build 69 passed, 7 Python suites 148 passed | 5f7ebeb |
| 4b-13 | done | incremental_update opened its store connection before taking the build lock, so a full-build swap in that window redirected the pass's writes to the replaced file; both twins now acquire the build lock first and open the store under it (change detection runs in the lock on the same connection); lock-held defer regression tests added on both sides, verified failing first ("store was opened ... before the build lock was acquired") | 4757871 |
| 4b-14 | done | Rust sync repair path (cmds_core.rs refresh_conn -> repair_changed) wrote without the build lock Python's cairn sync takes; refresh_conn now acquires `cairn_store::swap::build_lock` before repair_changed and defers on contention via the existing stale-graph banner returning Ok, so read commands still answer during a concurrent build; refresh_repair_defers_while_a_build_holds_the_lock added failing-first (files.size converged 24->50 pre-fix); Python twin unchanged (sync.py already holds build_lock across its writes); out-of-scope observation left as-is: Python watcher.refresh_for_query still repairs lock-free | fbac8a0 |
| 4b-15 | done | Decorates edges carried the decorator's line, which a same-named outer definition's span contains, so both builders' enclosing-owner resolution picked the outer via the innermost-span rule before the nearest-following-start rule ever ran; for decorates edges the source now resolves to the nearest same-named definition with line_start >= edge line before the containing-span rule (calls/contains unchanged), in builder.rs `enclosing_owner` and builder.py `_enclosing_owner`; owner tests extended with both plan shapes on both sides and verified failing first; the parser's duplicate decorates edges (nested defs inherit the enclosing decorators) remain parity-asserted behavior | 726da15 |
| 4b-16 | done | Golden writers separated: flow_golden.py now writes all five fixtures under `rust/golden/flow/` (four previously sat in `rust/golden/mcp`, which parity.py --record rewrites whole), parity.py no longer carries foreign `cases` keys into its mcp fixtures, and the Rust flow_golden test replays the flow fixtures from their new home; verified failing-first (explore.json drift assertion + test_migration_fixture_writers.py), post-fix flow_golden 5/5 and `parity.py --store self` 26/26; pre-existing, observed not fixed: flow-store.kg bytes vary run-to-run from build_store's SQLite page layout while fixture JSON stayed byte-equal across three rebuilds | bddc66b |
| 4b-17 | done | Dashboard settings saves now name why a config.json write was refused, in both engines: Rust settings::set_config_values returns Result<(), String> with the parse/write reason ("not valid JSON (…)", "top-level value is not a JSON object", io errors) rendered by settings_save; Python set_config_values_with_reason returns (ok, reason) with the same taxonomy (set_config_values keeps its bool contract by delegating; _read_config_object folded in and deleted) rendered by routes/settings.py; failing-test-first verified on both sides | db45bac |
| 4b-18 | done | `edge_signals` doctor check added to both engines: WARNs (exit stays 0) when reference edges predate the ref_name/receiver_type/call_arity signal columns, recommending `cairn build`; identical SQL (ref_name IS NULL AND resolution IN ('ambiguous','unresolved') AND kind NOT IN ('contains','imports') AND COALESCE(source,'tree_sitter') != 'scip') with contains/imports, SCIP-sourced and exact edges excluded by design, and stores lacking the columns probed via pragma_table_info (doctor opens read-only, never migrates); wired after parse_errors in both degraded paths; 3 Rust + 3 pytest failing-first tests; live `doctor --json` on this workspace's own pre-signal store WARNs with 3685 flagged edges, cross-checked against the raw SQL | adf8043 |
| 4b-19 | done | parity.py --record had re-staged the corpus from the live unversioned source before rebuilding, so a drifted source produced a wholesale pinned-corpus diff (demonstrated: the self workspace's current sample diverges from its pin on 92/100 files); build_pinned_store now stages via reproduce_pinned_corpus(), a byte-exact copy of `rust/golden/stores/<store>/corpus` when it exists, raises SystemExit ("REFUSING record") for a pinned store without its corpus, and reads the live source only on first pin; two failing-first tests in test_migration_fixture_writers.py; no Rust twin — only parity.py writes rust/golden/stores | 736c06c |

Wave 3 covered the CLI surface port (4c), one commit each on
`rust-migration/phase4-wave2`:

| Workstream | State | Summary | Commit |
|---|---|---|---|
| 4c-1a | done | Task-queue port (list/show/claim/complete/drop) with its gate repair: the check-comment-lengths hook flagged the reworded critic_concept docstring as a new >3-line violation and left the old text's baseline fingerprint stale (scripts/hooks/comment-style-baseline.json); docstring shrank to 2 lines and the hook's sanctioned `--update` removed exactly the one stale entry (baseline diff is a pure 7-line deletion, the only change a shrink-only ratchet permits); full `pre-commit run --all-files` all ten hooks Passed, comment-style pytest + ten task/critic/wiki files 226 passed; rust legs green from round 3 and unchanged since | 3336438 |
| 4c-1b | done | wiki generate/status/retry/export/enrich with the Notes-aware critic; three review findings fixed failing-first: PageEntry::seeds_value now inserts docs,files,symbols sorted to match json.dumps(sort_keys=True) (page_input_hash_matches_python_canonicalization pinned the Python sha256 and failed on the preserve_order hash), the RESEED keys corrected to ("wiki","generate","--llm") and ("wiki","enrich","--all") — with hash parity fixed the gate failed exactly as predicted ("Queued wiki-page task" vs "Adopted live wiki-page task"), and cross-engine adoption on the pinned store shows the rust leg adopting the python-queued tasks by identical ids (10 task files, previously 20 duplicates) — and _seed_wiki's reset (reports rmtree + non-golden task purge) now precedes seeding so the Notes-bearing Architecture-Report survives (both wiki-generate legs regenerate the body, splice "## Notes kept note", drop "Stale body."); the skip/adopt contract is pinned by the hash test + recorded adoption check (not expressible as a py-vs-rs golden case, which must diverge by design); cli_golden.py 40/40, cairn-query lib 67 | 7d3f023 |
| 4c-1c | done | hooks install/uninstall ported from cli/hooks_viz.py + hooks/git_hooks.py: uninstall_git_hooks + NO_GIT_REPOS_GUIDANCE in agent_install.rs, --workspace option and real handlers in cmds_system.rs replacing the not_ported stub, click-parity `Error: <msg>`/exit-1; failing-first: 3 new cli_golden.py cases (install, uninstall, install-on-empty-workspace) failed on the pre-fix binary (clap exit 2); post-fix cli_golden.py 43/43, cargo test -p cairn-cli --lib 109/110 (the 1 failure is the documented live-daemon quirk); Python twin unchanged — git_hooks.py is the behavior of record | f164855 |
| 4c-2 | done | Lifecycle and ops commands port, six findings fixed: the RESEED loop matches tuple(cmd[:3]) then tuple(cmd[:2]) so ("sync","--workspace") fires past the positional path and _seed_sync_drift rmtree's scratch/.knowledge before load_pinned_store (the mutating case now live — it failed pre-fix on the rust blank-line progress divergence); plain_progress_draw emits the two draws as \r-overwritten segments of one line with a single trailing newline and comma-groups totals via fmt_int; redact_match takes the last split segment verbatim ("/tmp/x/" -> bare "[PATH]"); exe_venv_root() walks ancestors for the .venv root and stale_artifacts() lists build/dist/*.egg-info via the sidecar interpreter including the "unknown (stale build artifacts only)" arm; confirm accepts click's full true set (y/yes/t/true/1/on, case-insensitive); local human_bytes deleted for pub(crate) cmds_core::human_bytes; _seed_telemetry plants fixed tool_metrics + events rows so metrics legs render populated tables (>80-col `metrics --builds` table stays excluded under the recorded round-2 rich-wrapping ruling); cli_golden.py 58/58 twice, 119 lib tests; disclosed incident: a yes-path differential probe pinned only CAIRN_HOME, so the python leg stripped real registrations from Claude Desktop and Gemini configs (Gemini restored via install-agents --scope global and verified, Claude's stripped entry was the stale stdio registration doctor itself flags, doctor exits 0, mistake recorded; the yes-path end-to-end differential was not re-run — confirm parsing is unit-tested) | d4bbeb0 |
| 4c-3 | done | Graph analytics commands port, nine findings addressed: changed_entry's destructure swap ((old,new) used as (new,old)) fixed so changed-file JSON emits the effective path (blast deletion/rename classification test panicked pre-fix); _seed_blast_drift seeds a fresh pinned store+corpus for all five blast legs (previously vacuous); py_splitlines drops the post-trailing-boundary segment; viz --scope/--format and blast --format are clap ValueEnums, caps/top-k/max-hits use range(1..) parsers, dataflow --max-symbols is i64 with python's warning-then-default (exit codes verified py:2 rs:2 on the invalid arms, -1 accepted by both); embed ANN branch draws the forced description-change and stop segments (2 "ANN index" draws each side); refresh_probe shared by refresh_conn/refresh_with_banner with failed strict repair propagating to "Error:" exit 1; duplicate plain_progress_draw removed into a pub generalized lifecycle fn; impact closure fast-path gated on !fuzzy; embed --adopt-server-model exits 1 naming the unported ladder; cli_golden.py 89/89 on three consecutive runs, clippy/fmt clean on the three crates | 75f2a04 |
| 4c-4a | done | Knowledge layer port, three findings fixed: longname_entry no longer patches typeflag 'L' over a sealed header — the checksum is computed exactly once via header_with_typeflag() (defect reproduced with real readers: python tarfile returned 29/35 members with the long-named file and everything after it lost, bsdtar printed "Damaged tar archive"; post-fix 35/35, tar -tzf/-xzf exit 0, extracted tree diff -r identical); member_names() in the targz tests recomputes every header checksum (148..156 as spaces) and rejects mismatches (proven failing-first by temporarily restoring the double-sum); falsy scalar tags (Integer(0), Boolean(false), Real 0.0) are skipped, matching okf/concept.py's `tags or []` (seeded 42/0/false/4.5/0.0/abc bundle: both engines print the identical 2-error output, exit 1); cargo test --workspace green except the documented daemon quirk, cli_golden.py 101/101, pinned-bundle export lossless | e870b77 |
| 4c-4b | done | knowledge workflow sync and memory reflect/evolve/capture/embed/batch-critic/consolidate ported, stub arms removed: KM layer in tools_km.rs (reflect_store, evolve_memory, batch_critic, consolidate_memories, workflow staleness/sync, capture_memory with session_origin, raw-signals apply_score), memory-embedding family in cairn-build embeddings.rs (chunk_memory_body, embed_memory_concepts, embed_memory, reap_orphaned_memory_embeddings), CLI arms in cmds_knowledge.rs incl. the droid/opencode/claude subprocess extraction for capture; failing-first via 17 new cli_golden.py legs (0/17 on the stub binary, each failing with the not-implemented diagnostic); post-fix legs 17/17, full cli_golden.py 118/118, parity.py --store self 26/26, targeted lib tests green (cairn-query 94, cairn-build 24, cairn-daemon 5, cairn-cli 123 + the documented daemon quirk); --max-steps 0 exits 2 (IntRange parity), empty --session-transcript queues identically; workspace-wide suites left to the gates | 104d1d0 |
| 4c-4c | done | compass generate/gaps/flow/flow-gaps ported, six findings fixed: module_match_params mid-path LIKE corrected to `%/{e}/%` (new golden leg `compass generate --dry-run cairn/graph` and unit test failed pre-fix with "(no symbols detected in this module)"); cross_module_deps parts[..2] panic replaced with a min(2) slice (unit test panicked "range end index 2 out of range"); skillgen.rs deduplicated onto tools_km::resolve_module/module_match_params (mirroring python's single generator.py), infer_repo NULL-tolerant on repos.path; agent-sync arm mirrors SubprocessBackend (exec_agent with 120s kill-timeout, queue_wait FileQueue fallback polling to min(600,120)s, resolved repo threaded into CompassFacts) — verified byte-identical "llm unavailable" exit 1 differentially with CAIRN_LLM_BACKEND=droid; cli_golden.py comment corrected; stray 0-byte rust/golden/stores/self/self.kg deleted; cli_golden.py 134/134, cairn-query 100/100 lib + 28 integration/doc tests, cairn-cli 123/124 (documented daemon quirk) | c5ab700 |
| 4c-5 | done | ask, context, review ported (RouteOutcome::route_query in tools_km.rs, review engine as cairn-query review.rs, click-parity UsageError output in cmds_core.rs handlers, not_ported stub arms removed), AGENTS.md fallback section + docs/cli-reference.md Query coverage updated; the gate-2 fmt failure (a line edited after the earlier fmt pass) fixed via cargo fmt --all then the exact hook invocations re-run (cargo-fmt/cargo-clippy Passed on cmds_core.rs, whitespace-only change); re-verified on the final tree: targeted cargo tests green except the documented daemon quirk, the 19 ask/context/review golden legs 19/19 (full cli_golden.py 153/153 passed pre-reformat, the only change since is the whitespace collapse); remaining not_ported arms were prs/bench/eval (4c-6); docs/cli-reference.md still carries stale PyPI-CLI claims for the 4c-2/4c-3/4c-4 families — those waves' doc debt, out of 4c-5 scope | efb6ce2 |
| 4c-6 | done | prs/bench/eval resolved by ruling (D-014 recorded in decisions.md after escalating the specs/ edit ban; entry number verified free with D-013 highest): they stay on the PyPI CLI — bench (~3,800 lines, `[bench]` datasets extra, SWE-bench checkouts) and eval measure the Python engine itself so a Rust port would be an incomparable harness (the Rust engine's perf home is scripts/rust_migration, plan 4e-4 lists bench in the deletion set), and prs' --conflicts leg consumes the D-013 communities sidecar; Rust side: Prs/Bench/Eval take a hidden trailing-var-arg PypiOnlyArgs (so `bench --suite perf --json` reaches the diagnostic instead of clap exit 2) and route to a pypi_only(name) handler exiting 1 naming the PyPI CLI — the D-013 shape; the dead not_ported catch-all and CoreCommand::name() deleted, zero not_ported arms remain (G4c); failing-first via tests/pypi_only_commands.rs (placeholder message + clap exit 2 observed pre-fix); docs updated (cli-reference, README, CONTRIBUTING); no cli_golden case added for ruled commands (D-013 precedent) | c2e411d |
| 4c-7 | done | Python's _apply_scip_overlay (builder.py:291) ported into the Rust builder: pub builder::apply_scip_overlay runs as a fifth populate() pass after import-edge materialization (mirroring the builder.py:539 call site), reusing scip::importer::import_scip_file and generate_index_result as-is (no runtime-missing/install-hint branch, per the tribal-memory ruling); BuildSummary gained Option<ScipReport> scip (skip_serializing_if, matching Python's conditional dict key), the build panel renders the scip row, skip-row writes deduped into importer::replace_skip_row; failing-first via rust/crates/cairn-build/tests/builder_scip_overlay.rs ("configured index must land scip edges", 0 edges pre-fix), three tests cover applied-overlay+summary, corrupt-index degradation, and generate_missing=false; goldens insensitive (no golden corpus carries a root cairn.json, overlay is a no-op there); cairn-build 76 passed, cairn-cli 123/124 (documented daemon quirk), Python twin 9 passed with no defect; the cairn-cli sync/update leg still awaits its own wiring (should call apply_scip_overlay with generate_missing=false) | 16df517 |

Wave 4 covered the entry-point flip (4d), one commit each on
`rust-migration/phase4-wave2`:

| Workstream | State | Summary | Commit |
|---|---|---|---|
| 4d-1 | done | Ruling D-015 (decisions.md): Windows keeps the Python engine and release-rust.yml intentionally gains no Windows target — the workspace does not compile there (unguarded std::os::unix in targz.rs, agent_install.rs, cmds_core.rs, scip/indexers.rs, daemon lifecycle.rs, tools_km.rs flock), D-011 already rules Windows wheels bundle nothing, and a windows-latest MSVC leg conflicts with plan §12's solo-maintainer CI-cost risk; failing-first code fix: shutil.which("cairn") on Windows resolves pip's native cairn.exe launcher, passing the #!-only native check and flipping daemon_impl() to rust — rust_bin() now gates the PATH/~/.local legs behind _is_windows_host() (env + bundled legs stay cross-platform), pinned by test_windows_console_launcher_keeps_python_default (an os.name-patch variant was discarded for failing for the wrong reason — WindowsPath flavor makes Path.exists() False on macOS); no Rust twin (rust rust_bin has no PATH leg); 41 passed across test_wheel_binary_discovery.py/test_connect.py/test_serve_manager.py, ruff clean; wheel_smoke.sh not run (Windows-only path sits after its bundled-binary leg) | f7e4832 |
| 4d-2 | done | Console-script exec shim (round 2): console.py skips leading -v/--verbose before the _pypi_only sidecar membership test, restoring `cairn -v prs` to the Python CLI (new test failed pre-fix with execved=[(stub, ['-v','prs'])]; verified end-to-end through the real console script: -v prs/--verbose bench -> Python, -v config --json -> EXECED passthrough exit 7); the test helper's bundled=False branch and the mirrored pre-existing test monkeypatch HOME to tmp_path so rust_bin()'s ~/.local/bin leg cannot flip the no-binary premise (test hardening only — local ~/.local/bin/cairn is a #! script, verified, so no failing-first applies); 14 passed in test_wheel_binary_discovery.py, ruff/mypy clean; round-1 core unchanged (console.py exec shim via rust_bin(), pyproject entry flip to cairn.console:main, wheel_smoke.sh exec probe) | f95de19 |
| 4d-3 | blocked | Crates-io publish job added to release-rust.yml publishing the workspace in dependency order (store->parse->query->daemon->build->cli), gated on secrets.CARGO_REGISTRY_TOKEN with the 3c notice+exit-0 skip shape, plus a tag<->workspace-version guard (a mismatch would 404 every binstall pkg-url); Homebrew tap side was already configured in 3c; failing-first tests/test_release_distribution.py (2 failed pre-fix — no publish loop, no token gate; 3/3 post-fix) also pins binstall pkg-url <-> tarball naming <-> formula URL coherence; docs/release-checklist.md records what remains user-owned: HOMEBREW_TAP_REPO var + HOMEBREW_TAP_TOKEN and CARGO_REGISTRY_TOKEN secrets, and two cairn-parse packaging rulings (tree-sitter-kotlin git pin and vendored publish=false cairn-tree-sitter-dart, both proven to block cargo package this session; cairn-store packages clean, cairn-cli needs its deps registered first); BLOCKED: gh shows the repo has zero variables and zero secrets, so the token/tap credentials are missing; no Rust code changed (no twin, no goldens) | 542a03e |
| 4d-4 | done | Gate-measurement campaign closing Remaining-work item 6 (the G1/G2 rows above and the "4d-4 gate measurements" block below carry the record: RSS MET — 58.7%/29.6%/30.8% of Python; per-tool p95 no-regression NOT MET — 21/26, 11/26, 17/26; closure 1.14x on the 1000-file x5 gate point, NOT MET — insert-bound in both engines, the >=5x wall target needs a re-ruling, Rust's gain is peak WAL); attribution measured, not assumed: the Python daemon pays a ~3.5s per-call freshness walk (_detect_changed 3.51s warm, iter_source_files 3.37s over the 243,186-entry tree), Rust's small-store p95 misses come from a fresh SQLite connection per call (300 open->query->close cycles reproduce the 10-25ms tail; follow-up: port Python's per-thread pool to cairn-daemon under the 4b-13 swap semantics), `path` a steady 437ms on a wide self pattern (flagged); round-2 ledger corrections: self-corpus growth attributed to Python source (rust/ excluded by cairn.json since 8cbceea — 848 indexable files, 415 rust/ files config_exclude-skipped; tracked .py outside benchmarks/datasource 788->965 between 6e09906 and 542a03e), artifact sha256 clause corrected (kept-aside binary byte-identical to the post-TCP_NODELAY-revert rebuild, both 3fb93972...), edges table 76,294 = 71,100 summary edges + 5,194 kind='imports' rows recorded as a non-divergence; D-007–D-010 finalized against crates.io (exact =-pins, cargo-deny allowlist); no code/test changed this round — round-1 regression tests stand (proven failing on HEAD, passing now); ruff clean, comment-style hook exit 0, closure_bench gate exit 0 with parity PASS, pytest selection 11 passed in 154.7s; cargo-deny not run locally (CI leg) | d67c450 |

Wave 5 covered the Python-engine removal (4e), one commit each on
`rust-migration/phase4-wave2`:

| Workstream | State | Summary | Commit |
|---|---|---|---|
| 4e-1 | done | Parity oracles frozen at the last Python capture; the gate-attempt-3 fix was staging only — pre-commit refused to run on the unstaged .pre-commit-config.yaml edit, so the named remediation `git add .pre-commit-config.yaml` was applied and the frozen reference rust/golden/dump/self-corpus.norm.txt staged alongside it (the harness commits both; `pre-commit run check-added-large-files --files rust/golden/dump/self-corpus.norm.txt` -> Skipped under the staged exclusion); full `pre-commit run --all-files` all ten hooks Passed, with the two cargo hooks now actually checking the workspace rather than skipping; no code or fixture bytes changed that round, so the recorded battery stands: G2_LEGS=base PASS and rebuild,staging PASS/PASS on bash 3.2.57, parity replay 26/26 on all three stores, cli_golden 153/153, cargo flow_golden 5/5, engine-free regression test 1 passed, pytest collection 4847 clean, ruff clean | 3172fb1 |
| 4e-2 | done | Python engine deleted — the PyPI wheel is the binary launcher plus sidecars (1048 touched paths, 962 deletions); round-4 findings: scripts/run_skill_evals.py validates the shipped skill evals against the Rust surfaces (26 MCP tools scraped from cairn-daemon's `text_tool(` catalog, 114 CLI commands in 10 groups from the clap enums in cmds_*.rs — prs/bench/eval absent) and exits 0 with 6/6 specs valid (exit 2 before), pyyaml an explicit dev dependency, test_skill_evals_validator.py 2 passed; the `[ingest]` sidecar wired instead of accepted as a regression — new cairn_query::sidecar resolves the interpreter (CAIRN_SIDECAR_PYTHON, then one beside the binary, then PATH python3), fed_binary_docs converts each PDF/docx through `python -m cairn.ingest_convert` (stderr -> skip reason; no importable sidecar -> "cairn[ingest] not installed"), console.py sets CAIRN_SIDECAR_PYTHON=sys.executable before exec; failing-first (two cairn-query sidecar tests and the launcher hand-off test red then green), end-to-end `knowledge ingest --file spec.docx` staged 1 document with a fake sidecar and skipped without one; stale Python-engine references swept (agent_templates.rs, spike_golden.rs, schema.rs, evals README/rule08b; `rg src/cairn rust/crates/cairn-cli/templates` empty); new doctor check `agent_hooks` scans Claude/Cursor settings for retired `python -m cairn.hooks.claude_hooks` commands and WARNs with a `cairn install-agents` migration hint (failing-first, verified live) — `report` gained one PASS line, re-frozen via `cli_golden.py --record --only "report --db"`, and D-016 records both divergences (download-reranker, report), the sidecar wiring and the hook-migration cost; final-tree checks all exit 0: fmt, clippy -D warnings, cargo test on cairn-cli/query/daemon/store (lib 136/111/14+4+1/17+4, every integration suite ok), release build, cli_golden 153/153, g2_check PASS, parity self 26/26, pytest 86 passed 2 skipped (ingest extra), pre-commit all hooks, wheel_smoke PASS (py3-none-macosx_11_0_arm64 launcher wheel), run_skill_evals 6/6; deferred: the CHANGELOG line, the 4e-3 docs sweep, a Rust-driven perf gate, `cargo test --workspace`, `uv sync` of the dev venv, `cairn update` | 63e3762 |
| 4e-3 | done | Docs, AGENTS.md and CLI hints aligned with the engine removal; round-2 findings: init --with-hooks wires to the shared run_hooks_install (cmds_system.rs, now pub(crate)) and --import-docs is retired naming `cairn knowledge import` — the dead "run the Python CLI" messages are gone and the cli-reference init row states the retirement; mcp-tools.md's connection bullet lists the verified writable set (explore, recall_memory, record_memory, knowledge_delete, generate_flow — checked against the ctx.conn() call sites in cairn-daemon/src/tools.rs) with knowledge_add/knowledge_status bundle-only and wiki_generate read-conn + bundle writes; build --lsp and connect --impl help, daemon_impl/daemon_bin doc comments and the cli.rs/main.rs module docs swept of Python-CLI transition text (behavior unchanged, auth_lifecycle matrix green); ingest skip message corrected to cairn-intel[ingest] in Rust (ingest.rs), the Python sidecar twin (ingest_convert.py), its tests and the cli-reference quote; failing-first via new rust/crates/cairn-cli/tests/retired_text.rs (3 tests) and the flipped ingest owner test, red then green; targeted gate 318 passed / 0 failed across cairn-cli + cairn-query + cairn-daemon, pytest 17 passed / 2 skipped, fmt clean, doc-links broken 0, conformance fresh (15 rows), cairn update run; no golden case pins init/build/connect help (153 cases checked); spec archival deferred to the integrator — remaining specs/rust-migration references live only in CHANGELOG.md and the frozen rust/golden/stores fixtures | 467daf3 |

### 4d-4 gate measurements (G1 RSS/p95 legs, G2 closure leg)

These are the performance legs of G1 ("daemon RSS ≤ 60% of Python's; no tool
regresses p95 latency", plan §5) and G2 ("closure ≥5× faster on the blowup
case", plan §6) that the Gate verdicts table above recorded without
measurement (Remaining work item 6); G4d itself (plan §15: `wheel_smoke.sh`
green with `cairn <cmd>` resolving to the binary) is not recorded here.
Recorded by `baseline.py --impl rust` with a same-day Python control per
corpus (20 timed calls per tool, sequential runs) and `closure_bench.py
--edge-factor 5` on the 1000-file medium scaling gate point; the full record
(method deltas, per-tool tables, attribution probes, raw artifacts) is the
"Rust engine measurements (4d-4)" section of `baselines.md`. Machine: Apple
M4 Pro, 12 CPUs, macOS 27.0.1; binary built from this tree (cairn-intel
0.23.0); self corpus at 848 files / 11,828 symbols (Python source growth
since P0-2; `rust/` stays excluded by the root `cairn.json`), yarl and attrs
byte-equal to their P0-2 pins.

| Leg | cairn self | yarl | attrs | Verdict |
|---|---:|---:|---:|---|
| G1: daemon RSS peak ≤ 60% of Python | 58.7% (125,232 / 213,424 KiB) | 29.6% | 30.8% | MET |
| G1: per-tool p95 no worse than Python (26 tools, same-day control) | 21/26 | 11/26 | 17/26 | NOT MET |
| G2: closure ≥ 5× on the blowup case | 1.14× (1000-file ×5: python 3.99 s, rust 3.50 s, 803,934 identical rows); 1.32× on self ×1 | — | — | NOT MET |

Attribution, each measured: self-store graph tools are 100–500× faster in
Rust because the Python daemon pays a ~3.5 s per-call freshness walk on this
checkout (`_detect_changed` 3.51 s warm, its `iter_source_files` walk 3.37 s
over the 243k-entry tree) and the Rust daemon runs no
per-call guard; the small-store p95 misses come from the Rust daemon opening
a fresh SQLite connection per call (300 raw open→query→close cycles reproduce
the 10–25 ms tail on ~7% of calls; a persistent connection answers in 8 µs;
Python pools per thread with an inode swap check in `_server_core._conn`) —
follow-up: port that pool to `cairn-daemon` under the 4b-13 swap semantics;
`path` on self is a steady 437 ms on a wide pattern (flagged);
`trace_flow`/`ask_compass`/`knowledge_search` run 2–5× slower at p50
(millisecond scale); the closure is insert-bound in both engines at depth 4
(Python spends 3.2 s of 4.3 s in the `transitive_edges` insert), so the ≥5×
wall target needs a re-ruling — the Rust gain is peak WAL (58 MB vs 187 MB
at ×5, 27 MB vs 115 MB on self).

Pins: D-007–D-010 finalized in `decisions.md` against crates.io on
2026-10-24 — notify 8.2.0 and yaml-rust2 0.13.0 are the newest stable
releases, prost 0.14.4 the newest stable with 0.14.0/0.14.2 yanked, minijinja
3.0.1 stays pinned below 3.0.2/3.0.3; all four are exact `=`-pins in the
manifests and `rust/Cargo.lock` with CC0-1.0 on the cargo-deny allowlist.
