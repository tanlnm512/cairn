# Hybrid SCIP / Tree-sitter Indexing

> **Status:** Design document, not yet implemented.
> **Goal:** Make `cairn build` SCIP-aware by language — use a pre-built SCIP index
> when one is configured and present; fall back to tree-sitter otherwise.

## Motivation

Today cairn indexes every language with tree-sitter. Edge resolution is best-effort:
a multi-tier resolver (same-file → import-aware → same-repo → global) pins `target_name`
to a symbol, but many edges stay `unresolved` or `ambiguous`. On a real workspace the
build reports ~34% exact, ~9% ambiguous, ~57% unresolved.

SCIP (Sourcegraph Code Intelligence Protocol) indexes are produced by compiler-grade
indexers (`scip-kotlin`, `scip-typescript`, `scip-java`, ...) and carry **exact** symbol
bindings — every reference occurrence names the definition it points to, so resolution is
a single dict lookup instead of 4 tiers. The payoff: languages with heavy overload/generic
usage (Kotlin, Java, TypeScript) get accurate call graphs; everything else keeps tree-sitter's
zero-setup freshness.

Cairn stays a **consumer** of SCIP indexes, never a producer. Indexes are generated
out-of-band (CI, a make target) and pointed at via config.

---

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Index format | **Real SCIP protobuf** | What production indexers emit. Current importer only parses JSON despite its docstring. |
| Importer quality | **Rewrite now** | The current importer has three real bugs (below); a hybrid is only worth it if SCIP data is actually correct. |
| Index discovery | **Explicit config paths** | `cairn.json` declares each language's index file. No magic convention scanning. Absent file → tree-sitter fallback. |

### Bugs in the current importer (to fix in the rewrite)
1. **No real edge resolution.** `target_id` is never set; edges use a "rolling last definition"
   heuristic for `source_id` and hardcode `resolution='exact'`. Cross-file targets are wrong.
2. **Clobbers tree-sitter data.** `files` uses `INSERT OR REPLACE` with `hash='scip_imported'`,
   `line_count=0`, overwriting real tree-sitter metadata.
3. **Single-line collapse.** Definitions store `line_end = line_start` and ignore columns/end-line.

---

## Verified facts (from the codebase)

- **No protobuf deps exist.** `scip` / `protobuf` / `sourcegraph` are absent from `pyproject.toml`
  and `uv.lock`. A new `[scip]` optional dependency is needed (pattern: the `semantic`/`ann` extras).
- **SCIP resolution is simpler than tree-sitter's.** Each `Occurrence.symbol` (a descriptor like
  `"scip-python python main main_func ."`) is shared between a definition and its references.
  One pass builds `{descriptor → def_symbol_id}`; each reference's `target_id` resolves directly.
- **`source` column is FTS5-safe.** The triggers (`schema.py:82-103`) only reference
  `rowid, name, qualified_name, docstring`. Additive ALTER is invisible to them; existing
  migrations (`metadata`, `parameters`, ...) already followed this pattern.
- **Build hook point:** between `_resolve_all` and `backup_to` in `_build_graph_impl`
  (`builder.py`). After tree-sitter resolution (SCIP's exact edges aren't re-resolved), before
  persist (captured by `backup_to` for in-memory builds). Works for both `repo_filter` modes.
- **Workspace root is available** at `builder.py` (`ws_root`) for `cairn.json` lookup.
- **One tiny test exists** (`test_big_tech_improvements.py:54-79`) against the JSON path; no sample
  SCIP index file exists anywhere. Tests will construct a protobuf `Index` programmatically.

---

## Design

### Config — `cairn.json`

```json
{
  "scip": {
    "kotlin": "build/scip/kotlin.scip",
    "typescript": "build/scip/ts.scip"
  }
}
```

Maps `language → index file path` (relative to workspace root). At build time, cairn resolves
each path and keeps only languages whose index file **actually exists** → that set drives the
tree-sitter skip. Absent/missing file is the fallback.

### Dependency — `pyproject.toml`

```toml
scip = [
    "scip>=0.5.0",   # compiled scip.proto bindings (Sourcegraph)
]
```

Guarded import (same pattern as torch/numpy for the semantic stack). Tree-sitter-only builds
never import it; missing extra degrades with an install hint.

### Schema — `symbols.source`

```python
SYMBOL_SOURCE_MIGRATION = "ALTER TABLE symbols ADD COLUMN source TEXT"
```

Provenance column: `'tree_sitter'` or `'scip'`. Appended to `MIGRATIONS`; auto-named
`symbols.source`; idempotent. NULL on legacy rows is treated as `tree_sitter`. Set in the
INSERT (not a separate UPDATE) to avoid FTS churn.

### Importer rewrite — `parsers/scip_importer.py`

**D1. Protobuf parsing.** Detect format by magic bytes (protobuf `Index.documents` starts with
`0x0a`; JSON starts with `{`). Parse via `scip.Index.FromString(bytes)`. Keep a JSON fallback
only for the legacy test.

**D2. Proper edge resolution (two-pass).**
- *Pass 1 — collect definitions:* for occurrences with the `Definition` role, record
  `{symbol_descriptor → (def_symbol_id, file_id, line, col)}`. Store the full descriptor as
  `qualified_name` (lossless, unlike the current `rstrip/split` mangling).
- *Pass 2 — emit symbols + edges:*
  - Definition → symbol with `source='scip'`, real `line_start`/`line_end`/`column_start`/
    `column_end` from the `Range`, `kind` mapped from `syntax_kind`, `docstring` from the
    matching `SymbolInformation.documentation`.
  - Non-definition → edge with `source_id` = enclosing definition in that file (via
    `occurrence.enclosing_range`, falling back to nearest preceding def if absent),
    `target_id` = definition looked up in the Pass-1 map (`'exact'` if found, `'unresolved'`
    if external/stdlib), `kind` from roles (`import`→`'import'`, access bits→`'reference'`,
    else `'call'`).

**D3. Don't clobber tree-sitter rows.** `files`: `INSERT OR IGNORE` (don't overwrite hash/
line_count/size/mtime). `repos.path`: workspace-relative (`.` or repo name) consistent with the
portability work. `symbols`: `INSERT OR IGNORE`. Stats use `cursor.rowcount`, not loop counters.

### Build integration — `graph/builder.py`

In `_build_graph_impl`, after scanning:
1. Load `cfg.scip` from `ws_root`. Resolve each index path; keep languages whose file exists →
   `scip_languages: dict[lang, abs_index_path]`. (This is the fallback: absent → tree-sitter.)
2. Partition scanned files: those in `scip_languages` are **not** tree-sitter-parsed.
3. **Post-resolve hook** (after `_resolve_all`, before `backup_to`): for each configured
   `(lang, index_path)`, run the rewritten importer on the live `conn`. Captured by `backup_to`
   for in-memory builds; runs before the CLI's `build_dataflow_index`/`build_transitive_closure`
   passes so derived indexes cover SCIP symbols too.

`repo_id` derivation: repo basename, consistent with `files.repo_id`. Multi-repo maps
`relative_path`→repo via `infer_repo_for_path`.

### CLI

- **`cairn build`:** no new flags — discovery is config-driven. Summary line reports which
  languages used SCIP and symbol counts, or "tree-sitter for all".
- **`cairn import-scip`:** kept as the manual escape hatch; reads protobuf with a `--format`
  flag (default `proto`; `json` for the legacy path).
- **`cairn config`:** echoes resolved `scip` config + whether each index file exists.

---

## Change list

| File | Change |
|------|--------|
| `pyproject.toml` | + `[scip]` optional dependency |
| `src/cairn/graph/schema.py` | + `SYMBOL_SOURCE_MIGRATION` |
| `src/cairn/graph/config.py` | + `scip` field + `_SCIP_KEY` + parsing |
| `src/cairn/parsers/scip_importer.py` | **rewrite**: protobuf, real target resolution, no clobbering, `source='scip'` |
| `src/cairn/graph/builder.py` | language-check skip + post-resolve SCIP import hook |
| `src/cairn/cli/core.py` | build summary line; config echo |
| `src/cairn/cli/hooks_viz.py` | `import-scip` reads protobuf (+ `--format` flag) |
| `tests/test_scip_importer.py` | NEW — protobuf import + cross-file resolution |
| `tests/test_build_scip_hybrid.py` | NEW — hybrid build + fallback-to-tree-sitter |
| `tests/test_big_tech_improvements.py` | update importer stats assertions |
| `tests/test_portable_paths.py` | SCIP path portability case |
| `docs/scip.md` | how to generate an index and wire `cairn.json` |
| `CHANGELOG.md` | feature note |

## Risks & mitigations

- **`scip` package availability/format drift** → guard import behind availability check; degrade
  with install hint. Magic-byte format detection is robust.
- **Missing `enclosing_range`** (some indexers omit it) → fall back to nearest preceding
  definition in the file. Documented degradation, better than the current "always last seen".
- **`source` NULL on legacy rows** → treated as `tree_sitter`; a rebuild populates it. No backfill.
- **`syntax_kind` → `kind` mapping** → start coarse (`FUNCTION`→`function`, `CLASS`→`class`,
  `METHOD`→`method`, `MODULE`→`module`, else `scip_symbol`). Graph traversal keys on edges, not kinds.
- **Build ordering** → SCIP import runs before `backup_to`, so the CLI's derived-index passes
  (dataflow, transitive closure) automatically cover SCIP symbols. Verified.
