# Plan: shared-memory-bus

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-17

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1     | M1 — Identity + concurrency substrate | Two concurrent simulated agents write distinct per-agent namespaces in one store with no lost write; agent ids validated at the share/check surface; existing single-agent memory suites pass unchanged (FR-004 baseline) | FR-005, FR-006, FR-004 | — |
| 2     | M2 — Share + recall read-through | Agent A `cairn memory share --agent A --symbols <set>`; agent B's next relevant recall returns the shared entry | FR-001, FR-003 | Phase 1 |
| 3     | M3 — Overlap detection + end-to-end | Agent B checks an edit set overlapping agent A's recent activity → warning names A and the overlapping symbols; final FR-004 proof | FR-002 | Phase 1 (∥ Phase 2) |

FR assignment (each exactly once): FR-004→M1, FR-005→M1, FR-006→M1,
FR-001→M2, FR-003→M2, FR-002→M3.

Ordering rationale (smallest-first, risky early): concurrent writers are the
spec's top risk, and the store is the write chokepoint every later milestone
passes through — survey S4 (`store_memory` src/cairn/memory/store.py:99,
`create_memory` :42), confirmed via `cairn callers store_memory`: callers in
src/cairn/memory/promotion.py (`capture_memory`, `batch_critic`, `decay`,
`evolve_memory`, `demote_memory`) and src/cairn/memory/store_protocol.py
(`add`, `update`). M1 lands that substrate first; FR-004's byte-identical
baseline is established before any surface changes and re-proven at CP2/CP3.

## Dependencies
```
M1 (substrate) ──→ M2 (share + recall)
       └──────────→ M3 (overlap)          M2 ∥ M3 (one shared file, see map)
```
- M1 → M2: M2 consumes the namespace visibility rule and id validation;
  nothing is shareable or readable-through before the substrate exists.
- M1 → M3: the overlap check attributes activity to agents; survey S3
  records session ids as per-MCP-session, not stable agent identities
  (`memory_refs` src/cairn/graph/schema.py:121-131, `tool_metrics` :330,
  `events` :385, `emit` src/cairn/telemetry/events.py:152 — all present,
  none agent-keyed).
- Graph evidence for FR-003's regression surface: `cairn impact recall_memory`
  → 11 impacted symbols, all tests (tests/test_mcp_degradation_footnote.py,
  tests/test_mcp_connection_leaks.py, tests/test_memory_stale_flag.py).
- Command registration: memory group at src/cairn/cli/__init__.py:36,
  subcommands in src/cairn/cli/memory.py (survey) — the single shared file
  between M2 and M3 additions.

## Parallelization map
<!-- Which work areas are independent and can run concurrently, and which are
     strictly sequential. The task-breaker turns this into [P] markers. -->
- Independent (group A, within M1): agent-id validation helper (new module) ∥
  namespace storage + locking (src/cairn/graph/schema.py additive change,
  src/cairn/memory/store.py) — disjoint files; they meet only when the M2/M3
  surfaces consume both.
- Independent (group B, after M1): M2 ∥ M3 — M2 files: src/cairn/cli/memory.py
  (share subcommand), src/cairn/memory/store.py (share write + recall
  read-through), src/cairn/mcp_server/tools_memory.py (`recall_memory` :21,
  `record_memory` :134 — survey S4), new tests. M3 files: new overlap-query
  module over memory_refs/tool_metrics/events, src/cairn/cli/memory.py (check
  surface), new tests. Sole collision: src/cairn/cli/memory.py — both append a
  subcommand to the same click group; serialize only the two registration
  tasks (or order the merges). Serializing whole milestones is not justified
  by the file evidence.
- Strictly ordered: M1 → M2 (M1 produces the namespace rule + validation that
  M2 consumes); M1 → M3 (M1 produces agent identity that M3's activity
  attribution consumes); within M1, the store.py substrate precedes M2's
  edits to the same file (store.py) and tools_memory.py.

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->
- **After Phase 1**: WAL/busy_timeout unchanged on the shared connect path —
  `sed -n '837,855p' src/cairn/graph/schema.py` (survey S1 verify); chokepoint
  signatures intact — `grep -n "def store_memory\|def create_memory"
  src/cairn/memory/store.py` (survey S4 verify); this milestone's concurrency
  test module passes (exact pytest target unknown — verify at task time);
  FR-004 baseline — `python -m pytest tests/test_memory_store_fixes.py
  tests/test_core_smoke.py tests/test_memory_stale_flag.py
  tests/test_mcp_degradation_footnote.py tests/test_mcp_connection_leaks.py`.
- **After Phase 2**: fixture workspace demo — A shares, B's next relevant
  recall returns the entry; recall regression surface green —
  `python -m pytest tests/test_memory_stale_flag.py
  tests/test_mcp_degradation_footnote.py tests/test_mcp_connection_leaks.py`.
- **After Phase 3**: overlap demo — warning names the other agent and the
  overlapping symbols; FR-004 final proof — CP1 suite set passes unchanged
  plus this milestone's new tests.

## Risks & mitigations
- Risk: race conditions under concurrent writers (spec risk) → mitigation:
  reuse the store's WAL + busy_timeout connect path (survey S1), add explicit
  cross-agent locking in M1, concurrency tests with two simulated agents.
- Risk: identity spoofing in shared mode (spec risk) → mitigation: FR-006
  validation at the share/check surface (M1), opt-in sharing per workspace.
- Risk: M2/M3 merge collision in src/cairn/cli/memory.py → mitigation: the
  only shared file; keep registration separate from logic so the collision is
  two appended blocks.

## Delivery
Solo, branch `feat/shared-memory-bus` (spec.md). Default per template: the
single end-of-plan commit — code + docs together, never per task (ADR-001,
tick-commit node). If PR-per-milestone is chosen instead, each phase lands as
one PR on the same branch; phase ordering is unchanged.
