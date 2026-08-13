# Configuration

cairn is configured through environment variables and an optional
per-workspace `cairn.json` file. All of them are optional: defaults are
tuned for small and medium repos, and the base install needs nothing set to
work. The knobs below matter mainly when you relocate the store, scale to a
large corpus, or enable semantic search.

This is the complete list of `CAIRN_*` variables, grouped by purpose. Every
entry reflects current source behavior (verified in `src/`). Workspace-file
options (`exclude` / `include` / `repo_namespaces`) are documented at the end.

> **A bad config never breaks the build.** Malformed JSON or wrong-typed values
> in `cairn.json` are ignored with a stderr warning, never raised.

> **Defaults are sane for small/medium repos.** The semantic-search and ANN
> variables only matter at scale or when you have installed the `[semantic]` /
> `[ann]` extras. Leave them unset for a zero-network, torch-free install.

## Storage paths

cairn keeps one store per workspace under `~/.cairn/<key>/`, where
`<key>` is a short hash of the workspace root. A store contains a `.kg` SQLite
database and a `.knowledge/` markdown bundle. Resolution order is
`CAIRN_WORKSPACE` (env) > registered ancestor (walked up from cwd) > cwd.

| Variable | Type / Default | Effect |
|----------|----------------|--------|
| `CAIRN_HOME` | path, default `~/.cairn` | Root holding all per-workspace stores and the `workspaces.json` registry. Override for tests, CI, or a shared volume. |
| `CAIRN_DB` | path, default `<home>/<key>/.kg` | Hard override for the SQLite graph DB path. Used by the MCP server and tests to pin a store explicitly. |
| `CAIRN_KNOWLEDGE` | path, default `<home>/<key>/.knowledge` | Hard override for the OKF markdown bundle (compass, wiki, memory). |
| `CAIRN_LIB` | path, default `<CAIRN_HOME>/lib` | Shared library directory for the heavy semantic dependencies (torch, sentence-transformers, numpy). Installed once via `cairn embed --install-deps` using `pip install --target`, so they survive `uv tool install --force` reinstalls (which reset the tool's own venv). Prepended to `sys.path` at import time when it exists, so `import torch` / `import sentence_transformers` resolve from here, not the venv. |
| `CAIRN_BIN` | path, default unset | Path to the `cairn` executable; used by the SSE daemon lifecycle so spawned processes find the right binary. |
| `CAIRN_WORKSPACE` | absolute path, default unset | The workspace root. Highest-priority input to store resolution (overrides the ancestor walk). Set it when running `cairn` from outside the repo tree. |
| `CAIRN_SESSION` | string, default `"unknown"` | Session label attached to metric/usage telemetry buffers; useful to attribute server traffic to a session. |

## Server and runtime

| Variable | Type / Default | Effect |
|----------|----------------|--------|
| `CAIRN_LOG_LEVEL` | string, default `WARNING` | Log threshold for the `cairn` namespace logger. One of `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`, matched case-insensitively (`debug`, `Debug`, `DEBUG` all work). An invalid value falls back to `WARNING` with a single notice on stderr — never raised, so a typo can't break `cairn build` or server boot. The `-v`/`--verbose` CLI flag forces `DEBUG` and wins over this env var; it must precede the subcommand (`cairn -v build`, not `cairn build -v`), because click parses it as a group option — for position-independent control, set `CAIRN_LOG_LEVEL=DEBUG`. Records go to **stderr only** (stdout stays clean as the JSON-RPC channel under the stdio MCP transport), and only the `cairn` logger is configured — never the root logger. |
| `CAIRN_READ_ONLY` | `0`/`1`, default unset (writable) | When `1`/`true`/`yes`, the MCP server opens the DB read-only. Set automatically by the SSE daemon (the safe shared-instance mode) and by `cairn serve` when read-only is requested. |
| `CAIRN_WORKERS` | int, default = CPU count | Number of parallel parse/build workers. Honored by the builder; uncapped so you can raise it on big machines. |
| `CAIRN_MAX_RESULT_CHARS` | int, default `60000` | Cap on the character count of MCP tool results. Truncates oversized responses to keep agent context windows bounded. |

## Semantic search

These only take effect when the `[semantic]` extra is installed
(`pip install cairn-intel[semantic]`). Without it, semantic_search is unavailable
and the variables are inert. See also the `CAIRN_FUSION` note below.

| Variable | Type / Default | Effect |
|----------|----------------|--------|
| `CAIRN_FUSION` | `0`/`1`, default `1` | Reciprocal Rank Fusion of BM25 + vector scores. When on (default), the returned `score` is a fusion rank number (~0.01–0.02), not cosine similarity. Set to `0` to expose raw cosine scores. |
| `CAIRN_ANN_BACKEND` | string, default `sqlite-vec` | When unset or `sqlite-vec` (and the `sqlite-vec` package is importable), uses a native ANN index. Set to `off` (or any other value) to force the brute-force cosine scan. Any load failure degrades to brute force automatically. |
| `CAIRN_EMBED_BACKEND` | `local`/`openai`/`hash`, default `local` | Embedding provider. `local` uses sentence-transformers in-process (and silently falls back to `hash` when the package is missing); `openai` calls the OpenAI embeddings API; `hash` is a deterministic dep-free hash embedder (low quality, for tests/offline smoke checks). |
| `CAIRN_EMBED_LOCAL_MODEL` | string, default `BAAI/bge-m3` | HuggingFace model id for the `local` backend. |
| `CAIRN_EMBED_OPENAI_MODEL` | string, default `text-embedding-3-small` | Model name for the `openai` backend. |
| `CAIRN_EMBED_KNOWLEDGE_MODEL` | string, default unset | Optional separate model for embedding the `.knowledge/` markdown corpus (lets docs use a different model than code). Falls back to the main model. |
| `CAIRN_EMBED_FP16` | `0`/`1`, default unset | When `1`, loads the local model with `torch_dtype=float16` (halves GPU/CPU memory). |
| `CAIRN_EMBED_MAX_SEQ_LEN` | int, default `512` | Max sequence length passed to the local model. Raise for long functions; lower to trade recall for speed. |
| `CAIRN_EMBED_TRUST_REMOTE_CODE` | `0`/`1`, default unset | When `1`, sets `trust_remote_code=True` on model load — required by some custom-architecture models. |
| `CAIRN_RERANK` | `0`/`1`, default unset | When `1`/`true`/`on`, runs a CrossEncoder reranker over retrieval results. Also auto-enabled by a successful `cairn download-reranker` (writes a `~/.cairn/rerank_enabled` marker). Set to `0`/`false`/`off` to force it OFF (hard kill switch — wins over the marker). Needs the `[semantic]` extra (no separate install); if the configured model isn't cached, falls back to hybrid (vector + BM25 + RRF) order. |
| `CAIRN_RERANK_MODEL` | string, default `BAAI/bge-reranker-base` | CrossEncoder model used when reranking. The default is the natural pair for the `bge-m3` embedder (same BAAI family). |

### Disabling fusion to read real cosine scores

By default semantic_search returns **Reciprocal Rank Fusion** scores (BM25
blended with vector similarity). These are small numbers (~0.01–0.02) that
express **rank order only**, not match strength. They are not cosine similarity,
regardless of the `threshold` argument you pass.

That is usually what you want — rank order is what matters for "which results to
show." But when you need the score to reflect how strongly a hit actually matches
(for example, to decide how confident a hit is, or to threshold a batch),
set:

```bash
export CAIRN_FUSION=0
```

With fusion off, real cosine scores (0.3–0.6+ for genuinely on-topic hits using
`BAAI/bge-m3`) appear in the `score` field. Rank order stays meaningful either
way; only the interpretation of the number changes.

## LLM and task queue

cairn never calls an LLM directly. Instead it queues synthesis work
(compass/wiki generation) on a file-based task queue, which an external agent
claims and completes. See `cairn task list / show / claim / complete`.

| Variable | Type / Default | Effect |
|----------|----------------|--------|
| `CAIRN_LLM_BACKEND` | string, default `file-queue` | How LLM-driven commands route their work. `file-queue` (default) enqueues tasks for an external agent; a deterministic critic fact-checks every completed result before it is committed. Recognized by the compass and memory commands. |

## Parsing

| Variable | Type / Default | Effect |
|----------|----------------|--------|
| `CAIRN_CHUNK_VARIANT` | `A`/`B`, default `B` | How source is chunked before embedding. Variant `B` is the current default; override to `A` for the legacy chunker if you need to reproduce older embeddings. Changing this invalidates existing embeddings on the next `cairn embed`. |

## Workspace config file (`cairn.json`)

An optional JSON file at the workspace (or repo) root. Unknown keys are
ignored (forward-compatible). Recognized keys:

| Key | Type | Effect |
|-----|------|--------|
| `exclude` | list of gitignore globs | Patterns to skip during indexing, matched against repo-root-relative paths. Combined with the built-in skip set and `.gitignore`. |
| `include` | list of gitignore globs | Patterns to force-include, overriding `exclude` and the default skip set. Use to pull a checked-in vendored dir back into the graph. |
| `repo_namespaces` | object `prefix -> repo id` | Maps import-path prefixes to owning repo ids, used by `cairn deps` / `cross_repo_deps` to detect cross-repo links. When empty, falls back to the built-in default map and `CAIRN_REPO_NAMESPACES`. |
| `scip` | object `language -> index path` | Maps a language to a pre-built [SCIP](https://github.com/sourcegraph/scip) index file (relative to workspace root). At build time, tree-sitter still parses those files (for modifiers, body, inheritance, parent scope that SCIP can't emit); SCIP's compiler-grade call/reference edges are then **merged** onto the tree-sitter rows (`source='merged'`). When an indexer's symbol names don't line up with tree-sitter's (e.g. scip-swift's opaque USRs, where the merge rate is 0), the build reverts that language to pure-SCIP. A missing index file falls back to tree-sitter alone for that language. Requires the optional `[scip]` extra. Keys must match scanner language names exactly (lowercase: `kotlin`, `typescript`, `java`, `swift`, …) — an unrecognized key is warned about and its data is still imported (as standalone `source='scip'` rows) but won't merge with tree-sitter. If a known indexer (`scip-swift`, `scip-java`, `scip-typescript`) is on `PATH`, a missing index is auto-generated once before the fallback kicks in. See [scip.md](./scip.md). |

Example:

```json
{
  "exclude": ["static/", "**/vendor/**"],
  "include": ["vendor/lib/"],
  "repo_namespaces": {
    "com.example.sdk": "sdk",
    "com.example.core": "core",
    "com.example.billing": "billing-svc"
  },
  "scip": {
    "kotlin": "build/scip/kotlin.scip",
    "typescript": "build/scip/ts.scip",
    "swift": "build/scip/swift.scip"
  }
}
```

### Cross-repo namespaces in detail

`cross_repo_deps` maps an imported namespace to the repo that owns it. The map
is resolved once per process, in priority order:

1. **`CAIRN_REPO_NAMESPACES`** (env var) — a JSON object of `prefix -> repo
   id`. Highest priority; overrides everything. Useful for CI or one-off runs.
2. **`repo_namespaces`** in `cairn.json` — the documented, version-friendly
   way to configure a workspace.
3. **Built-in default map** — a small set of prefixes from the reference
   workspace. Used silently when nothing else is set, so existing setups keep
   working.

`cairn config` prints the resolved map and which source it came from. A malformed
env var or config value is ignored with a stderr warning — never raised.

---

If you are unsure what is in effect for your workspace, run:

```bash
cairn config            # shows resolved paths + the active repo_namespaces map
cairn config --list     # shows the registry of all registered workspaces
```

It prints the resolved home, db, knowledge paths, and registry entries, so you
can confirm where data actually lives before changing any of the above.
