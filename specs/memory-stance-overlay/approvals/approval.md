# Approval freeze

**Approved-at**: `24ba73a`
**Algorithm**: sha256 over UTF-8 bytes

Recorded only after explicit user approval. `spec.md` is hashed without
its volatile Status line. `tech-spec.md` is verified as an immutable
prefix so implementation may append D-### decisions. `task.md` is not
frozen because it is the status holder.

## Frozen contract
- `spec.md`: exact sha256:fac427c0eed76c601e1c59c4c7a818415b3db48f16a4b57944d5d679619162f1 length:5827
- `plan.md`: exact sha256:767152a165c6b17b2de10117a248e710246989d3862dcfe46112d99ef0f7b2b2 length:4159
- `survey.md`: exact sha256:610d25340458a2cd648fc7b8164cc6f109b11e2cd9e70030f9a6b5dc4984fdf3 length:5641
- `test.md`: exact sha256:a5cf114f3672e3ed4689481c13ca2832640400672bdd0f35e2f61d4e0a72ecc1 length:26020
- `tech-spec.md`: prefix sha256:8885f651089caa020fc632bca3f8b990fe5126fbc581f0fb82b575e802d94263 length:20638

Any other change to these files requires a fresh user approval and a
new freeze record.
