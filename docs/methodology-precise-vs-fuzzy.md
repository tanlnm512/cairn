# Precise vs Fuzzy: A False-Positive Methodology for Trusting a Code Graph

← [Docs index](README.md)

> A standalone methodology note — legible without installing cairn. Linked
> from the [README](../README.md) and [benchmarks](benchmarks.md).
> Read it when deciding how far to trust a fuzzy-mode call-graph answer, or
> when re-running the false-positive measurement on your own repo.

## Contents

| Section | What it covers |
|---------|----------------|
| [`## The problem`](#the-problem) | Why a name-only "who calls this" answer can't be trusted at face value. |
| [`## The methodology`](#the-methodology) | The three resolution labels, the two query modes, and the FP-rate formula. |
| [`## The numbers (cairn on cairn, Python corpus)`](#the-numbers-cairn-on-cairn-python-corpus) | Per-name and aggregate false-positive rates, their interpretation, and the empty-precise caveat. |
| [`## How to reproduce`](#how-to-reproduce) | The `cairn build` and `cairn callers` commands behind the counts. |
| [`## Corpus details`](#corpus-details) | What the run measured on: corpus, build shape, resolution mix, eval recall. |
| [`## What this measures, and what it doesn't`](#what-this-measures-and-what-it-doesnt) | The metric's scope — precision cost of trusting names, not recall. |
| [`## Reproducibility note`](#reproducibility-note) | Why to re-run on your own repo rather than quote these numbers. |

## The problem

Every code graph can tell you "who calls this." The hard question is **whether
to trust the answer.** A fuzzy graph — one that returns every call site whose
target shares a name with your query — is inflated by name collisions. Ask
"who calls `get`?" and you get every `get` in the repo, even though most of
them call entirely different functions that merely happen to share the name.

This is not a theoretical concern. On cairn's own codebase (1,942 symbols,
11,595 edges), the common name `get` has **565 fuzzy call sites but 0 precise
ones** — every single fuzzy result for `get` is a name collision, not a real
caller of any single `get` definition.

## The methodology

cairn's resolver labels every call edge with one of three resolutions:

- **`exact`** — the resolver pinned this edge to exactly one definition. Trusted.
- **`ambiguous`** — multiple candidate definitions existed; the resolver declined to guess.
- **`unresolved`** — external or stdlib; no definition in the indexed graph.

Graph queries then expose **two modes**:

- **Precise mode (default)** — follows only `exact` edges.
- **Fuzzy mode (`--fuzzy`)** — adds every name-only match, resolved or not.

The **false-positive rate** of the fuzzy mode, for a given name, is:

```
FP_rate = 1 - (precise_callers / fuzzy_callers)
```

A high FP rate for a common name means: *"if you trusted the fuzzy graph, most
of what it told you about this symbol was noise."* Precise mode exists to
exclude exactly that noise — and the FP rate quantifies how much noise, per
name and in aggregate.

## The numbers (cairn on cairn, Python corpus)

Measured on cairn's own source tree, freshly built (see corpus details below;
re-run on your own repo — numbers are machine- and corpus-specific). Target: a
set of common, collision-prone bare names. Counts use `get_callers(...,
limit=10000)` so they reflect the true edge population, not the default 200
result cap.

| name | precise callers | fuzzy callers | false-positive rate |
|------|----------------:|--------------:|--------------------:|
| `get` | 0 | 565 | 100% |
| `append` | 0 | 664 | 100% |
| `join` | 0 | 169 | 100% |
| `add` | 0 | 94 | 100% |
| `exists` | 0 | 108 | 100% |
| `mkdir` | 0 | 107 | 100% |
| `fetchone` | 0 | 112 | 100% |
| `strip` | 0 | 197 | 100% |
| `execute` | 493 | 493 | 0% |
| `close` | 1 | 177 | 99% |
| **aggregate** | **494** | **2,686** | **82%** |

**Interpretation.** For the eight names at 100%, the fuzzy graph is pure
noise — every result is a name collision. `execute` resolves precisely (0%
false positives) because there is exactly one `execute` definition in scope.
`close` is near-100% false-positive here because the test fixtures define a
second `close`, so the resolver correctly labels those calls `ambiguous`
rather than guessing — note how this *raises* the FP rate, demonstrating that
precise mode trades recall for precision exactly as designed. The aggregate
**82% false-positive rate** means: on common names in this corpus, trusting
the fuzzy graph would give you the wrong answer four times out of five.

### Empty precise ≠ unused

A precise result of 0 does **not** mean the symbol is dead — it means "no
*resolvable* callers." Retry with `--fuzzy` before concluding a symbol is
unused; the fuzzy candidates are each labelled unverified so you can check
them against the actual source.

## How to reproduce

```bash
cairn build                          # build the graph (first run)
cairn callers <name>                 # precise (default): real callers only
cairn callers <name> --fuzzy         # candidate list, each labelled unverified
```

For the FP rate, compare the counts: `FP = 1 - precise/fuzzy`.

## Corpus details

- **Corpus:** cairn's own source tree (`src/cairn/`, Python).
- **Build:** ~4s wall-clock (machine-dependent); 1,942 symbols, 11,595 edges
  (3,924 exact / 1,199 ambiguous / 6,472 unresolved), 229 files, 15.6 MB
  SQLite store.
- **Exact-pinned rate:** 34% of edges pinned `exact` (3,924 / 11,595). (The
  `cairn bench --suite scaling` "resolve_rate" column uses a broader
  definition `exact + ambiguous / total` = 44% — a different metric; do not
  conflate the two.)
- **Eval recall (Recall@10):** 0.0 on cairn's own repo. This is an honest
  finding: the eval query set (`tests/eval/queries.yaml`) targets generic
  codebase shapes, not cairn's specific symbol names, so no expected fragment
  matches. It measures the query set's fit to the corpus, not retrieval
  quality. A corpus-tuned query set is future work.

## What this measures, and what it doesn't

This methodology measures **the cost of trusting a name-only graph** — the
noise precise mode removes. It does not measure recall (does the graph find
every real caller?), which depends on the resolver's coverage. The two are
complementary: precise mode trades recall for precision, and the FP rate
quantifies the precision gain. A resolver that labeled everything `exact`
would score 0% false positives but be wrong; the invariant test
(`tests/test_invariants.py::test_invariant_exact_resolution_has_target_id`)
guards that `exact` only labels genuinely-resolved edges.

## Reproducibility note

Numbers are machine- and corpus-specific. Re-run on your own repo to get your
own FP rates — the methodology is the portable part, not the specific
percentages.
