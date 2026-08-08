# Phase 2 — Prove It (Business Test Case)

> **Roadmap milestone:** Month 2 — Prove it.
> **Read alongside:** [`spec.md`](spec.md), [`plan.md`](plan.md).
> **Purpose:** prove, in user-facing scenarios, that the phase delivered value.

## The user this phase serves

**Persona:** the same skeptical evaluator from Phase 1, plus a new one — the
**comparison shopper**. They have read cairn's claims and a competitor's
(codebase-memory-mcp) and are deciding. They will not install cairn to test
it; they will skim the README and the methodology. What they need from Phase 2
is **public, reproducible proof** that the verification contract holds, and a
demo they can re-run if they want to check.

**Value delivered by Phase 2:** the evaluator's question shifts from "does
cairn enforce its promises?" (Phase 1) to "can I *see* that it does, without
taking your word for it?" Phase 2's answer: filled benchmarks, a visible
critic verdict, and a one-command self-demo.

## Acceptance scenarios

### Scenario A — "The benchmarks show the precise-vs-fuzzy tradeoff, with real numbers"
**Maps to:** 2.1. **Strengthens:** promise #1 (evidence).
**Spec "Done when":** benchmarks.md tables are no longer `_fill_`; 2.4 cites real numbers.

```
GIVEN a reader opening docs/benchmarks.md
 WHEN they look at the resolution-label methodology table
 THEN they see real false-positive rates for three corpora (Python, Kotlin, TS)
  AND each table records the corpus commit SHA + cairn version + hardware
  AND the numbers are reproducible by re-running the cited commands
```

**Demonstrating command (a reader / CI job):**
```bash
cairn eval --corpus L1        # on a pinned corpus
cairn impact invoke            # precise
cairn impact invoke --fuzzy    # fuzzy — the gap is the false-positive rate
```

**Why this matters:** the strategic finding is that resolution labels are
commoditized. cairn's remaining edge on promise #1 is the *documented,
measured* tradeoff. Blank tables are a claim; filled tables are evidence. The
comparison shopper notices the difference.

### Scenario B — "I can ask whether a doc is trustworthy and get a machine-checked answer"
**Maps to:** 2.2. **Strengthens:** promise #2 (evidence).
**Spec "Done when":** a user can ask "is this compass guide trustworthy?" and get a machine-checked answer.

```
GIVEN a generated compass/wiki/memory concept on disk
 WHEN the user runs `cairn verify <doc>` (or an agent calls ask_compass / wiki generate)
 THEN the critic verdict is returned: passed (bool), errors [], warnings []
  AND file-ref errors and symbol-ref warnings are listed distinctly
```

**Demonstrating command:**
```bash
cairn verify .knowledge/compass/some_module.md
# prints: passed=false, errors=[Hallucinated file: x.py], warnings=[Unknown symbol: Foo]
# (today `cairn compass validate` does this for all compass files at once;
#  `cairn wiki generate` surfaces verdicts on the CLI. Phase 2 adds the
#  unified single-doc `verify` + structured MCP field.)
```

**Why this matters:** the critic is the mechanism behind "every symbol/file
in a doc exists in the graph." It is already surfaced in `generate_flow`,
`compass validate`, and `wiki generate` — but as flattened text or scoped to
one doc type. Phase 2 unifies it into a single-doc `verify` command and a
structured MCP field, making promise #2 *observable* to any caller, for any
doc, in a machine-readable shape.

### Scenario C — "I can run cairn on cairn and get correct results, verbatim"
**Maps to:** 2.3. **Strengthens:** all three (lived experience).
**Spec "Done when":** the README quick-start, run on cairn's own repo, returns real, correct results.

```
GIVEN a new user who has just installed cairn
 WHEN they clone cairn's repo and run the self-demo command from the README
 THEN cairn builds its own graph and explore/impact/get_compass return correct, non-empty results
  AND the same demo runs green in CI (so it cannot silently rot)
```

**Demonstrating command:**
```bash
scripts/demo_self.sh   # or: pytest -m core -k self_demo
```

**Why this matters:** dogfooding is the most credible demo — "this tool
indexes itself, here is the verbatim output." It converts the README's claims
into a reproducible experience. A visitor who runs it and sees real
`explore("build_graph")` output believes the contract faster than any prose.

### Scenario D — "A non-user can understand the methodology without installing cairn"
**Maps to:** 2.4. **Strengthens:** all three (narrative).
**Spec "Done when":** a standalone artifact legible to someone who has never installed cairn.

```
GIVEN a reader who has not installed cairn
 WHEN they read docs/methodology-precise-vs-fuzzy.md
 THEN they understand the false-positive methodology, see real numbers, and could reproduce them
  AND the post is linked from the README and benchmarks.md
```

**Demonstrating check:**
```bash
test -f docs/methodology-precise-vs-fuzzy.md && echo "exists"
grep -rl "methodology-precise-vs-fuzzy" README.md docs/benchmarks.md   # linked from both
```

**Why this matters:** the comparison shopper does not install; they read.
This post is the artifact that travels — shareable on its own, carrying real
numbers and a reproducible method. It is Phase 2's distribution payload.

## Phase exit criteria (all four required)

- [ ] Scenario A: three corpora filled in benchmarks.md with SHAs + version + hardware.
- [ ] Scenario B: `cairn verify` prints verdicts; MCP tools return the verdict additively.
- [ ] Scenario C: self-demo script green in CI, referenced from README quick-start.
- [ ] Scenario D: methodology post exists with real numbers, linked from README + benchmarks.md.

## What this phase does NOT prove

- It does **not** prove the resolver is *high-quality* — only that its precise
  vs fuzzy tradeoff is *measured*. If recall is poor, the honest follow-up is
  a resolver-tuning task (Phase 4).
- It does **not** add memory grounding (Phase 3) or new languages (Phase 4).
- It does **not** change the critic's enforcement — only surfaces its verdict.
  Tightening symbol-refs to block is a deliberate later change.

Phase 2's job is to make Phase 1's guarantees *visible and reproducible*. It
is the "show, don't tell" phase.
