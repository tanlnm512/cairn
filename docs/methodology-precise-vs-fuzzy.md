# Precise vs Fuzzy: A False-Positive Methodology for Trusting a Code Graph

> A standalone methodology note — legible without installing cairn. Linked
> from the [README](../README.md) and [benchmarks](benchmarks.md). Part of
> Phase 2 ("Prove it") of the [roadmap](roadmap.md).

## The problem

Every code graph can tell you "who calls this." The hard question is **whether
to trust the answer.** A fuzzy graph — one that returns every call site whose
target shares a name with your query — is inflated by name collisions. Ask
"who calls `get`?" and you get every `get` in the repo, even though most of
them call entirely different functions that merely happen to share the name.

This is not a theoretical concern. On cairn's own codebase (1,929 symbols,
11,514 edges), the common name `get` has **200 fuzzy call sites but 0 precise
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

Measured on cairn's own source tree, freshly built (commit pinned below).
Target: a set of common, collision-prone bare names.

| name | precise callers | fuzzy callers | false-positive rate |
|------|----------------:|--------------:|--------------------:|
| `get` | 0 | 200 | 100% |
| `append` | 0 | 200 | 100% |
| `join` | 0 | 169 | 100% |
| `add` | 0 | 94 | 100% |
| `exists` | 0 | 108 | 100% |
| `mkdir` | 0 | 106 | 100% |
| `fetchone` | 0 | 111 | 100% |
| `strip` | 0 | 197 | 100% |
| `execute` | 200 | 200 | 0% |
| `close` | 175 | 175 | 0% |
| **aggregate** | **375** | **1,560** | **76%** |

**Interpretation.** For the eight names at 100%, the fuzzy graph is pure
noise — every result is a name collision. For `execute` and `close`, cairn's
resolver pins them precisely (0% false positives) because their definitions
are unambiguous in scope. The aggregate **76% false-positive rate** means: on
common names in this corpus, trusting the fuzzy graph would give you the wrong
answer three times out of four.

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
- **Build:** 4.0s wall-clock; 1,929 symbols, 11,514 edges (4,066 exact / 1,020
  ambiguous / 6,428 unresolved), 227 files, 15.4 MB SQLite store.
- **Resolve rate:** 35% of edges pinned `exact` (4,066 / 11,514).
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
