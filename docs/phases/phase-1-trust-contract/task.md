# Phase 1 — Task Tracker

> **Live status board.** Sub-tasks are derived from [`plan.md`](plan.md);
> **this file is the single source of truth for status.** The plan owns the
> task list and sequencing; this file owns the status of each task. Update
> this file as work progresses — do not edit `plan.md` checkboxes for status.
> Item IDs (`1.1`, `1.2`, …) are stable across all phase docs; sub-task IDs
> (`1.1.1`, `1.1.2`, …) are stable here.

## Status legend

| Status | Meaning |
|--------|---------|
| `todo` | Not started |
| `doing` | In progress |
| `done` | Complete |
| `blocked` | Waiting on a dependency or decision (note what) |
| `skipped` | Deliberately dropped (note why) |

## 1.1 — Close the SCIP importer resolution loop

> *Status at v0.6.1: the importer fix is already in code (`scip_importer.py:540-542`). The work here is regression coverage + rewriting the doubly-stale BUGS.md entry (its `Fix:` says "not yet implemented" and `Prevention:` calls the invariant test "future" — both wrong).*

| ID | Task | Status | Notes |
|----|------|--------|-------|
| 1.1.1 | Add `tests/test_scip_importer_resolution.py` with a checked-in minimal `.scip` fixture (caller + target + external ref); assert resolved call → `exact` w/ `target_id`, external ref → `unresolved` w/ NULL. | todo | |
| 1.1.2 | Run `cairn import-scip` on a real scip-java index; spot-check 5 cross-file call edges by hand against source. | todo | |
| 1.1.3 | Close `docs/BUGS.md#scip-importer-fake-resolution` with a "Fixed in \<version\>" line pointing to both tests. | todo | needs the version this lands in |
| 1.1.4 | Document the resolution rule in `docs/scip.md` § importer behavior (one paragraph). | todo | |

## 1.2 — Close the invariant-test gap

> *Status at v0.6.1: `test_invariant_exact_resolution_has_target_id` exists but only seeds hand-built rows.*

| ID | Task | Status | Notes |
|----|------|--------|-------|
| 1.2.1 | Pair the existing hand-seeded invariant with the 1.1 fixture-driven test so the importer path is exercised end-to-end. | todo | depends on 1.1.1 |
| 1.2.2 | Confirm the test runs in `ci.yml` (full-suite step). It is intentionally NOT in `-m core` by design (`test_core_smoke.py:4-5`). | done | Settled: runs in full CI suite; `-m core` exclusion is intentional |
| 1.2.3 | Verify the test fails RED on the pre-fix importer (git-checkout old importer, run fixture test, confirm RED) — once. | todo | one-time proof the guard bites |

## 1.3 — Critic invariant tests

> *Status at v0.6.1: file-ref-rejects and real-refs-pass are covered in `tests/test_compass_critic.py` + `tests/test_core_smoke.py`; only symbol-ref-warns (1.3.2) is missing. Behavior is asymmetric (file refs block, symbol refs warn).*

| ID | Task | Status | Notes |
|----|------|--------|-------|
| 1.3.1 | `test_critic_rejects_unknown_file_ref` — backtick path not in graph → `passed is False` + error present. | done | Covered in `tests/test_compass_critic.py:134` + `tests/test_core_smoke.py:308` (runs under `-m core`) |
| 1.3.2 | Add `test_critic_warns_unknown_symbol_ref` — fake `Symbol(...)` → warning in `result.warnings` (NOT `passed is False`). | todo | do not over-assert; the only genuinely missing critic test |
| 1.3.3 | `test_critic_passes_real_refs` — only graph-verified refs → no errors/warnings, passes. | done | Real-refs-pass covered in `tests/test_compass_critic.py:145` + `tests/test_core_smoke.py:319` |
| 1.3.4 | Confirm coverage at the `critic_concept` level suffices for compass+wiki (memory does NOT call `critic_concept`). | done | Memory is not a `critic_concept` caller (grep of `src/cairn/memory/`); compass+wiki covered |

## 1.4 — Narrative repositioning

| ID | Task | Status | Notes |
|----|------|--------|-------|
| 1.4.1 | Rewrite README "Why cairn?" to lead with the 3-promise verification contract. | todo | |
| 1.4.2 | Move resolution-labeled edges content below the contract, framed as evidence for promise #1. | todo | |
| 1.4.3 | Update `docs/architecture.md` § "What cairn is" to mirror; one shared 3-promise block, quoted in both. | todo | no drift |
| 1.4.4 | Add a one-paragraph "verification contract" anchor linkable from roadmap + future docs. | todo | |
| 1.4.5 | Scan AGENTS.md + `docs/mcp-tools.md` for claims contradicting the contract (e.g. "resolution labels are unique"). | todo | |

## Phase exit gate

Phase 1 is done when **all four** hold (mirror of `plan.md` § Definition of done):

- [ ] 1.1.3 closed (BUGS.md entry rewritten to retract "not yet implemented" + record version + test pointers)
- [ ] 1.2.3 verified RED once against the pre-fix importer
- [ ] 1.3.2 green (the only genuinely missing critic test; 1.3.1/1.3.3/1.3.4 already done)
- [ ] 1.4.1–1.4.4 landed; README + architecture.md lead with the contract identically

## Burndown

| Item | Sub-tasks | done | doing | todo | blocked | skipped |
|------|-----------|------|-------|------|---------|---------|
| 1.1 | 4 | 0 | 0 | 4 | 0 | 0 |
| 1.2 | 3 | 1 | 0 | 2 | 0 | 0 |
| 1.3 | 4 | 3 | 0 | 1 | 0 | 0 |
| 1.4 | 5 | 0 | 0 | 5 | 0 | 0 |
| **Total** | **16** | **4** | **0** | **12** | **0** | **0** |
