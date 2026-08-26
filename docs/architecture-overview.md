# Cairn System Architecture

← [Docs index](README.md)

> **Big-picture guide to how cairn fits together.** Layers, the interactions
> between them, the build and query flows, and the LLM boundary — with flow and
> sequence diagrams. For the per-module reference, see
> [architecture.md](architecture.md); for tool-level detail,
> [mcp-tools.md](mcp-tools.md).

## Table of contents

1. [System at a glance](#1-system-at-a-glance)
2. [The four tool layers](#2-the-four-tool-layers)
3. [How the layers interact](#3-how-the-layers-interact)
4. [Build flow: source → graph](#4-build-flow-source--graph)
5. [Query flow: agent → answer](#5-query-flow-agent--answer)
6. [The LLM boundary & critic loop](#6-the-llm-boundary--critic-loop)
7. [Memory lifecycle](#7-memory-lifecycle)
8. [Storage layout](#8-storage-layout)

---

## 1. System at a glance

cairn is a **local, structural, agent-first** codebase intelligence system.
It parses your source into a typed call graph in SQLite, then layers a
knowledge system on top (compass, wiki, memory, business knowledge). The same
store backs a `cairn` CLI for humans and a 27-tool MCP server for AI agents.

```mermaid
flowchart TB
    subgraph Clients["Clients"]
        H["Human<br/>cairn CLI"]
        A["AI Agent<br/>MCP server (27 tools)"]
    end

    subgraph Router["Routing"]
        EX["explore()<br/>graph aggregator"]
        AC["ask_compass()<br/>cross-layer router"]
    end

    subgraph Layers["5 Layers"]
        L1["L1 Graph<br/>9 tools"]
        L2["L2 Compass + KB<br/>5 tools"]
        L4["L4 Memory<br/>8 tools"]
        L5["L5 Knowledge<br/>5 tools"]
    end

    subgraph Store["Local store"]
        DB[(".kg<br/>SQLite + FTS5 + vectors")]
        KB[(".knowledge/<br/>OKF markdown")]
    end

    subgraph Build["Build pipeline"]
        P["tree-sitter parsers<br/>14 languages"]
        R["resolver<br/>5 tiers"]
        D["dataflow + closure<br/>precompute"]
    end

    H --> Router
    A --> Router
    Router --> Layers
    L1 --> DB
    L2 --> KB
    L2 -.bridges.-> L1
    L4 --> KB
    L4 -.verifies.-> L1
    L5 --> KB
    L5 -.bridges.-> L1
    P --> R --> D --> DB
    KB <--> DB
```

The defining property: **every output is verifiable.** Edges carry resolution
labels (`exact`/`ambiguous`/`unresolved`); LLM-synthesized docs are
fact-checked by a deterministic critic against the graph. See the
[Resolution model](architecture.md#resolution-model) for the deep dive.

---

## 2. The four tool layers

| Layer | Tools | Purpose | Backed by |
|-------|-------|---------|-----------|
| **L1 Graph** | 9 | The structural core: definitions, callers/callees, blast radius, semantic search, the `explore` aggregator, cross-repo deps, graph viz | `.kg` SQLite |
| **L2 Compass + KB + Router** | 5 | Module navigation guides (`get_compass`), KB search (`search_knowledge`), the cross-layer router (`ask_compass`), flow tracing/generation (`trace_flow`, `generate_flow`) | `.knowledge/` + `.kg` |
| **L4 Memory** | 8 | Tribal memory across tiers: record / recall / digest / promote / demote / evolve / decay / delete | `.knowledge/` + `.kg` (refs) |
| **L5 Knowledge** | 5 | Business docs + procedural workflows: add / search / delete / status / trace_workflow | `.knowledge/` |

> There is no separate "L3". The router (`ask_compass`) lives in the L2 group
> but acts as the cross-layer orchestrator; `explore` lives in L1 but acts as
> the graph aggregator. Together they're the two "front doors" for queries.

```mermaid
flowchart LR
    Q["Query"] --> G{Graph-shape<br/>or cross-layer?}
    G -- "structural: who calls X,<br/>blast radius, how does X work" --> EX["explore()<br/>L1 aggregator"]
    G -- "cross-layer: context for<br/>a file, mixed intent" --> AC["ask_compass()<br/>L2 router"]
    EX --> L1["L1 Graph<br/>(FTS5 + neighborhood +<br/>blast radius + ambiguous hops)"]
    AC --> L1A["L1"]
    AC --> L2A["L2 Compass/KB"]
    AC --> L4A["L4 Memory"]
    AC --> L5A["L5 Knowledge"]
    L1 --> ANS["One consolidated answer"]
    AC --> ANS
```

---

## 3. How the layers interact

The layers are **decoupled by storage**, not by network. L1 is the only layer
that owns source of truth (the graph); L2/L4/L5 are markdown on top of it, and
they *reach into* L1 for verification and bridging. There are exactly three
cross-layer interactions:

```mermaid
flowchart LR
    L1["L1 Graph"]
    L2["L2 Compass/KB"]
    L4["L4 Memory"]
    L5["L5 Knowledge"]

    L2 -- "1. Critic fact-checks<br/>compass/wiki refs<br/>against symbols+files" --> L1
    L4 -- "2. refs-verified fraction<br/>recomputed live each recall" --> L1
    L5 -- "3. knowledge_search bridges<br/>business docs to<br/>cross_repo_deps" --> L1
```

1. **L2 → L1 (critic gate).** When a compass or wiki concept is generated, the
   deterministic critic verifies every backtick-quoted file/symbol reference
   against the graph. A hallucinated symbol can't land in a doc.
2. **L4 → L1 (live verification).** `recall_memory` and `memory_digest`
   recompute the fraction of a memory's references that still exist in the
   graph *right now* — a low value flags a memory citing renamed/removed code.
3. **L5 → L1 (graph bridge).** `knowledge_search` appends `cross_repo_deps`
   results to business documents that declare `affects_repos`, linking prose
   specs to the actual dependency graph.

Everything else is one-directional: queries enter via `explore`/`ask_compass`
and read down; writes enter via build (L1) or record/add (L4/L5).

---

## 4. Build flow: source → graph

A `cairn build` is the heaviest operation. It's decomposed into phases, each
emitting a progress event, and ends with precomputed derived indexes.

```mermaid
flowchart TB
    START([cairn build]) --> SCAN["1. scan<br/>gitignore + cairn.json filters<br/>+ default skip set"]
    SCAN --> PARSE["2. parse<br/>tree-sitter (14 langs)<br/>+ route + service-call detection"]
    PARSE --> INSERT["3. insert<br/>symbols, edges, imports<br/>+ FTS5 sync triggers"]
    INSERT --> RESOLVE["4. resolve<br/>5-tier edge resolution<br/>label exact/ambiguous/unresolved"]
    RESOLVE --> PERSIST{in-memory<br/>build?}
    PERSIST -- "yes (full workspace)" --> SWAP["5a. persist<br/>atomic .tmp + os.replace<br/>(SQLite page copy)"]
    PERSIST -- "no (single repo)" --> DERIVED
    SWAP --> DERIVED["6. derived indexes<br/>dataflow (O(1) impact)<br/>+ transitive closure (distance 4)"]
    DERIVED --> DONE([done])
```

### Build sequence (per workspace)

```mermaid
sequenceDiagram
    participant CLI as cairn build
    participant B as builder
    participant DB as :memory: DB
    participant R as resolver
    participant Disk as on-disk .kg

    CLI->>B: build_graph(progress)
    B->>DB: get_build_db() (FK off, sync OFF, 256MB cache)
    B->>B: scan_workspace (emit "scan")
    loop every file (parallel if >10)
        B->>B: parser.parse() (emit "parse_progress")
        B->>B: detect_routes + detect_service_calls
    end
    B->>B: emit "parse_done"
    loop every parsed file
        B->>DB: insert_parsed_file (emit "insert_progress")
        Note over DB: triggers sync symbols_fts
    end
    loop every repo
        B->>R: resolve_repo_edges (emit "resolve_start")
        R->>DB: build indexes (symbol/import/members/ancestor)
        R->>DB: resolve_edge x N (5 tiers)
        R->>DB: UPDATE edges SET target_id, resolution (emit "resolve_done")
    end
    B->>Disk: backup_to() (emit "persist")<br/>atomic .tmp + os.replace
    B->>Disk: build_dataflow_index + build_transitive_closure
    B->>Disk: PRAGMA wal_checkpoint(TRUNCATE)
```

**Key points:**

- The full-workspace build runs in `:memory:` and is swapped to disk atomically
  — readers never see a partial graph. Single-repo rebuilds stay on disk.
- The **resolver** runs 5 tiers in order (type-aware → same-file → import-aware
  → same-repo → global) and stops at the first tier with a unique match;
  multiple matches in a tier → `ambiguous` (no fallthrough).
- `cairn update` (incremental, from git diff) **rebuilds the derived indexes**
  (dataflow + transitive closure) whenever any file actually changed. So
  `explore` and `impact_analysis` see fresh O(1) precomputed paths after an
  update, not just the live BFS fallback. (If nothing changed, no rebuild is
  performed — same as `cairn build` on a no-op tree.)

---

## 5. Query flow: agent → answer

Two front doors, both read-only over the graph.

### `explore(query)` — the one-call graph answer

```mermaid
sequenceDiagram
    participant Agent as AI Agent
    participant EX as explore()
    participant L1 as L1 Graph
    participant DB as .kg

    Agent->>EX: explore("how does auth work")
    EX->>L1: search_symbols (FTS5 + BM25)
    L1-->>EX: seeds (top symbols)
    opt RRF fusion enabled (default)
        EX->>L1: semantic_search (vectors)
        L1-->>EX: more seeds (RRF-merged)
    end
    loop per seed
        EX->>L1: get_callers + get_callees (1-hop)
        L1-->>EX: neighborhood
    end
    EX->>L1: _read_source_spans (verbatim code)
    EX->>L1: impact_analysis (depth 2, shallow blast radius)
    EX->>L1: _ambiguous_dispatch (polymorphism grep can't see)
    EX-->>Agent: { seeds, files w/ source, call_paths,<br/>blast_radius, dispatch_hops }
```

`explore` is pure L1. It returns matching symbols' verbatim source, the call
paths between them, a shallow blast radius, and the ambiguous-dispatch hops —
polymorphism a grep fundamentally cannot surface — in a single round trip.

### `ask_compass(query)` — the cross-layer router

```mermaid
sequenceDiagram
    participant Agent as AI Agent
    participant AC as ask_compass()
    participant CL as classify_intent
    participant L1 as L1 Graph
    participant L2 as L2 Compass/KB
    participant L4 as L4 Memory
    participant L5 as L5 Knowledge

    Agent->>AC: ask_compass("context for AuthModule")
    AC->>CL: regex over INTENT_PATTERNS (first match)
    CL-->>AC: layer ∈ {L1,L2,L4,L5,ALL}
    alt single layer matched
        AC->>AC: query that one layer
    else ALL (complex/no match)
        AC->>L1: _query_graph_hybrid
        AC->>L2: _get_compass / _search_wiki
        AC->>L4: _search_memory
        AC->>L5: _search_knowledge
    end
    opt all layers empty (degraded)
        AC->>AC: re-run graph + memory + knowledge
    end
    AC-->>Agent: consolidated cross-layer result
```

`ask_compass` classifies intent via ordered regex patterns and dispatches to one
or all layers. With a `file_path` and no query, it loads compass + wiki + memory
context directly for that file.

---

## 6. The LLM boundary & critic loop

cairn **never calls an LLM itself.** Where LLM-quality synthesis helps
(compass/wiki generation), it uses a decoupled task queue, and a deterministic
critic fact-checks every result against the graph before commit.

```mermaid
flowchart TB
    GEN["Need synthesis?<br/>e.g. generate compass"]
    GEN --> Q{LLM available<br/>in-process?}
    Q -- "yes (droid/opencode/claude)" --> LOOP["sync revise loop<br/>max 4 cycles"]
    Q -- "no" --> TASK["async task queue<br/>cairn task create"]
    TASK --> CLAIM["agent: cairn task claim"]
    CLAIM --> COMP["agent: cairn task complete<br/>--result-file body.md"]
    COMP --> CRITIC
    LOOP --> CRITIC["deterministic critic<br/>verifies refs vs graph"]
    CRITIC --> D{passed?}
    D -- "yes" --> WRITE["write concept<br/>(compass/wiki)"]
    D -- "no, attempts left" --> REVISE["spawn revise task<br/>OR client.revise()"]
    REVISE --> CRITIC
    D -- "no, max attempts" --> DROP["drop<br/>(no file written)"]
```

### Two critic loops, one gate

```mermaid
sequenceDiagram
    participant G as generator
    participant C as critic_concept
    participant DB as .kg
    participant B as OKF bundle

    loop up to MAX_REVISE_CYCLES+1
        G->>G: client.synthesize or client.revise
        G->>C: critic_concept(draft)
        C->>DB: verify file refs and symbol refs
        DB-->>C: exists per ref
        C-->>G: passed / errors / quality_score
        G->>G: break if no errors
    end
    G->>B: write_concept only if accepted
```

The async task queue (`complete_task`) follows the same gate: on critic failure
with attempts remaining, it spawns a `-revise` task carrying the errors; on
exhaustion, it drops. The **deterministic critic** is the shared checkpoint —
an agent may hallucinate, but a hallucinated symbol can never land in a
compass/wiki doc because the critic only allows graph-verified references.

---

## 7. Memory lifecycle

Memory is a four-tier system with promotion, decay, and live verification.

```mermaid
flowchart LR
    CAP["capture_memory<br/>(agent records learning)"]
    CAP --> SCORE["score_memory<br/>7 signals"]
    SCORE --> TIER{score?}
    TIER -- "< 0.3" --> RAW["raw/<br/>ephemeral"]
    TIER -- "< 0.5" --> DRAFT["drafts/<br/>awaiting critic"]
    TIER -- ">= 0.5" --> TRIBAL["tribal/<br/>durable, read these"]

    RAW -.->|"decay >7d"| ARCH["archived/"]
    TRIBAL -.->|"stale >90d"| ARCH
    DRAFT -.->|"batch_critic<br/>promote"| TRIBAL
    TRIBAL -.->|"force promote"| CANON["canonical<br/>(compass/wiki)"]

    RECALL["recall_memory / digest"]
    TRIBAL --> RECALL
    RECALL -.->|"live: refs-verified<br/>fraction vs graph"| VERIFY["L1 graph check"]
```

The **7 scoring signals** (weighted): `0.25*graph_verification +
0.20*cross_session_refs + 0.15*agent_confidence + 0.20*critic_score +
0.05*freshness + 0.05*reinforcement + 0.10*authority`. A memory decays over
type-dependent windows (`decision`: 90d; `pattern`/`mistake`/`workaround`:
270d) unless human-authored.

The standout feature: `recall_memory` and `memory_digest` recompute the
**refs-verified fraction live** — the fraction of a memory's backtick-quoted
file/symbol references that still exist in the graph *right now*. A memory
citing renamed code self-invalidates.

---

## 8. Storage layout

Two stores side by side, both local, both readable.

### SQLite (`.kg`)

```mermaid
erDiagram
    repos ||--o{ files : has
    files ||--o{ symbols : contains
    symbols ||--o{ edges : "source of"
    symbols ||--o{ edges : "target of"
    files ||--o{ imports : declares
    symbols ||--o{ embeddings : "vector of"
    symbols ||--o{ embeddings_mv : "extra vectors of"
    symbols ||--o{ transitive_edges : "closure source"

    repos { string id PK }
    files {
      string id PK
      string repo_id FK
      string path
    }
    symbols {
      string id PK
      string file_id FK
      string name
      string kind
    }
    edges {
      string id PK
      string source_id FK
      string target_id FK
      string kind
      string resolution
    }
    imports {
      string id PK
      string file_id FK
      string imported_path
    }
    embeddings {
      string symbol_id PK
      string model PK
      blob vec
    }
    embeddings_mv {
      string symbol_id PK
      string model PK
      string vector_kind PK
      int dim
      blob vec
      string chunk
      string content_hash
    }
    term_df {
      string token PK
      int symbol_df
      int n_symbols
    }
    dataflow {
      string symbol PK
      json within_repo
      json cross_repo
    }
    transitive_edges {
      string source_id PK
      string target_name PK
      int distance
    }
```

- **WAL journaling**, 256MB mmap, 5s busy_timeout. Read-only opens use
  `file:...?mode=ro` (the SSE daemon's safe shared-instance mode).
- **FTS5** external-content table over `symbols` (name, qualified_name,
  docstring), synced by triggers, powers BM25-ranked lexical search.
- **Derived tables**: `dataflow` (O(1) blast radius for public symbols),
  `transitive_edges` (closure matrix, materialised to distance 4) — built by `cairn build`
  and rebuilt by `cairn update` whenever any file changed.
- **Vector search artifacts**: per-model sqlite-vec `vec0` ANN tables —
  `vec_<model>` over `embeddings` (populated by every `cairn embed`) and
  `vecmv_<model>` over `embeddings_mv` (the opt-in multi-vector table, written
  only by `cairn embed --multivector` and empty on default builds; multi-vector
  queries merge by max score per symbol) — plus `term_df`, the persisted
  document-frequency table refreshed by the same `cairn embed` pass, powering
  IDF-aware query enrichment.

### OKF markdown bundle (`.knowledge/`)

```
.knowledge/
├── compass/            # module navigation guides (Compass concepts)
├── wiki/               # architectural articles
├── knowledge/          # business docs + workflows (L5)
├── memory/
│   ├── raw/            # ephemeral captures (expire >7d)
│   ├── drafts/         # awaiting critic review
│   ├── tribal/         # promoted, durable — read these
│   └── archived/       # decayed/demoted sink
├── _tasks/             # LLM task queue + results + claim markers
├── index.md            # auto-generated per-dir index
└── log.md              # append-only audit log
```

Every concept is a markdown file with YAML frontmatter (OKF v0.2). The
`generated.by` field records the last producer; `memory_signals` caches the
7-signal score; `memory_tier` tracks the lifecycle stage.

---

## See also

- [architecture.md](architecture.md) — per-layer deep dive, resolution model, LLM boundary.
- [mcp-tools.md](mcp-tools.md) — all 27 MCP tools with argument schemas.
- [benchmarks.md](benchmarks.md) — how to measure retrieval quality and performance.
- [architecture.html](../architecture.html) — interactive HTML version of these diagrams.
