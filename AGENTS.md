# Codebase Intelligence System

This workspace uses a local knowledge graph (cairn) for codebase intelligence.
All AI coding agents working in this workspace should use these tools.

## MCP Server
- Name: `cairn` (auto-connected at session start)
- Transport: stdio
- 28 tools across 4 layers: graph (9), knowledge base + compass (5), memory (8), knowledge (6)
  (`explore` is the recommended first call -- it aggregates the graph layer;
  `ask_compass` is the cross-layer router)

## Shipping a change — MANDATORY workflow (agent trigger)
TRIGGER: the moment you finish editing and are about to commit, push, or open a
PR — STOP and follow the shipping procedure end to end:
`branch → pre-commit run --all-files → conventional commit → push feature branch
→ PR (fill the audit checklist) → watch CI → fix on the same branch → post-merge
cairn update + record_memory`.

Hard rules (do not violate):
- Never push directly to `main` (it skips the PR-title/dependency-review gates and the review layer).
- Never `git commit --no-verify` past a pre-commit failure (that defeats Layer 0; only a human may decide to).
- Commit AND PR title must be conventional: `type(optional-scope): subject` (`feat fix chore docs ci refactor perf test build style revert`).
- Do not skip the PR template's audit checklist — it is the Layer 2-3 review gate.

The explore-first / before-editing / after-task sections below are the *how*;
the procedure above is the path for landing a change, and
`docs/review-checklist.md` is the review gate. CI
(Layers 0-1) enforces the automatable parts; this workflow covers the rest.

## Workflow: explore-first

### For almost any question -- "how does X work", a flow, surveying an area:
1. Call `explore(query)` FIRST. It returns matching symbols' verbatim source
   grouped by file, the call paths between them (including ambiguous dispatch
   hops), and a blast-radius summary -- one call, one answer.
2. Reach for the specific tools only to drill down when `explore` is thin:
   - `ask_compass(query)` -- cross-layer routing (graph + wiki + compass + memory)
   - `get_callers` / `get_callees` / `impact_analysis` -- deeper call-graph traversal
   - `search_knowledge` / `recall_memory` -- knowledge-layer questions `explore` doesn't cover

### When to escalate beyond explore (one trigger per tool)
`explore` makes three trade-offs by design. Escalate only when you hit a limit:

| explore's limit | You need... | Escalate to |
|-----------------|-------------|-------------|
| Blast radius is depth-2 only | Recursive callers (breaking change) | `impact_analysis(name)` + `cross_repo_deps(repo)` |
| Neighborhood is unordered | Execution order (what runs when) | `trace_flow(entry)` |
| Results are pure L1 structural | Why/decisions/wiki/tribal knowledge | `ask_compass(query)` or `recall_memory(query)` |
| FTS5 seeds are token-based | Meaning-based match (synonyms) | `semantic_search(query)` |

Escalations are additive -- call them *after* `explore` to go deeper, not
instead of it. `explore` already gave you the seed names and file locations
the escalation tools need.

### Before editing a file, ALWAYS:
1. Call `ask_compass(file_path="<path>")` to load compass + memory context
2. Call `find_definition` for any symbol you need to understand
3. Call `get_callers` to understand who depends on what you are changing (within-repo)
4. Call `cross_repo_deps(repo_name)` for cross-repo blast radius
5. Call `impact_analysis(symbol_name)` if making breaking changes (within-repo recursive)

### Resolution-aware querying (precise vs fuzzy)
`get_callers`, `get_callees`, and `impact_analysis` default to **precise**:
they only follow edges the resolver could pin to exactly one definition.

- **Empty precise result ≠ "no callers".** It means "no *resolvable* callers."
  Before concluding a symbol is unused, retry with `fuzzy=True`.
- **Precise is ground truth for blast radius** — not inflated by name collisions.
- **Fuzzy is a candidate list, not truth** — verify each against actual code.
  A fuzzy result for `invoke` can span 200+ sites across repos/languages that
  merely share the name.
- **`resolution` label:** `exact` = trusted; `ambiguous` = multiple candidates,
  resolver declined to guess; `unresolved` = external/stdlib.

When precise is right: impact, refactoring, signature changes.
When fuzzy is right: auditing, dead-code hunting, exploring unfamiliar code.

### When you need architectural context:
- Call `get_compass(module_name)` for a 25-35 line navigation guide
- Call `search_knowledge(query, type_filter="Wiki")` for feature/architecture documentation

### When you need past decisions:
- Call `recall_memory(query)` -- symbol/title-keyed, NOT full-text. Query by
  symbol name or title tokens ("ApiFactory", "backoff"), not natural language.

### After completing a task, ALWAYS:
1. Run `cairn update` to refresh the graph with your changes
2. If the task touched a performance or fallback path, run `cairn doctor`
   (exit 0 = PASS/WARN, 1 = at least one FAIL). On a non-zero exit, a
   degradation is active — record it as `record_memory(type="mistake")` and fix
   it before shipping.
3. Call `record_memory` for any learnings:
   - type="decision" for architectural choices made
   - type="pattern" for reusable code patterns discovered
   - type="mistake" for errors others should avoid
   - type="workaround" for non-obvious solutions used
4. Set confidence (0.0-1.0) based on how sure you are

## PR review (the audit gate)
Before requesting or approving review on a PR (feature, improvement, or bugfix),
follow `docs/review-checklist.md`. It uses cairn's own tools to verify, for
every change:
- **Blast radius** — `explore` + `impact_analysis` (and `cross_repo_deps` for
  public-API changes) on changed symbols.
- **Layering** — `ask_compass` on changed files against the documented architecture.
- **Post-task hygiene** — that `cairn update` and `record_memory` (above) actually
  ran, not just were claimed.

The PR template (`.github/PULL_REQUEST_TEMPLATE.md`) carries the author
checklist; `docs/review-checklist.md` is the procedure behind each box.
Layers 0-1 (pre-commit + CI: tests, pip-audit, bandit, mypy, PR-title, dependency-review) are automated;
this is the human/agent layer that catches what they can't.

## Tool Quirks (empirically verified)

| Tool | Behavior | Workaround |
|------|----------|------------|
| `ask_compass` | Routes correctly but returns empty body skeletons when wiki/compass coverage is thin. | Drill down with the specific layer tool; don't treat empty response as "no info exists". |
| `recall_memory` | Multi-token lexical matching, with a semantic fallback when lexical search comes up empty. | Natural-language and multi-token queries ("backoff retry policy") work, not just single symbol tokens. |
| `impact_analysis` | Within-repo by default, but includes cross-repo consumer reach in its output. Precise mode only follows resolved edges, so common names can under-report. | Pair with `cross_repo_deps(repo)` for the full picture. Use `fuzzy=True` when precise impact looks suspiciously small for a widely-used symbol. |
| `search_symbols` | FTS5 + phrase splitting handles underscored tokens (`*core_ui_v4*` matches). Substring and camelCase patterns also match via a LIKE-based pass (the FTS5 `*` wildcard is prefix-only and the tokenizer doesn't split camelCase, so non-prefix queries fall back to LIKE). | Wildcards and substring queries both work, on underscored and camelCase names. |
| `get_callers`/`impact_analysis` on a Kotlin class invoked via `operator fun invoke` | Bare calls of the standard Android UseCase idiom (`someUseCase(params)` against a DI-injected property) resolve to the callee's declared type. | Both bare `someUseCase(params)` and explicit-receiver `this.someUseCase(params)` shapes retarget correctly (regression-tested). |
| `semantic_search` | Defaults to RRF fusion (BM25 + vector, `CAIRN_FUSION=1` default): the returned `score` is a rank-fusion number (~0.01-0.02), not cosine similarity, regardless of the `threshold` argument. Real cosine scores (0.3-0.6+ for genuinely on-topic hits with `local`/`BAAI/bge-m3`) only show when fusion is off. | Rank order is meaningful either way. Set `CAIRN_FUSION=0` if you need the score to reflect actual match strength (e.g. deciding how confident a hit is), not just relative order. |
| `ann_backend_enabled` | On by default: `CAIRN_ANN_BACKEND` unset resolves to `sqlite-vec`. It degrades silently to the brute-force cosine scan if the extension fails to load. | Set `CAIRN_ANN_BACKEND=off` to force the brute-force scan. |

## LLM Task Queue (agent-decoupled synthesis)
Cairn never calls an LLM directly. To generate compass/wiki with LLM quality:
- `cairn task list --status pending` -- see queued work
- `cairn task show <id>` -> `cairn task claim <id>` -> `cairn task complete <id> --result-file <path>`
- The deterministic critic fact-checks every result; only graph-verified files/symbols allowed.

## Wiki (architecture documentation)
`cairn wiki generate --llm` queues page-writing tasks (a catalog refinement
task first with `--refine-catalog`); work them through the task queue:
- `cairn task claim <id>` -> write the page -> `cairn task complete <id> --result-file <path>`;
  the result body must end with a `## Sources` footer -- the deterministic critic
  fact-checks it against the graph before the page is promoted.
- `cairn wiki status` -- per-page state, derived at read time (planned / queued / in-progress / promoted / failed / dropped) with counts
- `cairn wiki retry` -- re-queue failed pages as fresh task chains
- `cairn wiki export --dir DIR` -- write every promoted page out as markdown
- `cairn wiki enrich [<page-id>]` -- queue an append-only extension of a promoted page
Consume the pages through the router: `ask_compass` reads wiki alongside
graph/compass/memory; `search_knowledge(query, type_filter="Wiki")` queries pages directly.

## CLI Fallback (if MCP tools are unavailable):
- `cairn def <symbol>` -- find definition
- `cairn callers <symbol>` -- who calls this
- `cairn impact <symbol>` -- what breaks if changed (within-repo)
- `cairn deps <repo>` -- cross-repo dependency map
- `cairn context <file>` -- load context for a file
- `cairn ask "<question>"` -- natural language query across all layers
- `cairn memory record <type> "<title>"` -- capture a learning

## Knowledge Files

The `.knowledge/` directory (in cairn/) contains OKF markdown files:
- `compass/` -- module navigation guides (25-35 lines each)
- `wiki/` -- architectural documentation
- `memory/tribal/` -- past decisions, patterns, mistakes
- `memory/raw/` -- ephemeral captures (do not read)
- `memory/drafts/` -- awaiting quality review (do not read)

You can read these files directly when MCP is unavailable.
