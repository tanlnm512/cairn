# Plan: rust-migration

Executes `spec.md`. Three phases plus a foundations phase; every phase ends at
a hard gate and is independently shippable. Python stays the default
implementation until the Phase 3 flip.

## 1. Goals / non-goals

Goals:
- Rust serving path (daemon), then build path, then CLI — each phase gated on
  parity evidence, not on faith.
- Kill the known super-linear paths (closure) and the per-file process-pool
  overhead.
- Dual-channel distribution: PyPI stays a supported publish channel
  (maturin/PyO3 abi3-py310 wheels wrapping the rust binary) alongside
  Python-free GitHub Release binaries + Homebrew; the Python-version matrix
  still collapses — one abi3 wheel per platform covers py3.10+.

Non-goals:
- No tool-surface changes: same 26 MCP tools (see `docs/mcp-tools.md`), same
  CLI names, same resolution-label semantics.
- No `.kg` schema redesign. The schema is the migration contract, additive-only.
- No wholesale port of the pytest suite; it is replaced by contract tests,
  a ported smoke floor, and differential runs (§8).
- No local sentence-transformers inference in Rust (§9).

## 2. Frozen contracts

| # | Contract | Arbiter |
|---|----------|---------|
| C-1 | `.kg` SQLite schema (~30 tables, `symbols_fts` FTS5, `fts5vocab` usage, per-model `vec0` tables) | Canonical dump diff (P0-3) |
| C-2 | MCP tool surface: names, args, response shapes for all 26 tools | MCP golden fixtures (P0-3) |
| C-3 | `.knowledge` bundle (markdown tree + manifests) | Untouched — language-neutral |
| C-4 | CLI machine contracts: `config --json/--db/--mcp-config`, stdout-pure-JSON modes, exit codes | CLI golden diffs (Phase 3) |
| C-5 | Agent-integration templates and auth-key resolution order (env > persisted file) | Parity tests in 1c |

Any contract-breaking need discovered mid-migration becomes a spec amendment
first, never a silent divergence in the Rust tree.

## 3. Target architecture

Cargo workspace at `rust/`, one tag/version with the Python package until the
flip:

- `cairn-store` — schema DDL mirror, connection factory (rusqlite `bundled`
  with FTS5, sqlite-vec static init), vec0 helpers, canonical dump for diffs.
- `cairn-query` — read path: FTS5 search, definition lookup, precise/fuzzy
  callers/callees, recursive impact, `explore` aggregation, `trace_flow`,
  taint, pack.
- `cairn-parse` — grammar crates + per-language symbol/edge extraction ports.
- `cairn-build` — scanner, rayon builder, resolver, incremental/graft,
  streaming closure.
- `cairn-daemon` — rmcp streamable-HTTP server, bearer auth, healthz, the
  launchd/reuse lifecycle.
- `cairn-cli` — the `cairn` binary (Phase 3).

## 4. Phase 0 — Foundations (≈2 weeks)

- **P0-1** Cargo workspace skeleton; CI Rust leg: fmt, `clippy -D warnings`,
  `cargo test`, cargo-deny (advisories/licenses), release dry-run build
  matrix (aarch64/x86_64 × macOS/Linux, musl static for Linux). Does not gate
  Python releases yet.
- **P0-2** Baselines on the benchmark corpora (yarl, attrs) + the cairn
  self-store: full-build wall time, closure time + peak WAL, per-tool p95
  latency, daemon RSS. Re-measured in place; numbers are never ported.
- **P0-3** Parity harnesses:
  - MCP golden recorder: drives the live Python daemon across all 26 tools
    with a fixed query script, normalizes volatile fields (timings, metric
    counters), writes fixtures under `rust/golden/mcp/`.
  - `.kg` canonical dump: per-table sorted-row export so two builds of the
    same corpus compare content-equal regardless of row order/rowids.
- **P0-4** Tech-spec D-### decisions (Constitution C-03) for the first
  dependency batch: rmcp, rusqlite, sqlite-vec, tree-sitter core + grammar
  crates, clap. Pin versions at decision time.
- **P0-5** Spike: rmcp skeleton serving `search_symbols` read-only from a real
  `.kg` — proves transport + store stack end to end. Merged inert; no user
  surface.

**Phase 0 exit:** Rust CI leg green; baselines + goldens committed; spike
serves `search_symbols` golden-equal.

## 5. Phase 1 — Read-path daemon (≈6–8 weeks)

- **1a** Store layer: read-only open, pragma parity (`busy_timeout`,
  journal settings), vec0 load, per-model table resolution.
- **1b** Query engine port, tool by tool: `search_symbols` (BM25 ordering
  parity), `find_definition`, `get_callers`/`get_callees` (precise + fuzzy),
  `impact_analysis` (recursion + truncation semantics), `explore` assembly,
  `trace_flow`, `semantic_search` (server + hash backends; brute-force cosine
  in native Rust — the numpy fast-path becomes ordinary code), memory
  recall/record, knowledge tools.
- **1c** Daemon: rmcp streamable HTTP on the same port; bearer-key resolution
  order per C-5; healthz payload parity (consumed by `cairn connect`'s
  reuse-guard); launchd plist and stray-sweep reused — the plist execs the
  Rust binary.
- **1d** Opt-in switch: `CAIRN_DAEMON_IMPL=rust` (or `cairn connect --impl
  rust`) starts the Rust daemon; default stays Python; doctor reports which
  impl is serving.
- **1e** Parity burn-down: run the golden suite; classify every diff —
  volatile field → normalize; real divergence → fix or record a ruling.

**Gate G1 (Phase 1 exit):**
- 26/26 tools golden-equal on ≥3 stores (cairn self, yarl, attrs).
- `cairn connect` against the Rust daemon works unchanged (auth + healthz +
  store-mismatch guard).
- Daemon RSS ≤ 60% of Python's; no tool regresses p95 latency.
- Shipped in a tagged release behind the opt-in flag.

**Decision point:** proceed to Phase 2 only if G1 passes. If Rust shows no
measurable serving win, stop here — the schema/golden/harness investment
still pays for the Python codebase, and nothing user-facing regressed.

## 6. Phase 2 — Build path (≈10–12 weeks)

- **2a** Parsers: port per-language extraction (16 modules, inline queries).
  Each language lands separately behind its existing golden fixtures and
  corpus coverage. The tree-sitter-kotlin crate replaces the vendored C
  extension and its `setup.py` build — revalidate node-type coverage against
  the vendored grammar before switching.
- **2b** Resolver: exact/ambiguous/unresolved triage ported exactly;
  differential check = per-corpus resolution stats (exact% within tolerance,
  zero silent label flips).
- **2c** Builder + scanner: rayon pool; output independent of parse order;
  id determinism handled at dump time if rowids diverge.
- **2d** Incremental + watcher (`notify` crate): graft semantics preserved.
- **2e** Closure: streaming level-BFS rewrite with bounded memory; the
  flagship target is ≥5× on the recorded blowup case.
- **2f** Embeddings build path: hash + server backends native; vec0 rebuild
  via the sqlite-vec crate. Local sentence-transformers is not ported — the
  embedding-server backend is the sanctioned path (§9).
- **2g** SCIP import: prost port of the checked-in stub's semantics;
  run-once auto-gen registry behavior preserved.

**Gate G2:** Python-built and Rust-built `.kg` canonical dumps compare
content-equal per corpus; build wall time ≤ Python on all corpora; closure
≥5× faster on the blowup case. Runtime switch `CAIRN_BUILD_IMPL=rust` for
early adopters; Python build stays default until the flip.

## 7. Phase 3 — CLI + distribution + flip (≈6 weeks)

- **3a** clap port, command by command; human-readable output diffs reviewed
  per command; C-4 machine contracts asserted by tests.
- **3b** Dashboard: same templates + static assets served over HTTP
  (minijinja); no visual redesign rides the migration.
- **3c** Distribution (dual channel, PyPI required):
  - Wheels: maturin + PyO3 (abi3-py310) so `pip install cairn-intel`,
    pipx and uv keep working; the wheel entry-point binding (PyO3 `#[pyo3(name
    = "main")]` module vs bundled-binary exec shim, the ruff/uv pattern) is a
    recorded D-### decision. Extend the existing tag-driven `release.yml`
    Trusted Publishing flow to build and publish the wheel matrix — the
    release pipeline is reused, not replaced.
  - Binaries: cargo-dist (or release workflow) → GitHub Release binaries
    (aarch64/x86_64 macOS + Linux, musl static), Homebrew tap,
    `cargo binstall` — the Python-free channel.
- **3d** Flip (v1.0): the Rust engine is `cairn` on every channel; the PyPI
  package ships the rust binary in the wheel (impl=rust by default from the
  wheel), no deprecation window — pip/pipx/uv installs transition in place.
  Agent-install templates unchanged (URL/key flow is contract C-5, identical).
- **3e** Docs sweep: README, `docs/*`, AGENTS.md CLI-fallback section updated
  to the binary; this spec moves to archive.

## 8. Testing strategy

The pytest suite is the spec of record until the flip and keeps running; it is
not ported wholesale. Three layers replace it for Rust:

1. **Contract/golden tests** (Rust): MCP goldens, `.kg` canonical-dump diffs,
   CLI output goldens. These are the C-02 failing-test-first mechanism — a
   workstream's test is the golden that its implementation must turn green.
2. **Rust unit tests** for algorithm invariants, floored by a port of the
   `pytest -m core` smoke set (one focused case per core function).
3. **Differential corpus runs** in CI — nightly, not per-PR, for cost; per-PR
   runs the smoke corpora only.

Known-quirk parity: goldens capture actual Python behavior; intentional
divergences get a recorded ruling and a CHANGELOG line, never a silent break.

## 9. What stays Python (sidecars)

- `[ingest]` PDF/docx conversion: no equal-quality Rust counterparts; stays a
  pip extra on the PyPI package (wheel installs keep `[ingest]`/`[semantic]`
  working as today, binary detects them at runtime). Binary-only installs
  (brew/binstall) invoke a pip-installed sidecar or skip the feature; drop it
  only if usage data says it is unused.
- `[semantic]` local embeddings: served by the embedding-server backend.
  An `ort`-based local backend is a possible post-flip D-###, explicitly out
  of scope here.
- `[graph-analytics]` Louvain: petgraph has none; port via a thin crate or
  keep as a sidecar — decide at Phase 2 entry.

## 10. Dependencies (Constitution C-03)

All crate additions land as recorded D-### decisions with cost (binary size,
platform coverage, license) and pinned versions; cargo-deny gates from P0-1.
First batch (P0-4): rmcp, rusqlite, sqlite-vec, tree-sitter core + 15 grammar
crates, clap. Later batches: notify, prost, minijinja, cargo-dist tooling,
maturin + pyo3 (abi3-py310) for the required PyPI wheel channel.

## 11. CI and shipping

- New Rust leg per P0-1; Python legs unchanged until 3d. rustfmt/clippy join
  pre-commit as local hooks.
- C-01 applies unchanged to every landing: branch → pre-commit → conventional
  commit → PR (audit checklist) → CI.
- One release tag for the repo; the cargo package version tracks the pyproject
  version until the flip, then becomes the only version.

## 12. Risk register

| Risk | Mitigation |
|------|------------|
| Grammar-crate drift vs pinned Python grammar versions | Pin crates to the matching parser.c releases; per-language golden revalidation in 2a |
| sqlite-vec is pre-v1 | Pin; vec0 on-disk format already shared by both sides |
| rmcp major-version churn | Golden transport tests; SDK is Tier-1 official |
| Solo-maintainer stall near the end | Hard gates; every phase ships standalone value; Python never bit-rots before the flip |
| Determinism mismatches (rowids, float text, ordering) | Canonical-dump normalization defined once in P0-3 |
| Two daemons, one port | Impl switch is explicit; connect's reuse-guard verifies store path; doctor reports the serving impl |
| CI cost doubles during transition | Cross-compile + cargo cache for the Rust leg; corpus differentials nightly |
| Kotlin crate coverage gap vs vendored grammar | Node-type revalidation gate in 2a before the vendored ext is dropped |
| PyPI wheel channel re-adds per-platform wheel builds | abi3-py310 collapses the Python-version matrix to one wheel per platform; existing release.yml Trusted Publishing is reused |

## 13. Timeline (solo, elapsed)

Phase 0 ≈2 wks → Phase 1 ≈6–8 wks → Phase 2 ≈10–12 wks → Phase 3 ≈6 wks:
roughly 6–7 months to the flip at part-time pace; gates can stop or pause the
campaign after any phase with a shippable product.

## 14. Immediate next actions

1. P0-1 workspace skeleton + CI leg.
2. P0-2 record baselines.
3. P0-3 build both parity harnesses, capture initial goldens.
4. P0-4 first D-### dependency batch.
5. P0-5 rmcp + search_symbols spike.

## 15. Phase 4 — Leftovers to Python-engine removal

Supersedes §14. Re-validated against `main`: the Rust daemon and build engine
have landed, but the PyPI `cairn` entry point is still the Python CLI. The
bundled binary only replaces the daemon, about half the top-level commands are
`not_ported` stubs in `cairn-cli`, and the parity oracles (`parity.py`,
`g2_check.sh`, `cli_golden.py`, `wheel_smoke.sh`) only run by hand. Phase 4
closes that gap. Workstreams run in order and each ends at a gate.

### 4a — Parity gates in CI

- **4a-1** Add a CI job that runs `parity.py` replay over `rust/golden/stores/`,
  `g2_check.sh`, `cli_golden.py` and `wheel_smoke.sh`: nightly and on manual
  dispatch, plus per-PR on `rust/**` for the cheap subset.
- **4a-2** Extend `g2_check.sh` with a rebuild-into-an-existing-store leg and a
  `build --staging` leg. Today the oracle only builds into fresh stores.

**G4a:** all four oracles green on `main` in CI.

### 4b — Engine correctness

Each fix is failing-test-first (C-02). Fix Rust first. Fix Python too while it
is still a shipped fallback.

| ID | Defect | Where |
|----|--------|-------|
| 4b-1 | `build --staging` never renames `{db}.tmp` over the store | `cairn-cli` `cmds_core.rs` build |
| 4b-2 | A non-staging rebuild restarts `IdGen` at 0 without clearing rows, so ids and `UNIQUE(repo_id,path)` collide | `cairn-build` `builder.rs` `build_graph` |
| 4b-3 | The dataflow PK migration marker is recorded without rebuilding the table | `cairn-store` `schema.rs` `apply_schema` |
| 4b-4 | Call and contains edges are attributed to the first same-named symbol in the file | `cairn-build` `builder.rs` edge insert |
| 4b-5 | The default incremental update lists only uncommitted changes (`git diff HEAD`), and git paths are read without `-z` | `cairn-build` `incremental.rs` |
| 4b-6 | Repair drops receiver type and arity; it is repo-scoped while the nulling is global; resolved edges lose `target_name`, so repair and the ancestor index miss them | `resolver.rs`, `resolver.py`, `incremental.*` |
| 4b-7 | The import-aware candidate score adds the match offset | `resolver.rs`, `resolver.py` |
| 4b-8 | `trace_flow` keeps a node's first-visit depth, so shorter paths are pruned | `cairn-query` `graph.rs`, `traversal.py` |
| 4b-9 | The daemon's writable tool connection bypasses the store factory (no WAL, mmap or sqlite-vec registration). A healthz 200 carrying `unhealthy` is not a defect: Python reuses it the same way, and a fresh workspace is `unhealthy` until its first build | `cairn-daemon` `tools.rs` |
| 4b-10 | User config is overwritten when its backup or parse fails | `agent_install.rs`, `dashboard/settings.rs`, `paths.py` |

Follow-ups found while landing 4b-1–4b-10, same rules:

| ID | Defect | Where |
|----|--------|-------|
| 4b-11 | Cross-repo `imports` edges nulled by the delete-leg backfill are re-materialized only for repos that had files reindexed | `incremental.py`, `incremental.rs` |
| 4b-12 | A file whose mtime moved but whose content did not is fully reindexed (and triggers workspace-wide repair); compare `files.hash` first | `incremental.py`, `incremental.rs` change detection |
| 4b-13 | `incremental_update` opens its connection before taking the build lock, so a full-build swap in that window sends the pass's writes to the replaced file (Python has the same window) | `incremental.rs`, `incremental.py` |
| 4b-14 | The Rust `sync` repair path may write without the build lock that Python's `cairn sync` takes | `cmds_core.rs` `repair_changed` |
| 4b-15 | Decorator edges still mis-attribute in two shapes: a decorated definition nested in a same-named outer definition, and a decorator above a differently named definition when the same-named one appears later | both builders |
| 4b-16 | `flow_golden.py --check` fails on `main`: `parity.py --record` rewrites the `rust/golden/mcp/*` fixtures it also writes, so the generator cannot be used as a check | `scripts/rust_migration/flow_golden.py`, `parity.py` |
| 4b-17 | The dashboard reports a malformed `config.json` as a generic write failure; surface the parse reason | `dashboard/mod.rs`, `routes/settings.py` |
| 4b-18 | Stores built before the `edges` signal columns keep the old repair behaviour until rebuilt, with no prompt; add a `doctor` check that recommends a rebuild | `cmds_doctor.rs`, `cli/system/doctor.py` |
| 4b-19 | `parity.py --record` for a corpus store rebuilds the pinned corpus from a different source than the original capture (hundreds of corpus files change); it must reproduce the pinned subset exactly or refuse | `scripts/rust_migration/parity.py` |

**G4b:** every fix lands with its regression test, and G4a stays green.

### 4c — Port the remaining CLI surface

Ordered by who depends on each command. Each command lands with a
`cli_golden.py` case (C-4) before its stub arm is removed.

- **4c-1** The agent workflow in `AGENTS.md`:
  - `task list/show/claim/complete/drop`
  - `wiki generate/status/retry/export/enrich`, including the LLM task queue
    and the deterministic critic. The critic must also check `## Notes`, or
    promote the spliced body.
  - `hooks install/uninstall`
- **4c-2** Lifecycle and ops: `status`, `version`, `upgrade`, `uninstall`,
  `sync`, `metrics`, `report`, `download-reranker`.
- **4c-3** Graph analytics: `semantic`, `embed`, `grep`, `map`, `blast`,
  `rationale`, `dataflow build/lookup`, `viz`. `communities` (Louvain) needs a
  §9 D-### first: port it or keep it as a sidecar.
- **4c-4** Knowledge layer:
  - `knowledge ingest/rebuild/embed`. Document conversion stays the `[ingest]`
    sidecar per §9.
  - `workflow sync`, tarball `knowledge export`, `validate`, `validate-paths`,
    `verify`, `skill generate`
  - `memory reflect/evolve/capture/embed/batch-critic/consolidate`
  - `compass generate/gaps/flow/flow-gaps`
- **4c-5** `ask`, `context`, `review`, currently declared PyPI-only: port them
  or record a ruling.
- **4c-6** Dev-only `prs`, `bench`, `eval`: port them or record a ruling that
  they move to `scripts/`.
- **4c-7** SCIP overlay hook in the Rust builder.

**G4c:** no `not_ported` arm left in `cairn-cli` unless it carries a recorded
ruling, and `cli_golden.py` green.

### 4d — Entry-point flip

- **4d-1** Windows D-###: either add a Windows target to `release-rust.yml` and
  bundle it in the Windows wheels, or keep the Python fallback on Windows under
  a recorded ruling.
- **4d-2** The PyPI console script execs the bundled binary for every command
  (D-011 exec shim). The Python CLI runs only when no binary is present.
- **4d-3** Distribution configuration: Homebrew tap repo and token, crates.io
  publish.
- **4d-4** Record the open gate measurements: daemon RSS ≤ 60% of Python,
  per-tool p95 no worse, closure ≥ 5× on the blowup case. Finalize the
  provisional pins D-007–D-010.

**G4d:** `wheel_smoke.sh` green on every wheel platform, with `cairn <cmd>`
resolving to the binary.

### 4e — Python-engine removal

- **4e-1** Freeze the oracles: regenerate the goldens from Python one last
  time, then drop the Python side of the differential scripts. From here the
  Rust goldens are the spec of record (§8).
- **4e-2** Delete the Python engine modules under `src/cairn`. Keep only the
  wheel shim and the §9 sidecars. Port whatever pytest coverage is worth
  keeping into Rust tests first.
- **4e-3** Docs sweep (README, `docs/*`, `AGENTS.md` CLI fallback); archive
  this spec.

**G4e:** the PyPI wheel holds only the shim, the binary and the sidecars;
Rust CI and `wheel_smoke.sh` green.

### Implementation detail for 4c–4e

#### Execution protocol (every wave)

1. **Implement**, one agent per workstream, each in its own git worktree and
   branch; no pushes from agents. Warm the build from a copy of the main
   checkout's `rust/target`.
2. **Review**, a fresh agent per branch that sees the diff and the intent, not
   the author's conclusions.
3. **Fix**, the original author addresses the review on the same branch with
   new commits.
4. **Integrate**, merge every branch locally onto `main` and run the full gate
   set below on the combined tree before pushing; stack a PR on another when
   both touch the same test file.

Per-change gates: `cargo fmt --check`, `cargo clippy --workspace --all-targets
-D warnings`, `cargo test --workspace --no-fail-fast`, the targeted pytest
selection, then `g2_check.sh` (`G2_LEGS=base,rebuild,staging`), `parity.py`
self/yarl/attrs, `cli_golden.py`, `resolver_parity.py`. Rebuild the release
binary before the oracles; they reuse whatever binary the target dir holds.

Golden changes: refresh only the affected fixture, captured from the Python
daemon over the unchanged pinned store, and only when Rust then matches it
byte for byte. Never accept a wholesale `--record` diff (4b-19).

Local quirk: `cmds_doctor::tests::daemon_check_reports_the_serving_impl`
asserts against whatever daemon is live on the machine and fails while a
developer daemon runs.

Model fit: mechanical port-and-compare work against a Python reference and a
golden (4c-2, 4c-3, most of 4c-4, 4c-6, 4e deletions) suits a small model with
a stronger reviewer. Stateful or cross-cutting work (4c-1 task queue and
critic, memory scoring in 4c-4, 4c-5, 4c-7, 4d decisions, 4e scoping) needs a
stronger implementer.

#### 4c — command map

Python CLI sources are the behaviour of record; Rust dispatch lives in the
listed `cairn-cli` module, library logic in the matching crate.

| Item | Commands | Python source | Rust dispatch | Library home |
|------|----------|---------------|---------------|--------------|
| 4c-1 | `task list/show/claim/complete/drop` | `cli/task.py`, `llm/tasks.py` | `cmds_knowledge.rs` | new task-queue module beside the ported KM layer in `cairn-query` |
| 4c-1 | `wiki generate/status/retry/export/enrich` | `cli/wiki.py`, `wiki/*`, `compass/critic.py` | `cmds_knowledge.rs` | same, plus the critic over `cairn-store` |
| 4c-1 | `hooks install/uninstall` | `cli/hooks_viz.py`, `hooks/*` | `cmds_system.rs` | `agent_install.rs` (shares hook rendering) |
| 4c-2 | `status`, `sync`, `metrics`, `report` | `cli/system/{status,sync,metrics,report}.py` | `cmds_system.rs` | `cairn-store`, `cairn-build` (sync takes the build lock) |
| 4c-2 | `version`, `upgrade`, `uninstall`, `download-reranker` | `cli/upgrade.py`, `cli/uninstall.py`, `cli/download_reranker.py` | `cmds_system.rs` | `cairn-cli` |
| 4c-3 | `semantic`, `embed` | `cli/embed.py` | `cmds_core.rs` | `cairn-query` search, `cairn-build` embeddings |
| 4c-3 | `grep`, `map`, `blast`, `rationale` | `cli/{grep,map,blast,rationale}.py` | `cmds_core.rs` | `cairn-query` |
| 4c-3 | `dataflow build/lookup` | `cli/dataflow.py`, `graph/dataflow.py` | `cmds_core.rs` | `cairn-build` closure/dataflow |
| 4c-3 | `viz` | `cli/hooks_viz.py`, `viz/*` | `cmds_core.rs` | dashboard viz layer |
| 4c-3 | `communities` | `cli/communities.py` | `cmds_core.rs` | D-### first: port Louvain or keep the sidecar |
| 4c-4 | `knowledge ingest/rebuild/embed`, tarball export | `cli/knowledge.py`, `knowledge/*` | `cmds_knowledge.rs` | KM layer; conversion stays the `[ingest]` sidecar |
| 4c-4 | `workflow sync` | `cli/knowledge.py` | `cmds_knowledge.rs` | KM layer |
| 4c-4 | `memory reflect/evolve/capture/embed/batch-critic/consolidate` | `cli/memory.py`, `memory/*` | `cmds_knowledge.rs` | KM layer memory lifecycle |
| 4c-4 | `compass generate/gaps/flow/flow-gaps` | `cli/compass.py`, `compass/*` | `cmds_knowledge.rs` | KM layer + `cairn-query` flow |
| 4c-4 | `validate`, `validate-paths`, `verify`, `skill generate` | `cli/validate.py`, `cli/compass.py`, `cli/skill.py`, `skillgen/*` | `cmds_knowledge.rs` | KM layer |
| 4c-5 | `ask`, `context`, `review` | `cli/ask_context.py`, `cli/review.py`, `review/*` | `cmds_core.rs` | `cairn-query` cross-layer router |
| 4c-6 | `prs`, `bench`, `eval` | `cli/prs.py`, `cli/bench.py`, `bench/*`, `eval.py` | `cmds_core.rs`, or move to `scripts/` | — |
| 4c-7 | SCIP overlay during build | `graph/builder.py` `_apply_scip_overlay` | `cairn-build` builder | ported SCIP importer |

Known defects to carry into the port rather than replicate: the critic skips
`## Notes` while the task queue promotes the raw body; a critic exception
leaves a task `in-progress` and unclaimable; archived memories age from
creation, not archival; all-non-ASCII knowledge titles share one id. Fix each
in Python first (test-first), then port the fixed behaviour.

Per command: add the `cli_golden.py` case from the Python CLI, implement until
it is green, then remove the stub arm. Machine contracts (C-4) are asserted by
tests; human-readable output is diffed and reviewed.

#### 4d — entry-point flip steps

1. Windows D-###: build `x86_64-pc-windows-msvc` in `release-rust.yml` and
   bundle it in Windows wheels, or record the Python-fallback ruling.
2. Console-script shim: `cairn.cli:main` execs the discovered binary
   (`CAIRN_RUST_BIN` > bundled > PATH) with the original argv and exit code;
   the Python CLI is the fallback only when no binary is found. `wheel_smoke.sh`
   asserts that every C-4 command resolves to the binary.
3. Configure the Homebrew tap repo and token; publish `cairn-cli` to crates.io
   so the binstall metadata resolves.
4. Run `baseline.py` and `closure_bench.py` against the Rust engine and record
   RSS, p95 and the closure ratio in `baselines.md`; finalize D-007–D-010.

#### 4e — removal steps

1. Final Python capture: record every golden and dump fixture from the Python
   engine at one commit; from then on fixtures change only through Rust.
2. Convert differential scripts (`g2_check.sh`, `resolver_parity.py`,
   `cli_golden.py`, `parity.py`) to replay-only against the frozen fixtures.
3. Inventory pytest coverage by contract; port contracts without a Rust owner
   test; delete the rest with the modules.
4. Delete the engine packages under `src/cairn` (graph, parsers, mcp_server,
   cli, dashboard, knowledge, memory, wiki, compass, okf, retrieval, llm,
   review, hooks, skillgen, telemetry, viz, bench); keep the shim, `paths`
   discovery and the §9 sidecars.
5. Docs sweep and archive this spec.
