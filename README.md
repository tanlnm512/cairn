# cairn

> **Verifiable codebase memory for AI agents** — a structural graph + compass + wiki + tribal memory, all local, all traceable to source.

The precise call graph, without the guesswork · every answer re-derivable from local data · 100% local SQLite, no network calls · LLM never in the query path

[![PyPI version](https://img.shields.io/pypi/v/cairn-intel.svg)](https://pypi.org/project/cairn-intel/)
[![License: MIT](https://img.shields.io/pypi/l/cairn-intel.svg)](LICENSE)
[![Python versions](https://img.shields.io/pypi/pyversions/cairn-intel.svg)](https://pypi.org/project/cairn-intel/)
[![CI](https://img.shields.io/github/actions/workflow/status/tanlnm512/cairn/ci.yml?branch=main&label=CI)](https://github.com/tanlnm512/cairn/actions/workflows/ci.yml)

cairn parses your repos with tree-sitter into a **resolution-labeled structural
graph** (14 languages), fuses it with **code-grounded tribal memory**, compass,
and wiki, and serves all of it through one MCP server (27 tools) + a `cairn`
CLI. The product is a **verification contract**: every `exact` edge is actually
resolved, every symbol in a synthesized doc is graph-verified by a
deterministic critic, and the LLM never sits in the query path.

## Contents

- [Get Started](#get-started)
- [Language Support](#language-support)
- [Why cairn?](#why-cairn)
- [Key Features](#key-features)
- [How It Works](#how-it-works)
- [Measured Results](#measured-results)
- [CLI Reference](#cli-reference)
- [MCP Tools](#mcp-tools)
- [Configuration](#configuration)
- [Supported Platforms & Agents](#supported-platforms--agents)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Dependency Licenses](#dependency-licenses)
- [Status](#status) · [License](#license)

## Get Started

**1. Install the CLI**

```bash
pip install cairn-intel
```

Already installed? `cairn upgrade` updates in place (detects `uv tool` /
`pipx` / `pip`; `--check` previews without changing anything).

**2. Wire up your agent(s)**

```bash
cairn install-agents
```

Detects which AI clients you have (Claude Code, Cursor, ZCode, Droid, Claude
Desktop, opencode, agy), shows what's already wired, and prompts for scope:
`workspace` (per-project `./.claude/`, `./.cursor/` …) or `global`
(`~/.claude/` — every project inherits it). Non-interactive:
`cairn install-agents --yes --scope global`. Manual wiring is one JSON block:

```json
{ "mcpServers": { "cairn": { "command": "cairn", "args": ["serve"] } } }
```

**3. Build the graph**

```bash
cairn build          # parse the workspace, resolve edges (first run; ~4s on cairn itself)
```

**4. No more syncing**

```bash
cairn update         # incremental reindex — only what changed since the last build
```

With the optional `[watch]` extra, a running `cairn serve` watches the
workspace and reindexes edits within a ~2s debounce window — no restart, no
manual update. Pending files carry a staleness banner on results until the
update lands.

**Uninstall** — `cairn uninstall` removes agent wiring, hooks, and the graph
store; `--dry-run` previews the scope; `cairn uninstall-agents` only removes
client configs.

## Language Support

Fourteen languages, one uniform contract: definitions, call edges (labeled
`exact` / `ambiguous` / `unresolved`), references, and inheritance wherever the
grammar carries them. `.h` headers are sniffed to Objective-C / C++ / C.

| Language | Extensions | Status |
|----------|------------|--------|
| Kotlin | `.kt` | Full (tree-sitter) · **SCIP merge** via `scip-java` adds compiler-grade exact call edges |
| Java | `.java` | Full · **SCIP merge** via `scip-java` |
| Swift | `.swift` | Full · **SCIP coexistence** (opaque-USR quirk falls back to pure-SCIP cleanly — see [docs/scip.md](docs/scip.md)) |
| TypeScript | `.ts` `.tsx` `.mts` `.cts` | Full · SCIP merge via `scip-typescript` (auto-index capable) · JSX component refs tracked |
| JavaScript | `.js` `.jsx` `.mjs` `.cjs` | Full · React `<Comp/>` JSX refs tracked as references edges |
| Python | `.py` | Full · SCIP consume via `scip-python` |
| Go | `.go` | Full · SCIP consume via `scip-go` |
| Dart | `.dart` | Full |
| Objective-C | `.m` `.mm` | Full · `.h` sniffed |
| PHP | `.php` `.phtml` `.php3`-`5` | Full (pure-PHP grammar, no HTML mixing) |
| Ruby | `.rb` `.rbw` | Full |
| C# | `.cs` `.csx` | Full |
| C | `.c` | Full · `.h` sniffed |
| C++ | `.cpp` `.cc` `.cxx` `.hpp` | Full · `.h` sniffed |

Not indexed (yet): Vue / Svelte single-file components, CSS, HTML. SCIP
coexistence is opt-in per language via `cairn.json` — without an index,
tree-sitter alone carries the language. Details: [docs/scip.md](docs/scip.md).

## Why cairn?

A code graph alone is commoditized — several tools index symbols and call
edges. Generic agent memory is ungrounded — it lets an LLM silently rewrite
what it "remembers." cairn is the narrow intersection: a structural graph
**fused with code-grounded memory, where every output is traceable to source
and every synthesized doc is fact-checked before it lands.**

Three promises cairn can machine-check — and does, in CI, on cairn's own code:

1. **Every `exact` edge is actually resolved.** The resolver pins each edge to
   one definition before labeling it `exact` (`target_id IS NOT NULL`); an
   invariant test guards this on every build.
2. **Every symbol in a compass / wiki / memory doc exists in the graph.** A
   deterministic critic fact-checks every LLM-synthesized doc against the graph
   before it is written; hallucinated references are rejected or flagged.
3. **Every answer is re-derivable from local data.** cairn never calls an LLM
   in the query path — the LLM stays on a task queue behind the critic gate, so
   outputs are verifiable, not probabilistic.

**Resolution labels are the evidence for promise #1.** Every code graph can
tell you "who calls this." cairn tells you **whether to trust the answer**:
`exact` (pinned to one definition — trusted), `ambiguous` (multiple
candidates; the resolver declined to guess), `unresolved` (external / stdlib).
Graph tools default to precise mode — only `exact` edges — so blast radius is
never inflated by name collisions:

```bash
cairn impact invoke              # precise (default): real callers only — ground truth
cairn impact invoke --fuzzy      # candidate list (name matches), each labelled unverified
```

Measured on this repo: **82% of fuzzy results for common names are
name-collision noise that precise mode excludes**
([methodology](docs/methodology-precise-vs-fuzzy.md) ·
[worked example](docs/examples/resolution-walkthrough.md)). An empty precise
result means "no *resolvable* callers," not "unused" — retry with `--fuzzy`
before concluding a symbol is dead. And `explore` surfaces `ambiguous`
dispatch hops — polymorphism that grep fundamentally cannot see.

### Honest trade-offs

- **Semantic retrieval is opt-in and mid-band.** On the hand-verified ground
  truth, pooled recall/MRR sit at 0.4174 / 0.2862 — below the 0.50 / 0.33
  shipping targets, which is exactly why the semantic stack is an extra, not
  the default path; the structural tools carry the precision story, and the
  full sweep/k-fold evidence is published in
  [docs/benchmarks.md](docs/benchmarks.md).
- **The `[semantic]` extra is heavy.** Real embeddings pull
  sentence-transformers (+ torch on Linux); a one-time ~836 MB model download
  lives in `~/.cairn/lib/`. The default install is torch-free and network-free.
- **Synthesized docs need an LLM pass.** Compass/wiki generation runs through
  the task queue (`cairn task`) with the critic gate — deterministic, but it
  won't happen purely locally without any model access.

## Key Features

- **Surgical context** — `explore(query)` returns matching symbols' verbatim
  source, the call paths between them, and a blast-radius summary in one round
  trip. The recommended first call for agents.
- **Trust-labeled blast radius** — precise mode follows only resolved edges;
  `--fuzzy` gives an explicitly-labelled candidate list for auditing and
  dead-code hunts.
- **Cross-repo reach** — `cross_repo_deps(repo)` maps who consumes your public
  API across registered repos; `impact_analysis` reports consumer reach.
- **Code-grounded memory** — decisions / patterns / mistakes / workarounds,
  symbol-keyed, recalled alongside graph results; promotion and decay
  lifecycles keep it honest.
- **Always fresh** — incremental `cairn update` (git-diff-driven), optional
  live watch with staleness banners, and `cairn doctor`'s 8 health checks
  gating CI.
- **100% local** — one SQLite store under `~/.cairn`; no network calls, no
  telemetry egress (OTLP export is opt-in and best-effort).
- **Agent-first surfaces** — the same store backs 27 MCP tools and the CLI;
  `cairn install-agents` wires every detected client in one command.
- **Local dashboard** — `cairn dashboard` opens a read-only web console at
  `127.0.0.1:8765`: interactive graph with symbol search, recorded
  tool-call history and token usage (MCP + CLI, mode-labeled estimates),
  session chains, health/memory panels, machine-wide workspace switching,
  and CSV/JSON export of any filtered view.

## How It Works

```text
 ┌─────────────────────────── your AI agents ───────────────────────────┐
 │   Claude Code · Cursor · ZCode · Droid · opencode · Claude Desktop   │
 └──────────────┬────────────────────────────────────▲──────────────────┘
        MCP (stdio) · 27 tools                      │  results: verbatim source,
                ▼                                   │  call paths, blast radius
        ┌────────────────────────┐        ┌─────────┴────────┐
        │    cairn MCP server    │◀──────▶│    cairn CLI     │
        └───────────┬────────────┘        └──────────────────┘
                    ▼
 ┌────────────────────── one local SQLite store (~/.cairn) ─────────────┐
 │ graph: symbols + exact/ambiguous/unresolved edges · compass + wiki   │
 │ tribal memory · knowledge (OKF) · embeddings (optional [semantic])   │
 └─────────────────────────────────▲────────────────────────────────────┘
                                   │ candidate docs only — critic gate
                     LLM task queue (never in the query path):
                     every generated doc fact-checked against the graph
                     before it lands; hallucinated symbols rejected
```

The query path is deterministic: tree-sitter parsing → SQLite graph →
FTS5/BM25 (+ optional vector fusion and rerank) → labeled results. The LLM
only ever runs off-path, generating compass/wiki/memory candidates that the
deterministic critic verifies symbol-by-symbol against the graph.

## Measured Results

**Agent effort vs a grep-and-read baseline** (`cairn bench --suite agent`;
deterministic arms, no LLM, reproducible in CI — 300-file corpus, 6 task
shapes, medians):

| metric | grep-only baseline | with cairn | reduction |
|--------|-------------------:|-----------:|----------:|
| tokens / query | 217,187 | 1,146 | **99.5%** |
| tool calls / query | 153.2 | 1.5 | **99.0%** |
| wall-clock / query | 24.9 ms | 7.6 ms | 3.3× |

Depth-3 blast radius is the extreme: **2 tool calls and 712 tokens vs 303
calls and 429,600 tokens** — grep must read every name-collision hit; precise
edges don't.

**Query latency** (`cairn bench --suite perf`, p95): `find_definition`
0.03 ms · `get_callers` 0.06 ms · `impact_analysis` 0.11 ms ·
`search_symbols` 6.25 ms · `semantic_search` 201.67 ms (with embeddings) ·
`explore` 513.73 ms. First-`semantic_search` latency after boot warm-up:
**15.5 s cold → 232.6 ms warm** (committed
[warm-time artifact](docs/benchmarks.md#warm-time--first-query-latency-after-boot-warm-up)).

**Self-demo** — cairn indexes its own source in ~4s (~1,900 symbols /
~11,500 edges), and CI re-runs the build + resolution invariant on every push
(`tests/test_self_demo.py`), so the dogfood cannot silently rot. Reproduce:

```bash
git clone https://github.com/tanlnm512/cairn && cd cairn
cairn build
cairn def build_graph               # -> src/cairn/graph/builder.py
cairn impact build_graph            # -> real transitive callers (non-empty)
sqlite3 "$(cairn config --db)" \
  "SELECT COUNT(*) FROM edges WHERE resolution='exact' AND target_id IS NULL"   # -> 0
```

Full harness, scaling runs, and the k-fold retrieval-quality campaign:
[docs/benchmarks.md](docs/benchmarks.md).

## CLI Reference

`cairn --help` (or `cairn <group> --help`) is authoritative. The essentials:

| Command | What it does |
|---------|--------------|
| `cairn serve` | Run the stdio MCP server |
| `cairn dashboard` | Local read-only web console (127.0.0.1:8765) |
| `cairn build` / `cairn update` | Full build (first run) / incremental reindex |
| `cairn def <symbol>` | Find a symbol's definition |
| `cairn impact <symbol>` | Within-repo blast radius (precise default; `--fuzzy` to audit) |
| `cairn ask "<question>"` | Natural-language query routed across all layers |
| `cairn context <file>` | Compass + memory + wiki context for a file |
| `cairn memory / compass / wiki / task / knowledge …` | The layered stores + LLM task queue |
| `cairn install-agents` / `cairn uninstall` | Wire / remove agent integration |
| `cairn upgrade` | In-place update from PyPI (`--check` to preview) |
| `cairn eval` / `cairn bench` | Retrieval-quality / performance harnesses |
| `cairn doctor` | 8 health checks (PASS/WARN/FAIL; exit code gates CI) |
| `cairn report` | Redacted diagnostic bundle for bug reports (never uploads) |

Deep reference: [docs/cli-reference.md](docs/cli-reference.md).

## MCP Tools

27 tools across four layers — same store as the CLI:

| Layer | Tools |
|-------|-------|
| **graph** (9) | `find_definition`, `get_callers` / `get_callees`, `impact_analysis`, `cross_repo_deps`, `semantic_search`, `search_symbols`, `explore` (the aggregator — recommended first call), `visualize_graph` |
| **compass + knowledge base** (5) | `get_compass`, `search_knowledge`, `ask_compass` (cross-layer router), `trace_flow`, `generate_flow` |
| **memory** (8) | tribal memory: recall / record / lifecycle (promote, demote, evolve, decay, delete, digest) |
| **knowledge** (5) | OKF business docs / workflows: add / search / status |

Per-tool shapes and examples: [docs/mcp-tools.md](docs/mcp-tools.md).

## Configuration

The default install is dependency-light and network-free. Opt in with extras:

| Extra | Adds | Key env var |
|-------|------|-------------|
| `[semantic]` | `sentence-transformers` + `numpy` — real embeddings and CrossEncoder reranking | `CAIRN_RERANK=1`/`=0`; `CAIRN_FUSION` (default on) |
| `[ann]` | `sqlite-vec` — native ANN index for large corpora | `CAIRN_ANN_BACKEND=sqlite-vec` |
| `[scip]` | `protobuf` — consume pre-built [SCIP](docs/scip.md) indexes for compiler-grade exact edges | declare indexes in `cairn.json` |
| `[watch]` | `watchdog` — live rebuilds while `cairn serve` runs | `CAIRN_WATCH=0` disables |
| `[otlp]` | OpenTelemetry export of local telemetry events | `CAIRN_OTEL_ENDPOINT` (unset = off) |

Also: `CAIRN_HOME` (store location, default `~/.cairn`), `CAIRN_TELEMETRY=off`
(master kill switch). Full reference:
[docs/configuration.md](docs/configuration.md).

## Supported Platforms & Agents

- **Platforms** — anywhere Python ≥ 3.10 runs; CI tests 3.10–3.14 (macOS +
  Linux; `make ci-local` replicates CI in a clean Linux container).
- **Agents** — Claude Code, Cursor, ZCode, Droid, Claude Desktop, opencode,
  agy — wired by `cairn install-agents`, or any MCP client via the manual
  JSON block above.

## Troubleshooting

- `cairn doctor` — 8 health checks with PASS/WARN/FAIL each; non-zero exit
  means an active degradation (agents can gate on it).
- `cairn report` — redacted diagnostic bundle for bug reports; it never
  uploads anything.
- Empty `impact` / `get_callers` on a symbol you know is used → it's precise
  mode: retry `--fuzzy` / `fuzzy=True`; the name-matches list is labelled.
- `cairn update` did nothing on a fresh clone → that's correct (clean tree,
  no diff); run `cairn build` first.
- Known issues registry: [docs/BUGS.md](docs/BUGS.md).

## Development

```bash
pip install -e ".[dev]"   # pytest + watchdog + build + ruff
                            # (compiles the vendored Kotlin grammar — needs a C toolchain)
pytest -m core            # fast <3s smoke subset (one test per core function)
pytest                    # full suite (the CI path)
make ci-local             # clean-room CI replication in a Linux container
```

`pytest -m core` includes the **cairn-on-cairn self-demo**: cairn indexes its
own source in an isolated temp DB and asserts the core queries return correct
results for known symbols — and that the resolution invariant holds on the
verifier's own code. Releases are cut by tagging `vX.Y.Z` (tag-triggered
workflow; see [docs/release-checklist.md](docs/release-checklist.md)).
Contributions follow [docs/contribution-workflow.md](docs/contribution-workflow.md).

## Dependency Licenses

MIT-licensed; dependencies are all permissive (MIT, BSD, Apache-2.0, MPL-2.0,
PSF) — see [NOTICE](NOTICE). The optional `[semantic]` extra resolves `torch`
(+ NVIDIA CUDA components on Linux, under their own licenses) at install
time; the `BAAI/bge-m3` embedding model (MIT) downloads on demand to
`~/.cairn/lib/` and is not redistributed with cairn.

## Status

**Beta — pre-1.0 (v0.13.0).** Public surfaces (CLI flags, MCP tool shapes,
knowledge-file layout) may still shift before 1.0. Feedback welcome via
[GitHub issues](https://github.com/tanlnm512/cairn/issues).

## License

[MIT](LICENSE) — © 2025–2026 Tan Le
