# Approval freeze

**Approved-at**: `9a43103`
**Algorithm**: sha256 over UTF-8 bytes

Recorded only after explicit user approval. `spec.md` is hashed without
its volatile Status line. `tech-spec.md` is verified as an immutable
prefix so implementation may append D-### decisions. `task.md` is not
frozen because it is the status holder.

## Frozen contract
- `spec.md`: exact sha256:ba3316cc58b2f9d4fa540dcb90a8fc989ca2b43e497dae9f2917712269001d42 length:8160
- `plan.md`: exact sha256:7343c2b88af1c29875ea7420772caac618834dbc465c604e696fee2565c7712e length:10097
- `survey.md`: exact sha256:d40d349616b00a6465f56be46dafcdbc2d758a620960a2f726607db4d6af96ba length:34514
- `test.md`: exact sha256:5ac3a2c75a1fc78df0ac9739408943cb6c5eecf79267ff5de9a1e57da86d9dfd length:42771
- `tech-spec.md`: prefix sha256:c9b422d1ade4a4ce6ace346703a341c0e2eec10910208a3b03c1407c4c39c448 length:29630

Any other change to these files requires a fresh user approval and a
new freeze record.
