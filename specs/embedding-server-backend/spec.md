# Spec: Embedding Server Backend (oMLX / Ollama / OpenAI-compatible)

**Status:** Proposed — research complete, live-verified on this machine (2026-08-27)
**Scope:** `src/cairn/graph/embeddings.py` and its integration surfaces; no schema changes
**Default behavior:** unchanged (`CAIRN_EMBED_BACKEND=local` remains the default; everything here is opt-in)

---

## 1. Problem

Cairn's default embedding path (`local`) requires the sentence-transformers +
torch stack (~1 GB+ install into `~/.cairn/lib`) and loads bge-m3 weights
**into every cairn process**: the MCP server pays ~9.4 s on the first
`semantic_search` (see `model_warmup.py` header) and holds ~1-2 GB RSS for
weights that an already-running local inference server could serve once,
shared across cairn, editors, and other agents.

Users running [oMLX](https://github.com/jundot/omlx), Ollama, LM Studio, or
llama.cpp servers already have an OpenAI-compatible `/v1/embeddings` endpoint
on localhost. Cairn has an `openai` backend that speaks exactly this wire
format — but its base URL is hardcoded to `https://api.openai.com`
(`src/cairn/graph/embeddings.py:740`) and it requires `OPENAI_API_KEY`.

## 2. Verified evidence (2026-08-27, this Mac)

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
| Process footprint | ST: torch + weights in-process; oMLX: none (server holds model once) |

Conclusions the spec relies on:
1. **Vectors from the same weights are interchangeable** (cosine 1.000000;
   cairn compares with cosine everywhere — `cosine_scan`, vec0 cosine metric —
   so even norm differences would be tolerated, but there are none).
2. **Divergence is bounded by truncation only** (512 vs 8192 context). Cairn's
   `chunk_for_symbol` already caps text at `max_tokens*4` chars client-side,
   so the window is small and identical in spirit to today's local behavior.
3. Ollama serves the same model (`ollama pull bge-m3`, id `bge-m3`, 1024 dims)
   behind the same `/v1/embeddings` shape at `http://localhost:11434/v1`
   ([library page](https://ollama.com/library/bge-m3)). LM Studio
   (`:1234/v1`), llama.cpp server, and vLLM expose the same endpoint shape —
   **one generic client covers all of them.**

oMLX quirks found during verification (document, don't code around):
- oMLX's embedding engine loads **safetensors only**; the `BAAI/bge-m3` HF
  repo ships only `pytorch_model.bin` → one-time local conversion required
  (appendix A).
- oMLX may require an API key (`~/.omlx/settings.json` → `auth.api_key`;
  this machine uses `1234`). Auth failures return OpenAI-shaped
  `authentication_error`; unknown model returns `not_found_error` that helpfully
  lists available model ids.
- The served model id is the model-directory basename (`bge-m3`), not the HF
  path (`BAAI/bge-m3`).

## 3. Goals / Non-goals

**Goals**
- Embed via any OpenAI-compatible `/v1/embeddings` server (oMLX, Ollama,
  LM Studio, llama.cpp, vLLM, …) with zero torch footprint in the cairn process.
- Preserve every existing guarantee: model-stamp invalidation, vec0 ANN table
  scoping, staleness/re-embed semantics, MCP embed buffering, warmup safety.
- A verifiable migration path from `local` to `server` that does not force a
  full corpus re-embed when the server serves the same weights (parity-checked,
  not assumed).
- Graceful degradation when the configured backend/model disappears: a
  parity-verified fallback ladder (§4.7) that never silently mixes vector
  spaces, ending in the already-shipped BM25/FTS5 hybrid leg.
- A persistent config substrate (`~/.cairn/config.json`) plus a dashboard
  Settings + Embeddings-status UI (§4.8); env vars remain the override layer.

**Non-goals (follow-ups, separate specs)**
- `/v1/rerank` for the reranker (oMLX exposes it; rerank stays CrossEncoder).
- Per-corpus server models (`CAIRN_EMBED_KNOWLEDGE_MODEL` etc. stay local-only).
- HTTP keep-alive / connection pooling (measured overhead ~100 ms/query is
  acceptable; revisit only if bench shows it matters).
- New cloud providers (the existing `openai` backend covers OpenAI).

## 4. Design

### 4.1 Configuration surface

| Env var | Meaning | Default |
|---|---|---|
| `CAIRN_EMBED_BACKEND` | `server` \| `omlx` \| `ollama` (new) alongside `local`/`hash`/`openai` | `local` (unchanged) |
| `CAIRN_EMBED_BASE_URL` | Base URL ending in `/v1` | preset: `omlx` → `http://127.0.0.1:8000/v1`; `ollama` → `http://127.0.0.1:11434/v1`; bare `server` → **required** |
| `CAIRN_EMBED_SERVER_MODEL` | Model id sent in the request | `bge-m3` |
| `CAIRN_EMBED_API_KEY` | Optional bearer token (oMLX with auth on) | none |
| `CAIRN_EMBED_TIMEOUT` | Per-request timeout (seconds) | `30` |
| `CAIRN_EMBED_SERVER_BATCH` | Max inputs per HTTP request | `32` |
| `CAIRN_EMBED_MODEL_STAMP` | Override the derived model stamp (migration alias, §4.4) | derived |

`omlx` / `ollama` are pure presets of `server` (base URL + docs); they add no
separate code path.

### 4.2 Backend resolution and availability

- `_effective_backend()` (`embeddings.py:323`): the server family resolves to
  `server` with no import probing — there is nothing to import. It must
  **never** resolve to `hash` (that fallback exists only for
  local-without-sentence-transformers).
- `embeddings_available()` (`embeddings.py:65`): for `server`, one cached
  process-level probe — `GET {base}/models` with a 2 s timeout, API key
  attached if set, that must return 200 **and list the configured model id**.
  Either check failing → False. No key requirement.
- **Degradation contract (Trust & Proof):** when the probe fails (server
  down *or* model missing from the listing), or an embed call errors
  mid-query — verified: today such an error propagates out of
  `semantic_search` uncaught — the fallback ladder in §4.7 runs before
  anything is returned. Its terminal state is the existing BM25/FTS5 hybrid
  leg with `provenance` tagging: never a silent empty result, never hash
  vectors. `cairn embed` exits 1 with the remediation message.
- The MCP embed flusher's existing retry-on-failure loop
  (`mcp_server/embed_buffering.py`) already tolerates transient server errors
  for memory embeds; no change needed there beyond the error text.

### 4.3 `_embed_server` client

One function, `_embed_server(texts) -> (List[bytes], int)`, wired into
`_embed()` dispatch (`embeddings.py:806`):

- POST `{base}/embeddings` with `{"model": …, "input": […]}`; sort response
  `data` by `index` (same as `_embed_openai`); decode via `_floats_to_blob`.
- **Chunking:** split inputs into `CAIRN_EMBED_SERVER_BATCH`-sized requests
  (embed_all's default batch of 64 becomes 2+2 requests). Order preserved.
- **Retries:** up to 3 attempts with exponential backoff (0.5/1/2 s, jittered)
  on connection errors, timeouts, HTTP 5xx, and 429. **No retry on other
  4xx** — raise `RuntimeError` carrying the server's error `message` verbatim
  (oMLX's "Available models: …" text is the diagnosis a user needs).
- **Auth:** `Authorization: Bearer {CAIRN_EMBED_API_KEY}` only when set.
- **Dim guard:** all returned vectors in a response must share one dim;
  mismatch within a batch → RuntimeError (defensive; oMLX/Ollama never do).

### 4.4 Model stamping and migration

The model stamp (persisted per row, drives re-embed + vec0 table names —
`current_model()` at `embeddings.py:39`, `purge_stale_models` at
`embeddings.py:682`):

- **Derived stamp:** `server/{netloc}/{model}` — e.g.
  `server/127.0.0.1:8000/bge-m3`. Switching local→server, server→server on a
  different port, or changing the model id each re-embed naturally through the
  existing model-swap machinery. This is the safe default: identity of the
  *producer* is part of the stamp, so a different server behind the same model
  name can never silently mix vector spaces.
- **Migration alias (`CAIRN_EMBED_MODEL_STAMP`):** opt-in override for the
  common case "same bge-m3 weights, now served by oMLX/Ollama". With the alias
  set, rows are stamped with the alias (e.g. `BAAI/bge-m3`) and existing
  vectors are kept. Because this *assumes* numeric compatibility, the
  assumption is **verified at first use, not trusted**:
  - On the first `embed_all`/`embed_symbols` run under an alias stamp that
    already has stored rows: sample up to 16 existing `(chunk, vec)` rows
    under that stamp, embed the chunks via the server, compute mean cosine.
  - mean ≥ **0.98** → proceed (parity proven; measured today: 1.000000).
  - mean < 0.98 (or dim mismatch) → **hard abort**, zero rows written, error
    names the measured mean and instructs removing the alias (which falls
    back to the derived stamp + full re-embed).
- The existing truncation divergence (0.991 worst case >512 tokens) sits
  above the 0.98 gate by design: chunk text is already char-capped
  client-side, so real-world samples pass; a genuinely different model fails
  by a wide margin.

### 4.5 Warmup

`model_warmup.py` warms only when the effective backend is `local`. Add: for
`server`, the warm step is one tiny `POST /v1/embeddings` with a single short
string (triggers the server's lazy model load — measured seconds on first
call). Same constraints as today: daemon thread, never raise into boot, one
warning on failure, `PYTEST_CURRENT_TEST` guard unchanged.

### 4.6 Telemetry, doctor, CLI, docs

- **Telemetry:** new durable event `EMBED_SERVER_DEGRADED` mirroring the
  `HASH_FALLBACK` pattern (`embeddings.py:369`) — fired once per process per
  reason when a server-backend probe/request fails or a ladder rung
  activates (reason enum per FR-13). Payload: host+model only, never
  request bodies.
- **Doctor:** new checks when a server backend is configured —
  (1) probe `GET /v1/models` (2 s); (2) configured model id present in the
  listing; (3) quick parity sample as in §4.4 when rows exist; (4) report
  warm round-trip latency. Exit semantics unchanged.
- **CLI:** `cairn embed` needs no new flags (env-driven, like today), but its
  failure output for server-down must carry the remediation hint. CliRunner
  tests required (house rule: one per subcommand surface touched).
- **Docs:** `docs/configuration.md` gains the env table + per-server quick
  starts (oMLX incl. the safetensors conversion note, Ollama `ollama pull
  bge-m3`, LM Studio/llama.cpp URLs); `install_hint()` gains one line: a
  server backend needs no torch install at all; `docs/retrieval.md` notes the
  backend option. Privacy note in configuration.md: embedding input is code
  text sent to the configured URL — localhost by default; pointing it at a
  remote host is an explicit user choice (same trust model as the existing
  `openai` backend).

### 4.7 Availability & fallback ladder

Evaluated whenever the configured server backend cannot serve the dense leg:
probe failure, model missing from the `/v1/models` listing, or an embed-call
error mid-query. Each rung either restores the dense leg **with proof** or
falls through. The ladder is evaluated at most once per process per
backend-state (cached alongside the availability probe; `reset_backend_cache()`,
`cairn doctor`, and `cairn embed` force re-evaluation).

1. **Parity-verified replacement on the same server.** Scan the probe's
   `/v1/models` listing for other embedding-capable ids. For each candidate,
   run the §4.4 parity check against stored rows. Mean cosine ≥ 0.98 → the
   candidate serves the same weights: adopt it **for this session only**
   via the alias-stamp mechanism (queries embed through the candidate;
   stored rows keep their stamp; zero re-embed) and notify (FR-13) that
   `cairn embed --adopt-server-model <id>` or the dashboard button makes it
   permanent. Below 0.98 → different vector space: the dense leg stays
   dead, the notification says a re-embed is required to switch to that
   model, fall through.
2. **Parity-verified local model.** `sentence_transformers` importable AND
   model weights cached AND parity ≥ 0.98 vs stored rows → session fallback
   to the local backend (the measured 1.000000 case: same bge-m3 weights;
   also the clean return path for local→server migrations). Not installed,
   not cached, or below threshold → fall through.
3. **Terminal: existing BM25/FTS5 hybrid.** The dense leg contributes
   nothing; results come from the BM25 leg + RRF fusion — already shipped,
   verified behavior (`semantic_search` returns `provenance="bm25"` when
   zero cosine candidates survive). Results gain `degraded="embedding-backend"`
   plus the remediation hint naming what was tried.

Hard rules: the hash backend is never a rung; a rung that changes the vector
producer is taken **only** when the parity gate proves numeric compatibility;
everything else notifies and degrades loudly.

### 4.8 Persistent config + dashboard Settings UI

Cairn config today is env-only, so no process can change another process's
backend — a config UI needs a substrate first.

- **`~/.cairn/config.json`** (sibling of the existing `workspaces.json`
  registry): JSON keys mirroring the §4.1 env vars. Precedence:
  **env var > config file > default**, so CI, tests, and power users keep
  today's env-driven semantics untouched. Backend/model resolution re-stats
  the file (mtime + size) and re-reads on change, alongside the existing
  per-process caches (`_EFFECTIVE_BACKEND_CACHE` et al.).
- **Dashboard Settings section** (new POST routes; the dashboard stays
  loopback-bound as it is today): backend choice (local / server presets /
  custom URL), server model id, API key, timeout/batch, migration alias —
  plus a "Run parity check" action that reports the §4.4 verdict live.
  oMLX's `/admin` panel is the UX reference (settings forms + live model
  listing + apply-without-restart feel); the visual language stays the
  dashboard's existing post-0.13.0 style — this adds a section, not a
  revamp.
- **Embeddings status view**: effective backend, resolved stamp, per-corpus
  row counts, last-embedded time, probe health, and the active fallback
  rung as a banner (this doubles as FR-13's notification surface).
- **Security notes:** the base-URL field is the one exfiltration-relevant
  setting (embedding input is code text). Loopback-only binding is
  mandatory (already the dashboard default), a base-URL change requires an
  explicit confirm step, and the API key is write-only (never rendered
  back, masked in the UI, value not returned by the API after save).

## 5. Functional requirements

- **FR-1** `CAIRN_EMBED_BACKEND=server|omlx|ollama` embeds all corpora (code,
  knowledge, memory) through one OpenAI-compatible `/v1/embeddings` client;
  presets differ only in default base URL.
- **FR-2** Availability probing per §4.2 (reachable server **and** model id
  present in the listing); server-down/model-missing never degrades to hash;
  `is_hash_fallback()` stays False for server backends.
- **FR-3** Batch chunking, bounded retries, timeout, and verbatim server-error
  propagation per §4.3.
- **FR-4** Model stamp derivation per §4.4; all existing stamp-driven
  machinery (staleness, purge, vec0 table names) works unmodified.
- **FR-5** Migration alias with the 0.98 mean-cosine parity gate and hard
  abort; the gate runs before any row is written under the alias.
- **FR-6** Warmup probe step per §4.5 under the module's existing safety rules.
- **FR-7** `EMBED_SERVER_DEGRADED` telemetry event; doctor checks per §4.6.
- **FR-8** Docs + `install_hint()` updates per §4.6.
- **FR-9** The `local`, `hash`, and `openai` backends are byte-for-byte
  unchanged (openai keeps its hardcoded URL and key requirement).
- **FR-10** Persistent config substrate per §4.8: `~/.cairn/config.json`,
  env > file > default precedence, mtime-based re-read, invalidation via
  `reset_backend_cache()`.
- **FR-11** Dashboard Settings section + Embeddings status view per §4.8:
  loopback-only POST routes, API key write-only, base-URL confirm step,
  parity-check action, fallback-rung banner.
- **FR-12** The §4.7 fallback ladder, including catching dense-leg embed
  errors inside `semantic_search` (today they propagate uncaught). Rung 1
  and 2 activate only on parity ≥ 0.98 and are session-scoped; permanence
  requires the explicit adopt command/UI action.
- **FR-13** Notifications for every degraded/failed state: one warn-once
  logger line, one durable telemetry event (`EMBED_SERVER_DEGRADED` with a
  reason enum: `server_down | model_missing | parity_fail |
  fallback_session_alias | fallback_local | hybrid_only`), an MCP result
  footnote on affected queries (the `unembedded_memory_hint` pattern,
  `embeddings.py:1478`), a doctor entry, and the dashboard banner.

## 6. Decisions

- **D-1 — new `server` backend, `openai` untouched.** Zero regression surface
  for existing users; the openai backend keeps its contract. The two share
  only the response-parsing idiom.
- **D-2 — stamp includes the server netloc.** Identity of the producer is part
  of vector identity; the alias override exists for verified migration only.
- **D-3 — no silent hash fallback on server errors.** Trust & Proof: a wrong
  answer silently is worse than a loud failure with a remediation hint.
- **D-4 — client-side truncation stays as-is.** The char cap in
  `chunk_for_symbol` already bounds the 512-vs-8192 divergence to the
  measured 0.991 worst case; parity gate tolerates it, different models don't.
- **D-5 — single server model for all corpora in v1** (like the openai
  backend today). Per-corpus server models are a follow-up if asked for.
- **D-6 — urllib, no new dependency.** Same stdlib approach as `_embed_openai`;
  measured ~100 ms/query overhead is acceptable for 1-2 embed_query calls per
  search.
- **D-7 — parity gate threshold 0.98.** Measured same-weights parity is
  ~1.000000 with truncation noise down to 0.991; unrelated models land far
  below 0.9 in practice. 0.98 splits the two regimes with margin both ways.
- **D-8 — env > config file > default.** The dashboard can only change
  behavior through a persistent substrate; env stays on top so CI/tests and
  the env-driven test doctrine keep working unchanged.
- **D-9 — a different model id never silently serves the dense leg.** Only
  the runtime parity gate (≥0.98 mean cosine vs stored rows) earns a
  session-scoped switch via the alias stamp; permanence is always an
  explicit user action. Silent model substitution would mix vector spaces —
  the worst failure mode this system can have.
- **D-10 — notifications reuse existing patterns.** Warn-once logger,
  reason-enum telemetry event, MCP result footnote (`unembedded_memory_hint`
  precedent), doctor entry, dashboard banner — no new notification channel.

## 7. Test criteria

- **TC-1** `_embed_server` against a stdlib `http.server` on `127.0.0.1:0`
  serving canned OpenAI-shaped responses (with shuffled `index` order) →
  correct, order-preserving float32 blobs. No network, no host literals
  asserted.
- **TC-2** Retry ladder: 500,500→200 succeeds after backoff; any other 4xx
  raises immediately with the server's message embedded.
- **TC-3** Connection-refused (bind a socket, close it, point the backend at
  the port): RuntimeError carries base URL + remediation; effective backend
  remains `server`; `is_hash_fallback()` False; `semantic_search` returns the
  `server-unavailable` provenance shape.
- **TC-4** Stamp derivation from URL; `CAIRN_EMBED_MODEL_STAMP` override wins.
- **TC-5** Parity gate: store vectors from canned server A; alias to canned
  server B returning orthogonal vectors → `embed_all` aborts, zero rows under
  the alias; with matching vectors → proceeds and reports skip (no re-embed).
- **TC-6** CliRunner e2e: `cairn embed` against the canned server writes rows
  under the derived stamp; server-down path exits 1 with the hint (also the
  "nonexistent endpoint" e2e, per the fixture-built-DB lesson).
- **TC-7** Availability probe caching per process; `reset_backend_cache()`
  clears it (tests toggle the env).
- **TC-8** Non-regression: full existing embedding/semantic suite green;
  `_embed_openai` still targets api.openai.com.
- **TC-9** Warmup: server-backend warm step issues exactly one probe; failure
  → single warning, no raise; `PYTEST_CURRENT_TEST` guard intact.
- **TC-10** Config precedence: env beats file, file beats default; file
  mtime change is picked up without process restart; `reset_backend_cache()`
  forces re-read.
- **TC-11** Dashboard settings: POST routes reject non-loopback binds;
  API-key GET never returns the stored value; base-URL change without the
  confirm field is a 4xx; writes round-trip into `config.json`.
- **TC-12** Ladder rung 1: model missing + candidate with matching vectors
  (canned server) → session alias active, results dense, adopt-notification
  present; candidate with orthogonal vectors → rung refused, notification
  says re-embed, falls through. Rung 2: sentence-transformers importable +
  parity → local session fallback; not installed → falls through. Rung 3:
  results are BM25-only with `provenance="bm25"`, `degraded` tag, footnote,
  and one telemetry event per reason.
- **TC-13** Embed-error containment: a server that 500s *after* the probe
  (race) mid-`semantic_search` does not raise; the ladder runs and hybrid
  results return (regression test for the uncaught-error gap).
- **TC-14** Ladder evaluation caching: one evaluation per process per
  backend-state; doctor/embed force re-evaluation.

## 8. Risks / notes

- **Latency:** +~100 ms per embed_query (HTTP + JSON of 1024 floats).
  Bounded, measured, acceptable; keep-alive is the escape hatch later.
- **Server-side model changes under a stable id** (user re-pulls a different
  model): the derived stamp does not change (same netloc+id). The §4.4 parity
  gate covers this only when an alias is set. Residual risk accepted — same
  class as re-pinning a local model file; doctor's parity check is the
  detection surface.
- **oMLX onboarding friction:** safetensors-only + BAAI repo without
  safetensors (appendix A) + per-install API key. All documentation, not code.
- **First-call server latency:** the server lazy-loads the model on first
  embedding request (seconds). Warmup (§4.5) exists to move this off the
  first query, mirroring today's local behavior.
- **Dashboard write surface:** adding POST routes ends the dashboard's
  read-only era. Loopback binding + confirm-on-base-URL + write-only keys
  (§4.8) bound the risk; the residual is any local process able to reach
  the port — same trust domain oMLX's admin accepts, documented openly.
- **Session fallback surprise:** rung 1/2 keep search working silently
  after e.g. an oMLX model rename. The FR-13 notification (banner, footnote,
  doctor) is the counterweight; permanence still requires an explicit adopt.
- **Ladder evaluation cost:** rung 1 parity checks embed ~16 stored chunks
  per candidate. Bounded by the once-per-process cache (§4.7) and the
  candidate list being small in practice.

## 9. Rollout

Two phases, each independently shippable:

- **Phase 1 — server backend + ladder (env-configured):** FR-1..FR-7,
  FR-12, FR-13 (logger/telemetry/footnote/doctor parts). Default-off, no
  schema migration (stamps are strings in existing columns; vec0 tables are
  model-scoped by name and created lazily). This phase alone fixes the
  uncaught-embed-error gap.
- **Phase 2 — config substrate + dashboard UI:** FR-8, FR-10, FR-11, and
  the dashboard-banner notification surface of FR-13.

Post-merge each phase: `cairn update`, `record_memory(type="decision")` for
the phase's D-items, CHANGELOG entry under `[Unreleased]`.

---

## Appendix A — reproduce the verification (this machine)

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
# parity: /tmp/cairn_omlx_parity.py — ST vs oMLX, cosine/truncation/latency
```

Ollama equivalent: `ollama pull bge-m3` → base URL `http://localhost:11434/v1`,
model id `bge-m3`, no auth.

## Appendix B — researched surfaces

- oMLX: `/v1/embeddings` + `/v1/rerank`, model auto-discovery from
  `~/.omlx/models`, Apache-2.0, macOS 15+/Apple Silicon —
  [github.com/jundot/omlx](https://github.com/jundot/omlx), [omlx.ai](https://omlx.ai/)
- Ollama: official [bge-m3 library model](https://ollama.com/library/bge-m3),
  OpenAI-compatible `/v1/embeddings` at `:11434/v1`
- Same endpoint shape: LM Studio `:1234/v1`, llama.cpp server, vLLM, vllm-mlx
  ([waybarrios/vllm-mlx](https://github.com/waybarrios/vllm-mlx))
