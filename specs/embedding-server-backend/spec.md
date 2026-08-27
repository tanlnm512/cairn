# Spec: Embedding server backend (oMLX / Ollama / OpenAI-compatible)

**Status**: draft
**Created**: 2026-08-27
**Branch**: `docs/embedding-server-backend-spec`

## What

Cairn gains the ability to produce embeddings through any OpenAI-compatible
`/v1/embeddings` server already running on the user's machine — oMLX, Ollama,
LM Studio, llama.cpp, vLLM — instead of loading the sentence-transformers +
torch stack into every cairn process. Users configure a backend preset and
model id; cairn verifies numeric compatibility with stored vectors before
reusing them, degrades loudly (never silently) when the server or model
disappears, and can be configured and observed from the dashboard.

## Why

The default `local` backend costs a ~1 GB+ dependency install into
`~/.cairn/lib` and loads bge-m3 weights into **every** cairn process: the MCP
server pays ~9.4 s on the first `semantic_search` and holds ~1-2 GB RSS, while
an already-running local inference server could serve the same model once,
shared across cairn, editors, and other agents. Cairn's existing `openai`
backend already speaks the right wire format but hardcodes
`https://api.openai.com` (`src/cairn/graph/embeddings.py:740`) and requires an
API key — unusable for local servers.

## Business value

- **SC-1**: An agent runner with oMLX/Ollama installed gets semantic search
  with zero torch install and no first-query model-load stall (measured warm
  parity: oMLX 244 ms/query vs 137 ms in-process; batch-64 equal).
- **SC-2**: Switching producer with the same weights keeps existing vectors
  (measured parity cosine 1.000000) — no multi-minute corpus re-embed.
- **SC-3**: Any backend/model disappearance produces a named degradation with
  a remediation hint and continued BM25/FTS5 hybrid results — never a silent
  empty result, never mixed vector spaces.
- **SC-4**: Zero behavior change for `local`/`hash`/`openai` users (CI and
  existing tests untouched).

## User stories

### US1 — Embed via a local inference server (P1)
As an agent runner with oMLX or Ollama already running, I want cairn to use
its `/v1/embeddings` endpoint for all corpora, so that cairn needs no torch
stack and shares one model load with my other tools.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given a reachable server with the configured model id listed, When
  `cairn embed` runs, Then all three corpora embed through the server and
  rows are stamped `server/{netloc}/{model}` (FR-001, FR-004).
- AC2: Given the server returns a transient error (connection reset, 5xx,
  429), When embedding runs, Then requests retry with backoff and bounded
  chunking, and a non-transient 4xx fails with the server's own error message
  verbatim (FR-003).
- AC3: Given the server is down or the model id is missing from
  `/v1/models`, When any query runs, Then the dense leg is skipped with a
  named degradation and hash vectors are never used (FR-002).

### US2 — Migrate without re-embedding (P1)
As a user moving from the local bge-m3 to a server serving the same weights,
I want cairn to verify compatibility and keep my existing vectors, so that
the switch costs seconds, not a full corpus re-embed.

**Acceptance criteria**:
- AC1: Given stored vectors under stamp `BAAI/bge-m3` and a migration alias
  configured, When the first embed pass runs, Then a parity check (mean
  cosine over sampled stored chunks, gate 0.98) runs BEFORE any row is
  written; below the gate it hard-aborts with the measured value (FR-005).
- AC2: Given the parity gate passes, When embedding completes, Then zero
  rows were re-embedded and dense search still returns correct results
  (FR-005, FR-004).

### US3 — Degrade loudly, never silently (P1)
As an agent relying on `semantic_search`, I want a verified fallback ladder
when the configured backend fails, so that search keeps working via the
BM25/FTS5 hybrid and I am told exactly what degraded and why.

**Acceptance criteria**:
- AC1: Given the configured model is missing but a same-server candidate
  passes the parity gate, When a query runs, Then the candidate serves the
  dense leg for that session via the alias mechanism and a notification names
  the explicit command that makes it permanent (FR-012).
- AC2: Given no parity-verified replacement exists (server or local), When a
  query runs, Then results are BM25/FTS5-hybrid with `provenance="bm25"`, a
  degradation tag, and a remediation hint — and an embed error mid-query does
  not raise out of `semantic_search` (FR-012).
- AC3: Given any degraded state, When it first occurs, Then one warn-once
  log line, one reason-enum telemetry event, an MCP result footnote, a doctor
  entry, and a dashboard banner appear (FR-013).

### US4 — Configure and observe from the dashboard (P2)
As a user who prefers UI over env vars, I want a dashboard Settings section
and Embeddings status view, so that I can switch backends, run parity checks,
and see fallback state without editing shell profiles.

**Acceptance criteria**:
- AC1: Given the dashboard, When I save settings, Then values persist to
  `~/.cairn/config.json` with env vars still taking precedence, and running
  processes pick up changes without restart (FR-010, FR-011).
- AC2: Given a base-URL change attempt, When saving without the explicit
  confirm step, Then the write is refused; the API key is never rendered
  back (FR-011).
- AC3: Given any backend state, When I open the status view, Then I see
  effective backend, resolved stamp, per-corpus counts, probe health, and
  the active fallback rung (FR-011).

### US5 — Operational safety nets (P2)
As a maintainer, I want warmup, doctor, and telemetry coverage for the new
backend, so that failures are diagnosable and first-query cost stays off the
query path.

**Acceptance criteria**:
- AC1: Given a server backend at MCP boot, When the warm step runs, Then one
  tiny probe triggers the server's lazy model load without ever raising into
  boot (FR-006).
- AC2: Given doctor with a server backend configured, When run, Then it
  checks probe, model listing, parity sample, and latency (FR-007).
- AC3: Given a fresh install, When a user asks how to get semantic search,
  Then `install_hint()` mentions the server path as the no-torch option
  (FR-008).

## Requirements

- **FR-001**: The system shall embed all corpora (code, knowledge, memory)
  through one OpenAI-compatible `/v1/embeddings` client when
  `CAIRN_EMBED_BACKEND` is `server`, `omlx`, or `ollama`; presets differ only
  in default base URL (`omlx` → `http://127.0.0.1:8000/v1`, `ollama` →
  `http://127.0.0.1:11434/v1`; bare `server` requires `CAIRN_EMBED_BASE_URL`).
- **FR-002**: The system shall gate the server backend on a cached
  process-level probe — `GET {base}/models` (2 s timeout, optional bearer)
  returning 200 AND listing the configured model id — and shall never
  resolve a server backend to the hash backend; `is_hash_fallback()` stays
  False.
- **FR-003**: The system shall chunk inputs into
  `CAIRN_EMBED_SERVER_BATCH`-sized requests (default 32), retry connection
  errors / timeouts / 5xx / 429 up to 3 times with exponential backoff
  (0.5/1/2 s, jittered), fail other 4xx immediately with the server's error
  message verbatim, honor `CAIRN_EMBED_TIMEOUT` (default 30 s), and reject
  mixed-dimension batches.
- **FR-004**: The system shall stamp server-produced rows
  `server/{netloc}/{model}` so all existing stamp-driven machinery
  (staleness, purge, vec0 table names) works unmodified;
  `CAIRN_EMBED_MODEL_STAMP` overrides the derived stamp.
- **FR-005**: The system shall run the migration-alias parity check (sample
  ≤16 stored chunks, mean cosine gate 0.98, dim match) BEFORE any row is
  written under an alias stamp, and hard-abort with the measured value on
  failure. With zero stored rows under the stamp, the check is skipped
  (vacuous pass — nothing to compare) and embedding proceeds.
- **FR-006**: The system shall warm a server backend with one tiny
  `/v1/embeddings` probe in the existing background warmup thread, never
  raising into boot and respecting the `PYTEST_CURRENT_TEST` guard.
- **FR-007**: The system shall emit one `EMBED_SERVER_DEGRADED` telemetry
  event per process per reason and extend `cairn doctor` with probe,
  model-listing, parity-sample, and latency checks for server backends.
- **FR-008**: The system shall update `docs/configuration.md`,
  `docs/retrieval.md`, and `install_hint()` to document the server backend
  (including the oMLX safetensors conversion and privacy notes).
- **FR-009**: The system shall keep the `local`, `hash`, and `openai`
  backends byte-for-byte unchanged (openai keeps its hardcoded URL and key
  requirement).
- **FR-010**: The system shall persist configuration in
  `~/.cairn/config.json` with precedence env > file > default, re-reading on
  mtime change alongside the existing per-process caches, invalidated by
  `reset_backend_cache()`.
- **FR-011**: The system shall expose a dashboard Settings section and
  Embeddings status view (loopback-only POST routes, write-only API key,
  confirm-required base-URL changes, live parity-check action, fallback-rung
  banner).
- **FR-012**: The system shall implement the availability fallback ladder:
  (rung 1) same-server candidate adopted session-scoped via alias only on
  parity ≥ 0.98, otherwise notify re-embed; (rung 2) local model on the same
  parity gate; (rung 3) the existing BM25/FTS5 hybrid — the query rides
  today's BM25+RRF fusion path unchanged with zero dense candidates (no new
  short-circuit), results tagged `provenance="bm25"` plus an additive
  `degraded` key and hint — including catching dense-leg embed errors inside
  `semantic_search`, which today propagate uncaught. Permanence of a rung-1
  adoption (`cairn embed --adopt-server-model` / dashboard button) persists
  the ALIAS BINDING: the stored corpus keeps its stamp while embeds and
  queries run through the adopted model, with the FR-005 parity check
  re-verified once per process; it is not a corpus restamp.
- **FR-013**: The system shall notify on every degraded/failed state via one
  warn-once logger line, one `EMBED_SERVER_DEGRADED` event with reason enum
  (`server_down | model_missing | parity_fail | fallback_session_alias |
  fallback_local | hybrid_only`), an MCP result footnote, a doctor entry,
  and a dashboard banner.

## Scope

**In**: server/omlx/ollama backends; parity-verified migration and fallback
ladder; notifications; config substrate; dashboard Settings + status view;
warmup/doctor/telemetry/docs.

**Out (deferred)**: `/v1/rerank` for the reranker (oMLX exposes it; separate
spec); per-corpus server models; HTTP keep-alive/connection pooling; cloud
providers beyond the existing `openai` backend; a full dashboard visual
revamp (oMLX's admin is the UX reference for the Settings section only).

## Assumptions & risks

- Assumption: same weights ⇒ near-identical vectors (verified: cosine
  1.000000, worst-case 0.991 on >512-token inputs from truncation), so the
  0.98 gate cleanly separates same-weights from different-model regimes.
- Assumption: config-file precedence env > file keeps CI/tests and the
  env-driven test doctrine working unchanged.
- Risk: a server model changed under a stable id mixes vector spaces
  silently — mitigation: parity gate on alias adoption, doctor parity
  sample, documented "run doctor after re-pulling models" rule; residual
  accepted (D-009).
- Risk: dashboard POST routes end the read-only era — mitigation: loopback
  binding, base-URL confirm step, write-only API key.
- Risk: first embedding request pays server-side lazy model load (seconds) —
  mitigation: warmup probe (FR-006).

---

# Design annex (verified research + architecture)

Research and live verification were completed before this pipeline started;
the annex below is the evidence base the Stage 2 agents build on.

## A1. Verified evidence (2026-08-27, this Mac)

Live comparison: sentence-transformers `BAAI/bge-m3` (the exact code path
`_embed_local` uses, max_seq_length=512, `normalize_embeddings=True`) vs
oMLX 0.6.2 `/v1/embeddings` serving the same BAAI weights:

| Check | Result |
|---|---|
| Dimensions | 1024 = 1024 |
| Per-text cosine(ST, oMLX), 8 real symbol chunks + 1 query | **min 1.000000, mean 1.000000** |
| Top-3 retrieval order on mini corpus | **identical** |
| Long input >512 tokens (truncation divergence) | cosine 0.991 |
| oMLX vector norm | 1.0 (unit-normalized, like ST's `normalize_embeddings=True`) |
| Warm single-query latency | 137 ms (ST) vs 244 ms (oMLX incl. HTTP) |
| Warm batch-64 | 0.38 s (ST) vs 0.42 s (oMLX incl. HTTP) |
| Process footprint | ST: torch + weights in-process; oMLX: none |

Conclusions:
1. Vectors from the same weights are interchangeable (cosine 1.000000;
   cairn compares with cosine everywhere — `cosine_scan`, vec0 cosine metric).
2. Divergence is bounded by truncation only; `chunk_for_symbol` caps text at
   `max_tokens*4` chars client-side.
3. Ollama serves the same model (`ollama pull bge-m3`, id `bge-m3`, 1024
   dims) behind the same `/v1/embeddings` shape; LM Studio (`:1234/v1`),
   llama.cpp server, vLLM expose the same endpoint — one generic client
   covers all.

oMLX quirks (document, don't code around):
- Embedding engine loads safetensors only; the `BAAI/bge-m3` HF repo ships
  only `pytorch_model.bin` → one-time local conversion (appendix C).
- May require an API key (`~/.omlx/settings.json` → `auth.api_key`); auth
  failures return OpenAI-shaped `authentication_error`; unknown model returns
  `not_found_error` listing available ids.
- Served model id is the model-directory basename (`bge-m3`), not the HF path.

## A2. Design

### A2.1 Configuration surface

| Env var | Meaning | Default |
|---|---|---|
| `CAIRN_EMBED_BACKEND` | `server` \| `omlx` \| `ollama` (new) alongside `local`/`hash`/`openai` | `local` (unchanged) |
| `CAIRN_EMBED_BASE_URL` | Base URL ending in `/v1` | preset per backend; required for bare `server` |
| `CAIRN_EMBED_SERVER_MODEL` | Model id sent in the request | `bge-m3` |
| `CAIRN_EMBED_API_KEY` | Optional bearer token | none |
| `CAIRN_EMBED_TIMEOUT` | Per-request timeout (seconds) | `30` |
| `CAIRN_EMBED_SERVER_BATCH` | Max inputs per HTTP request | `32` |
| `CAIRN_EMBED_MODEL_STAMP` | Override the derived model stamp (migration alias) | derived |

### A2.2 Backend resolution and availability

- `_effective_backend()` (`src/cairn/graph/embeddings.py:323`): the server
  family resolves to `server` with no import probing and never to `hash`.
- `embeddings_available()` (`embeddings.py:65`): the FR-002 probe, cached per
  process.
- Degradation: probe failure or a mid-query embed error (verified: today
  such errors propagate out of `semantic_search` uncaught — the dense path
  has no handler around `embed_query`) runs the A2.7 ladder before anything
  is returned. `cairn embed` exits 1 with the remediation message.
- The MCP embed flusher's retry loop (`src/cairn/mcp_server/embed_buffering.py`)
  already tolerates transient server errors for memory embeds.

### A2.3 `_embed_server` client

POST `{base}/embeddings`, sort response `data` by `index`, decode via
`_floats_to_blob`; chunking/retry/timeout per FR-003; bearer auth only when
`CAIRN_EMBED_API_KEY` is set.

### A2.4 Model stamping and migration

- Derived stamp `server/{netloc}/{model}`; switching local→server, port
  changes, or model-id changes re-embed via the existing model-swap
  machinery (`current_model()` at `embeddings.py:39`, `purge_stale_models`
  at `embeddings.py:682`).
- Migration alias = `CAIRN_EMBED_MODEL_STAMP`; first use runs the FR-005
  parity check against stored rows (zero stored rows ⇒ vacuous pass); pass ⇒
  rows keep the alias stamp (measured today: 1.000000); fail ⇒ hard abort,
  zero rows written.
- The 0.991 truncation worst case sits above the 0.98 gate by design;
  different models land far below 0.9.

### A2.5 Warmup

`src/cairn/graph/model_warmup.py` warms only `local` today. For `server`,
the warm step is one tiny POST (triggers server-side lazy load), under the
module's existing constraints (daemon thread, never raise into boot, one
warning, `PYTEST_CURRENT_TEST` guard).

### A2.6 Telemetry, doctor, CLI, docs

- `EMBED_SERVER_DEGRADED` mirrors the `HASH_FALLBACK` pattern
  (`embeddings.py:369`); payload host+model only, never request bodies.
- Doctor: probe / model-listing / parity sample / latency; exit semantics
  unchanged.
- `cairn embed`: env-driven as today; server-down output carries the
  remediation hint; CliRunner tests per house rule.
- Docs per FR-008, including the privacy note (embedding input is code text
  sent to the configured URL; localhost by default, remote is an explicit
  user choice — same trust model as the existing `openai` backend).

### A2.7 Availability & fallback ladder

Evaluated on probe failure, model-missing, or embed-call error; each rung
restores the dense leg with proof or falls through; evaluated at most once
per process per backend-state (cached; `reset_backend_cache()`, doctor, and
embed force re-evaluation).

1. **Parity-verified replacement on the same server**: scan `/v1/models`
   for embedding-capable candidates; parity check per candidate; ≥ 0.98 ⇒
   adopt session-scoped via the alias mechanism (zero re-embed) + notify
   that `cairn embed --adopt-server-model` or the dashboard button makes it
   permanent — permanence persists the ALIAS BINDING (the corpus keeps its
   stamp while embeds and queries run through the adopted model, FR-005
   parity re-verified once per process; not a corpus restamp); below gate ⇒
   different vector space, dense leg stays dead, notification says re-embed
   required, fall through.
2. **Parity-verified local model**: sentence-transformers importable AND
   weights cached AND parity ≥ 0.98 ⇒ session fallback to local (the
   measured 1.000000 case; also the clean return path for local→server
   migrations); else fall through.
3. **Terminal — existing BM25/FTS5 hybrid**: the dense leg contributes
   nothing; the query rides today's BM25+RRF fusion path unchanged with
   zero dense candidates (verified: `semantic_search` returns
   `provenance="bm25"` when zero cosine candidates survive — no new
   short-circuit); results gain `degraded="embedding-backend"` + remediation
   hint as additive keys.

Hard rules: hash is never a rung; a producer-changing rung is taken only on
parity proof; everything else notifies and degrades loudly.

### A2.8 Persistent config + dashboard Settings UI

- `~/.cairn/config.json` (sibling of `workspaces.json`): keys mirroring the
  A2.1 env vars; env > file > default; mtime-based re-read.
- Dashboard Settings section (loopback-only POSTs): backend choice, server
  model id, API key (write-only), timeout/batch, migration alias, "Run
  parity check" action. oMLX's `/admin` is the UX reference; visual language
  stays the dashboard's existing post-0.13.0 style — a section, not a
  revamp.
- Embeddings status view: effective backend, resolved stamp, per-corpus row
  counts, last-embedded time, probe health, active fallback rung as banner.
- Security: loopback binding mandatory; base-URL change requires an explicit
  confirm step; API key never rendered back.

## A3. Decisions (summaries; tech-spec.md owns the formal D-001..D-010)

- D-001 — new `server` backend, `openai` untouched (zero regression surface).
- D-002 — stamp includes the server netloc (producer identity is vector
  identity; alias is the verified exception).
- D-003 — no silent hash fallback on server errors (loud failure with
  remediation).
- D-004 — client-side truncation stays as-is (char cap bounds divergence to
  the measured 0.991 worst case).
- D-005 — single server model for all corpora in v1 (like the openai
  backend today).
- D-006 — urllib, no new dependency (measured ~100 ms/query overhead
  acceptable for 1-2 embed_query calls per search).
- D-007 — parity gate threshold 0.98 (same-weights ~1.000000 with noise to
  0.991; unrelated models far below 0.9).
- D-008 — env > config file > default (dashboard changes behavior only
  through the persistent substrate; env semantics untouched).
- D-009 — a different model id never silently serves the dense leg (only
  the parity gate earns a session-scoped switch; permanence is explicit).
- D-010 — notifications reuse existing patterns (warn-once logger,
  reason-enum telemetry, MCP footnote à la `unembedded_memory_hint`,
  doctor, dashboard banner).

## A4. Risks / notes

- Latency: +~100 ms per embed_query (HTTP + JSON of 1024 floats); bounded,
  measured; keep-alive is the escape hatch later.
- Server-side model change under a stable id: covered only by the alias
  gate and doctor parity sample when no alias is set; residual accepted.
- oMLX onboarding friction (safetensors conversion, per-install API key):
  documentation, not code.
- Dashboard write surface: loopback + confirm + write-only keys bound the
  risk; residual = any local process on the port (same trust domain oMLX's
  admin accepts).
- Session-fallback surprise: FR-013 notifications are the counterweight.
- Ladder evaluation cost: rung 1 parity embeds ~16 chunks per candidate;
  bounded by the once-per-process cache.

## A5. Rollout

Two phases, each independently shippable:

- **Phase 1 — server backend + ladder (env-configured)**: FR-001..FR-007,
  FR-012, FR-013 (logger/telemetry/footnote/doctor parts). Default-off, no
  schema migration (stamps are strings in existing columns; vec0 tables are
  model-scoped by name and created lazily). This phase alone fixes the
  uncaught-embed-error gap.
- **Phase 2 — config substrate + dashboard UI**: FR-008, FR-010, FR-011,
  and the dashboard-banner notification surface of FR-013.

## Appendix B — researched surfaces

- oMLX: `/v1/embeddings` + `/v1/rerank`, model auto-discovery from
  `~/.omlx/models`, Apache-2.0, macOS 15+/Apple Silicon —
  github.com/jundot/omlx, omlx.ai
- Ollama: official bge-m3 library model (ollama.com/library/bge-m3),
  OpenAI-compatible `/v1/embeddings` at `:11434/v1`
- Same endpoint shape: LM Studio `:1234/v1`, llama.cpp server, vLLM,
  vllm-mlx (github.com/waybarrios/vllm-mlx)

## Appendix C — reproduce the verification (this machine)

```bash
# oMLX model setup: BAAI/bge-m3 ships pytorch_model.bin only; oMLX needs safetensors.
PYTHONPATH=~/.cairn/lib .venv/bin/python - <<'EOF'
from huggingface_hub import snapshot_download
import torch
from safetensors.torch import save_file
d = "/Users/tanle/.omlx/models/BAAI/bge-m3"
snapshot_download("BAAI/bge-m3", local_dir=d)
sd = torch.load(f"{d}/pytorch_model.bin", map_location="cpu", weights_only=True)
save_file({k: v.contiguous() for k, v in sd.items()}, f"{d}/model.safetensors", metadata={"format": "pt"})
EOF

omlx restart   # discovers models/BAAI/bge-m3, serves it as id "bge-m3"
curl -s -H "Authorization: Bearer $(jq -r .auth.api_key ~/.omlx/settings.json)" \
  http://localhost:8000/v1/models
# parity probe: embed stored chunks via server, compare cosine vs stored rows
```

Ollama equivalent: `ollama pull bge-m3` → base URL `http://localhost:11434/v1`,
model id `bge-m3`, no auth.
