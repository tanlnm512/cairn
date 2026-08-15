# Plan: Postgres Backend (gated)

Companion: [spec.md](spec.md) · [task.md](task.md). "Done when" wording is
owned by spec.md and repeated verbatim in task.md.

## 0. Evidence base (Phase-A survey, 2026-08-15)

- **The seam does not exist on main.** `git merge-base --is-ancestor
  9efaf55 HEAD` → not an ancestor; no branch contains it; files
  `graph/store_protocol.py` / `graph/store_sqlite.py` absent from the
  working tree; grep for `GraphStore`/`SQLiteGraphStore` in `src/`+
  `tests/` → zero hits. The commit message described exactly the shape
  needed: "GraphStore (read primitives: find_definition,
  get_callers/callees, search_symbols, ann_query) + GraphImpactStore …
  SQLiteGraphStore, a thin pass-through adapter… The protocol drops that
  [connection] argument — the store owns the connection… Read-only by
  intent."
- **Every read path takes a raw connection.** `graph/traversal.py` module
  docstring: "All functions take a sqlite3.Connection (from schema.get_db)
  and return sqlite3.Row objects"; consumers: MCP tools, compass, wiki,
  viz, knowledge, eval.
- **Port difficulty inventory** (survey): Hard — `schema.py` (lifecycle:
  :memory: builds, `backup()`, file swap, flock, PRAGMAs, ATTACH),
  `lexical.py` (FTS5+bm25+triggers), `ann_index.py` (vec0, extension
  loading, rowid join), `embeddings.py` (rowid-stable upsert),
  `builder.py` (executemany/500-file commits/swap); Moderate —
  `incremental.py`, memory/*, mcp_server/*, `scip_importer.py`, cli health
  checks; Trivial — `resolver.py`, `cross_repo.py`, `stats.py`,
  `explore.py`, compass/viz/wiki consumers (plain SELECTs).
- **No recursive CTEs, no JSON1-in-SQL** (`cli/system.py` `_attr_counts`
  deliberately parses JSON in Python "so the query does not depend on
  SQLite's JSON1 extension") — the relational surface is unusually
  portable; the hard parts are FTS5, vec0, and the file lifecycle.
- **Migrations** are additive ALTERs recorded in `schema_meta` (12 entries);
  no numeric version (noted by `cli/system.py` `_report_versions`).
- Prior Postgres references in repo: only the redaction pattern
  (`memory/privacy.py` `_URI_SCHEME`) and tests for it.

## 1. Implementation options

### Option A — Whole-hog migration (SQLite dropped)

Reimplement everything on Postgres; local mode runs a bundled postgres or
connects to a server.

- **Pros:** one storage engine; no parity matrix; unlocks hosted
  multi-writer forever.
- **Cons:** destroys the local-first zero-config product
  (README/architecture positioning); forfeits the in-memory-build + atomic
  swap pipeline, single-file portability, WAL checkpointing; forces a
  ~51-file test rewrite; FTS5/vec0 parity fights are mandatory rather than
  optional; contradicts the verified doctrine that local bottlenecks are
  CPU-side. **Rejected at verification time** (spec §2).

### Option B — Dual backend behind a re-landed GraphStore seam (read-side
first, gated write-side)

PG-0 seam (behavior-identical) → PG-1 knob → PG-2 parity suite →
*(gate)* → PG-3 PG read store → PG-4 search parity → PG-5 build/write →
PG-6 CI.

- **Pros:** every step is independently valuable and revertible; PG-0..2
  cost ~1 week and make the storage assumption *testable* even if the gate
  never fires; risk is concentrated exactly where parity is provable
  (golden suite); SQLite default is structurally untouchable (the default
  backend *is* the pass-through adapter).
- **Cons:** the seam touches every read call site once (mechanical but
  wide — survey: tools, compass, wiki, viz, knowledge, eval all pass
  `conn`); dual backends mean a permanent parity tax on schema changes;
  async-vs-sync driver mismatch risk if the MCP server later wants
  asyncpg.
- **Cost:** PG-0..2 ≈ 1 week; PG-3..6 ≈ 3–4 weeks if the gate fires.

### Option C — Projection/replication model (read-replica only)

Keep SQLite as the sole source of truth; periodically export the graph
into Postgres for remote serving (a `cairn export --postgres` command).

- **Pros:** smallest possible step to a remote-serving story; no seam
  needed in the query layer (the exporter writes, a thin PG reader
  serves); zero risk to local builds.
- **Cons:** doesn't solve writer concurrency or the cloud couplings —
  it *is* C-1 with a delay; staleness becomes an export-interval; two
  schemas to keep in sync with no shared test contract (worst of both);
  the cloud-simulation phase would still be required before it means
  anything.
- **Cost:** ~1–2 weeks for a minimal exporter+reader.

## 2. Recommendation: **Option B**

B is the only option whose *unconditional* prefix (PG-0..2) is worth doing
even if Postgres is never shipped: it converts "everything takes a raw
sqlite3.Connection" — today an unverifiable architectural assumption —
into a typed, mockable seam with a parity suite, which the perf phase
(PERF-1 routing) and the cloud phase (future store injection) both
benefit from. A is disqualified by the verification (spec §2); C solves
none of the verified couplings and creates an unsynchronized second
schema. The gate (spec §5) keeps B honest: if the cloud rehearsal shows
shared-volume SQLite is adequate, B simply stops after PG-2.

## 3. Sequencing (chosen path)

```
Unconditional (week 1)
  PG-0  re-land 9efaf55-shape protocol + SQLiteGraphStore pass-through
        (reference: dangling commit's store_protocol.py design notes)
  PG-1  CAIRN_GRAPH_BACKEND factory knob (default sqlite)
  PG-2  backend-agnostic golden parity suite (from MCP-level outputs)
  → gate: pytest green, bench --compare within 15%

[ARMED GATE — cloud-simulation SIM-6 findings; spec §5 numeric triggers]

Conditional (weeks 2-5, only if triggered)
  PG-3  PostgresGraphStore: schema translation (tables+indexes),
        psycopg3 pooling, parity green (no FTS/ANN yet — degraded modes
        documented)
  PG-4  tsvector+GIN & pgvector parity with divergence policy +
        ranking-diff quantification
  PG-5  build/update write path: staging cutover, COPY loading,
        pg_advisory_lock build lock; scaling_suite on PG
  PG-6  CI matrix (services: postgres), docs posture
```

Ordering rationale: read-side before write-side because the survey rates
all read consumers Trivial/Moderate while the build pipeline is Hard;
search parity (PG-4) is isolated *after* PG-3 so basic relational parity
is proven before the tokenization fight; the write path is last because it
replaces the codebase's most clever trick (in-memory build + atomic swap)
and is only worth it if serving has already proven out.

## 4. Verification commands

- PG-0: `uv run pytest -q` green (unchanged behavior); `uv run cairn bench
  --compare baseline.json` within 15%.
- PG-1: `CAIRN_GRAPH_BACKEND=bogus cairn search x` fails loudly with the
  unknown-backend error; unset/default runs normally.
- PG-2: `uv run pytest tests/test_graph_store_parity.py -q` (new suite).
- PG-3..5 (when armed): `CAIRN_GRAPH_BACKEND=postgres uv run pytest
  tests/test_graph_store_parity.py -q` against `CI`-provided or local PG;
  `docker`-less local PG via Apple `container` (postgres:16 image,
  ci-local.sh invocation pattern with published port).
- PG-6: CI green on the matrix; `grep -rn "sqlite" docs/quickstart.md`
  still shows SQLite as the only documented default.
