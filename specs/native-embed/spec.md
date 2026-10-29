# Spec: native-embed

**Status**: draft          <!-- draft while writing → approved at the approve gate
                                 (explicit user sign-off — a human gate, never
                                 auto-satisfied) → active once the first task spawns
                                 → done when all tasks are ticked and `check.py`
                                 re-runs green -->
**Effort**: standard       <!-- standard = one merged design spawn authors
                                 plan/tech-spec/task, qa separate; large = the full
                                 plan ∥ tech ∥ qa wave + a separate tasks wave.
                                 Gates are identical at every effort. Bump to
                                 large for multi-area, auth, persistence,
                                 migration, or research-heavy work. -->
**Created**: 2026-10-29
**Branch**: `feat/native-embed`

## What
Cairn regains one-command local semantic embedding: behind an opt-in
cargo feature, `CAIRN_EMBED_BACKEND=native` embeds corpora and queries
in-process with BAAI/bge-m3 weights downloaded from HuggingFace via a
restored `cairn embed --download-model` pre-warm — no external
embeddings server, no Python.

## Why
The Rust migration deleted the Python engine's local-inference path
(`--install-deps`/`--download-model` with sentence-transformers) and
with it the only dependency-free way to get real vectors: today real
embeddings require standing up an OpenAI-compatible server first. The
default hash embedder degrades retrieval (doctor warns), so offline
and try-out users get a mid-band experience with no local upgrade
path inside the binary.

## Business value
- Offline/try-out users reach real bge-m3 retrieval with two commands
  (`--download-model`, then `embed`) and zero infrastructure.
- Default builds keep the recorded "binary ships no local inference"
  posture — no size, license, or dependency-tree cost unless opted in.
- Success measures: native embed produces 1024-dim vectors end to end
  (embed → semantic_search); feature-off builds reject `native` with
  an actionable hint; doctor reports native health.

## User stories
<!-- Ordered by priority; each independently demoable. -->
### US1 — One-command real vectors (P1)
As a cairn user on a machine with no embeddings server, I want
`CAIRN_EMBED_BACKEND=native cairn embed --download-model` followed by
`cairn embed --build-index` to produce real bge-m3 vectors, so that
semantic search works at full quality without infrastructure.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given a feature-enabled build and a cold cache, When the user
  runs the pre-warm command, Then model weights land in the
  fastembed-managed model cache and the command exits 0 without
  embedding (FR-002).
- AC2: Given the pre-warmed cache and `CAIRN_EMBED_BACKEND=native`,
  When the user runs `cairn embed`, Then symbols are embedded with
  1024-dim vectors stamped distinctly from hash and server rows
  (FR-001, FR-004).
- AC3: Given native embeddings in the store, When the user runs
  `cairn semantic "<concept>"`, Then results come from native vectors,
  not a hash fallback (FR-001, FR-007).

### US2 — Never hard-fails offline (P1)
As an offline user with a cold cache, I want `cairn embed` under
`native` to proceed with the hash embedder and tell me how to get
real vectors, so that indexing is never blocked by a download.

**Acceptance criteria**:
- AC1: Given a feature-enabled build, `native` configured, and no
  cached model, When the user embeds, Then the run completes via the
  hash embedder with a visible degraded-mode hint naming the pre-warm
  command (FR-005).
- AC2: Given a feature-off build, When `CAIRN_EMBED_BACKEND=native`
  is set, Then the backend resolves unavailable with a hint naming
  the cargo feature (FR-003).
- AC3: Given a feature-off build, When the user runs the pre-warm
  flag, Then the command errors naming the cargo feature and
  downloads nothing (FR-002).

### US3 — Doctor sees native (P2)
As a user diagnosing retrieval quality, I want `cairn doctor` to
report the native backend's state, so that I can tell feature,
model, and index health apart.

**Acceptance criteria**:
- AC1: Given any build, When the user runs `cairn doctor`, Then the
  embedding line reflects the native feature (compiled or not), model
  cache state, and native embedding count (FR-006).

## Requirements
<!-- EARS-shaped SHALL statements. Standing requirements use the
     ubiquitous pattern ("The system shall X"); the rest use WHEN / IF …
     THEN / WHERE patterns. One verb, one system, testable. -->
- **FR-001**: WHERE the build includes the `local-embed` feature, the system shall embed corpus symbols and natural-language queries
  in-process with BAAI/bge-m3 when `CAIRN_EMBED_BACKEND=native`.
- **FR-002**: WHERE the build includes the `local-embed` feature, WHEN the user runs `cairn embed --download-model`, the system shall download
  the BAAI/bge-m3 weights into the fastembed-managed model cache
  (FASTEMBED_CACHE_DIR / HF_HOME-resolved) and exit without embedding;
  WHERE the build omits the feature, the system shall error naming the
  cargo feature instead of downloading.
- **FR-003**: WHERE the build omits the `local-embed` feature, the system shall resolve `CAIRN_EMBED_BACKEND=native` as unavailable
  with a hint naming the cargo feature.
- **FR-004**: The system shall stamp native embedding rows
  `native/BAAI/bge-m3`, distinct from `hash-256-v1` and `server/*`
  stamps, scoping persistence, vec0 ANN tables, and reaping.
- **FR-005**: WHEN the build includes the feature, `native` is configured, and the model is not cached, the system shall fall back
  to the hash embedder and surface a degraded-mode hint naming the
  pre-warm command.
- **FR-006**: `cairn doctor` and the daemon healthz endpoint shall
  report native backend health: feature compiled, model cache state,
  and native embedding count (a native-configured cold-cache fallback
  counts as a degradation).
- **FR-007**: The system shall resolve the same effective backend and
  model stamp on the corpus and query legs for one configuration, so
  native vectors are written and searched under one stamp.

## Quality attributes
<!-- Triage every family. An applicable NFR is EARS-shaped and traces to a
     task and TC like an FR. An inapplicable NFR must say why. -->
- **NFR-001**: Security — applicable: The system shall source model
  weights only from HuggingFace via the pinned hf-hub dependency of
  the pinned fastembed crate, with the dependency tree passing
  `cargo-deny` (licenses/advisories) unchanged for default builds.
- **NFR-002**: Privacy — applicable: The system shall send embedded
  text nowhere but the local process; native inference shall make no
  network calls after model download.
- **NFR-003**: Performance — applicable: Corpus embedding shall
  preserve the existing batch-size flag, progress reporting, and
  post-embed ANN rebuild behavior under the native transport.
- **NFR-004**: Reliability — applicable: Concurrent embedding calls shall serialize on one lazily-initialized native model instance
  without deadlock or repeated model loads.
- **NFR-005**: Observability — not applicable: covered by FR-006's
  doctor/healthz contract; no signal surface beyond it is introduced.
- **NFR-006**: Accessibility — not applicable: no new interactive or
  graphical surface; CLI text output only.

## Scope
**In**: `EffectiveBackend`/`resolve_backend` native arm (cairn-query);
the native transport on both legs (query `embed_query`, corpus
`cairn-build` `Transport`); `EmbedArgs::download_model` + hint copy;
doctor/healthz native reporting; feature-gated Cargo wiring; tests
(feature-off resolution table, feature-on dimension/stamp/fallback
integration); docs (README, cli-reference).
**Out (deferred)**: any cross-encoder reranker; models beyond
BAAI/bge-m3 (env-selectable catalog); changes to server backends or
their presets; daemon/MCP configuration changes; GPU/accelerated
runtimes; `CAIRN_EMBED_TRUST_REMOTE_CODE`-style knobs.

## Assumptions & risks
- Assumption: the pinned fastembed release ships a BAAI/bge-m3 dense
  model card with a stable model id (research RQ1 verifies; fallback
  is candle, at spec cost).
- Assumption: ONNX CPU throughput is adequate for corpus-scale
  embedding on target machines (bench in RQ2 scope).
- Risk: the ort/onnxruntime native dependency inflates artifacts or
  trips cargo-deny — mitigation: the feature gate keeps default
  builds byte-identical in dependency terms; deny.toml ruling is a
  research deliverable, and a deny failure downgrades to a documented
  build-from-source path before it blocks the spec.
- Risk: fastembed model updates drift dimensions (1024) or ids —
  mitigation: pin the exact crate version and assert the model card's
  dimensions in a feature-on test.

## Research questions (Stage 0)
- RQ1: Which fastembed release ships a BAAI/bge-m3 dense model, and
  what are its exact model id, embedding dimension, and
  initialization API surface?
- RQ2: What dependency tree (ort/onnxruntime, hf-hub and transitive)
  does that fastembed release pull, do its licenses clear
  `rust/deny.toml`, and what are the cache-dir/HF_HOME/offline
  semantics of its download path?
- RQ3: What are fastembed's initialization cost and concurrency
  characteristics — is a process-wide lazy singleton with mutex
  serialization sound (Send/Sync, re-entrancy), and does it expose a
  pre-warm/inference API separation usable by `--download-model`?
