# Pinned parity stores

Byte-exact store fixtures for `scripts/rust_migration/parity.py`. One
directory per `--store` (`self`, `yarl`, `attrs`), each under the 25 MB
check-in cap:

- `store.kg` — the full store the goldens were captured over (subset-cap
  corpus build + hash embed + seeded bundle, DELETE journal, sidecar-free).
- `corpus/` — the exact workspace subset the store was built from (served as
  `CAIRN_WORKSPACE`); `cap_files` in `manifest.json` records the subset cap.
- `bundle/` — the knowledge/memory bundle snapshot served as
  `CAIRN_KNOWLEDGE`; replay never reads the live bundle.
- `manifest.json` — cap, file counts, store counts, env pins.

`parity.py --record` rebuilds a store in scratch, checks these bytes in, and
captures the goldens over them; replay copies the same bytes to scratch for
both sides and reads every tool's arguments from the fixture `replay` blocks.
Each fixture's `replay_store` / `replay_workspace` keys point readers at the
serving artifacts.
