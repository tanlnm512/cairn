# Bug Registry

Lessons learned, root-caused. Each entry is a one-time discovery converted to
permanent, queryable memory — so the same bug doesn't get solved twice.

Search this when you hit something unfamiliar: the symptom, the file, or the
mechanism. If you find a new bug, add an entry **before** fixing it, while the
pain is fresh.

## Format
Each entry: symptom (what went wrong) → root cause (why) → fix (what was done)
→ prevention (what guard stops it now) → related (files, tests, commits).

Keep entries to one paragraph. If a bug needs a deeper writeup
(architecture-level root cause, multi-file analysis, decision rationale
worth re-reading), put the long form in `docs/postmortems/<date>-<slug>.md`
and leave a one-line pointer here — `[→ postmortem](postmortems/...)`. This
keeps the registry scannable; detail lives separately.

## Entries

## 2026-08-06 / comments-only-code-drift

**Symptom:** After a bulk comment-trimming task run by 5 parallel sub-agents, 4 files had their executable code silently altered (string literals in CLI help text changed in `cli/query.py` and `cli/serve.py`, a `print()` message shortened in `agent_integration/skill/scripts/impact_guard.py`, and a variable renamed `legacy_repo_root` → `repo_root` in `compass/generator.py`). The agents self-reported "no executable code changed" — this was false.
**Root cause:** Reading diffs visually misses string-literal and variable-rename changes, especially across 84 files. Sub-agents reliably self-report success even when their edits cross into code. The failure is structural: comments and code share the same text buffer, so an agent editing comments can accidentally include adjacent code in its `old_string`/`new_string` blocks.
**Fix:** All 4 oversteps were reverted by hand. An AST-comparison script (`scripts/verify_no_code_change.py`) was created that blanks docstrings and compares `ast.dump()` of changed files — catching any executable-code difference.
**Prevention:** Run `make verify-no-code-change` (or `make verify-no-code-change REF=HEAD~1` for a committed change) after any "comments-only" / "docs-only" / "refactor" change. Documented in `docs/release-checklist.md` under "Agent / bulk-edit safety".
**Related:** `scripts/verify_no_code_change.py`, `docs/release-checklist.md`, commits `e710543`, `8c91ae4`.

## 2026-08-06 / portable-path-stale-comments

**Symptom:** The watcher (`graph/watcher.py`) and incremental reindexer (`graph/incremental.py`) contained comments and fallback logic claiming "build stores relative paths" and "repo_id='' for single-repo workspaces." Neither was true: the builder (`graph/builder.py`) stored absolute paths, and repo_id was the directory basename, not empty string. The code was half-finished — designed for relative paths but never completed. [→ postmortem](postmortems/2026-08-06-portable-paths.md)
**Root cause:** A design decision (store relative paths for portability) was partially implemented and documented in comments before the builder was updated. The comments drifted into lies, and the fallback logic in watcher/incremental existed to paper over the mismatch, making the inconsistency invisible.
**Fix:** The builder was updated to store repo-relative paths (`files.path`, `repos.path`, `parse_errors.file_path`, `skipped_files.path`). A single chokepoint (`scanner.resolve_file_path`) resolves them to absolute at disk-I/O time. Legacy absolute paths pass through unchanged for backward compat. Stale comments were corrected/removed.
**Prevention:** Invariant test `test_files_path_always_relative_after_build` in `tests/test_invariants.py` — if anything ever stores an absolute path again, CI fails. General lesson: don't leave a design decision half-implemented with comments describing the intended state; either implement it or don't write the comment.
**Related:** `src/cairn/graph/scanner.py:resolve_file_path`, `tests/test_portable_paths.py`, commit `29c6e62`.

## 2026-08-06 / scip-pypi-package-misidentification

**Symptom:** A design plan (`docs/scip-hybrid-plan.md`) specified `"scip>=0.5.0"` as a pip dependency for Sourcegraph's SCIP protobuf bindings. This package does not exist on PyPI — `scip` on PyPI is an unrelated bioimaging library (Scalable Cytometry Image Processing). Sourcegraph ships no official Python bindings (open issue: sourcegraph/scip#259).
**Root cause:** The dependency was asserted from assumption ("there's probably a pip package for this") rather than verified empirically. No one ran `pip install scip` or checked PyPI before writing the plan.
**Fix:** The plan was corrected to specify a vendored generated stub (`_scip_pb2.py` generated from upstream `scip.proto` via `grpcio-tools`) + the generic `protobuf` runtime package. Verified by actually generating bindings locally and round-tripping a real SCIP message.
**Prevention:** Process rule: before specifying any dependency in a plan or `pyproject.toml`, verify it exists on PyPI and is the package you think it is (`pip index versions <name>` or check pypi.org). Empirical verification beats assumption. Add this to `docs/release-checklist.md`.
**Related:** `docs/scip-hybrid-plan.md`, commit `8bca34b`.

## 2026-08-06 / repo-id-empty-string-myth

**Symptom:** Comments in `graph/incremental.py:64` and `graph/watcher.py:64` stated "build_graph stores repo_id='' for single-repo workspaces." This was false — the builder stores the repo directory basename (e.g. `"cairn"`), not an empty string. The false belief led to a `SELECT ALL` fallback in watcher.py that masked the real behavior.
**Root cause:** The comment was likely written during an earlier code iteration where repo_id *was* empty, and never updated when the builder changed to use `repo_path.name`. No test validated the actual stored value, so the lie persisted undetected.
**Fix:** Stale comments removed. The `SELECT ALL` fallback in watcher.py was re-commented accurately (it's now a safety net for workspace-name mismatches, not for empty repo_id).
**Prevention:** When a comment describes a specific runtime value ("stores X", "returns Y"), it should be backed by a test that asserts that value. Comments about runtime behavior rot; tests don't.
**Related:** `src/cairn/graph/incremental.py`, `src/cairn/graph/watcher.py`, `src/cairn/graph/scanner.py`.

## 2026-08-06 / scip-importer-fake-resolution

**Symptom:** The SCIP importer (`parsers/scip_importer.py`) wrote edges with `resolution='exact'` but never resolved `target_id` — it was always NULL. The `source_id` used a "rolling last definition" heuristic (whichever definition was most recently seen in document order), not the actual enclosing scope. Cross-file call targets were structurally wrong.
**Root cause:** The importer consumed only 3 fields from each SCIP occurrence (`symbol`, `range`, `symbol_roles`) and ignored the fields that enable real resolution (the shared `symbol` descriptor between definition and reference, the `relationships` list, `enclosing_range`). It then labeled the result `'exact'` despite no resolution occurring — the worst kind of bug: silently wrong data presented as trustworthy.
**Fix:** Fixed before 0.6.1. The importer now builds a `{symbol_descriptor → def_symbol_id}` map from definition occurrences (pass 1), resolves each reference's `target_id` via that map, computes `source_id` from `enclosing_range` (with a nearest-preceding-definition fallback), and sets `resolution='exact'` only when `target_id` is actually found (`scip_importer.py:540-542`). A reference whose target is not in the index is tagged `'unresolved'`, never `'exact'`. (The `Fix:` line previously said "the rewrite (not yet implemented)" — that was stale; the rewrite shipped.)
**Prevention:** Never label data `'exact'` or `'verified'` unless the code path that produced it actually performs the verification. The `resolution` column is a trust signal consumed by precise-by-default queries (`get_callers`, `impact_analysis`); false `'exact'` rows silently pollute blast-radius analysis. Guarded by two tests: `tests/test_invariants.py::test_invariant_exact_resolution_has_target_id` (the schema-level invariant) and `tests/test_scip_importer.py::test_protobuf_cross_file_resolution_is_exact` / `test_protobuf_external_reference_is_unresolved` (the importer-driven end-to-end check). (The `Prevention:` line previously called the invariant test "future" — that was stale; both tests exist.)
**Related:** `src/cairn/parsers/scip_importer.py:540-542`, `src/cairn/graph/traversal.py:STRUCTURAL_EDGE_KINDS`, `tests/test_invariants.py:213`, `tests/test_scip_importer.py:54,87`, `docs/scip-hybrid-plan.md`.
