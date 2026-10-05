# Approval freeze

**Approved-at**: `9a43103`
**Algorithm**: sha256 over UTF-8 bytes

Recorded only after explicit user approval. `spec.md` is hashed without
its volatile Status line. `tech-spec.md` is verified as an immutable
prefix so implementation may append D-### decisions. `task.md` is not
frozen because it is the status holder.

## Frozen contract
- `spec.md`: exact sha256:97c42cd566fa2a1406a5caf0874298ed052891d43e30a54308a011c6e82aec18 length:4959
- `plan.md`: exact sha256:b147fb1063da4dde00a5cebdefeb47c9a51ba548dbf5636f0e1e7386b94389fe length:4337
- `survey.md`: exact sha256:259bede5fa00c32e33cfac0db1711b8cc931db7340c9c88fe41098952c475261 length:3864
- `test.md`: exact sha256:ffaa34328c8a06f105428d3552a0c621b69cf17af7977d4483ba24073d5757c8 length:14841
- `tech-spec.md`: prefix sha256:158c522e768041390816d4047b4ed7fec1906059752512d51bb0cc8c5c179f76 length:19362

Any other change to these files requires a fresh user approval and a
new freeze record.
