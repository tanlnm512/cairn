# CLI golden fixtures (C-4)

Frozen fixtures: the spec of record for `scripts/rust_migration/cli_golden.py`.
The Rust binary must keep matching them.

- `cases.json` — one entry per command in the harness's fixed case list, in
  order: the scrubbed argv (`label`), exit code, and normalized stdout/stderr
  as captured from the last Python CLI over the seeded pinned `self` store.
  Paths, timestamps, durations and store keys are already scrubbed
  (`normalization` names the rule set).
- `seeds/` — the OKF concepts the harness seeds into the scratch bundle
  before each leg (tasks, wiki pages, memories, workflow, compass), one file
  per concept id. `_tasks/*.result.md` are the frozen task results.

Replay (the gate): `uv run --no-sync python scripts/rust_migration/cli_golden.py`
rebuilds the release binary and diffs every case; `--rust-bin` points at a
prebuilt binary.

Refresh (intentional divergence only, with a recorded ruling):
`cli_golden.py --record --only <label-prefix>` re-captures just the affected
cases from the Rust binary and stamps them under `capture.refreshed`; a bare
`--record` re-freezes every case (`--reference "<cmd>"` records another
executable). A changed case list fails replay loudly until re-recorded.
