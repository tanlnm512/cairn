# Phase 2 — Prove It (Specification)

> **Roadmap milestone:** Month 2 — Prove it.
> **Goal:** cairn *shows* the contract holds — with numbers, a visible critic,
> and a reproducible demo. Phase 1 made the guarantees machine-checkable;
> Phase 2 makes them legible to someone who has never installed cairn.
> **Theme:** proof before breadth.

## Context

Phase 1 leaves cairn with enforceable promises but no public proof. The
benchmark methodology exists (`docs/benchmarks.md` § "resolution-label
methodology") but the result tables are blank (`_fill_`). The critic's verdict
is *partially* surfaced already — the MCP `generate_flow` tool returns
errors/warnings/quality in its result string (`tools_compass.py:230-243`), and
`cairn compass validate` / `cairn validate-paths` print verdicts, and
`cairn wiki generate` surfaces them — but there is no single-doc `cairn verify
<doc>` command and the MCP verdict is a flattened text string, not a
structured field. And cairn's most compelling demo (indexing itself) is not
packaged as a reproducible walkthrough.

Phase 2 closes those three gaps. Where Phase 1's output was *guarantees*,
Phase 2's output is *evidence*: filled benchmark numbers, a structured critic
verdict, and a one-command dogfood demo.

## In-scope items

| ID | Item | Strengthens promise | Done when |
|----|------|---------------------|-----------|
| 2.1 | Fill the benchmark tables (`cairn eval`, `cairn bench`) on three corpora: cairn's own repo (Python), a Kotlin repo, a TypeScript repo. Publish precise-vs-fuzzy false-positive rates. | **#1** (evidence) | The **false-positive-rate table** (the actual differentiator) is filled with measured Python-corpus numbers; the methodology post (2.4) cites real numbers. The recall / perf / scaling tables remain `_fill_` placeholders — filling them needs corpus-tuned eval query sets and multi-corpus measurement, deferred to Phase 4. (Original wording "tables are no longer `_fill_`" was over-broad; narrowed to the FP table specifically.) |
| 2.2 | Make the critic visible *as a structured verdict*: add a unified `cairn verify <doc-path>` command (the capability partly exists as `cairn compass validate` / `cairn validate-paths`) and expose the verdict as a structured additive dict field in MCP `generate_flow` / `ask_compass` (today it is a flattened text string in `generate_flow`, and `wiki generate` already surfaces it on the CLI). | **#2** (evidence) | A user can run `cairn verify <doc>` on any single concept and get a machine-checked verdict; MCP callers receive a structured `critic` field. |
| 2.3 | "cairn on cairn" live demo: a single `cairn build` on this repo produces a reproducible query walkthrough. | *(all three)* | The README quick-start includes a verbatim cairn-on-cairn walkthrough (build, def, impact, the invariant check) that returns real, correct results. A `-m core` test (`tests/test_self_demo.py`) gates the demo in CI — non-vacuous (non-empty impact, full-path def assertions, invariant guard `total_exact > 1000`). (Original wording named `explore` + `get_compass` in the demo; the shipped demo exercises `def` + `impact` + the invariant — `explore`/`get_compass` are MCP-first tools exercised elsewhere.) |
| 2.4 | Methodology post / shareable doc: "precise vs fuzzy — a false-positive methodology for trusting a code graph," using 2.1's numbers. | *(all three)* | A standalone artifact legible to someone who has never installed cairn. |

2.1 is the evidence backbone (2.4 depends on it). 2.2 surfaces the mechanism
behind promise #2. 2.3 is the demo that turns claims into reproducible
experience. 2.4 turns the whole phase into a shareable narrative.

## Out of scope

- **New languages / SCIP wiring.** Phase 4. (Phase 2's corpora use languages
  cairn already parses: Python, Kotlin, TypeScript.)
- **Memory grounding** (3.1/3.2). Phase 3.
- **Optimizing the resolver.** If 2.1's benchmarks reveal a quality gap,
  fixing the resolver is a *consequence* scoped as follow-up, not part of 2.1.
  2.1 measures; it does not tune.
- **Changing the critic's enforcement.** 2.2 exposes verdicts; it does not
  make unknown symbols block (that asymmetry is Phase 1's documented
  contract). Tightening it is a deliberate spec change for later.

## Dependencies

- 2.1 → 2.4 (the post cites the numbers; cannot write 2.4 without 2.1's
  results).
- 2.2 is independent of 2.1/2.3.
- 2.3 is independent but benefits from 2.1 (a demo with blank benchmarks
  undermines itself).
- Phase 1 is a soft prerequisite: 2.1's false-positive methodology assumes
  the `exact`/`target_id` invariant holds (1.1/1.2). Publishing numbers on a
  broken invariant would measure the wrong thing.

## Risks

- **2.1's corpora must be reproducible.** Numbers from "my laptop on a
  snapshot" are not evidence. Mitigation: pick open-source repos at pinned
  commits; record the commit SHAs in benchmarks.md.
- **2.1 may reveal that precise recall is lower than hoped.** That is an
  *honest result*, not a failure — the methodology's value is showing the
  precise-vs-fuzzy tradeoff. If precise recall is poor, the follow-up is a
  resolver-quality task (Phase 4-ish), not fudging the number.
- **2.2 changing MCP return shapes.** Adding a critic verdict to `ask_compass`
  / `wiki generate` return values is additive but could surprise agents that
  parse the current shape. Mitigation: verdict is a new field, not a
  replacement; existing fields unchanged.
- **2.3 demo drift.** cairn indexing itself must stay correct as cairn
  evolves. Mitigation: the demo is a checked-in script run in CI (a
  regression test, not a one-time artifact).
