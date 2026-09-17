# Tasks: shared-memory-bus

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.
**Before-audit**: passed @ 3fb556e (2026-09-17: check.py 0 fail; suite 3378 green at 3fb556e; clean tracked tree; branch feat/shared-memory-bus; 0/11 pre-done; C-01..C-04 reviewed)

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
<!-- All tasks todo: survey.md supporting evidence — no `share` command, no
     `--agent` option anywhere in src/cairn/cli/, and no overlap query exists. -->
| Phase | Total | Done |
|-------|-------|------|
| 1 | 4 | 4 |
| 2 | 4 | 4 |
| 3 | 3 | 3 |
| **Σ** | 11 | 11 |

## Phase 1: M1 — Identity + concurrency substrate (FR-005, FR-006, FR-004)
<!-- Checkpoint: WAL/busy_timeout unchanged on the shared connect path
     (`sed -n '837,855p' src/cairn/graph/schema.py`); chokepoint signatures
     intact (`grep -n "def store_memory\|def create_memory" src/cairn/memory/store.py`);
     T004 module passes; FR-004 baseline suite green. -->
- [x] T001 Add `agent_symbols` table and index to `_apply_schema` in src/cairn/graph/schema.py (FR-005, D-002)
  - done 2026-09-17 — pytest tests/test_invariants.py — agent_symbols schema idempotent (6 passed)
  - DDL: `(agent_id TEXT NOT NULL, symbol TEXT NOT NULL, memory_id TEXT, kind TEXT NOT NULL, ts TEXT NOT NULL)` plus index on `(symbol, ts)`, riding the idempotent executescript like the other tables
  - read-only `mode=ro` opens never apply schema — never assume the table exists from a read-only connection before a writable open
  - anchor: `pytest tests/test_invariants.py::test_invariant_schema_migration_idempotent`
- [x] T002 [P] Add `validate_agent_id` helper to src/cairn/memory/store.py (FR-006, D-001)
  - done 2026-09-17 — pytest tests/test_memory_store_fixes.py — validate_agent_id accept/reject tests green
  - non-empty, length-capped at 64, charset-sanitized to `[A-Za-z0-9._-]`; one shared helper, no registration command
  - anchor: `pytest tests/test_memory_store_protocol.py tests/test_memory_store_fixes.py`
- [x] T003 (after T001, T002) Add `share_memory` beside `create_memory`/`store_memory` in src/cairn/memory/store.py (FR-005, D-004)
  - done 2026-09-17 — pytest tests/test_memory_store_fixes.py — share_memory idempotency + validation tests green
  - single-statement idempotent insert of `(agent_id, symbol, memory_id, kind='share', ts)` rows on the writable conn; WAL + busy_timeout reused, no new lock protocol
  - consumes: `agent_symbols` DDL from T001; `validate_agent_id` from T002 (non-empty, ≤64, `[A-Za-z0-9._-]`)
  - `store_memory` and `create_memory` signatures untouched — 139-symbol blast radius; do not thread agent state through them
  - anchor: `pytest tests/test_memory_store_protocol.py tests/test_memory_store_fixes.py`
- [x] T004 (after T003) Add two-concurrent-simulated-agents test module tests/test_memory_share_concurrency.py (FR-005, FR-004)
  - done 2026-09-17 — pytest tests/test_memory_share_concurrency.py — 4 passed (WAL substrate, no lost writes)
  - two simulated agents write distinct per-agent namespace rows into one store — no lost write under concurrent writers
  - consumes: `share_memory` write path from T003
  - C-04: no eager `cairn.cli`/`cairn.mcp_server` imports at module level; `tmp_path` isolation, never the real `~/.cairn`
  - proof: `sed -n '837,855p' src/cairn/graph/schema.py` shows WAL/busy_timeout unchanged; baseline `python -m pytest tests/test_memory_store_fixes.py tests/test_core_smoke.py tests/test_memory_stale_flag.py tests/test_mcp_degradation_footnote.py tests/test_mcp_connection_leaks.py` passes

## Phase 2: M2 — Share + recall read-through (FR-001, FR-003)
<!-- Checkpoint: fixture workspace demo — A shares, B's next relevant recall
     returns the entry; recall regression surface green
     (`python -m pytest tests/test_memory_stale_flag.py
     tests/test_mcp_degradation_footnote.py tests/test_mcp_connection_leaks.py`). -->
- [x] T005 (after T002, T003) Add `share` verb to the `memory` CLI group in src/cairn/cli/memory.py (FR-001)
  - done 2026-09-17 — TC-001/002/004 proofs PASS — share verb live (closing audit)
  - `cairn memory share --agent <id> --symbols a,b,c <memory_id>`; follow the `demote` verb pattern — opens the DB via `--db`, validates via `validate_agent_id`, passes the writable conn into `share_memory`
  - consumes: `share_memory` and `validate_agent_id` in src/cairn/memory/store.py from T002/T003
  - anchor: `pytest tests/test_cli_smoke.py tests/test_memory_mcp_trim.py`
- [x] T006 (after T005) Add `--agent` read-through to `cairn memory search` in src/cairn/cli/memory.py (FR-003, D-003)
  - done 2026-09-17 — pytest tests/test_memory_share_recall.py — CLI read-through attribution green
  - `--agent` omitted → legacy code path byte-identical (FR-004); set → merge other agents' `share` rows on the searched symbols
  - consumes: the `memory` click group with T005's `share` verb registered — same file, serialized per plan.md's merge-collision mitigation
  - anchor: `pytest tests/test_cli_smoke.py tests/test_memory_mcp_trim.py`
- [x] T007 [P] (after T003) Add optional trailing `agent` parameter to `recall_memory` in src/cairn/mcp_server/tools_memory.py (FR-003, FR-004, D-003)
  - done 2026-09-17 — pytest tests/test_memory_share_recall.py — MCP agent read-through + FR-004 byte-identity green
  - `agent=None` → current output byte-identical, no sharing query executed; set → validate, merge other agents' `share` rows on the recalled symbols before the degradation footnote, per-result attribution line, footnote stays once and last
  - consumes: `agent_symbols` share rows as inserted by `share_memory` (T003): `(agent_id, symbol, memory_id, kind, ts)`
  - read paths keep read-only opens — journal-mode flips break the byte-identity sidecar pins
  - anchor: `pytest tests/test_mcp_degradation_footnote.py tests/test_memory_stale_flag.py tests/test_mcp_connection_leaks.py`
- [x] T008 (after T006, T007) Add share-to-recall integration tests in tests/test_memory_share_recall.py (FR-001, FR-003)
  - done 2026-09-17 — pytest tests/test_memory_share_recall.py — 7 share-to-recall integration tests green
  - fixture workspace: A shares via `cairn memory share`, B's next relevant recall returns the shared entry via both `search --agent` and `recall_memory` with `agent` set
  - consumes: `share` verb (T005), `search --agent` (T006), `recall_memory` agent parameter (T007)
  - proof: `python -m pytest tests/test_memory_stale_flag.py tests/test_mcp_degradation_footnote.py tests/test_mcp_connection_leaks.py` green

## Phase 3: M3 — Overlap detection + end-to-end (FR-002)
<!-- Checkpoint: overlap demo — warning names the other agent and the
     overlapping symbols; FR-004 final proof — CP1 suite set passes unchanged
     plus this milestone's new tests. -->
- [x] T009 (after T003) Add `check_overlap` beside `create_memory`/`store_memory` in src/cairn/memory/store.py (FR-002, D-002)
  - done 2026-09-17 — pytest tests/test_memory_store_fixes.py — check_overlap 8 anchor tests green
  - returns `(agent_id, symbol, kind, ts)` rows for other agents' recent `share`/`intent` rows intersecting the queried symbol set within the recency window; reads use the read-only `mode=ro` path
  - consumes: `agent_symbols` rows written by `share_memory` (T003)
  - anchor: `pytest tests/test_memory_store_protocol.py tests/test_memory_store_fixes.py`
- [x] T010 (after T005, T009) Add `check` verb to the `memory` CLI group in src/cairn/cli/memory.py (FR-002)
  - done 2026-09-17 — TC-005/006/007 proofs PASS — check verb warnings live (closing audit)
  - `cairn memory check --agent <id> --symbols a,b,c`; records the caller's `kind='intent'` rows via one writable open, reads conflicts via the read-only path, one warning line per conflict naming the other agent and the overlapping symbols
  - consumes: `check_overlap` from T009; `validate_agent_id` from T002; the `memory` click group with T005's `share` verb registered — same file, serialized per plan.md's merge-collision mitigation
  - anchor: `pytest tests/test_cli_smoke.py tests/test_memory_mcp_trim.py`
- [x] T011 (after T010) Add end-to-end overlap tests in tests/test_memory_overlap_check.py (FR-002, FR-004)
  - done 2026-09-17 — pytest tests/test_memory_overlap_check.py — 13 e2e tests green; TC-017 proof PASS
  - B checks an edit set overlapping A's recent activity → warning names A and the overlapping symbols; concurrent-writer race stays lossless under WAL + busy_timeout
  - consumes: `check` verb (T010), `check_overlap` (T009)
  - proof: FR-004 final — `python -m pytest tests/test_memory_store_fixes.py tests/test_core_smoke.py tests/test_memory_stale_flag.py tests/test_mcp_degradation_footnote.py tests/test_mcp_connection_leaks.py` passes unchanged plus this milestone's new tests

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `- [x]` done + proof note:
      `done <date> — <test/command that proves it>`
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
- Every task cites its FR-###; tasks with no FR are scope creep — fix the
  spec first
