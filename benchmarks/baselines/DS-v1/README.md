# DS-v1 bench baselines

Committed baseline artifacts for the `benchmark-datasource` dataset version
`DS-v1` (spec FR-004, task T015; tech-spec D-001 self-describing JSONs).
Consumed by `cairn bench --baseline DS-v1` (T014), which resolves
`benchmarks/baselines/DS-v1/<suite>.json` per suite.

## Artifacts

| file | suite | source |
|------|-------|--------|
| `perf.json` | perf | `uv run cairn bench --suite perf --json` |
| `scaling.json` | scaling | `uv run cairn bench --suite scaling --json` (sizes 100, 500, 1000, 5000 — minutes-scale; the closure builds dominate) |
| `agent.json` | agent | `uv run cairn bench --suite agent --json` (additive fourth artifact: the task names three files, but T014's `--baseline` compare surface includes the agent suite) |
| `quality.json` | quality | `scripts/mint_baselines.py` quality path — see below |

Each artifact carries, at top level:

- `"schema": "cairn-bench-baseline/1"` — the D-001 self-describing tag;
- the T013 stamp: `dataset` (name/version/tree_hash/identity_size),
  `cairn_version`, `machine_profile` (arch/cpu/cpu_count/os/runner_class);
- `timestamp` and the suite payload keys, verbatim from the CLI / harness.

## How they were minted (verbatim commands)

One command mints all four, on the reference machine only:

```
uv run python scripts/mint_baselines.py --version DS-v1
```

The script is a thin wrapper so the artifacts and this README cannot drift;
per suite it runs exactly:

```
uv run cairn bench --suite perf --json
uv run cairn bench --suite scaling --json
uv run cairn bench --suite agent --json
```

and adds only the additive `"schema"` tag on top of the CLI's own stamped
payload. `quality.json` is minted in-process (it is not a CLI suite):

1. copy `benchmarks/datasource/t2/yarl` to a throwaway workspace and put
   the empty `.git` scanner marker on the copy (same idiom as
   `scripts/verify_ground_truth.py` — the committed tree stays marker-free);
2. `build_graph` a fresh graph over the copy;
3. `embed_all` with the **local** embedding backend (`BAAI/bge-m3`;
   `CAIRN_EMBED_BACKEND` left unset at its `local` default and asserted to
   be the *effective* backend — quality numbers over hash vectors would be
   meaningless token overlap, not semantics);
4. `run_evaluation(conn, bundle_root=None,
   queries_path=benchmarks/datasource/t2/ground_truth)` — the graded
   loader + identity-first matcher (D-008);
5. stamp with `build_artifact_stamp()` + the same schema tag.

Connections go through `cairn.graph.schema.get_db` (Row factory), the same
opener `cairn eval` uses — `semantic_search` reads scan rows by column name.

### quality.json L5 note

No OKF knowledge bundle exists for the t2 snapshot (see
`scripts/verify_ground_truth.py`), so the L5 retrieval surface is empty and
**L5 scores 0.0 by construction** — recorded in the artifact's `l5_surface`
field as "surface absent", not "retrieval failed". An L5 baseline becomes
possible when a knowledge bundle for the snapshot exists.

## Machine context

Minted on the maintainer's machine (`machine_profile.runner_class =
"reference-local"`, i.e. outside GitHub Actions — D-005). Full profile
facts are in every artifact's `machine_profile` block. Timing comparisons
from any other machine WARN and stay advisory; they never gate (D-005:
warn, never normalize).

## Immutability (D-010)

This directory is **immutable once committed**. Never edit an artifact.
Corrections, re-measurements, or dataset changes ship as a NEW directory —
`benchmarks/baselines/DS-v2/`, `DS-v3/`, ... — minted the same way with
`--version DS-v2`. `scripts/mint_baselines.py` refuses to overwrite
existing artifacts unless `--force` is passed, and `--force` is for
pre-commit re-mints on the minter's own machine only.
