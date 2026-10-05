# Approval freeze

**Approved-at**: `9a43103`
**Algorithm**: sha256 over UTF-8 bytes

Recorded only after explicit user approval. `spec.md` is hashed without
its volatile Status line. `tech-spec.md` is verified as an immutable
prefix so implementation may append D-### decisions. `task.md` is not
frozen because it is the status holder.

## Frozen contract
- `spec.md`: exact sha256:7aab6459182806ca43c37c272e2e0be693a061fc205592641f3a4184b1d61041 length:5796
- `plan.md`: exact sha256:f1f5542b44218bad06533cdaca5b7d0ebf0191750ce11d2922e1e506d1f1a3dd length:4577
- `survey.md`: exact sha256:4e54e25726a90c579a096bf2eae9f5dc81b27a31d9d4f86eb7b0ee7b0a67063b length:5248
- `test.md`: exact sha256:ad2171e98f9ffe2463feb1ef07c18209025bc0a2ef88aa4298fa8bc4d1148fb7 length:16620
- `tech-spec.md`: prefix sha256:4d80da7535d6f6c581965be4456e83f47812dc785de57b362585d82a1027acb8 length:19954

Any other change to these files requires a fresh user approval and a
new freeze record.
