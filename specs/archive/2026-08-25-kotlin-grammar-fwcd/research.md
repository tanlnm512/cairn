# Research: kotlin-grammar-fwcd

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-25
<!-- External grounding for tech decisions: every claim below carries a source
     URL/DOI — no unsourced "it is known that". The tech agent consumes this
     file when choosing options in tech-spec.md. -->

> Provenance: written by the orchestrator from the pre-spec research
> session (2026-08-25 — the same conversation that motivated the spec).
> The researcher spawn was skipped per the Researcher gate: every external
> question the spec raised was already answered there. Sources are the
> live repos/pages fetched during that session.

## Questions

### What does cairn use for Kotlin today, and where did it come from?
- **source**: https://pypi.org/project/tree-sitter-kotlin/ · **claim**:
  PyPI `tree-sitter-kotlin` latest 1.1.0 (2025-01-09) is authored by
  amaanq and built from tree-sitter-grammars/tree-sitter-kotlin — an
  independent grammar, NOT fwcd's · **relevance**: FR-001, FR-006 ·
  **confidence**: high
- **source**: https://github.com/tree-sitter-grammars/tree-sitter-kotlin
  (commits/master) · **claim**: 13 commits, initial 2024-08-30, last
  2025-01-16 — dormant 19+ months · **relevance**: the Why of the spec ·
  **confidence**: high
- **source**: local `pyproject.toml:30` (`tree-sitter-kotlin==1.1.0`),
  `src/cairn/parsers/_registry.py:121` (`"kotlin": "tree_sitter_kotlin"`)
  · **claim**: cairn consumes it as a wheel dep on tree-sitter 0.26 ·
  **relevance**: FR-001 · **confidence**: high

### Is fwcd/tree-sitter-kotlin adoptable, and is it worth it?
- **source**: https://github.com/fwcd/tree-sitter-kotlin (commits/main) ·
  **claim**: last commit 2026-08-01; ~35 commits Feb–Aug 2026 with
  substantive Kotlin work — KEEP-0438 destructuring (#251), `when` guard
  conditions (#256/#263), multi-dollar interpolation (#260), trailing
  commas (#252/#264), NUL-byte parse-hang fix (#279), class-vs-infix
  disambiguation (#280) · **relevance**: FR-004 · **confidence**: high
- **source**: same repo README · **claim**: JetBrains PSI
  cross-validation harness, 74/121 (61.2%) structural match among clean
  parses, exclusion list + TODO tracked openly; dormant grammar has no
  equivalent harness · **relevance**: Why · **confidence**: high
- **source**: same repo · **claim**: MIT, 471 commits, 191 stars;
  maintainer also runs the Kotlin LSP/debug adapter · **relevance**: Why ·
  **confidence**: high

### How is fwcd's grammar distributed for Python?
- **source**: https://github.com/fwcd/tree-sitter-kotlin/blob/main/pyproject.toml ·
  **claim**: declares `name = "tree-sitter-kotlin"` v0.4.0 with
  `tree-sitter~=0.22` (stale vs cairn's 0.26); the PyPI name is owned by
  the dormant tree-sitter-grammars build, so it cannot publish under its
  own name · **relevance**: FR-005 — adoption requires vendoring the
  generated parser and building a cairn-owned artifact ·
  **confidence**: high

### Is the node-type vocabulary compatible with cairn's KotlinParser?
- **source**: every node-type literal in `src/cairn/parsers/kotlin.py`
  vs fwcd's raw `grammar.js` + `src/node-types.json` · **claim**: core
  extraction surface matches exactly (`class_declaration`,
  `object_declaration`, `function_declaration`, `property_declaration`,
  `call_expression`, `call_suffix`, `primary_constructor`,
  `class_parameter`, `navigation_expression`, `modifiers`,
  `simple_identifier`, `type_identifier`, `import_header`,
  `constructor_invocation`, `nullable_type`, `this_expression`,
  `function_value_parameters`, `enum_entry`, `class_body`); 7 mismatches
  drive FR-002/FR-003:
  1. `interface_declaration` absent — interfaces are `class_declaration`
     with an `interface` keyword child
  2. `enum_declaration` absent — enums are `class_declaration`
     (`enum class`) with `enum_class_body`
  3. `class_parameters` hidden (`_class_parameters`) — never a node type
  4. `delegation_specifiers` plural hidden — only singular
     `delegation_specifier` visible
  5. `type_reference` hidden (`_type_reference`)
  6. `qualified_identifier` → `identifier`
  7. `inheritance_specifier` not a node — supertypes are
     `delegation_specifier`s; keywords in `inheritance_modifier` ·
  **relevance**: FR-002, FR-003 · **confidence**: high (grammar.js rule
  list; node-types.json cross-check partially truncated at "i")

## Options summary

### Packaging approach (FR-005) — decided in spec: in-tree vendored extension
- in-tree vendored C extension — one package/release path; cairn-intel
  becomes a compiled distribution (CI gains a cibuildwheel matrix)
- separate `cairn-tree-sotlin-kotlin` PyPI package — cairn stays pure
  Python; +1 package to publish and CI forever
- git-URL dependency — zero vendoring; pip compiles C at install time,
  breaks toolchain-free install

### Extraction scope — decided in spec: parity only
- parity only — same symbols/edges on existing code; new syntax parses
  clean (fits "minor update")
- extend extraction — also extract new constructs semantically (past
  minor-update scope)
