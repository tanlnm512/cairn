# Plan: Benchmark Datasource — pinned baselines for future comparison

Companion: [spec.md](spec.md) · [task.md](task.md). "Done when" wording is
owned by spec.md and repeated verbatim in task.md.

## 0. Evidence base (survey, 2026-08-16)

- CI bench job (`.github/workflows/ci.yml` `bench:`) compares PRs against a
  **rolling actions/cache baseline** refreshed by main-branch runs — advisory
  comment, never a gate, nothing pinned.
- `cairn bench --save/--compare` (cli/bench.py) compares two local JSON runs;
  `compare_reports` thresholds at 15%; artifacts carry a timestamp but no
  dataset version or machine profile.
- `bench/corpus.py`: seeded generator, deterministic, but dense-random
  (closure 340 s @1,000 files) with twin docstrings — unusable for quality
  calibration (rerank-gate calibration fell back to cairn's own src/).
- `src/cairn/eval.py`: recall@10/MRR harness for L1 code + L5 knowledge
  retrieval against ground-truth query datasets — dataset not maintained.
- `docs/benchmarks.md`: three `_fill_` families (retrieval L1/L5, perf query
  table, scaling table).
- Hard constraint: CI is network-free; heavy wheels already in core
  (numpy promotion noted for size budgeting of fixtures).

## 1. Implementation options

### Option A — Pin the synthetic tier only

Manifest + hash-check `bench/corpus.py` outputs; commit baseline JSONs
generated from it; fill docs tables from those.

- **Pros:** smallest change; zero licensing/size questions; fully
  deterministic; everything runs in CI today.
- **Cons:** baselines inherit the corpus's pathologies — timing references
  reflect a dense-random graph nothing real looks like (closure 340 s is
  20× a real repo's shape), and quality tables would be built on
  twin-docstring data the rerank calibration already proved meaningless.
  "Comparable in the future" would be true but the comparison would be
  about a world nobody deploys.

### Option B — Vendored real repos everywhere

Vendor 2–3 real repo snapshots (small, medium, large) into the repo; all
suites and baselines run against them.

- **Pros:** maximum realism; one tier to maintain conceptually; quality
  ground truth is natural to author.
- **Cons:** size (even 3 small repos ≈ 5–15 MB against a repo that is
  otherwise lean), license diligence × 3, and the large tier can't be
  vendored at all (20k files) — so the design degenerates into A+B hybrid
  anyway. Fixed-byte snapshots are deterministic, but committing large
  binary-ish trees churns git history forever.

### Option C — Tiered datasource (T1 synthetic pinned / T2 vendored real /
T3 manifest-fetched) with versioned baseline artifacts

- **Pros:** each tier answers the question it is uniquely good at — T1
  gives bit-reproducible regression detection in every CI run; T2 gives
  realistic shape for quality + agent-effort + the reference tables at a
  bounded ≤ 3 MB; T3 gives scale coverage without vendoring. Baseline
  directories version the whole substrate (`DS-v1`) so future comparisons
  name their reference exactly.
- **Cons:** three tiers to document and keep coherent; T3 runs are local/
  scheduled discipline rather than enforced; slightly more tooling
  (`--baseline` resolution, machine-profile stamping).

## 2. Recommendation: **Option C**

The deciding argument is the session's own evidence: the two failure modes
that motivated this phase — unattributable regressions (rolling cache) and
meaningless quality calibration (pathological corpus) — require *both*
determinism (T1) and realism (T2). A pinned-only or real-only design fixes
one failure mode and keeps the other. T3 exists because scale can't be
vendored, full stop; a manifest that pins url+commit is the honest answer
and costs almost nothing. The versioned-baseline directory ties all three
together with a name future PRs and releases can cite.

## 3. Sequencing (chosen path)

```
Week 1
  DS-1  manifest + T1 content-hash CI check          (locks determinism)
  DS-2  T2 snapshot selection + vendoring + NOTICE   (unblocks 3 and 5)
Week 2
  DS-3  ground-truth QA set + validator              (the quality substrate)
  DS-4  baseline artifacts + --baseline resolution + machine profiles
  → gate: DS-v1 directory complete; compare works end to end
Week 3
  DS-5  docs table generation + CI check against hand-edits
  DS-6  CI wiring (advisory vs DS-v1; retrieval eval on T2; T3 command)
  → gate: benchmarks.md has zero _fill_; a PR can cite "vs DS-v1"
```

Dependency notes: DS-3 and DS-5 require DS-2's snapshot; DS-4 requires
DS-1's versioning; DS-6 is last because it consumes everything. The
existing rolling-cache job stays exactly as it is — DS-6 *adds* the
pinned-baseline comparison line to its comment, it does not replace the
rolling one (both are useful: rolling catches runner drift too).

## 4. Verification commands (per milestone gate)

- DS-1: regenerate T1 from manifest in a clean checkout → identical hash
  (`cairn bench --suite perf` corpus stats match the manifest).
- DS-2: `uv run cairn build --workspace benchmarks/datasource/t2/<repo>`
  green + a query smoke (`find_definition` on a known symbol).
- DS-3: `uv run python scripts/verify_ground_truth.py` re-verifies every
  expectation against a fresh build (exit 0).
- DS-4: `uv run cairn bench --compare --baseline DS-v1` (and the agent /
  retrieval equivalents) — comparison renders with dataset-version header;
  deliberately mismatched machine profile shows the warning.
- DS-5: regeneration command reproduces the tables byte-identically; CI
  check fails on a hand-edited table.
- DS-6: advisory PR comment contains both "rolling" and "vs DS-v1" lines;
  retrieval eval posts a trend line.
