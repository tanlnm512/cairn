# Benchmarks

cairn ships three harnesses for measuring retrieval quality,
build/query performance, and agent effort (tool calls + context tokens)
against ground truth or a control arm. This document explains **what**
each one measures, **how** to run it, and the methodology for the
resolution-label comparison that distinguishes cairn from name-only
("fuzzy") code graphs.

> **Status:** the harnesses are checked in and self-running; the number tables
> below are intentionally left as placeholders for you to fill with results
> from your own hardware and corpus. Matching numbers across machines is
> misleading — run them in your environment.

## Quick reference

| Command | Measures | Output |
|---------|----------|--------|
| `cairn eval` | Retrieval quality — Recall@10 and MRR vs ground-truth queries | per-corpus table or JSON |
| `cairn bench --suite perf` | Build phase timings, embed cost, query latency | per-op table (median / p95 / ops/sec) |
| `cairn bench --suite scaling` | How build/embed cost scales with corpus size | per-size table (build / embed / DB MB / **resolve_rate**) |
| `cairn bench --suite agent` | Agent effort — tool calls + context tokens, cairn vs a grep/read control | per-task table (calls / est tokens / wall ms) |

---

## Retrieval quality — `cairn eval`

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
cairn eval                       # against the current DB + knowledge store
cairn eval --corpus L1           # code only
cairn eval --corpus L5           # knowledge only
cairn eval --json                # machine-readable
cairn eval --queries path/to/queries.yaml   # custom ground-truth set
```

**What it exercises:** `eval_l1_query` tries `semantic_search` first, falls
back to `search_symbols` (FTS5 + BM25) if embeddings are empty or it throws.
`eval_l5_query` uses the OKF bundle search. The fallback path means `cairn eval`
works on a default (no-torch) install — it just exercises the lexical pipeline.

**Results template:**

| corpus | samples | recall@10 | mrr |
|--------|---------|-----------|-----|
| L1     | 30      | _fill_    | _fill_ |
| L5     | 10      | _fill_    | _fill_ |

> **Measured on cairn's own repo (Python, 2026-08):** L1 recall@10 = 0.0,
> L5 recall@10 = 0.0. This is an honest finding, not a quality regression:
> the query set in `tests/eval/queries.yaml` targets generic codebase shapes,
> not cairn's specific symbol names, so no expected fragment matches. It
> measures the query set's fit to the corpus, not retrieval quality. A
> corpus-tuned query set is future work. See
> [methodology-precise-vs-fuzzy.md](methodology-precise-vs-fuzzy.md) for the
> measurement that *does* characterize cairn's differentiator (the 82%
> precise-vs-fuzzy false-positive rate on common names).

---

## Performance — `cairn bench --suite perf`

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
cairn bench --suite perf                       # synthetic corpus, medium complexity
cairn bench --suite perf --n-files 1000        # bigger corpus
cairn bench --suite perf --workspace /path/to/repo   # real repo
cairn bench --suite perf --json --save base.json     # capture a baseline
cairn bench --suite perf --compare base.json         # diff + exit 2 on regression
cairn bench --suite perf --compare base.json --threshold 0.10   # tighten to 10%
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

## Scaling — `cairn bench --suite scaling`

Measures how build/embed cost grows with corpus size.

**What it measures:** for each size in `--sizes` (default `100,500,1000,5000`),
generates a fresh synthetic corpus, builds + embeds under a single
`peak_memory` trace, and records one row with `n_files`, `symbols`,
`build_seconds`, `embed_seconds`, `db_size_mb`, `resolve_rate`, and
`peak_memory_mb`.

**Run:**

```bash
cairn bench --suite scaling
cairn bench --suite scaling --sizes 100,1000,10000
cairn bench --suite scaling --json
```

**Results template:**

| files | symbols | build (s) | embed (s) | DB MB | resolve | peak MB |
|-------|---------|-----------|-----------|-------|---------|---------|
| 100   | _fill_  | _fill_    | _fill_    | _fill_ | _fill_  | _fill_  |
| 500   | _fill_  | _fill_    | _fill_    | _fill_ | _fill_  | _fill_  |
| 1000  | _fill_  | _fill_    | _fill_    | _fill_ | _fill_  | _fill_  |
| 5000  | _fill_  | _fill_    | _fill_    | _fill_ | _fill_  | _fill_  |

---

## The resolution-label methodology (cairn's differentiator)

cairn labels every call edge `exact`, `ambiguous`, or `unresolved`. Precise
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
cairn impact invoke                      # exact-edge callers only

# Fuzzy: name-only candidates — every site that merely shares the name.
cairn impact invoke --fuzzy              # candidate list, labelled unverified
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
| `get`           | 0               | 565              | 100%          |
| `append`        | 0               | 664              | 100%          |
| `join`          | 0               | 169              | 100%          |
| `add`           | 0               | 94               | 100%          |
| `exists`        | 0               | 108              | 100%          |
| `mkdir`         | 0               | 107              | 100%          |
| `fetchone`      | 0               | 112              | 100%          |
| `strip`         | 0               | 197              | 100%          |
| `execute`       | 493             | 493              | 0%            |
| `close`         | 1               | 177              | 99%           |
| **aggregate**   | **494**         | **2,686**        | **82%**       |

Measured on cairn's own repo (Python, 2026-08): 1,942 symbols, 11,595 edges
(3,924 exact / 1,199 ambiguous / 6,472 unresolved), 34% exact-pinned rate,
~4s build. Counts use `limit=10000` so they reflect the true edge population,
not the default 200 result cap. Full writeup:
[methodology-precise-vs-fuzzy.md](methodology-precise-vs-fuzzy.md).

### `explore` surfaces ambiguous dispatch

The `explore` tool reports `ambiguous` dispatch hops — polymorphic call sites
the resolver couldn't pin to one definition. These are edges that grep cannot
see at all (they require symbol resolution, not text matching). Count them:

```bash
cairn ask "how does <X> dispatch"        # explore reports ambiguous hops inline
```

### Agent-effort reduction vs a grep-only baseline — `cairn bench --suite agent`

Measures what an agent *spends* answering task-shaped questions — tool calls
and context tokens — with cairn's query tools versus a plain grep-and-read
loop. No LLM is in the loop: both arms are deterministic scripts over the
same synthetic corpus, so the comparison is reproducible in CI (no network,
no model, hash embed backend, reranker pinned off).

Six tasks, each genuinely answerable by both arms:

| task | question shape | cairn arm | grep/read control arm |
|------|----------------|-----------|----------------------|
| `definition-lookup` | where is `Cls0011_1` defined? | `find_definition` | grep `class Cls0011_1:` → read hits |
| `caller-enumeration` | which code calls it? | `find_definition` → `get_callers` | grep the name → read every hit |
| `blast-radius-depth3` | what breaks if it changes (depth 3)? | `find_definition` → `impact_analysis` | grep name → read → grep the names those files *define* → read (3 rounds) |
| `entry-to-leaf-flow` | what does `method_2` execute end-to-end? | `trace_flow` | grep name → read → grep the names those files *call* → read (4 rounds) |
| `concept-search` | what relates to the `Cls0039` cluster? | `semantic_search` | grep the keyword → read hits |
| `common-name-impact` | what breaks if `method_0` changes? (name shared by every class) | `impact_analysis` precise → `fuzzy=True` escalation | grep `method_0(` → read hits (matches ~every file) |

**Run:**

```bash
cairn bench --suite agent                      # 300-file synthetic corpus, 3 runs
cairn bench --suite agent --n-files 100 --runs 5
cairn bench --suite agent --workspace /path/to/repo --json
cairn bench --suite agent --json --save base.json && cairn bench --suite agent --compare base.json
```

**Measured** (`cairn bench --suite agent`, default synthetic corpus: 300
files / 63,900 lines / 1.7 MB, medium complexity, corpus + task seed
`0xC0DE`, 3 runs, medians; hash embed backend; tokens = chars / 4):

| task | cairn calls | grep calls | cairn tokens | grep tokens | token reduction |
|------|------------:|-----------:|-------------:|------------:|----------------:|
| definition-lookup | 1 | 2 | 427 | 1,432 | 70% |
| caller-enumeration | 2 | 5 | 1,429 | 5,728 | 75% |
| blast-radius-depth3 | 2 | 303 | 712 | 429,600 | 99.8% |
| entry-to-leaf-flow | 1 | 302 | 1,595 | 429,600 | 99.6% |
| concept-search | 1 | 6 | 601 | 7,160 | 92% |
| common-name-impact | 2 | 301 | 2,114 | 429,600 | 99.5% |
| **total (6 tasks)** | **9** | **919** | **6,878** | **1,303,120** | **99.5%** |

Aggregate, per query (6 queries):

| metric          | grep-only baseline | with cairn | reduction |
|-----------------|--------------------|------------|-----------|
| tokens / query  | 217,187            | 1,146      | 99.5%     |
| tool calls / query | 153.2           | 1.5        | 99.0%     |
| wall-clock / query (in-process) | 24.9 ms | 7.6 ms | 3.3× |

**Methodology.** The **cairn arm** runs the queries-layer call sequence an
agent would make per task; each call counts once, and its context cost is the
JSON-serialized result an MCP client receives, capped at
`MAX_RESULT_CHARS` (60,000) exactly as `cairn serve` caps it. The **control
arm** is a fixed grep/read recipe (stdlib `re` over the corpus in sorted
order — no ripgrep tuning, no ctags): grep the symbol name, read only the
matched files, follow hops by grepping the names those files define
(impact-shaped tasks) or call (flow-shaped tasks), one alternation grep per
hop (bounded at 40 names), each file read once per task. Its cost is
greps + file reads, and the chars of the matched file content it reads.
Tokens everywhere are the documented proxy **chars / 4** — the same
~4-chars-per-token approximation the embeddings chunker and the MCP result
cap use, not a real tokenizer. Targets are picked from the built graph by a
seeded RNG (seed `0xC0DE`, overridable), the corpus is the `generate_corpus`
default seed, and per-task numbers are medians over `--runs` (default 3).
Two consecutive full runs of the table above produced identical call and
token counts in both arms.

**Honest limitations.** This is a scripted harness, not a live LLM agent:
real agents interleave reasoning with tool calls, and a stronger grep agent
(better patterns, file-span reads instead of whole files, an attention cap)
would over-read less — the grep arm is a fixed recipe, not an optimized
search. The corpus is deterministic synthetic Python, so collision-heavy
tasks (`common-name-impact`, where grep reads essentially the whole corpus)
may overstate what happens on codebases with more unique names; conversely
cairn's answer is bounded (`limit`) while grep enumerates every match.
Wall-clock is in-process tool execution, not end-to-end agent latency — a
real agent pays an LLM round-trip per tool call, which multiplies the
call-count advantage; and on `concept-search` the grep arm is genuinely
*faster* (8.3 ms vs 35.6 ms for the hash backend's brute-force cosine scan),
shown unrounded in the JSON rather than hidden. Symbol ids are random per
build, so a tie-bounded result set (`semantic_search`'s limit cutoff) can
swap one near-tied row between rebuilds — observed drift is well under 1%,
far below the 15% `--compare` gate.

---

## Interpreting results

- **Build times** dominate at scale; query times are sub-millisecond once the
  graph is built. Optimize the build path (`--workers`, incremental `cairn update`)
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
- [cli-reference.md](cli-reference.md) — full `cairn eval` / `cairn bench` flags.
