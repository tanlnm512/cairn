# Survey: native-embed

**Created**: 2026-10-29 | **Baseline**: 0.24.2 @ 210e2d18
The survey node's output — the single source of truth for code state. Every citation
in the other five docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

Note on tooling: the repo's `cairn` code-intelligence CLI returned
`No definition found for 'resolve_backend'` / `No symbols matching 'embed*'`
for this session's store, so all evidence below comes from this session's own
`rg`/read output (session-rule compliant either way).

## Items

```
item FR-001: "in-process BAAI/bge-m3 embedding when CAIRN_EMBED_BACKEND=native behind a local-embed feature"
  evidence:   rust/crates/cairn-query/src/tools_search.rs:385-402 `pub fn resolve_backend(name: &str)` arms:
             `"hash" | "local" => (EffectiveBackend::Hash, None)` / `"server" | "omlx" | "ollama" => {...}`
             and `// Unknown names resolve to local semantics.` `_ => (EffectiveBackend::Hash, None)`
             — `native` today silently resolves to Hash. No `fastembed`/`hf-hub`/`hf_hub` match in
             Cargo.toml or any rust/crates/*/Cargo.toml (rg this session, 0 matches). No
             `[features]` section exists in any crate manifest (rg '^\[features\]' — 0 matches).
  status:     TODO
  verify:     rg -n 'fastembed|hf-hub|hf_hub' Cargo.toml rust/crates/*/Cargo.toml → 0 matches (pass:
              absence proven); cargo test -p cairn-query --lib tools_search::tests → 29 passed, incl.
              unknown_backend_names_resolve_to_local_semantics
  gap:        no native transport, no fastembed/hf-hub dependency, no local-embed cargo feature,
              no `native` arm in resolve_backend

item FR-002: "cairn embed --download-model downloads BAAI/bge-m3 into the HF cache and exits"
  evidence:   rust/crates/cairn-cli/src/cmds_core.rs:3953-3973 EmbedArgs has only db/batch-size/limit/
             no-reap/build-index/no-multivector. Removed in git 99d32fa9 ("refactor(cli): remove the
             retired command and flag stubs", 2026-10-28): diff shows
             `-    if args.download_model {` / `-    return retired_embed_flag("--download-model");`
             and `-    #[arg(long = "download-model")]` / `-    pub download_model: bool,`.
             Live run this session: `rust/target/debug/cairn embed --download-model` →
             `error: unexpected argument '--download-model' found`. CHANGELOG.md:41 records the flag
             among "Retired stubs ... (now unknown-argument errors)". Remaining `--download-model`
             hits are only pinned historical fixtures (rust/golden/stores/self/corpus/src/cairn/cli/embed.py).
  status:     TODO
  verify:     rust/target/debug/cairn embed --download-model → "error: unexpected argument
              '--download-model' found" (red, as expected for absence)
  gap:        flag, HF-cache download path, and hf-hub dependency all absent

item FR-003: "feature-omitted build resolves native as unavailable with a hint naming the cargo feature"
  evidence:   tools_search.rs:400-401 `// Unknown names resolve to local semantics.` /
             `_ => (EffectiveBackend::Hash, None)` — `native` resolves to Hash, NOT Unavailable.
             tools_search.rs:807-814 install_hint(): "No embedding backend is serving real vectors. \
              Set CAIRN_EMBED_BACKEND=omlx or ollama (or server + CAIRN_EMBED_BASE_URL) ... \
              Or set CAIRN_EMBED_BACKEND=hash for a dep-free smoke test." — names no cargo feature.
             cmds_core.rs:2145-2153 exit_backend_unavailable: `display::error("Semantic dependencies
             unavailable")` + `display::dim(&tools_search::install_hint())` for non-server names.
  status:     PARTIAL
  verify:     cargo test -p cairn-query --lib tools_search::tests → 29 passed (incl.
              unknown_backend_names_resolve_to_local_semantics and install_hint_names_only_shipped_backends)
  gap:        no `native` match arm (unknown names go to Hash today); no cfg(feature=...)-gated
              resolution; install_hint/exit copy names no cargo feature

item FR-004: "native rows stamped native/BAAI/bge-m3, distinct from hash-256-v1 and server/*, scoping persistence, vec0 tables, reaping"
  evidence:   tools_search.rs:222 `pub const HASH_MODEL: &str = "hash-256-v1";`;
             tools_search.rs:297 `Ok(format!("server/{}/{}", netloc_of(&base), server_model()))`
             (server_model_stamp); tools_search.rs:409-413 semantic_env model per backend.
             cairn-store/src/vec0.rs:25-37 table_name sanitizes `/`→`_` (prefix vec_/vecmv_);
             vec0.rs:234-237 test asserts `table_name("BAAI/bge-m3", ...) == "vec_BAAI_bge_m3"`.
             Persistence keyed (symbol_id, model): embeddings.rs:448-454 upsert
             `ON CONFLICT(symbol_id, model) DO UPDATE`. Reaping is per-model: embeddings.rs:998-1023
             `doomed: HashMap<String, Vec<i64>>` keyed by model → `vec0::delete_index_rows`;
             embeddings.rs:1028 reap_orphaned_embeddings.
  status:     PARTIAL
  verify:     cargo test -p cairn-store --lib vec0 → 5 passed (incl. sanitizes_model_names);
              cargo test -p cairn-build --test embeddings_parity → 3 passed
  gap:        machinery is stamp-agnostic and complete, but no native/* stamp exists to exercise it

item FR-005: "model not cached → hash fallback with degraded-mode hint naming the pre-warm command"
  evidence:   tools_search.rs:414 `let hash_fallback = backend == EffectiveBackend::Hash && name == "local";`
             — predicate is keyed on name=="local" only. cmds_core.rs:2316-2329 embed() warning:
             "Using the hash embedder (dep-free): token-overlap vectors only, so retrieval is \
              degraded." + dim lines naming CAIRN_EMBED_BACKEND=omlx|ollama|server (no pre-warm
             command). tools_search.rs:1202-1204 `vectors_carry_token_overlap_only(env)` gates
             rerank confidence on hash vectors.
  status:     PARTIAL
  verify:     cargo test -p cairn-query --lib tools_search::tests → 29 passed
  gap:        no model-cache detection; hash_fallback predicate does not cover a `native` name;
              degraded hint names server setup, not a pre-warm/download command

item FR-006: "cairn doctor reports native backend health: feature compiled, model cache state, native embedding count"
  evidence:   cmds_doctor.rs:151 `let mut results = vec![check_schema(&conn), check_embeddings(), check_ann(&conn)];`
             `results.extend(check_embed_server(&conn));` — the full check set. check_embeddings
             (248-264) reports only `backend: {configured}` or the hash WARN; check_ann (268-339)
             reports per-model vec0 counts; check_embed_server (343+) probes the server family.
             rg -ni 'native|feature|cache' cmds_doctor.rs → 0 matches (this session).
             healthz.rs:57-67 degradation_count counts `backend_name() == "local"` and
             `!ann_backend_enabled()` only.
  status:     TODO
  verify:     rg -ni 'native|feature|cache' rust/crates/cairn-cli/src/cmds_doctor.rs → 0 matches
              (pass: absence proven)
  gap:        no native-backend check, no compiled-feature report, no model-cache-state probe,
              no native-stamp embedding count

item FR-007: "corpus and query legs resolve the same effective backend and model stamp for one configuration"
  evidence:   both legs route through one resolver: corpus leg embeddings.rs:39-44
             `fn resolve_embed_env() -> Result<EmbedEnv, String> { let env = semantic_env(None); ...}`;
             embed_all (embeddings.rs:429) and embed_memory_concepts (embeddings.rs:851) call it.
             Query leg tools_search.rs:1124-1132 dense_retrieve uses `embed_query(env, ...)` and
             `ann_query(conn, &env.model, ...)` from the same SemanticEnv; memory query leg
             tools_km.rs:2555 `embed_query(&env, query)`. BUT the CLI embed handler resolves its own
             env (cmds_core.rs:2312 `let env = tools_search::semantic_env(None);`) separate from
             embed_all's internal resolve (embeddings.rs:429) — two semantic_env calls per process.
  status:     PARTIAL
  verify:     cargo test -p cairn-build --test embeddings_parity → 3 passed (golden hash corpus
              round-trips under one stamp); cargo test -p cairn-query --lib tools_search::tests →
              29 passed
  gap:        agreement holds by shared-construction today; no test asserts corpus-leg ==
              query-leg stamp, and the embed CLI path takes two independent semantic_env resolutions

item NFR-001: "weights only from HuggingFace via pinned hf-hub of pinned fastembed; cargo-deny unchanged for default builds"
  evidence:   pin policy, cairn-query/Cargo.toml:12-19: `pdf-extract = "=0.12.1"`, `quick-xml = "=0.42.0"`,
             `regex = "1"`, `rusqlite = { version = "=0.40.2", features = ["bundled"] }`, `sha2 = "=0.10.9"`
             — exact pins for most deps, major-only for regex/serde_json. rust/deny.toml:12-24 licenses
             allow = ["Apache-2.0", "Apache-2.0 WITH LLVM-exception", "BSD-2-Clause", "BSD-3-Clause",
             "CC0-1.0", "ISC", "MIT", "Unicode-3.0", "Zlib"]; advisories `yanked = "deny"`.
             CI gate .github/workflows/rust.yml:105 `- name: cargo deny check`. No fastembed/hf-hub
             anywhere in live code (rg, 0 matches outside rust/golden fixtures).
  status:     TODO
  verify:     rg -n 'fastembed|hf-hub|hf_hub' Cargo.toml rust/crates/*/Cargo.toml → 0 matches (pass);
              `cargo deny check` NOT runnable locally — `error: no such command: deny` — CI-only gate:
              unknown — verify (pass state at HEAD unverified this session)
  gap:        neither fastembed nor hf-hub is a dependency; deny pass-state with them added is unknown

item NFR-002: "embedded text never leaves the local process; no network after model download"
  evidence:   today's only remote transport is the server leg: tools_search.rs:735-738
             `"embedding server unreachable after 3 retries"` (HTTP to the configured base URL);
             the hash leg is pure in-process sha256 (tools_search.rs:439-475 hash_vec;
             embeddings.rs:79-88 Transport::Hash maps texts via hash_vec, no I/O).
             embeddings.rs (cairn-build) imports no HTTP machinery at all.
  status:     TODO
  verify:     rg -n 'fastembed|hf-hub' Cargo.toml rust/crates/*/Cargo.toml → 0 matches (pass:
              no native transport exists whose network behavior would need bounding)
  gap:        no native inference path exists; its no-network-after-download property is unproven
              until the transport exists

item NFR-003: "batch-size flag, progress reporting, and post-embed ANN rebuild preserved under the native transport"
  evidence:   cmds_core.rs:3958-3960 `/// Embedding batch size.` `#[arg(long = "batch-size",
             default_value_t = 64)] pub batch_size: usize`; embeddings.rs:441
             `for batch in stale_rows.chunks(batch_size)`; progress cmds_core.rs:2349
             `crate::cmds_lifecycle::plain_progress_draw("Embedding", 0, 0, 0.0, "symbols")`;
             post-embed ANN rebuild cmds_core.rs:2361-2395 `vec0::rebuild_index(&conn, &env.model, ...)`
             for VecSource::Embeddings and VecSource::EmbeddingsMv. embed_texts dispatch
             (embeddings.rs:77-96) is transport-agnostic — batching/rebuild wrap it.
  status:     PARTIAL
  verify:     cargo test -p cairn-build --test embeddings_parity → 3 passed
  gap:        machinery exists and is transport-agnostic, but has never run under any native
              transport (none exists); native-model latency vs progress cadence untested

item NFR-004: "concurrent embedding serializes on one lazily-initialized native model instance"
  evidence:   no native model instance exists anywhere (rg fastembed → 0 matches). Lazy-init
             precedents in-tree: cairn-query/src/ingest.rs:313 `static RES: OnceLock<ParserRegexes>
             = OnceLock::new();`; cairn-daemon/src/conn_pool.rs:42 `idle: Mutex<Vec<Parked>>`.
  status:     TODO
  verify:     rg -n 'fastembed|hf-hub' Cargo.toml rust/crates/*/Cargo.toml → 0 matches (pass:
              nothing to serialize today)
  gap:        no model handle, no lazy-init point, no concurrency story for a native embedder

item NFR-005: "observability — not applicable: covered by FR-006's doctor contract"
  evidence:   N/A premise holds at baseline: the only health surfaces are doctor
             (cmds_doctor.rs:151 check set) and daemon /healthz (healthz.rs:32-46 bounded payload);
              no additional signal surface would be displaced.
  status:     DONE
  verify:     rg -n 'check_' rust/crates/cairn-cli/src/cmds_doctor.rs | head → the four check
              functions (schema/embeddings/ann/embed_server) — confirmed this session
  gap:        none (baseline premise verified; native additions accrue to doctor per FR-006)

item NFR-006: "accessibility — not applicable: CLI text output only, no new interactive surface"
  evidence:   the embed surface is clap flags only: cmds_core.rs:3953-3973 EmbedArgs (six long
             flags, no positional/interactive args); output via display::info/warning/dim and
             rail::summary_panel (cmds_core.rs:2317-2434).
  status:     DONE
  verify:     read of cmds_core.rs:3953-3973 this session (EmbedArgs field list above)
  gap:        none (baseline premise verified)
```

## Supporting evidence

Machinery later docs will lean on (all verbatim from this session):

- **EffectiveBackend enum** — tools_search.rs:308-314:
  `/// Effective embedding backend after availability probing and fallback.`
  `pub enum EffectiveBackend { Hash, Server, Unavailable, }`
- **resolve_backend arms** — tools_search.rs:386-402: `"hash" | "local" => (EffectiveBackend::Hash, None)`;
  server family probes via `probe_server(&base, &server_model(), api_key.as_deref())` →
  `(EffectiveBackend::Server, Some(base))` else `Unavailable`; `_ => (EffectiveBackend::Hash, None)`.
- **Query-leg dispatch** — tools_search.rs:781-804 embed_query matches all three backends
  (Hash → hash_vec; Server → server_embed; Unavailable → Err("embedding backend unavailable")).
- **Corpus-leg dispatch** — embeddings.rs:23-36 `enum Transport { Hash, Server { base, model,
  api_key } }` / `struct EmbedEnv { transport, model }` (model = "Row stamp scoping staleness
  and vec0 tables (`current_model()`)"); resolve_embed_env (39-74) delegates to
  `cairn_query::tools_search::semantic_env(None)` — cairn-build has no independent resolver.
  embed_texts (77-96) dispatches per Transport.
- **Stamps** — HASH_MODEL "hash-256-v1" (tools_search.rs:222); DEFAULT_DIM 256 (:225);
  server_model_stamp `server/{netloc}/{model}` (:292-297), overridable by
  CAIRN_EMBED_MODEL_STAMP; CAIRN_EMBED_SERVER_MODEL default "bge-m3" (:287-289).
- **vec0 naming** — vec0.rs:25-37 table_name (`[a-zA-Z0-9_]` else `_`, prefix vec_/vecmv_);
  rebuild_index (112-161) drops+recreates `vec0(embedding float[{dim}] distance_metric=cosine)`
  and re-inserts per model; sync_index_row (166-185) delete+reinsert; delete_index_rows
  (190-209) chunked IN-lists of 500.
- **Mixed-dimension guard** — tools_search.rs:766-773 (server_embed):
  `return Err(format!("mixed-dimension batch: expected {dim}-dim vectors, got {}", vec.len()))`.
- **CLI embed handler** — cmds_core.rs:2311-2460: semantic_env → unavailable exit / hash_fallback
  warning → embed_all(batch_size, limit, !no_reap, multivector) → optional vec0 rebuild for both
  sources → summary panel (embedded/skipped/reaped/failed batches/model/vectors/mv vectors).
- **Hash-fallback warning copy** — cmds_core.rs:2317-2329 (quoted in FR-005) — note it is
  displayed only when `env.hash_fallback` (name == "local"), so an unknown name like `native`
  resolves to Hash with **no warning** and `semantic` (not `semantic (hash backend)`) provenance
  (tools_search.rs:1100-1114).
- **install_hint / empty_index_hint** — tools_search.rs:807-823 (quoted in FR-003);
  empty_index_hint tells the user to "Run `cairn embed` once".
- **Doctor** — cmds_doctor.rs:151 check set; SERVER_FAMILY `["server", "omlx", "ollama"]`
  (cmds_doctor.rs:28); ann WARN copies say "run `cairn embed` to (re)build the ANN index".
- **healthz** — healthz.rs:37-46 payload (status/store_reachable/read_only/degradations/db/
  uptime_seconds/impl); degradation_count (57-67) counts local-hash and ANN-off only.
- **Workspace/pins** — root Cargo.toml: workspace members (7 crates + vendored tree-sitter-dart),
  `[workspace.package] version = "0.24.2"`, `[patch.crates-io] sqlite-vec = { path =
  "rust/vendor/sqlite-vec" }` (D-010 note), `[profile.release] strip = true`; **no workspace
  [features] anywhere**. deny.toml licenses allow-list + advisories `yanked = "deny"` with one
  pinned RUSTSEC ignore (pdf-extract transitive).
- **Tests** — backend-resolution units: rust/crates/cairn-query/src/tools_search/tests.rs
  (resolve_backend, install_hint, embed_query, probe_server, server_embed retry/backoff —
  `29 passed` this session). Corpus side: rust/crates/cairn-build/tests/embeddings_parity.rs
  (`3 passed`; includes embed_symbols_resyncs_the_vec0_row_in_place). CLI tests dir:
  rust/crates/cairn-cli/tests/{dashboard_parity,impact_guard,init_hooks}.rs + README.md.
  Golden embed corpus: rust/golden/embed/{corpus/{alpha,beta,gamma}.py, embed-corpus.kg,
  embed-golden.json} — golden head: `"model": "hash-256-v1"`, vec_counts
  `vec_hash_256_v1: 11`, `vecmv_hash_256_v1: 18`.
- **Verify commands run this session (all pass)**:
  `cargo test -p cairn-query --lib tools_search::tests` → 29 passed, 0 failed;
  `cargo test -p cairn-store --lib vec0` → 5 passed, 0 failed;
  `cargo test -p cairn-build --test embeddings_parity` → 3 passed, 0 failed.
- **Baseline** — `git rev-parse --short HEAD` → `210e2d18` ("Merge pull request #206 from
  tanlnm512/chore/cargo-only-install"); workspace version `0.24.2` (root Cargo.toml:15).

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
