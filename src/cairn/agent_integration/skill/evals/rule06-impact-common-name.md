---
id: rule06-impact-common-name
rule: 6
title: do not report name-collision-inflated impact_analysis counts as real blast radius
scenario: "Agent runs impact_analysis on a lifecycle/common name (e.g. initAppTracking reached via onCreate) and gets total: 222+ with non-empty cycles."
expected_calls:
  - tool: impact_analysis
    reason: run it but recognize the result as a name-collision artifact, not real coupling
  - tool: get_callers
    reason: re-resolve the real, narrow blast radius via the concrete class/interface method rather than the lifecycle entry point
  - tool: scripts/impact_guard.py
    reason: CLI helper that auto-detects the collision and falls back to a trustworthy dataflow number
wrong_calls:
  - tool: impact_analysis
    why: "quoting the raw inflated total (222, 1020, ...) to the user as if it were a verified blast radius"
coverage: single-repo
---

# Eval: impact_analysis on a common/lifecycle name

**Rule:** Golden Rule 6 (`references/golden-rules.md`)

## Scenario

The user asks: "what's the impact of changing auto-tracking's startup wiring?"
The agent traces the wiring to `MyApplication.initAppTracking()`, which is
called from `Application.onCreate`. It calls:

```
impact_analysis("initAppTracking")
```

## What the tool actually returns

`total: 222+` (grows with depth), and a non-empty `cycles` field naming
`onCreate` (and often `onViewCreated`, `render`, `create` in larger repos) --
lifecycle method names shared by dozens of unrelated classes across the
codebase.

## Correct behavior

1. Notice the result is large (well past the ~100 default threshold) *and*
   `cycles` is non-empty.
2. Recognize this as a name-collision artifact per Rule 6, not real coupling.
3. Do not quote the raw total (`222`, `1020`, whatever it is that run) as the
   blast radius in a report to the user.
4. Either re-run with a scoped/qualified name, cap `depth<=2`, or (if using
   the CLI) run `scripts/impact_guard.py initAppTracking` and report its
   output instead -- it performs steps 1-3 automatically and falls back to
   `cairn dataflow dataflow-lookup` for a trustworthy number.
5. State the real, narrow blast radius explicitly: the call site in
   `MyApplication.kt`, and whatever concrete class implements the tracking
   interface -- found via `get_callers` on the specific interface/method, not
   via the lifecycle entry point.

## Failure mode this guards against

An agent reports "changing this touches 1020 symbols" (or any similarly
inflated number) as if it were a real, verified blast radius, causing the
user to over-scope a review or refuse a safe change. This exact failure
happened once in production use before this rule/script existed -- an
`impact_analysis("initAppTracking")` call reported 1020 impacted symbols for
what was actually a leaf feature with a blast radius of ~4 files, purely
because the traversal walked through `onCreate`/`onViewCreated`/`render` name
collisions.
