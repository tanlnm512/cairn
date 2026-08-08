# Roadmap

> Living document. The vision is stable; the milestones move. When a milestone
> lands, mark it done and keep the row (the history is the point). When the
> landscape shifts, update the strategic findings and re-sequence — that's how
> this stays honest.
>
> Each milestone is expanded into four executable documents (spec, plan,
> tech-guide, business-test-case) under [`docs/phases/`](phases/README.md).
> The roadmap says *what* and *why*; the phase docs say *how* and *prove it*.

## Vision

**cairn is the verifiable memory of your codebase for AI agents.**

Not a code graph — structural resolution is commoditized
([codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp),
Serena, SCIP indexers all do it). Not generic agent memory — that's ungrounded
(mem0 / Letta / Zep let an LLM silently rewrite what it "remembers"). cairn is
the narrow, defensible intersection: **a structural graph fused with
code-grounded tribal memory, where every output is traceable to source and
every synthesized doc is fact-checked before it lands.**

The product is a **verification contract**, made of three promises cairn can
machine-check:

1. **Every `exact` edge is actually resolved** (`target_id IS NOT NULL`).
2. **Every symbol in a compass / wiki / memory doc exists in the graph**
   (critic gate).
3. **Every answer is re-derivable from local data** (the LLM is never in the
   query path).

That contract is the moat. The five layers are how it's delivered; resolution
labels are evidence for promise #1 — not the headline.

## Strategic findings (landscape, Aug 2026)

These drive every sequencing decision. Re-verify when revising this doc.

- **Resolution labels are no longer a differentiator.**
  codebase-memory-mcp (~38k stars, MIT, local, single-binary) ships an
  essentially identical labeling scheme (`CALLS` / `CALL_REFERENCE` / `USAGE` +
  a "zero-edge guarantee"), with ~158 grammars and a Cypher query layer. On the
  graph axis alone, cairn is one of many and losing. **Stop competing there.**

- **Three things only cairn owns.** Every competitor holds a slice; nobody
  holds all three:
  1. The **unified 5-layer stack** (graph + compass + memory + knowledge +
     wiki) behind one local store and one MCP surface.
  2. **Code-grounded memory + a graph-verifying critic** with a real lifecycle
     (promote / demote / evolve / decay / digest). No general memory tool
     grounds memory in symbols, and no graph tool has memory.
  3. **Verifiability as a philosophy** — "LLM outside the query path." The
     critic is the most novel technical feature in the landscape; it is
     currently hidden, which undersells the product.

- **The leverage move is SCIP consumption, not grammar count — and the
  indexers already exist.** cairn will never reach 158 languages and shouldn't
  try. For languages with a compiler-backed indexer (Kotlin, Java, TS, Python,
  Go, Rust, C#, C/C++, Dart, PHP, Swift, Ruby), cairn gets compiler-grade edges
  by *consuming* SCIP and layering memory + MCP on top. Writing an indexer is
  embedding a compiler (months-years per language, perpetual maintenance tax) —
  that is Sourcegraph's job, not cairn's. The remaining gap is the
  analyzer-less long tail, where tree-sitter is already the right tool.

## The 3-month plan — Trust & Proof (Aug → Oct 2026)

Sequenced so each milestone **strengthens the contract** before the next
extends it. Every item has a machine-checkable "done when" — a trust product
is judged by what it can guarantee, not what it claims.

> **Two items unblock the rest:** the README repositioning (1.4) and the
> benchmark fill (2.1). Everything downstream depends on the contract being
> provable. If capacity is constrained, protect these two.

### Month 1 — Make the contract real (Aug)

The phase where cairn stops *claiming* verifiability and starts *proving* it.

| # | Work | Done when |
|----|------|-----------|
| 1.1 | **Rewrite the SCIP importer** to resolve `target_id` from a `{symbol_descriptor → def_id}` map, derive `source_id` from `enclosing_range`, and label `exact` *only* when `target_id` is found. (BUGS.md: scip-importer-fake-resolution.) | A pure-SCIP build of cairn's own repo has zero `exact` rows with NULL `target_id`. |
| 1.2 | **Graph invariant test:** `resolution='exact'` ⟹ `target_id IS NOT NULL`, in CI. (BUGS.md calls this out explicitly.) | The test runs on every CI build and fails the build on violation. |
| 1.3 | **Critic invariant test:** no compass / wiki / memory doc can name a symbol absent from the graph. | A unit test that submits a doc referencing a fake symbol is rejected by the critic. |
| 1.4 | **Reposition README + `architecture.md` "Why cairn?"** to lead with the 3-promise contract, then the 5 layers, then resolution labels as evidence. | The front page reads as "verifiable codebase memory," not "the resolution-label graph." |

### Month 2 — Prove it (Sep)

The phase where cairn *shows* the contract holds, with numbers and a live demo.

| # | Work | Done when |
|----|------|-----------|
| 2.1 | **Fill the benchmark tables** (`cairn eval`, `cairn bench`) on three corpora: cairn's own repo (Python), a Kotlin repo, a TypeScript repo. Publish precise-vs-fuzzy **false-positive rates** — the methodology `benchmarks.md` already describes. | `benchmarks.md` tables are no longer `_fill_`; the methodology post (2.4) cites real numbers. |
| 2.2 | **Make the critic visible.** Expose critic verdicts (verified / rejected / must-fix) in `ask_compass`, `wiki generate`, and a new `cairn verify <doc>` command. | A user can ask "is this compass guide trustworthy?" and get a machine-checked answer. |
| 2.3 | **"cairn on cairn" live demo.** A single `cairn build` on this repo produces a reproducible `explore` + `impact` + `get_compass` walkthrough that a visitor can run verbatim. | The README quick-start, run on cairn's own repo, returns real, correct results. |
| 2.4 | **Methodology post / shareable doc:** "precise vs fuzzy — a false-positive methodology for trusting a code graph." Uses 2.1's numbers. | A standalone artifact that makes the verification contract legible to someone who has never installed cairn. |

### Month 3 — Turn proof into the memory moat (Oct)

The phase where the verified foundation becomes the thing no competitor has:
trustworthy, code-grounded memory. New languages wait (see Beyond) — they
widen the market but don't strengthen the contract.

| # | Work | Done when |
|----|------|-----------|
| 3.1 | **Graph-grounded memory recall.** Memories that cite symbols get symbol-verified on recall (reject / flag if the cited symbol no longer exists). Extends the critic concept to the memory layer. | A memory citing a deleted symbol is surfaced as stale, not served as truth. |
| 3.2 | **Memory-triggered build hints.** `cairn update` after editing an anchored file reports "N memories reference this — review before relying on impact results." | Editing a memory-anchored file produces an actionable, graph-grounded warning. |

## Beyond 3 months (directional, not detailed)

Re-prioritized at the end of Q4. Lead items first.

- **Consume SCIP indexers — don't build them.** The scale path is wiring,
  not authoring. Every production SCIP indexer sits on a real compiler /
  type-checker (scip-java → `javac` plugin, scip-typescript → `tsc`,
  scip-python → Pyright, scip-go → `go/packages`, rust-analyzer `scip` →
  itself, scip-clang → Clang, scip-dotnet → Roslyn, scip-dart → Dart
  analyzer, scip-php → `nikic/php-parser`, scip-swift → IndexStoreDB). A
  tree-sitter-based indexer is a contradiction in terms — tree-sitter has no
  name resolution, and the one tool that tries (`scip-tree-sitter`) emits
  highlighting tokens only, "no navigation." Writing an indexer is embedding a
  compiler: a months-to-years-per-language project with a perpetual
  compiler-release maintenance tax. That is Sourcegraph's job, not cairn's.
  - **The work here is consumption:** correct the importer (1.1), then wire
    the indexers that *already exist* into `cairn.json` auto-generation.
    Almost every language cairn would want is already covered: Kotlin, Java,
    TS, Python, Go, Rust (`rust-analyzer scip`), C# (`scip-dotnet`),
    C/C++ (`scip-clang`, beta), Dart (`scip-dart`), PHP (`scip-php`).
    Priority: C/C++ and Rust (widen the market beyond the current 9 tree-sitter
    languages — C/C++ is `parse_errors` today) → C#. Blocked on 1.1 being solid.
  - **The real gap is the analyzer-less long tail** (Erlang, Lua, R, Elixir,
    Nim, Zig, most Lisps): no compiler semantic API, no SCIP indexer, and
    nothing to consume. Tree-sitter is the correct tool there — and cairn
    already does it. SCIP adds nothing where there is nothing to consume.
- **Expose `kind=` filter in MCP wrappers** (the library layer supports it; the
  MCP surface doesn't — quick polish that completes the API).
- **Memory lifecycle as a visible cadence** — `cairn memory digest` showing
  graduating / expiring entries.
- **Cross-repo blast radius as a flagship query** — a question no
  context-packer can answer; worth featuring prominently.
- **Content & distribution.** cairn is at a small star count vs
  codebase-memory-mcp's ~38k. The verification narrative is the wedge; the
  methodology post (2.4) is the first swing.

## What this plan deliberately does not do

- **Does not chase grammar count.** cairn will never reach 158 languages and
  shouldn't try. SCIP leverage is the only realistic scale path.
- **Does not fight codebase-memory-mcp on the graph axis.** Resolution labels
  stay as evidence, not headline.
- **Does not add features that dilute the contract.** Anything that can't be
  machine-verified waits until the foundation is airtight.

## Honest risks

1. **codebase-memory-mcp is ~10x the stars and matches the headline
   differentiator.** Repositioning (1.4) is the response — stop fighting on
   their axis. But this is real; cairn will lose a head-to-head "which code
   graph?" comparison.
2. **Solo maintainer, project is days old.** Bus factor. The bug-registry +
   invariant-test discipline is good hygiene; keep it up.
3. **Language coverage gap (9 vs 158 / 40+).** SCIP leverage (Beyond) is the
   only realistic path.
4. **The trust promise is fragile.** Items 1.1–1.3 exist because the promise
   already had a hole (fake SCIP resolution). Trust products die by one bad
   `exact` — the invariant tests exist to make that failure loud and immediate.
