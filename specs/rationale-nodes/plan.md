# Plan: rationale-nodes

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-04

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | Requirements | Depends on |
|-------|-----------|---------------------|--------------|------------|
| 1     | Extraction core | A `cairn build` of the cairn repo itself populates `rationale` with one attributed record per `NOTE:`/`WHY:`/`HACK:` comment — a `SELECT` on the table shows kind + text + enclosing symbol (or NULL for file-level) | FR-001, FR-002, FR-005, FR-006, FR-008, NFR-003 | — |
| 2     | Query surfaces | `cairn rationale --symbol X` / `--file F` prints ordered, kind-tagged rows; explore output gains a "Rationale" section only when the queried symbol has records; MCP tool count unchanged | FR-003, FR-004 | Phase 1 |
| 3     | Incremental honesty + docs | Edit a marker comment, run `cairn update` — that file's rationale rows exactly match its current comments; feature documented | FR-007 | Phase 1 |

## Dependencies
Phase 1 is the serial spine: the table DDL, the `ParsedFile.rationale` field +
comment-map/marker module, the shared-walk hook, and the persist/attribution
step each consume the previous one's output (dataclass field → walk hook →
persist). Verified with the workspace graph: `ParsedFile` has 10 impacted
symbols (9 direct callers), and `insert_parsed_file` is fed by both
`_insert_results` (full build, builder.py) and `reindex_paths`
(incremental.py) — persistence lands once and both paths inherit it.

Phases 2 and 3 are independent of each other once Phase 1 lands: Phase 2
touches the read layer + CLI + explore rendering; Phase 3 touches only the
incremental delete path. Disjoint files (see map below).

## Parallelization map
<!-- Which work areas are independent (different files/subsystems, no shared
     state) and can be developed concurrently, and which are strictly
     sequential. The task-breaker turns this into [P] markers per task. -->
- Independent: **query surfaces** (Phase 2) ∥ **incremental delete** (Phase 3) —
  disjoint files: `src/cairn/cli/rationale.py`, `src/cairn/cli/__init__.py`,
  `src/cairn/mcp_server/tools_graph.py`, `src/cairn/graph/rationale.py` vs
  `src/cairn/graph/incremental.py`; no shared state beyond the Phase 1 table
- Independent: within Phase 2, **CLI command** ∥ **explore section** —
  `cli/rationale.py` + `cli/__init__.py` vs `mcp_server/tools_graph.py`;
  both consume the `graph/rationale.py` read helpers, neither edits them
- Independent: within Phase 1, **schema DDL** (`graph/schema.py`) ∥
  **extraction module** (`parsers/_rationale.py`, `parsers/base.py`) — pure
  DDL vs pure parsing; the persist task chains on both
- Strictly ordered: **extraction module** → **shared-walk hook** — the hook
  calls the module's helper; both touch `parsers/base.py`
- Strictly ordered: **extraction module + hook + DDL** → **persist +
  attribution** (`graph/builder.py`, `graph/repository.py`) — persist reads
  `ParsedFile.rationale` and writes the `rationale` table
- Strictly ordered: **persist** → **read helper** (`graph/rationale.py`) →
  **CLI / explore** — readers query rows only persist can produce

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->
- **After Phase 1**: a build of the cairn repo itself yields attributed rows
  (row count > 0 in the `rationale` table of the store DB; path per
  `cairn config --json`), and the parser suite is green:
  `python3 -m pytest tests/ -k parser -q` (survey S3 verify).
- **After Phase 2**: `cairn rationale --symbol <existing symbol>` prints
  ordered kind-tagged rows; explore on a symbol with records shows the
  section and on one without shows none; tool count pin holds:
  `python3 -c "from cairn.mcp_server.server import verify_tool_count; verify_tool_count()"`.
- **After Phase 3**: remove a marker comment from a tracked file, run
  `cairn update` — that file's rationale rows match its current comments; and
  `python3 -m pytest tests/ -q -k "incremental and not infra"` (survey S6
  verify) is green.

## Risks & mitigations
- Risk: comment node types per grammar unverified (survey S2 gap) →
  mitigation: all 16 registry languages empirically probed with tiny
  tree-sitter parses this session; the verified map table is the tech-spec's
  D-001 and the single source; any language without a resolvable node takes
  the FR-006 no-fail arm.
- Risk: marker comments directly above a definition fall outside its span and
  go file-level (spec risk) → mitigation: attribution is span-containment
  only, pinned with fixture tests; look-ahead is an explicit follow-up.
- Risk: hoisting the 11 identical per-parser `_walk` copies into
  `TreeSitterParserBase` regresses a parser → mitigation: behavior-preserving
  hoist with identical signature, guarded by the parser suite
  (`python3 -m pytest tests/ -k parser -q`) at the Phase 1 checkpoint.
- Risk: multi-line marker comments duplicating records (spec risk) →
  mitigation: one record per comment node, continuation lines folded into
  the text (block comments parse as single nodes — probed); pinned in
  fixtures.

## Delivery
Solo, PR-per-milestone: three PRs from `feat/rationale-nodes` (branch per
spec.md), each landing code + tests + spec-doc updates together; docs ride
the Phase 3 PR. Every PR follows the C-01 shipping workflow (branch →
pre-commit → conventional commit → PR with audit checklist → CI).
