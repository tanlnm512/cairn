# Plan: native-embed

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-29

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | Requirements | Depends on |
|-------|-----------|---------------------|--------------|------------|
| 1 | Feature skeleton and backend resolution | An opt-in `local-embed` cargo feature compiles cairn-query (with passthrough on cairn-build, cairn-daemon, cairn-cli) carrying a pinned, trimmed fastembed; feature-off builds resolve the name `native` explicitly as unavailable with a hint naming the cargo feature, and the ungated `native/BAAI/bge-m3` stamp lands beside the hash stamp; both cargo-deny graphs validated | FR-003, FR-004, NFR-001 | — |
| 2 | Native transport on both legs | `cairn embed --download-model` pre-warms the HuggingFace cache and exits without embedding; feature-on builds resolve `native` to the Native backend via the model-cache probe; with the cache warm, corpus and query legs embed in-process with dense 1024-dim bge-m3 vectors under one stamp end to end (embed, vec0 table, semantic search); a cold cache falls back to the hash embedder with a visible hint naming the pre-warm command | FR-001, FR-002, FR-005, FR-007, NFR-002, NFR-003, NFR-004 | Phase 1 |
| 3 | Doctor, healthz, and docs | `cairn doctor` reports the native feature state, model cache state, and native embedding count in any build; the daemon healthz counts the native cold-cache fallback as a degradation; README, cli-reference, and CHANGELOG document the flow and the feature install path | FR-006 | Phase 2 |

## Dependencies
- The phase spine is serial: the cargo feature must exist before any cfg-gated
  code compiles, and the resolution contract (`EffectiveBackend::Native`, the
  stamp constant, the fallback semantics) must exist before transports, CLI
  copy, and doctor consume it.
- Within each phase the work areas are file-disjoint and run concurrently
  (see the map).
- Biggest external risk pulled early: the ort/onnxruntime dependency tree and
  its cargo-deny ruling land in Phase 1, so the spec's deny risk is proven or
  demoted before any transport code exists.

## Parallelization map
<!-- Which work areas are independent (different files/subsystems, no shared
     state) and can be developed concurrently, and which are strictly
     sequential. The task-breaker turns this into [P] markers per task. -->
- Independent after the cargo wiring: the feature-off half of the resolution
  arm and its tests (`rust/crates/cairn-query/src/tools_search.rs`,
  `rust/crates/cairn-query/src/tools_search/tests.rs`) ∥ the native embedder
  module (new `embed_native.rs` plus its mod declaration in
  `rust/crates/cairn-query/src/lib.rs`) ∥ cargo-deny validation
  (`rust/deny.toml`) — disjoint files, none consuming another's output. The
  feature-on half of the arm is not in this wave: it calls the module's
  cache probe (D-008 in tech-spec.md), so it lands after the module, in
  Phase 2.
- Independent after the resolution contract: the corpus-leg transport
  (`rust/crates/cairn-build/src/embeddings.rs`,
  `rust/crates/cairn-build/tests/embeddings_parity.rs`) ∥ the query-leg
  dispatch and fallback (tools_search.rs, chained after the embedder module)
  ∥ the CLI flag and hint copy (`rust/crates/cairn-cli/src/cmds_core.rs`) —
  disjoint crates and files; all consume the enum variant, stamp constant, and
  SemanticEnv fields, not each other.
- Independent in Phase 3: doctor (`rust/crates/cairn-cli/src/cmds_doctor.rs`)
  ∥ healthz (`rust/crates/cairn-daemon/src/healthz.rs`) ∥ docs (`README.md`,
  `docs/cli-reference.md`, `CHANGELOG.md`) — disjoint files, no upstream
  beyond the transports they observe.
- Strictly ordered: cargo feature wiring → every cfg-gated task (nothing gated
  compiles without the feature and dependency in place).
- Strictly ordered: resolution contract → transports, CLI, doctor (they
  consume `EffectiveBackend::Native`, the stamp, and the fallback marker it
  defines — a drifted contract would surface as rework in every consumer).
- Strictly ordered: native embedder module → the probed feature-on
  resolution arm and semantic_env (the arm calls the module's cache probe) →
  corpus and query transports plus the pre-warm flag (the transports consume
  the module's cache probe, warm, and embed functions; the CLI hint consumes
  the fallback marker).

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->
- **After Phase 1**: `cargo build -p cairn-cli` and
  `cargo build -p cairn-cli --features local-embed` both succeed;
  `cargo test -p cairn-query --lib tools_search::tests` green with the
  native rows added (feature-off resolves unavailable plus the feature
  hint; the feature-on arm resolves the Native variant unprobed — its
  probe lands with the gated module in Phase 2); `cargo tree -e normal
  -p cairn-cli` lists no fastembed for the default build; `cargo deny
  check` unchanged for the default graph and clear for the feature-on
  graph.
- **After Phase 2**: `cargo test -p cairn-build --test embeddings_parity`
  green with the stamp-parity row added; `cargo test -p cairn-query
  --features local-embed --lib tools_search::tests` green — the feature-on
  build resolves Native on a warm cache and the hash fallback on a cold one
  (hermetic: temp HF cache dir, no network); with a
  pre-warmed cache, `CAIRN_EMBED_TEST_NATIVE=1 cargo test -p cairn-query
  --features local-embed --test native_embed` asserts 1024-dim vectors and
  the native stamp on both legs.
- **After Phase 3**: `cairn doctor` shows the native line (feature, cache,
  count) in both build flavors; README and cli-reference document
  `CAIRN_EMBED_BACKEND=native` and `--download-model`; CHANGELOG un-retires
  the flag.

## Risks & mitigations
- Risk: the ort/onnxruntime tree trips cargo-deny (licenses or advisories) →
  mitigation: Phase 1 validates both graphs before transport work starts;
  deny.toml extensions are recorded rulings inside its documented
  extend-as-batches policy, and a deny failure downgrades to the spec's
  documented build-from-source path before it blocks the spec.
- Risk: fastembed releases drift model ids or the 1024 dimension →
  mitigation: exact crate pin plus the env-gated dimension and stamp test in
  Phase 2.
- Risk: cache-location precedence diverges between the probe, pre-warm, and
  inference paths → mitigation: all three go through fastembed's own cache
  logic (pre-warm constructs the model and drops it), and the probe reads the
  cache dir fastembed itself reports.
- Risk: switching `native` from silent hash semantics to explicit rejection
  changes behavior for existing configurations → mitigation: unknown names
  keep hash semantics (the catch-all arm stays), only `native` gets an
  explicit arm, and the two pinned resolution and hint tests are updated in
  the same commit.
- Risk: tests that depend on machine model-cache state → mitigation:
  fallback tests are hermetic (temp HF cache directory); real-inference tests
  are env-gated and skip silently without a warm cache.

## Delivery
Branch `feat/native-embed` (spec header), one PR landing the whole plan, the
single end-of-plan commit — code + docs together, never per task. The C-01
workflow throughout: prek run over all files, conventional commit, PR audit
checklist filled, CI watched on the feature branch.
