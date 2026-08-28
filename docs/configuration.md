# Configuration

Read this when you need to change what gets indexed, where state lives, or
how retrieval behaves.

## `cairn.json` (workspace root)

Parsed by `src/cairn/graph/config.py:load_config`. Unknown keys are ignored;
malformed files warn and fall back to defaults.

| Key | Type | Purpose |
|---|---|---|
| `exclude` | list of globs | repo-root-relative paths to skip (layer over gitignore) |
| `include` | list of globs | force-include; overrides skip-dirs, gitignore, and `exclude` — never the 1 MB cap |
| `repo_namespaces` | map | import-path prefix → owning repo id (cross-repo analysis) |
| `scip` | map | language → SCIP index path (relative); auto-generates when possible |
| `ingest` | object | knowledge-ingestion pipeline config (classification rules, dirs) |

## Store resolution

`src/cairn/paths.py:resolve_store` — priority: `CAIRN_DB` / `CAIRN_KNOWLEDGE`
overrides → `~/.cairn/workspaces.json` registry → cwd auto-register. The
store directory is `sha256(workspace_path)[:16]` under `CAIRN_HOME`
(default `~/.cairn`), holding `.kg` (SQLite) and `.knowledge/` (OKF bundle).
`CAIRN_HOME` binds at process start — in-process env changes do nothing; use
`--db` / `--workspace` flags instead.

## Environment variables

**Paths & identity**

| Var | Effect |
|---|---|
| `CAIRN_HOME` | central home dir (default `~/.cairn`) |
| `CAIRN_WORKSPACE` | explicit workspace root |
| `CAIRN_DB` / `CAIRN_KNOWLEDGE` | hard path overrides |
| `CAIRN_LIB` | shared dependency library path |
| `CAIRN_BIN` | path to the `cairn` binary (agent install) |

**Build**

| Var | Effect |
|---|---|
| `CAIRN_WORKERS` | parse parallelism (default cpu_count, clamped 1–256) |
| `CAIRN_WATCH` | file watcher gate (`[watch]` extra) |
| `CAIRN_REPO_NAMESPACES` | env-level cross-repo namespace map (JSON) |

**Retrieval & embeddings** — see [retrieval.md](retrieval.md) for behavior:
`CAIRN_FUSION`, `CAIRN_RERANK`, `CAIRN_RERANK_MODEL`,
`CAIRN_RERANK_MIN_MARGIN`, `CAIRN_ANN_BACKEND`, `CAIRN_EMBED_BACKEND`,
`CAIRN_EMBED_LOCAL_MODEL`, `CAIRN_EMBED_OPENAI_MODEL`,
`CAIRN_EMBED_KNOWLEDGE_MODEL`, `CAIRN_EMBED_MEMORY_MODEL`,
`CAIRN_WARM_MODELS`, `CAIRN_CHUNK_VARIANT`. Three local-backend knobs are
env only — not read from config.json: `CAIRN_EMBED_TRUST_REMOTE_CODE`,
`CAIRN_EMBED_FP16`, `CAIRN_EMBED_MAX_SEQ_LEN`. The server-backend knobs
(`CAIRN_EMBED_BASE_URL`, `CAIRN_EMBED_SERVER_MODEL`,
`CAIRN_EMBED_API_KEY`, `CAIRN_EMBED_TIMEOUT`, `CAIRN_EMBED_SERVER_BATCH`,
`CAIRN_EMBED_MODEL_STAMP`) are tabled in
[Embedding server backends](#embedding-server-backends) below.

**Operations & telemetry**

| Var | Effect |
|---|---|
| `CAIRN_TELEMETRY` | `off` disables emission entirely |
| `CAIRN_OTEL_ENDPOINT` | opt-in synchronous OTLP log export |
| `CAIRN_SESSION` | session id for tool metrics |
| `CAIRN_LOG_LEVEL` / `CAIRN_LOGGER_NAME` | logging |
| `CAIRN_READ_ONLY` | read-only mode |
| `CAIRN_CONN_POOL` | pooled SQLite connections (default 1) |
| `CAIRN_MAX_RESULT_CHARS` | response truncation threshold |
| `CAIRN_TOOL_METRICS_MAX_AGE_SECONDS` / `_MAX_ROWS` | retention |

## Embedding server backends

`CAIRN_EMBED_BACKEND=server` — or the presets `omlx` / `ollama` — embeds all
three corpora (code, knowledge, memory) through any OpenAI-compatible
`/v1/embeddings` server already running on the machine: oMLX, Ollama, LM
Studio, llama.cpp, vLLM. No torch install and no in-process model: one
shared server load serves cairn, editors, and other agents. The presets
differ only in default base URL; bare `server` requires
`CAIRN_EMBED_BASE_URL`.

| Var | Meaning | Default |
|---|---|---|
| `CAIRN_EMBED_BACKEND` | `server` / `omlx` / `ollama` (alongside `local`/`hash`/`openai`) | `local` |
| `CAIRN_EMBED_BASE_URL` | base URL ending in `/v1` | preset per backend; required for bare `server` |
| `CAIRN_EMBED_SERVER_MODEL` | model id sent in requests | `bge-m3` |
| `CAIRN_EMBED_API_KEY` | optional bearer token | none |
| `CAIRN_EMBED_TIMEOUT` | per-request timeout (seconds) | `30` |
| `CAIRN_EMBED_SERVER_BATCH` | max inputs per HTTP request | `32` |
| `CAIRN_EMBED_MODEL_STAMP` | override the derived model stamp (migration alias) | derived |

Presets: `omlx` → `http://127.0.0.1:8000/v1`, `ollama` →
`http://127.0.0.1:11434/v1`. Any other OpenAI-compatible server works via
`CAIRN_EMBED_BACKEND=server` plus `CAIRN_EMBED_BASE_URL` (LM Studio serves
`:1234/v1`; llama.cpp server and vLLM expose the same endpoint shape).

Requests are batched (`CAIRN_EMBED_SERVER_BATCH` inputs per POST) and
retried on connection errors, timeouts, 5xx, and 429 — up to 3 retries with
jittered exponential backoff (0.5/1/2 s). Other 4xx fail immediately with
the server's error message verbatim (oMLX's `not_found_error` lists the ids
it does serve), and a batch returning mixed dimensions is rejected.

### Availability probe

A server backend is usable only while `GET {base}/models` (2 s timeout,
bearer header when `CAIRN_EMBED_API_KEY` is set) returns 200 **and** the
listing includes the configured model id. The verdict is cached per
process; `cairn doctor` and backend changes force a re-probe. A failing
probe never silently swaps in another producer — how cairn degrades is the
fallback ladder documented in [retrieval.md](retrieval.md).

### `~/.cairn/config.json`

Every `CAIRN_EMBED_*` knob can also live in `$CAIRN_HOME/config.json`
(sibling of `workspaces.json`) as a flat JSON object whose keys mirror the
env-var names:

```json
{
  "CAIRN_EMBED_BACKEND": "omlx",
  "CAIRN_EMBED_SERVER_MODEL": "bge-m3"
}
```

Precedence is env > file > default — a set-but-blank env var falls through
to the file (env values are stripped, so blanks never shadow it) — and the
file is re-read when its mtime changes, so edits reach running processes
without a restart. The dashboard
Settings page persists values here — including the API key, which is
write-only in the UI and never rendered back. Base-URL changes in the UI
require an explicit confirm step. The Embeddings status view
(`/embeddings`) shows each knob's effective value and its source
(env / file / default) alongside probe health and the active fallback rung.

### Privacy

Embedding input is code text: with a server backend, the text of your
symbols is sent to the configured URL. Localhost is the default; pointing
`CAIRN_EMBED_BASE_URL` at a remote host sends your code there — an explicit
user choice, under the same trust model as the existing `openai` backend.

### oMLX setup (one-time)

oMLX's embedding engine loads safetensors only, and the `BAAI/bge-m3` HF
repo ships only `pytorch_model.bin` — one local conversion is needed. Any
environment with `torch`, `huggingface_hub`, and `safetensors` works (the
`[semantic]` extra installs all three):

```bash
python - <<'EOF'
from pathlib import Path
import torch
from huggingface_hub import snapshot_download
from safetensors.torch import save_file
d = Path.home() / ".omlx/models/BAAI/bge-m3"
snapshot_download("BAAI/bge-m3", local_dir=d)
sd = torch.load(d / "pytorch_model.bin", map_location="cpu", weights_only=True)
save_file({k: v.contiguous() for k, v in sd.items()},
          d / "model.safetensors", metadata={"format": "pt"})
EOF

omlx restart   # discovers ~/.omlx/models/BAAI/bge-m3, serves it as id "bge-m3"
```

Notes:

- The served model id is the model-directory **basename** (`bge-m3`), not
  the HF path — that is the id to put in `CAIRN_EMBED_SERVER_MODEL`.
- oMLX installs may require an API key (`~/.omlx/settings.json` →
  `auth.api_key`); put the same value in `CAIRN_EMBED_API_KEY`. Auth
  failures surface as OpenAI-shaped `authentication_error` messages.
- Verify with `curl -s http://127.0.0.1:8000/v1/models` (add
  `-H "Authorization: Bearer <key>"` when auth is on): the configured id
  must appear in the listing — that is exactly what the probe checks.

### Ollama setup

`ollama pull bge-m3` is the whole setup: the `ollama` preset supplies the
base URL (`http://127.0.0.1:11434/v1`), the model id is `bge-m3`, no auth.

With a server backend configured, `cairn doctor` checks the probe, the
model listing, a parity sample against stored rows, and latency; without
one it stays a single informational line.

## Install extras (`pip install cairn-intel[…]`)

| Extra | Adds | When you need it |
|---|---|---|
| *(core)* | 14 tree-sitter grammars, sqlite-vec, numpy, click, mcp | graph + FTS5 search + dashboard out of the box |
| `semantic` | sentence-transformers | real embeddings + rerank (torch-based, large) |
| `ann` | sqlite-vec | explicit ANN install (already core since 0.14) |
| `ingest` | pymupdf4llm, mammoth, markdownify | PDF/DOCX ingestion |
| `scip` | protobuf | consuming pre-built SCIP indexes |
| `watch` | watchdog | live file watcher / MCP watch mode |
| `otlp` | opentelemetry sdk + OTLP exporter | `CAIRN_OTEL_ENDPOINT` export |
| `dev` | pytest, ruff, mypy, bandit, pip-audit, pre-commit, commitizen | contributing — CI installs only this extra, so optional deps in tests must use `importorskip` |

The default install is zero-network and torch-free; without `[semantic]`,
embeddings fall back to a deterministic hash backend and retrieval still
works (lexically-fused, weaker semantics — see the `HASH_FALLBACK` /
`SEMANTIC_BACKEND` events in `cairn doctor`). The other torch-free path is
an embedding server: with a server backend configured
([Embedding server backends](#embedding-server-backends)), vectors come
from the configured `/v1/embeddings` endpoint and `[semantic]` stays
unnecessary.
