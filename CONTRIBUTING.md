# Contributing to cairn

Thanks for your interest in contributing to **cairn** — a local codebase
intelligence system (structural graph + compass + wiki + agent memory) exposed
via the `cairn` CLI and an MCP server. This is currently a small, single-maintainer
project, so contributions of any size — bug reports, fixes, docs, tests, new
parsers — are genuinely welcome and have a real impact.

## Development setup

cairn is a **Rust binary**; Python 3.10+ is used only for the dev tooling
(wheel builds). MIT-licensed.

```bash
git clone https://github.com/tanlnm512/cairn.git
cd cairn

# Dev tooling (a uv.lock is committed at the repo root):
uv sync --extra dev

# The engine itself:
cargo build --release --manifest-path Cargo.toml -p cairn-cli
```

Development installs use the cargo build above, or `scripts/install.sh`
(`cargo install --locked` from this checkout); a Rust toolchain is
required either way.

Install the git hooks with [prek](https://github.com/j178/prek) (the same
gates CI's pre-commit job runs):

```bash
git config --unset core.hooksPath   # only if an older checkout set it
prek install
```

## Running tests

There are two feedback loops:

```bash
cargo test --manifest-path Cargo.toml --workspace   # the engine + dev-gate suite
cargo xtask parity --store self && cargo xtask cli-golden   # frozen-oracle replays
```

The oracle replays run against the release binary on `rust/**` PRs and
nightly; run them locally when touching the frozen oracles under
`rust/golden/`. Run both loops before opening a PR.

### Performance changes

If your change touches daemon/build hot paths, measure with the recorded
harnesses — `cargo xtask baseline` (per-tool p95 over the pinned stores,
with `--compare` against a recorded baseline) and `cargo xtask closure-bench`
(closure wall time and peak WAL, with `--compare`).
Rebuild the release binary first; both compare modes fail on regression.

## Project layout

The engine is the cargo workspace under `rust/` (one crate per concern:
store, parse, build, query, daemon, cli — see
[docs/architecture.md](docs/architecture.md)). Repo dev gates and the
frozen-oracle replays are the `xtask` crate (`cargo xtask <command>`; run it
bare for the command list).

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

- Follow existing patterns in the crate you're touching.
- Keep functions focused; prefer small, testable units.
- Add tests for new behavior; every contract has one owner test at its
  strongest boundary (see AGENTS.md's "Before Adding a Test").
- Don't introduce new runtime dependencies without a recorded decision — the
  default install is deliberately lightweight (no torch, no network).

## Pull requests

1. Open a PR against `main`.
2. Describe the change and link any related issue.
3. Ensure `cargo test --workspace` passes at minimum.
4. Keep PRs scoped — one logical change each.
5. Enable auto-merge on your PR (`gh pr merge --auto --merge`) so it merges
   the moment review and required checks land.

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
- OS and install channel (Homebrew / cargo / tarball / PyPI)
- Steps to reproduce, expected vs. actual behavior, and any relevant logs

For security-sensitive reports, see **SECURITY.md** instead of a public issue.
