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
- **There is no pip-installable "scip" bindings package.** Checked PyPI directly: `scip` is an
  unrelated bioimaging library (Scalable Cytometry Image Processing); Sourcegraph ships no official
  Python bindings (open, unresolved: [sourcegraph/scip#259](https://github.com/sourcegraph/scip/issues/259)).
  Confirmed by generating real bindings from the upstream `scip.proto` with `grpcio-tools` locally —
  the working combination is a **vendored, checked-in generated stub** + the real `protobuf` runtime
  package. `scip.Index.FromString(bytes)` round-trips correctly once generated this way; the module
  is just not importable as `scip` off the shelf.
- **SCIP has two range encodings.** `Occurrence.range`/`enclosing_range` (`repeated int32`) are
  deprecated in `scip.proto` in favor of a `typed_range`/`typed_enclosing_range` oneof
  (`SingleLineRange` / `MultiLineRange`). New producers SHOULD only set the typed form, so an
  importer that reads only the legacy fields will silently get empty ranges from up-to-date indexers.
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

No PyPI package ships Sourcegraph SCIP protobuf bindings (verified: `scip` on PyPI is an unrelated
bioimaging library; see Verified facts). The real dependency is the generic `protobuf` runtime,
paired with a checked-in generated stub — the same approach Google's own `googleapis-common-protos`
and similar libraries use to avoid requiring `protoc`/`grpcio-tools` at install time.

```toml
scip = [
    "protobuf>=5.26",   # runtime for the vendored, checked-in scip_pb2.py stub
]
```

- **`src/cairn/parsers/_scip_pb2.py`** — generated once via
  `python -m grpc_tools.protoc -I. --python_out=. scip.proto` against upstream
  [`sourcegraph/scip`'s `scip.proto`](https://github.com/sourcegraph/scip/blob/main/scip.proto)
  (pin the commit SHA in a header comment for reproducibility), then committed like any other
  source file. Regenerating is a dev-only step (`grpcio-tools` is a `dev` extra, never a runtime
  dependency) — end users installing `cairn-intel[scip]` only ever need `protobuf`.
- **Version coupling.** Modern protoc-generated Python code embeds a
  `_runtime_version.ValidateProtobufRuntimeVersion(...)` check pinned to the generator's own
  `protobuf` version — an installed runtime that's too old raises at import time. Pin the `[scip]`
  extra's floor to whatever the committed stub's header declares, and re-generate + re-pin together
  whenever `grpcio-tools` is upgraded; don't let the two drift independently.

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
`0x0a` — confirmed by serializing a real message; JSON starts with `{`). Parse via
`_scip_pb2.Index.FromString(bytes)` against the vendored stub (see Dependency section — there is no
importable `scip` package). Keep a JSON fallback only for the legacy test.

**D2. Proper edge resolution (two-pass).**
- *Pass 1 — collect definitions:* for occurrences with the `Definition` role, record
  `{symbol_descriptor → (def_symbol_id, file_id, line, col)}`. Store the full descriptor as
  `qualified_name` (lossless, unlike the current `rstrip/split` mangling).
- *Pass 2 — emit symbols + edges:*
  - Definition → symbol with `source='scip'`, real `line_start`/`line_end`/`column_start`/
    `column_end` from the range, `kind` mapped from `syntax_kind`, `docstring` from the
    matching `SymbolInformation.documentation`. Range read order: prefer `typed_range`
    (`single_line_range`/`multi_line_range` oneof — what current indexers emit) and fall back to
    the deprecated `range` (repeated int32) only if the oneof is unset. Reading only the legacy
    field would silently zero out ranges from up-to-date indexers.
  - Non-definition → edge with `source_id` = enclosing definition in that file (via
    `occurrence.typed_enclosing_range`, falling back to the deprecated `enclosing_range`, then to
    nearest preceding def if both are absent), `target_id` = definition looked up in the Pass-1 map
    (`'exact'` if found, `'unresolved'` if external/stdlib), `kind` from roles (`import`→`'import'`,
    access bits→`'reference'`, else `'call'`).

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

### Incremental updates — `graph/incremental.py`

`_build_graph_impl` is not the only write path. `reindex_paths` (`graph/incremental.py`) is the
common entry point for `cairn update` (git-diff), the file watcher's debounced sync, and MCP
catch-up-at-boot — and it is language-blind: for any changed file it deletes that file's existing
symbols/edges/embeddings and unconditionally re-parses with tree-sitter. Left untouched, this
silently reverts a SCIP-covered file to tree-sitter data (dropping `source='scip'` provenance and
exact resolution) on its very next edit, with no signal to the user — since the SCIP index itself
is generated out-of-band and cairn never re-triggers that generation.

**Resolution (decision, not derived from code): fall back to tree-sitter for the edited file.**
Consistent with the config-driven fallback already established for a missing/absent index file —
a single edited file behaves like "no SCIP data available for this file right now." Concretely:
`reindex_paths` re-parses the changed file exactly as it does today; no special-casing needed there.
The only change is provenance-awareness: the row it produces is tagged `source='tree_sitter'` for
that file, which is already correct once the importer rewrite stops hardcoding `source='scip'`
elsewhere. The file's symbols/edges lose exact resolution until the next full `cairn build`
re-imports the (regenerated, out-of-band) SCIP index for that language — a real but bounded and
self-healing staleness window, not a silent permanent downgrade.

Rejected alternative: leave the stale SCIP-sourced rows untouched and skip re-parsing entirely.
Keeps precision but means edited files silently stop reflecting their current content in the graph
until the next full build with no log signal — worse for correctness than a temporary precision dip.

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
| `pyproject.toml` | + `[scip]` optional dependency (`protobuf`, not a nonexistent `scip` package); `grpcio-tools` added to `dev` for regenerating the stub |
| `src/cairn/parsers/_scip_pb2.py` | NEW — generated (not hand-written) from upstream `scip.proto`; header comment pins the source commit SHA and generator version |
| `scripts/regen_scip_pb2.sh` (or Makefile target) | NEW — dev-only regeneration recipe (`grpc_tools.protoc`), documents when/how to re-pin |
| `src/cairn/graph/schema.py` | + `SYMBOL_SOURCE_MIGRATION` |
| `src/cairn/graph/config.py` | + `scip` field + `_SCIP_KEY` + parsing |
| `src/cairn/parsers/scip_importer.py` | **rewrite**: protobuf via `_scip_pb2`, typed-range-aware, real target resolution, no clobbering, `source='scip'` |
| `src/cairn/graph/builder.py` | language-check skip + post-resolve SCIP import hook |
| `src/cairn/graph/incremental.py` | no structural change, but covered by new tests — confirm `reindex_paths` tags `source='tree_sitter'` on re-parse of a previously-SCIP file |
| `src/cairn/cli/core.py` | build summary line; config echo |
| `src/cairn/cli/hooks_viz.py` | `import-scip` reads protobuf (+ `--format` flag) |
| `tests/test_scip_importer.py` | NEW — protobuf import + cross-file resolution + typed-range fixtures |
| `tests/test_build_scip_hybrid.py` | NEW — hybrid build + fallback-to-tree-sitter |
| `tests/test_scip_incremental.py` | NEW — edit a SCIP-covered file post-build, assert `reindex_paths` falls back to tree-sitter for that file and re-`source='scip'` on the next full build |
| `tests/test_big_tech_improvements.py` | update importer stats assertions |
| `tests/test_portable_paths.py` | SCIP path portability case |
| `docs/scip.md` | how to generate an index, wire `cairn.json`, and regenerate `_scip_pb2.py` |
| `CHANGELOG.md` | feature note |

## Risks & mitigations

- **No pip-installable SCIP bindings** → resolved by vendoring a checked-in generated stub
  (`_scip_pb2.py`) + depending on the real `protobuf` package; see Dependency section. Guard the
  import behind an availability check regardless, so a missing/mismatched `protobuf` runtime
  degrades with an install hint rather than crashing tree-sitter-only builds. Magic-byte format
  detection is robust and verified against a real serialized message.
- **Generated-stub / `protobuf` runtime version coupling** → the vendored stub's
  `ValidateProtobufRuntimeVersion` check ties it to a specific `protobuf` floor. Re-generate and
  re-pin together whenever `grpcio-tools` (dev-only) is upgraded; don't let the committed stub and
  the `[scip]` extra's version floor drift apart.
- **Legacy vs. typed range fields** → prefer `typed_range`/`typed_enclosing_range`, fall back to
  the deprecated `range`/`enclosing_range` repeated-int32 forms. Without this, indexes from
  up-to-date indexers (which SHOULD only set the typed form) would silently import with empty
  ranges.
- **Incremental updates bypass the SCIP importer** (`reindex_paths` in `graph/incremental.py`,
  shared by `cairn update`, the file watcher, and MCP catch-up) → resolved by design: on an edited
  SCIP-covered file, fall back to tree-sitter for that file (same as the existing "missing index"
  fallback), tagging it `source='tree_sitter'`. Bounded, self-healing staleness — the next full
  `cairn build` restores `source='scip'` — rather than a silent, permanent, unsignaled downgrade.
- **Missing `enclosing_range`/`typed_enclosing_range`** (some indexers omit both) → fall back to
  nearest preceding definition in the file. Documented degradation, better than the current
  "always last seen".
- **`source` NULL on legacy rows** → treated as `tree_sitter`; a rebuild populates it. No backfill.
- **`syntax_kind` → `kind` mapping** → start coarse (`FUNCTION`→`function`, `CLASS`→`class`,
  `METHOD`→`method`, `MODULE`→`module`, else `scip_symbol`). Graph traversal keys on edges, not kinds.
- **Build ordering** → SCIP import runs before `backup_to`, so the CLI's derived-index passes
  (dataflow, transitive closure) automatically cover SCIP symbols. Verified.
