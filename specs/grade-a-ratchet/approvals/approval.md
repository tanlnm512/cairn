# Approval freeze

**Approved-at**: `2e7a7e7`
**Algorithm**: sha256 over UTF-8 bytes

Recorded only after explicit user approval. `spec.md` is hashed without
its volatile Status line. `tech-spec.md` is verified as an immutable
prefix so implementation may append D-### decisions. `task.md` is not
frozen because it is the status holder.

## Frozen contract
- `spec.md`: exact sha256:d7b0a2c99d7312e16716a5a1e0deedf86400a8f7c769eadf0ea0441066761ac1 length:5875
- `plan.md`: exact sha256:e7355cd0f5f0c52d268871a28935552bb18af0edb6bed7e3dc2ae71532c8ac43 length:5857
- `survey.md`: exact sha256:dc4b75be5d7eee7db0267b2a3172b88a9937b49e4dc400ea6459482082537097 length:8939
- `test.md`: exact sha256:715ea60dbc2b9607f9f98ccf66e6cbbc16fefc564a5f95da160f6b00a64c5d77 length:22837
- `tech-spec.md`: prefix sha256:55d652b25785abe58959ebd932ae9b7f02970ffb7443abdeaa5834e7dbc5c878 length:19987

Any other change to these files requires a fresh user approval and a
new freeze record.
