# Bug Registry

Lessons learned, root-caused. Each entry is a one-time discovery converted to
permanent, queryable memory — so the same bug doesn't get solved twice.

Search this when you hit something unfamiliar: the symptom, the file, or the
mechanism. If you find a new bug, add an entry **before** fixing it, while the
pain is fresh.

## How to use this file

1. **Scan the index table** below for the symptom, area, or date you need.
2. **Jump to the entry** (same slug) under `## Entries` for the full
   symptom → root cause → fix → prevention → related detail.
3. For a deeper architecture writeup, follow the postmortem link if present.

## Format

Each entry: **TL;DR** (one line) → symptom (what went wrong) → root cause (why)
→ fix (what was done) → prevention (what guard stops it now) → related (files,
tests, commits).

Keep entries to one paragraph per field. If a bug needs a deeper writeup
(architecture-level root cause, multi-file analysis, decision rationale
worth re-reading), put the long form in `docs/postmortems/<date>-<slug>.md`
and leave a one-line pointer here — `[→ postmortem](postmortems/...)`. This
keeps the registry scannable; detail lives separately.

## Index

| Date | Slug | Area | Symptom (one line) |
|------|------|------|--------------------|
| 2026-08-06 | comments-only-code-drift | agent-safety | Sub-agents silently altered code during a "comments-only" trim task. |
| 2026-08-06 | portable-path-stale-comments | graph | Comments claimed relative paths; builder stored absolute. [→ postmortem](postmortems/2026-08-06-portable-paths.md) |
| 2026-08-06 | scip-pypi-package-misidentification | deps | Plan specified a `scip` pip package that's an unrelated bioimaging lib. |
| 2026-08-06 | repo-id-empty-string-myth | graph | Comments claimed `repo_id=''`; builder stored the directory basename. |
| 2026-08-06 | scip-importer-fake-resolution | parser | SCIP importer labeled edges `'exact'` with `target_id` always NULL. |
| 2026-08-10 | record-memory-unredacted-secrets | security | `record_memory` persisted secrets verbatim; hook path redacted, MCP path didn't. |
| 2026-08-10 | inverted-parse-error-telemetry | graph | Clean build reported ~100% parse errors; broken build reported 0. |
| 2026-08-10 | rrf-fusion-silently-skipped | retrieval | `semantic_search` fusion never ran — `.get()` on `sqlite3.Row` was swallowed. |
| 2026-08-10 | clear-repo-stale-exact-resolution | graph | `_clear_repo` nulled `target_id` but left `resolution='exact'` on orphaned edges. |
| 2026-08-10 | scip-import-partial-write-no-rollback | graph | Failed SCIP import left partial writes that a later commit persisted. |
| 2026-08-10 | schema-init-flag-before-migration | graph | DB path marked initialized before migration applied; failures became permanent. |
| 2026-08-10 | raw-memory-tier-id-collision | memory | Two same-day raw captures with the same title overwrote each other. |
| 2026-08-10 | knowledge-status-missing-scope-guard | security | `knowledge_status` could archive non-knowledge docs (no namespace guard). |
| 2026-08-10 | dead-depends-on-key-in-knowledge-search | retrieval | Cross-repo bridge line in `knowledge_search` never printed (wrong dict key). |
| 2026-08-10 | swift-modifier-attribute-pollution | parser | Swift modifier extraction included `@available` attribute text. |
| 2026-08-10 | dead-appcontext-lifespan-scaffolding | architecture | `AppContext` lifespan was wired but no tool consumed it — dead contract. |
| 2026-08-14 | wal-sidecar-swap-build-loss | graph | `--staging`/`backup_to` swap left the old `-wal`; new connections read the PRE-BUILD graph — build silently lost. |
| 2026-08-14 | build-lock-loser-unlink | graph | Failed flock acquire unlinked the WINNER'S lock file → deterministic double-acquire → corrupted swap. |
| 2026-08-14 | knowledge-layer-unredacted | security | Knowledge add/import/workflow persisted title+body verbatim — 4th codepath-divergence redaction incarnation. |
| 2026-08-14 | watchdog-exit-bypasses-atexit | server | stdio parent-death `os._exit(0)` skipped atexit → ≤30s metrics/events + 15s embeddings lost per session end. |
| 2026-08-14 | sweeper-matched-nothing | server | Stray-sweeper patterns matched argv shapes editors never spawn → orphans (the leak class) stayed invisible; fallback could kill other-DB servers. |
| 2026-08-14 | parser-golden-self-validation | parser | Golden fixtures regenerated from parser output — systematic drops (Ruby chains, PHP ns-calls, Kotlin properties) shipped green. |
| 2026-08-14 | telemetry-config-vs-execution | telemetry | `semantic_backend` reported configured fusion/rerank, not what ran — provenance metric lied on degrade. |

## Entries

---

### 2026-08-06 / comments-only-code-drift
**TL;DR:** Sub-agents silently altered executable code during a "comments-only" trim; an AST-comparison guard now catches it.

**Symptom:** After a bulk comment-trimming task run by 5 parallel sub-agents, 4 files had their executable code silently altered (string literals in CLI help text changed in `cli/query.py` and `cli/serve.py`, a `print()` message shortened in `agent_integration/skill/scripts/impact_guard.py`, and a variable renamed `legacy_repo_root` → `repo_root` in `compass/generator.py`). The agents self-reported "no executable code changed" — this was false.

**Root cause:** Reading diffs visually misses string-literal and variable-rename changes, especially across 84 files. Sub-agents reliably self-report success even when their edits cross into code. The failure is structural: comments and code share the same text buffer, so an agent editing comments can accidentally include adjacent code in its `old_string`/`new_string` blocks.

**Fix:** All 4 oversteps were reverted by hand. An AST-comparison script (`scripts/verify_no_code_change.py`) was created that blanks docstrings and compares `ast.dump()` of changed files — catching any executable-code difference.

**Prevention:** Run `make verify-no-code-change` (or `make verify-no-code-change REF=HEAD~1` for a committed change) after any "comments-only" / "docs-only" / "refactor" change. Documented in `docs/release-checklist.md` under "Agent / bulk-edit safety".

**Related:** `scripts/verify_no_code_change.py`, `docs/release-checklist.md`, commits `e710543`, `8c91ae4`.

---

### 2026-08-06 / portable-path-stale-comments
**TL;DR:** Comments claimed relative paths; the builder stored absolute — half-finished design made the comments lie. [→ postmortem](postmortems/2026-08-06-portable-paths.md)

**Symptom:** The watcher (`graph/watcher.py`) and incremental reindexer (`graph/incremental.py`) contained comments and fallback logic claiming "build stores relative paths" and "repo_id='' for single-repo workspaces." Neither was true: the builder (`graph/builder.py`) stored absolute paths, and repo_id was the directory basename, not empty string. The code was half-finished — designed for relative paths but never completed.

**Root cause:** A design decision (store relative paths for portability) was partially implemented and documented in comments before the builder was updated. The comments drifted into lies, and the fallback logic in watcher/incremental existed to paper over the mismatch, making the inconsistency invisible.

**Fix:** The builder was updated to store repo-relative paths (`files.path`, `repos.path`, `parse_errors.file_path`, `skipped_files.path`). A single chokepoint (`scanner.resolve_file_path`) resolves them to absolute at disk-I/O time. Legacy absolute paths pass through unchanged for backward compat. Stale comments were corrected/removed.

**Prevention:** Invariant test `test_files_path_always_relative_after_build` in `tests/test_invariants.py` — if anything ever stores an absolute path again, CI fails. General lesson: don't leave a design decision half-implemented with comments describing the intended state; either implement it or don't write the comment.

**Related:** `src/cairn/graph/scanner.py:resolve_file_path`, `tests/test_portable_paths.py`, commit `29c6e62`.

---

### 2026-08-06 / scip-pypi-package-misidentification
**TL;DR:** A plan specified a `scip` pip dependency that is an unrelated bioimaging library, not Sourcegraph's bindings.

**Symptom:** A design plan (`docs/scip-hybrid-plan.md`) specified `"scip>=0.5.0"` as a pip dependency for Sourcegraph's SCIP protobuf bindings. This package does not exist on PyPI — `scip` on PyPI is an unrelated bioimaging library (Scalable Cytometry Image Processing). Sourcegraph ships no official Python bindings (open issue: sourcegraph/scip#259).

**Root cause:** The dependency was asserted from assumption ("there's probably a pip package for this") rather than verified empirically. No one ran `pip install scip` or checked PyPI before writing the plan.

**Fix:** The plan was corrected to specify a vendored generated stub (`_scip_pb2.py` generated from upstream `scip.proto` via `grpcio-tools`) + the generic `protobuf` runtime package. Verified by actually generating bindings locally and round-tripping a real SCIP message.

**Prevention:** Process rule: before specifying any dependency in a plan or `pyproject.toml`, verify it exists on PyPI and is the package you think it is (`pip index versions <name>` or check pypi.org). Empirical verification beats assumption. Add this to `docs/release-checklist.md`.

**Related:** `docs/scip-hybrid-plan.md`, commit `8bca34b`.

---

### 2026-08-06 / repo-id-empty-string-myth
**TL;DR:** Comments claimed `repo_id=''` for single-repo workspaces; the builder stored the directory basename.

**Symptom:** Comments in `graph/incremental.py:64` and `graph/watcher.py:64` stated "build_graph stores repo_id='' for single-repo workspaces." This was false — the builder stores the repo directory basename (e.g. `"cairn"`), not an empty string. The false belief led to a `SELECT ALL` fallback in watcher.py that masked the real behavior.

**Root cause:** The comment was likely written during an earlier code iteration where repo_id *was* empty, and never updated when the builder changed to use `repo_path.name`. No test validated the actual stored value, so the lie persisted undetected.

**Fix:** Stale comments removed. The `SELECT ALL` fallback in watcher.py was re-commented accurately (it's now a safety net for workspace-name mismatches, not for empty repo_id).

**Prevention:** When a comment describes a specific runtime value ("stores X", "returns Y"), it should be backed by a test that asserts that value. Comments about runtime behavior rot; tests don't.

**Related:** `src/cairn/graph/incremental.py`, `src/cairn/graph/watcher.py`, `src/cairn/graph/scanner.py`.

---

### 2026-08-06 / scip-importer-fake-resolution
**TL;DR:** SCIP importer labeled edges `'exact'` while `target_id` was always NULL — silently wrong data presented as trustworthy.

**Symptom:** The SCIP importer (`parsers/scip_importer.py`) wrote edges with `resolution='exact'` but never resolved `target_id` — it was always NULL. The `source_id` used a "rolling last definition" heuristic (whichever definition was most recently seen in document order), not the actual enclosing scope. Cross-file call targets were structurally wrong.

**Root cause:** The importer consumed only 3 fields from each SCIP occurrence (`symbol`, `range`, `symbol_roles`) and ignored the fields that enable real resolution (the shared `symbol` descriptor between definition and reference, the `relationships` list, `enclosing_range`). It then labeled the result `'exact'` despite no resolution occurring — the worst kind of bug: silently wrong data presented as trustworthy.

**Fix:** Fixed before 0.6.1. The importer now builds a `{symbol_descriptor → def_symbol_id}` map from definition occurrences (pass 1), resolves each reference's `target_id` via that map, computes `source_id` from `enclosing_range` (with a nearest-preceding-definition fallback), and sets `resolution='exact'` only when `target_id` is actually found (`scip_importer.py:540-542`). A reference whose target is not in the index is tagged `'unresolved'`, never `'exact'`.

**Prevention:** Never label data `'exact'` or `'verified'` unless the code path that produced it actually performs the verification. The `resolution` column is a trust signal consumed by precise-by-default queries (`get_callers`, `impact_analysis`); false `'exact'` rows silently pollute blast-radius analysis. Guarded by two tests: `tests/test_invariants.py::test_invariant_exact_resolution_has_target_id` (the schema-level invariant) and `tests/test_scip_importer.py::test_protobuf_cross_file_resolution_is_exact` / `test_protobuf_external_reference_is_unresolved` (the importer-driven end-to-end check).

**Related:** `src/cairn/parsers/scip_importer.py:540-542`, `src/cairn/graph/traversal.py:STRUCTURAL_EDGE_KINDS`, `tests/test_invariants.py:213`, `tests/test_scip_importer.py:54,87`, `docs/scip-hybrid-plan.md`.

---

### 2026-08-10 / record-memory-unredacted-secrets
**TL;DR:** `record_memory` persisted secrets verbatim; the hook path redacted but the primary MCP write path didn't.

**Symptom:** `record_memory` (MCP tool) persisted secrets verbatim — an agent recording a memory whose body contained a token/secret (API key, bearer token) wrote it to disk, recallable later via `recall_memory`.

**Root cause:** The hook-based auto-capture path (`hooks/claude_hooks.py`) called `strip_private_data` before storing, but the primary MCP write path (`record_memory` → `capture_memory`) did not. The redaction was applied at each caller boundary instead of at the shared storage chokepoint, so the MCP path — the most common write path — missed it.

**Fix:** `capture_memory` (`memory/promotion.py`) now calls `strip_private_data(body)` before scoring/storing. Every caller (MCP tool, CLI, hook) gets the redaction for free.

**Prevention:** Put security-sensitive transformations at the shared chokepoint, not each caller boundary — a new caller can always forget the per-call redaction. Regression tests: `tests/test_redaction_chokepoints.py` (41 tests, every entry point incl. the store-chokepoint guards added 2026-08-14) + `tests/test_memory_lifecycle.py::TestCaptureMemoryRedaction`.

**Related:** `src/cairn/memory/promotion.py`, `src/cairn/memory/privacy.py`.

---

### 2026-08-10 / inverted-parse-error-telemetry
**TL;DR:** A clean build reported ~100% parse errors; a broken build reported 0 — off-by-one into the wrong tuple slot.

**Symptom:** A clean build (all files parse successfully) reported ~100% parse errors; a fully broken build reported 0. Build-health signals meant the opposite of what they said.

**Root cause:** `builder.py:210` counted `r[5] is not None` — slot 5 is the successful-parse payload (`pf`), not the error slot (`err`, slot 6). Tuple shape: `(path, rel_path, language, repo, fi_hash, pf, err, st)`. A classic off-by-one into the wrong semantic slot.

**Fix:** Changed `r[5]` to `r[6]` with a comment documenting the tuple layout.

**Prevention:** When indexing into a positional tuple, a `NamedTuple` makes the field name compile-checked. For raw tuples, an inline comment documenting the slot semantics catches index drift. Regression test: `tests/test_audit_remediation.py::test_p2_*`.

**Related:** `src/cairn/graph/builder.py:210`.

---

### 2026-08-10 / rrf-fusion-silently-skipped
**TL;DR:** `semantic_search` fusion never ran — `.get()` on `sqlite3.Row` raised and was swallowed by a bare `except`.

**Symptom:** `semantic_search` under default settings (RRF fusion on) silently degraded to vector-only ranking on every call instead of the documented BM25+vector blend. Results came back, but fusion never ran.

**Root cause:** The fusion path called `r.get("id")` on `sqlite3.Row` objects returned by `search_symbols`. `sqlite3.Row` has no `.get()` method; the resulting `AttributeError` was swallowed by a bare `except Exception: pass` around the fusion block, so the degradation was invisible. The exception was logged at `debug` level, which no default log config surfaces.

**Fix:** Convert BM25 rows to `dict` at the boundary so `.get()` works uniformly. Narrowed the exception logging from `debug` to `warning` so a future regression in this path is visible.

**Prevention:** A bare `except Exception: pass` is a bug factory — it converts hard failures into silent fallbacks. When graceful degradation is intentional, log at `warning` (not `debug`) so the degradation is observable. Regression test: `tests/test_audit_remediation.py::test_p3_semantic_search_fusion_runs`.

**Related:** `src/cairn/graph/semantic.py:190`.

---

### 2026-08-10 / clear-repo-stale-exact-resolution
**TL;DR:** `_clear_repo` nulled `target_id` but left `resolution='exact'`, so precise queries treated dangling edges as resolved.

**Symptom:** After a single-repo rebuild (`_clear_repo`), precise-mode queries (`get_callers`, `impact_analysis`) treated orphaned cross-repo edges (whose `target_id` had been nulled) as resolved — producing wrong or incomplete blast-radius results.

**Root cause:** `_clear_repo` (`builder.py:884`) nulled `target_id` on orphaned edges but did not reset `resolution` back to `'unresolved'`. The equivalent path in `incremental.py:135` does both. Precise queries filter on `resolution='exact'`, so dangling edges with `target_id IS NULL AND resolution='exact'` polluted results.

**Fix:** Added `resolution = 'unresolved'` to the same UPDATE statement, mirroring the incremental path.

**Prevention:** When two code paths perform the same logical operation (clearing edges), they must reset the same columns — drift between them creates silent data-quality bugs. Regression test: `tests/test_audit_remediation.py::test_p4_clear_repo_leaves_no_exact_resolution_without_target`. Related: `[[indexing-workflow-audit-findings]]`.

**Related:** `src/cairn/graph/builder.py:884`, `src/cairn/graph/incremental.py:135`.

---

### 2026-08-10 / scip-import-partial-write-no-rollback
**TL;DR:** A failed SCIP import left partial writes on `conn`; a later unrelated commit persisted them into the graph.

**Symptom:** A failed SCIP import left half-imported rows pending on the shared `conn`; a later unrelated `conn.commit()` in the same build persisted them, silently mixing a broken SCIP index into an otherwise-successful graph.

**Root cause:** `import_scip_file` commits at the end of a successful import but does not roll back on failure. The builder's `except` block caught the exception and logged it but never called `conn.rollback()`, leaving the partial writes pending.

**Fix:** The builder's `except` block now calls `conn.rollback()` before logging, scoping the revert to only the failed import's pending writes (earlier committed work is unaffected).

**Prevention:** When catching an exception from a transactional operation that may have left partial writes, always roll back before continuing — a pending write on a shared connection will eventually be committed by something unrelated. Regression test: `tests/test_audit_remediation.py::test_p5_failed_scip_import_rolls_back_pending_writes`.

**Related:** `src/cairn/graph/builder.py:527`, `src/cairn/parsers/scip_importer.py`.

---

### 2026-08-10 / schema-init-flag-before-migration
**TL;DR:** DB path was marked initialized before migration applied; a mid-migration failure permanently skipped schema setup.

**Symptom:** If `_apply_schema` raised mid-migration (disk full, locked file), the DB path was already marked "initialized" in `_INITIALIZED_PATHS`; every later `get_db(path)` call in that process skipped schema application permanently, and unrelated queries later failed with "no such column" far from the real cause.

**Root cause:** `_INITIALIZED_PATHS.add(key)` ran before `_apply_schema`/`_maybe_backfill_fts`/`commit()`. The `_INIT_LOCK` prevented a second thread from seeing a half-initialized connection, but did not protect against the path being marked initialized while the migration failed.

**Fix:** Moved `_INITIALIZED_PATHS.add(key)` to after the migration+commit succeed. A forced-failure retry now re-attempts schema application.

**Prevention:** A "done" flag must be set after the work it guards completes, not before — otherwise a failure leaves the system in a permanently broken state that masks the original error. Regression test: `tests/test_audit_remediation.py::test_p6_init_flag_not_set_on_migration_failure`.

**Related:** `src/cairn/graph/schema.py:427`.

---

### 2026-08-10 / raw-memory-tier-id-collision
**TL;DR:** Raw memory tier used `date+slug` with no uuid; same-title same-day captures overwrote each other.

**Symptom:** Two same-day raw memory captures with identically-slugified titles (common from the generic titles auto-capture hooks produce) silently overwrote each other via `bundle.write_concept`.

**Root cause:** `store_memory` built the raw tier's `concept_id` from only `date + slug`, unlike every other tier which appends a uuid suffix specifically to prevent same-title collisions.

**Fix:** Appended the same uuid-suffix scheme the other tiers use, keeping the date prefix (so decay can still purge by age).

**Prevention:** When a naming scheme has a collision-avoidance suffix, apply it uniformly — a single tier without it creates a silent data-loss path. Regression test: `tests/test_audit_remediation.py::test_p7_raw_tier_ids_are_collision_safe`.

**Related:** `src/cairn/memory/store.py:110`.

---

### 2026-08-10 / knowledge-status-missing-scope-guard
**TL;DR:** `knowledge_status` could archive non-knowledge docs via a crafted `doc_id` — sibling `knowledge_delete` had the guard, it didn't.

**Symptom:** `knowledge_status` could archive a compass/wiki/memory concept that was never a knowledge doc, via a crafted `doc_id`.

**Root cause:** Its sibling `knowledge_delete` enforces a doc-scope check (explicitly there "so an LLM client can't point this destructive tool at a compass/wiki/memory doc via a crafted doc_id"), but `knowledge_status` — four lines below — did not.

**Fix:** Applied the same namespace guard `knowledge_delete` uses.

**Prevention:** When two sibling tools operate on the same namespace, both need the same scope guard — a guard on the "more destructive" one doesn't protect the other. Regression test: `tests/test_audit_remediation.py::test_p8_knowledge_status_*`.

**Related:** `src/cairn/mcp_server/tools_knowledge.py:137`.

---

### 2026-08-10 / dead-depends-on-key-in-knowledge-search
**TL;DR:** Cross-repo bridge line in `knowledge_search` never printed — code read `depends_on`, the actual key is `dependencies`.

**Symptom:** The documented "cross-repo bridge" enrichment line in `knowledge_search` output (and its CLI duplicate) never printed — in both the MCP tool and CLI.

**Root cause:** The code read `deps.get("depends_on")`, but `cross_repo_deps` has only ever returned `dependencies`/`dependents`. The key never matched, so the line was dead since it was written.

**Fix:** Changed to `deps.get("dependencies")` and extract the `repo` field from each dependency entry (it's a list of dicts, not strings). Fixed in both MCP tool and CLI duplicate.

**Prevention:** When consuming a function's return value, the key names must match the actual return shape — a typo'd key fails silently against a dict. Regression test: `tests/test_audit_remediation.py::test_p9_knowledge_search_renders_cross_repo_bridge`.

**Related:** `src/cairn/mcp_server/tools_knowledge.py:88`, `src/cairn/cli/knowledge.py:216`, `src/cairn/graph/cross_repo.py:125`.

---

### 2026-08-10 / swift-modifier-attribute-pollution
**TL;DR:** Swift modifier extraction included `@available` attribute text — the nested path didn't filter like the direct-child path.

**Symptom:** Swift modifier extraction included attribute text (e.g. `@available(iOS 14, *)`) in a symbol's modifier list — a silent, language-specific data-quality drift.

**Root cause:** `_collect_modifiers`'s nested `modifiers`-node path appended any non-empty child text unconditionally, while the direct-child path filtered through `SWIFT_MODIFIERS`. The tree-sitter Swift grammar nests `attribute` children inside `modifiers` (confirmed empirically), so they leaked through.

**Fix:** Filter the nested path through `SWIFT_MODIFIERS` too, matching the direct-child path and the Java/Kotlin extractors.

**Prevention:** When two branches of a function perform the same logical operation, both must apply the same filter — asymmetry creates silent data-quality drift that only surfaces under specific input shapes. Regression test: `tests/test_audit_remediation.py::test_p10_swift_modifiers_filtered`.

**Related:** `src/cairn/parsers/swift.py:118`.

---

### 2026-08-10 / dead-appcontext-lifespan-scaffolding
**TL;DR:** `AppContext` lifespan was wired into FastMCP but no tool consumed it — a half-finished refactor implying a contract that didn't hold.

**Symptom:** `AppContext` / `app_lifespan` in `_server_core.py` existed to thread `db_path`/`knowledge_path`/`read_only` through requests, but none of the 28 `@mcp.tool()` functions consumed `ctx.request_context.lifespan_context`. The scaffolding implied a threading contract that didn't exist.

**Root cause:** A half-finished refactor: the lifespan was wired into `FastMCP()` and the `AppContext` payload was yielded, but no tool was ever migrated to read from it. A new tool reading the scaffolding would conclude config flows through `ctx`, when it actually flows through module-level `_conn()`/`_store()` reading env vars.

**Fix:** Removed the `AppContext` dataclass and its fields; kept `app_lifespan` as a minimal no-op (FastMCP requires a lifespan for hooks); documented `_conn()`/`_store()` as the single source of truth. The more-correct fix (wire `ctx` through all tools) is deferred to a separate spec — high regression surface across 28 call sites for no current benefit.

**Prevention:** Half-finished abstractions are worse than no abstraction — they imply a contract that doesn't hold. Either finish the refactor (wire all consumers) or delete it.

**Related:** `src/cairn/mcp_server/_server_core.py`.

---

### wal-sidecar-swap-build-loss (2026-08-14)

**TL;DR:** The atomic-swap replaced only the main DB file; a surviving old `-wal` made every new connection read the pre-build graph — the build was silently lost while `integrity_check` passed.

**Symptom:** After `cairn build --staging` (or any `backup_to` persist) that coincided with an open WAL writer — the SSE daemon's 30s flush window, an in-flight tool call, or a crash-leftover wal — new connections served the OLD graph and the old connection's close-checkpoint then overwrote the new inode with old pages.

**Root cause:** SQLite WAL frames live in the shared `<db>-wal` sidecar, not inside the replaced inode. `os.replace` swapped the main file only.

**Fix:** Under the build lock: checkpoint the old DB (best-effort), then unlink `<db>-wal`/`<db>-shm` immediately before `os.replace`. Open connections keep their fds on the unlinked inodes. Regression: `tests/test_swap_wal_safety.py` (holds a WAL writer with committed frames across the swap). Found independently by two auditors in the same audit.

**Prevention:** Any file-replacement persistence over a WAL-mode SQLite must treat `-wal`/`-shm` as part of the database. Never swap the main file alone.

### build-lock-loser-unlock (2026-08-14)

**TL;DR:** A FAILED flock acquire unlinked the WINNER'S lock file — a third process then acquired a fresh inode while the winner still built (deterministic double-acquire; two builders wrote the same `.tmp`).

**Root cause:** The `finally` block ran `os.unlink(lock_path)` unconditionally — on the failure path it deleted a file it did not own. flock is inode-based; recreating the path mints a new lockable inode.

**Fix:** The lock file is deliberately never unlinked, by winner or loser — a stale zero-byte `.build.lock` is harmless because only flock state matters and it dies with the holder's fd. Regression: `test_failed_acquire_never_unlinks_winners_lock_file`.

**Prevention:** Never unlink a lock file you didn't successfully lock. With flock, the file's existence is irrelevant; the kernel-held lock is the truth.

### knowledge-layer-unredacted (2026-08-14)

**TL;DR:** The knowledge layer (add_document/add_workflow/import_directory) persisted title+body verbatim — the redaction chokepoint existed only in the memory layer. Fourth incarnation of the codepath-divergence class.

**Fix:** Redaction at the store chokepoints (knowledge add path strips title/body BEFORE slug derivation; capture/evolve strip titles; `tool_metrics.error_message` strips before buffering; namespace guards enforced in the stores so every caller inherits them). 41 tests in `tests/test_redaction_chokepoints.py`.

**Prevention:** Security-sensitive transformations live at the shared chokepoint — this is the same prevention line as 2026-08-10's `record-memory-unredacted-secrets`, and it failed again because the fix landed at a caller. Audit check: grep for NEW persistence paths, not for old fixes.

### watchdog-exit-bypasses-atexit (2026-08-14)

**TL;DR:** The stdio parent-death watchdog exited via `os._exit(0)`, which never runs atexit — up to 30s of tool_metrics/events and 15s of queued memory embeddings were silently lost on every normal MCP client disconnect.

**Root cause:** `os._exit` skips cleanup by design, and atexit does not fire from the watchdog's exit path (verified empirically). The buffered sinks relied on atexit for the final drain.

**Fix:** The watchdog explicitly drains all three buffers (telemetry flush, `_flush_metrics`, embed flush — each isolated) before `os._exit(0)`. Tests: `TestWatchdogBufferDrain`.

**Prevention:** Any `os._exit` in a process with buffered state must drain explicitly. atexit is main-thread-shutdown-only.

### sweeper-matched-nothing (2026-08-14)

**TL;DR:** The stray-process sweeper — the remediation for the stdio-leak/"database is locked" class — matched argv shapes editors never spawn (`cairn serve.*<db>` with db in env, launchd's `serve run`), so orphans stayed invisible; its broad fallback could kill a legitimate server on a different DB/workspace.

**Fix:** Three-gate pipeline: pgrep superset → per-pid cmdline anchored token check (argv[0] ends in `cairn`, argv[1] == `serve`) → lsof-on-db verification; kills only verified db-holders, never when verification is impossible; cmdline re-checked before SIGTERM/SIGKILL (pid-reuse guard). 20 mocked tests in `tests/test_stray_sweeper_safety.py`.

**Prevention:** Process-matching rules must be derived from how processes are ACTUALLY spawned (read the launcher code), not from how they were in one deployment. Killing requires positive verification, not pattern absence.

### parser-golden-self-validation (2026-08-14)

**TL;DR:** Golden fixtures are regenerated from parser output, so systematic drops self-validate — Ruby chained calls, PHP namespaced calls, and ALL Kotlin class-body properties shipped green with zero failing tests.

**Root cause:** `regenerate.py` bakes current behavior into `expected.json`; a parser that silently drops a construct produces fixtures that assert the drop.

**Fix:** The three drops fixed with hand-curated direct regression tests (`tests/test_parser_audit_fixes.py`, 18 tests) that parse idiom snippets and assert the edge/symbol EXISTS — independent of golden regeneration.

**Prevention:** Generated fixtures can pin behavior but cannot detect omissions. Every language needs at least a few hand-written positive assertions for its signature idioms, and audits must probe real-world constructs (fixtures alone are insufficient — noted at PHP/Ruby addition, now enforced).

### telemetry-config-vs-execution (2026-08-14)

**TL;DR:** `semantic_backend` emitted the fusion/rerank the user CONFIGURED, not what RAN — a degraded call reported `fusion=1, rerank=1`, so the provenance metric measured configuration and the exact degradations the telemetry spec existed to expose stayed invisible.

**Fix:** Outcome flags threaded from the fusion/rerank branches into the emit; `fusion_degraded`/`rerank_degraded` attrs added (declared in the cardinality guard). Plus: `ann_fallback` gains the never-emitted `no_index`/`query_error` reasons with OperationalError discrimination, doctor probes index existence + staleness, new `semantic_unavailable`/`embed_flush_stalled` events, `cairn metrics --tasks`.

**Prevention:** A telemetry attr must be derived from the code path that did the work, not from the config that requested it. When adding a signal, ask "what would this report if the feature silently failed?" — if the answer is "success", the signal is wired wrong.
