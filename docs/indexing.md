# Indexing: how code gets into the graph

Read this when you're touching the build path, the resolver, SCIP import, or
wondering what `cairn build` / `cairn update` actually do.

![Indexing pipeline diagram](diagrams/indexing-pipeline.html)

Open [diagrams/indexing-pipeline.html](diagrams/indexing-pipeline.html) for
the full-size version.

## Full build (`cairn build` / `cairn init`)

Entry: `src/cairn/graph/builder.py:build_graph`. Ordered stages:

1. **Scan** — `src/cairn/graph/scanner.py`
   - `discover_repos(workspace)` finds `.git` subdirectories (single-repo
     fallback when the root is itself a repo).
   - Per file, a 4-layer filter: default skip-dirs → gitignore (pathspec,
     nested) → `cairn.json` `exclude` globs → 1 MB size cap. `include`
     overrides the first three, never the cap.
   - Every file hashed (SHA-256). Skips are audited into `skipped_files`
     with reasons: `default_skip`, `gitignored`, `config_exclude`, `size_cap`.

2. **Register repos** — upsert `repos` rows, infer dominant language, pull
   `git_remote` (`src/cairn/utils/git.py`).

3. **Clear** — rebuild deletes that repo's files/symbols/edges/imports/
   embeddings; incoming cross-repo edges are nulled to `unresolved`.
   Crash recovery: `repo_build_state` marks an in-progress rebuild and is
   cleared only on completion.

4. **Parse** — `src/cairn/parsers/` via `ProcessPoolExecutor`
   (`CAIRN_WORKERS`, default cpu_count, capped 256). 14 languages: python,
   typescript, javascript, java, kotlin, swift, go, dart, csharp, c, cpp,
   objc, php, ruby. Each parser returns a `ParsedFile` (symbols, edges,
   imports) — dataclasses in `src/cairn/parsers/base.py`.

5. **Enrich** — best-effort per file: `detect_routes` (route symbols +
   reference edges) and `detect_service_calls` (`http_call` / `service_call`
   edges).

6. **Insert** — `insert_parsed_file` writes `files`, `symbols`
   (`source='tree_sitter'`), `imports`, `edges` (unresolved at this point).
   On-disk builds commit every 500 files.

7. **Resolve** — `src/cairn/graph/resolver.py:resolve_repo_edges`. Priority
   tiers per edge, first tier with candidates wins:

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

8. **SCIP import** (optional) — `src/cairn/parsers/scip_importer.py`. If
   `cairn.json` has a `scip` map (language → index path), the index is
   auto-generated when possible (`scip_indexers.py`) and imported
   (protobuf or JSON). Definitions merge into tree-sitter rows by
   `(file_id, name, line_start)`; SCIP's exact edges replace fuzzy ones
   (`source='merged'`). If the merge rate is 0% (e.g. opaque Swift USRs),
   the language falls back to pure-SCIP rows.

9. **Derived indexes** — `build_dataflow_index` (per-symbol impact sets) and
   `build_transitive_closure` (multi-hop calls at O(1)).

10. **Persist** — full-workspace builds run in-memory, then
    `backup_to` + `swap_db_file` atomically replaces `.kg` (telemetry tables
    are carried across). `--staging` builds to `.kg.tmp` and swaps under the
    build lock. A `build_runs` row records timings and resolution stats.

## Incremental update (`cairn update`)

Entry: `src/cairn/graph/incremental.py`.

- **Change detection**: primary is `git diff --name-only HEAD` filtered to
  indexed extensions; fallback (no git) compares size/mtime with a 0.5s
  tolerance and also catches new + deleted files. `--file <path>` reindexes
  one file.
- **Reindex** (`reindex_paths`): per changed file, in one transaction —
  snapshot old symbol names, delete the file's rows (edges, embeddings +
  ANN vec0 entries, imports, symbols), re-parse and re-insert if the file
  still exists.
- **After**: the resolver re-runs per repo; `repair_incoming_edges`
  re-resolves edges in *other* files that pointed at deleted symbols —
  unique to the incremental path; derived indexes are maintained for just
  the affected symbols (full rebuild only if never built); memory decay runs.

## What agents should know about resolution labels

Precise queries (`get_callers`, `impact_analysis` default) follow only
`exact` edges — ground truth for blast radius. **Empty precise ≠ unused**:
retry `fuzzy=True` before concluding a symbol is dead; fuzzy results are a
candidate list to verify, not truth. Kotlin `operator fun invoke` idioms are
handled (bare and explicit-receiver call shapes retarget correctly).

## Schema quick map

See [architecture.md](architecture.md#key-data-model-facts) for the table
inventory. Indexes of note: `idx_edges_source/target/kind`,
`idx_symbols_name/qualified/file/kind`, `idx_transitive_*`, and trigger-synced
`symbols_fts`.
