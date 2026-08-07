# cairn

> Local codebase intelligence system: structural graph + compass + wiki + agent memory.

[![PyPI version](https://img.shields.io/pypi/v/cairn-intel.svg)](https://pypi.org/project/cairn-intel/)
[![License: MIT](https://img.shields.io/pypi/l/cairn-intel.svg)](LICENSE)
[![Python versions](https://img.shields.io/pypi/pyversions/cairn-intel.svg)](https://pypi.org/project/cairn-intel/)
[![CI](https://img.shields.io/github/actions/workflow/status/tanlnm512/cairn/ci.yml?branch=main&label=CI)](https://github.com/tanlnm512/cairn/actions/workflows/ci.yml)

cairn builds a precise, language-aware structural graph of your codebase and
exposes it to both humans (the `cairn` CLI) and AI agents (a stdio MCP server with
27 tools). Symbols, call edges, definitions, blast radius, and tribal memory all
live in a local SQLite store — no network call, no torch in the default install.

## What is cairn?

cairn is a **local** codebase intelligence system. It parses your repos with
tree-sitter into a **structural graph** (definitions, call edges, cross-repo
dependencies) stored in SQLite, then layers a **compass** (per-module navigation
guides), a **wiki** (architecture docs), **memory** (decisions / patterns /
mistakes / workarounds), and a **knowledge** store on top. It is **MCP-native**:
the same store backs the `cairn` CLI and a 27-tool MCP server, making it
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
cairn impact invoke              # precise (default): real callers only — ground truth
cairn impact invoke --fuzzy      # candidate list (name matches), each labelled unverified
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
cairn update                       # parse the workspace and build the graph
cairn def SomeSymbol               # find where a symbol is defined
cairn ask "how does auth work"     # natural-language query across all layers
```

The graph lives under `~/.cairn` by default (override with `CAIRN_HOME`).

## Install for AI agents

cairn ships a stdio MCP server (`cairn serve`). To wire it into your AI coding
clients (Claude Code, Cursor, Droid, ZCode, Claude Desktop, agy, opencode):

```bash
cairn install-agents
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
cairn install-agents --yes                          # auto-install detected-not-installed
cairn install-agents --client claude,cursor         # force specific clients
cairn install-agents --scope global                 # write to ~/.claude/ etc.
cairn install-agents --force                        # overwrite existing files
cairn install-agents --dry-run                      # preview without writing
```

Or wire manually — the MCP config is:

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
| `[scip]` | `protobuf` — consume pre-built [SCIP](docs/scip.md) indexes for compiler-grade exact call edges (Kotlin/Java/Swift/TypeScript) alongside tree-sitter | declare indexes in `cairn.json` under `scip` |
| `[watch]` | `watchdog` — live graph rebuilds on filesystem change | — |

## Architecture (5 layers)

The MCP server exposes 27 tools across five layers:

| Layer | Purpose |
|-------|---------|
| **graph** (9 tools) | Structural graph: `find_definition`, `get_callers` / `get_callees`, `impact_analysis`, `cross_repo_deps`, `semantic_search`, `search_symbols`, `explore` (the graph aggregator and recommended first call), and `visualize_graph` |
| **compass + knowledge base** (5 tools) | `get_compass`, `search_knowledge`, `ask_compass` (cross-layer router), `trace_flow`, `generate_flow` |
| **memory** (8 tools) | Tribal memory: recall / record / lifecycle (promote, demote, evolve, decay, delete, digest) |
| **knowledge** (5 tools) | The OKF knowledge store — add / search / status business docs and workflows |

## CLI

The `cairn` command groups the main functionality. Run `cairn --help`
(or `cairn <group> --help`) for the authoritative, full list.

| Command | What it does |
|---------|--------------|
| `cairn serve` | Run the stdio MCP server |
| `cairn update` | Parse the workspace and rebuild the graph |
| `cairn def <symbol>` | Find a symbol's definition |
| `cairn impact <symbol>` | Within-repo blast radius (precise by default; `--fuzzy` to audit) |
| `cairn ask "<question>"` | Natural-language query routed across all layers |
| `cairn context <file>` | Load compass + memory + wiki context for a file |
| `cairn memory …` | Record / list / search tribal memory |
| `cairn task …` | Optional LLM task queue (`list` / `show` / `claim` / `complete`) |
| `cairn knowledge …` | Inspect and export the knowledge store |
| `cairn compass …` | Generate / list / validate module compass guides |
| `cairn wiki …` | Generate / search the architecture wiki |
| `cairn install-agents` | Drop integration files into supported AI agents |
| `cairn bench` | Performance / scalability benchmarks (`--save`/`--compare` for regression checks) |

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
cairn embed --install-deps    # one-time: downloads bge-m3 (~836 MB)
cairn embed                   # builds the embedding index
```

**Bootstrap script** (install + wire agents in one shot):

```bash
./scripts/install.sh                       # install cairn + interactively pick agents
./scripts/install.sh --agents all          # wire all detected clients
./scripts/install.sh --scope global        # write agent configs to ~/.claude/ etc.
./scripts/install.sh --no-agents           # install cairn only, skip agent wiring
```

The full suite (no marker) is the CI path.

## Dependency licenses

cairn is MIT-licensed. Its dependencies are all permissive (MIT, BSD,
Apache-2.0, MPL-2.0, PSF); see [NOTICE](NOTICE) for the full list.

The optional `[semantic]` extra is not installed by default. If you opt into it
on Linux, `pip` resolves `torch` and its transitive NVIDIA CUDA runtime
packages, which carry their own licenses (torch: BSD; NVIDIA CUDA components:
NVIDIA EULA / Apache-2.0). The embedding model `BAAI/bge-m3` (MIT) is
downloaded on demand to `~/.cairn/lib/` and is not redistributed with cairn.
None of these are bundled with cairn — they are resolved and accepted by the
end user at install time.

## Status

**Beta — pre-1.0 (v0.5.3).** Public surfaces (CLI flags, MCP tool shapes,
knowledge-file layout) may still shift before 1.0. Feedback welcome via
[GitHub issues](https://github.com/tanlnm512/cairn/issues).

## License

[MIT](LICENSE) — © 2025–2026 Tan Le
