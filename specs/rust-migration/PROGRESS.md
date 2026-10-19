# Progress: rust-migration

Campaign ledger for the migration executed by `plan.md`. State recorded at
branch `feat/rust-migration`, HEAD `07cb16e`. Landed workstreams carry their
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
| G1 | PASS (round 3) | `parity.py --store self`: rounds 0–2 exit 1, round 3 exit 0 — 26/26 tools golden-equal on the self store; yarl/attrs each reached 26/26 repeatedly with an intermittent cap-tie residual (see Remaining work) |
| G2 | PASS (round 0) | `g2_check.sh` exit 0 — python vs rust canonical dumps content-equal on the self-store source (25 tables, 113,051 rows) and a probe corpus; rust build 3.70s vs python 5.76s |
| G3 | PASS (round 1) | round 0: `cargo test` exit 101, `cli_golden.py` exit 1; round 1: `cargo test --workspace` exit 0 and `cli_golden.py` 19/19 exit 0 |

Current-HEAD verification run for this ledger: `cargo clippy --workspace
--all-targets -- -D warnings` exit 0 (the two pre-existing needless-borrow
lints in cairn-cli test code are fixed in 07cb16e).

## Remaining work

1. **Flow golden harness untracked** — `rust/crates/cairn-query/tests/flow_golden.rs`, `rust/golden/flow/`, `scripts/rust_migration/flow_golden.py` pass locally (G3 round 1: `cargo test` exit 0) but are not committed at HEAD.
2. **CLI stub groups** — knowledge ingest/rebuild/embed, knowledge export tarball mode, workflow sync, memory reflect/evolve/capture/embed/batch-critic/consolidate, compass generate/gaps/flow/flow-gaps, wiki generate/status/retry/export/enrich, task/skill/verify remain explicit not-ported stubs; `context`, `ask`, `review` stay PyPI-CLI.
3. **SCIP overlay into the Rust builder** — the importer is ported; Python's `_apply_scip_overlay` builder hook has no Rust counterpart.
4. **Distribution configuration** — Homebrew tap repo/token unconfigured (tap push skipped with a notice); cairn-cli crates.io publish pending (activates the binstall metadata).
5. **Flip artifacts** — version still 0.22.0 (tracks pyproject); no v-tag or release cut; PyPI deprecation window not started; README wording describing the security-fixes-only policy is ahead of the actual flip.
6. **`CAIRN_BUILD_IMPL=rust` runtime switch** — named by plan §6's G2 gate; recorded by no workstream (verified absent from rust/crates, src, scripts at HEAD).
7. **Unrecorded gate measurements** — Rust daemon RSS ≤ 60% of Python and per-tool p95 no-regression vs the P0-2 baselines; closure ≥ 5× ratio on the blowup case (harness ready: `closure_bench.py`).
8. **Provisional pins to finalize** — D-007 (notify), D-008 (prost), D-009 (minijinja), D-010 (yaml-rust2 batch) are marked provisional in `decisions.md`.
9. **Corpus parity residual** — fresh yarl/attrs builds intermittently swap boundary rows in `search_symbols`/`find_definition` cap slices (cross-build bm25 tie volatility, owned by the parity runner's sort-lines normalization ruling).
10. **Archive `plan.md`** — the 3e archive step was deferred while workstreams still read the plan.
