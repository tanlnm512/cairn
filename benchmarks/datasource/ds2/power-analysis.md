# DS-v2 sizing — Sakai topic-set-size power analysis

**Decision.** Target DS-v2 size = **150 L1 queries** (= `max(150 L1 floor,
n_required)` — the power analysis requires at most 54 evaluated queries to
certify the observed effect under the conservative input, 109 even at the
80%-power framing, so the D-010 floor of 150 governs and certifies with
margin) plus **≥ 40 L5 queries** (D-010 / FR-002 floor; never lowered). At
150 evaluated queries the minimum certifiable two-sided-95% effect is
**≈ +0.061 to +0.067** (matched vs conservative dispersion input), i.e. the
observed +0.1123 validates with ≈1.7× margin, while a half-effect +0.05
does **not** certify at 150 (it needs 220–269 evaluated queries at CI-
clearing, 450–549 at 80% power — beyond the authoring budget; recorded as
out of reach, not silently dropped). The 150 L1 target must keep all four
query kinds represented (callers / definition / flow / impact — the kinds
DS-v1's `queries.jsonl` already carries).

Schema: `cairn-ds2-power-analysis/1` (machine mirror:
`benchmarks/datasource/ds2/power-analysis.json`).

## Method

Named method: **Sakai-style topic-set-size design** — a two-sided paired
test at α = 0.05 in the **detectable-effect framing** (primary: choose n so
the 95% CI half-width shrinks to the effect size, the criterion the DS-v1
bootstrap guard actually failed), with the classic **80%-power paired
variant** recorded as a secondary robustness check. Closed-form
normal-approximation formulas (Sakai's paired-t Excel tools implement the
same detectable-effect logic via the noncentral t; the normal form is the
standard closed approximation and is stated explicitly here so every figure
below is recomputable with stdlib arithmetic):

```
h        = (ci_high - ci_low) / 2                 # CI half-width at n = 29
sigma_d  = h * sqrt(n) / z_975                    # per-query paired-difference SD
n_detect = ceil( (z_975 * sigma_d / delta)^2 )    # detectable-effect framing
n_pow80  = ceil( ((z_975 + z_80) * sigma_d / delta)^2 )   # 80% power variant
delta_min(N) = z_975 * sigma_d / sqrt(N)          # min certifiable effect at N
z_975 = 1.959964   z_80 = 0.841621
```

**Evaluation-unit mapping.** DS-v1's evidence unit is its 29-query validate
split (seeded 50/50 of 58 L1, D-006). DS-v2's declared protocol aggregates
the paired bootstrap over **pooled per-query paired differences with each
query validate-side exactly once** (the seeded ≥5-fold rotation design,
D-009: "the aggregate verdict is a legitimate n=all-queries test"), so a
required n in *evaluated queries* maps 1:1 onto the *L1 authoring total* —
this is what makes `max(150, n_required)` a like-for-like comparison. (Under
DS-v1's fixed-split convention the mapping would be 2:1; DS-v2 does not use
that convention for its accept gate.)

References: Sakai, "Topic Set Size Design", Information Retrieval Journal
2016 (+ tools); Smucker, Allan & Carterette, CIKM'07 (paired bootstrap is a
first-class test); Urbano et al., arXiv:1905.11096 (small query sets
systematically under-detect true effects — DS-v1's 29-query failure is the
expected behavior). All as curated in `specs/retrieval-quality-v2/research.md`
RQ4.

## Inputs (each committed, cited by path)

1. `benchmarks/quality/ablation.json` — the committed DS-v1 record
   (`cairn-quality-ablation/1`): `dataset.split.validate = 29`, and the five
   confirmation-ladder rows' `validate.bootstrap` blocks (paired bootstrap
   vs the incumbent's validate recall 0.2521, 10,000 resamples, 95% CI per
   `benchmarks/quality/ablation.md` Figure 2).
2. `benchmarks/quality/ablation.md` — Figure 2 (the same five Δ / CI rows)
   and the statement that at n = 29 every bootstrap CI straddles zero with
   widths ≈ ±0.13–0.15.
3. `specs/retrieval-quality-v2/research.md` RQ4 — the 1/√n CI-scaling
   arithmetic (±0.13–0.15 at n = 29 → ≈ ±0.07–0.08 at n ≈ 120) and the
   Sakai / Urbano sourcing above.
4. `specs/retrieval-quality-v2/tech-spec.md` D-010 (150/40 is the floor;
   target = max(150 L1, n_required)), D-006 (the 29/29 split convention the
   evidence was measured under), D-009 (the pooled per-query accept gate
   that defines the evaluation unit).
5. `specs/retrieval-quality-v2/spec.md` FR-002 and
   `specs/retrieval-quality-v2/test.md` TC-005 (the ≥150 L1 / ≥40 L5 floors
   with all four kinds represented).

**Per-query matrices are not committed.** The topic-by-run score matrices
Sakai's canonical workflow consumes exist only as session-local `/tmp`
artifacts (disclosed in `ablation.json` → `measurement.provenance`; absent
on this machine), so σ_d is **derived from the committed 95% CI half-widths
at n = 29** — the derivation input is itself committed evidence. No new
retrieval sweeps were run and no model inference performed: everything below
is arithmetic over the cited files.

## Arithmetic, step by step

Step 1 — half-widths and σ_d from the five committed CIs (n = 29):

| candidate (ablation.json row) | Δ | 95% CI | h = (hi−lo)/2 | σ_d = h·√29/z_975 |
|---|---|---|---|---|
| recipe-C_TRIM (= C_TRIM + rerank-on) | +0.0422 | [−0.1118, +0.1916] | 0.1517 | 0.416808 |
| enrich-on + rerank-off (B) | **+0.1123** | [−0.0240, +0.2514] | 0.1377 | **0.378342** |
| C_TRIM + rerank-off | −0.0563 | [−0.1839, +0.0575] | 0.1207 | 0.331633 |
| C_TRIM + enrich-on + rerank-on | +0.0376 | [−0.1149, +0.1893] | 0.1521 | 0.417907 |
| C_TRIM + enrich-on + rerank-off | +0.0911 | [−0.0480, +0.2356] | 0.1418 | 0.389607 |

The h column reproduces the record's "widths ≈ ±0.13–0.15" (range
0.1207–0.1521). Two σ_d inputs are carried forward: the **matched** estimate
from the best near-miss's own CI (0.378342 — the candidate DS-v2's first
re-test is expected to carry) and the **conservative** max over the five rows
(0.417907).

Step 2 — case (a), certify the observed Δ = +0.1123 at 95%:

- detectable-effect, matched σ: n = ⌈(1.959964 · 0.378342 / 0.1123)²⌉ = ⌈43.60⌉ = **44**
- detectable-effect, conservative σ: n = ⌈(1.959964 · 0.417907 / 0.1123)²⌉ = ⌈53.20⌉ = **54**
- 80% power, matched σ: n = ⌈(2.801585 · 0.378342 / 0.1123)²⌉ = ⌈89.09⌉ = **90**
- 80% power, conservative σ: n = ⌈(2.801585 · 0.417907 / 0.1123)²⌉ = ⌈108.69⌉ = **109**

All four ≤ 150, so the floor governs.

Step 3 — case (b), certify a conservative half-effect Δ = +0.05 at 95%:

- detectable-effect, matched σ: **220**; conservative σ: **269**
- 80% power, matched σ: **450**; conservative σ: **549**

All four exceed 150 (and the ~3–5× authoring envelope); recorded as beyond
budget with the consequence below.

Step 4 — what 150 buys: δ_min(150) = z_975·σ_d/√150 = **+0.060546**
(matched) / **+0.066878** (conservative). The observed +0.1123 is ≈1.7× the
minimum certifiable effect.

Step 5 — target: `max(150, n_required)` with the operative n_required = 54
(the conservative detectable-effect figure for the observed effect; the
decision is framing-invariant — even the 80%-power figure, 109, stays under
150) → **target = 150 L1 queries, ≥ 40 L5** (D-010 floor; FR-002).

## Cross-checks

- **1/√n route agrees with the σ route:** shrinking the best candidate's own
  half-width, ⌈29·(0.1377/0.1123)²⌉ = 44 = n_detect(matched) — the two
  derivations are algebraically identical and numerically agree.
- **RQ4 arithmetic reproduced:** h(120) = 0.1377·√(29/120) = **0.067693** ≈
  the "≈ ±0.07–0.08 at n ≈ 120" recorded in research.md RQ4 (derived here
  from the committed CI, not copied).
- **Why DS-v1 failed, quantified:** at n = 29, δ_min = z_975·σ_d/√29 ≈
  0.1377 (matched) — exactly the measured half-width; Urbano et al.'s
  small-set under-detection is the expected regime, not an anomaly.

## Recompute

`uv run python benchmarks/datasource/ds2/recompute_power.py` — stdlib-only;
re-derives every figure in this document from `power-analysis.json`'s
inputs, asserts exact agreement with the recorded outputs, and prints the
recorded n_required values.

## Constraints honored

Arithmetic over committed evidence only (no retrieval sweeps, no model
inference, nothing re-measured); every figure above either comes from a
cited file or is arithmetic over cited figures; DS-v1 artifacts untouched.
