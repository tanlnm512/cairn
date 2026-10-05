# Approval freeze

**Approved-at**: `9a43103`
**Algorithm**: sha256 over UTF-8 bytes

Recorded only after explicit user approval. `spec.md` is hashed without
its volatile Status line. `tech-spec.md` is verified as an immutable
prefix so implementation may append D-### decisions. `task.md` is not
frozen because it is the status holder.

## Frozen contract
- `spec.md`: exact sha256:543af2502e5f03f15ed3e7d379712e0caaf7a4ce01f48bb36ad8a94f54b67466 length:7041
- `plan.md`: exact sha256:aad5a2343c748111a76cbedec95594ec726aeceffded61e119e147bd78709a13 length:5763
- `survey.md`: exact sha256:b60c35c79a911c78fb82882b2165edb5f12e66fe0798970b8ff2a59cae32b9a3 length:5711
- `test.md`: exact sha256:78a4f847fb28ece7ebc58ebce9b8a1a1ec81811b47ee8063093e4513de517593 length:21226
- `tech-spec.md`: prefix sha256:0e6a3a5c80b458996d8c5d7aa57bbe84867c7662482b87937d149d25f51afb82 length:20073

Any other change to these files requires a fresh user approval and a
new freeze record.
