# Benchmarks

← [Docs index](README.md)

What `cairn bench` measures and how to reproduce every published number from
a clean clone. Read this when you want to verify a benchmark claim or rerun
a suite yourself. Every command below is runnable as written; every published
number carries the stamp of the exact task set that produced it.

| Suite | Command | Measures |
|---|---|---|
| perf | `cairn bench --suite perf` | Query latency (p95) over a synthetic corpus |
| scaling | `cairn bench --suite scaling --sizes 100,500,1000,5000` | Latency vs corpus size |
| agent | `cairn bench --suite agent` | Agent effort (tool calls, tokens) vs a grep baseline on a synthetic corpus |
| swe-bench | `cairn bench --suite swe-bench` | Agent effort vs a grep baseline on the pinned SWE-bench Lite subset |

Exit-code contract for all suites: 0 clean; 1 usage/configuration error;
2 regressions found by a baseline comparison. `--json` emits the
machine-readable payload; `--save FILE` persists it; `--compare FILE` /
`--baseline VERSION` compare against a saved report (swe-bench comparisons
use the agent-report shape).

## Agent deterministic arm

`cairn bench --suite agent` runs the same seven task-shaped questions through
two scripted arms: cairn graph calls and a grep/read-only control. The suite
uses a deterministic 300-file corpus (seed `49374`), three runs per task, the
dep-free hash embedder, and reports per-task medians. Tool calls are
deterministic; estimated tokens vary by at most about 2% run-to-run (medians
can land on an adjacent value); wall time is advisory and machine-dependent.

Context cost uses the deployed agent contract: cairn results are
JSON-serialized and capped at the MCP result limit; control-arm file reads
count full text. `est_tokens = chars / 4`.

| metric (median / task) | grep baseline | with cairn | reduction |
|---|---:|---:|---:|
| tool calls | 301 | 1 | 99.7% |
| est. tokens | 429,600 | 1,479 | 99.7% |

Task shapes: definition lookup, caller enumeration, depth-3 blast radius,
entry-to-leaf flow, concept search, common-name impact, and a 4,000-token
context-pack fit check. The pack fit arm finds all four seeded targets
(`fit_rate = 1.0`) and consumes 2,077 estimated tokens.

## Scaling snapshot

`cairn bench --suite scaling` builds isolated corpora at 100, 500, 1000, and
5000 files, multiplies structural edges five-fold, and exercises full plus
incremental transitive-closure maintenance. Wall timings and database sizes
are machine-specific; the reference-local snapshot below is advisory.

| point | symbols | structural edges | build | closure | closure peak | maintain | DB |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1,000 files | 31,001 | 700,000 | 13.6 s | 6.8 s | 228 MB | 0.34 s | 311 MB |
| 5,000 files | 155,001 | 3,500,000 | 541.3 s | 36.8 s | 1,139 MB | 2.29 s | 1,558 MB |

The 1000-file point is the CI budget gate: closure stays within 60 s and
512 MB, while bounded incremental maintenance stays under 2 s.

## SWE-bench deterministic arm

`cairn bench --suite swe-bench` measures context-acquisition efficiency on
SWE-bench task inputs: a cairn-equipped arm (graph queries over the task's
repository) against a grep-and-read control arm, both deterministic scripted
loops — no LLM, no network after warm-up, no subprocesses in the measured
path. The report lists per-task rows keyed by `instance_id` and cross-task
medians: tool calls and estimated tokens for both arms, plus the reduction.

### Metric class

Published swe-bench numbers are efficiency medians — median tool calls and
median estimated tokens per task — and form their own metric class. They are
not numerically comparable to resolve-rate results on SWE-bench (including
RepoGraph's +32.8% relative improvement), which measure whether an LLM agent
solves the task. No resolve-rate figure is published here: the deterministic
arm cannot attempt resolution, and the LLM-in-the-loop arm is out of scope.

### Task set and pinning

Tasks come from SWE-bench Lite, `test` split, pinned by
`benchmarks/datasource/swe-bench-subset.json`:

| Manifest key | Value |
|---|---|
| `dataset` | `princeton-nlp/SWE-bench_Lite` |
| `split` | `test` |
| `dataset_revision_sha` | `6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2` |
| `subset` | 50 ordered instance ids (6 astropy, 44 django) |
| `reported` | `true` — this subset backs the published medians |

The loader validates the manifest before any fetch, loads the split at the
pinned revision, and projects each row to five fields: `instance_id`,
`repo`, `base_commit`, `problem_statement`, `environment_setup_commit`.
Gold-patch fields (`patch`, `test_patch`, `FAIL_TO_PASS`, `PASS_TO_PASS`)
never cross the loader seam. No task content is vendored: the first run
fetches the split from the Hugging Face Hub and clones each task's repository
at `base_commit` into a content-addressed workspace cache
(`<CAIRN_HOME>/cache/swe-bench/`); after warm-up every rerun is fully
offline.

Every saved report carries a dataset stamp: the manifest's revision sha, the
sha256 digest of the manifest file bytes, the instance count, and the slice
label when one was used. Numbers are only comparable under an identical
stamp.

### Measurement recipe

The recipe is part of the metric:

1. Per task, the probe target is the first code-like identifier in
   `problem_statement` — a token containing an underscore, a digit, or
   internal/all-uppercase letters, in first-occurrence order.
2. The cairn arm plays the fixed sequence `find_definition(limit=10)` →
   `get_callers(limit=100)` → `impact_analysis(max_depth=3, limit=100)` over
   the graph built from the task's checked-out workspace.
3. The control arm greps the same identifier (literal substring match over
   `*.py` files in sorted order) and reads each matched file once.
4. Both arms run `--runs` times (default 3); per-task effort is the run
   median; the report's medians are the medians across tasks.
5. Token accounting: the cairn arm counts JSON-serialized result characters
   per call, capped at the MCP result cap, with build-volatile uuid ids
   masked before counting; the control arm counts the full text of each read
   file. `est_tokens = chars / 4` (`chars_per_token` in the report).

Caveat: lexical probe-target extraction can select an identifier absent from
the workspace; both arms then report near-zero effort and that row's
reduction is not meaningful (7 of the 50 pinned rows). Published medians are
computed over all pinned tasks as-is.

### Determinism and offline contracts

- Tool calls and `est_tokens` are deterministic: identical across reruns and
  across fresh builds. Wall time is advisory — printed live, never persisted.
- The persisted report carries no timestamp and no wall-clock figures;
  reruns save byte-identical JSON.
- The run pins `CAIRN_EMBED_BACKEND=hash` and `CAIRN_RERANK=0` and builds
  into an isolated database; the environment is restored afterward.
- After the first fetch and checkouts, reruns operate fully offline
  (`HF_HUB_OFFLINE=1` is supported for the split read).

### Reproducing the published medians

From a clean clone (Python ≥ 3.10, `git` on PATH, network for the first
fetch only):

```bash
git clone https://github.com/tanlnm512/cairn && cd cairn
pip install -e '.[bench]'
cairn bench --suite swe-bench --smoke      # first 2 pinned tasks; fast check
cairn bench --suite swe-bench --json       # full pinned subset; the published run
```

The `--json` payload's `medians` block must equal the published medians
below exactly — no tolerance band (the deterministic contract). Run options:
`--slice START:END` selects a positional span of the pinned subset;
`--manifest PATH` points at a different pin file. These are run options, not
published configurations; the CI rot guard runs the same suite logic
hermetically (a `-m core` smoke test over a local fixture task set — no
network, no `datasets` import).

### Published medians (pinned subset, 50 tasks, 3 runs)

| metric | grep baseline | with cairn | reduction |
|---|---:|---:|---:|
| tool calls / task (median) | 19.5 | 3.0 | 84.6% |
| est. tokens / task (median) | 151,007.5 | 1,555.5 | 99.0% |

Stamp: `princeton-nlp/SWE-bench_Lite` `test` @
`6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2`,
manifest sha256 `c3d3743cf5c2042eb0123077388724e414c64a81e9ff06bad3b7f706ceafc742`,
50 instances, `--runs 3`.

### What this suite does not measure

No LLM runs in the loop, so no resolve rate is published. Resolution
measurement (gold patches, the `swebench` evaluation harness) belongs to a
separate, deferred arm on the same pinned split.
