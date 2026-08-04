# Resolution labels: a worked walkthrough

This is the concrete version of the [Resolution model][rm] design. It shows what
precise-vs-fuzzy actually returns, why it matters, and the ambiguous-dispatch
hops `explore` surfaces that grep cannot.

[rm]: ../architecture.md#resolution-model

## Setup: a name that collides

Imagine a polyglot repo where the name `invoke` appears in unrelated places:

- `PaymentsSdk.invoke(...)` — a real call site of the `PaymentsSdk` class.
- `AuthUseCase.invoke(...)` — a DI-injected UseCase idiom (no relation).
- `Worker.invoke(...)` — a stdlib-style callable (no relation).

Every code graph that resolves by name alone would lump these together.
cairn's resolver pins each edge to a definition when it can, and **labels**
the edge with the outcome.

## Precise (default) — ground truth

```bash
cairn impact invoke
```

Follows **only** `exact` edges. Returns the callers the resolver could pin to
the `invoke` definition you asked about — and *only* those. If `invoke` is the
`PaymentsSdk` method, you get the real call sites of that method:

```
Affected callers (precise): 3
  checkout/PaymentFlow.kt:42   handlePayment()
  ...
```

No false positives from the unrelated `AuthUseCase.invoke` or `Worker.invoke`.

## Fuzzy — the candidate list

```bash
cairn impact invoke --fuzzy
```

Adds name-only matches — every site that calls *something* named `invoke`,
resolved or not. Each is explicitly labelled `unverified`:

```
Candidates (fuzzy, name-only, unverified): 214
  checkout/PaymentFlow.kt:42   handlePayment()
  auth/LoginViewModel.kt:18    authenticate()   <- candidate, verify
  queue/WorkerPool.kt:77       process()        <- candidate, verify
  ...
```

The **false-positive rate** here is `1 - 3/214 ≈ 98.6%` — i.e. a name-only
graph would inflate blast radius ~70×. Precise mode protects you from that.

## When precise returns empty

```bash
cairn impact somePrivateHelper
# (empty)
```

This means "no *resolvable* callers," **not** "unused." The symbol may be called
only via patterns the resolver can't pin (reflection, dynamic dispatch,
cross-repo calls the namespace map doesn't cover). Before deleting it:

```bash
cairn impact somePrivateHelper --fuzzy   # audit the candidate list
```

## Ambiguous dispatch — what `explore` adds

`explore` reports `ambiguous` dispatch hops: call sites where multiple candidate
definitions existed and the resolver deliberately declined to guess. These are
polymorphic call sites — the kind of thing grep cannot find at all, because
finding them requires symbol resolution, not text matching.

```bash
cairn ask "how does dispatch work in the notifier"
```

The `explore` result includes an **Ambiguous dispatch hops** section listing
each such edge and its candidate definitions. This is where you discover the
extension points, the DI wiring, and the plugin seams — the polymorphism that
makes the codebase behave the way it does.

## Summary

| Question | Use | Why |
|----------|-----|-----|
| "What breaks if I change X?" | precise (default) | No false positives — exact edges only. |
| "Is X unused / dead?" | precise, then `--fuzzy` | Empty precise ≠ unused; audit fuzzy before deleting. |
| "Where is X dispatched polymorphically?" | `explore` / `ask` | Surfaces ambiguous hops grep can't see. |
| "Show me everything that might call X" | `--fuzzy` | Candidate list, labelled unverified — verify each. |

See also: [benchmarks.md — the resolution-label methodology](../benchmarks.md#the-resolution-label-methodology-cairns-differentiator)
for how to measure the precise-vs-fuzzy false-positive rate on your own codebase.
