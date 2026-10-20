# cairn-tree-sitter-dart

Vendored generated parser for the dart tree-sitter grammar consumed by
`cairn-parse`. Do not edit the generated sources by hand.

- Source: https://github.com/efrenbl/tree-sitter-dart (the grammar the Python
  `tree-sitter-dart==0.1.0` wheel ships; the crates.io `tree-sitter-dart` is a
  different grammar)
- Tag: `v0.1.0`, commit `12ba73fbfd0755652d97bd10e76a84c076112d14`
- Vendored files: `src/parser.c`, `src/scanner.c`, `src/tree_sitter/*`
- License: MIT (`LICENSE`, upstream notice included)
- Pin rationale: `specs/rust-migration/decisions.md` (D-005)

Bump procedure: replace the vendored files with the new tag's generated
sources and re-run the 2a node-type revalidation gate before merging.
