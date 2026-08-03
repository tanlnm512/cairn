# Benchmarks

codegraph ships two harnesses for measuring retrieval quality and
build/query performance against ground truth. This document explains **what**
each one measures, **how** to run it, and the methodology for the
resolution-label comparison that distinguishes codegraph from name-only
("fuzzy") code graphs.

> **Status:** the harnesses are checked in and self-running; the number tables
> below are intentionally left as placeholders for you to fill with results
> from your own hardware and corpus. Matching numbers across machines is
> misleading — run them in your environment.

## Quick reference

| Command | Measures | Output |
|---------|----------|--------|
| `cg eval` | Retrieval quality — Recall@10 and MRR vs ground-truth queries | per-corpus table or JSON |
| `cg bench --suite perf` | Build phase timings, embed cost, query latency | per-op table (median / p95 / ops/sec) |
| `cg bench --suite scaling` | How build/embed cost scales with corpus size | per-size table (build / embed / DB MB / **resolve_rate**) |

---

## Retrieval quality — `cg eval`

Evaluates the L1 (code) and L5 (knowledge) retrieval pipelines against a
ground-truth query set.

**Fixture:** `tests/eval/queries.yaml` — 40 queries (30 L1 + 10 L5), each with
a `query` string and an `expect` list of expected symbol-name / concept-id
fragments. A query "passes" if any expected fragment is a case-insensitive
substring of any retrieved name in the top-*k*.

**Metrics:**

- **Recall@10** — fraction of queries with ≥1 match in the top 10.
- **MRR** (Mean Reciprocal Rank) — `1/rank` of the first match, averaged.

**Run:**

```bash
cg eval                       # against the current DB + knowledge store
cg eval --corpus L1           # code only
cg eval --corpus L5           # knowledge only
cg eval --json                # machine-readable
cg eval --queries path/to/queries.yaml   # custom ground-truth set
```

**What it exercises:** `eval_l1_query` tries `semantic_search` first, falls
back to `search_symbols` (FTS5 + BM25) if embeddings are empty or it throws.
`eval_l5_query` uses the OKF bundle search. The fallback path means `cg eval`
works on a default (no-torch) install — it just exercises the lexical pipeline.

**Results template:**

| corpus | samples | recall@10 | mrr |
|--------|---------|-----------|-----|
| L1     | 30      | _fill_    | _fill_ |
| L5     | 10      | _fill_    | _fill_ |

---

## Performance — `cg bench --suite perf`

Measures per-operation latency against a generated synthetic Python corpus
(unless `--workspace` points at a real repo).

**What it measures:**

- **Build phase** — instrumented via the builder's `progress` callback:
  `scan → parse → insert → resolve → persist`, plus a `build (total)`.
- **Embed phase** — `embed_all`, warmup + `--repeats` timed runs.
- **Query battery** — `find_definition`, `search_symbols`, `get_callers`,
  `get_callees`, `impact_analysis`, plus guarded `semantic_search` and
  `explore` over real symbols sampled from the built graph.
- Also records `db_size_mb`, `symbols`, `edges`.

**Run:**

```bash
cg bench --suite perf                       # synthetic corpus, medium complexity
cg bench --suite perf --n-files 1000        # bigger corpus
cg bench --suite perf --workspace /path/to/repo   # real repo
cg bench --suite perf --json --save base.json     # capture a baseline
cg bench --suite perf --compare base.json         # diff + exit 2 on regression
cg bench --suite perf --compare base.json --threshold 0.10   # tighten to 10%
```

> **CI signal:** `--compare` exits with code **2** if any operation regressed
> by more than `--threshold` (default 15%) versus the baseline. Wire it into CI
> to catch performance regressions on PRs.

**Results template:**

| operation         | median (ms) | p95 (ms) | ops/sec |
|-------------------|-------------|----------|---------|
| build (total)     | _fill_      | —        | —       |
| build.parse       | _fill_      | —        | —       |
| build.resolve     | _fill_      | —        | —       |
| embed_all         | _fill_      | —        | —       |
| find_definition   | _fill_      | _fill_   | _fill_  |
| search_symbols    | _fill_      | _fill_   | _fill_  |
| get_callers       | _fill_      | _fill_   | _fill_  |
| get_callees       | _fill_      | _fill_   | _fill_  |
| impact_analysis   | _fill_      | _fill_   | _fill_  |

---

## Scaling — `cg bench --suite scaling`

Measures how build/embed cost grows with corpus size.

**What it measures:** for each size in `--sizes` (default `100,500,1000,5000`),
generates a fresh synthetic corpus, builds + embeds under a single
`peak_memory` trace, and records one row with `n_files`, `symbols`,
`build_seconds`, `embed_seconds`, `db_size_mb`, `resolve_rate`, and
`peak_memory_mb`.

**Run:**

```bash
cg bench --suite scaling
cg bench --suite scaling --sizes 100,1000,10000
cg bench --suite scaling --json
```

**Results template:**

| files | symbols | build (s) | embed (s) | DB MB | resolve | peak MB |
|-------|---------|-----------|-----------|-------|---------|---------|
| 100   | _fill_  | _fill_    | _fill_    | _fill_ | _fill_  | _fill_  |
| 500   | _fill_  | _fill_    | _fill_    | _fill_ | _fill_  | _fill_  |
| 1000  | _fill_  | _fill_    | _fill_    | _fill_ | _fill_  | _fill_  |
| 5000  | _fill_  | _fill_    | _fill_    | _fill_ | _fill_  | _fill_  |

---

## The resolution-label methodology (codegraph's differentiator)

codegraph labels every call edge `exact`, `ambiguous`, or `unresolved`. Precise
queries (the default) follow **only** `exact` edges, so blast radius is never
inflated by name collisions. Fuzzy queries (`--fuzzy` / `fuzzy=True`) add
name-only matches as an explicitly-labelled candidate list.

This is measurable, and is the single most important comparison to draw against
name-only / fuzzy code graphs.

### Precise-vs-fuzzy false-positive rate

For a symbol that shares its name with unrelated definitions across the
codebase:

```bash
# Precise (default): only resolved edges. Ground truth for blast radius.
cg impact invoke                      # exact-edge callers only

# Fuzzy: name-only candidates — every site that merely shares the name.
cg impact invoke --fuzzy              # candidate list, labelled unverified
```

The **false-positive rate** of fuzzy is:

```
FP_rate = 1 - (precise_callers / fuzzy_callers)
```

A name like `invoke` (common in DI / UseCase idioms) typically yields a fuzzy
candidate list spanning hundreds of unrelated sites, while precise returns only
the real callers. Capture both counts and report `FP_rate` — it quantifies how
much a fuzzy graph would inflate impact estimates.

| symbol          | precise callers | fuzzy candidates | fuzzy FP rate |
|-----------------|-----------------|------------------|---------------|
| _example_       | _fill_          | _fill_           | _fill_        |

### `explore` surfaces ambiguous dispatch

The `explore` tool reports `ambiguous` dispatch hops — polymorphic call sites
the resolver couldn't pin to one definition. These are edges that grep cannot
see at all (they require symbol resolution, not text matching). Count them:

```bash
cg ask "how does <X> dispatch"        # explore reports ambiguous hops inline
```

### Token / tool-call reduction vs a grep-only baseline

To measure how much codegraph reduces agent context cost versus a naive
"grep-and-read" agent workflow:

1. Pick *N* representative questions ("how does auth work", "who calls X",
   "what breaks if I change Y").
2. For each, run an agent **without** codegraph (grep + file reads) and **with**
   codegraph (`explore` + drill-down). Capture token usage and tool-call count.
3. Report the deltas:

| metric          | grep-only baseline | with codegraph | reduction |
|-----------------|--------------------|----------------|-----------|
| tokens / query  | _fill_             | _fill_         | _fill_%   |
| tool calls / query | _fill_          | _fill_         | _fill_%   |
| wall-clock / query | _fill_          | _fill_         | _fill_×   |

---

## Interpreting results

- **Build times** dominate at scale; query times are sub-millisecond once the
  graph is built. Optimize the build path (`--workers`, incremental `cg update`)
  rather than query latency.
- **resolve_rate** (from the scaling suite) is the fraction of edges the
  resolver pinned to a definition (`exact + ambiguous / total`). It surfaces
  how much of your graph is *trustworthy* for precise queries. A low rate
  means many edges are `unresolved` (external/stdlib) — expected, not a bug.
- **Regression gates** (`--compare`) should pin a baseline on your main branch
  and run on every PR. Use `--threshold 0.15` as a starting point; tighten as
  the baseline stabilizes.

## See also

- [architecture.md § Resolution model](architecture.md#resolution-model) — the
  precise-vs-fuzzy design.
- [cli-reference.md](cli-reference.md) — full `cg eval` / `cg bench` flags.
