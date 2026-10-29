# Tasks: native-embed

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
**Lifecycle**: v2
Status reflects code state per [survey.md](survey.md), not intent.
**Delivered**: commit @ c6d0fd64 — all 12 tasks done; suites green in both build flavors (workspace default EXIT=0, feature-on EXIT=0 incl. the real-model 1024-dim canary); e2e verified (download → embed → semantic → doctor); cargo-deny green on both graphs

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1     | 3     | 3    |
| 2     | 6     | 6    |
| 3     | 3     | 3    |
| **Σ** | 12    | 12   |

## Phase 1: Feature skeleton and backend resolution (FR-003, FR-004, NFR-001)
<!-- Checkpoint: both cfg builds compile; feature-off resolves native as
     unavailable with the feature hint; the Native variant and native stamp
     land (feature-on resolution is probed and verified in Phase 2, after the
     gated module exists); default dependency tree unchanged; both deny
     graphs validated. -->
- [x] T001 [P] Wire the local-embed cargo feature and the pinned fastembed dependency (FR-001, NFR-001)
  - Touches:
    - `rust/crates/cairn-query/Cargo.toml`
    - `rust/crates/cairn-build/Cargo.toml`
    - `rust/crates/cairn-daemon/Cargo.toml`
    - `rust/crates/cairn-cli/Cargo.toml`
  - D-001/D-003/D-004. Verify: `cargo build -p cairn-cli` and `cargo build -p cairn-cli --features local-embed`; `cargo tree -e normal -p cairn-cli` lists no fastembed by default.
  - done 2026-10-29 — both builds compile; `cargo tree -e normal -p cairn-cli -i fastembed` reports no matching package. Feature set is `onnx` + `hf-hub-rustls-tls` + `ort-download-binaries-rustls-tls`: fastembed declares ort `default-features = false`, so the prebuilt-binaries feature must be explicit for D-004.
- [x] T002 Add the native resolution arm, stamp constant, and feature-off rejection hint (after T001) (FR-003, FR-004)
  - Touches:
    - `rust/crates/cairn-query/src/tools_search.rs`
    - `rust/crates/cairn-query/src/tools_search/tests.rs`
  - D-007/D-008/D-009. Feature-off deliverable: the explicit arm returns `(EffectiveBackend::Unavailable, None)` with the hint naming the cargo feature; the feature-on arm resolves the `Native` variant unprobed — the availability probe and hash fallback are threaded in T005 once the gated module exists (D-008); the ungated stamp constant lands beside `HASH_MODEL` (D-009). Survey gaps: FR-003 PARTIAL — the catch-all arm resolves `native` silently to Hash today and the install hint names no cargo feature; FR-004 PARTIAL — the machinery is stamp-agnostic but no native stamp exists. Verify first: `cargo test -p cairn-query --lib tools_search::tests` (29 passing, survey) and grep the two pinned tests' fixture names before changing the arm.
  - done 2026-10-29 — `cargo test -p cairn-query --lib tools_search::tests` green (29 default / 31 feature-on); both pinned tests updated (native rejection row + local-embed hint assertion).
- [x] T003 [P] Validate cargo-deny for the default and feature-on graphs (after T001) (NFR-001)
  - Touches:
    - `rust/deny.toml`
  - Direct licenses verified in research RQ2; the tokenizers transitive tail (onig, esaxx) is what this run rules on; extend the allow list only as a recorded ruling. Verify: `cargo deny check` (CI gate — the command is absent locally per survey NFR-001).
  - done 2026-10-29 — cargo-deny installed locally; both graphs green: `cargo deny --config rust/deny.toml check` and `... --features cairn-cli/local-embed check` report `advisories ok, bans ok, licenses ok, sources ok`. Recorded rulings: `CDLA-Permissive-2.0` (webpki-roots, rustls download client) and `MPL-2.0` (option-ext, hf-hub dirs path) added to licenses allow; `RUSTSEC-2024-0436` (unmaintained `paste` via ort) added to advisories ignore. onig declares MIT; esaxx-rs Apache-2.0 (both already allowed).

## Phase 2: Native transport on both legs (FR-001, FR-002, FR-005, FR-007, NFR-002, NFR-003, NFR-004)
<!-- Checkpoint: the pre-warm flag downloads and exits; a warm cache embeds
     corpus and query in-process at 1024-dim under one stamp end to end; a
     cold cache falls back to hash with the pre-warm hint; parity suite
     green. -->
- [x] T004 [P] Implement the gated native embedder module (after T001) (FR-001, FR-002, NFR-002, NFR-004)
  - Touches:
    - `rust/crates/cairn-query/src/embed_native.rs`
    - `rust/crates/cairn-query/src/lib.rs`
  - New module: lazy OnceLock-mutexed model (D-006), dense BGEM3 embed (D-002), cache probe, warm = construct-and-drop (D-005). Verify: `cargo build -p cairn-query --features local-embed`.
  - done 2026-10-29 — module compiles and serves the real flow. Cache-layout note: hf-hub 0.5's Api download path writes `models--BAAI--bge-m3/snapshots/<commit>/` with no `refs/` pointer, so the probe scans snapshots for the ONNX files instead of resolving a ref file.
- [x] T005 Resolve native in semantic_env with the cold-cache hash fallback and query-leg dispatch (after T002, T004) (FR-001, FR-005, FR-007)
  - Touches:
    - `rust/crates/cairn-query/src/tools_search.rs`
    - `rust/crates/cairn-query/src/tools_search/tests.rs`
  - D-008. Threads the module's cache probe into the native arm of resolve_backend (the arm exists from T002; the probe comes from T004), then the fallback predicate and native-cold marker on SemanticEnv, then the `embed_query` Native dispatch. Survey gaps: FR-005 PARTIAL — the fallback predicate keys on the name local only and the hint names server setup, no pre-warm command; FR-007 PARTIAL — no test asserts corpus-leg equals query-leg stamp. Hermetic tests via a temp HF_HOME dir (C-04). Verify: `cargo test -p cairn-query --features local-embed --lib tools_search::tests`.
  - done 2026-10-29 — 31 tests green feature-on (hermetic cold/warm via temp FASTEMBED_CACHE_DIR under an env mutex); `native_cold` + extended `hash_fallback` on SemanticEnv; `embed_query` native arm verified end to end by the env-gated suite.
- [x] T006 Add the corpus-leg native transport with the stamp-parity test (after T005) (FR-001, FR-007, NFR-003)
  - Touches:
    - `rust/crates/cairn-build/src/embeddings.rs`
    - `rust/crates/cairn-build/tests/embeddings_parity.rs`
  - Transport::Native arm in embed_texts; resolve_embed_env maps Native with the stamp as env model; batching, progress, and rebuild wrappers untouched (NFR-003 PARTIAL: machinery exists but has never run under a native transport, survey). Verify: `cargo test -p cairn-build --test embeddings_parity` (3 passing, survey).
  - done 2026-10-29 — 4 tests green in both flavors; `native_config_stamps_both_legs_identically` pins corpus-leg (`current_model`) == query-leg (`semantic_env`) for the cold hash stamp, the warm native stamp, and the feature-off rejection. Real transport verified by the e2e run (embed → dim=1024, model=native/BAAI/bge-m3).
- [x] T007 [P] Restore the download-model pre-warm flag on cairn embed (after T004) (FR-002, FR-003)
  - Touches:
    - `rust/crates/cairn-cli/src/cmds_core.rs`
  - Restore the flag verbatim from the 99d32fa9 removal diff (survey FR-002); warm-then-exit in feature builds, feature-hint error otherwise (D-007). Verify the red state first: the debug binary errors `unexpected argument '--download-model' found` (survey).
  - done 2026-10-29 — flag restored on EmbedArgs; TC-001 verified for real (2.1G weights cached, exit 0, no embedding) and TC-017 verified on the feature-off build (exit 1, output names local-embed).
- [x] T008 Surface the native degraded-mode hint naming the pre-warm command (after T005, T007) (FR-005)
  - Touches:
    - `rust/crates/cairn-cli/src/cmds_core.rs`
  - Branch the embed-handler warning on the native-cold marker (D-008); the existing hash warning copy names server backends only (survey FR-005) — the native branch names `cairn embed --download-model`. Chained after T007: same file.
  - done 2026-10-29 — embed handler branches on `native_cold` first, naming the pre-warm command; observed live before the model warmed ("Using the hash embedder: the native model is not cached...").
- [x] T009 Add feature-on integration tests for dimension, stamp, and fallback (after T006) (FR-001, FR-004, FR-005)
  - Touches:
    - `rust/crates/cairn-query/tests/native_embed.rs`
  - Env-gated by CAIRN_EMBED_TEST_NATIVE (skips silently without a warm cache, C-04): assert 1024-dim dense vectors, the `native/BAAI/bge-m3` stamp on both legs, and the vec0 table name — the drift canary for the spec's model-drift risk. Verify: `cargo test -p cairn-query --features local-embed --test native_embed` (skip path).
  - done 2026-10-29 — skip path green (default invocation); with `CAIRN_EMBED_TEST_NATIVE=1 FASTEMBED_CACHE_DIR=<warm>` the canary passes against the real model (1024-dim, one stamp, both legs). Table-name note: the sanitizer's real output for the stamp is `vec_native_BAAI_bge_m3` (the survey's `vec_BAAI_bge_m3` claim dropped the prefix); asserted via the public `cairn_store::vec0::table_name`.
  - Env-gated by CAIRN_EMBED_TEST_NATIVE (skips silently without a warm cache, C-04): assert 1024-dim dense vectors, the `native/BAAI/bge-m3` stamp on both legs, and the vec0 table name — the drift canary for the spec's model-drift risk. Verify: `cargo test -p cairn-query --features local-embed --test native_embed` (skip path).

## Phase 3: Doctor, healthz, and docs (FR-006)
<!-- Checkpoint: doctor shows the native line (feature, cache, count) in
     both build flavors; healthz counts the native-cold fallback; README,
     cli-reference, and CHANGELOG document the flow. -->
- [x] T010 [P] Report native backend health in doctor (after T002, T004) (FR-006)
  - Touches:
    - `rust/crates/cairn-cli/src/cmds_doctor.rs`
  - Extend check_embeddings with the three native facts: feature compiled (cfg on the cli passthrough feature), cache state (ungated wrapper returning unknown when not compiled), native row count via model-prefix match (D-009). Survey gap: FR-006 TODO — rg for native, feature, or cache over cmds_doctor.rs returns 0 matches today. Verify (observable probe, per TC-007): `cargo build -p cairn-cli` then `rust/target/debug/cairn doctor` — rg the embedding line for the native feature-not-compiled report.
  - done 2026-10-29 — observed all three states live: feature-off "backend: native, feature not compiled (0 native embedding(s) in store)" + hint; feature-on cold "model not cached -- hash fallback in use" + pre-warm hint; feature-on warm "backend: native, model cached, 5 native embedding(s)" PASS.
- [x] T011 [P] Count the native cold-cache fallback as a healthz degradation (after T005) (FR-006)
  - Touches:
    - `rust/crates/cairn-daemon/src/healthz.rs`
  - degradation_count counts local-hash and ANN-off only today (survey); add the native-cold hash fallback to the count without changing the bounded payload shape. Traced to the updated FR-006 text: it covers doctor and the healthz endpoint, with the cold-cache fallback counted as a degradation.
  - done 2026-10-29 — degradation_count now derives from `semantic_env(None).hash_fallback` (covers local default and native-cold; an explicit `hash` configuration stays uncounted); `cargo test -p cairn-daemon` green.
- [x] T012 [P] Document the native backend, pre-warm flag, and feature install path (FR-002, FR-003)
  - Touches:
    - `README.md`
    - `docs/cli-reference.md`
    - `CHANGELOG.md`
  - Document CAIRN_EMBED_BACKEND=native, the pre-warm flag, the local-embed install path, and the fallback semantics; un-retire the flag in the changelog (CHANGELOG line 41, survey FR-002); keep the default-build no-local-inference posture statement.
  - done 2026-10-29 — README config row + trade-off passage (default-build posture kept, feature path documented), cli-reference embed row gains --download-model, CHANGELOG Unreleased Added entry un-retires the flag (the 0.24.2 Released section itself is left intact — released history is not rewritten; the Added entry supersedes it).

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `(implemented)` landed —
      implementation evidence durable before the one all-at-once tick ·
      `- [x]` done + proof note: `done <date> — <test/command that proves it>`
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
- Every task cites FR-### or an applicable NFR-###; a task with neither is
      scope creep — fix the spec first
- Every code task carries a `Touches:` block (repo-relative files,
      directories, or globs); `[P]` tasks whose touches overlap are chained, not
      spawned together
