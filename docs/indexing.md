# Indexing: how code gets into the graph

← [README](../README.md)

Read this when you're touching the build path, the resolver, or wondering
what `cairn build` / `cairn update` actually do. The engine lives in the
`cairn-build` crate (scanner, builder, resolver, incremental) with
per-language extraction in `cairn-parse`.

## Full build (`cairn build` / `cairn init`)

Entry: `cairn-build` `builder.rs::build_graph`, run under the store's
`build_lock`. Ordered stages:

1. **Scan** — `scanner.rs`
   - repo discovery finds `.git` subdirectories (single-repo fallback when the
     root is itself a repo; `cairn.json` `include_nested_repos` opts nested
     repositories in under prefixed ids).
   - Per file, a 4-layer filter: default skip-dirs → gitignore (nested) →
     `cairn.json` `exclude` globs → 1 MB size cap. `include` overrides the
     first three, never the cap.
   - Every file hashed (SHA-256). Skips are audited into `skipped_files`
     with reasons: `default_skip`, `gitignored`, `config_exclude`,
     `size_cap`, `parser_unavailable`.

2. **Register repos** — upsert `repos` rows, infer dominant language, pull
   `git_remote`.

3. **Clear** — a rebuild deletes that repo's files/symbols/edges/imports/
   embeddings; incoming cross-repo edges are nulled to `unresolved`.
   Crash recovery: `repo_build_state` marks an in-progress rebuild and is
   cleared only on completion.

4. **Parse** — `cairn-parse` on a rayon pool (`CAIRN_WORKERS`, default cpu
   count, clamped 1–256); scan order is restored with a stable sort before
   any insert, so output is independent of parse completion order. 15
   languages: python, typescript, javascript, java, kotlin, swift, go, dart,
   csharp, c, cpp, objc, php, ruby, rust. Each parser returns a `ParsedFile`
   (symbols, edges, imports).

5. **Enrich** — best-effort per file: `routes.rs` (route symbols + reference
   edges) and `service_calls.rs` (`http_call` / `service_call` edges).

6. **Insert** — write `files`, `symbols` (`source='tree_sitter'`),
   `imports`, `edges` (unresolved at this point), `rationale`.

7. **Resolve** — `resolver.rs`. Priority tiers per edge, first tier with
   candidates wins:

   | Tier | Basis |
   |---|---|
   | 0 | type-aware receiver dispatch (walks ancestors) |
   | 1 | same file |
   | 2 | import-aware (direct + containing import match) |
   | 3 | same repo |
   | 4 | global |

   Labels written to `edges.resolution`:
   - `exact` — one candidate; `target_id` set.
   - `ambiguous` — several candidates; `target_id` NULL, `target_name` kept
     for fuzzy queries.
   - `unresolved` — no candidates (stdlib/external).

   The resolver also stores its signals on the edge (`ref_name`,
   `receiver_type`, `call_arity`) so incremental repair can re-resolve
   with the same inputs. `--lsp` exits 1 naming the retired pyright
   upgrade pass.

8. **Derived indexes** — `cairn build --with-closure` materializes
   `transitive_edges` (multi-hop calls at O(1)); the precomputed impact
   index is a separate step, `cairn dataflow build` (capped by
   `--max-symbols` / `CAIRN_DATAFLOW_MAX_SYMBOLS`, default 2000).

9. **Persist** — the default build writes into the store under the build
   lock; `--staging` builds into `<db>.tmp` and atomically swaps it in,
   carrying the telemetry tables across. A `build_runs` row records timings
   and resolution stats.

## Incremental update (`cairn update`)

Entry: `cairn-build` `incremental.rs::incremental_update`, which takes the
build lock before opening its connection.

- **Change detection**, per repo: `--diff-ref A..B` uses exactly
  `git diff -z --name-only A..B`; otherwise `git diff -z --name-only HEAD`
  plus `git ls-files -z --others --exclude-standard` (new untracked files),
  filtered to indexed extensions. Without git, size/mtime drift (0.5 s
  tolerance) is compared, and a drifted mtime whose content hash is
  unchanged only refreshes the stored stat. `--file <path>` reindexes one
  file.
- **Reindex** (`reindex_paths`): per changed file, in one transaction —
  snapshot old symbol names, delete the file's rows (edges, embeddings +
  ANN vec0 entries, imports, symbols, rationale), re-parse and re-insert if
  the file still exists.
- **After**: the resolver re-runs per repo; `repair_incoming_edges`
  re-resolves edges in *other* files that pointed at deleted or renamed
  symbols — unique to the incremental path — and cross-repo `imports` edges
  nulled by the delete leg are re-materialized.

## What agents should know about resolution labels

Precise queries (`get_callers`, `impact_analysis` default) follow only
`exact` edges — ground truth for blast radius. **Empty precise ≠ unused**:
retry `fuzzy=True` before concluding a symbol is dead; fuzzy results are a
candidate list to verify, not truth. Kotlin `operator fun invoke` idioms are
handled (bare and explicit-receiver call shapes retarget correctly).

## Parser conformance matrix

One row per golden-fixtured language (`tests/fixtures/golden/`); every row
is green (the `cairn-parse` golden tests assert each `expected.json`) or
explicitly tier-marked with the feature gap. Regenerate with
`scripts/gen_conformance_table.py`; `--check` fails when the committed table
drifts from the fixtures.

<!-- conformance:begin (generated by scripts/gen_conformance_table.py) -->

| Language | Golden | Symbols | Edges | Imports | Status |
|---|---|---|---|---|---|
| c | green | 4 | 2 | 3 | no hierarchy edges |
| cpp | green | 7 | 4 | 3 | green |
| csharp | green | 13 | 5 | 3 | green |
| dart | green | 11 | 3 | 1 | green |
| go | green | 8 | 3 | 2 | no hierarchy edges |
| java | green | 11 | 3 | 1 | green |
| javascript | green | 7 | 2 | 1 | green |
| kotlin | green | 12 | 12 | 1 | green |
| objc | green | 16 | 10 | 1 | green |
| php | green | 19 | 12 | 4 | green |
| python | green | 7 | 5 | 4 | green |
| ruby | green | 7 | 18 | 2 | green |
| rust | green | 5 | 2 | 0 | no import edges; no hierarchy edges |
| swift | green | 11 | 4 | 1 | green |
| typescript | green | 13 | 3 | 1 | green |

<!-- conformance:end -->

## Schema quick map

See [architecture.md](architecture.md#key-data-model-facts) for the table
inventory. Indexes of note: `idx_edges_source/target/kind`,
`idx_symbols_name/qualified/file/kind`, `idx_transitive_*`, and trigger-synced
`symbols_fts`.
