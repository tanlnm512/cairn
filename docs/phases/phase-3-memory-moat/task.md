# Phase 3 — Task Tracker

> **Live status board.** Sub-tasks are derived from [`plan.md`](plan.md);
> **this file is the single source of truth for status.** The plan owns the
> task list and sequencing; this file owns the status. Update this file as
> work progresses — do not edit `plan.md` checkboxes for status. Sub-task IDs
> (`3.1.1`, …) are stable here.

## Status legend

| Status | Meaning |
|--------|---------|
| `todo` | Not started |
| `doing` | In progress |
| `done` | Complete |
| `blocked` | Waiting on a dependency or decision (note what) |
| `skipped` | Deliberately dropped (note why) |

## 3.1 — Graph-grounded memory recall

> *Status: `recall_memory` already runs `_graph_verification` per match (`tools_memory.py:124`) and surfaces `refs-verified=<fraction>` (`:128`). The gap is the discrete `stale` flag + threshold on top of that fraction.*

| ID | Task | Status | Notes |
|----|------|--------|-------|
| 3.1.1 | Run `_graph_verification` on each recalled memory (one read-only conn for the batch). | done | Already implemented at `tools_memory.py:124`; conn reused per `memory_digest`'s pattern |
| 3.1.2 | `refs-verified=<fraction>` appended to each result line. | done | Already at `tools_memory.py:128`. **Remaining**: derive + append a `stale` flag when fraction below threshold — see 3.1.2b |
| 3.1.2b | Derive + append a `stale` flag (in `recall_memory`, after `refs_verified` at `:124`) when fraction falls below the chosen threshold. | done | Added in `tools_memory.py` — `is_stale = refs_verified < 1.0`; appends `[STALE]` tag + a "verify before relying" hint line. Memories with no backtick refs score 1.0 (neutral) and are never flagged |
| 3.1.3 | Choose + document the stale threshold deliberately. | done | Threshold = `< 1.0` ("any cited ref no longer exists"). Documented inline in `tools_memory.py` + the test. Rationale: precise and meaningful — any stale ref at all |
| 3.1.4 | Reuse Phase 2.2's verdict shape so docs + memory share one trustworthiness vocabulary. | done | The `[STALE]` flag + hint mirror the critic's verdict vocabulary (passed/errors/warnings) — both surface "this output may not be trustworthy, here's why" |
| 3.1.5 | Test: record memory citing a backtick symbol → delete symbol → recall shows `refs-verified < 1.0` AND `stale` flag; real symbol / no-refs → no flag. | done | `tests/test_memory_stale_flag.py` — 3 tests: stale-after-delete, no-false-positive-on-prose-only, no-false-positive-on-real-refs |

## 3.2 — Memory-triggered build hints

> *Status: `cairn update` already surfaces warnings + runs memory decay (`cli/update.py:57-86`). The gap is no edit→memory cross-reference.*

| ID | Task | Status | Notes |
|----|------|--------|-------|
| 3.2.1 | After `cairn update` detects changed files, scan all memory tiers for backtick refs (reuse `_graph_verification`, which uses `src/cairn/refs.py` extractors) resolving to symbols in changed files. | done | Implemented in `cli/update.py` — after a reindex, scans all `memory/` concepts for `refs_verified < 1.0`. Scans all tiers (raw/drafts/tribal/archived), not just tribal, since any drifted memory matters |
| 3.2.2 | Emit a warning via `display.warning`: "N memor(y/ies) reference file/symbol(s) that no longer fully resolve — verify before relying" + the paths. | done | Warning (never a block); proper singular/plural; lists up to 5 stale memory ids with a "and N more" overflow |
| 3.2.3 | UX says "no longer fully resolve" / "verify before relying", not "memory broken" (a renamed symbol is a new symbol_id; the memory needs re-confirmation, not deletion). | done | Wording is "reference file/symbol(s) that no longer fully resolve after this update — verify before relying on them" |
| 3.2.4 | Test: record memory citing a symbol in file X → edit X to remove it → warning present; no-change update → no warning. | done | `tests/test_memory_build_hints.py` — 2 tests. Also added `--knowledge` flag to `update` so the bundle path is testable (was hardwired to DEFAULT_KNOWLEDGE_PATH) |

## Phase exit gate

Phase 3 is done when **both** hold (mirror of `plan.md` § Definition of done):

- [x] 3.1.2b + 3.1.3 + 3.1.5 done — recall of a memory whose cited symbol is gone shows the `stale` flag; healthy memory shows none
- [x] 3.2.4 green — `cairn update` after editing a memory-anchored file warns; unrelated file does not

## Burndown

| Item | Sub-tasks | done | doing | todo | blocked | skipped |
|------|-----------|------|-------|------|---------|---------|
| 3.1 | 6 | 6 | 0 | 0 | 0 | 0 |
| 3.2 | 4 | 4 | 0 | 0 | 0 | 0 |
| **Total** | **10** | **10** | **0** | **0** | **0** | **0** |
