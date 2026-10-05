# Approval freeze

**Approved-at**: `e6dd71a`
**Algorithm**: sha256 over UTF-8 bytes

Recorded only after explicit user approval. `spec.md` is hashed without
its volatile Status line. `tech-spec.md` is verified as an immutable
prefix so implementation may append D-### decisions. `task.md` is not
frozen because it is the status holder.

## Frozen contract
- `spec.md`: exact sha256:f8b9e3945d14bb8b612d26fe7bdbf8c9b298a7d5c3072828c151d8efc29a5e30 length:6246
- `plan.md`: exact sha256:445b3a3ebabc81dc17598126814dfe0cb1de59363f815c9f26d16eb24146c749 length:5619
- `survey.md`: exact sha256:938270b6a681b40cd1bae624ca0511a74c6e44e1d63b2469c649aa9da829ad98 length:4378
- `test.md`: exact sha256:64aa8e620783b8e6aff665d4158ec1210e48f1b855abfe290a221ae34542e498 length:45318
- `tech-spec.md`: prefix sha256:fa8b7c733cf0cae36b12741ad2adea3f5ab53617ab5a9a983097ee3ea9a47b3f length:22168

Any other change to these files requires a fresh user approval and a
new freeze record.
