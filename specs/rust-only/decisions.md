# Decisions: rust-only

Stable-id registry for the rust-only spec. One entry per decision the plan's
"Decisions to record" section lists, recorded in the phase that makes it.

## D-R01 — Native Louvain in the engine (Phase 2)

Supersedes the communities-sidecar ruling (D-013: `communities` runs the
networkx sidecar in-process).

**Decision**: `cairn communities` is computed natively in `cairn-build`
(`rust/crates/cairn-build/src/communities.rs`) over the compact structural
graph the closure already builds; no new dependency. The networkx sidecar and
its console routing are retired.

**Why**: the Python engine is gone; keeping a networkx sidecar forces every
install to carry a Python interpreter for one command and leaves US2-AC1
(binary-only `communities` writing both tables) unsatisfiable.

**Acceptance**: property-based, not byte-parity with networkx — random
streams differ, so partitions may legitimately differ. A fixed seed always
yields the same partition; modularity on the fixed fixtures is at least the
networkx reference within a pinned tolerance; the label and hub rules port
from `src/cairn/communities.py` (edge kinds/weights, dominant-segment labels,
hub fallback, sort orders, CLI flags and output shape).

**Cost**: one more module in `cairn-build` and per-command maintenance of the
Louvain core; partitions observed by users can differ from the sidecar's at
equal-or-better modularity.

## D-R02 — Cargo workspace manifest at the repo root (Phase 2)

**Decision**: the cargo workspace manifest and lockfile live at the repo root
(`Cargo.toml`, `Cargo.lock`), with members under `rust/crates/`,
`rust/vendor/`, and `.cargo/config.toml` pinning `build.target-dir =
"rust/target"` so every build-artifact path (scripts, workflows, golden
binaries, the Dockerfile aside) keeps resolving.

**Why**: the rust-only gates run bare `cargo fmt` / `cargo clippy` /
`cargo test --workspace` / `cargo build --release` from the repo root; with
the manifest nested under `rust/` those commands fail with "could not find
Cargo.toml" (cargo never searches downward). A root symlink cannot substitute
(cargo resolves member paths against the manifest's parent, not the link
target), and nested workspaces are unsupported.

**Cost**: every explicit `--manifest-path rust/Cargo.toml` reference had to
move (workflows, pre-commit, scripts, docs, Dockerfile); the Dockerfile builds
into `/src/target` since it does not carry the repo's `.cargo` pin.

## D-R02 — docx via `zip` + `quick-xml` in `cairn-query` (Phase 3)

docx→markdown is implemented natively in `rust/crates/cairn-query/src/convert.rs`
beside `ingest.rs`, reading `word/document.xml` (plus `styles.xml`,
`numbering.xml`, `word/_rels/document.xml.rels`) with the `zip` crate
(=9.1.2, `deflate-flate2` feature only) and `quick-xml` (=0.42.0). Both are
MIT and land together as one reader; recorded as one decision.

**Why**: FR-002 requires markdown conversion (headings, paragraphs, emphasis,
lists, tables, links) with no Python present; mammoth + markdownify pulled
two Python libraries and an interpreter hop for the same block model.

**Cost**: two new transitive trees (licenses verified against
rust/deny.toml's allowlist with cargo-deny); the binary-size cost is measured
together with the PDF crate in D-R03.

## D-R03 — native PDF text extraction via `pdf-extract` (=0.12.1) (Phase 3)

The engine's default `.pdf` path links `pdf-extract` 0.12.1 (MIT) and
normalizes its output into markdown paragraphs. No AGPL code is linked into
the engine (FR-003).

Candidates evaluated on quality / license / size:

- `pdf-extract` 0.12.1 — MIT, pure Rust; extracted the fixture PDF's
  paragraphs verbatim in local probes; errors and empty output map cleanly
  onto the existing skip-reason vocabulary. **Chosen.**
- raw `pdf` / `lopdf` — MIT/Apache, but content-stream extraction (font
  encodings, CMaps, Flate) would be hand-rolled for lower fidelity.
- `pdfium-render` (Pdfium) — permissive but a C++ binary blob and a
  cross-build burden, not pure Rust.
- PyMuPDF bindings — AGPL; forbidden in the engine by FR-003.

**Cost**: new transitive tree (cargo-deny clean against the allowlist) and
paragraph-text-only quality (no table or multi-column fidelity) — users who
need that fidelity install the converter package (D-R04). Release-binary
size for D-R02 + D-R03 together (aarch64-apple-darwin, `strip = true`):
56,478,128 → 58,088,544 bytes (+1,610,416, +2.85%), far under the 100 MB
wheel budget (NFR-001).

## D-R04 — optional converter package `cairn-intel-ingest` (Phase 3)

The Python converter moved out of `cairn-intel` into a standalone package at
`packaging/cairn-intel-ingest` (module `cairn_intel_ingest`, invoked as
`python -m cairn_intel_ingest <path>` through the existing `sidecar.rs`
resolution: `CAIRN_SIDECAR_PYTHON`, interpreter beside the binary, PATH). The
package is PDF-only and carries `pymupdf4llm` (AGPL) as a dependency; the
AGPL surface exists only there, never in the engine.

**Why**: FR-003 requires a separately-installable higher-fidelity PDF
converter while the default engine stays AGPL-free; a standalone package is
the only install shape that does both.

**Routing contract**: `.docx` always converts natively; `.pdf` prefers the
sidecar when `import cairn_intel_ingest` succeeds on a resolvable
interpreter, else converts natively; a present-but-failing sidecar skips with
the sidecar's reason (no silent fallback).

**Cost**: the `cairn-intel[ingest]` extra is retired (CI installs the
package from `packaging/` so pip-audit keeps auditing the pymupdf4llm
surface); publishing the package to PyPI is release engineering outside this
phase's code.
