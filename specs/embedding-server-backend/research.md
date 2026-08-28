# Research: embedding-server-backend

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-27

Researcher-gate note: the Stage-1 researcher spawn was **skipped on purpose**
— all technical questions below were already resolved and live-verified in the
orchestrator's session (2026-08-27) before this pipeline started. Zero open
questions remained at Stage 0. This file records the findings and their
sources so tech-spec.md can cite them; measurements marked "live" ran on this
machine (Mac Studio, oMLX 0.6.2, sentence-transformers 5.6.1 + torch 2.13).

## Questions

### Do local inference servers expose OpenAI-compatible embeddings, and are vectors numerically compatible with sentence-transformers?
- **source**: live measurement 2026-08-27 (this machine) · **claim**: ST
  `BAAI/bge-m3` vs oMLX `/v1/embeddings` on identical weights: per-text
  cosine 1.000000 (min/mean) on 8 symbol chunks + 1 query; top-3 retrieval
  order identical; >512-token inputs diverge to 0.991 (truncation only);
  oMLX vectors unit-norm, 1024-dim · **relevance**: FR-004/FR-005 parity
  gates, D-007 threshold · **confidence**: high
- **source**: live measurement 2026-08-27 · **claim**: warm latency 244
  ms/query (oMLX incl. HTTP) vs 137 ms (ST in-process); batch-64 0.42 s vs
  0.38 s; server-side lazy model load costs seconds on first call ·
  **relevance**: FR-006 warmup, D-006 urllib choice · **confidence**: high

### Which servers to target, and does one client cover them?
- **source**: https://github.com/jundot/omlx + https://omlx.ai/ · **claim**:
  oMLX exposes `POST /v1/embeddings` (+ `/v1/rerank`), auto-discovers models
  from `~/.omlx/models`, Apache-2.0, macOS 15+/Apple Silicon · **relevance**:
  FR-001 presets · **confidence**: high (verified live)
- **source**: https://ollama.com/library/bge-m3 · **claim**: Ollama ships
  official `bge-m3` (1024-dim) and an OpenAI-compatible `/v1/embeddings` at
  `http://localhost:11434/v1` · **relevance**: FR-001 ollama preset ·
  **confidence**: high (docs; not live-tested here — Ollama not installed)
- **source**: https://github.com/waybarrios/vllm-mlx · **claim**: LM Studio
  (`:1234/v1`), llama.cpp server, vLLM/vllm-mlx expose the same
  `/v1/embeddings` request/response shape · **relevance**: generic-client
  design (D-001 family) · **confidence**: med (docs only)
- **source**: live probes 2026-08-27 · **claim**: oMLX error shapes are
  OpenAI-standard — unknown model → `not_found_error` whose message lists
  available ids; missing auth → `authentication_error` · **relevance**:
  FR-002 probe, FR-003 verbatim-error propagation · **confidence**: high

### oMLX embedding-model requirements?
- **source**: oMLX server error + source log, live 2026-08-27 · **claim**:
  embedding engine loads safetensors only; the `BAAI/bge-m3` HF repo ships
  only `pytorch_model.bin`, so a one-time torch→safetensors conversion is
  required (procedure in spec appendix C); served id = model-dir basename
  (`bge-m3`); auth via `~/.omlx/settings.json` `auth.api_key` ·
  **relevance**: FR-008 docs, FR-004 stamp naming · **confidence**: high

## Options summary

### Backend shape
- extend the existing `openai` backend with a base-url env — smallest diff,
  but couples local-server semantics (no key, presets) onto a cloud backend
- new `server` backend + presets, `openai` untouched — zero regression
  surface, one more dispatch branch

### Model-stamp identity
- bare model id — switches producers silently under one stamp
- `server/{netloc}/{model}` derived stamp + explicit alias — re-embeds on
  producer change by default; alias is the verified escape hatch

### HTTP client
- stdlib urllib — no new dependency, ~100 ms/query measured overhead
- httpx/requests — keep-alive and pooling, new dependency for marginal gain
  at 1-2 embed_query calls per search

### Degradation on failure
- silent model substitution — fastest, mixes vector spaces (worst failure
  mode)
- parity-gated session fallback ladder ending in BM25/FTS5 hybrid — costs a
  ~16-chunk parity embed per candidate, proves compatibility before use
- hard fail — simplest, loses search entirely on transient server loss
