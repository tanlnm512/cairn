---
id: rule08b-empty-precise-gave-up
rule: 8
title: empty precise is "no resolvable edge", not "no callers" -- retry fuzzy
scenario: "Agent is asked for callers of a symbol defined in an external pod (not indexed); precise get_callers returns [] and the agent reports 'no callers'."
expected_calls:
  - tool: get_callers
    reason: precise first; if empty, retry with fuzzy=True before concluding there are no callers
  - tool: search_symbols
    reason: confirm the symbol has no local definition (empty result), distinguishing external-pod case from rule01's ambiguous-name case
wrong_calls:
  - tool: get_callers
    why: "reporting 'no callers' / 'cannot enumerate' from an empty precise result alone, when a real caller exists and was never queried via fuzzy=True"
coverage: single-repo
---

# Eval: empty precise result reported as "no callers" instead of retrying fuzzy

**Rule:** Golden Rule 8 (`references/golden-rules.md`) — the other direction from
`rule08-precise-then-fuzzy.md` (that one guards against over-trusting fuzzy noise;
this one guards against under-using fuzzy at all).

## Scenario

The agent is asked "list callers of `BEAPIConnector.startLoadURL`". `startLoadURL`
is defined in a sibling pod that isn't vendored/indexed in this workspace, so no
`symbols` row exists for it. Precise `get_callers("startLoadURL")` therefore always
returns zero rows — not because there are no callers, but because there's no local
symbol for the resolver to pin an edge to.

## What actually happened (caught in a live A/B benchmark run, 2026-07-28)

The agent ran `search_symbols("startLoadURL")` (empty), then `get_callers` precise
(empty), and reported "could not enumerate real callers" — stopping there. A parallel
run using only `grep -rn startLoadURL` found the real caller in one shot:
`BEInternetConnectionManager.pingGoogle()` at
`beCustomer/Infrastructure/beSDK/BEInternetConnectionManager.swift:87`. Manually
re-running `get_callers("startLoadURL", fuzzy=True)` confirmed the graph had this
edge the whole time — the agent just never asked for it.

## Correct behavior

1. Precise empty is not the end of the investigation — it means "no *resolvable*
   edge," which is expected whenever the target symbol is defined outside the
   indexed workspace (external pod, vendored dependency, another language).
2. Retry with `fuzzy=True` before reporting "no callers"/"can't find callers."
3. If fuzzy also comes up empty, *then* it's reasonable to say no callers were
   found in the indexed workspace.

## Mitigation shipped

`get_callers`/`get_callees` (`src/cairn/mcp_server/tools_graph.py`) now retry fuzzy
automatically when `fuzzy=False` and precise returns zero rows, labeling the
result as unverified fuzzy candidates rather than silently returning "no callers
found." This removes the dependency on the agent remembering to retry, but the
verification discipline in `rule08-precise-then-fuzzy.md` (check each fuzzy hit
against real code before citing it) still applies to whatever the fallback
surfaces.

## Failure mode this guards against

The agent tells the user "there are no callers of X in this codebase" (implying
it's dead code / safe to remove/rename) when a real caller exists and was simply
never queried for, because the agent treated an empty *precise* result as
equivalent to "no callers exist."
