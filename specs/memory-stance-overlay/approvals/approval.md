# Approval freeze

**Approved-at**: `9a43103`
**Algorithm**: sha256 over UTF-8 bytes

Recorded only after explicit user approval. `spec.md` is hashed without
its volatile Status line. `tech-spec.md` is verified as an immutable
prefix so implementation may append D-### decisions. `task.md` is not
frozen because it is the status holder.

## Frozen contract
- `spec.md`: exact sha256:fac427c0eed76c601e1c59c4c7a818415b3db48f16a4b57944d5d679619162f1 length:5827
- `plan.md`: exact sha256:767152a165c6b17b2de10117a248e710246989d3862dcfe46112d99ef0f7b2b2 length:4159
- `survey.md`: exact sha256:610d25340458a2cd648fc7b8164cc6f109b11e2cd9e70030f9a6b5dc4984fdf3 length:5641
- `test.md`: exact sha256:f095ac1d7d0c84931910c21b7e2190cc821bf4ce72c96085d389d3d449f85ef2 length:13432
- `tech-spec.md`: prefix sha256:8885f651089caa020fc632bca3f8b990fe5126fbc581f0fb82b575e802d94263 length:20638

Any other change to these files requires a fresh user approval and a
new freeze record.
