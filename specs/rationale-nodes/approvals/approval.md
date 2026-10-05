# Approval freeze

**Approved-at**: `ab764b9`
**Algorithm**: sha256 over UTF-8 bytes

Recorded only after explicit user approval. `spec.md` is hashed without
its volatile Status line. `tech-spec.md` is verified as an immutable
prefix so implementation may append D-### decisions. `task.md` is not
frozen because it is the status holder.

## Frozen contract
- `spec.md`: exact sha256:282b53ec5790ec88283c50b2e5a7976c65a5df59b5472dfcef6641bc1ab9de39 length:6349
- `plan.md`: exact sha256:e1a192c13be826be14ab99259c8b8ce13d6b145ed205dc648357aa37133f389c length:5500
- `survey.md`: exact sha256:a06ca69beb208f9e37cb0cd397211fd8d1de35ded875f3adbe5e625689ac34ca length:5382
- `test.md`: exact sha256:68bb05b9363dfcacec72f1b67d73a346912e65692237706843fd6475164b2f04 length:19093
- `tech-spec.md`: prefix sha256:de4d76f530eca0c180e409727de6699bb74006b808bf0adb9866247170917e7a length:25935

Any other change to these files requires a fresh user approval and a
new freeze record.
