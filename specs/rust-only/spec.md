# Spec: rust-only

**Status**: draft
**Effort**: large
**Created**: 2026-10-26
**Branch**: TBD

## What
Finish the move to Rust: no Python at runtime on any channel, and the
engine installable from PyPI on every mainstream platform. The two
remaining Python sidecars (`communities`, document conversion) move into
the engine, the PyPI wheel becomes the engine binary itself instead of a
Python launcher, and the engine gains a Windows build.

## Why
The engine is Rust and the Python engine is deleted, but four gaps remain:

- **Platforms without a working install.** `release.yml` publishes wheels
  only for macOS arm64 and manylinux x86_64/aarch64 (`CIBW_ARCHS_MACOS:
  arm64`, `CIBW_SKIP: "*musllinux*"`). macOS Intel, Alpine/musl and Windows
  users receive the sdist, which carries no binary, so `cairn` installs and
  then exits with "no engine binary found". Windows lost its engine
  entirely when the Python engine was deleted: the Windows ruling assumed
  that wheel would keep shipping the Python engine, and `paths.rust_bin()`
  returns `None` on Windows by design.
- **Python still runs for two features.** `cairn communities` executes in
  the launcher process with networkx (`src/cairn/console.py`,
  `src/cairn/communities.py`), and the engine shells out to
  `python -m cairn.ingest_convert` for PDF and docx
  (`rust/crates/cairn-query/src/ingest.rs`, `src/cairn/ingest_convert.py`).
  The PDF path depends on PyMuPDF, which is AGPL-3.0.
- **The wheel adds a Python hop.** Every `cairn` invocation from a PyPI
  install starts a Python interpreter only to `execv` the binary.
- **A retired feature still answers.** `cairn build --lsp` is parsed
  (`rust/crates/cairn-cli/src/cmds_core.rs`) but no pyright upgrade pass
  exists in the engine, so the flag silently does nothing.
- **Two migration gates were never met.** Per-tool p95 matched Python on
  21/26 tools on the self store (the daemon opens a fresh SQLite connection
  per call), and the closure ≥ 5× target fell to 1.14× because the closure
  is insert-bound in both engines.

## Business value
Every user who runs `pip install cairn-intel`, `pipx install cairn-intel`
or `uv tool install cairn-intel` on Linux (glibc or musl), macOS (Intel or
Apple Silicon) or Windows x86_64 gets the same working engine; the project
keeps one language and one toolchain at runtime. Success: wheels for all
seven platform targets pass the installed-product smoke test on their own
runners, and no Python file ships in a wheel.

## User stories
### US1 — Install anywhere from PyPI (P1)
As a developer on Windows, macOS Intel or Alpine Linux, I want the PyPI
package to install a working `cairn`, so that I am not forced onto a
different install channel.

**Acceptance criteria**:
- AC1: Given a supported platform, When the user installs `cairn-intel`
  from PyPI, Then `cairn --version` and `cairn config --json` run from the
  installed wheel (FR-004, FR-005).
- AC2: Given a platform with no published wheel and a Rust toolchain, When
  the user installs from the sdist, Then the engine is compiled and runs
  (FR-006).
- AC3: Given Windows x86_64, When the user runs `cairn build`, `cairn
  connect` and an MCP tool call, Then each behaves as on macOS and Linux
  (FR-007).

### US2 — No Python at runtime (P1)
As a user of `cairn communities` or `cairn knowledge ingest`, I want those
commands to work from the engine binary alone, so that they behave the same
on every channel.

**Acceptance criteria**:
- AC1: Given a built store, When the user runs `cairn communities` from a
  binary-only install, Then the `communities` and `symbol_communities`
  tables are written deterministically for a fixed seed (FR-001).
- AC2: Given a `.docx` file, When it is ingested, Then markdown with
  headings, lists, tables and links is staged without any Python
  interpreter present (FR-002).
- AC3: Given a `.pdf` file, When it is ingested, Then text is extracted by
  the engine; When the optional high-fidelity converter is installed, Then
  the engine uses it instead (FR-003).

### US3 — One toolchain for maintainers (P2)
As the maintainer, I want the PyPI package built directly from the cargo
workspace, so that releases need no Python packaging glue and CI no longer
runs Python-only gates on code that no longer exists.

**Acceptance criteria**:
- AC1: Given a release tag, When `release.yml` runs, Then every wheel is
  produced from `rust/crates/cairn-cli` with no `setup.py`, launcher or
  binary-injection step (FR-004, FR-010).

### US4 — Daemon latency (P3)
As an agent calling cairn's MCP tools, I want small-store tool calls to be
no slower than the Python daemon was.

**Acceptance criteria**:
- AC1: Given the yarl and attrs pinned stores, When the baseline harness
  measures per-tool p95, Then no tool regresses beyond the recorded
  tolerance (FR-008).

## Requirements
- **FR-001**: The engine shall compute `cairn communities` natively: a
  seeded Louvain partition over the structural edge kinds and weights the
  sidecar uses today, writing the existing `communities` and
  `symbol_communities` tables, with the same CLI flags and output shape.
- **FR-002**: The engine shall convert `.docx` documents to markdown
  natively (headings, paragraphs, emphasis, lists, tables, links).
- **FR-003**: The engine shall extract `.pdf` text natively by default. A
  higher-fidelity PDF converter shall be optional, installed separately and
  invoked only when present. No AGPL code shall be linked into the engine
  binary.
- **FR-004**: The PyPI package shall be built from the cargo workspace with
  the engine binary installed directly as the `cairn` executable. Wheels
  shall contain no Python modules.
- **FR-005**: Wheels shall be published for manylinux x86_64 and aarch64,
  musllinux x86_64 and aarch64, macOS x86_64 and arm64, and Windows x86_64,
  each tagged for all Python 3 versions.
- **FR-006**: The sdist shall build the engine from source with a Rust
  toolchain, including the vendored grammar and sqlite-vec sources.
- **FR-007**: The engine shall compile, pass its test suite and run on
  Windows x86_64: Unix-only APIs gated or replaced, the build lock,
  daemon lifecycle and home/config paths implemented for Windows, and a
  Windows leg in `rust.yml`.
- **FR-008**: The daemon shall reuse SQLite connections across tool calls,
  invalidating them when the store file is swapped by a build.
- **FR-009**: The closure performance gate shall be re-ruled against what
  the closure can achieve (insert-bound; peak-WAL gain measured), and the
  new gate recorded and measured.
- **FR-010**: Python shall be removed from the runtime and release path:
  `src/cairn/` deleted, the `cairn-intel` extras retired, Python-only CI
  gates removed or scoped to the remaining dev scripts.
- **FR-011**: User and contributor docs (`README.md`, `docs/`, `AGENTS.md`,
  `docs/cli-reference.md`, `docs/release-checklist.md`) shall describe the
  binary-only install on every channel.
- **FR-012**: `cairn build --lsp` shall either run an LSP-based edge
  upgrade pass in the engine or fail with a message naming the retirement;
  it shall not be accepted silently.
- **NFR-001**: Every wheel shall stay under PyPI's default per-file limit
  (100 MB).
- **NFR-002**: The MCP tool contract, the CLI machine contracts and the
  `.kg` schema shall not change, except additively.

## Scope
**In**: native communities and document conversion, maturin bin wheels for
seven targets, source sdist, Windows engine support, daemon connection
reuse, closure gate re-ruling, Python removal from runtime and release,
docs.
**Out (deferred)**: Windows aarch64 and other Linux architectures (armv7,
ppc64le, s390x); a GUI installer; porting the dev-only Python scripts that
do not gate releases.

## Assumptions & risks
- Assumption: the PyPI name `cairn-intel` and its version stay shared with
  the cargo workspace.
- Risk: Louvain partitions cannot match networkx byte for byte (random
  number streams differ) — mitigation: a recorded ruling with
  property-based acceptance (determinism, modularity at least the
  networkx result within a tolerance on fixtures).
- Risk: native PDF extraction is weaker than pymupdf4llm on tables and
  multi-column layouts — mitigation: the optional converter package keeps
  the current quality for users who install it.
- Risk: Windows CI cost (15 grammar crates, bundled SQLite and sqlite-vec
  under MSVC) on a solo-maintained project — mitigation: one Windows leg
  with a shared cache; parity oracles stay Linux/macOS.
- Risk: the maturin switch changes how pip installs the executable —
  mitigation: smoke-test each wheel on its own runner before publish.
