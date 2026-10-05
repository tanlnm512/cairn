# Approval freeze

**Approved-at**: `9a43103`
**Algorithm**: sha256 over UTF-8 bytes

Recorded only after explicit user approval. `spec.md` is hashed without
its volatile Status line. `tech-spec.md` is verified as an immutable
prefix so implementation may append D-### decisions. `task.md` is not
frozen because it is the status holder.

## Frozen contract
- `spec.md`: exact sha256:2755c29a68631e7a736865efa064be6c5d877051c12b6c72ca610311412e036f length:6812
- `plan.md`: exact sha256:1f3420e6aa8b37c628cddc66f97705907991fc766095cf7e1d202e3057667269 length:12262
- `survey.md`: exact sha256:d200c2a33a3c14559e0a74d1a7c5ab2df180a46013a69db19d8873e7020162bd length:24158
- `test.md`: exact sha256:fba6ad1ae097483e8c7667ba2e7139964e90f34ebf3bc076ac2591b19dc52a0d length:11923
- `tech-spec.md`: prefix sha256:8fb99d95a205bbf6069c6a8d51ced5650299521a8e776719f93291f231aa9abe length:30302

Any other change to these files requires a fresh user approval and a
new freeze record.
