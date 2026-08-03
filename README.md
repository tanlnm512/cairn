# cairn

> Local codebase intelligence system: structural graph + compass + wiki + agent memory.

[![PyPI version](https://img.shields.io/pypi/v/cairn-intel.svg)](https://pypi.org/project/cairn-intel/)
[![License: MIT](https://img.shields.io/pypi/l/cairn-intel.svg)](LICENSE)
[![Python versions](https://img.shields.io/pypi/pyversions/cairn-intel.svg)](https://pypi.org/project/cairn-intel/)
[![CI](https://img.shields.io/github/actions/workflow/status/tanlnm512/cairn/ci.yml?branch=main&label=CI)](https://github.com/tanlnm512/cairn/actions/workflows/ci.yml)

cairn builds a precise, language-aware structural graph of your codebase and
exposes it to both humans (the `cg` CLI) and AI agents (a stdio MCP server with
26 tools). Symbols, call edges, definitions, blast radius, and tribal memory all
live in a local SQLite store — no network call, no torch in the default install.

## What is cairn?

cairn is a **local** codebase intelligence system. It parses your repos with
tree-sitter into a **structural graph** (definitions, call edges, cross-repo
dependencies) stored in SQLite, then layers a **compass** (per-module navigation
guides), a **wiki** (architecture docs), **memory** (decisions / patterns /
mistakes / workarounds), and a **knowledge** store on top. It is **MCP-native**:
the same store backs the `cg` CLI and a 26-tool MCP server, making it
**agent-first** — your coding agents query one local source of truth instead of
re-reading the whole repo every turn.

## Why cairn? Resolution-labeled edges

Every code graph can tell you "who calls this." cairn is the one that tells
you **whether to trust the answer.** The resolver labels each call edge:

- **`exact`** — pinned to one definition. Trusted.
- **`ambiguous`** — multiple candidates; the resolver declined to guess.
- **`unresolved`** — external or stdlib.

Graph tools default to **precise mode** — they follow *only* `exact` edges. So
blast radius is **never inflated by name collisions**. A common name like
`invoke` can have hundreds of call sites across a polyglot repo that merely
share the name; precise mode returns only the real callers, while **fuzzy mode**
(`--fuzzy` / `fuzzy=True`) adds the name-only matches as an explicitly-labelled
candidate list to verify.

```bash
cg impact invoke              # precise (default): real callers only — ground truth
cg impact invoke --fuzzy      # candidate list (name matches), each labelled unverified
```

An empty precise result means "no *resolvable* callers," **not** "unused" —
retry with `--fuzzy` before concluding a symbol is dead. And `explore` surfaces
`ambiguous` dispatch hops — polymorphism that grep fundamentally cannot see.

This is measurable: see [docs/benchmarks.md](docs/benchmarks.md#the-resolution-label-methodology-cairns-differentiator)
for the precise-vs-fuzzy false-positive methodology, and
[docs/examples/resolution-walkthrough.md](docs/examples/resolution-walkthrough.md)
for a worked example. Full design at
[docs/architecture.md § Resolution model](docs/architecture.md#resolution-model).

## Quick start

```bash
pip install cairn-intel
cg update                       # parse the workspace and build the graph
cg def SomeSymbol               # find where a symbol is defined
cg ask "how does auth work"     # natural-language query across all layers
```

The graph lives under `~/.cairn` by default (override with `CAIRN_HOME`).

## Install for AI agents

cairn ships a stdio MCP server (`cg serve`). To wire it into your AI coding
clients (Claude Code, Cursor, Droid, ZCode, Claude Desktop, agy, opencode):

```bash
cg install-agents
```

This detects which clients are installed, shows whether cairn is already
wired in, and interactively prompts you to choose:

```
Client detection:
  [✓] claude           claude CLI on PATH              cairn: [ ] not installed
  [✓] cursor           Cursor.app in /Applications     cairn: [ ] not installed
  [✓] zcode            ~/.zcode exists                 cairn: [✓] installed

Install cairn for which clients?
Clients [claude,cursor]:

Config scope:
  workspace  — write to ./.claude/, ./.cursor/ etc. (per-project)
  global     — write to ~/.claude/, ~/.cursor/ etc. (all projects inherit)
Scope [workspace]:
```

**Scope:** `workspace` (default) writes configs to the current project dir
(`./.claude/`, `./.cursor/`); `global` writes to your home dir (`~/.claude/`,
`~/.cursor/`) so all projects inherit cairn without per-project setup.

Non-interactive flags for scripts/CI:

```bash
cg install-agents --yes                          # auto-install detected-not-installed
cg install-agents --client claude,cursor         # force specific clients
cg install-agents --scope global                 # write to ~/.claude/ etc.
cg install-agents --force                        # overwrite existing files
cg install-agents --dry-run                      # preview without writing
```

Or wire manually — the MCP config is:

```json
{
  "mcpServers": {
    "cairn": {
      "command": "cg",
      "args": ["serve"]
    }
  }
}
```

The recommended first call from an agent is `explore(query)`, which returns
matching symbols' verbatim source, the call paths between them, and a
blast-radius summary in a single round trip.

## Supported languages

Kotlin, Java, Python, Swift, TypeScript, JavaScript, Dart, Objective-C, Go

## Optional features

The default install is dependency-light and network-free. Opt in with extras:

| Extra | Adds | Key env var |
|-------|------|-------------|
| `[semantic]` | `sentence-transformers` + `numpy` — real embeddings and CrossEncoder reranking | `CAIRN_RERANK=1`; fusion is governed by `CAIRN_FUSION` (default on) |
| `[ann]` | `sqlite-vec` — native approximate-nearest-neighbour index for large corpora | `CAIRN_ANN_BACKEND=sqlite-vec` |
| `[watch]` | `watchdog` — live graph rebuilds on filesystem change | — |

## Architecture (5 layers)

The MCP server exposes 26 tools across five layers:

| Layer | Purpose |
|-------|---------|
| **graph** (9 tools) | Structural graph: `find_definition`, `get_callers` / `get_callees`, `impact_analysis`, `cross_repo_deps`, `semantic_search`, `search_symbols`, `explore` (the graph aggregator and recommended first call), and `visualize_graph` |
| **compass + knowledge base** (5 tools) | `get_compass`, `search_knowledge`, `ask_compass` (cross-layer router), `trace_flow`, `generate_flow` |
| **memory** (7 tools) | Tribal memory: record / recall / search decisions, patterns, mistakes, workarounds |
| **knowledge** (5 tools) | The OKF knowledge store — add / search / status business docs and workflows |

## CLI

The `cg` command groups the main functionality. Run `cg --help`
(or `cg <group> --help`) for the authoritative, full list.

| Command | What it does |
|---------|--------------|
| `cg serve` | Run the stdio MCP server |
| `cg update` | Parse the workspace and rebuild the graph |
| `cg def <symbol>` | Find a symbol's definition |
| `cg impact <symbol>` | Within-repo blast radius (precise by default; `--fuzzy` to audit) |
| `cg ask "<question>"` | Natural-language query routed across all layers |
| `cg context <file>` | Load compass + memory + wiki context for a file |
| `cg memory …` | Record / list / search tribal memory |
| `cg task …` | Optional LLM task queue (`list` / `show` / `claim` / `complete`) |
| `cg knowledge …` | Inspect and export the knowledge store |
| `cg compass …` | Generate / list / validate module compass guides |
| `cg wiki …` | Generate / search the architecture wiki |
| `cg install-agents` | Drop integration files into supported AI agents |
| `cg bench` | Performance / scalability benchmarks (`--save`/`--compare` for regression checks) |

## Development

```bash
pip install -e ".[dev]"   # pytest + watchdog + build
pytest -m core            # fast <3s smoke subset (one test per core function)
```

## Build & distribute

Build a wheel + sdist for distribution:

```bash
make dist        # → dist/cairn_intel-<version>-py3-none-any.whl
```

Hand the `.whl` file to a teammate or upload to PyPI. Install from a wheel:

```bash
uv tool install ./cairn_intel-<version>-py3-none-any.whl
```

Semantic search deps (torch + sentence-transformers) are a one-time separate
download that persists in `~/.cairn/lib/` (survives reinstalls):

```bash
cg embed --install-deps    # one-time: downloads bge-m3 (~836 MB)
cg embed                   # builds the embedding index
```

**Bootstrap script** (install + wire agents in one shot):

```bash
./scripts/install.sh                       # install cg + interactively pick agents
./scripts/install.sh --agents all          # wire all detected clients
./scripts/install.sh --scope global        # write agent configs to ~/.claude/ etc.
./scripts/install.sh --no-agents           # install cg only, skip agent wiring
```

The full suite (no marker) is the CI path.

## Status

**Beta — pre-1.0 (v0.5.3).** Public surfaces (CLI flags, MCP tool shapes,
knowledge-file layout) may still shift before 1.0. Feedback welcome via
[GitHub issues](https://github.com/tanlnm512/cairn/issues).

## License

[MIT](LICENSE) — © 2025 Tan Le
