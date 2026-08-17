# Benchmarks

cairn ships three harnesses for measuring retrieval quality,
build/query performance, and agent effort (tool calls + context tokens)
against ground truth or a control arm. This document explains **what**
each one measures, **how** to run it, and the methodology for the
resolution-label comparison that distinguishes cairn from name-only
("fuzzy") code graphs.

> **Status:** the reference tables below are GENERATED from the committed
> DS-v1 baseline artifacts (benchmarks/baselines/DS-v1/) between sentinel
> markers — never hand-edited; regenerate with
> `uv run python scripts/gen_benchmark_tables.py`. Matching numbers across
> machines is misleading; these cite their dataset version, machine class,
> and mint date in the provenance line under each table.

## Quick reference

| Command | Measures | Output |
|---------|----------|--------|
| `cairn eval` | Retrieval quality — Recall@10 and MRR vs ground truth; `--sweep` / `--kfold` lever sweeps (pooled paired bootstrap) | per-corpus table, JSON, or sweep document |
| `cairn bench --suite perf` | Build phase timings, embed cost, query latency | per-op table (median / p95 / ops/sec) |
| `cairn bench --suite scaling` | How build/embed cost scales with corpus size | per-size table (build / embed / DB MB / **resolve_rate**) |
| `cairn bench --suite agent` | Agent effort — tool calls + context tokens, cairn vs a grep/read control | per-task table (calls / est tokens / wall ms) |
| `scripts/measure_warm_time.py` | Warm-time — first semantic-query wall-time in a fresh process, warm-up active vs cold | `benchmarks/quality/warm_time.json` |
| `benchmarks/quality/ablation.md` | The retrieval-quality campaigns' unified ablation record (DS-v1 k-fold + DS-v2 zero-shot legs; verdict: no ship) | committed record, schema `cairn-quality-ablation/2` |

---

## Retrieval quality — `cairn eval`

Evaluates the L1 (code) and L5 (knowledge) retrieval pipelines against a
ground-truth query set.

**Fixture:** `tests/eval/queries.yaml` — 40 queries (30 L1 + 10 L5), each with
a `query` string and an `expect` list of expected symbol-name / concept-id
fragments. A query "passes" if any expected fragment is a case-insensitive
substring of any retrieved name in the top-*k*. `--queries` also accepts a
**ground-truth directory** — a `queries.jsonl` + `expectations.tsv` pair, the
form the committed datasets ship in; the sweep mode below requires it.

**Metrics:**

- **Recall@10** — fraction of queries with ≥1 match in the top 10.
- **MRR** (Mean Reciprocal Rank) — `1/rank` of the first match, averaged.

**Run:**

```bash
cairn eval                       # against the current DB + knowledge store
cairn eval --corpus L1           # code only
cairn eval --corpus L5           # knowledge only
cairn eval --json                # machine-readable
cairn eval --queries path/to/queries.yaml       # custom ground-truth yaml
cairn eval --queries path/to/ground_truth/      # ground-truth directory
cairn eval --queries path/to/ground_truth/ --sweep combos.json --out sweep.json
cairn eval --queries path/to/ground_truth/ --sweep combos.json --kfold --folds 5
```

**What it exercises:** `eval_l1_query` tries `semantic_search` first, falls
back to `search_symbols` (FTS5 + BM25) if embeddings are empty or it throws.
`eval_l5_query` uses the OKF bundle search. The fallback path means `cairn eval`
works on a default (no-torch) install — it just exercises the lexical pipeline.

**Ground truth: DS-v1 and DS-v2.** Two committed datasets live under
`benchmarks/datasource/`. **DS-v1** (`t2/ground_truth`) is the graded pair the
tables below cite. **DS-v2** (`ds2/`) is the larger successor: 198 queries —
154 L1 spanning four kinds (callers / definition / flow / impact) across the
two vendored corpora `yarl` and `attrs-26.1.0`, plus 44 L5 — carrying 558
hand-verified expectations, every one resolvable **tier-1-exact** (zero
aspirational rows). DS-v2 ships with its own verifier,
`benchmarks/datasource/ds2/verify_dataset.py` (runnable directly): it builds
fresh graphs over both corpora, re-resolves all 558 expectations, and
cross-checks the manifest's pinned corpus tree hashes. CI re-runs that
verifier on every push as the `ds2-seal` job, so the ground truth cannot
drift silently.

**Results (generated from DS-v1):**

<!-- cairn-bench-tables:quality start -->
| corpus | samples | recall@10 | mrr    |
|--------|---------|-----------|--------|
| L1     | 58      | 0.4174    | 0.2862 |
| L5 †   | 24      | 0.0000    | 0.0000 |

> 58 L1 queries / 160 expectations; 24 L5 queries / 74 expectations — graded pair (benchmarks/datasource/t2/ground_truth), identity-first matcher.
> † L5 surface absent for DS-v1: none (no OKF knowledge bundle for the t2 snapshot) — scores are 0.0 by construction, not retrieval failures.
> Source: DS-v1 baseline (benchmarks/baselines/DS-v1/quality.json) — runner class reference-local (macOS-26.5.2-arm64-arm-64bit, arm64, 10 CPUs), minted 2026-08-16, cairn 0.11.0, embed local / BAAI/bge-m3.
<!-- cairn-bench-tables:quality end -->

> **Historical note:** the pre-DS-v1 era (2026-08, generic queries.yaml set)
> reported recall@10 = 0.0 — the set targeted generic codebase shapes, not
> the corpus's symbols. DS-v1's hand-verified ground truth replaced that
> surface; the first real numbers are the L1 row above. See
> [methodology-precise-vs-fuzzy.md](methodology-precise-vs-fuzzy.md) for the
> measurement that *does* characterize cairn's differentiator (the 82%
> precise-vs-fuzzy false-positive rate on common names).

### Lever sweeps and k-fold cross-validation

`--sweep` replaces the single evaluation with a sweep over retrieval-lever
combinations: a JSON file or inline JSON list of `{name, params}` combos
(`RetrievalParams` fields; `null`/omitted = today's default). It requires
`--queries` pointing at a ground-truth directory, and by default evaluates
the TUNE split only — held-out ids are guarded by the harness. `--out` writes
the canonical sweep document; the harness itself never writes, so that flag
is the only writer.

`--kfold` (requires `--sweep`) runs the sweep once per fold of a seeded
k-fold rotation instead of one tune/validate split; `--folds` (default 5)
sets the fold count, and the harness refuses fewer than 5. The discipline is
deliberate: the significance verdict comes from a **pooled per-query paired
bootstrap across all folds** (every query is scored held-out exactly once),
with per-fold spread reported descriptively only — never as five independent
verdicts.

### Campaign verdict — nothing shipped

Two retrieval-quality campaigns ran through this harness. The unified record
is committed at `benchmarks/quality/ablation.{json,md}` (schema
`cairn-quality-ablation/2`; the first campaign's record is embedded verbatim
under `campaigns.retrieval-quality-v1`). The honest summary:

- On the DS-v1 k-fold leg, all three candidate levers **cleared the 95%
  pooled bootstrap guard**, and **multivector reached both SC-1 targets**
  (recall@10 **0.5588**, MRR **0.3395** — the first configuration in either
  campaign to do so).
- Zero-shot validation on DS-v2 **refuted the transfer**: multivector scored
  macro **0.4632 / 0.2844** across the two unseen corpora — below the
  incumbent's 0.4778 / 0.3769 macro — and no candidate improved on the
  incumbent zero-shot.
- The verdict is therefore **no ship**: defaults are unchanged, every lever
  remains flag-off and eval-harness-only (none is exposed through the MCP
  tools), and the committed figures of record stand.

The next binding constraint is lever generalization across corpora, not
evidence power; the armed follow-up experiments are listed in the ablation
record.

---

## Warm-time — first-query latency after boot warm-up

`scripts/measure_warm_time.py` measures what the boot-time model warm-up
(`cairn.graph.model_warmup`, wired into `cairn serve`) buys the first
`semantic_search`: two fresh subprocesses over one pre-built tiny embedded
DB, cold (the lazy embedder + cross-encoder loads land inside the query)
versus warm (`warm_models_in_background()` started and joined first). The
committed artifact is `benchmarks/quality/warm_time.json`; its `notes`
field records that the 322 ms figure in
`docs/phases/performance-gap/task.md` is **advisory context, not a gate** —
no committed baseline ever carried a warm-time number, so the artifact is
the first committed measurement and has no BEFORE to regress against.

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
> to catch performance regressions on PRs. `--baseline DS-v1` compares against
> the committed stamped artifact instead and prints a provenance header
> (dataset version + tree hash, cairn version, runner class) before the table,
> warning — advisory only — when the baseline's machine-profile *class* differs
> from the current run's. CI's bench job compares each PR against a **rolling
> same-class baseline** minted on every push to `main` (a SHA sidecar names the
> commit it was minted on); the committed DS-v1 artifact is the cold-start
> fallback.

**Results (generated from DS-v1):**

<!-- cairn-bench-tables:perf start -->
| operation             | median (ms) | p95 (ms) | ops/sec  |
|-----------------------|-------------|----------|----------|
| build (total)         | 1736.78     | 1736.78  | 0.58     |
| build.derived.closure | 25284.88    | 25284.88 | 0.04     |
| build.insert          | 412.48      | 412.48   | 2.42     |
| build.parse           | 255.97      | 255.97   | 3.91     |
| build.resolve         | 645.09      | 645.09   | 1.55     |
| build.scan            | 423.23      | 423.23   | 2.36     |
| embed_all             | 105.71      | 106.54   | 9.46     |
| explore               | 453.18      | 513.73   | 2.21     |
| find_definition       | 0.02        | 0.03     | 41595.33 |
| get_callees           | 0.02        | 0.03     | 47904.43 |
| get_callers           | 0.05        | 0.06     | 18882.46 |
| impact_analysis       | 0.10        | 0.11     | 10407.65 |
| impact_analysis_wide  | 0.89        | 0.89     | 1129.62  |
| search_symbols        | 6.21        | 6.25     | 161.02   |
| semantic_search       | 196.12      | 201.67   | 5.10     |
<!-- cairn-bench-tables:perf end -->

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

**Results (generated from DS-v1):**

<!-- cairn-bench-tables:scaling start -->
| files | symbols | build (s) | embed (s) | DB MB  | resolve | peak MB |
|-------|---------|-----------|-----------|--------|---------|---------|
| 100   | 3000    | 1.807     | 1.033     | 12.56  | 1.000   | 12.43   |
| 500   | 15000   | 7.703     | 5.149     | 61.88  | 1.000   | 59.33   |
| 1000  | 30000   | 17.808    | 10.327    | 123.63 | 1.000   | 118.38  |
| 5000  | 150000  | 475.929   | 51.555    | 618.92 | 1.000   | 591.20  |
<!-- cairn-bench-tables:scaling end -->

---

## T3 scale runs (local)

For real-world scale points beyond the synthetic corpus, the manifest also
pins two public repositories (`benchmarks/datasource/manifest.json`, `t3`
section — added in T019, never vendored):

| entry | pinned commit | scale point |
|-------|---------------|-------------|
| `home-assistant/core` | `0308f01b295a8ecfef9938b67514aa1b7b95e5bc` | ~27k files (18.2k Python) — mid scale |
| `torvalds/linux` | `3eb40771c00a8488fa6ed2cc1fe203477908bf38` | ~70k files (26.7k .c, 16.6k .h) — extreme scale |

These are fetched by a **local, maintainer-run** command — the multi-GB
clones are deliberately outside CI (the suite stays offline; no T3 fetch
step exists in `ci.yml`):

```bash
uv run python scripts/fetch_t3_corpus.py --list                # pins, no network
uv run python scripts/fetch_t3_corpus.py "home-assistant/core" # fetch by pin
uv run python scripts/fetch_t3_corpus.py "torvalds/linux" --run-bench
```

**Pin-enforcement contract.** The command *always* checks out the manifest's
pinned commit explicitly (`git clone --no-checkout` + `git checkout --detach
<pin>`, then a `rev-parse HEAD` equality check against the pin). The
default-branch HEAD is never materialized, and an unreachable pin (moved,
force-pushed away, typo'd) fails loudly — exit 3, naming the entry, the
expected pin, and what was found — rather than silently benching some other
commit. Clones cache outside the repository (`~/.cairn/bench-t3/<name>` by
default; `--cache`/`--dest` override), so nothing T3-related lands in the
checkout. The script lives in `scripts/` (decision D-009): no `git clone`
ever appears in `src/cairn`.

**Results record the manifest entry.** With `--run-bench`, the verified
checkout is benched via `cairn bench --workspace <checkout> --json --save
<dest>/<name>.json`, and the script stamps the manifest entry (repo +
commit + scale hint, the `t3_entry` shape the T013 artifact-stamp hook
defines) into the saved artifact's `dataset` block — so every T3 result is
self-describing about exactly which pinned corpus measured it.

**The eval ground truth is pinned the same way.** Beyond the T1/T2 content
pins in CI's bench job, the `ds2-seal` job re-runs
`benchmarks/datasource/ds2/verify_dataset.py` on every push — rebuilding
fresh graphs over both DS-v2 corpora and re-resolving all 558 expectations
tier-1-exact. Like the T1/T2 pins, it is a deterministic content check, not
a timing, so drift reddens CI instead of hiding in the tree.

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
  the baseline stabilizes. CI already does this with a rolling same-class
  baseline (re-minted on every push to `main`, SHA-attributed via sidecar),
  falling back to the committed DS-v1 artifact on cold start.

## See also

- [architecture.md § Resolution model](architecture.md#resolution-model) — the
  precise-vs-fuzzy design.
- [cli-reference.md](cli-reference.md) — full `cairn eval` / `cairn bench` flags.
