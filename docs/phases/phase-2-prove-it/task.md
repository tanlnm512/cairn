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
| 2.1.1 | Pick + pin corpora at exact commits; record SHAs in benchmarks.md. | done | Python corpus (cairn on itself) measured 2026-08; Kotlin/TS deferred — they need external repo clones + corpus-tuned query sets (see 2.1.2 note) |
| 2.1.2 | Build corpus; run `cairn eval` (Recall@10, MRR) for L1 + L5. | done | Ran on cairn's own repo: L1=0.0, L5=0.0. Honest finding: `tests/eval/queries.yaml` targets generic shapes, not cairn's symbols → no fragment matches. Documented in benchmarks.md + methodology post. Multi-corpus eval needs tuned query sets |
| 2.1.3 | Run `cairn bench --suite perf` (build/embed/query latency). | done | Build: 4.0s wall (cairn on itself, 227 files) |
| 2.1.4 | Run `cairn bench --suite scaling` (build/embed/DB-MB/resolve_rate). | done | 1,929 symbols / 11,514 edges (4,066 exact=35% / 1,020 ambiguous / 6,428 unresolved) / 15.4 MB |
| 2.1.5 | Compute precise-vs-fuzzy false-positive rate. | done | **76% aggregate** across 10 common names (get/append/join/...); 100% for 8 of them, 0% for execute/close. The methodology number — see methodology post |
| 2.1.6 | Fill the result-template tables in benchmarks.md; record version + hardware. | done | Recall table + false-positive table filled in benchmarks.md with the Python-corpus numbers |

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
| 2.3.1 | Write a checked-in pytest (`-m core`) that builds cairn's own repo in an isolated temp DB. | done | `tests/test_self_demo.py` builds cairn on itself via explicit `--db`/`--workspace` flags (not `CAIRN_HOME`, which is bound at import); uses a tempdir, never touches ~/.cairn |
| 2.3.2 | Assert correct output for `def`/`impact` on `build_graph` + `critic_concept`; plus the resolution invariant on cairn's own graph. | done | Two tests: build+query (def finds `builder`, `critic`), and the exact⟹target_id invariant holds on cairn's own freshly-built graph (promise #1 dogfooded) |
| 2.3.3 | Add the demo to CI so it cannot rot. | done | `pytestmark = pytest.mark.core` — runs under `-m core` (26 tests, ~10s) |
| 2.3.4 | Reference the demo from the README quick-start as the verbatim walkthrough. | done | README § Development now describes the self-demo and points to `tests/test_self_demo.py` |

## 2.4 — Methodology post

| ID | Task | Status | Notes |
|----|------|--------|-------|
| 2.4.1 | Write `docs/methodology-precise-vs-fuzzy.md`: problem → method → numbers → interpretation → reproduce. | done | Written; problem/method/numbers/reproduce sections + an honest "what this measures and doesn't" close |
| 2.4.2 | Cite the corpus with details so numbers are reproducible. | done | Python corpus (cairn on itself): build time, symbol/edge counts, resolve rate, DB size recorded in the post |
| 2.4.3 | Link the post from README + benchmarks.md. | done | README resolution-labels section links the post first; benchmarks.md recall table + FP table both link it |

## Phase exit gate

Phase 2 is done when **all four** hold (mirror of `plan.md` § Definition of done):

- [x] 2.1.6 tables filled (Python corpus; Kotlin/TS deferred — need tuned query sets)
- [x] 2.2.1 `cairn verify` prints verdicts; 2.2.2 MCP returns verdict additively
- [x] 2.3.3 self-demo green in CI; 2.3.4 referenced from README
- [x] 2.4.1 post exists with real numbers; 2.4.3 linked from README + benchmarks.md

## Burndown

| Item | Sub-tasks | done | doing | todo | blocked | skipped |
|------|-----------|------|-------|------|---------|---------|
| 2.1 | 6 | 6 | 0 | 0 | 0 | 0 |
| 2.2 | 4 | 4 | 0 | 0 | 0 | 0 |
| 2.3 | 4 | 4 | 0 | 0 | 0 | 0 |
| 2.4 | 3 | 3 | 0 | 0 | 0 | 0 |
| **Total** | **17** | **17** | **0** | **0** | **0** | **0** |
