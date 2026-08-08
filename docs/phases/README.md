# Phase Documentation

> The [roadmap](../roadmap.md) says *what* and *why*. Each phase folder expands
> one roadmap milestone into four documents so the work is clear, consistent,
> and executable — not aspirational.

## Phases

| Phase | Folder | Roadmap milestone | Status |
|-------|--------|-------------------|--------|
| 1 | [`phase-1-trust-contract/`](phase-1-trust-contract/) | Month 1 — Make the contract real | Upcoming |
| 2 | [`phase-2-prove-it/`](phase-2-prove-it/) | Month 2 — Prove it | Upcoming |
| 3 | [`phase-3-memory-moat/`](phase-3-memory-moat/) | Month 3 — Turn proof into the memory moat | Upcoming |

Phase 4 ("Beyond 3 months") stays directional in the roadmap until the end of
Q4, at which point it is promoted into its own phase folder.

## The five-file contract

Every phase has exactly five files. They answer five distinct questions and
must stay mutually consistent — the `spec` names the items, the `plan`
sequences them, the `tech-guide` grounds them in code, the
`business-test-case` proves them in user terms, and the `task` tracker holds
live status. If the five disagree, that is a bug in the phase, not a footnote.

| File | Answers | Owner question | Read it when |
|------|---------|----------------|--------------|
| **`spec.md`** | *What & why* | What are we building, and which promise does each item fulfill? | Starting the phase, or checking scope |
| **`plan.md`** | *How & when* | What are the tasks, in what order, with what risks? | Planning a sprint, estimating, sequencing |
| **`tech-guide.md`** | *How does it work* | Which files/symbols/patterns, and what pitfalls? | Implementing a task |
| **`business-test-case.md`** | *Does it deliver value* | What scenario, in user terms, proves the phase landed? | Validating, demoing, or closing the phase |
| **`task.md`** | *Where are we* | What is the status of each sub-task right now? | Stand-up, sprint review, unblocking |

### The plan / task split (important)

`plan.md` and `task.md` look similar but own **different things**. Keeping
them separate is what lets the plan stay stable while status moves daily.

- **`plan.md` owns the task list and sequencing.** It describes the sub-tasks
  as a numbered list (under each item) and the order/dependencies between
  items. It does **not** carry status — no checkboxes. If the plan changes,
  the work changed.
- **`task.md` owns the status.** Every plan sub-task gets a stable ID
  (`<item>.<seq>`, e.g. `1.1.1`, `1.1.2`) and a row in `task.md` with a status
  (`todo` / `doing` / `done` / `blocked` / `skipped`) and a notes column. A
  burndown table at the bottom rolls up counts per item. Update this file as
  work progresses; do not edit `plan.md` for status.

The mapping is mechanical: plan item `1.1`'s numbered sub-tasks become
`task.md` rows `1.1.1`, `1.1.2`, … One source of truth per concern.

### Consistency rules

1. **Item IDs are sacred.** A roadmap item like `1.1` keeps the same ID across
   all five files in the phase. Sub-task IDs (`1.1.1`, …) are stable in
   `task.md`. Do not renumber; do not invent new IDs.
2. **Every spec item has a "Done when."** The same "Done when" wording appears
   in `plan.md`'s item header, `task.md`'s phase-exit gate, and
   `business-test-case.md`'s acceptance scenario. One definition of done, four
   places.
3. **Every tech-guide reference is real.** File paths and symbol names must
   point at things that exist in the tree (or will exist, clearly marked
   `// new`). No invented APIs.
4. **Scope is explicit in two places.** `spec.md` lists what is *out* of scope;
   `plan.md` repeats the boundary as a guard against scope creep.
5. **One promise per item.** Each item in `spec.md` names the verification
   promise (1, 2, or 3 — see roadmap vision) it strengthens. Items that don't
   strengthen any promise don't belong in a trust-first plan.
6. **Status lives only in `task.md`.** The plan, spec, tech-guide, and
   business-test-case are status-free. If you want to know "are we done?",
   read `task.md`'s burndown; if you want to know "what does done mean?", read
   the spec.

## Cross-references

- [Roadmap](../roadmap.md) — vision, strategic findings, sequencing rationale.
- [Architecture](../architecture.md) — the 5 layers, resolution model, LLM boundary.
- [BUGS registry](../BUGS.md) — pitfalls that became tech-guide content.
- [Benchmarks](../benchmarks.md) — the methodology Phase 2 fills with numbers.
