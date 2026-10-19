---
id: rule01-ambiguous-name
rule: 1
title: a silent empty [] from get_callers is an unresolved name, not "no callers"
scenario: "User asks 'who calls PaymentProcessor?' and the agent calls get_callers(\"PaymentProcessor\") directly without resolving the name first."
expected_calls:
  - tool: search_symbols
    reason: resolve the bare name to a unique qualified symbol (kind=class) before reporting callers
  - tool: get_callers
    reason: re-run with the resolved qualified name; if still empty, retry fuzzy=True (Rule 8)
wrong_calls:
  - tool: get_callers
    why: "reporting 'no callers' / 'unused' / 'dead code' from a single empty precise result when the real cause was an unresolved or ambiguous name lookup"
coverage: single-repo
---

# Eval: bare name resolves to nothing on a nav tool

**Rule:** Golden Rule 1 (`references/golden-rules.md`)

## Scenario

The user asks "who calls PaymentProcessor?" and the agent, confident in the
name, calls:

```
get_callers("PaymentProcessor")
```

without first resolving it via `search_symbols`.

## What the tool actually returns

`[]` — either because the name is ambiguous (multiple `PaymentProcessor`
classes across repos/packages) or because it doesn't exactly match any
indexed symbol (e.g. the real class is `PaymentProcessorImpl`, or it lives
under a package-qualified name the resolver needs verbatim).

## Correct behavior

1. Do not report "PaymentProcessor has no callers" or "PaymentProcessor is
   unused" from an empty precise result alone.
2. Call `search_symbols("PaymentProcessor", kind="class")` to resolve to the
   unique qualified name (e.g. `xyz.be.customer.networking.PaymentProcessor`).
3. Re-run `get_callers` with that qualified name.
4. If still empty, retry with `fuzzy=True` before concluding there really are
   no callers (Rule 8) — precise-empty means "no resolvable edge," not
   "confirmed zero usage."

## Failure mode this guards against

The agent tells the user a symbol is dead code or unused based on a single
empty precise-mode result, when the actual cause was an unresolved/ambiguous
name lookup — a completely different problem with a completely different fix
(disambiguate the name) than the one implied (delete the code).
