# Pinned parity stores

Byte-exact store fixtures for `scripts/rust_migration/parity.py`. One
directory per `--store` (`self`, `yarl`, `attrs`), each under the 25 MB
check-in cap:

- `store.kg` — the full store the goldens were captured over (subset-cap
  corpus build + hash embed + seeded bundle, DELETE journal, sidecar-free).
- `corpus/` — the exact workspace subset the store was built from (served as
  `CAIRN_WORKSPACE`); `cap_files` in `manifest.json` records the subset cap.
  Git cannot track a `.git` path, so the corpus ships without its repo
  marker — build harnesses stage a copy with a `.git` marker file restored.
- `bundle/` — the knowledge/memory bundle snapshot served as
  `CAIRN_KNOWLEDGE`; replay never reads the live bundle.
- `manifest.json` — cap, file counts, store counts, env pins.

Frozen: the bytes are the spec of record and no recorder exists anymore.
`parity.py` replay copies the checked-in bytes to scratch, serves them to the
rust daemon, and reads every tool's arguments from the fixture `replay`
blocks. Each fixture's `replay_store` / `replay_workspace` keys point readers
at the serving artifacts.
