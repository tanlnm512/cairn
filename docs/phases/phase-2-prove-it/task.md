# Phase 2 — Task Tracker

> **Live status board.** Sub-tasks are derived from [`plan.md`](plan.md);
> **this file is the single source of truth for status.** The plan owns the
> task list and sequencing; this file owns the status. Update this file as
> work progresses — do not edit `plan.md` checkboxes for status. Sub-task IDs
> (`2.1.1`, …) are stable here.

## Status legend

| Status | Meaning |
|--------|---------|
| `todo` | Not started |
| `doing` | In progress |
| `done` | Complete |
| `blocked` | Waiting on a dependency or decision (note what) |
| `skipped` | Deliberately dropped (note why) |

## 2.1 — Fill the benchmark tables

| ID | Task | Status | Notes |
|----|------|--------|-------|
| 2.1.1 | Pick + pin three corpora at exact commits (cairn repo Python, a Kotlin repo, a TS repo); record SHAs in benchmarks.md. | todo | |
| 2.1.2 | Build each corpus; run `cairn eval` (Recall@10, MRR) for L1 + L5. | todo | note lexical vs embedding path per corpus |
| 2.1.3 | Run `cairn bench --suite perf` (build/embed/query latency, median/p95/ops-sec). | todo | |
| 2.1.4 | Run `cairn bench --suite scaling` (build/embed/DB-MB/resolve_rate per size). | todo | resolve_rate ≠ false-positive rate |
| 2.1.5 | Compute precise-vs-fuzzy false-positive rate per corpus: `(fuzzy_count - precise_count) / fuzzy_count` averaged across a common-name set. | todo | the methodology number |
| 2.1.6 | Fill the result-template tables in benchmarks.md; record cairn version + hardware next to each. | todo | blocks 2.4 |

## 2.2 — Make the critic visible

> *Status at v0.6.1: the verdict is already surfaced in several places — MCP `generate_flow` (text string), `cairn compass validate`, `cairn validate-paths`, `cairn wiki generate`. The gaps: a unified single-doc `cairn verify` command, and a structured (dict) verdict field in MCP returns.*

| ID | Task | Status | Notes |
|----|------|--------|-------|
| 2.2.1 | Add `cairn verify <doc-path>` — thin wrapper over existing `cairn compass validate`, accepting any single concept id. | done | Added to `src/cairn/cli/validate.py`; loads via `OKFBundle.read_concept`, runs `critic_concept`, prints verdict, exit 1 on fail / 2 on missing doc |
| 2.2.2 | Promote MCP verdict from flattened text to a structured additive block in `generate_flow` (both reject + accept branches). | done | Added `_critic_verdict_block` helper in `tools_compass.py`; appends a `cairn-critic` JSON block (passed/quality/errors/warnings) after the prose — additive, prose unchanged |
| 2.2.3 | Keep file-ref errors and symbol-ref warnings distinct in the surfaced verdict. | done | The block surfaces `errors[]` and `warnings[]` as separate arrays; never collapsed |
| 2.2.4 | Test: structured verdict + verify command for good/bad/missing concepts. | done | `tests/test_verify_cmd.py` — 4 tests: real-refs pass, unknown-file fail (exit 1), missing doc (exit 2), verdict block is valid JSON |

## 2.3 — "cairn on cairn" live demo

| ID | Task | Status | Notes |
|----|------|--------|-------|
| 2.3.1 | Write a checked-in script (or pytest `-m core`) that builds cairn's own repo in an isolated store (`CAIRN_HOME`/`tmp_path`). | todo | do not reuse ~/.cairn |
| 2.3.2 | Assert non-empty correct output for `explore("build_graph")`, `impact("critic_concept")`, `get_compass(...)`. | todo | target symbols verified in tech-guide |
| 2.3.3 | Add the demo to CI so it cannot rot. | todo | |
| 2.3.4 | Reference the demo from the README quick-start as the verbatim walkthrough. | todo | |

## 2.4 — Methodology post

| ID | Task | Status | Notes |
|----|------|--------|-------|
| 2.4.1 | Write `docs/methodology-precise-vs-fuzzy.md`: problem → method → numbers (from 2.1) → interpretation → reproduce. | todo | depends on 2.1.6 |
| 2.4.2 | Cite the three corpora with pinned SHAs so numbers are reproducible. | todo | |
| 2.4.3 | Link the post from README (resolution-labels evidence section) and `docs/benchmarks.md`. | todo | |

## Phase exit gate

Phase 2 is done when **all four** hold (mirror of `plan.md` § Definition of done):

- [ ] 2.1.6 tables filled for three corpora with SHAs + version + hardware
- [ ] 2.2.1 `cairn verify` prints verdicts; 2.2.2 MCP returns verdict additively
- [ ] 2.3.3 self-demo green in CI; 2.3.4 referenced from README
- [ ] 2.4.1 post exists with real numbers; 2.4.3 linked from README + benchmarks.md

## Burndown

| Item | Sub-tasks | done | doing | todo | blocked | skipped |
|------|-----------|------|-------|------|---------|---------|
| 2.1 | 6 | 0 | 0 | 6 | 0 | 0 |
| 2.2 | 4 | 4 | 0 | 0 | 0 | 0 |
| 2.3 | 4 | 0 | 0 | 4 | 0 | 0 |
| 2.4 | 3 | 0 | 0 | 3 | 0 | 0 |
| **Total** | **17** | **4** | **0** | **13** | **0** | **0** |
