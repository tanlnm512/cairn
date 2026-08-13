# cairn

> Verifiable codebase memory for AI agents: a structural graph + compass + wiki + tribal memory, all local, all traceable to source.

[![PyPI version](https://img.shields.io/pypi/v/cairn-intel.svg)](https://pypi.org/project/cairn-intel/)
[![License: MIT](https://img.shields.io/pypi/l/cairn-intel.svg)](LICENSE)
[![Python versions](https://img.shields.io/pypi/pyversions/cairn-intel.svg)](https://pypi.org/project/cairn-intel/)
[![CI](https://img.shields.io/github/actions/workflow/status/tanlnm512/cairn/ci.yml?branch=main&label=CI)](https://github.com/tanlnm512/cairn/actions/workflows/ci.yml)

cairn is the **verifiable memory of your codebase for AI agents.** It parses
your repos with tree-sitter into a precise structural graph (symbols, call
edges, blast radius) and fuses it with code-grounded tribal memory — all in a
local SQLite store, all behind one MCP server (27 tools) + a `cairn` CLI. The
product is a **verification contract**: every `exact` edge is actually resolved,
every symbol in a compass/wiki/memory doc is graph-verified by a deterministic
critic, and the LLM is never in the query path. No network call, no torch in
the default install.

## What is cairn?

cairn is a **local, verifiable, agent-first** codebase memory system. It parses
your repos with tree-sitter into a **structural graph** (definitions, call
edges, cross-repo dependencies) stored in SQLite, then layers a **compass**
(per-module navigation guides), a **wiki** (architecture docs), **memory**
(decisions / patterns / mistakes / workarounds), and a **knowledge** store on
top. It is **MCP-native**: the same store backs the `cairn` CLI and a 27-tool
MCP server, making it **agent-first** — your coding agents query one local
source of truth instead of re-reading the whole repo every turn.

## Why cairn? The verification contract

A code graph alone is commoditized — several tools now index symbols and call
edges. Generic agent memory is ungrounded — it lets an LLM silently rewrite
what it "remembers." cairn is the narrow intersection: a structural graph
**fused with code-grounded memory, where every output is traceable to source
and every synthesized doc is fact-checked before it lands.** The product is a
**verification contract** — three promises cairn can machine-check:

1. **Every `exact` edge is actually resolved.** The resolver pins each edge to
   one definition before labeling it `exact` (`target_id IS NOT NULL`); an
   invariant test guards this on every build.
2. **Every symbol in a compass / wiki / memory doc exists in the graph.** A
   deterministic critic fact-checks every LLM-synthesized doc against the graph
   before it is written; hallucinated references are rejected or flagged.
3. **Every answer is re-derivable from local data.** cairn never calls an LLM
   in the query path — the LLM stays on a task queue with a critic gate, so
   outputs are verifiable, not probabilistic.

The five layers (graph + compass + memory + knowledge + wiki) are how the
contract is delivered; resolution-labeled edges are the *evidence* for promise
#1, not the headline.

### Resolution-labeled edges (evidence for promise #1)

Every code graph can tell you "who calls this." cairn tells you **whether to
trust the answer.** The resolver labels each call edge:

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

This is measurable: see [docs/methodology-precise-vs-fuzzy.md](docs/methodology-precise-vs-fuzzy.md)
for the false-positive methodology and measured numbers (82% of fuzzy results
for common names are name-collision noise that precise mode excludes), and
[docs/benchmarks.md](docs/benchmarks.md#the-resolution-label-methodology-cairns-differentiator)
for the harness. For a worked example, see
[docs/examples/resolution-walkthrough.md](docs/examples/resolution-walkthrough.md).
Full design at [docs/architecture.md § Resolution model](docs/architecture.md#resolution-model).

## Quick start

```bash
pip install cairn-intel              # install from PyPI (the recommended path)
cairn build                         # parse the workspace and build the graph (first run)
cairn update                        # incremental reindex after the first build
cairn def SomeSymbol                # find where a symbol is defined
cairn impact SomeSymbol             # within-repo blast radius (precise by default; --fuzzy to audit)
cairn ask "how does auth work"      # natural-language query across all layers
```

The graph lives under `~/.cairn` by default (override with `CAIRN_HOME`).

> **First run vs later runs.** `cairn build` parses every file from scratch.
> `cairn update` reindexes only what changed since the last build (via `git diff
> HEAD` plus the existing graph) — so on a fresh clone with a clean working tree,
> use `cairn build` first, since `cairn update` would see no changes.

### Try it on cairn itself (the verification contract, demonstrated)

cairn indexes its own source as a dogfood. Clone this repo and run the exact
commands above — then verify the contract holds on the verifier's own code:

```bash
git clone https://github.com/tanlnm512/cairn && cd cairn
cairn build                                     # ~4s; builds ~1,900 symbols / ~11,500 edges
cairn def build_graph                           # -> src/cairn/graph/builder.py
cairn impact build_graph                        # -> real transitive callers (non-empty)
# Promise #1, checked directly: no exact edge has a NULL target_id.
sqlite3 "$(cairn config --db)" \
  "SELECT COUNT(*) FROM edges WHERE resolution='exact' AND target_id IS NULL"   # -> 0
```

The `-m core` test suite runs this same build + invariant check in CI
(`tests/test_self_demo.py`), so the dogfood cannot silently rot.

## Upgrading

cairn can update itself in place — it detects how it was installed
(`uv tool`, `pipx`, or `pip`) and re-installs the latest version from PyPI:

```bash
cairn upgrade          # update to the latest published version
cairn upgrade --check  # only check what's latest, don't change anything
cairn version          # print the installed version
```

If PyPI is unreachable, `cairn upgrade` prints the manual command instead.

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
cairn install-agents --client claude --client cursor  # force specific clients
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

Kotlin, Java, Python, Swift, TypeScript, JavaScript, Dart, Objective-C, Go, PHP, Ruby, C#, C, C++

## Optional features

The default install is dependency-light and network-free. Opt in with extras:

| Extra | Adds | Key env var |
|-------|------|-------------|
| `[semantic]` | `sentence-transformers` + `numpy` — real embeddings and CrossEncoder reranking | reranking auto-enables after `cairn download-reranker` (default model `BAAI/bge-reranker-base`); `CAIRN_RERANK=1`/`=0` to override; fusion governed by `CAIRN_FUSION` (default on) |
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
| `cairn build` | Parse the workspace and build the graph (full; first run) |
| `cairn update` | Incremental reindex of changed files (after the first build) |
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
| `cairn upgrade` | Update cairn in place from PyPI (detects install method; `--check` to preview) |
| `cairn bench` | Performance / scalability benchmarks (`--save`/`--compare` for regression checks) |

## Development

```bash
pip install -e ".[dev]"   # pytest + watchdog + build
pytest -m core            # fast <3s smoke subset (one test per core function)
```

The `-m core` suite includes a **"cairn on cairn" self-demo**
(`tests/test_self_demo.py`): cairn indexes its own source tree in an isolated
temp DB and asserts the core query commands return correct results for known
symbols — and that the resolution invariant (every `exact` edge has a non-null
`target_id`) holds on cairn's own code. It is the strongest dogfood: the
verification contract, demonstrated on the verifier.

## Semantic search

The default install is network- and torch-free. Semantic search deps
(torch + sentence-transformers) are a one-time separate download that
persists in `~/.cairn/lib/` (survives reinstalls):

```bash
cairn embed --install-deps    # one-time: downloads bge-m3 (~836 MB)
cairn embed                   # builds the embedding index
```

## Development

cairn is developed on GitHub and released to PyPI. The recommended install
for end users is `pip install cairn-intel` (see Quick start above). The
following is for contributors only:

```bash
pip install -e ".[dev]"   # editable install: pytest + watchdog + build + ruff
pytest -m core            # fast <3s smoke subset (one test per core function)
pytest                    # full suite (the CI path)
make dist                 # build wheel + sdist into dist/ (for releases)
```

Releases are cut by tagging `vX.Y.Z` — see the tag-triggered workflow in
`.github/workflows/release.yml` and the pre-release checklist in
`docs/release-checklist.md`.

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

**Beta — pre-1.0 (v0.9.1).** Public surfaces (CLI flags, MCP tool shapes,
knowledge-file layout) may still shift before 1.0. Feedback welcome via
[GitHub issues](https://github.com/tanlnm512/cairn/issues).

## License

[MIT](LICENSE) — © 2025–2026 Tan Le
