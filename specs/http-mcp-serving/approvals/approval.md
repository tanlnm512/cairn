# Approval freeze

**Approved-at**: `88a03f1`
**Algorithm**: sha256 over UTF-8 bytes

Recorded only after explicit user approval. `spec.md` is hashed without
its volatile Status line. `tech-spec.md` is verified as an immutable
prefix so implementation may append D-### decisions. `task.md` is not
frozen because it is the status holder.

## Frozen contract
- `spec.md`: exact sha256:ba3316cc58b2f9d4fa540dcb90a8fc989ca2b43e497dae9f2917712269001d42 length:8160
- `plan.md`: exact sha256:7343c2b88af1c29875ea7420772caac618834dbc465c604e696fee2565c7712e length:10097
- `survey.md`: exact sha256:d40d349616b00a6465f56be46dafcdbc2d758a620960a2f726607db4d6af96ba length:34514
- `test.md`: exact sha256:2dfde22bb579ea2f113f85dae8fe50f7abf45defe4269df8d09d1d1a3084515c length:47011
- `tech-spec.md`: prefix sha256:0d230bad48f457d812d2f512e49e2ba68cefc7dce5e060b42410769b51fe7620 length:30591

Any other change to these files requires a fresh user approval and a
new freeze record.
