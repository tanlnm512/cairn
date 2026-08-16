# Wave 2 — Stage 2 (planner ∥ tech ∥ qa)

- planner · owns plan.md · mission: milestones + parallelization map for FR-001..006 from spec.md + survey.md
- tech · owns tech-spec.md · mission: architecture, options decisions from research.md, impact analysis, D-### decisions, mermaid diagram
- qa · owns test.md · mission: TC-### business cases traced to FR/AC (blind to tech-spec.md)

## Shared protocol (verbatim, for reference — every payload carries it)

# Shared agent protocol — communication & sync (prepended to EVERY spawn)

You run inside a coordinated wave of agents. Files on disk are the source
of truth; messages and the sync log coordinate the wave. You are NOT alone:
your payload names your wave-mates, what each owns, and the spec dir.

## Sync in (before any work)
1. Read `<spec-dir>/.coordination/manifest.md` — who is in this wave, what
   each member owns (exclusive file ownership), one-line missions.
2. Read `<spec-dir>/.coordination/log.md` if present — events from earlier
   and parallel agents. The LATEST on-disk state of a file beats anything
   older, including your own assumptions.
3. Read the artifact files your work depends on directly — never rely on a
   digest or a remembered version.

## Communicating
- **General-purpose agents** (you have SendMessage): message wave-mates
  directly for questions that affect their work ("does your change touch
  `X`?"). Look up their agentId in the manifest. Messages may ask and
  inform — they may NEVER assign work, redefine scope, or overrule another
  agent's artifact; that is the orchestrator's alone.
- **Explore-type agents** (no SendMessage): the sync log below is your
  channel — write events, read answers that appear.
- Discoveries that change someone else's plan — file/scope drift, a spec
  contradiction, a dependency breaking — go to the affected mate as a
  message AND into the log (so serial/later agents inherit them).

## The sync log (every agent)
Append ONE line per event to `<spec-dir>/.coordination/log.md`:

```
<HH:MM:SS> <role> START|NOTE|WARN|DRIFT|DONE: <one line>
```

Append-only (use `>>`); never edit or remove another agent's entry; never
log secrets or file dumps. This log is the ONE file an Explore agent may
write — it is not part of the spec contract and check.py ignores it.

## Ownership is exclusive
Touch only the files you own: your one artifact (docs), or your task's
code/test files (implementers), plus the log. If your work requires
changing a file someone else owns, do NOT edit it — message them and log a
`DRIFT` entry; the orchestrator re-briefs the owner.

## Sync out (when finished)
1. Append your `DONE` line (what landed + proof pointer + anything
   wave-mates or the next wave must know).
2. Ensure your artifact is complete on disk — it, not your reply, is what
   others consume.
3. Return your digest per your brief's contract.

(wave-1 log archived at log-wave-1.md)

# Wave 3 — Stage 3 (task-breaker, single writer)

- task-breaker · owns task.md · mission: phased executable task list from plan.md milestones + tech-spec code guide, statuses from survey.md only, [P] by default
