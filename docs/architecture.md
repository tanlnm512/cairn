# Architecture

← [README](../README.md)

Read this when you need the system shape: what the pieces are, where state
lives, and which crate owns which job.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="diagrams/readme-architecture-dark.png">
  <img src="diagrams/readme-architecture.png" alt="cairn architecture: AI agents query the MCP server and CLI, which read one local SQLite store; an LLM task queue runs off-path, admitted only through a critic gate">
</picture>

## What cairn is

Cairn is a local codebase-intelligence system for AI coding agents, shipped
as one static `cairn` binary (`cargo binstall`, `cargo install`, or a release
tarball). It parses a workspace into a SQLite symbol/call graph, layers
hybrid retrieval on top, and keeps durable knowledge (documents, memories,
compass/wiki guides) in an Open Knowledge Format (OKF) bundle. Everything is
local; no LLM is called in-process — LLM-quality synthesis runs through a
decoupled task queue.

## The three tiers

### Surfaces — how agents and humans reach cairn

| Surface | Crate | Facts |
|---|---|---|
| MCP server | `cairn-daemon` | Streamable HTTP daemon on `:9876` (one shared process, read-write default; `cairn connect` ensures it is running); exactly 26 tools; bearer-key middleware on non-loopback binds; `/healthz` payload consumed by the connect reuse-guard |
| CLI | `cairn-cli` | entry point `cairn`; the clap command tree in `cmds_*.rs`, documented in [cli-reference.md](cli-reference.md) |
| Dashboard | `cairn-cli` (`src/dashboard/` + `dashboard-assets/`, minijinja) | loopback-only `127.0.0.1:8765`; views use read-only SQLite connections, the Settings page persists the embedding knobs to `~/.cairn/config.json`; knowledge views — `/knowledge` catalog, `/knowledge/{family}/{slug}` detail with relationship panels, `/knowledge/graph` relationship canvas — render from the OKF bundle + derived `knowledge_edges` index |

### Core engines

| Engine | Crates | Job |
|---|---|---|
| Graph engine | `cairn-store` (schema + connection factory), `cairn-build` (scanner, rayon builder, resolver, incremental/graft, streaming closure, SCIP import), `cairn-parse` (per-language tree-sitter extraction) | scan → parse → resolve → persist the symbol/call graph; see [indexing.md](indexing.md) |
| Retrieval | `cairn-query` (`tools_search.rs`: FTS5/BM25 + dense vectors fused by RRF over `vec0` ANN tables; `tools_nav.rs`, `tools_flow.rs`, `tools_repo.rs`: definition lookup, callers/callees, impact, `explore`, `trace_flow`, taint, pack) | the hybrid query path; dense vectors come from the `hash` embedder or an OpenAI-compatible embeddings server — no local inference and no reranker ship in the binary |
| Knowledge & memory | `cairn-query` (`tools_km.rs`, `ingest.rs`, `tasks.rs`), dispatched by `cairn-cli` `cmds_knowledge.rs` | OKF document store, staged doc ingestion, tiered agent memory with bi-temporal validity and per-agent sharing, the LLM task queue |
| Compass & wiki | `cairn-query` (`tools_wiki.rs`, `tools_flow.rs`, `skillgen.rs`) | navigation guides, architecture pages, generated skills; LLM work runs via the task queue, fact-checked by a deterministic critic |

### Resolution tiers, Rust sources, and worktree behavior

- The resolver labels every call edge `exact` / `ambiguous` / `unresolved`
  (see [indexing.md](indexing.md)).
- Rust sources use the generic tree-sitter tier with a pinned grammar. It
  records definitions and spans, keeps call edges `unresolved`, and records
  `.rs` files as parser-unavailable skips when the grammar cannot load.
- `cairn.json` `include_nested_repos` defaults to false. When true, initialized
  submodules and nested child repositories are indexed once under
  parent-prefixed repo ids.

### Storage — where state lives

```
~/.cairn/                        (CAIRN_HOME overrides)
  workspaces.json                workspace abs path → store key registry
  config.json                    persisted knobs (embedding backend, written by the dashboard Settings page)
  <sha256(path)[:16]>/           one store per workspace
    .kg                          SQLite: graph + FTS5 + embeddings + telemetry
    .knowledge/                  OKF bundle: concepts as frontmatter markdown
```

Path resolution (`cairn-cli` `store_paths.rs`): the workspace is the explicit
`--workspace`, else `CAIRN_WORKSPACE`, else the nearest registered ancestor of
the cwd, else the cwd; `CAIRN_DB` / `CAIRN_KNOWLEDGE` override the store
files inside it, and `--db` wins over everything in-process.

## Module map

| Crate | Purpose |
|---|---|
| `cairn-store` | `.kg` schema DDL, connection factory (bundled SQLite + FTS5 + sqlite-vec), vec0 helpers, the build lock and staged-swap primitives, canonical dump for parity diffs (`conn.rs`, `schema.rs`, `vec0.rs`, `swap.rs`, `dump.rs`) |
| `cairn-query` | read path: FTS5 search, definition lookup, precise/fuzzy callers/callees, recursive impact, `explore` aggregation, flow/taint/pack/federation tools (`tools_*.rs`); the knowledge, memory, wiki, task-queue and skill layers; the `[ingest]` sidecar bridge (`sidecar.rs`) |
| `cairn-parse` | per-language tree-sitter symbol/edge extraction (15 languages; the Dart grammar is vendored and Kotlin is pinned to an upstream commit) |
| `cairn-build` | scanner, rayon builder, resolver, incremental/graft, streaming closure, embeddings build, SCIP import |
| `cairn-daemon` | streamable-HTTP MCP server, bearer auth, `/healthz`, the launchd/reuse lifecycle (`server.rs`, `auth.rs`, `healthz.rs`, `lifecycle.rs`) |
| `cairn-cli` | the `cairn` binary: clap command tree (`cmds_*.rs`), dashboard server, agent-install templates and hook runner |

`cairn viz --export FILE` writes one self-contained HTML file.

## Key data model facts

The `.kg` SQLite database (`rust/crates/cairn-store/src/schema.rs`) holds:

- **Core graph**: `repos`, `files`, `symbols`, `edges` (with
  `resolution` = `exact` / `ambiguous` / `unresolved`, plus the resolver
  signal columns `ref_name` / `receiver_type` / `call_arity`), `imports`.
- **Search**: `symbols_fts` (FTS5 over symbols, trigger-synced), `term_df`
  (IDF stats for query enrichment).
- **Vectors**: `embeddings`, `embeddings_mv` (multi-vector), plus
  `knowledge_embeddings`, `memory_embeddings`. ANN lives in `vec0` virtual
  tables (`vec_*`, `vecmv_*`) via the statically linked `sqlite-vec`
  extension, same file.
- **Derived**: `dataflow` (precomputed impact), `transitive_edges` (O(1)
  multi-hop calls), `rationale` (marker-comment records), `communities` /
  `symbol_communities` (written by the Louvain sidecar).
- **Knowledge relationships**: `knowledge_edges` (doc↔doc rows:
  `doc_id`, `related_id`, `relation`, `kind` = `extracted`/`inferred`/
  `derived`, `provenance`, `created_at` — supersede pairs store both
  directions) and `knowledge_doc_refs` (doc→code refs with
  `ref_kind` = `file`/`symbol` and a `verified` flag). Both are rebuilt
  from OKF frontmatter by `cairn knowledge rebuild`; the frontmatter is
  the durable record, these tables a rebuildable index.
- **Memory bookkeeping**: `memory_refs`, `memory_validity` (bi-temporal
  intervals), `agent_symbols` (per-agent sharing), `memory_failure_signatures`.
- **Ops/telemetry**: `build_runs`, `tool_metrics`, `events`, `skipped_files`,
  `parse_errors`, `pending_sync`, `repo_build_state`, `schema_meta`.

## Runtime model

- One daemon per machine serves every registered workspace store over
  loopback; `cairn connect` starts it on first dial and reuses a healthy one
  via `/healthz`. Each tool call opens its own SQLite connection — read-only
  for reads, writable for the write tools — so a read-only daemon
  (`--read-only`) cannot land writes.
- One writer at a time: builds, incremental writers and `sync` repair take
  the `build_lock` (flock, non-blocking); `cairn build --staging` writes to
  `<db>.tmp` and atomically swaps it in under that lock, carrying the
  telemetry tables across.
- Freshness: CLI read commands refresh drifted files before answering and
  defer with a stale-graph banner while a build holds the lock; `cairn
  update` (git-diff driven, also run by the git and agent hooks) is the
  incremental path. A `pending_sync` row surfaces as a staleness banner on
  results until the update lands.
- Telemetry is local and best-effort (`tool_metrics`, `events`);
  `CAIRN_TELEMETRY=off` disables it. Nothing leaves the machine.
