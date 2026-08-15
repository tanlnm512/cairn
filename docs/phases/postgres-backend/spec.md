# Phase: Postgres Backend (requirement verification + gated plan)

- **Status:** planned — **gated, not scheduled** (see §2 verdict and §5
  trigger conditions)
- **Date drafted:** 2026-08-15
- **Code state baseline:** v0.10.0 @ `7e90628` (main)
- **Companion docs:** [plan.md](plan.md) · [task.md](task.md)
- **Gating phase:** [../cloud-simulation/](../cloud-simulation/spec.md)
  (SIM-6 findings decide the trigger)

## 1. The requirement, as asked

> "Shift SQL to Postgres — check pros and cons, is it valid?"

## 2. Verification verdict

### INVALID as a wholesale replacement of SQLite

The evidence is structural, not preferential:

1. **Positioning**: `README.md` — "all in a local SQLite store" and
   `docs/architecture.md` — "cairn is a **local, structural, agent-first**
   code intelligence system". A required database server ends zero-config
   single-binary adoption, the current differentiator vs cloud-hosted
   competitors.
2. **The load-bearing machinery is file-semantics-based**, from the
   Phase-A SQLite-dependency survey (31 files under `src/` touch sqlite3):
   - `graph/schema.py` — full builds run in `:memory:` (`get_build_db`) and
     persist via `Connection.backup()` C-level page copy + atomic
     `os.replace` file swap (`backup_to`/`swap_db_file`, with WAL-sidecar
     cleanup); `build_lock` is `fcntl.flock`; PRAGMAs everywhere
     (`journal_mode`, `mmap_size`, `busy_timeout`, `wal_checkpoint`).
     None of this has a Postgres meaning.
   - **FTS5 with bm25() and unicode61 tokenization** (`graph/lexical.py`
     `search_symbols`, external-content `symbols_fts` + 3 sync triggers in
     `SCHEMA_SQL`) — Postgres `tsvector`/ts_rank tokenizes and ranks
     differently; result *order changes*, and cairn's LIKE-substring
     fallback union (for camelCase) has no equivalent.
   - **sqlite-vec vec0** (`graph/ann_index.py` — `CREATE VIRTUAL TABLE …
     USING vec0(embedding float[N] distance_metric=cosine)`, KNN via
     `embedding MATCH ? AND k = ?`) — pgvector changes both syntax and the
     **rowid-keyed join contract**: `graph/embeddings.py` uses
     `ON CONFLICT(symbol_id, model) DO UPDATE` *specifically because it
     preserves rowid*, which vec0 keys on.
   - `copy_telemetry_tables` uses `ATTACH DATABASE` + cross-DB
     `INSERT … SELECT` — no Postgres equivalent (dblink/FDW is not a
     port).
   - Tests: `tests/conftest.py` `fresh_db` = real in-memory SQLite with the
     production schema; ~51 test files reference sqlite3; WAL-swap and
     contention tests (`OperationalError` string-matching) are
     SQLite-specific doubles.
3. **What Postgres would buy is not currently needed on the local path.**
   cairn is single-user, per-workspace, single-writer by design (`build_lock`
   docstring); the telemetry layer already absorbs and *measures* the only
   contention that exists (`note_contention`). The measured doctrine in
   `docs/benchmarks.md` — "Build times dominate at scale; query times are
   sub-millisecond once the graph is built" — says the local bottleneck is
   CPU parsing, not storage concurrency.

### VALID as an **opt-in second backend** for a server/cloud edition

What Postgres genuinely unlocks maps exactly onto the couplings the
cloud-simulation phase measures (C-1 one-file-four-stores, C-2 POSIX locks,
C-6 dark read-tiers): network-transparent storage, real concurrent readers
beside a writer, no shared-volume WAL hazards, and a request-borne store
selector. But — decisively — **the seam needed to host a second backend
does not exist on main**: commit `9efaf55` ("feat(graph): add GraphStore
protocol seam for swappable storage backend", files
`graph/store_protocol.py` + `graph/store_sqlite.py`) was never merged; it
survives only as a dangling commit on a deleted branch, and every query
function in `graph/traversal.py` et al. takes a raw `sqlite3.Connection`
first argument today.

**Therefore:** valid only as (a) re-land the seam, (b) implement the
Postgres backend *behind it* as an optional extra, (c) SQLite remains the
default and the only mode the local product ever requires. Whether (b) is
ever executed is decided by the cloud-simulation findings (§5).

## 3. Pros and cons (with evidence)

| Dimension | Postgres pros | Postgres cons (evidence) |
|---|---|---|
| Concurrency | Real multi-reader + writer via MVCC; deletes flock/busy_timeout machinery for hosted use | Local single-user path gains nothing; `note_contention` telemetry (a feature) loses its subject |
| Remote serving | Network-transparent: gateway and indexer need not share a volume (fixes cloud-sim C-1) | `paths.py` workspace model is process-local; tools carry no workspace param (C-3) — needs a routing layer regardless of DB |
| Search parity | `tsvector`+GIN is mature; pgvector does ANN | FTS5→tsvector changes tokenization & ranking (unicode61 camelCase notes in `SCHEMA_SQL`; bm25 vs ts_rank); LIKE-union fallback has no equivalent; vec0 rowid contract breaks (`embeddings.py` upsert comment) |
| Ops | Managed HA/backup; SQL tooling | New server dependency, connection lifecycle, per-backend test matrix (~51 SQLite-coupled test files today) |
| Build pipeline | COPY-based bulk load is fast | In-memory build + `backup()` + atomic file swap (`get_build_db`/`backup_to`/`swap_db_file`) becomes staging-schema + cutover transaction — a rewrite of the highest-value build trick in the codebase |
| Portability | Central store shared by CI/agents | Single-file `.kg` portability (copy/commit/vacuum) is lost for PG users; `cli/core.py` `checkpoint` (WAL truncate) meaningless |
| Cost of path | — | No seam exists on main (9efaf55 dangling); survey port-difficulty: `schema.py`/`lexical.py`/`ann_index.py`/`embeddings.py`/`builder.py` all rated **Hard** |

## 4. Items and "Done when"

Each item appears verbatim in [plan.md](plan.md) and [task.md](task.md).
Items PG-0..PG-2 are unconditional groundwork (valuable even if the backend
is never shipped); PG-3+ are gated.

### PG-0 — Re-land the GraphStore read-side seam

- **Done when**: a `GraphStore` protocol covering the read primitives
  (`find_definition`, `get_callers`, `get_callees`, `search_symbols`,
  `ann_query`, impact/dataflow reads) exists on main with a
  `SQLiteGraphStore` pass-through adapter (the 9efaf55 shape), all MCP/CLI
  read paths route through it, and the full test suite passes unchanged
  (behavior-identical refactor).

### PG-1 — Backend-selection knob

- **Done when**: `CAIRN_GRAPH_BACKEND` (default `sqlite`) selects the store
  implementation at connection-factory level, unknown values fail loudly,
  and every existing test passes on the default without modification.

### PG-2 — Parity test contract

- **Done when**: a backend-agnostic read-parity suite (golden outputs for
  definition/caller/callee/search/impact/dataflow on a fixed corpus) runs
  against any registered backend, seeded green for SQLite.

### PG-3 — *(gated)* Postgres read-side implementation

- **Done when**: a `PostgresGraphStore` (psycopg 3, `[postgres]` extra)
  serves the PG-2 parity suite green against a schema translated from
  `SCHEMA_SQL` (tables + indexes; no FTS5/vec0 yet), with connection
  pooling, and PG-2 goldens match SQLite results exactly.

### PG-4 — *(gated)* Search parity: full-text + vectors on Postgres

- **Done when**: `search_symbols` on PG uses `tsvector`+GIN (or a validated
  alternative) with a documented tokenization-divergence policy, ANN uses
  pgvector with cosine, and the PG-2 suite plus a ranking-divergence test
  quantify any ordering differences against FTS5/bm25 in a written
  acceptance note.

### PG-5 — *(gated)* Write path and build pipeline

- **Done when**: `build`/`update` can target Postgres (staging-schema
  cutover replacing the in-memory+swap trick, `executemany`→COPY or batched
  inserts), `build_lock` semantics preserved via `pg_advisory_lock`, and
  `bench/scaling_suite.py` runs against PG with numbers recorded.

### PG-6 — *(gated)* CI + release posture

- **Done when**: CI runs the PG parity matrix against a service container
  (GH Actions `services: postgres`), SQLite remains the sole default in
  docs and quickstart, and `docs/configuration.md` documents the opt-in.

## 5. Gate: trigger conditions (from cloud-simulation SIM-6)

Execute PG-3+ only if the findings report shows **any** of:
- ≥ 1 `lock_contention` event per 100 gateway queries under ≥ 2 concurrent
  readers + 1 writer on the shared volume;
- reader staleness unbounded in deployments without the watcher
  (FRESH-1 not shipped or disabled);
- a demonstrated need for geographically split (non-shared-volume)
  services.

If none trigger, PG-0..PG-2 still land (they are pure refactors that make
the storage assumption testable), and this phase ends at PG-2 with the
gate re-armed by the next cloud rehearsal.

## 6. Scope

**In scope:** PG-0..PG-6 as gated above; schema translation; parity suite;
CI matrix.

**Out of scope:**
- Making Postgres the default or removing SQLite (never in this phase).
- Multi-tenancy/workspace routing (C-3) — separate phase if cloud edition
  proceeds.
- Migrating telemetry/OKF/task-queue stores (they can stay file/SQLite
  local even in a PG world; only the graph read/write path is in scope).
- Neo4j/Falkor/Memgraph graph databases (graphify runner-up backends) —
  the query shapes are relational (survey: no graph-pattern queries, all
  SQL over `symbols`/`edges`); a document/graph DB adds nothing but an
  operator burden.

## 7. Risks

| Risk | Mitigation |
|------|------------|
| Seam refactor perturbs hot query paths | PG-0 is behavior-identical (pass-through); perf gate `bench --compare` within 15% |
| Parity suite passes vacuously (both backends wrong the same way) | Goldens authored from *current user-visible outputs* (MCP tool responses), not from internal queries |
| Tokenization divergence makes "parity" ill-defined | PG-4 requires a written divergence policy + quantified ranking-diff test before acceptance |
| Scope creep toward full cloud edition | §5 numeric gate + §6 exclusions; PG-3..6 individually revertible |
| sqlite-vec/pgvector drift over versions | Pin both in extras; parity suite runs in CI for both |
