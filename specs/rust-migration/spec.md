# Spec: rust-migration

**Status**: draft
**Created**: 2026-10-18
**Branch**: `feat/rust-migration` (created at Phase 0 start)

## What
Migrate cairn's engine from Python to Rust incrementally, without breaking the
shipped tool: read-path MCP daemon first, then the build path, then the CLI.
The SQLite `.kg` schema and the MCP tool surface are frozen cross-language
contracts; every phase is validated by golden/differential testing against the
Python implementation, which stays authoritative until the final flip.
Distribution is dual-channel after the flip: PyPI remains a required publish
channel (maturin-built abi3 wheels wrapping the Rust binary, so `pip install`
keeps working), alongside Python-free GitHub Release binaries and Homebrew.

## Why
Performance and distribution, not language preference:

- The transitive-closure build degrades super-linearly on large graphs
  (measured: half-hour builds and multi-GB WAL at 5× edge counts). A streaming
  Rust implementation with bounded memory is the structural fix; the
  level-BFS bounds work already identified the blowup mechanism.
- The build path pays ProcessPoolExecutor pickling per file; a rayon thread
  pool over shared memory removes that overhead class.
- A single static binary removes the multi-version Python CI matrix, the
  wheel/platform churn, and pipx/uv install friction for what is a CLI tool.

The test suite (~2× the source in lines, 300+ files) is the de facto
behavioral spec, which makes a big-bang rewrite untenable for a solo
maintainer. The strangler approach ships standalone value at every phase gate
and keeps a working Python product at all times.

## Business value
Faster builds and queries on large repos; installs keep working from PyPI
(maturin wheels wrapping the rust binary) and gain Python-free channels
(GitHub Releases/Homebrew); lower daemon memory. Success at Phase 1 exit: the Rust
daemon serves all 26 tools golden-equivalent on ≥3 real stores with lower RSS.
Success at Phase 2 exit: the Rust build matches Python's `.kg` content on the
benchmark corpora and beats closure time by ≥5×.

## Where the plan lives
`plan.md` — phase gates, workstreams, contracts, testing strategy, dependency
decisions, risk register, timeline. Feasibility assessment (2026-10-18) that
this spec executes: dependency mapping verified against the Rust ecosystem
(rmcp Tier-1, sqlite-vec crate, tree-sitter grammar crates incl. dart/objc/
kotlin).

## Relationship to other specs
`remote-mcp-oauth` (draft) re-bases onto whichever daemon implementation is
live when that work starts; the OAuth/auth middleware lands on the Rust daemon
if Phase 1 has shipped by then. The `.kg` schema and tool contracts this spec
freezes are the same ones every other spec builds on.
