---
id: rule09-impact-plus-cross-repo
rule: 9
title: impact_analysis + cross_repo_deps always together for multi-repo symbols
scenario: "Agent is asked for the blast radius of changing a shared-library symbol that has cross-repo consumers."
expected_calls:
  - tool: impact_analysis
    reason: within-repo recursive blast radius
  - tool: cross_repo_deps
    reason: cross-repo consumers (impact_analysis does not cross repos)
wrong_calls:
  - tool: impact_analysis
    without: cross_repo_deps
    why: "reports the small within-repo count as the complete blast radius, missing cross-repo consumers that live in other service graphs"
coverage: multi-repo
---

# Eval: impact_analysis + cross_repo_deps called together

**Rule:** Golden Rule 9 (`references/golden-rules.md`)

## Scenario

The user asks: "what's the blast radius if we change the signature of
`AuthService.validateToken`? It's used across our services." The agent knows
(or should confirm) that `AuthService` lives in a shared library repo that
other service repos consume as a dependency. It calls:

```
impact_analysis("validateToken")
```

...and stops there, then reports the result as the complete blast radius.

## What the tool actually returns

`impact_analysis` is **within-repo only**. It traces callers of
`validateToken` inside the single repo where it is defined (the shared
library). It returns the in-library call sites -- but it does NOT see the
service repos that depend on the library, because those callers live in
*other* graphs. The count looks reassuringly small (a handful of internal
helpers), giving the false impression that the change is contained.

## Correct behavior

1. Recognize that `AuthService.validateToken` is part of a shared library
   consumed by multiple repos -- a multi-repo change by definition.
2. Per Rule 9, call **both** tools, always, for any symbol that may cross a
   repo boundary:
   ```
   impact_analysis("validateToken")
   cross_repo_deps("AuthService.validateToken")
   ```
3. `impact_analysis` reports the within-repo callers; `cross_repo_deps`
   reports the external consumers (which service repos import the library and
   where they call the symbol).
4. Combine both into the blast-radius report: name the in-repo call sites
   *and* the cross-repo consumers explicitly. Never report one without the
   other for a multi-repo symbol.
5. If `cross_repo_deps` returns empty, say so explicitly -- "no cross-repo
   consumers found" -- rather than omitting the dimension. Absence of a check
   is indistinguishable from absence of callers to the user.

## Expected tool calls

- `impact_analysis("validateToken")` -- exactly once, for the within-repo
  half.
- `cross_repo_deps("AuthService.validateToken")` -- exactly once, for the
  cross-repo half.
- Calling only one of the pair for a shared-library symbol is the failure.

## Failure mode this guards against

An agent calls `impact_analysis` alone for a shared-library symbol, gets a
small within-repo number, and reports "blast radius is contained to ~5 files"
-- completely missing that three downstream service repos call
`validateToken` in their request hot paths. The user ships what they believe
is a safe signature change and breaks auth in every dependent service. This
is the canonical Rule 9 failure: treating a within-repo traversal as the full
blast radius for a multi-repo change.

## Pass / fail criteria

- **PASS:** Both `impact_analysis` and `cross_repo_deps` are called for the
  target symbol, and the final report explicitly distinguishes within-repo
  callers from cross-repo consumers (or states that no cross-repo consumers
  were found).
- **FAIL:** Only `impact_analysis` is called and its result is reported as the
  complete blast radius. Also FAIL if `cross_repo_deps` is called but its
  results are omitted from the user-facing report.
