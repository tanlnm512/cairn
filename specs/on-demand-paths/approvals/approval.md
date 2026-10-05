# Approval freeze

**Approved-at**: `4f0dcdd`
**Algorithm**: sha256 over UTF-8 bytes

Recorded only after explicit user approval. `spec.md` is hashed without
its volatile Status line. `tech-spec.md` is verified as an immutable
prefix so implementation may append D-### decisions. `task.md` is not
frozen because it is the status holder.

## Frozen contract
- `spec.md`: exact sha256:543af2502e5f03f15ed3e7d379712e0caaf7a4ce01f48bb36ad8a94f54b67466 length:7041
- `plan.md`: exact sha256:aad5a2343c748111a76cbedec95594ec726aeceffded61e119e147bd78709a13 length:5763
- `survey.md`: exact sha256:b60c35c79a911c78fb82882b2165edb5f12e66fe0798970b8ff2a59cae32b9a3 length:5711
- `test.md`: exact sha256:593692f70da799071d680179506c4d00eac0df89c286ec312f41c11279218757 length:21549
- `tech-spec.md`: prefix sha256:1f863aa8c5bdd9dd26911a665525a3ce36cb9eeb0cee242c118f5ae79b520fa6 length:24677

Any other change to these files requires a fresh user approval and a
new freeze record.
