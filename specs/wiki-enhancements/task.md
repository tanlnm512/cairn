# Tasks: <name>

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.
**Before-audit**: pending — the orchestrator writes `passed @ <sha>` here

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1     | 3     | 0    |
| **Σ** | 3     | 0    |

## Phase 1: <milestone name> (FR-###)
<!-- Checkpoint: <exit condition from plan.md> -->
- [ ] T001 <verb phrase — files touched> (FR-###)
- [ ] T002 [P] <...> (FR-###)
- [ ] T003 <...> (FR-###)

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `- [x]` done + proof note:
      done <date> — <test/command that proves it>
- Dropped: `- [ ] ~~T004~~ dropped <date> (D-###)` — never delete the line;
  dropped tasks stay visible with the decision that killed them
- `[P]` = parallelizable (default — no shared files, no upstream task);
  chained tasks note `(after T###)` and name the exact interface they
  consume from their upstream — symbols, signatures, file formats; serial
  runs need a reason, parallel runs need none
- Fix rounds append `(fix <n>/5)` to the entry — the cap survives resume
  only if the count lives here, in the status holder
- Every task cites its FR-###; tasks with no FR are scope creep — fix the
  spec first
