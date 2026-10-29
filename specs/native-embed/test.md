# Test Cases: native-embed

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-29
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.

Conventions: the default build means a cairn binary built without extra
cargo features; a feature build means one built with the `local-embed`
cargo feature (a Given precondition, never an implementation detail). Cases
that need downloaded model weights or a feature build are manual — the
bounded auto cases pin the same contracts on the default build and via the
existing regression suites.

## TC-001 — Pre-warm downloads model weights and exits without embedding
- **Story**: US1 · **Traces to**: FR-002, AC1
- **Given** a feature build, an empty fastembed-managed model cache (isolated to a fresh directory via the cache environment variable), and a freshly initialized workspace with at least one indexed symbol
- **When** the user runs the pre-warm command (`cairn embed --download-model`) with the native backend selected
- **Then** the BAAI/bge-m3 weights land in the model cache (the model's cache directory is non-empty afterwards), the command exits 0, no embedding runs (no embedding progress or summary), and the workspace's embedding count stays zero
- **Pass condition**: manual — run `CAIRN_EMBED_BACKEND=native rust/target/debug/cairn embed --download-model` with the model cache root isolated to an empty directory; pass = exit 0, the model's cache directory under that root is non-empty (weights present), output contains no embedding summary, and `rust/target/debug/cairn doctor` afterwards reports the model as cached with a native embedding count of 0

## TC-002 — Native embed writes vectors under the native stamp
- **Story**: US1 · **Traces to**: FR-001, FR-004, AC2
- **Given** a feature build, the model cache pre-warmed (TC-001 state), and a small indexed workspace (a handful of symbols, enough to span several small batches)
- **When** the user runs `CAIRN_EMBED_BACKEND=native cairn embed --build-index`
- **Then** the run completes, the summary names the model stamp `native/BAAI/bge-m3` (not the hash embedder's stamp), and the ANN index holds rows under the native stamp — no new rows appear under the hash stamp
- **Pass condition**: manual — run the embed, then `rust/target/debug/cairn doctor`; pass = exit 0, the summary/doctor report the stamp `native/BAAI/bge-m3`, and doctor's per-model counts show native-stamp rows while the hash-stamp count is unchanged

## TC-003 — Semantic search answers from native vectors, not a hash fallback
- **Story**: US1 · **Traces to**: FR-001, FR-007, AC3
- **Given** a workspace embedded natively (TC-002 state) and the native backend selected
- **When** the user runs `cairn semantic "<concept from the corpus>"`
- **Then** ranked results return from the native vectors: the output's provenance names the native backend/model stamp (not the hash backend), no degraded-mode warning appears, and corpus and query vectors were produced under the one native stamp (a hash query leg against native corpus rows would be unusable)
- **Pass condition**: manual — run `CAIRN_EMBED_BACKEND=native rust/target/debug/cairn semantic "<concept>"`; pass = results returned, provenance line names the native stamp `native/BAAI/bge-m3`, no hash/degraded wording in the output

## TC-004 — Corpus and query legs keep one backend and stamp under one configuration
- **Story**: US1 · **Traces to**: FR-007
- **Given** a feature build, the model cache pre-warmed, and an indexed workspace
- **When** the user embeds natively and then runs semantic queries under that same configuration — the corpus-and-query stamp parity check introduced with the native transport
- **Then** both legs use one effective backend and model stamp: the corpus embed and the query results report the same `native/BAAI/bge-m3` stamp for that one configuration (a mismatched query stamp could not search the native rows at all)
- **Pass condition**: manual — under one configuration, run `CAIRN_EMBED_BACKEND=native rust/target/debug/cairn embed --build-index` then `CAIRN_EMBED_BACKEND=native rust/target/debug/cairn semantic "<concept>"`; pass = the embed summary and the semantic provenance both name the same `native/BAAI/bge-m3` stamp

## TC-005 — Feature-off build rejects native with a hint naming the cargo feature
- **Story**: US2 · **Traces to**: FR-003, AC2
- **Given** the default build (local-embed feature not compiled) and an initialized empty workspace — the empty-corpus edge must still hit backend resolution, not crash or silently proceed
- **When** the user sets `CAIRN_EMBED_BACKEND=native` and runs `cairn embed`
- **Then** the native backend resolves as unavailable: the command fails with an error whose hint names the `local-embed` cargo feature, no hash-embedder run happens (no hash model stamp in the output), and the failure is visible — never a silent degrade to hash
- **Pass condition**: `c="$(pwd)/rust/target/debug/cairn" && d="$(mktemp -d)" && cd "$d" && "$c" init >/dev/null 2>&1 && CAIRN_EMBED_BACKEND=native "$c" embed >embed.log 2>&1; s=$?; rg -qi "local-embed" embed.log && ! rg -q "hash-256-v1" embed.log && test "$s" -ne 0`

## TC-006 — Cold cache falls back to hash with a pre-warm hint, never blocks
- **Story**: US2 · **Traces to**: FR-005, AC1
- **Given** a feature build, `native` configured, an isolated empty model cache, and a minimal indexed workspace
- **When** the user runs `cairn embed` (no pre-warm)
- **Then** the run completes successfully via the hash embedder (exit 0, rows land under the hash stamp so indexing is not blocked by a download) and a visible degraded-mode hint names the pre-warm command `cairn embed --download-model`
- **Pass condition**: manual — run `CAIRN_EMBED_BACKEND=native rust/target/debug/cairn embed` with an isolated empty cache; pass = exit 0, output contains a degraded-mode hint naming `cairn embed --download-model`, and the summary/doctor name the hash model stamp for the written rows

## TC-007 — Doctor reports the native feature state on a default build
- **Story**: US3 · **Traces to**: FR-006, AC1
- **Given** the default build (feature not compiled), an initialized workspace, and `CAIRN_EMBED_BACKEND=native` set
- **When** the user runs `cairn doctor`
- **Then** the embedding health output reflects the native backend: it reports the feature as not compiled, and does not claim a cached model or any native embeddings
- **Pass condition**: `c="$(pwd)/rust/target/debug/cairn" && d="$(mktemp -d)" && cd "$d" && "$c" init >/dev/null 2>&1 && CAIRN_EMBED_BACKEND=native "$c" doctor >doc.log 2>&1; rg -qi "native" doc.log && rg -qi "not compiled|disabled|unavailable" doc.log`

## TC-008 — Doctor reports compiled feature, cache state, and native count on a feature build
- **Story**: US3 · **Traces to**: FR-006, AC1
- **Given** a feature build; once with the model cache pre-warmed and a natively-embedded workspace (TC-002 state), once with an isolated empty cache
- **When** the user runs `cairn doctor` in each state
- **Then** doctor distinguishes all three signals: feature compiled; model cache state matching reality (cached after pre-warm, not cached when empty); native embedding count matching the rows embedded under the native stamp (non-zero in the embedded workspace)
- **Pass condition**: manual — run `rust/target/debug/cairn doctor` in both states; pass = the native health line(s) report compiled + cached + a native count equal to the embedded rows in the first state, and compiled + not-cached + count 0 in the cold state

## TC-009 — Native rows coexist with hash rows; reaping is scoped per stamp
- **Story**: US1 · **Traces to**: FR-004, AC2
- **Given** a feature build, pre-warmed cache, and a workspace that already holds hash-stamped embedding rows
- **When** the user embeds natively, re-embeds natively a second time, then re-embeds under the hash backend
- **Then** native rows persist under `native/BAAI/bge-m3` without overwriting or colliding with `hash-256-v1` rows; each backend's ANN rows live under its own stamp; each re-embed reaps only its own stamp's stale rows (the other stamp's row count never drops)
- **Pass condition**: manual — record `rust/target/debug/cairn doctor` per-model counts after each step; pass = after the native embed both stamps coexist with stable hash counts, and after each re-embed only the re-embedded stamp's count changes

## TC-010 — Per-stamp persistence and ANN-table machinery (regression guard)
- **Story**: US1 · **Traces to**: FR-004
- **Given** the stamp-keyed persistence and per-model ANN table machinery at baseline (stamp-agnostic; the native stamp will exercise it)
- **When** the store's per-model table and corpus parity suites run
- **Then** model stamps keep scoping persistence keys, ANN table naming, and reaping — including sanitization of stamp characters — so a new native stamp slots in without collisions
- **Pass condition**: `cargo test -p cairn-store --lib vec0 && cargo test -p cairn-build --test embeddings_parity`

## TC-011 — Default dependency tree stays free of native-inference crates (standing guard)
- **Story**: US1 · **Traces to**: NFR-001
- **Given** the repository at any future commit, default feature set (no opt-in)
- **When** the default dependency graph is resolved
- **Then** no native-inference or model-hub dependency appears on the default path — the "binary ships no local inference" posture holds unless the feature is opted in; this standing guard fails if such a crate ever leaks into default builds
- **Pass condition**: `t="$(mktemp)" && cargo tree -e normal >"$t" && ! rg -qi "fastembed|hf-hub|hf_hub" "$t"`

## TC-012 — Dependency audits stay green; weights come only from HuggingFace
- **Story**: US1 · **Traces to**: NFR-001
- **Given** the change with its pinned model-inference dependencies behind the feature gate
- **When** the dependency audit gate runs for default builds (licenses and advisories), and the pre-warm download runs on a feature build
- **Then** the audit passes unchanged for default builds, and model weights are fetched only from huggingface.co via the pinned model-hub dependency — no other download source appears
- **Pass condition**: manual — the CI `cargo deny check` step is green for the default build (run `cargo deny check` locally where installed); the TC-001 run observes downloads originating from huggingface.co only

## TC-013 — Native inference makes no network calls after download (abuse case)
- **Story**: US2 · **Traces to**: NFR-002
- **Given** a feature build with the model already cached, and all network egress blocked at the OS level (an eavesdropper controls the network)
- **When** the user runs a native corpus embed and a native semantic query
- **Then** both complete successfully with zero network dependence — no connection errors, and an observer on the network captures no outbound traffic carrying workspace text or queries
- **Pass condition**: manual — with networking disabled (airplane mode / firewall deny-all), run `CAIRN_EMBED_BACKEND=native rust/target/debug/cairn embed --build-index` and `... cairn semantic "<concept>"`; pass = both exit 0 with results, no network errors, and a packet capture during the runs shows no outbound connections

## TC-014 — Batch flag, progress, and post-embed ANN rebuild preserved under native
- **Story**: US1 · **Traces to**: NFR-003
- **Given** a feature build, pre-warmed cache, and a workspace with enough symbols to fill several batches at a small batch size
- **When** the user runs `CAIRN_EMBED_BACKEND=native cairn embed --batch-size 8 --build-index`
- **Then** the batch-size flag is honored (progress advances batch by batch), progress reporting stays visible during embedding, and the ANN index is rebuilt afterwards (native-stamp ANN rows non-zero in doctor, rebuild reported)
- **Pass condition**: manual — run the command; pass = exit 0, batch progress lines observed during the run, and `rust/target/debug/cairn doctor` reports non-zero native-stamp ANN rows; the same commands are the standing verify at full-repo scale (outside the bounded proof's runtime cap)

## TC-015 — Embed keeps its batching flag on the default build
- **Story**: US1 · **Traces to**: NFR-003
- **Given** the default build
- **When** the user views the embed command's help
- **Then** the batch-size flag remains on the command surface — the native work does not disturb the existing embed flags
- **Pass condition**: `rust/target/debug/cairn embed --help 2>&1 | rg -q "batch-size"`

## TC-016 — Semantic queries racing a native embed complete and leave the store consistent
- **Story**: US2 · **Traces to**: NFR-004
- **Given** a feature build, pre-warmed cache, and a workspace with enough pending symbols that a native embed run stays in flight for a while
- **When** the user starts a native `cairn embed` and issues several `cairn semantic` queries against the same workspace while the embed is still running
- **Then** the embed run and every racing query complete without hanging or deadlocking, each query returns results, and the store stays consistent afterwards — no corrupt or partial rows (doctor healthy, native-stamp counts match the embed summary), and a subsequent semantic search still answers
- **Pass condition**: manual — start `CAIRN_EMBED_BACKEND=native rust/target/debug/cairn embed --build-index`, then run several `CAIRN_EMBED_BACKEND=native rust/target/debug/cairn semantic "<concept>"` while it is in flight; pass = the embed exits 0, every query returns results, `rust/target/debug/cairn doctor` reports a healthy store with native-stamp counts matching the embed summary, and one further semantic query returns results

## TC-017 — Feature-off build refuses the pre-warm command with a feature-naming error
- **Story**: US2 · **Traces to**: FR-002
- **Given** the default build (local-embed feature not compiled) and an initialized workspace with an empty model cache
- **When** the user runs the pre-warm command (`cairn embed --download-model`) with the native backend selected
- **Then** the command errors instead of downloading: the error names the `local-embed` cargo feature, and no model weights are fetched or cached (the isolated cache root stays empty)
- **Pass condition**: `c="$(pwd)/rust/target/debug/cairn" && d="$(mktemp -d)" && cd "$d" && "$c" init >/dev/null 2>&1 && CAIRN_EMBED_BACKEND=native "$c" embed --download-model >dl.log 2>&1; s=$?; rg -qi "local-embed" dl.log && test "$s" -ne 0`

## Coverage matrix
<!-- Every FR and applicable NFR appears; `check.py` fails a requirement with no TC. -->
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001 | TC-002, TC-003 | manual |
| FR-002 | TC-001, TC-017 | auto+manual |
| FR-003 | TC-005 | auto |
| FR-004 | TC-002, TC-009, TC-010 | auto+manual |
| FR-005 | TC-006 | manual |
| FR-006 | TC-007, TC-008 | auto+manual |
| FR-007 | TC-003, TC-004 | manual |
| NFR-001 | TC-011, TC-012 | auto+manual |
| NFR-002 | TC-013 | manual |
| NFR-003 | TC-014, TC-015 | auto+manual |
| NFR-004 | TC-016 | manual |
| NFR-005 | — not applicable (doctor contract, FR-006) | — |
| NFR-006 | — not applicable (CLI text output only) | — |
