# Tasks: Postgres Backend (gated)

Companion: [spec.md](spec.md) · [plan.md](plan.md). Status reflects code
state on v0.10.0 @ `7e90628` (surveyed 2026-08-15), not intent.

## Burndown

| Status | Count |
|--------|-------|
| done | 0 |
| partial | 0 |
| todo (unconditional, PG-0..2) | 7 |
| todo (gated, PG-3..6) | 10 |
| **total** | **17** |

Items PG-3..PG-6 (10 tasks) are **gated** — do not start before the
cloud-simulation SIM-6 findings arm the trigger (spec §5).

---

## PG-0 — Re-land the GraphStore read-side seam

Done when: a `GraphStore` protocol covering the read primitives
(`find_definition`, `get_callers`, `get_callees`, `search_symbols`,
`ann_query`, impact/dataflow reads) exists on main with a
`SQLiteGraphStore` pass-through adapter (the 9efaf55 shape), all MCP/CLI
read paths route through it, and the full test suite passes unchanged
(behavior-identical refactor).

- [ ] **P0.1 — Author the protocol.** `graph/store_protocol.py` with the
      read primitives listed above; store owns the connection; returns
      `Mapping[str, Any]` rows (per the 9efaf55 design notes — "Mirrors the
      function API, not the connection", "Read-only by intent"). Reference:
      `git show 9efaf55:src/cairn/graph/store_protocol.py` (dangling but
      readable).
      verify: `uv run pytest tests/test_graph_store_protocol.py -q` (new —
      protocol conformance via SQLiteGraphStore).
- [ ] **P0.2 — Pass-through adapter + factory.** `graph/store_sqlite.py`
      `SQLiteGraphStore` wrapping the existing functions;
      `get_store(db_path=None, backend=None)` factory in `graph/__init__.py`
      exports.
      verify: `grep -rn "GraphStore" src/cairn/graph/__init__.py` shows the
      export; import-time cycle check (`python -c "import cairn.graph"`).
- [ ] **P0.3 — Route read consumers.** Switch MCP tools
      (`mcp_server/tools_graph.py`), compass, wiki, viz, knowledge, eval
      read paths from `conn = _conn(); queries.f(conn, …)` to
      `store = _store(); store.f(…)`. Mechanical, per the survey's consumer
      list.
      verify: `uv run pytest -q` fully green (no behavior change);
      `verify_tool_count()` still asserts 27 tools at boot.
- [ ] **P0.4 — Perf parity gate.**
      verify: `uv run cairn bench --compare baseline.json` exit 0 (within
      15%; baseline saved from main per plan.md §4).

## PG-1 — Backend-selection knob

Done when: `CAIRN_GRAPH_BACKEND` (default `sqlite`) selects the store
implementation at connection-factory level, unknown values fail loudly,
and every existing test passes on the default without modification.

- [ ] **P1.1 — Knob + failure mode.** Env read in the factory; unknown
      backend raises a named error naming valid options; documented in
      `docs/configuration.md` env table.
      verify: `CAIRN_GRAPH_BACKEND=bogus cairn search foo` exits non-zero
      with the error; default path unaffected (`uv run pytest -q`).

## PG-2 — Parity test contract

Done when: a backend-agnostic read-parity suite (golden outputs for
definition/caller/callee/search/impact/dataflow on a fixed corpus) runs
against any registered backend, seeded green for SQLite.

- [ ] **P2.1 — Goldens from MCP-level outputs.** Author goldens from
      current *user-visible* tool responses (not internal SQL) on the
      seeded corpus (reuse `bench/corpus.py`), guarding against vacuous
      parity (spec §7).
      verify: `uv run pytest tests/test_graph_store_parity.py -q` green on
      SQLite; parametrized by backend registry.
- [ ] **P2.2 — Fuzz-lite divergence check.** Random query battery (names,
      prefixes, fuzzy toggles) comparing store output vs legacy function
      output on the same SQLite DB — must be identical.
      verify: seeded fuzz test green over ≥ 500 queries.

## ——— ARMED GATE (cloud-simulation SIM-6 → spec §5) ———

## PG-3 — *(gated)* Postgres read-side implementation

Done when: a `PostgresGraphStore` (psycopg 3, `[postgres]` extra) serves
the PG-2 parity suite green against a schema translated from `SCHEMA_SQL`
(tables + indexes; no FTS5/vec0 yet), with connection pooling, and PG-2
goldens match SQLite results exactly.

- [ ] **P3.1 — Schema translation.** `SCHEMA_SQL` → Postgres DDL (tables,
      the ~30 indexes, `schema_meta`); additive-migration runner equivalent
      to `MIGRATIONS`/`schema_meta` bookkeeping; loader `pg_load(db_url)`
      that builds from an existing SQLite `.kg` (one-shot sync tool).
      verify: DDL applies cleanly on PG 16; sync tool round-trips row
      counts for all 20 tables.
- [ ] **P3.2 — Read methods.** Implement the protocol's primitives in
      psycopg 3 (parameterized SQL, `RealDictCursor`-style rows); pool via
      `psycopg_pool`; degraded modes (no FTS/ANN yet → LIKE + brute cosine
      over fetched vectors, matching existing fallback semantics in
      `lexical.py`/`semantic.py`) documented in the class docstring.
      verify: `tests/test_graph_store_parity.py` green with
      `CAIRN_GRAPH_BACKEND=postgres` against local PG (Apple container,
      postgres:16, published port — ci-local.sh pattern).

## PG-4 — *(gated)* Search parity: full-text + vectors on Postgres

Done when: `search_symbols` on PG uses `tsvector`+GIN (or a validated
alternative) with a documented tokenization-divergence policy, ANN uses
pgvector with cosine, and the PG-2 suite plus a ranking-divergence test
quantify any ordering differences against FTS5/bm25 in a written
acceptance note.

- [ ] **P4.1 — tsvector mapping + divergence policy.** Generated column
      `tsv` from (name, qualified_name, docstring); policy doc for
      unicode61-vs-default-parser differences (esp. camelCase splits —
      today handled by FTS5 notes + LIKE-union in `lexical.py`).
      verify: ranking-diff test reports overlap@10 between engines on a
      query battery; acceptance note written with numbers.
- [ ] **P4.2 — pgvector ANN.** `vector(N)` column + HNSW cosine index
      replacing vec0 MATCH/k; respects the rowid-contract lesson
      (`embeddings.py` upsert comment) by keying joins on explicit ids.
      verify: ANN-vs-brute-cosine agreement test (recall@10 ≥ 0.95 on
      corpus); `ann_fallback` telemetry path still functional.

## PG-5 — *(gated)* Write path and build pipeline

Done when: `build`/`update` can target Postgres (staging-schema cutover
replacing the in-memory+swap trick, `executemany`→COPY or batched
inserts), `build_lock` semantics preserved via `pg_advisory_lock`, and
`bench/scaling_suite.py` runs against PG with numbers recorded.

- [ ] **P5.1 — Build to staging + cutover.** Replaces
      `get_build_db`/`backup_to`/`swap_db_file`: build into
      `<schema>_staging`, then transactional rename/swap (or TRUNCATE+COPY
      in one txn). 500-file commit batching replaced by txn-batched COPY.
      verify: crash-injection test mid-build leaves the serving schema
      intact (old rows or new rows, never mixed).
- [ ] **P5.2 — Advisory build lock.** `pg_advisory_lock` keyed by a stable
      hash of the workspace; second builder fails fast (parity with the
      flock RuntimeError contract).
      verify: two concurrent `cairn update` against PG — one succeeds, one
      exits with the documented error.
- [ ] **P5.3 — Scaling numbers.**
      verify: `uv run cairn bench --suite scaling --sizes 100,1000,5000`
      on PG recorded in docs/benchmarks.md (new PG table column set),
      compared to SQLite.

## PG-6 — *(gated)* CI + release posture

Done when: CI runs the PG parity matrix against a service container (GH
Actions `services: postgres`), SQLite remains the sole default in docs and
quickstart, and `docs/configuration.md` documents the opt-in.

- [ ] **P6.1 — CI matrix.** `.github/workflows/ci.yml` gains a
      `services: postgres` job running the parity suite with
      `CAIRN_GRAPH_BACKEND=postgres` (SQLite jobs unchanged).
      verify: CI green on the matrix.
- [ ] **P6.2 — Docs posture.** `[postgres]` extra in pyproject;
      configuration.md env entry; quickstart untouched (SQLite-only).
      verify: `grep -n postgres docs/quickstart.md` → no hits;
      configuration.md documents the knob.
- [ ] **P6.3 — Record learnings.** `cairn memory record decision
      "postgres backend gate outcome"` with the SIM-6 numbers that decided
      it.

---

## Post-phase hygiene

- `cairn update` + `cairn doctor` after each merged item group.
- Any fallback/degradation path added (P3.2 degraded modes) gets
  `record_memory(type="workaround")` per workspace convention.
