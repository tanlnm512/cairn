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
`CAIRN_EMBED_FP`, `CAIRN_EMBED_MAX_SEQ_LEN`,
`CAIRN_EMBED_TRUST_REMOTE_CODE`, `CAIRN_WARM_MODELS`, `CAIRN_CHUNK_VARIANT`.

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
`SEMANTIC_BACKEND` events in `cairn doctor`).
