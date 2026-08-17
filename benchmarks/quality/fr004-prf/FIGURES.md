# FR-004 PRF figures (T020) — DS-v1 5-fold, D-002 grid

Protocol (D-009): torch threads 1, local bge-m3, brute-force cosine, rerank under the CAIRN_RERANK=1 marker (flat pairs, gate 0.45); 5-fold seeded rotation (fold_seed 24301) over the 58 L1 queries; single-lever combos (prf only), the implicit all-levers-off row is the integrity baseline.

Cells are recall@10/MRR. `*` in-sweep p95 — measured in the serial run on an otherwise-quiet reference machine (MEASURE.md; the D-015 parallel-wave plan was dropped before any run).

## Integrity gate

Pooled all-levers-off 0.4174/0.2862 vs committed 0.4174/0.2862 — within band True (±0.002 recall / ±0.006 MRR); tune/validate anchors match the committed Figure 1/2 figures to 4 decimals; the two runs' implicit rows are byte-identical cross-run (True).

## Headline

Both grid points are negative and not significant — PRF stays flag-off. p95 (99.4 / 81.1 ms) sits far inside the rerank budget it would replace (1142.0 ms on vs 28.9 ms off, committed session figures), so the AC5 latency half holds while the quality half fails.

### prf@docs=3,terms=10,lambda=0.5

| | pooled (n=58) | tune (n=29) | validate (n=29) |
|---|---|---|---|
| candidate | 0.3728/0.1752 | 0.5484/0.2642 (p95 99.4 ms*) | 0.1972/0.0862 (p95 99.3 ms*) |
| incumbent (all-levers-off) | 0.4174/0.2862 | 0.5828/0.4444 (p95 1040.6 ms*) | 0.2521/0.1279 (p95 1057.6 ms*) |

- Pooled paired bootstrap vs incumbent (D-009): **Δ = -0.0447**, p = 0.3025, 95% CI [-0.1315, +0.0402] (t-test cross-check p = 0.3127) — NOT significant.

### prf@docs=10,terms=10,lambda=0.5

| | pooled (n=58) | tune (n=29) | validate (n=29) |
|---|---|---|---|
| candidate | 0.3624/0.1568 | 0.5713/0.2368 (p95 77.7 ms*) | 0.1535/0.0768 (p95 85.1 ms*) |
| incumbent (all-levers-off) | 0.4174/0.2862 | 0.5828/0.4444 (p95 1040.6 ms*) | 0.2521/0.1279 (p95 1057.6 ms*) |

- Pooled paired bootstrap vs incumbent (D-009): **Δ = -0.0550**, p = 0.1857, 95% CI [-0.1375, +0.0250] (t-test cross-check p = 0.1958) — NOT significant.

## docs=10 vs docs=3 head-to-head

Δ = -0.0103, p = 0.6579 — not significant; the grid gives no reason to prefer either point.
