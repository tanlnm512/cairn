# Proposal: Leveling Up Cairn for the Agentic Development Era

← [Docs index](README.md)

**Author:** Cairn analysis (agent-assisted)
**Date:** 2026-09-16
**Status:** Historical roadmap snapshot — current reference docs are authoritative

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Background & Current State](#2-background--current-state)
3. [Market Landscape & Trends](#3-market-landscape--trends)
4. [Problem Statement](#4-problem-statement)
5. [Proposed Capabilities](#5-proposed-capabilities)
6. [Architecture Impact](#6-architecture-impact)
7. [Competitive Analysis](#7-competitive-analysis)
8. [Roadmap & Prioritization](#8-roadmap--prioritization)
9. [Success Metrics & KPIs](#9-success-metrics--kpis)
10. [Risks & Mitigations](#10-risks--mitigations)
11. [Resource Requirements](#11-resource-requirements)
12. [Open Questions](#12-open-questions)
13. [References](#13-references)

---

## 1. Executive Summary

Cairn is a local-first codebase-intelligence system that provides AI coding
agents with a verifiable structural graph, hybrid semantic retrieval, tiered
memory, and LLM-assisted documentation — all grounded in a deterministic
SQLite store with trust-labeled resolution. Its core differentiator is the
**verification contract**: every edge is labeled `exact`/`ambiguous`/`unresolved`,
every synthesized doc is critic-gated against the graph, and the LLM never
sits in the query path.

The agentic-development landscape in 2025–2026 has shifted decisively toward:
remote OAuth-secured MCP servers, agent skills and structured context
engineering, self-improving feedback loops, multi-agent coordination, and
standardized agent benchmarks. Cairn's current architecture is well-positioned
to serve these trends but has gaps in deployment model, output format,
feedback automation, and benchmark credibility.

This proposal identifies **ten capability upgrades** across three priority
tiers, organized around a single strategic thesis: *become the trust layer for
agentic development — the context provider every agent calls once before
writing code, and the memory substrate that makes agents measurably better
over time.*

### Top-line asks

| Priority | Capability | Effort | Impact |
|----------|-----------|--------|--------|
| P0 | Remote MCP server with OAuth 2.1 | High | Unblocks enterprise & cloud-agent adoption |
| P0 | Agent Skills as first-class output | Low | Rides the hottest ecosystem pattern; low cost |
| P1 | Self-improving PR-review feedback loop | Medium | Creates the closed learning loop |
| P1 | Compact context pack (token-budgeted) | Medium | Best-in-class codebase context for agents |
| P1 | SWE-bench agent benchmark integration | Medium | Publishes a credible, comparable number |
| P2 | Multi-agent shared memory bus | Medium | Coordination substrate for parallel agents |
| P2 | Temporal knowledge graph for memory | Medium | Differentiator vs. Mem0/Letta/Zep |
| P2 | Dataflow-aware taint tracking | Medium | Security-aware blast radius |
| P3 | IDE extensions (VS Code, JetBrains) | High | Mainstream developer adoption |
| P3 | Cross-repo semantic federation | High | Organization-wide code intelligence |

---

## 2. Background & Current State

### 2.1 What Cairn does today

Cairn parses a workspace with tree-sitter into a SQLite symbol/call graph
(15 languages), layers hybrid retrieval (BM25 + embeddings + RRF fusion with
optional cross-encoder rerank) on top, and maintains durable knowledge in an
Open Knowledge Format (OKF) bundle. It serves AI agents through a 25-tool MCP
server and a 52-command CLI; humans get a local web dashboard.

The system is organized in four layers:

| Layer | Modules | What it provides |
|-------|---------|-----------------|
| **Graph** | `src/cairn/graph/` | Symbol/call graph, resolution labels, blast radius, dataflow, cross-repo deps |
| **Retrieval** | `src/cairn/retrieval/`, `src/cairn/graph/semantic.py` … | 3-stage hybrid search with gated 4th-stage rerank |
| **Knowledge & memory** | `src/cairn/knowledge/`, `src/cairn/memory/`, `src/cairn/okf/` | OKF doc store, tiered memory (raw → drafts → tribal → archived), promotion/decay |
| **Compass & wiki** | `src/cairn/compass/`, `src/cairn/wiki/`, `src/cairn/llm/tasks.py` | Navigation guides + architecture docs, LLM-synthesized via a critic-gated task queue |

### 2.2 Measured results (from `cairn bench --suite agent`)

Against a grep-and-read baseline on a 300-file corpus:

| Metric | Baseline | With Cairn | Reduction |
|--------|----------|-----------|-----------|
| Tokens per query | 217,187 | 1,146 | 99.5% |
| Tool calls per query | 153.2 | 1.5 | 99.0% |
| Wall-clock per query | 24.9 ms | 7.6 ms | 3.3× |

Depth-3 blast radius: **2 tool calls and 712 tokens** vs. **303 calls and
429,600 tokens** with grep.

### 2.3 Honest trade-offs acknowledged in the README

- Semantic retrieval is opt-in and mid-band (pooled recall/MRR at
  0.4174 / 0.2862 — below the 0.50 / 0.33 targets).
- The `[semantic]` extra pulls sentence-transformers + torch (~836 MB model).
- Compass/wiki generation needs LLM access; it won't run purely locally
  without a model.
- Rust support is generic-tier with unresolved call edges.
- Only local stdio/SSE transports for MCP; no remote/OAuth deployment model.

### 2.4 Verification contract (the moat)

Three machine-checked promises:

1. Every `exact` edge is actually resolved (`target_id IS NOT NULL`); an
   invariant test guards this on every build.
2. Every symbol in a compass/wiki/memory doc exists in the graph (deterministic
   critic fact-checks before write).
3. Every answer is re-derivable from local data; the LLM is never in the query
   path.

Resolution labels are the evidence: `exact` (trusted, pinned to one
definition), `ambiguous` (resolver declined to guess), `unresolved`
(external/stdlib). 82% of fuzzy results for common names are name-collision
noise that precise mode excludes.

### 2.5 Current deployment model

- **100% local** — one SQLite store under `~/.cairn`; no network calls.
- MCP server runs as stdio per-client spawn (or SSE daemon on `:9876`).
- `cairn install-agents` wires 9+ AI clients (Claude Code, Cursor, ZCode,
  Droid, Claude Desktop, opencode, agy, kilo, omp).
- Platforms: anywhere Python ≥ 3.10 runs; CI tests 3.10–3.14 on macOS + Linux.

---

## 3. Market Landscape & Trends

### 3.1 Remote MCP servers with OAuth 2.1 are the 2026 default

Major platforms (GitHub, Vercel, Linear, Notion, Supabase, Stripe, Figma) now
publish OAuth-secured hosted MCP endpoints. MCP's current spec (November 2025)
replaced legacy SSE with Streamable HTTP and specifies OAuth 2.1 with PKCE for
remote servers [6-Apify][7-WorkOS]. Only 8.5% of the 3,012 registered MCP
servers used OAuth as of March 2026 [5-NimbleBrain] — the ecosystem is still
in transition, but the direction is clear.

Cairn's stdio-only model works for single-developer local use but blocks:
- Team deployments (one shared cairn store serving an org)
- Cloud agents (Codex Work, ChatGPT) that cannot reach `localhost`
- Enterprise security sign-off (no OAuth = no procurement)

Sourcegraph 7.0 exemplifies the direction: it now serves as a "shared
intelligence layer" with Deep Search exposed via its MCP server [SG-Blog],
and cross-tool comparisons consistently rank MCP-native code-intelligence
tools as the 2026 category to watch [Sentra][Pharaoh].

### 3.2 Agent Skills and context engineering

Anthropic's Claude Skills introduced a 3-layer context management system that
lets agents load domain-specific instructions on demand without breaching
context limits [3-LinkedIn-CE]. Context engineering — the discipline of
structuring what goes into the agent's context window — is now considered the
successor to prompt engineering [1-Fowler][6-Anthropic-CE]. Tools like AGENTS.md,
Ruler, and Packmind have emerged to codify project conventions for agents [5-Packmind].

Cairn's compass and wiki are essentially pre-packaged context — but they
aren't emitted in the Skill format that agents can load on demand.

### 3.3 Self-improving agents through accumulated behavioral rules

A 2026 arXiv paper (arXiv:2607.13091) presents a closed-loop framework where
every accepted review comment is codified as a persistent behavioral rule,
progressively expanding the agent's capability without retraining [1-arxiv-si].
This pattern — write code → review → feedback becomes memory → next iteration
is better — is the agentic equivalent of the build-measure-learn loop.

Cairn's memory system (decisions, patterns, mistakes, workarounds — all
symbol-keyed) is architecturally positioned for this but has no automated
feedback loop connecting PR outcomes to memory writes.
Drop-in implementations of the same loop pattern already exist as open-source
projects [2-BerriAI][3-MindStudio].

### 3.4 Multi-agent coordination and shared memory

Claude Code, Codex, and other tools now support spawning parallel sub-agents.
Agent-MCP provides a framework for coordinated multi-agent AI collaboration
through MCP [2-rina]. The "shared brain for AI coding agents" pattern — a
knowledge graph backing multiple agents on the same codebase — reported ~20×
token reduction for code exploration [4-reddit].

Cairn's memory is per-workspace and has no inter-agent coordination layer.

### 3.5 Code-review automation with full-codebase indexing

Greptile indexes the entire codebase and reviews each PR against that context,
catching bugs in seams between files, services, and shared libraries [5-Greptile].
CodeRabbit, DeepSource, and others follow similar approaches [7-devdigest][2-Optimal].
This is a proven monetization surface — teams pay for PR review that
understands the full codebase, not just the diff.

### 3.6 Cross-repo context for agents

Riftmap documented the finding that AI coding agents need cross-repo
dependency context to ship safely; teams that built dependency-graph
substrates saw measurable improvements in agent output quality [7-Riftmap].
Cairn's `cross_repo_deps` maps structural dependencies but doesn't do
semantic federation across repos.

### 3.7 PageRank-based repo maps (Aider) and graph-boosted benchmarks (RepoGraph)

Aider's tree-sitter repo map with graph-ranked symbol selection (commonly
described as PageRank-style; Aider's own post calls it a "graph ranking
algorithm") remains one of the best context mechanisms in any coding agent
[1-Aider][3-GitHub-PageRank][9-PythonAlchemist].
The RepoGraph paper (ICLR 2025, cited in [1-arxiv-cm]) showed that repository-level code graphs boost
existing agents by **+32.8% relative improvement on SWE-bench**.

Cairn's `repo_map` lists directory clusters and hubs but doesn't produce a
token-budgeted, graph-ranked context pack.

### 3.8 Agent memory benchmarks and temporal knowledge graphs

Mem0, Zep (with Graphiti), Letta, and Cognee are being compared head-to-head
on retention, footprint, and precision [1-devdigest][5-puppyone]. Zep's
bi-temporal graph model (facts carry both "when it was true" and "when we
learned it") scores 71.2 on LongMemEval in Mem0's head-to-head comparison
(Zep's own LoCoMo results reach 80–83% config-dependent) [2-Mem0-State].
Mem0 reports ~6,900 tokens per query on its current algorithm — all
memory-benchmark numbers above are vendor self-reported and should be treated
as directional, not authoritative [2-Mem0-State].

Cairn's memory has decay and promotion lifecycles but no explicit temporal
reasoning (no `valid_from` / `valid_until`, no `--as-of` queries).

### 3.9 Dataflow and taint analysis for AI-assisted development

As agents write more code, integrating static security analysis (taint
tracking, injection path detection) into the AI workflow is a growing need
[9-Autonomous][6-AgentPatterns]. Cairn's `dataflow.py` module already computes precomputed impact
tables; extending it to taint propagation is a natural evolution.

### 3.10 Anthropic's agentic coding trends

Anthropic's "2026 Agentic Coding Trends Report" positions 2025 as the year
agentic AI changed how developers write code, and 2026 as the year systemic
effects emerge [9-Anthropic]. CLI agents are fundamentally changing development
workflows [3-Firecrawl-trends]. Gartner projects 40% of enterprise applications
will integrate task-specific AI agents by end of 2026, up from <5% [2-Mem0-State].

---

## 4. Problem Statement

Cairn has built a strong technical foundation (verified graph, hybrid
retrieval, grounded memory, critic-gated docs) but faces four strategic gaps
that limit its addressable market and competitive position:

### Gap 1: Deployment model limits reach

The stdio-only MCP server and local-only SQLite store prevent team deployments,
cloud-agent integrations, and enterprise procurement. The ecosystem has moved
to OAuth-secured remote MCP servers; Cairn hasn't.

### Gap 2: Output format is tool-shaped, not agent-shaped

Compass, wiki, and memory exist as queryable stores. Agents must learn to
query them across multiple round trips. The emerging pattern is to pre-package
context as Skills that agents load on demand — a format Cairn doesn't emit.

### Gap 3: No closed learning loop

Cairn's memory system can store decisions, patterns, and mistakes, but nothing
automatically connects PR review outcomes back to memory writes. The
self-improving agent pattern (review comment → behavioral rule) is proven and
Cairn is architecturally ready for it but hasn't built the loop.

### Gap 4: No standard-benchmark credibility

The 99.5% token-reduction claim is measured on Cairn's own corpus. The
RepoGraph paper published +32.8% on SWE-bench — a standard, reproducible
benchmark that the community trusts. Without a comparable number, Cairn's
claims are self-referential.

---

## 5. Proposed Capabilities

### 5.1 Remote MCP server with OAuth 2.1 (P0)

**What:** Add `cairn serve --remote` with Streamable HTTP transport (the
current MCP spec's remote transport; legacy SSE was replaced in the November
2025 spec) and OAuth 2.1 + PKCE authorization. Support per-workspace
authorization scoping.

**Why now:** Remote MCP with OAuth is the 2026 default [7-WorkOS][6-Apify].
Cairn's stdio-only model blocks cloud agents, team deployments, and enterprise
security review. Only 8.5% of MCP servers use OAuth today [5-NimbleBrain] —
early movers gain mindshare.
Implementer experience reports highlight OAuth-for-agents as the hardest
unguided part of shipping a remote MCP server [3-Semaphore].

**Design sketch:**
- Transport: Streamable HTTP (the MCP spec's recommended remote transport)
- Auth: OAuth 2.1 with PKCE; support external IdP (Auth0, WorkOS, or self-hosted)
- Multi-tenancy: workspace path → authorized principals mapping
- Deployment: single binary (`cairn serve --remote --port 8443`) or container
- Local mode remains unchanged; remote is additive, not a replacement

**Impact on verification contract:** Unchanged. The remote server reads the
same SQLite store; resolution labels and critic gates are transport-agnostic.

**Effort estimate:** 6–8 weeks (transport + auth + multi-workspace routing +
CI + docs).

### 5.2 Agent Skills as first-class output (P0)

**What:** `cairn skill generate <module-or-area>` produces a packaged
`SKILL.md` (and supporting reference files) that any Claude-Skills-compatible
agent loads on demand.

**Why now:** Claude Skills and the broader "context engineering" movement are
the hottest ecosystem pattern [3-LinkedIn-CE][1-Fowler]. Cairn's compass and
wiki are essentially pre-packaged context — emitting them as Skills is a
format conversion, not a new capability. Low effort, high leverage.

**Design sketch:**
- Input: a module name, directory prefix, or explicit symbol list
- Content: compass body + key symbols (top-K by centrality) + relevant
  memory (top-N by symbol overlap) + a one-line "when to load this" header
- Output: a `SKILL.md` with frontmatter (name, description, triggers) plus
  optional reference files for deeper detail
- Deterministic generation (no LLM in the default path); LLM-assisted polish
  via the task queue with the critic gate, same as compass/wiki

**Effort estimate:** 2–3 weeks.

### 5.3 Self-improving PR-review feedback loop (P1)

**What:** An automated closed loop: PR diff → cairn computes blast radius and
gathers relevant memory/compass → reviewing agent gets full structural context
→ accepted review comments are recorded as memory → next PR benefits.

**Why now:** The self-improving agent pattern is proven [1-arxiv-si]. Cairn's
memory system is the perfect substrate. This is a monetizable feature (code
review is a paid surface — Greptile, CodeRabbit [5-Greptile]) and a
differentiator: Cairn's review is grounded in a verified graph, not just
an LLM reading the diff.

**Design sketch:**
- `cairn review --base <ref>`: computes `cairn blast` on the diff, then
  enriches with relevant memories, compass entries, and wiki sections for the
  affected symbols. Outputs a structured review-context pack.
- `cairn review --pre-submit`: runs before an agent opens a PR; checks the
  diff against accumulated behavioral rules (memories of type `mistake` and
  `pattern` keyed to affected symbols). Warns about known mistakes.
- **Auto-capture hook** (CI or CLI): when a review comment is resolved
  (accepted), record it as a memory (`mistake` or `pattern`), keyed to the
  affected symbols, with a link to the PR thread.
- **Integration:** GitHub Action / pre-push hook that calls `cairn review
  --pre-submit` and posts findings as PR comments.

**The closed loop:**
1. Agent writes code → `cairn review --pre-submit` checks against memory
2. PR opens → reviewer uses `cairn review --base main` for structural context
3. Review comments resolved → `cairn memory record` captures the learning
4. Next PR → pre-submit guard surfaces the captured learnings

**Effort estimate:** 4–6 weeks (review command + memory hook + GitHub Action).

### 5.4 Compact context pack (P1)

**What:** `cairn pack --task "<description>" --budget <tokens>` produces a
self-contained context pack: relevant source snippets + blast radius + compass
excerpt + relevant memories — ranked by structural centrality and fitted to a
token budget.

**Why now:** Aider's PageRank repo map remains best-in-class for compact
codebase context [1-Aider][3-GitHub-PageRank]. Cairn has the data to beat it:
resolution-labeled edges (filter out noise), precomputed transitive impact
(centrality for free), semantic search (find relevant code), memory and
compass (add tribal knowledge). No other tool combines all of these.

**Design sketch:**
1. Parse the task description → extract key terms → `semantic_search` for
   candidate symbols
2. Expand candidates via the call graph (callers + callees within N hops)
3. Rank the expanded set by structural centrality (in-degree × out-degree from
   precomputed `transitive_edges`)
4. Select top-K symbols that fit the budget; include:
   - Verbatim source (trimmed to signature + key body)
   - Blast-radius summary (depth-2, precise)
   - Compass excerpt for the containing module
   - Top-3 relevant memories
5. Emit as a single markdown block the agent can insert into its context

**Effort estimate:** 3–4 weeks.

### 5.5 SWE-bench agent benchmark integration (P1)

**What:** Add a `cairn bench --suite swe-bench` arm that measures: given a
SWE-bench issue, how much more efficiently does an agent solve it with Cairn
vs. without?

**Why now:** The RepoGraph paper published +32.8% relative improvement on
SWE-bench with repo-level code graphs [1-arxiv-cm]. This is the credibility
benchmark the community trusts. Without a standard-benchmark number, Cairn's
claims are self-referential.

**Design sketch:**
- Benchmark arm: run a deterministic agent loop (no LLM) on SWE-bench tasks
  with and without Cairn's tools; measure token usage, tool calls, and
  resolution rate
- Optionally: a "with-LLM" arm using a standard agent framework (e.g.,
  SWE-agent or OpenHands) to measure end-to-end resolve-rate improvement
- Publish results in the README alongside the existing `cairn bench` numbers
- CI job to prevent rot (same pattern as `test_self_demo.py`)

**Effort estimate:** 3–4 weeks (deterministic arm) + 2–3 weeks (LLM arm).

### 5.6 Multi-agent shared memory bus (P2)

**What:** Enable multiple agents working on the same codebase to share memory
and coordinate through Cairn's store.

**Why now:** Multi-agent development is growing rapidly (Claude Code
sub-agents, Codex forked threads, Agent-MCP [2-rina]). The "shared brain"
pattern showed ~20× token reduction (builder-reported; not independently
verified) [4-reddit]. Cairn is architecturally ready (single SQLite store,
symbol-keyed memory) but has no inter-agent coordination layer.

**Design sketch:**
- `cairn memory share --agent <id> --symbols <list>`: one agent broadcasts
  what it learned to other agents working on overlapping code
- **Conflict detection**: before an agent edits a set of symbols, check if
  another agent has recently touched or recorded memory for overlapping
  symbols; surface the overlap via `impact_analysis`
- **Memory subscriptions** (MCP resources): agents subscribe to symbol-level
  memory changes; on next `recall_memory` for those symbols, newly-shared
  entries surface
- No breaking changes: existing single-agent use is unchanged

**Effort estimate:** 4–5 weeks.

### 5.7 Temporal knowledge graph for memory (P2)

**What:** Add bi-temporal semantics to Cairn's memory: each memory record
carries `valid_from` and `valid_until` timestamps, plus a `--as-of <date>`
query option for point-in-time recall.

**Why now:** Zep's bi-temporal graph model scores highest on agent memory
benchmarks (71.2% [9-Medium-compare]). Temporal reasoning — knowing what was
true at a point in time — is crucial for understanding why a decision was
made, especially in codebases where symbols get renamed and refactored.

**Design sketch:**
- Schema: add `valid_from` (default: creation time) and `valid_until`
  (nullable) to the memory table
- **Auto-invalidation:** when a build detects that a memory's referenced
  symbol has been renamed or removed, set `valid_until` to the build time
  rather than deleting; auto-link to the successor symbol via the graph
- `recall_memory --as-of <date>`: return only memories valid at that date
- Default recall: only currently-valid memories (backwards-compatible)
- `cairn memory timeline <symbol>`: show the temporal history of memories
  for a symbol

**Effort estimate:** 3–4 weeks.

### 5.8 Dataflow-aware taint tracking (P2)

**What:** Extend `dataflow.py` to propagate taint labels through the call
graph, enabling security-aware queries: "which paths carry user-controlled
data to a SQL execution sink?"

**Why now:** As agents write more code, integrating static security analysis
into the AI workflow is a growing need [9-Autonomous][6-AgentPatterns].
Cairn's call graph + dataflow tables are the right substrate; adding taint
sources/sinks is incremental.

**Design sketch:**
- **Sources** (configurable): HTTP request params, CLI args, environment
  variables, file reads, external API responses
- **Sinks** (configurable): SQL execution, shell commands, filesystem
  writes, network sends, eval/deserialization
- **Propagation:** inter-procedural taint analysis on the call graph;
  leverage the existing `dataflow` precomputed tables for O(1) multi-hop
- `cairn taint --from "<source-pattern>" --to "<sink-pattern>"`: trace and
  display the taint path
- Surface in `explore` and `blast` results: a warning badge when the changed
  code touches a taint path

**Effort estimate:** 4–6 weeks.

### 5.9 IDE extensions (VS Code, JetBrains) (P3)

**What:** Native IDE extensions that show inline blast radius on hover,
surface compass/memory when opening a file, and render the graph in a native
panel.

**Why:** The dashboard is a good start but isn't inline. Developers who never
touch a CLI or MCP config need a zero-config visual surface. VS Code has the
largest install base; JetBrains covers enterprise Java/Kotlin teams.
Current editor comparisons show context control and agentic tooling as the
deciding features for developer adoption [MindStudio-IDE].

**Design sketch:**
- VS Code extension: hover provider (show blast radius on symbol hover),
  code-lens (show caller/callee counts), tree-view panel (explore graph),
  decoration (highlight taint paths)
- Communication: local HTTP to `cairn serve` (or a lightweight sidecar)
- JetBrains: IntelliJ Platform SDK plugin with equivalent features

**Effort estimate:** 8–12 weeks per IDE (can be staggered).

### 5.10 Cross-repo semantic federation (P3)

**What:** Query across all registered workspace stores simultaneously —
"which repos have authentication logic similar to ours?" — with per-repo
attribution.

**Why:** Cross-repo context is a confirmed need for agents [7-Riftmap].
Cairn's `cross_repo_deps` maps structural dependencies; semantic federation
extends this to meaning-level search. This is Sourcegraph's territory at
enterprise scale, but Cairn's angle is local-first and free.

**Design sketch:**
- Federated `semantic_search` that queries all registered stores and merges
  results with per-repo attribution
- Optional embedding-server federation (share a single embedding backend
  across stores)
- `cairn ask --all-repos "<question>"` for cross-repo natural-language queries

**Effort estimate:** 5–7 weeks.

---

## 6. Architecture Impact

### 6.1 What doesn't change

- **The verification contract** (resolution labels, critic gate, LLM-not-in-
  query-path) is preserved across all proposals. Remote MCP reads the same
  store; Skills are deterministic output; the review loop records through the
  existing memory API; the context pack is pure graph + retrieval.
- **The local-first story** remains the default. Remote serving, IDE extensions,
  and federation are additive opt-ins.
- **The SQLite schema** evolves additively (new columns, new tables); no
  destructive migrations.

### 6.2 What changes

| Component | Change | Risk |
|-----------|--------|------|
| `mcp_server/` | Add HTTP/SSE transport alongside stdio; add auth middleware | Medium — new attack surface; mitigate with loopback default + OAuth |
| `graph/schema.py` | Add temporal columns to memory tables | Low — additive |
| `graph/dataflow.py` | Add taint propagation pass | Medium — correctness of taint propagation; mitigate with tests against known CWE patterns |
| New: `skills/` | Skill generation module | Low — reads existing compass/wiki/memory |
| New: `review/` | PR-review context pack generation + memory hook | Low-medium |
| New: `pack/` | Context-pack builder | Low-medium |
| `bench/` | Add SWE-bench suite | Low |
| `cli/` | New commands (`skill`, `review`, `pack`, `taint`) | Low |
| New: `ide/` | VS Code / JetBrains extension codebases | Separate repos; low risk to core |

### 6.3 Dependency impact

Per Constitution C-03, every new runtime dependency requires a `D-###`
decision. Anticipated additions:

| Capability | New dep | Alternative |
|-----------|---------|-------------|
| Remote MCP | `uvicorn` (already present for dashboard), `authlib` | Hand-rolled OAuth (not recommended) |
| Taint tracking | None (pure graph traversal) | — |
| Skills / pack / review | None (reads existing stores) | — |
| SWE-bench | `swebench` (benchmark harness, dev-only) | Vendored harness |
| IDE extensions | None on the Python side | — |

---

## 7. Competitive Analysis

### 7.1 Landscape map

| Tool | Graph | Semantic | Memory | Verification | Local-first | MCP |
|------|-------|----------|--------|--------------|-------------|-----|
| **Cairn** | ✅ resolution-labeled | ✅ opt-in hybrid | ✅ code-grounded, symbol-keyed | ✅ critic-gated | ✅ | ✅ 25 tools |
| Sourcegraph (Cody/Amp) | ✅ | ✅ | ❌ | ❌ | ❌ (SaaS) | ✅ |
| Cursor | ❌ (embeddings only) | ✅ | ❌ | ❌ | ❌ (SaaS) | ❌ |
| Greptile | ✅ (proprietary) | ✅ | Partial (Learning) | ❌ | ❌ (SaaS) | ❌ |
| Aider | ✅ (tree-sitter) | ❌ | ❌ | ❌ | ✅ | ❌ |
| Augment Code | ❌ | ✅ | ❌ | ❌ | ❌ (SaaS) | ❌ |
| Codebase-Memory MCP | ✅ (tree-sitter) | ❌ | ✅ | ❌ | ✅ | ✅ |
| Mem0 | ❌ | ✅ | ✅ (general) | ❌ | Both | ✅ |
| Zep/Graphiti | ✅ (temporal) | ✅ | ✅ (temporal) | ❌ | ❌ (SaaS) | ✅ |
| Letta | ❌ | ✅ | ✅ (agent-level) | ❌ | ✅ | ✅ |

Cairn's unique intersection: **structural graph + code-grounded memory +
verification contract + local-first + MCP-native**. No other tool occupies
this cell.

### 7.2 Where competitors are ahead

| Gap | Who's ahead | What they have |
|-----|-------------|---------------|
| Remote MCP + OAuth | Sourcegraph, Zep, Mem0 | Hosted OAuth endpoints |
| Agent Skills output | Anthropic (native), Packmind, Ruler | Skill-format generators |
| PR-review monetization | Greptile, CodeRabbit | Full-codebase-indexed review as a service |
| Standard-benchmark credibility | RepoGraph (academic), Aider (community) | Published SWE-bench / HumanEval numbers |
| IDE integration | Cursor, Sourcegraph, Copilot | Native VS Code / JetBrains extensions |
| Temporal memory | Zep/Graphiti | Bi-temporal knowledge graph, 71.2% benchmark |

### 7.3 Where Cairn is ahead

| Advantage | Why it matters | No competitor has |
|-----------|---------------|-------------------|
| Resolution-labeled edges (`exact`/`ambiguous`/`unresolved`) | Trust-labeled blast radius; precise mode excludes 82% noise | All competitors output unlabeled results |
| Critic-gated synthesized docs | Hallucinated references are rejected before write | No other tool fact-checks LLM output against a graph |
| LLM never in query path | Deterministic, reproducible, auditable answers | Sourcegraph/Cursor/Greptile all use LLM in query |
| 100% local + no telemetry | Privacy-sensitive enterprises, air-gapped environments | Sourcegraph/Augment/Mem0 are SaaS-first |
| Token-efficiency benchmarks (99.5% reduction) | Quantified agent-cost savings | Few tools publish reproducible benchmark arms |

---

## 8. Roadmap & Prioritization

### Phase 1: Foundation (Weeks 1–10) — "Unblock adoption"

| Week | Deliverable |
|------|-------------|
| 1–2 | `cairn skill generate` — agent Skills output (P0) |
| 3–8 | Remote MCP server: Streamable HTTP + OAuth 2.1 + workspace auth (P0) |
| 9–10 | `cairn pack` MVP: semantic search → graph expansion → token-budgeted output (P1) |

**Milestone:** An agent can connect to Cairn remotely, load a pre-packaged
Skill for its current module, and get a one-shot context pack for its task.

### Phase 2: Intelligence loop (Weeks 11–18) — "Close the loop"

| Week | Deliverable |
|------|-------------|
| 11–14 | `cairn review` + pre-submit guard + memory auto-capture hook (P1) |
| 15–17 | SWE-bench benchmark arm + publish results (P1) |
| 18 | `cairn pack` v1.1: compass + memory enrichment in the pack |

**Milestone:** A PR-review agent uses Cairn for structural context; accepted
review comments become persistent behavioral rules; a SWE-bench number is
published in the README.

### Phase 3: Coordination & depth (Weeks 19–30) — "Differentiate"

| Week | Deliverable |
|------|-------------|
| 19–22 | Multi-agent shared memory bus (P2) |
| 23–25 | Temporal knowledge graph for memory (P2) |
| 26–30 | Dataflow-aware taint tracking MVP (P2) |

**Milestone:** Multiple agents on the same codebase share memory through
Cairn; memory has temporal validity; taint paths surface in blast results.

### Phase 4: Reach (Weeks 31+) — "Mainstream"

| Deliverable |
|-------------|
| VS Code extension (P3) |
| Cross-repo semantic federation (P3) |
| JetBrains extension (P3) |

### Timeline visualization

```
Weeks:  1---2---3---4---5---6---7---8---9---10--11--12--13--14--15--16--17--18--19--20--21--22--23--24--25--26--27--28--29--30--31+
Ph1:    [==Skills==][======Remote MCP======][=Pack=]
Ph2:                                        [===Review loop===][SWE-bench][Pack]
Ph3:                                                                      [=Shared mem=][==Temporal==][===Taint===]
Ph4:                                                                                                                           [IDE, Fed]
```

---

## 9. Success Metrics & KPIs

### 9.1 Adoption metrics

| Metric | Current | Target (6 months) | How to measure |
|--------|---------|-------------------|----------------|
| PyPI downloads/month | (baseline) | 2× baseline | PyPI stats |
| GitHub stars | (baseline) | 2× baseline | GitHub API |
| `cairn install-agents` completions | (baseline) | Track via local telemetry (opt-in) | `telemetry` table |
| Remote-server deployments | 0 | ≥ 100 self-hosted | Opt-in heartbeat |

### 9.2 Capability metrics

| Metric | Current | Target | How to measure |
|--------|---------|--------|----------------|
| Token reduction vs. grep (agent suite) | 99.5% | Maintain ≥ 99% | `cairn bench --suite agent` |
| SWE-bench improvement (with vs. without Cairn) | Not measured | Publish a number | `cairn bench --suite swe-bench` |
| Semantic recall/MRR | 0.42 / 0.29 | ≥ 0.50 / 0.33 | `cairn eval` |
| Memory precision (relevant results / total) | Not measured | ≥ 0.80 | New eval arm |
| Context-pack fit rate (useful context within budget) | N/A | ≥ 0.85 | Agent-suite with pack |

### 9.3 Quality metrics

| Metric | Current | Target | How to measure |
|--------|---------|--------|----------------|
| Exact-edge resolution rate | Tracked by invariant | Maintain / improve | SQLite invariant test |
| Critic-gate rejection rate (hallucinated refs) | Tracked | No regression | Critic logs |
| `cairn doctor` checks | 11 | 13 (add remote-health, taint-consistency) | `cairn doctor` |

---

## 10. Risks & Mitigations

### 10.1 Technical risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Remote MCP auth implementation is complex and error-prone | Medium | High | Use a proven OAuth library (`authlib`); follow MCP auth spec closely; start with loopback-only remote; pen-test before launch |
| Taint-tracking false positives erode trust | Medium | Medium | Conservative (opt-in) taint propagation; only surface in `explore`/`blast` when confidence is high; extensive tests against known CWE patterns |
| SWE-bench integration is brittle (upstream format changes) | Medium | Medium | Pin the SWE-bench version; treat the harness as dev-only, not a runtime dep |
| Multi-agent memory introduces race conditions | Medium | High | SQLite WAL mode + explicit locking; per-agent memory namespaces with read-through sharing |
| Temporal queries slow down memory recall | Low | Medium | Index on `valid_from`/`valid_until`; benchmark before/after |
| Context-pack ranking produces irrelevant results | Medium | Medium | Iterative eval: rank quality measured by agent task completion, not just cosine similarity; tune centrality weights |

### 10.2 Strategic risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| A major platform (Cursor, Sourcegraph) ships Cairn-equivalent graph+memory+verification | Medium | High | Move fast on the unique intersection (critic gate, resolution labels, local-first); publish benchmarks; build community |
| MCP ecosystem standardizes on a format that makes Cairn's tools redundant | Low | High | Cairn's value is in the store (verified graph + memory), not just the MCP surface; it can serve via any future protocol |
| Remote-server feature dilutes the "100% local" brand | Medium | Medium | Local remains the default; remote is explicitly opt-in (`cairn serve --remote`); docs lead with local |
| Scope creep delays shipping | High | Medium | Strict phased roadmap; each phase has a clear milestone; resist adding features mid-phase |

### 10.3 Operational risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Solo maintainer bandwidth | High | High | Prioritize P0–P1; P2–P3 are stretch goals; consider co-maintainers |
| Dependency additions increase wheel/platform complexity | Medium | Medium | Per C-03: every new dep gets a `D-###` decision; prefer optional extras |

---

## 11. Resource Requirements

### 11.1 Engineering effort

| Phase | Duration | Scope | Engineer-months |
|-------|----------|-------|-----------------|
| Phase 1 | 10 weeks | Skills + Remote MCP + Pack MVP | 2.5 EM |
| Phase 2 | 8 weeks | Review loop + SWE-bench + Pack v1.1 | 2 EM |
| Phase 3 | 12 weeks | Shared memory + Temporal + Taint | 3 EM |
| Phase 4 | 8+ weeks | IDE extensions + Federation | 4+ EM (separate repos) |
| **Total (Phases 1–3)** | **30 weeks** | | **~7.5 EM** |

### 11.2 Infrastructure (if pursuing remote)

| Item | Cost |
|------|------|
| OAuth provider (Auth0 / WorkOS free tier) | $0 up to 7K MAU (Auth0) or WorkOS free tier |
| Demo / docs hosting (remote instance) | ~$10/mo (small VPS) |
| SWE-bench eval compute | ~$50–200 one-time (LLM arm) |

---

## 12. Open Questions

1. **Remote MCP hosting model:** Should Cairn offer a hosted/managed remote
   instance (SaaS), or only self-hosted? The former requires infrastructure
   and a business model; the latter is simpler but limits adoption.
2. **Skill format standardization:** Will Anthropic's Skill format become a
   cross-agent standard, or should Cairn emit a neutral format (e.g., a
   structured markdown block) that works across agents?
3. **SWE-bench arm design:** Deterministic (no LLM, measuring token/tool
   efficiency) vs. LLM-in-the-loop (measuring end-to-end resolve rate)?
   The former is reproducible in CI; the latter is more credible.
4. **Taint source/sink configuration:** Ship with a default config for common
   frameworks (Express, Django, Rails, Spring), or require explicit
   configuration? Defaults improve onboarding but risk false positives.
5. **IDE extension priority:** VS Code first (largest install base) or
   JetBrains first (enterprise Java/Kotlin teams where Cairn's graph is
   strongest)?
6. **Multi-agent identity:** How do agents identify themselves? MCP session
   IDs? A `cairn agent register` command? This affects the shared-memory
   design.

---

## 13. References

### Market landscape & trends

[3-Firecrawl-trends] Firecrawl, "Top 15 Agentic AI Trends to Watch in 2026" (Jul 2026).
https://www.firecrawl.dev/blog/agentic-ai-trends

[9-Anthropic] Anthropic, "2026 Agentic Coding Trends Report" (Jan 2026).
https://resources.anthropic.com/hubfs/2026%20Agentic%20Coding%20Trends%20Report.pdf

[2-Mem0-State] Mem0, "State of AI Agent Memory 2026: Benchmarks & Trends Report" (Apr 2026).
https://mem0.ai/blog/state-of-ai-agent-memory-2026

### MCP ecosystem

[5-NimbleBrain] NimbleBrain, "The State of MCP Security: March 2026" (Mar 2026).
https://nimblebrain.ai/blog/state-of-mcp-security-2026/

[3-Firecrawl] Firecrawl, "10 Best MCP Servers for Developers in 2026" (Aug 2026).
https://www.firecrawl.dev/blog/best-mcp-servers-for-developers

[6-Apify] Apify, "The Complete MCP Server Handbook (April 2026)" (Apr 2026).
https://use-apify.com/blog/mcp-server-handbook-2026

[7-WorkOS] WorkOS, "Everything your team needs to know about MCP in 2026" (Mar 2026).
https://workos.com/blog/everything-your-team-needs-to-know-about-mcp-in-2026

[3-Semaphore] Semaphore, "MCP OAuth in Practice: Lessons from Building Authentication for AI Agents" (Mar 2026).
https://semaphore.io/blog/mcp-oauth-in-practice-lessons-from-building-authentication-for-ai-agents

[3-Firecrawl] Firecrawl, "10 Best MCP Servers for Developers in 2026" (Aug 2026).
https://www.firecrawl.dev/blog/best-mcp-servers-for-developers

### Context engineering & agent skills

[1-Fowler] Martin Fowler, "Context Engineering for Coding Agents" (Feb 2026).
https://martinfowler.com/articles/exploring-gen-ai/context-engineering-coding-agents.html

[3-LinkedIn-CE] Akshay Pachaar, "Context engineering in Claude Skills is GENIUS!" (Oct 2025).
https://www.linkedin.com/posts/akshay-pachaar_context-engineering-in-claude-skills-is-genius-activity-7388948667581284352-ZiX3

[5-Packmind] Packmind, "Best context engineering tools for AI coding in 2026".
https://packmind.com/context-engineering-ai-coding/best-context-engineering-tools/

[6-Anthropic-CE] Anthropic, "Effective context engineering for AI agents" (Sep 2025).
https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents

### Self-improving agents

[1-arxiv-si] "Self-Improving AI Coding Agents Through Accumulated Behavioral Rules" (arXiv:2607.13091, Jul 2026).
https://arxiv.org/abs/2607.13091

[2-BerriAI] BerriAI, "self-improving-agent" (GitHub).
https://github.com/BerriAI/self-improving-agent

[3-MindStudio] MindStudio, "Self-Improving AI Agents: What They Are, How They Work" (Apr 2026).
https://www.mindstudio.ai/blog/self-improving-ai-agent-feedback-loop

[6-AgentPatterns] AgentPatterns, "Agent Self-Review Loop for Iterative Self-Improvement".
https://agentpatterns.ai/code-review/agent-self-review-loop/

### Code graph & repo map research

[1-arxiv-cm] "Codebase-Memory: Tree-Sitter-Based Knowledge Graphs for LLM Code Exploration via MCP" (arXiv:2603.27277, Mar 2026).
https://arxiv.org/html/2603.27277v1

[1-Aider] Aider, "Building a better repository map with tree-sitter" (Oct 2023).
https://aider.chat/2023/10/22/repomap.html

[3-GitHub-PageRank] "PageRank Repo Map — Automatic Codebase Context Selection via Aider's tree-sitter graph" (GitHub issue, Mar 2026).
https://github.com/NousResearch/hermes-agent/issues/535

[9-PythonAlchemist] Python Alchemist, "Context Engineering for Coding Agents" (Mar 2026).
https://www.pythonalchemist.com/blog/context-engineering-coding-agents

### Agent memory

[1-devdigest] DevelopersDigest, "Best AI Agent Memory Providers in 2026: Mem0 vs Zep vs Letta vs..." (Jul 2026).
https://www.developersdigest.tech/blog/best-ai-agent-memory-providers-2026

[9-Medium-compare] Jarek Wasowski, "I Compared 5 AI Agent Memory Systems Across 6 Dimensions" (May 2026).
https://medium.com/@wasowski.jarek/i-compared-5-ai-agent-memory-systems-across-6-dimensions-none-wins-6a658335ed0a

[5-puppyone] PuppyOne, "Best AI Agent Memory Platforms 2026: Mem0 vs Zep vs Letta" (Apr 2026).
https://www.puppyone.ai/en/blog/best-ai-agent-memory-platforms

### Code review automation

[5-Greptile] Greptile, "AI Code Reviews that understand your entire codebase".
https://www.greptile.com/

[7-devdigest] DevelopersDigest, "Best AI Code Review Tools in 2026: CodeRabbit vs DeepSource vs Greptile" (Jun 2026).
https://www.developersdigest.tech/blog/best-ai-code-review-tools-2026

[2-Optimal] Optimal AI, "9 Best AI Code Review Tools 2026: Ranked & Compared" (Aug 2026).
https://getoptimal.ai/blog/best-ai-code-review-tools

[9-Autonomous] Zylos AI, "Autonomous Code Review: Multi-Agent Approaches to Pull Request Analysis" (Apr 2026).
https://zylos.ai/research/2026-04-22-autonomous-code-review-multi-agent-pr-analysis/

### Multi-agent coordination

[2-rina] rinadelph, "Agent-MCP" (GitHub).
https://github.com/rinadelph/Agent-MCP

[4-reddit] Reddit r/ClaudeAI, "I built a shared brain for AI coding agents — MCP tools, Neo4j, and 20x fewer tokens" (Feb 2026).
https://www.reddit.com/r/ClaudeAI/comments/1r74e12/

### Cross-repo context

[7-Riftmap] Riftmap, "AI Coding Agents Need Cross-Repo Context to Ship Safely" (May 2026).
https://riftmap.dev/blog/ai-coding-agents-need-cross-repo-context/

### Sourcegraph & competitive landscape

[SG-Blog] Sourcegraph, "A new era for Sourcegraph: The intelligence layer for AI coding agents and developers" (Feb 2026).
https://sourcegraph.com/blog/a-new-era-for-sourcegraph-the-intelligence-layer-for-ai-coding-agents-and-developers

[Sentra] Sentra, "Best Codebase Memory Tools for AI Agents (2026)".
https://www.sentra.app/articles/best-codebase-context-memory-tools

[Pharaoh] Pharaoh, "12 Codebase Intelligence Tool Comparison Picks for 2026" (Apr 2026).
https://pharaoh.so/blog/codebase-intelligence-tool-comparison-2026/

### IDE landscape

[MindStudio-IDE] MindStudio, "Best AI Code Editors in 2026: Cursor, Windsurf, Copilot, Claude Code" (Apr 2026).
https://www.mindstudio.ai/blog/best-ai-coding-editors

---

*All URLs accessed 2026-09-16. Some sources are blog posts and community
discussions; benchmark numbers from vendor blogs are self-reported. Academic
papers (arXiv) and official documentation (Anthropic, MCP spec) are treated
as higher-confidence sources.*
