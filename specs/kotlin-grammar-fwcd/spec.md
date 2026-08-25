# Spec: kotlin-grammar-fwcd

**Status**: approved      <!-- approved 2026-08-25 — the user's instruction
                                "implement it in a branch as minor update" is
                                the recorded go-ahead; clarify-pass defaults
                                were asked and left unanswered → recommended
                                defaults taken, vetoable at review. First task
                                spawned → active; all ticked + TCs green → done -->
**Created**: 2026-08-25
**Branch**: `feat/kotlin-grammar-fwcd`

## What
Cairn indexes modern Kotlin correctly. Codebases using recent Kotlin syntax
(KEEP-0438 destructuring, `when` guard conditions, multi-dollar string
interpolation, trailing commas) stop parsing into error nodes, and Kotlin
support rides an actively-maintained grammar instead of one frozen since
January 2025 — with zero change to what cairn extracts from Kotlin code
that already parses fine today.

## Why
The grammar backing Kotlin today (PyPI `tree-sitter-kotlin` 1.1.0, built
from tree-sitter-grammars/tree-sitter-kotlin by amaanq) last changed
2025-01-16 — 19 months dormant, 13 commits total. Modern Kotlin parses
into ERROR nodes or wrong tree shapes, and fixes will never land there.
fwcd/tree-sitter-kotlin is a different, actively-maintained grammar (last
commit 2026-08-01; KEEP-0438, when-guards, multi-dollar interpolation,
parse-hang fixes; a JetBrains PSI cross-validation harness). Adopting it
future-proofs Kotlin support. The two grammars are separate projects that
happen to share a name — this is a grammar swap, not a version bump.

## Business value
Kotlin users (Android/KMP codebases) get correct symbols and edges on
modern syntax, so blast-radius and impact queries on Kotlin repos are
trustworthy. Success is measured by: the existing Kotlin test corpus and
self-demo stay green on the new grammar (extraction parity), and a
modern-syntax fixture set parses free of ERROR nodes.

## User stories
<!-- Ordered by priority; each independently demoable. -->
### US1 — Modern Kotlin indexes correctly (P1)
As a cairn user with a Kotlin codebase, I want files using recent Kotlin
syntax to parse and index correctly, so that graph queries on my repo are
not silently degraded by parse errors.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given a `.kt` file using KEEP-0438 destructuring, `when` guard
  conditions, or multi-dollar interpolation, When cairn indexes it, Then
  the parse tree contains no ERROR nodes. (FR-004)
- AC2: Given the existing Kotlin test corpus, When it is parsed with the
  new grammar, Then symbol and edge extraction matches the current
  grammar's output (same symbols, same call/inheritance/import edges).
  (FR-002, FR-003)

### US2 — Grammar updates ship through the existing release flow (P2)
As the maintainer, I want the grammar vendored into cairn's repo, so that
a grammar bump is a file drop released by the existing tag-triggered flow
— no second package to publish, no new release ceremony.

**Acceptance criteria**:
- AC1: Given the vendored grammar, When cairn's release build runs, Then
  the compiled Kotlin language extension ships inside cairn-intel's own
  wheels. (FR-005)
- AC2: Given `pip install cairn-intel` on any supported platform, When no
  C toolchain is present, Then the install still succeeds from wheels.
  (FR-005)

## Requirements
- **FR-001**: The parser registry shall load the Kotlin language from the
  vendored fwcd tree-sitter-kotlin grammar (replacing the
  `tree_sitter_kotlin` PyPI module) on the tree-sitter 0.26 runtime.
- **FR-002**: WHEN fwcd's grammar models an interface or enum as a `class_declaration` variant, the Kotlin parser shall classify the emitted symbol as interface/enum accordingly, preserving extraction parity with the current grammar.
- **FR-003**: WHEN fwcd's grammar hides or renames node shapes the parser walks (`class_parameters` hidden, `delegation_specifiers` plural hidden, `type_reference` hidden, `qualified_identifier` → `identifier`), the Kotlin parser shall match the new shapes so call edges, property declarations, primary-constructor fields, and inheritance edges are unchanged.
- **FR-004**: WHERE a Kotlin file uses syntax newly supported by fwcd's grammar (KEEP-0438 destructuring, `when` guards, multi-dollar string interpolation, trailing commas), the parser shall produce a tree free of ERROR nodes.
- **FR-005**: The build shall produce platform wheels containing the
  compiled Kotlin language extension (macOS arm64/x64, manylinux
  x86_64/aarch64, musllinux, Windows) so `pip install cairn-intel` remains
  toolchain-free. Packaging approach: **in-tree vendored extension** —
  fwcd's generated parser is vendored into cairn's tree and compiled into
  cairn-intel's own wheels by the existing tag-triggered release flow
  (clarify pass 2026-08-25: asked, no answer — recommended default taken).
- **FR-006**: The system shall remove the `tree-sitter-kotlin==1.1.0` PyPI
  dependency outright (no fallback loader; single Kotlin grammar path).
- **FR-007**: WHEN Kotlin SCIP indexes are declared (scip-java merge), the hybrid build shall remain functional with the new grammar feeding the substrate — tree-sitter parses the Kotlin files, SCIP edges merge onto those rows (survey: builder.py `_merge_scip_defs_into_tree_sitter`).
- **FR-008**: The change shall ship as a minor-level update with changelog
  entries under `[Unreleased]` (Keep-a-Changelog; the version bump itself
  happens at tag time per the repo's release discipline).

## Scope
**In**: grammar vendoring + build/wheel integration; KotlinParser node-type
port; registry and pyproject changes; corpus parity tests + modern-syntax
fixtures; changelog entries.
**Out (deferred)**: semantic extraction of the NEW constructs
(destructuring → symbols, when-guards → edges); SCIP-side changes; other
languages' grammars; upstream fuzzing/conformance campaigns.

## Assumptions & risks
- Assumption (clarify pass 2026-08-25 — asked, no answers; recommended
  defaults taken, vetoable at the Stage-4 gate): in-tree vendored
  extension; parity-only extraction (no semantic extraction of new
  constructs); old dependency removed outright; constitution adopted from
  AGENTS.md hard rules.
- Assumption: "minor update" = changelog entries under `[Unreleased]`;
  actual version bump happens at tag time per the repo's tag-triggered
  release discipline.
- Assumption: fwcd's generated parser regenerates cleanly against the
  tree-sitter 0.26 ABI with the current tree-sitter CLI (fwcd's own
  pyproject pins `tree-sitter~=0.22`, which is stale — we vendor and
  regenerate, we do not pip-install fwcd's packaging).
- Risk: node-type drift beyond the 7 researched mismatches surfaces during
  implementation — mitigation: corpus parity tests (FR-002/003) plus an
  ERROR-node scan (FR-004) catch shape drift mechanically.
- Risk: hidden-rule differences (`_class_parameters` etc.) silently drop
  edges — mitigation: edge-count parity assertions in the corpus tests.
- Risk: in-tree compilation makes cairn-intel a compiled distribution —
  mitigation: cibuildwheel matrix in the existing release workflow; sdist
  keeps source builds possible where a toolchain exists.
