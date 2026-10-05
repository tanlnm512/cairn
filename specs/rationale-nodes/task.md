# Tasks: rationale-nodes

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
**Lifecycle**: v2
Status reflects code state per [survey.md](survey.md), not intent.
**Delivered**: commit @ 591bf92

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1 | 4 | 4 |
| 2 | 3 | 3 |
| 3 | 2 | 2 |
| **Σ** | 9 | 9 |

## Phase 1: Extraction core (FR-001, FR-002, FR-005, FR-006, FR-008, NFR-003)
<!-- Checkpoint: build of the cairn repo yields attributed rationale rows; python3 -m pytest tests/ -k parser -q green -->
- [x] T001 (implemented) [P] Add the additive `rationale` table (id, file_id, symbol_id nullable FK, line, kind, text) plus file_id/symbol_id indexes to the schema, following the additive-only CREATE TABLE IF NOT EXISTS pattern with no MIGRATIONS entry (FR-001)
  - done 2026-10-05 — done 2026-10-05 — additive rationale table verified (PRAGMA + pre-feature connect); schema suites 27 passed
  - Touches:
    - `src/cairn/graph/schema.py`
  - Verify before implementing: `sed -n '110,115p' src/cairn/graph/schema.py` (survey S7 — the additive pattern this follows)
- [x] T002 (implemented) [P] Add `RationaleRecord` + defaulted `rationale` list on `ParsedFile`, the `COMMENT_NODE_TYPES` per-language map (tech-spec D-001 table verbatim), and the opener-strip/marker-match/continuation-fold helper in a new `parsers/_rationale.py` — failing-test-first fixtures per mapped language plus docstring-produces-zero (FR-005) and out-of-scope-marker-produces-zero (FR-008) pins (FR-001, FR-005, FR-008)
  - done 2026-10-05 — done 2026-10-05 — 39 extraction tests green across all 16 mapped languages; docstring/TODO zero pins
  - Touches:
    - `src/cairn/parsers/base.py`
    - `src/cairn/parsers/_rationale.py`
    - `tests/test_rationale_extract.py`
  - Verify before implementing: `grep -rn "rationale" src/cairn --include="*.py"` (exit 1 — survey S1); re-run the tech-spec D-001 probe to confirm the map before coding it
- [x] T003 (implemented) (after T002 — consumes `COMMENT_NODE_TYPES` and the extract helper from `src/cairn/parsers/_rationale.py` and the `ParsedFile.rationale` list field on `ParsedFile`) Hoist the byte-identical `_walk(self, node, source, pf)` into `TreeSitterParserBase` with the comment-node check in its child loop, delete the 11 per-parser copies, add the same one-line check to `GenericTreeSitterParser._visit_children`, dart `_process_siblings`, and ruby `_walk_excluding_superclass`; include the FR-006 no-fail fixture (language with empty map entry yields zero records, no error) (FR-006, NFR-003)
  - done 2026-10-05 — done 2026-10-05 — walk hoisted, 11 copies deleted, parser suites 66 passed incl. FR-006 no-fail fixture
  - Touches:
    - `src/cairn/parsers/base.py`
    - `src/cairn/parsers/generic_tree_sitter.py`
    - `src/cairn/parsers/dart.py`
    - `src/cairn/parsers/ruby.py`
    - `src/cairn/parsers/c_family.py`
    - `src/cairn/parsers/csharp.py`
    - `src/cairn/parsers/go.py`
    - `src/cairn/parsers/kotlin.py`
    - `src/cairn/parsers/java.py`
    - `src/cairn/parsers/objc.py`
    - `src/cairn/parsers/php.py`
    - `src/cairn/parsers/python_parser.py`
    - `src/cairn/parsers/swift.py`
    - `src/cairn/parsers/typescript.py`
    - `tests/test_rationale_extract.py`
  - Verify before implementing: `grep -n "def _walk" src/cairn/parsers/*.py` (11 copies to delete); `python3 -m pytest tests/ -k parser -q` green before and after (survey S3 verify)
- [x] T004 (implemented) (after T001, T003 — consumes the `rationale` table columns and `ParsedFile.rationale` records) Persist rationale rows inside `insert_parsed_file` via a new `GraphRepository.insert_rationale`, attributing each record to the innermost callable-or-type symbol whose line_start/line_end span contains its line (module kind excluded; NULL when none) (FR-001, FR-002)
  - done 2026-10-05 — done 2026-10-05 — insert_rationale + innermost-span attribution; signal-persistence extended (innermost-wins/NULL pins)
  - Touches:
    - `src/cairn/graph/builder.py`
    - `src/cairn/graph/repository.py`
    - `tests/test_signal_persistence.py`
  - Verify before implementing: `grep -n "INTO symbols" src/cairn/graph/repository.py` (survey S5); extend `test_insert_parsed_file_persists_signals` (tests/test_signal_persistence.py:56) rather than adding a parallel case

## Phase 2: Query surfaces (FR-003, FR-004)
<!-- Checkpoint: cairn rationale prints ordered kind-tagged rows; explore shows the section only when records exist; verify_tool_count() passes -->
- [x] T005 (implemented) (after T004 — consumes the populated `rationale` table: rows keyed by symbol_id/file_id, ordered by line) Add read helpers in a new `src/cairn/graph/rationale.py` (records for symbol ids, records for file id, ordered by line) with tests (FR-003)
  - done 2026-10-05 — done 2026-10-05 — read module records_for_symbol_ids/records_for_file; test_rationale_read green
  - Touches:
    - `src/cairn/graph/rationale.py`
    - `tests/test_rationale_read.py`
  - Verify before implementing: `grep -n "def graph_grep" src/cairn/graph/grep.py` (the read-module precedent this mirrors)
- [x] T006 (implemented) [P] (after T005 — consumes its read helpers, records-for-symbol-ids and records-for-file ordered by line; do not edit them) Add the `cairn rationale` command in a new `src/cairn/cli/rationale.py` with `--symbol`/`--file`/`--db`/`--json`, printing records ordered by line with kind tags, registered by one import line in `cli/__init__.py` (FR-003)
  - done 2026-10-05 — done 2026-10-05 — cairn rationale CLI live output verified; 10 passed with smoke
  - Touches:
    - `src/cairn/cli/rationale.py`
    - `src/cairn/cli/__init__.py`
    - `tests/test_rationale_cli.py`
  - Verify before implementing: `cairn --help` (survey S9 verify); `sed -n '1,14p' src/cairn/cli/__init__.py` (import-side-effect wiring)
- [x] T007 (implemented) [P] (after T005 — consumes its read helpers, records-for-symbol-ids ordered by line; do not edit them) Append a gated `=== Rationale (N) ===` section to explore output after the Tribal memory section, rendering records for the matched seeds' symbol ids only when any exist; no new MCP tool (FR-004)
  - done 2026-10-05 — done 2026-10-05 — explore Rationale section gated; verify_tool_count COUNT_OK (26)
  - Touches:
    - `src/cairn/mcp_server/tools_graph.py`
    - `tests/test_explore_rationale.py`
  - Verify before implementing: `grep -n "Tribal memory" src/cairn/mcp_server/tools_graph.py` (survey S8 verify); `python3 -c "from cairn.mcp_server.server import verify_tool_count; verify_tool_count()"` after

## Phase 3: Incremental honesty + docs (FR-007, FR-003)
<!-- Checkpoint: removing a marker comment then cairn update leaves that file's rationale rows exactly matching its current comments; python3 -m pytest tests/ -q -k "incremental and not infra" green -->
- [x] T008 (implemented) [P] (after T004 — consumes the rationale persistence in `insert_parsed_file`; re-derive is already inherited via `reindex_paths`) Add `DELETE FROM rationale WHERE file_id = ?` to the incremental delete path, ordered before the symbols delete in the same transaction (nullable FK, no cascade — embeddings_mv ordering precedent), with a failing-test-first regression (FR-007)
  - done 2026-10-05 — done 2026-10-05 — incremental delete-before-symbols; live ranged-update re-derive verified; 96 incremental passed
  - Touches:
    - `src/cairn/graph/incremental.py`
    - `tests/test_rationale_incremental.py`
  - Verify before implementing: `sed -n '150,158p' src/cairn/graph/incremental.py` (delete ordering precedent); `python3 -m pytest tests/ -q -k "incremental and not infra"` (survey S6 verify)
- [x] T009 (implemented) (after T006, T007, T008 — documents the shipped CLI flags, explore section, and update behavior) Document the feature: rationale markers, `cairn rationale` usage, and the explore section in README/docs plus a CHANGELOG entry (FR-003)
  - done 2026-10-05 — done 2026-10-05 — cli-reference + CHANGELOG from landed contracts (flags match --help); doc-links 0 broken
  - Touches:
    - `README.md`
    - `CHANGELOG.md`
  - Verify before implementing: `cairn rationale --help` shows the shipped flags

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `(implemented)` landed —
      implementation evidence durable before the one all-at-once tick ·
      `- [x]` done + proof note: `done <date> — <test/command that proves it>`
- Dropped: `- [ ] ~~T004~~ dropped <date> (D-###)` — never delete the line;
  dropped tasks stay visible with the decision that killed them
- `[P]` = parallelizable (default — no shared files, no upstream task);
  chained tasks note `(after T###)` and name the exact interface they
  consume from their upstream — symbols, signatures, file formats; serial
  runs need a reason, parallel runs need none
- Fix rounds append `(fix <n>/5)` to the entry — the cap survives resume
  only if the count lives here, in the status holder. From round 2 on, an
  implementer's scratch note (what was tried, why it failed) may live at
  `notes/T###.md` — the one file an implementer may write under specs/,
  never read by check.py, never counted as status
- Every task cites FR-### or an applicable NFR-###; a task with neither is
  scope creep — fix the spec first
- Every code task carries a `Touches:` block (repo-relative files,
  directories, or globs); `[P]` tasks whose touches overlap are chained, not
  spawned together
- All statuses trace to survey.md item S1 (no rationale code exists —
  everything TODO); no task is marked done without a passing verify command
