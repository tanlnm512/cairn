# Quickstart

cairn is a local, structural code intelligence system: it parses your source
with tree-sitter, builds a call-graph in SQLite, and exposes the result to you
and to AI agents through a CLI (`cairn`) and an MCP server. It runs entirely
on your machine and never calls an LLM by itself.

This guide gets you from zero to querying a real repo in a few minutes.

## Prerequisites

- **Python 3.10+** (3.10/3.11/3.12/3.13/3.14 supported).
- Optionally **[uv](https://docs.astral.sh/uv/)** for fast, isolated installs.
  All examples below use plain `pip`; swap in `uv pip` if you prefer.

## Install

```bash
pip install cairn-intel
```

That pulls the core: tree-sitter, parsers for 14 languages (Kotlin, Java, Python,
Swift, TypeScript, JavaScript, Dart, Objective-C, Go, PHP, Ruby, C#, C, C++), the
SQLite
store, and the MCP server. It is zero-network and torch-free by default.

Optional extras, for when you need them:

```bash
pip install cairn-intel[semantic]   # sentence-transformers + numpy: embeddings + reranking
pip install cairn-intel[ann]        # native ANN index (sqlite-vec) for large corpora
pip install cairn-intel[watch]      # watchdog: file-watch hooks for live re-indexing
```

`[semantic]` is heavy (torch, hundreds of MB) — skip it unless you want
`semantic_search` / reranking. The base install is plenty for structural queries.

To turn on reranking (one command downloads `BAAI/bge-reranker-base`, the
natural pair for the bge-m3 embedder, and enables it for subsequent queries):

```bash
cairn download-reranker                  # ~1.1GB one-time download + auto-enable
cairn semantic "how does auth work"      # now reranks the top results
```

Set `CAIRN_RERANK=0` to turn it back off; `--model` picks a different
CrossEncoder. If the model is missing/evicted, queries fall back to the hybrid
(vector + BM25 + RRF) order rather than failing.

Verify it landed:

```bash
cairn --help
```

## Upgrading

cairn can update itself in place — it detects how it was installed
(`uv tool`, `pipx`, or `pip`) and re-installs the latest version from PyPI:

```bash
cairn upgrade          # update to the latest published version
cairn upgrade --check  # only check what's latest, don't change anything
cairn version          # print the installed version
```

## Build the graph

From your repository root (or any subdirectory of it):

```bash
cairn build     # first run: parse every file and build the full graph
```

`cairn build` parses the whole workspace from scratch. After the first build,
use `cairn update` to reindex only what changed (it diffs against `git HEAD`
plus the existing graph, so it is fast and incremental). On a fresh clone with a
clean working tree, `cairn update` would see no changes — start with `cairn
build`, then switch to `cairn update` for day-to-day edits.

The store lives under `~/.cairn/<workspace-key>/` by default — a `.kg`
SQLite database plus a `.knowledge/` bundle of markdown. See
[configuration.md](configuration.md) for how to relocate it.

## Query the graph

```bash
cairn def SomeSymbol              # where is SomeSymbol defined?
cairn callers SomeSymbol          # who calls it (precise edges only)?
cairn callers SomeSymbol --fuzzy  # include name-only matches (candidate list)
cairn callees SomeSymbol          # what does it call?
cairn impact SomeSymbol           # recursive blast radius if it changes
cairn deps my-repo                # cross-repo dependency map
cairn search "*Service*"          # symbol search (supports * wildcards)
```

A natural-language question across all layers (graph + compass + wiki + memory):

```bash
cairn ask "how does ApiFactory create clients"
```

> **Note:** the `explore` command listed in some older docs is an MCP-only tool.
> From the CLI, `cairn ask` is the equivalent entry point that routes across layers.

### Precise vs fuzzy — read this once

`callers`, `callees`, and `impact` default to **precise**: they follow only edges
the resolver pinned to exactly one definition. This is ground truth for blast
radius and refactoring. An empty precise result means "no *resolvable* callers,"
not "unused" — retry with `--fuzzy` before concluding a symbol is dead code.

Fuzzy adds name-only matches: a candidate list, not truth. Verify each hit
against the actual source. Use fuzzy for auditing and dead-code hunting; use
precise for impact analysis and signature changes.

See [architecture.md](architecture.md) → "Resolution model" for the full story.

## Wire it to an AI agent

cairn ships an MCP server (`cairn serve`). To connect a client like Claude
Desktop, Cursor, or ZCode, add it to the client's MCP config:

```json
{
  "mcpServers": {
    "cairn": {
      "command": "cairn",
      "args": ["serve"]
    }
  }
}
```

The server exposes 27 tools across 5 layers (graph, compass + knowledge base +
router, memory, knowledge). The aggregator tool `explore` is the recommended
first call for almost any question — it fans a query across the graph layer and
returns one consolidated answer in a single round trip.

For one-shot setup across all detected clients (writes MCP configs, skills,
slash commands, rules, and hooks):

```bash
cairn install-agents                    # interactive: pick clients + scope
cairn install-agents --yes              # auto-install all detected, default scope
cairn install-agents --scope global     # write to ~/.claude/, ~/.cursor/, etc.
```

It auto-detects installed clients and transport (SSE daemon vs stdio). Use
`--client cursor --client claude` to target specific ones, or `--dry-run` to
preview. To remove everything later: `cairn uninstall-agents`.

## Next steps

- [cli-reference.md](cli-reference.md) — every `cairn` command and flag.
- [mcp-tools.md](mcp-tools.md) — the 27 MCP tools, grouped by layer.
- [configuration.md](configuration.md) — env vars for paths, semantic search,
  workers, and the LLM task queue.
- [architecture.md](architecture.md) — the 5-layer design, resolution model,
  the LLM boundary, and where data lives on disk.
