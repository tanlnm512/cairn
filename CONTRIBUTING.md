# Contributing to cairn

Thanks for your interest in contributing to **cairn** — a local codebase
intelligence system (structural graph + compass + wiki + agent memory) exposed
via the `cairn` CLI and an MCP server. This is currently a small, single-maintainer
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

The editable install compiles the vendored Kotlin grammar extension
(`vendor/tree-sitter-kotlin/`) in-place, so a C toolchain (clang, gcc, or
MSVC) is required for development installs; released wheels ship the
extension prebuilt, so end-user installs stay toolchain-free.

Optional extras (see `pyproject.toml`): `[watch]` (file watching),
`[semantic]` (sentence-transformers + numpy; pulls torch, large),
`[ann]` (sqlite-vec native ANN index).

Install the dev pre-commit hook (runs `ruff` on staged `.py` files; aborts
on any unused-import / undefined-name / redefinition error so it's caught
locally instead of in CI):

```bash
./scripts/install-dev-hooks.sh   # one-time; sets core.hooksPath -> scripts/hooks/
```

Bypass once with `git commit --no-verify`; uninstall with
`git config --unset core.hooksPath`.

## Running tests

There are two feedback loops:

```bash
pytest -m core -q      # fast smoke subset (<3s) — the inner dev loop
pytest -q              # full suite (~60 test files) — the CI path
```

The `core` marker (declared in `pyproject.toml`) marks **one focused test per
core function across all 5 layers**, living in `tests/test_core_smoke.py`. It is
the fastest way to confirm you haven't broken the core query/build/transport
path. Run `pytest -q` before opening a PR.

### Performance changes

If your change touches build/embed/query hot paths, check for regressions with
the benchmark:

```bash
cairn bench --save before.json        # baseline before your change
# ...make your change, rebuild...
cairn bench --compare before.json     # flags ops >15% slower; exits 2 on regression
```

The default `hash` embed backend is dependency-free, so this runs on a plain
install. See `docs/cli-reference.md` for the full `cairn bench` surface.

## Project layout

Source lives under `src/cairn/`, with one subpackage per concern:
`graph/` (structural call graph + retrieval), `compass/` (module navigation
guides), `wiki/` (architectural docs), `memory/` (agent memory + tribal
knowledge), `knowledge/` (knowledge-base query tools), `parsers/` (tree-sitter
language parsers), `retrieval/` (FTS + semantic search), `mcp_server/` (the MCP
transport), `cli/` (the `cairn` entry point), `llm/` (decoupled task queue —
cairn never calls an LLM directly), `okf/` (the `.knowledge/` file format),
`viz/` (graph visualization), and `agent_integration/` (shipped templates for
`cairn install-agents`). Tests mirror the package under `tests/`.

## Codebase intelligence convention (read before editing)

cairn is **agent-first**: it is its own best tool for understanding itself.
Whether you're a human or an AI agent, **before editing a file**, use the
cairn tools to load context — this catches blast radius that grep misses:

1. `explore("<query>")` first — verbatim source + call paths in one call.
2. `ask_compass(file_path="<path>")` — load compass + memory for the file.
3. `get_callers("<symbol>")` — see who depends on what you're changing.
4. `impact_analysis("<symbol>")` — for anything potentially breaking.

If MCP tools aren't available, the `cairn` CLI mirrors them: `cairn context <file>`,
`cairn callers <symbol>`, `cairn impact <symbol>`, `cairn def <symbol>`, `cairn ask "<q>"`.
See **AGENTS.md** for the full tool list, resolution-aware querying (precise vs
`fuzzy=True`), and the post-task `cairn update` + `record_memory` loop.

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

## Contributor licensing (DCO)

cairn is MIT-licensed. To keep the project's licensing clean and to protect
every downstream user, we ask contributors to certify that they have the right
to contribute their code under those terms, using the
[Developer Certificate of Origin (DCO)](https://developercertificate.org/).

This is the same lightweight model the Linux kernel and many other projects use.
There is **no CLA** to sign and no copyright assignment — you keep your
copyright. The DCO is satisfied automatically by signing off your commit:

```bash
git commit -s          # adds "Signed-off-by: Your Name <you@example.com>"
```

To set it as the default for this repo:

```bash
git config format.signoff true
```

By submitting a pull request with a `Signed-off-by:` line, you attest that you
wrote the code yourself (or have the right to submit it), it is not a
derivative of GPL/AGPL or proprietary code, and you are licensing it to cairn
and its users under the MIT License. If a contribution is derived from
third-party MIT/Apache/BSD-licensed code, retain the upstream copyright and
license notice in the file or add it to `NOTICE`.

## Reporting issues

File bugs via **GitHub Issues**. Please include:

- cairn version (`cairn version`)
- Python version and OS
- Steps to reproduce, expected vs. actual behavior, and any relevant logs

For security-sensitive reports, see **SECURITY.md** instead of a public issue.
