# Specs index

All three specs completed and archived 2026-08-18 to `archive/`, after the
post-v0.12.0 doc re-audit at `8dbf2ca` (every FR verified DONE with verify
commands re-run green — see each archived `survey.md`). New specs scaffold
into `specs/<name>/` as usual (`scripts/scaffold.sh` registers them here).

## Archive

- [benchmark-datasource](archive/benchmark-datasource/spec.md) — done (20/20 tasks, merged via #35 2026-08-16)
- [retrieval-quality](archive/retrieval-quality/spec.md) — done (24/24 tasks, merged via #37 2026-08-16; SC-1 shortfall documented in benchmarks/quality/ablation.md)
- [retrieval-quality-v2](archive/retrieval-quality-v2/spec.md) — done (24/24 tasks 2026-08-17; k-fold + DS-v2 evidence base; 3 candidates cleared the DS-v1 guard, all refuted zero-shot transfer; document branch — no ship, verdict in benchmarks/quality/ablation.md)

Note: sealed benchmark artifacts (`benchmarks/quality/ablation.json`,
`benchmarks/datasource/ds2/*`) retain their historical `specs/<name>/...`
provenance citations — those records are immutable (blob-pinned or sealed);
resolve such paths against `archive/<name>/` or the relevant merge-commit
tree.
