# Approval freeze

**Approved-at**: `9217c45`
**Algorithm**: sha256 over UTF-8 bytes

Recorded only after explicit user approval. `spec.md` is hashed without
its volatile Status line. `tech-spec.md` is verified as an immutable
prefix so implementation may append D-### decisions. `task.md` is not
frozen because it is the status holder.

## Frozen contract
- `spec.md`: exact sha256:6017cde051a284b4251d325e56406dd7b013e1afd0f58521ff91493d2c2e9e8c length:5407
- `plan.md`: exact sha256:4d6599fb8129f01fe9d36a4a9ecd59ee8c2326643366cba25f58ec2eedfe4839 length:6524
- `survey.md`: exact sha256:38f2dbff135a3f786715c66a2f8de68482bb6c54a4c17f0bec659909d6724c9c length:10029
- `test.md`: exact sha256:d218edf47b9fb1674b2185de9f8827490e25a7aec450cd0e1ac0fcc2e321f61f length:9907
- `tech-spec.md`: prefix sha256:15996c0ba7e5d0f9b818ce05fdcee02b85836771ae5056dfee8f92d90092a990 length:20902

Any other change to these files requires a fresh user approval and a
new freeze record.
