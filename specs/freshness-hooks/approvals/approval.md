# Approval freeze

**Approved-at**: `44f2be2`
**Algorithm**: sha256 over UTF-8 bytes

Recorded only after explicit user approval. `spec.md` is hashed without
its volatile Status line. `tech-spec.md` is verified as an immutable
prefix so implementation may append D-### decisions. `task.md` is not
frozen because it is the status holder.

## Frozen contract
- `spec.md`: exact sha256:7aab6459182806ca43c37c272e2e0be693a061fc205592641f3a4184b1d61041 length:5796
- `plan.md`: exact sha256:f1f5542b44218bad06533cdaca5b7d0ebf0191750ce11d2922e1e506d1f1a3dd length:4577
- `survey.md`: exact sha256:4e54e25726a90c579a096bf2eae9f5dc81b27a31d9d4f86eb7b0ee7b0a67063b length:5248
- `test.md`: exact sha256:f3bfcbeeb666dda1def73c22d1ccd9c41d34495be53741a9c6b040f28caed9fb length:21846
- `tech-spec.md`: prefix sha256:fd37df18a595c698ef301e44171ea656d89e73d77f334479f6596fb1a887f314 length:20987

Any other change to these files requires a fresh user approval and a
new freeze record.
