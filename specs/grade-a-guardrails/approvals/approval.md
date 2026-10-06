# Approval freeze

**Approved-at**: `2e7a7e7`
**Algorithm**: sha256 over UTF-8 bytes

Recorded only after explicit user approval. `spec.md` is hashed without
its volatile Status line. `tech-spec.md` is verified as an immutable
prefix so implementation may append D-### decisions. `task.md` is not
frozen because it is the status holder.

## Frozen contract
- `spec.md`: exact sha256:0f2fc08d04c2a1392bdc2e593711c3a3cc1adadb7b16c74e43eeb5bb64b9efe5 length:8325
- `plan.md`: exact sha256:61aaf91df989b1c02ca039d59ff4dfcf5e096b4b2e9d9f1b434a0e7fae1aa6cb length:6830
- `survey.md`: exact sha256:bce9f41a64b12627de8cce661f33bab4d1faff454e65283d959c98d52d0f7472 length:12322
- `test.md`: exact sha256:51cf791592a2cf093c6aa7c0ee3a5822c815cec236306d87ed9226d7c391bce4 length:12047
- `tech-spec.md`: prefix sha256:7c4aba21c222912252a7c3f922a66fc3dd3927cdc78133a82e0af6ee6bfce670 length:20459

Any other change to these files requires a fresh user approval and a
new freeze record.
