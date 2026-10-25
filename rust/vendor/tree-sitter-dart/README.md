# cairn-tree-sitter-dart

Vendored generated parser for the dart tree-sitter grammar consumed by
`cairn-parse`. Do not edit the generated sources by hand.

- Source: https://github.com/efrenbl/tree-sitter-dart (the crates.io
  `tree-sitter-dart` is a different grammar; the golden fixtures were
  captured with this one)
- Tag: `v0.1.0`, commit `12ba73fbfd0755652d97bd10e76a84c076112d14`
- Vendored files: `src/parser.c`, `src/scanner.c`, `src/tree_sitter/*`
- License: MIT (`LICENSE`, upstream notice included)
- Why vendored: upstream ships only an old-style binding (tree-sitter 0.22 as
  a normal dependency), so the generated parser is wrapped behind a
  `tree_sitter_language::LanguageFn` crate instead of pulled as a git
  dependency; the full ruling (D-005) is archived in git history at
  `specs/rust-migration/decisions.md`

Bump procedure: replace the vendored files with the new tag's generated
sources and re-run the 2a node-type revalidation gate before merging.
