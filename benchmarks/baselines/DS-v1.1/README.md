# DS-v1.1 bench baselines — refreshed quality measurement context

Quality-only companion to `benchmarks/baselines/DS-v1/` (task T023, FR-007).
**DS-v1 stays the immutable BEFORE and remains byte-identical** (D-010); this
directory is the fresh re-measurement of the retrieval-quality surface at the
shipped config, minted after the T019 confirmation sweep confirmed a no-ship
(every candidate's bootstrap interval included zero).

## Version semantics

The `DS-*` version in `dataset.version` refers to the **ground truth**, which
is unchanged: both baselines stamp `DS-v1`
(tree_hash `65e3df39…f87bb`, 82 queries / 234 expectations, authoring task
T011). The **baseline directory** version tracks measurement context:
`DS-v1.0` (the DS-v1 directory) was minted 2026-08-16T07:10 UTC before the
sweep work; `DS-v1.1` was minted 2026-08-16T16:31 UTC after it, at the same
shipped config, carrying the measurement-state context DS-v1.0 lacked.

## Artifact

| file | suite | source |
|------|-------|--------|
| `quality.json` | quality | `uv run python scripts/mint_baselines.py --version DS-v1.1 --suites quality` |

Quality only, deliberately. `perf.json` / `agent.json` / `scaling.json`
comparisons still target **DS-v1**: their numbers were verified in place by
T020/T021 (`--baseline DS-v1`, threshold 0.15), and timing artifacts are
machine-noisy single-shot wall-times — re-minting them buys noise, not
information. Quality is the deterministic-ish surface worth refreshing: same
graph build (24 files / 1066 symbols / 2432 edges, 0 parse errors), same local
`BAAI/bge-m3` embeddings (1066 embedded / 0 skipped), same graded evaluation.

## Numbers vs DS-v1

| corpus | samples | recall@10 | mrr |
|--------|---------|-----------|-----|
| L1 | 58 | 0.4174 | 0.2862 |
| L5 † | 24 | 0.0000 | 0.0000 |

Identical to DS-v1 at the artifact's 4-decimal resolution — expected, since
T019 shipped no config change (the incumbent B / enrich-OFF / rerank-auto /
flat pairs / gate 0.45 is what DS-v1 was minted under). D-009 documents the
mint-time noise band (±0.002 recall / ±0.006 MRR): the deterministic
thread-pinned measurement reads 0.4195 / 0.2925, and this re-mint landed
exactly on the artifact figures in this session (as did T019's closing
fullset check: "before==after 0.4174/0.2862").

† L5 surface absent for DS-v1: no OKF knowledge bundle exists for the t2
snapshot — L5 scores are 0.0 by construction, not retrieval failures.

**Improvement margins (TC-024):** the shipped config is unchanged, so the
margin vs the BEFORE is zero by construction — "unchanged within measurement
band" is the honest statement, with the near-miss evidence (tune leader
0.6989 collapsing to +0.038 on validate; closest alternative +0.1123 at
p=0.118, below the 95% bootstrap bar) deferred to the ablation record
(T024, `benchmarks/quality/ablation.md`) alongside the SC-1 shortfall
documentation (targets recall@10 ≥ 0.50 / MRR ≥ 0.33).

## What this directory adds over DS-v1.0's artifact

- `retrieval` block (D-009 consequence — "future quality mints record their
  rerank/threading state so their artifacts are reproducible by
  construction"): effective rerank state (`enabled: true` via the persistent
  auto-enable marker — the shipped rerank-auto config, `CAIRN_RERANK` unset;
  model `BAAI/bge-reranker-base`, weights cached), `torch_num_threads: 8`,
  and the retrieval-relevant env (all unset — defaults).
- A fresh mint date for provenance ("when was this last actually measured"),
  alongside the T013 stamp fields DS-v1.0 already carried.

Measurement context: the mint's fresh graph DB has no vec0 ANN index, so
semantic search used the brute-force cosine scan — identical to DS-v1.0's
mint path (same script, fresh DB both times), so the comparison is
apples-to-apples; results are order-identical, only scan speed differs.

## Which baseline does what compare against?

- **Quality** — this directory is the current comparison target: future
  quality runs compare against `benchmarks/baselines/DS-v1.1/quality.json`
  so they don't warn against a stale mint.
- **Perf / scaling / agent** — still `DS-v1` (T020/T021-verified;
  `--baseline DS-v1`).
- **docs/benchmarks.md tables** — still generated from `DS-v1`. The
  generator renders all three families from one directory and its provenance
  line builds the source path from `dataset.version` (the ground-truth
  identity, `DS-v1`), so pointing it at this directory would emit a
  misleading path; with the numbers identical at rendered resolution, the
  DS-v1-sourced table is also this directory's numbers. T024's ablation
  record carries the fresh-mint context.

## Immutability (D-010)

Same rule as DS-v1: this directory is immutable once committed. Corrections
or re-measurements ship as a NEW version directory. The mint script refuses
to overwrite existing artifacts without `--force` (pre-commit re-mints on the
minter's machine only).
