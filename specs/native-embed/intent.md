# Intent: native-embed

**Status**: validated     <!-- draft while grilling → validated once
                                 `check.py --intent-only` exits 0 (the
                                 handoff gate below) -->
**Tier**: standard         <!-- the effort guess spec.md inherits:
                                 tiny | standard | large (SKILL.md §
                                 Effort scaling) -->
**Created**: 2026-10-29
**Baseline**: 0.24.2 @ 210e2d18
<!-- survey.md's baseline shape — version @ HEAD sha of the repo this
     intent was scouted against; the staleness diagnosis reads it
     unchanged, so it stays at the scouted HEAD -->

The intake artifact — WHAT the user wants and WHY, in their words,
before any docset file exists. spec-shape authors spec.md from here;
nothing in `specs/<name>/` beyond this file is intent's to write.

## Problem
"Before the changes, cairn download from hf, how to bring that back?"
The pre-migration Python engine offered `cairn embed --install-deps` /
`--download-model`: it installed sentence-transformers, pulled
`BAAI/bge-m3` weights from HuggingFace, and embedded in-process. The
Rust engine deleted that path (63e37629 removed the Python engine;
99d32fa9 removed even the retirement stubs) — real vectors today
require running a separate OpenAI-compatible embeddings server
(`omlx`/`ollama`/`server`). The user wants the one-command,
no-external-server flow back, implemented in Rust.

## Audience
- The offline laptop user with no ollama/infinity server running: one
  command should produce real bge-m3 vectors; today they get either
  the dep-free hash embedder (degraded retrieval, doctor warning) or
  must stand up a server first.
- The contributor evaluating cairn: semantic search should demo
  well without infrastructure, without forcing a multi-GB download on
  every default install.

## Constraints
Scout-verified facts (baseline 0.24.2 @ 210e2d18):
- The recorded architecture decision: "the binary ships no local
  inference" (README:181-188); default backend is the hash embedder.
- Backend resolution is one choke point: `EffectiveBackend {Hash,
  Server, Unavailable}` and `resolve_backend` in
  `rust/crates/cairn-query/src/tools_search.rs:310,386`; `local` and
  `hash` both resolve to Hash.
- Two transports dispatch on the enum: query-side `embed_query`
  (tools_search.rs:781) and corpus-side `Transport` in
  `rust/crates/cairn-build/src/embeddings.rs:33-94` (`embed_all`
  resolves `semantic_env` itself — both arms must stamp identically
  or corpus and query vectors never match).
- Model stamps scope rows and vec0 ANN tables
  (`server_model_stamp`, tools_search.rs:292): hash = `hash-256-v1`
  (256-dim), server = `server/<netloc>/<model>`; bge-m3 is 1024-dim
  and the mixed-dimension batch guard already exists (tools_search.rs:770).
- ANN (sqlite-vec) is statically linked and on by default;
  `--build-index` rebuilds per stamp.
- Dep policy: workspace pins exact versions; CI runs cargo-deny
  (`rust/deny.toml`) — an ONNX/runtime dep tree must clear it.
- Candidate crate: `fastembed` (ONNX CPU, hf-hub downloads into the
  standard HF cache — same flow as the old sentence-transformers
  path); pin must be verified to ship a BAAI/bge-m3 model card.

## Questions
<!-- The grilling frontier (references/grilling.md): every open
     question opens as a NEEDS CLARIFICATION marker (the grilling
     vocabulary — the checker's marker scan counts brackets, so write
     markers only on live question lines, never in comments) and
     closes exactly one way — answered (folded into the sections
     above), deferred (Scope out), or a named assumption. The pass
     ends when the frontier is empty: no marker survives. Closures
     stay listed here so spec-shape inherits the reasoning. -->
- Q1 build posture — answered: opt-in cargo feature (`local-embed`);
  default builds unchanged, the recorded "no local inference"
  decision holds for them; the heavy ort/ONNX tree is opt-in.
- Q2 backend name — answered: `CAIRN_EMBED_BACKEND=native`. New name,
  new semantics; `local` keeps meaning hash.
- Q3 model fetch — answered: explicit `cairn embed --download-model`
  pre-warm only; no auto-download on cold cache.
- Q4 offline behavior — answered: when native is configured but the
  model is absent, fall back to the hash embedder with a visible
  degraded-mode hint naming `--download-model` (mirrors today's
  `local` fallback UX; embedding never hard-fails offline).
- Q5 model set — answered: fixed `BAAI/bge-m3` only; no
  env-selectable catalog.


## FR seeds
<!-- EARS-shaped seed requirements — one verb, one system, testable;
     spec.md grows these into the full FR/NFR set -->
- **FR-001**: WHERE the build includes native embedding, the system
  shall embed corpora and queries in-process with BAAI/bge-m3 vectors
  when `CAIRN_EMBED_BACKEND` selects the native backend.
- **FR-002**: WHEN the user runs the model pre-warm command, the
  system shall download the model weights into the standard
  HuggingFace cache and exit without embedding.
- **FR-003**: WHERE the build omits native embedding, the system
  shall resolve the native backend as unavailable with a hint naming
  the cargo feature.
- **FR-004**: The system shall stamp native embedding rows distinctly
  from hash and server stamps, scoping vec0 tables and reaping.
- **FR-005**: WHEN the build includes native embedding and the
  backend is configured native but the model is not cached, the
  system shall fall back to the hash embedder and surface a
  degraded-mode hint naming the pre-warm command (embedding never
  hard-fails offline).
- **FR-006**: `cairn doctor` shall report native backend health
  (model present, embedding count) rather than only the hash
  fallback.

## Handoff
Next: `/spec-gen native-embed` — the docset (spec.md onward) is authored
there, never here. The Baseline above is what shape's survey
re-baselines against.
