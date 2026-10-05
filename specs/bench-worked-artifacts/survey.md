# Survey: bench-worked-artifacts

**Created**: 2026-10-05 | **Baseline**: 0.21.2 @ 0ccce8c
The survey node's output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item S1: "No --worked flag exists on cairn bench"
  evidence:   src/cairn/cli/bench.py:425-435 option list (json :430, save :431, compare :433) — no worked
  status:     TODO
  verify:     cairn bench --help
  gap:        add --worked DIR + bundle writer + inventory append

item S2: "The report-save seam is --save writing one JSON file — the raw-artifact half already exists"
  evidence:   src/cairn/cli/bench.py:663 `Path(save).write_text(json.dumps(payload, indent=2), encoding="utf-8")`; :431 `@click.option("--save", default=None, help="Save the result JSON to this file (baseline).")`
  status:     DONE
  verify:     grep -n "Path(save).write_text" src/cairn/cli/bench.py
  gap:        worked bundle = same payload + manifest + README + fixed layout + inventory row

item S3: "Machine/suite stamping is centralized in _stamp_and_emit — the manifest extends it"
  evidence:   src/cairn/cli/bench.py:307 `def _stamp_and_emit(report, stamp: dict, as_json: bool) -> dict:`
  status:     DONE
  verify:     grep -n "def _stamp_and_emit" src/cairn/cli/bench.py
  gap:        manifest needs argv + seed/repeats/runs surfaced next to the stamp

item S4: "benchmarks/README.md mandates the inventory convention this spec appends to"
  evidence:   benchmarks/README.md:5 "Inventory of every committed JSON artifact under `benchmarks/` and the" (rows key repo-relative, never basename — :8-9)
  status:     DONE
  verify:     sed -n '1,12p' benchmarks/README.md
  gap:        worked/ section + per-artifact rows appended by the command

item S5: "Committed baseline layout is benchmarks/baselines/DS-v*/; worked/ is new"
  evidence:   `ls benchmarks/` → README.md baselines datasource quality (no worked/); baselines/ holds DS-v1, DS-v1.1, ...
  status:     DONE
  verify:     ls benchmarks benchmarks/baselines
  gap:        create the benchmarks/worked/ suite-datasetversion layout on first run

item S6: "Minting precedent: scripts/mint_baselines.py is the existing producer script shape"
  evidence:   `ls scripts/` → mint_baselines.py
  status:     DONE
  verify:     python3 scripts/mint_baselines.py --help   (or head -20)
  gap:        worked bundles are minted by the bench command itself; no new script required

item S7: "Bench tests are infra-marked and determinism-only — new tests follow"
  evidence:   pyproject.toml:207-208 "The infra tier is CI selection, NOT a quarantine: every marked test still runs and must pass on each main push"
  status:     DONE
  verify:     grep -n "infra" pyproject.toml | head -2
  gap:        worked-bundle tests: structure + idempotence asserts only, no wall-clock asserts
```

## Supporting evidence

- Suite inventory the flag must cover: cli/bench.py `--suite perf|scaling|agent|swe-bench` (suite dispatch near :397-440) — the writer is suite-agnostic (one payload, one manifest).
- README companion precedent: benchmarks/quality/ campaigns carry sibling MEASURE/FIGURES docs naming artifacts (benchmarks/README.md rows) — the worked README follows the same "companion names the artifact" rule.
- Machine-profile drift convention: cross-machine mismatch is "warned, not normalized" in bench compare (cli/bench.py:349 area) — the manifest records the stamp but TCs never assert on it.

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
