# Approval freeze

**Approved-at**: `d702c6e`
**Algorithm**: sha256 over UTF-8 bytes

Recorded only after explicit user approval. `spec.md` is hashed without
its volatile Status line. `tech-spec.md` is verified as an immutable
prefix so implementation may append D-### decisions. `task.md` is not
frozen because it is the status holder.

## Frozen contract
- `spec.md`: exact sha256:6017cde051a284b4251d325e56406dd7b013e1afd0f58521ff91493d2c2e9e8c length:5407
- `plan.md`: exact sha256:4d6599fb8129f01fe9d36a4a9ecd59ee8c2326643366cba25f58ec2eedfe4839 length:6524
- `survey.md`: exact sha256:38f2dbff135a3f786715c66a2f8de68482bb6c54a4c17f0bec659909d6724c9c length:10029
- `test.md`: exact sha256:3c4a776ac50b87b83ab8bbaebedce4f0c36af18a3d8d0cab8e2885a2758394a9 length:10082
- `tech-spec.md`: prefix sha256:6ff64134915640a92f054dca11d6f262f61d2254049b4af6d6387c456b1cb697 length:22125

Any other change to these files requires a fresh user approval and a
new freeze record.
