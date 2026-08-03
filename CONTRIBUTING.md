# Contributing to cairn

Thanks for your interest in contributing to **cairn** — a local codebase
intelligence system (structural graph + compass + wiki + agent memory) exposed
via the `cg` CLI and an MCP server. This is currently a small, single-maintainer
project, so contributions of any size — bug reports, fixes, docs, tests, new
parsers — are genuinely welcome and have a real impact.

## Development setup

cairn targets **Python 3.10+** and is MIT-licensed.

```bash
git clone https://github.com/tanlnm512/cairn.git
cd cairn

# Preferred (a uv.lock is committed at the repo root):
uv sync --extra dev

# Or with plain pip:
pip install -e ".[dev]"
```

Optional extras (see `pyproject.toml`): `[watch]` (file watching),
`[semantic]` (sentence-transformers + numpy; pulls torch, large),
`[ann]` (sqlite-vec native ANN index).

## Running tests

There are two feedback loops:

```bash
pytest -m core -q      # fast smoke subset (<3s) — the inner dev loop
pytest -q              # full suite (all 58 test files) — the CI path
```

The `core` marker (declared in `pyproject.toml`) marks **one focused test per
core function across all 5 layers**, living in `tests/test_core_smoke.py`. It is
the fastest way to confirm you haven't broken the core query/build/transport
path. Run `pytest -q` before opening a PR.

### Performance changes

If your change touches build/embed/query hot paths, check for regressions with
the benchmark:

```bash
cg bench --save before.json        # baseline before your change
# ...make your change, rebuild...
cg bench --compare before.json     # flags ops >15% slower; exits 2 on regression
```

The default `hash` embed backend is dependency-free, so this runs on a plain
install. See `docs/cli-reference.md` for the full `cg bench` surface.

## Project layout

Source lives under `src/cairn/`, with one subpackage per concern:
`graph/` (structural call graph + retrieval), `compass/` (module navigation
guides), `wiki/` (architectural docs), `memory/` (agent memory + tribal
knowledge), `knowledge/` (knowledge-base query tools), `parsers/` (tree-sitter
language parsers), `retrieval/` (FTS + semantic search), `mcp_server/` (the MCP
transport), `cli/` (the `cg` entry point), `llm/` (decoupled task queue —
cairn never calls an LLM directly), `okf/` (the `.knowledge/` file format),
`viz/` (graph visualization), and `agent_integration/` (shipped templates for
`cg install-agents`). Tests mirror the package under `tests/`.

## Codebase intelligence convention (read before editing)

cairn is **agent-first**: it is its own best tool for understanding itself.
Whether you're a human or an AI agent, **before editing a file**, use the
cairn tools to load context — this catches blast radius that grep misses:

1. `explore("<query>")` first — verbatim source + call paths in one call.
2. `ask_compass(file_path="<path>")` — load compass + memory for the file.
3. `get_callers("<symbol>")` — see who depends on what you're changing.
4. `impact_analysis("<symbol>")` — for anything potentially breaking.

If MCP tools aren't available, the `cg` CLI mirrors them: `cg context <file>`,
`cg callers <symbol>`, `cg impact <symbol>`, `cg def <symbol>`, `cg ask "<q>"`.
See **AGENTS.md** for the full tool list, resolution-aware querying (precise vs
`fuzzy=True`), and the post-task `cg update` + `record_memory` loop.

## Coding style

- Follow existing patterns in the subpackage you're touching.
- Keep functions focused; prefer small, testable units.
- Add tests for new behavior. If the change is on the core query/build/transport
  path, add or extend a case under the `core` marker in
  `tests/test_core_smoke.py` so the fast loop covers it.
- Don't introduce new top-level dependencies without discussion — the default
  install is deliberately lightweight (no torch, no network).

## Pull requests

1. Open a PR against `main`.
2. Describe the change and link any related issue.
3. Ensure `pytest -m core` passes at minimum; `pytest -q` if you can run the
   full suite.
4. Keep PRs scoped — one logical change each.

## Reporting issues

File bugs via **GitHub Issues**. Please include:

- cairn version (`cg version`)
- Python version and OS
- Steps to reproduce, expected vs. actual behavior, and any relevant logs

For security-sensitive reports, see **SECURITY.md** instead of a public issue.
