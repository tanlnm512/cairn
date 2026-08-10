# Cairn: Query Decision Tree

A top-down "what's your question → which tool" map. Come here when the
condensed workflow in SKILL.md isn't enough to pick the right tool on the
first try. For the rationale behind each rule, see
`references/golden-rules.md`; for per-tool gotchas, see
`references/tool-behaviors.md`.

## The default path

For most questions — "how does X work", surveying an area, anything vague —
one call is enough:

```
explore(query)  →  done
```

`explore` aggregates FTS5 seeds (+ optional semantic expansion), 1-hop
callers/callees, verbatim source spans, depth-2 blast radius, and ambiguous
dispatch hops into a single answer. See Rule 2 in `golden-rules.md`.

## When explore isn't enough (escalation triggers)

`explore` makes three trade-offs by design. Each one has a specific
escalation tool:

| explore's limit | You need... | Escalate to |
|-----------------|-------------|-------------|
| Blast radius is **depth-2 only** | Recursive callers (breaking change) | `impact_analysis(name)` + `cross_repo_deps(repo)` |
| Neighborhood is **unordered** | Execution order (what runs when) | `trace_flow(entry)` |
| Results are **pure L1 structural** | Why/decisions/wiki/tribal knowledge | `ask_compass(query)` or `recall_memory(query)` |
| FTS5 seeds are **token-based** | Meaning-based match (synonyms, paraphrases) | `semantic_search(query)` |

These are additive — call them *after* `explore` to go deeper, not instead
of it. `explore` already gave you the seed names and file locations the
escalation tools need.

## Full decision tree

```
What's your question?
│
├─ "How does X work" / surveying / vague
│    │
│    ▼
│  explore(query)                         ← THE DEFAULT (one call)
│    │
│    ├─ Enough? → done (most common)
│    │
│    ├─ Need recursive blast radius?
│    │    └→ impact_analysis(seed) + cross_repo_deps(repo)   [Rule 9]
│    │
│    ├─ Need ordered execution chain?
│    │    └→ trace_flow(entry)                               [downward]
│    │
│    ├─ Need WHY / decisions / wiki?
│    │    └→ ask_compass(query)  or  recall_memory(query)
│    │
│    └─ explore returned thin?
│         ├→ search_symbols(pattern)      [lexical, broader]  [Rule 3]
│         └→ semantic_search(query)       [meaning-based]
│
├─ "Where is X defined" (exact or partial name)
│    └→ find_definition(name)
│         └→ nothing? search_symbols(pattern)                [Rule 1]
│
├─ "Who calls X" / "what breaks if I change X"
│    ├─ One hop:    get_callers(name)                        [precise]
│    └─ Recursive:  impact_analysis(name) + cross_repo_deps  [Rule 6, 9]
│         └─ Name might be common? scripts/impact_guard.py   [Rule 6]
│
├─ "What does X do when it runs"
│    ├─ One hop:    get_callees(name)                        [precise]
│    └─ Full chain:  trace_flow(entry)                       [ordered]
│
├─ "Why did we choose..." / past decisions
│    └→ recall_memory(query)    [symbol/title-keyed, Rule on recall]
│
└─ Business rules / specs / policy / "impact of changing X" (PO view)
     └→ knowledge_search(query)
          └→ read affects_repos → cross_repo_deps + impact_analysis
```

## The precise/fuzzy axis (applies to all traversal tools)

`get_callers`, `get_callees`, and `impact_analysis` default to **precise** —
they only follow edges the resolver pinned to exactly one definition.

```
precise result empty?
   │
   ├─ Symbol is widely used?  → retry fuzzy=True, verify each hit    [Rule 8]
   └─ Symbol is a leaf/util?  → [] is probably real (unused)
```

| Precise (default) | Fuzzy (opt-in) |
|-------------------|----------------|
| Refactoring / signature changes | Dead-code hunting |
| Blast radius (ground truth) | Auditing all call sites of a common name |
| Within-repo dependencies | Exploring unfamiliar code |

**Empty precise ≠ "no callers"** — it means "no *resolvable* callers."
Before concluding a symbol is unused, always retry with `fuzzy=True`.

## Pre-edit checklist (always, in this order)

Before touching code, run this sequential chain — each step depends on the
previous:

```
1. ask_compass(file_path="<path>")   ← compass + wiki + memory for the file
2. find_definition(symbol)            ← pin down exactly what you're changing
3. get_callers(symbol)                ← who depends on it (precise)        [Rule 1]
4. cross_repo_deps(repo)              ← cross-repo blast radius
5. impact_analysis(symbol)            ← recursive, if the change is breaking [Rule 6]
```

Skip steps 4-5 only for non-breaking edits to a single file with no callers.
