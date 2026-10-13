# Approval freeze

**Approved-at**: `31a66df`
**Algorithm**: sha256 over UTF-8 bytes

Recorded only after explicit user approval. `spec.md` is hashed without
its volatile Status line. `tech-spec.md` is verified as an immutable
prefix so implementation may append D-### decisions. `task.md` is not
frozen because it is the status holder.

## Frozen contract
- `spec.md`: exact sha256:1884c20156c7e91b0c2a0c19508be1efaae2be915cfe524b34b62c03037698f9 length:10596
- `plan.md`: exact sha256:31c43660b105b9cd0c9a7dfa7f26a55ee349d222ce0281cedcc388553e942db7 length:9755
- `survey.md`: exact sha256:95aedc92d12264161e3be95f0f75311f1d9313a370d064336b5f60c5d3fb002a length:25099
- `test.md`: exact sha256:1081f9f1a3aab326b4d3e95bbb455873f741d8cc289dd33b00834dd31d2503fa length:43823
- `tech-spec.md`: prefix sha256:ca51edc136618809d251998f64b3e793701175a0515881d1a7d82d0c0f29e67e length:42759

Any other change to these files requires a fresh user approval and a
new freeze record.
