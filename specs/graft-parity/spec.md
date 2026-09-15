# Spec: graft-parity

**Status**: done
**Created**: 2026-09-15
**Branch**: `feat/graft-parity`

## What
Close the capability gaps identified by a code-level comparison with
[trailhq/Graft](https://github.com/trailhq/Graft) (v0.18.0, inspected at clone):
always-fresh query answers, a diff-based PR blast-radius command, a
deterministic repo-orientation map, a signature-only file API surface, an
exhaustive regex grep grouped by enclosing symbol, preserved human notes on
regenerated knowledge pages, a self-contained graph HTML export, an opt-in
LSP edge-resolution pass, a generic-tier Rust indexer, and
submodule/nested-repo/worktree discovery.

## Why
Measured against Graft's implementation, cairn's default install has four
concrete gaps: (1) results go stale after edits with no warning unless the
optional `[watch]` extra is installed — `_staleness_banner` cannot fire
without the watcher's `pending_sync` rows; (2) blast radius is per-symbol
only, while this repo's own PR gate requires per-change radius, forcing
agents to hand-map diff→symbols→N impact calls; (3) there is no zero-cost
"first look at a repo" surface (compass is queued/LLM-gated, `explore`
answers targeted queries only); (4) audit tasks ("every occurrence of this
pattern") fall back to shell grep because `search_symbols` is token-based
FTS5. The remaining items (notes preservation, HTML export, LSP, Rust,
worktree/submodules) adopt Graft patterns that fit cairn's
resolution-labeled verification contract.

## Business value
Agents and reviewers get correct answers on the current working tree without
remembering `cairn update`, a PR-commentable blast radius that automates the
review-checklist's largest manual step, and cheaper orientation (signatures
and maps instead of whole-file reads). Success is measured by: no stale
answer on an edited tree in the default install; `cairn blast` producing a
review-ready markdown radius from a diff; `cairn map` / `file_api` /
`cairn grep` answering with zero LLM calls; and CI-visible regression tests
for each.

## User stories
### US1 — Always-fresh answers (P1)
As an agent querying cairn, I want graph tools to refresh drifted files
before answering, so that results describe the current working tree without
manual `cairn update` or the `[watch]` extra.

**Acceptance criteria**:
- AC1: Given an indexed file edited on disk, when any graph-layer MCP tool or
  CLI query command runs, then the drifted file is reindexed before the
  answer and the answer reflects the edit (FR-001).
- AC2: Given refresh disabled (`CAIRN_NO_REFRESH=1` or `--no-refresh`) or a
  read-only server, the system answers from the stored graph and reports
  drift via a staleness banner that does not depend on the watcher (FR-002).

### US2 — PR blast radius (P1)
As a reviewer, I want the reverse-dependency radius of a whole diff, so that
one command produces the review-checklist blast-radius evidence for a PR.

**Acceptance criteria**:
- AC1: Given a working-tree diff (or `--base <ref>`), `cairn blast` seeds the
  symbols whose stored spans intersect changed hunks and walks reverse call
  edges (precise default, `--fuzzy` opt-in) (FR-003).
- AC2: Given any of `--format text|markdown|mermaid|json`, blast renders that
  format; with no dependents it exits 0 stating that; with an unknown base
  ref it errors with CI fetch-depth guidance (FR-004).

### US3 — Orientation and API surface at token budget (P1)
As an agent entering a codebase, I want a deterministic repo map and a
signature-only file view, so that I orient and read API surfaces without
whole-file reads or an LLM.

**Acceptance criteria**:
- AC1: `cairn map` (CLI + MCP `repo_map`) groups directory clusters per repo
  scope with file/symbol/edge counts, top in-degree hubs, and global
  hotspots, with explicit dropped counts at caps (FR-005).
- AC2: MCP `file_api` returns every symbol's kind, qualified name, signature
  text, and span for a file, without bodies, degrading to name+span where a
  parser recorded no signature (FR-006).

### US4 — Exhaustive auditable search (P2)
As an agent auditing a pattern, I want regex search over indexed files whose
hits are grouped by enclosing symbol and ranked by coupling, so that
"every occurrence" tasks stay inside the graph.

**Acceptance criteria**:
- AC1: `cairn grep <pattern>` searches indexed files, groups hits by
  innermost enclosing symbol (file-level group outside spans), ranks groups
  by in-edge count, supports `-i`, `--fixed`, `--in <prefix>`, and reports
  truncation counts instead of dropping silently (FR-007).

### US5 — Durable knowledge and shareable artifacts (P2)
As a maintainer, I want my notes on generated pages to survive regeneration
and a graph view I can attach to a PR, so that human context is never lost
and reviews get a visual artifact.

**Acceptance criteria**:
- AC1: A human-authored `## Notes` section on a wiki/compass page survives
  regeneration byte-identical and the critic does not judge Notes content
  (FR-008).
- AC2: `cairn viz --export <file>.html` writes one self-contained HTML file
  (no network assets) rendering the selected scope (FR-009).

### US6 — Compiler-grade edges and broader coverage (P3)
As a power user, I want opt-in LSP edge resolution, Rust indexing via a
generic tier, and submodule/nested-repo/worktree awareness, so that
precision and coverage grow without weakening the verification contract.

**Acceptance criteria**:
- AC1: With pyright on PATH, `cairn build --lsp` upgrades resolvable
  ambiguous Python member-call edges to exact; without it, the pass is a
  deterministic no-op with a notice (FR-010).
- AC2: Rust `.rs` files index via the generic tier with name-based edges
  labeled `unresolved` (never exact) (FR-011).
- AC3: `discover_repos` includes initialized submodules and nested repos only
  when persisted config enables it, prefixing child paths per repo; a linked
  git worktree with no store seeds from the main checkout and refreshes
  drift (FR-012).

## Requirements
- **FR-001**: WHEN a graph-layer MCP tool (`find_definition`, `get_callers`,
  `get_callees`, `impact_analysis`, `explore`, `search_symbols`,
  `semantic_search`, `cross_repo_deps`) or CLI query command (`def`,
  `callers`, `callees`, `impact`, `search`, `context`) is invoked and any
  indexed file's stored `(size, mtime)` disagrees with the working tree, the
  system shall reindex exactly the drifted files via `reindex_paths` before
  answering.
- **FR-002**: IF refresh is disabled (`CAIRN_NO_REFRESH=1` or `--no-refresh`)
  or the store is read-only, THEN the system shall answer from the stored
  graph and report drift via a staleness banner computed from the probe,
  without requiring the `[watch]` extra.
- **FR-003**: The system shall provide `cairn blast` that computes the
  reverse-edge radius of a git diff: default working tree vs `HEAD`,
  `--base <ref>` against the merge base, seeding from symbols whose stored
  spans intersect changed hunks.
- **FR-004**: `cairn blast` shall render `text|markdown|mermaid|json`
  formats, use precise edges by default with `--fuzzy` opt-in, exit 0 with a
  "no dependents" statement when the radius is empty, and error with CI
  fetch-depth guidance when `--base` names a ref absent from the checkout.
- **FR-005**: The system shall provide `cairn map` (CLI + MCP `repo_map`):
  a deterministic, no-LLM orientation grouping directory clusters per repo
  scope with file/symbol/edge counts, top in-degree hubs per cluster, and
  global hotspots, reporting dropped counts at every cap.
- **FR-006**: The system shall provide an MCP `file_api` tool returning
  kind, qualified name, signature text, and line span for every symbol in a
  file without bodies, degrading to name+span where no signature was parsed.
- **FR-007**: The system shall provide `cairn grep <pattern>` performing
  regex search over indexed files, grouping hits by innermost enclosing
  symbol via stored spans (file-level group for hits outside any span),
  ranking groups by incoming edge count, supporting `-i`, `--fixed`,
  `--in <path-prefix>`, and reporting truncation counts.
- **FR-008**: WHERE a wiki or compass page carries a human-authored
  `## Notes` section, the system shall preserve it byte-identical across
  regeneration/enrichment and the deterministic critic shall not evaluate
  Notes content against code references.
- **FR-009**: `cairn viz --export <file>.html` shall write a single
  self-contained HTML file with no network-loaded assets that renders the
  selected graph scope.
- **FR-010**: WHERE `cairn build --lsp` runs and `pyright` is on `PATH`, the
  system shall use LSP definition lookups over ambiguous Python member-call
  edges to upgrade resolvable ones to `exact`; missing or failing server
  shall degrade to a deterministic no-op with a notice, and no existing
  `exact` edge shall be downgraded.
- **FR-011**: The system shall index Rust (`.rs`) via a generic tree-sitter
  extractor producing definitions and name-based call edges labeled
  `unresolved` (never `exact`), with the grammar dependency recorded as a
  `D-###` decision.
- **FR-012**: `discover_repos` shall optionally include initialized git
  submodules and nested child repos when persisted config enables it
  (default off), indexing their files under a prefixed repo id; WHERE the
  workspace is a linked git worktree with no store, the system shall seed
  the graph from the main checkout's store and reindex drift.

## Scope
**In**: the twelve FRs above, each with tests; CLI + MCP surfaces where
named; docs updates for new commands/tools.
**Out (deferred)**:
- LSP servers beyond pyright (gopls, clangd, tsserver) — pattern proven with
  pyright first.
- Generic-tier languages beyond Rust — one language proves the tier.
- Blast owners-from-git-history, blast viz export, push-mode context
  bundles, tokens-saved footers, SWE-bench external harness.
- npm/file-based agent instruction wiring (cairn is MCP-first).

## Assumptions & risks
- Assumption: the user's "implement all suggested execution items" covers
  the five ranked items including their heavyweight components, so LSP/Rust/
  worktree land as minimal-viable patterns per the scope bounds above.
- Assumption: the Graft clone inspected at `/tmp/graft` (v0.18.0) is
  reference-only; no Graft code is copied.
- Risk: LSP integration (FR-010) is the largest single item — bounded to
  pyright, stdio JSON-RPC, ambiguous-edges-only, best-effort no-op.
- Risk: vendoring a Rust grammar (FR-011) widens the wheel/platform matrix —
  constitution C-03 requires the dependency decision to be recorded and the
  grammar must remain optional or gracefully skippable.
- Risk: worktree seeding (FR-012) touches workspace identity — bounded to
  seed-then-refresh, never mutating the main checkout's store in place.
