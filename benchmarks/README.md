# Benchmarks artifact inventory

← [Docs index](../docs/README.md)

Inventory of every committed JSON artifact under `benchmarks/` and the
human-readable companion that names each — read this when you want the rendered
explanation of a benchmark result instead of parsing raw JSON. Rows key on
repo-relative path, never basename: `quality.json` exists under both
`baselines/DS-v1/` and `baselines/DS-v1.1/`, and basename keying over-merges.
A companion is either the campaign's sibling README/FIGURES/SIZE/MEASURE/ablation
doc or a generated reference table in `docs/benchmarks.md`.

| Artifact | Named by |
|----------|----------|
| `benchmarks/baselines/DS-v1.1/quality.json` | `benchmarks/baselines/DS-v1.1/README.md`, `benchmarks/baselines/DS-v1/README.md`, `benchmarks/quality/ablation.md`, `docs/benchmarks.md` |
| `benchmarks/baselines/DS-v1/agent.json` | `benchmarks/baselines/DS-v1/README.md`, `benchmarks/baselines/DS-v1.1/README.md` |
| `benchmarks/baselines/DS-v1/perf.json` | `benchmarks/baselines/DS-v1/README.md`, `benchmarks/baselines/DS-v1.1/README.md` |
| `benchmarks/baselines/DS-v1/quality.json` | `benchmarks/baselines/DS-v1/README.md`, `benchmarks/baselines/DS-v1.1/README.md`, `benchmarks/quality/ablation.md`, `docs/benchmarks.md` |
| `benchmarks/baselines/DS-v1/scaling.json` | `benchmarks/baselines/DS-v1/README.md`, `benchmarks/baselines/DS-v1.1/README.md` |
| `benchmarks/datasource/ds2/ground_truth/VERIFICATION.json` | `benchmarks/datasource/ds2/ground_truth/VERIFICATION.md`, `benchmarks/datasource/ds2/ground_truth/AUTHORING.md` |
| `benchmarks/datasource/ds2/ground_truth/manifest.json` | `benchmarks/datasource/ds2/ground_truth/VERIFICATION.md`, `benchmarks/datasource/ds2/ground_truth/AUTHORING.md`, `docs/benchmarks.md` |
| `benchmarks/datasource/ds2/power-analysis.json` | `benchmarks/datasource/ds2/power-analysis.md` |
| `benchmarks/datasource/ds2/second-corpus/attrs-26.1.0/provenance.json` | `benchmarks/datasource/ds2/second-corpus/DECISION.md` |
| `benchmarks/datasource/manifest.json` | `benchmarks/datasource/ds2/ground_truth/AUTHORING.md`, `benchmarks/datasource/ds2/ground_truth/VERIFICATION.md`, `docs/benchmarks.md` |
| `benchmarks/datasource/t2/provenance.json` | `benchmarks/datasource/ds2/second-corpus/DECISION.md` |
| `benchmarks/quality/ablation.json` | `benchmarks/quality/MEASURE.md`, `benchmarks/quality/ablation.md`, `benchmarks/datasource/ds2/power-analysis.md`, `benchmarks/datasource/ds2/ground_truth/VERIFICATION.md`, `benchmarks/quality/fr003-calibration/README.md` |
| `benchmarks/quality/fr003-calibration/analysis.json` | `benchmarks/quality/fr003-calibration/README.md`, `benchmarks/datasource/ds2/power-analysis.md` |
| `benchmarks/quality/fr003-calibration/d03-diagnostic.json` | `benchmarks/quality/fr003-calibration/README.md`, `benchmarks/quality/ablation.md` |
| `benchmarks/quality/fr003-calibration/p95-remeasure.json` | `benchmarks/quality/fr003-calibration/README.md`, `benchmarks/quality/ablation.md` |
| `benchmarks/quality/fr003-calibration/rows-fr003.json` | `benchmarks/quality/fr003-calibration/README.md` |
| `benchmarks/quality/fr003-calibration/sweep-baseline.json` | `benchmarks/quality/fr003-calibration/README.md` |
| `benchmarks/quality/fr003-calibration/sweep-df0.75.json` | `benchmarks/quality/fr003-calibration/README.md` (brace-glob `sweep-df0.{75,80,85,90}.json`) |
| `benchmarks/quality/fr003-calibration/sweep-df0.80.json` | `benchmarks/quality/fr003-calibration/README.md` (brace-glob `sweep-df0.{75,80,85,90}.json`) |
| `benchmarks/quality/fr003-calibration/sweep-df0.85.json` | `benchmarks/quality/fr003-calibration/README.md` (brace-glob `sweep-df0.{75,80,85,90}.json`) |
| `benchmarks/quality/fr003-calibration/sweep-df0.90.json` | `benchmarks/quality/fr003-calibration/README.md` (brace-glob `sweep-df0.{75,80,85,90}.json`) |
| `benchmarks/quality/fr004-prf/rows-fr004.json` | `benchmarks/quality/fr004-prf/FIGURES.md` (gap fill — `## Artifacts` table) |
| `benchmarks/quality/fr004-prf/sweep-prf-docs10.json` | `benchmarks/quality/MEASURE.md` |
| `benchmarks/quality/fr004-prf/sweep-prf-docs3.json` | `benchmarks/quality/MEASURE.md` |
| `benchmarks/quality/fr005-mv/sweep-mv.json` | `benchmarks/quality/MEASURE.md` (sibling dir doc: `benchmarks/quality/fr005-mv/SIZE.md`) |
| `benchmarks/quality/ladder-v2/rows-ds2.json` | `benchmarks/quality/ladder-v2/FIGURES.md` (gap fill — `## Artifacts` table) |
| `benchmarks/quality/ladder-v2/sweep-ds2-zeroshot.json` | `benchmarks/quality/ladder-v2/FIGURES.md` (gap fill — `## Artifacts` table) |
| `benchmarks/quality/ladder-v2/sweep-ladder-enrich-rerankoff.json` | `benchmarks/quality/MEASURE.md` |
| `benchmarks/quality/ladder-v2/sweep-ladder-enrichidf-rerankoff.json` | `benchmarks/quality/MEASURE.md` |
| `benchmarks/quality/warm_time.json` | `docs/benchmarks.md` (Quick reference row) |

## Keeping this inventory true

When a new JSON lands under `benchmarks/`, add its row above and give it a
companion. The drift detector is the gap loop, run from the repo root:

```sh
for j in $(find benchmarks -name '*.json' | sort); do grep -rl --include='*.md' -F "$(basename $j)" benchmarks docs > /dev/null || echo "GAP: $j"; done
```

A printed line is either an artifact no `.md` names yet (add a row and a
companion) or a brace-glob naming like the four `sweep-df0.*.json` rows above
(expand it to individual rows here, as this inventory does).
