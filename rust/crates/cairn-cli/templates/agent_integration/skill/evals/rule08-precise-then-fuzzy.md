---
id: rule08-precise-then-fuzzy
rule: 8
title: start precise; treat fuzzy hits as unverified candidates, never as confirmed callers
scenario: "Agent skips the precise step and calls get_callers(\"install\", fuzzy=True) directly, getting 7+ same-token hits across repos/languages."
expected_calls:
  - tool: get_callers
    reason: start precise on the resolved qualified name (AutoTracker.install) -- this is the ground-truth caller list
  - tool: search_symbols
    reason: resolve the qualified name before calling get_callers so fuzzy noise is avoided
wrong_calls:
  - tool: get_callers
    why: "calling fuzzy=True first and citing every same-token hit as a real dependent, inflating the blast radius with unrelated same-named methods"
coverage: multi-repo
---

# Eval: fuzzy candidates treated as confirmed callers

**Rule:** Golden Rule 8 (`references/golden-rules.md`)

## Scenario

The agent is auditing usage of a method named `install()` and, to save a
step, calls `get_callers("install", fuzzy=True)` directly instead of
starting precise.

## What the tool actually returns

7+ hits spanning multiple repos and languages: `AutoTracker.install` (the
real target), `BeMainService.onShowOverLay`'s internal `install()`, a Swift
`JSONTreeView.swift` `install()`, an unrelated `installPerformanceTracker`,
etc. — every symbol that merely shares the token `install`, not just real
callers of the target.

## Correct behavior

1. Start precise: `get_callers("AutoTracker.install")` (resolved, qualified
   name) — this is the ground-truth caller list.
2. Only reach for `fuzzy=True` if the precise result is empty or suspiciously
   small for a symbol you expect to be widely used.
3. When fuzzy is used, treat every hit as a *candidate*: check the file
   path/package against the domain before citing it as a real caller. A
   shared method name across repos/languages is not evidence of a real call
   edge.
4. Report only the candidates verified against actual code, and label the
   rest as "unrelated same-named methods" rather than silently dropping them
   or, worse, citing them as dependents.

## Failure mode this guards against

The agent reports "7 places call AutoTracker.install" and lists unrelated
methods from other repos as if they were real dependents, inflating the
apparent blast radius of a change and misleading the user about what
actually needs updating.
