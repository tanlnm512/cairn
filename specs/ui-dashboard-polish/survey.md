# Survey: <name>

**Created**: YYYY-MM-DD | **Baseline**: <version @ commit>
Phase-A output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item <id>: "<one-line description>"
  evidence:   <file:symbol with line — copied verbatim from grep/read output>
  status:     DONE | PARTIAL | TODO
  verify:     <the exact command that proves the status>
  gap:        <if PARTIAL/TODO: the precise missing piece>
```

## Supporting evidence
<Load-bearing symbols/counts cited by tech-spec.md's code guide: machinery,
couplings, consumer lists, counts. Same verbatim rule.>

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
