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
| 3.1.2b | Derive + append a `stale` flag (in `recall_memory`, after `refs_verified` at `:124`) when fraction falls below the chosen threshold. | todo | the only genuinely missing piece in 3.1 |
| 3.1.3 | Choose + document the stale threshold deliberately (e.g. `< 1.0` some stale, `< 0.5` mostly stale). | todo | do not silently pick |
| 3.1.4 | Reuse Phase 2.2's verdict shape so docs + memory share one trustworthiness vocabulary. | todo | |
| 3.1.5 | Test: record memory citing a backtick symbol → delete symbol → recall shows `refs-verified < 1.0` (works today) AND `stale` flag (Phase 3 adds); real symbol → no flag. | todo | backtick-only, matches doc layer |

## 3.2 — Memory-triggered build hints

> *Status: `cairn update` already surfaces warnings + runs memory decay (`cli/update.py:57-86`). The gap is no edit→memory cross-reference.*

| ID | Task | Status | Notes |
|----|------|--------|-------|
| 3.2.1 | After `cairn update` detects changed files, scan tribal memories for backtick refs (reuse `src/cairn/refs.py` `extract_file_refs`/`extract_symbol_refs` — shared by critic + scoring) resolving to symbols in changed files. | todo | |
| 3.2.2 | Emit a warning via the existing `display.warning` channel: "N memor(y/ies) reference symbols in \<file\> — verify: \<paths\>". | todo | warning, not block |
| 3.2.3 | Confirm UX says "symbol changed" not "memory broken" for the rename case (a renamed symbol is a new symbol_id). | todo | |
| 3.2.4 | Test: record memory backtick-citing a symbol in file X → edit X → warning present; edit unrelated file → no warning. | todo | backtick refs only, no false positives |

## Phase exit gate

Phase 3 is done when **both** hold (mirror of `plan.md` § Definition of done):

- [ ] 3.1.2b + 3.1.3 + 3.1.5 done — recall of a memory whose cited symbol is gone shows the `stale` flag; healthy memory shows none
- [ ] 3.2.4 green — `cairn update` after editing a memory-anchored file warns; unrelated file does not

## Burndown

| Item | Sub-tasks | done | doing | todo | blocked | skipped |
|------|-----------|------|-------|------|---------|---------|
| 3.1 | 6 | 2 | 0 | 4 | 0 | 0 |
| 3.2 | 4 | 0 | 0 | 4 | 0 | 0 |
| **Total** | **10** | **2** | **0** | **8** | **0** | **0** |
