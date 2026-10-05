# Approval freeze

**Approved-at**: `0ae5a87`
**Algorithm**: sha256 over UTF-8 bytes

Recorded only after explicit user approval. `spec.md` is hashed without
its volatile Status line. `tech-spec.md` is verified as an immutable
prefix so implementation may append D-### decisions. `task.md` is not
frozen because it is the status holder.

## Frozen contract
- `spec.md`: exact sha256:97c42cd566fa2a1406a5caf0874298ed052891d43e30a54308a011c6e82aec18 length:4959
- `plan.md`: exact sha256:b147fb1063da4dde00a5cebdefeb47c9a51ba548dbf5636f0e1e7386b94389fe length:4337
- `survey.md`: exact sha256:259bede5fa00c32e33cfac0db1711b8cc931db7340c9c88fe41098952c475261 length:3864
- `test.md`: exact sha256:cdd11966bb4a5c8929d8f8b565e5e228f2115ac4c49d85eba5dd381df48cf395 length:17075
- `tech-spec.md`: prefix sha256:1166c68583775c6d6dde3dc3e359195accd581387d31063b443aec5e6010540f length:19838

Any other change to these files requires a fresh user approval and a
new freeze record.
