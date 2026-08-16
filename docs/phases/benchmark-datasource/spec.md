# Phase: Benchmark Datasource — pinned baselines for future comparison

- **Status:** planned (spec expressed from the 2026-08-16 post-0.11.0 review)
- **Date drafted:** 2026-08-16
- **Code state baseline:** 0.11.0 @ `58c2a66` (main)
- **Companion docs:** [plan.md](plan.md) · [task.md](task.md)

## 1. Motivation — why "compare in the future" needs a datasource, not more runs

Cairn measures constantly but has no *durable comparison substrate*. Four
pieces of evidence from the performance phase and release cycle:

1. **Baselines are run-local and rolling.** `cairn bench --save/--compare`
   writes JSON wherever the user points; the CI bench job
   (`.github/workflows/ci.yml` `bench:` "advisory baseline comparison")
   restores a **rolling actions/cache** entry refreshed by default-branch
   runs — nothing is pinned, versioned, or reproducible after the cache
   rotates. A regression found today against last week's cache is not
   attributable to a cairn change vs a runner change.
2. **The synthetic corpus is deterministic but not representative.**
   `bench/corpus.py` (seeded, `DEFAULT_SEED = 0xC0DE`) is CI-safe but
   pathological: dense random call graphs (closure build 340 s at 1,000
   files — real repos are far shallower) and templated docstrings that
   made every chunk a near-identical twin, which is why the rerank-gate
   calibration had to fall back to a copy of cairn's own `src/`
   (2026-08-16 finding, recorded in tribal memory).
3. **Reference tables are still placeholders.** `docs/benchmarks.md` has
   three unfilled families — retrieval quality (L1/L5 recall/MRR,
   `_fill_` at the eval tables), the perf query table, and the scaling
   table — with no committed data behind them. Every future "did we get
   slower / worse" question currently requires re-deriving methodology.
4. **Quality drift is invisible.** `src/cairn/eval.py` (recall@10 / MRR
   harness for L1 code and L5 knowledge retrieval) exists, but its ground
   truth is not a maintained dataset — so a retrieval regression ships as
   easily as a timing regression, with nothing to compare against.

What competitors take for granted (codegraph's 7-repo Claude benchmark with
medians and a control arm) is a **dataset + recorded baselines + methodology**.
That is what this phase builds for cairn.

## 2. Goals

A **versioned benchmark datasource** under repo control, plus
**committed baseline artifacts** stamped with dataset version, cairn version,
and machine profile — so any future comparison (release-to-release, PR
advisory, competitor, or "why did doctor numbers change") resolves to:
same dataset version? same machine class? which cairn version? — and gets a
definite answer.

### The tiered datasource

| Tier | Content | Determinism | Where used |
|------|---------|-------------|-----------|
| **T1 synthetic** | today's `bench/corpus.py` outputs, pinned by a **manifest** (seed, size, complexity, generator version tag) | fully deterministic (regeneration hash-checked in CI) | every CI bench run; timing regressions |
| **T2 real-code snapshot** | a vendored, small (target ≤ 3 MB) real multi-language repo snapshot committed to the repo, with LICENSE + provenance manifest; plus a **hand-verified ground-truth QA set** (~50 code queries with known-symbol answers + ~20 knowledge queries) | fixed bytes = deterministic; realistic shapes (real fan-in/fan-out, real docstrings) | retrieval-quality evals (L1/L5), agent-effort suite, reference perf table |
| **T3 fetch-by-manifest** | manifests pinning external repos (url + commit) at 2–3 scale points (e.g. ~2k, ~20k files) | reproducible by pin, not vendored | scheduled/local scale runs only (no CI network) |

### Baseline artifacts

- In-repo directory `benchmarks/baselines/<dataset-version>/<suite>.json`
  (perf, scaling, agent, retrieval-quality), each stamped with: dataset
  version, cairn version, machine profile (arch, cpu count, runner class
  "reference-local" vs "ci-ubuntu-latest"), timestamp.
- `cairn bench --compare` gains baseline-set resolution: `--baseline
  <dataset-version>` resolves the committed artifact; mismatched machine
  profiles produce a loud warning header on the comparison (numbers still
  shown, attributed).
- Reference tables in `docs/benchmarks.md` (including the three `_fill_`
  families) are generated from committed T2/T3 baselines — never hand-typed.

### Ground-truth QA set (the part that makes quality comparable)

- Committed with T2: query → expected symbols (L1) / expected concepts
  (L5), hand-verified once at dataset creation, each entry traceable to a
  file in the snapshot.
- `eval.py` runs against it in CI (advisory at first, promotable to a gate
  once variance is known) — giving recall/MRR *trend lines* across
  releases, the analogue of the timing trend the bench cache gives today.

## 3. Items and "Done when"

Each item appears verbatim in [plan.md](plan.md) and [task.md](task.md).

### DS-1 — Datasource manifest + T1 pinning

- **Done when**: a `benchmarks/datasource/manifest.json` versions the T1
  generator (seed, sizes, complexity, generator git-sha) and a CI check
  regenerates T1 and asserts a content hash, so any corpus drift is a
  deliberate, versioned act.

### DS-2 — T2 real-code snapshot vendored

- **Done when**: a real multi-language repo snapshot (≤ 3 MB, permissive
  license, provenance manifest naming upstream repo + commit + license)
  lives under `benchmarks/datasource/t2/`, is exercised by a build+query
  smoke test, and its license/attribution is recorded in `NOTICE`.

### DS-3 — Ground-truth QA set

- **Done when**: ≥ 50 L1 queries (definition/callers/impact/flow with
  hand-verified expected symbols) and ≥ 20 L5 queries are committed under
  `benchmarks/datasource/t2/ground_truth/` with a schema `eval.py`
  consumes, and a validation script re-verifies every expectation against
  a freshly built graph (no stale answers).

### DS-4 — Baseline artifacts + compare resolution

- **Done when**: `benchmarks/baselines/DS-v1/` holds perf/scaling/agent/
  retrieval JSON artifacts stamped with dataset version, cairn version,
  and machine profile; `cairn bench --compare --baseline DS-v1` resolves
  them; machine-profile mismatch emits the warning header.

### DS-5 — Reference tables generated

- **Done when**: the three `_fill_` families in `docs/benchmarks.md`
  (retrieval quality, perf, scaling) are generated from the committed
  baselines by a documented one-command regeneration, and hand-editing
  those tables is no longer possible without breaking a CI check.

### DS-6 — CI wiring

- **Done when**: the rolling-cache advisory job additionally reports
  against the committed DS-v1 baseline (labeled by dataset version), the
  retrieval-quality eval runs on T2 in CI (advisory), and T3 manifests +
  a documented local command cover the scale tier.

## 4. Scope

**In scope:** the datasource tree, manifests, ground truth, baseline
artifacts, compare/bench wiring, docs generation, CI changes.

**Out of scope:**
- Gating CI on timing (stays advisory by design — the observability plan's
  "advisory first, gate later if stable" doctrine).
- Live LLM-in-the-loop benchmarks (the agent-effort harness stays
  scripted; an LLM arm is a future decision).
- Fetching T3 content in CI (network-free CI is a hard constraint).
- New benchmark metrics — this phase pins *what exists*; new suites adopt
  the datasource as they land.

## 5. Risks

| Risk | Mitigation |
|------|------------|
| Repo bloat (T2 snapshot + baselines) | ≤ 3 MB snapshot target; baselines are JSON (KBs); size check in CI |
| License/attribution errors in vendored code | permissive-license filter at selection; NOTICE entry; provenance manifest required before merge |
| Ground truth rots as cairn's resolution improves | DS-3's validator re-verifies against a fresh build; expectations record *why* (symbol identity, not incidental rank) |
| Baseline comparisons across machine classes mislead | machine-profile stamping + loud warning; reference-local baselines labeled as such |
| Dataset becomes a maintenance tax | version-bump policy: new dataset version = new directory, old baselines stay queryable; deprecate whole versions, never edit |
