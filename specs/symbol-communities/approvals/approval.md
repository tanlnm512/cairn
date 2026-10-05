# Approval freeze

**Approved-at**: `a020d3b`
**Algorithm**: sha256 over UTF-8 bytes

Recorded only after explicit user approval. `spec.md` is hashed without
its volatile Status line. `tech-spec.md` is verified as an immutable
prefix so implementation may append D-### decisions. `task.md` is not
frozen because it is the status holder.

## Frozen contract
- `spec.md`: exact sha256:2755c29a68631e7a736865efa064be6c5d877051c12b6c72ca610311412e036f length:6812
- `plan.md`: exact sha256:1f3420e6aa8b37c628cddc66f97705907991fc766095cf7e1d202e3057667269 length:12262
- `survey.md`: exact sha256:d200c2a33a3c14559e0a74d1a7c5ab2df180a46013a69db19d8873e7020162bd length:24158
- `test.md`: exact sha256:e96cd482a073ce5e4fb302dd049fab747991bf9963ecb4559e8177386a1c055c length:16787
- `tech-spec.md`: prefix sha256:27b8e048217b615e93cc4468022e2b6009d1bb77df10ab0f986c4e4861b28acc length:32330

Any other change to these files requires a fresh user approval and a
new freeze record.
