# Phase 2 — Prove It (Plan)

> **Roadmap milestone:** Month 2 — Prove it.
> **Read alongside:** [`spec.md`](spec.md), [`tech-guide.md`](tech-guide.md).

## Sequencing

Two tracks, with one dependency:

```
Track A (evidence):  2.1 (benchmarks) ──► 2.4 (methodology post)
Track B (surface):   2.2 (visible critic)   ║   2.3 (cairn-on-cairn demo)
```

2.1 must land before 2.4 (the post cites the numbers). 2.2 and 2.3 are
independent and run in parallel with Track A. Recommended order:

```
Week 1:  2.1 benchmarks (Python corpus first — cairn's own repo)
Week 2:  2.1 benchmarks (Kotlin + TS corpora)  ║  2.2 visible critic
Week 3:  2.3 demo  ║  2.4 methodology post (depends on 2.1 done)
```

## Tasks

### 2.1 — Fill the benchmark tables
**Done when:** `benchmarks.md` tables are no longer `_fill_`; 2.4 cites real numbers.

The sub-tasks (tracked in [`task.md`](task.md) § 2.1):
1. Pick and pin three corpora at exact commits: cairn's own repo (Python), a Kotlin repo, a TypeScript repo. Record SHAs in benchmarks.md.
2. Build each corpus; run `cairn eval` (Recall@10, MRR) for L1 + L5.
3. Run `cairn bench --suite perf` (build/embed/query latency, median/p95/ops-sec).
4. Run `cairn bench --suite scaling` (build/embed/DB-MB/resolve_rate per size).
5. Compute the precise-vs-fuzzy false-positive rate per corpus (the methodology benchmarks.md already describes): for a set of common symbol names, `(fuzzy_count - precise_count) / fuzzy_count`.
6. Fill the result-template tables in benchmarks.md with the measured numbers; note hardware + cairn version alongside the tables.

**Risk:** precise recall may be lower than hoped. That is honest — report it;
do not tune the resolver inside 2.1.

### 2.2 — Make the critic visible
**Done when:** a user can ask "is this compass guide trustworthy?" and get a machine-checked answer.

The sub-tasks (tracked in [`task.md`](task.md) § 2.2):
1. Add `cairn verify <doc-path>` — a thin wrapper over the existing load→`critic_concept`→print flow already in `cairn compass validate` (`cli/compass.py:170-199`), extended to accept any single concept path (compass/wiki/memory). The capability is ~80% there; the gap is the `verify` name + arbitrary single-doc arg.
2. Promote the MCP critic verdict from a flattened text string (today, in `generate_flow` at `tools_compass.py:230-243`) to a structured additive dict field (`critic: {passed, errors, warnings}`) in `generate_flow` / `ask_compass`. (The CLI `wiki generate` already surfaces the verdict — `cli/wiki.py:32-62`.) Existing return string stays the same for backward compat.
3. Keep file-ref errors and symbol-ref warnings distinct in the surfaced verdict (do not collapse).
4. Add a test asserting the structured verdict is returned for a known-good and known-bad concept.

**Risk:** additive MCP return field could surprise agents parsing the current shape. Keep existing fields byte-identical; verdict is a new key.

### 2.3 — "cairn on cairn" live demo
**Done when:** the README quick-start, run on cairn's own repo, returns real, correct results.

The sub-tasks (tracked in [`task.md`](task.md) § 2.3):
1. Write a checked-in script (`scripts/demo_self.sh` or a pytest under `-m core`) that builds cairn's own repo in an isolated store (`CAIRN_HOME`/`tmp_path`) — do not reuse `~/.cairn`.
2. Assert non-empty correct output for `explore("build_graph")`, `impact("critic_concept")`, `get_compass(...)` (target symbols verified in tech-guide).
3. Add the script to CI so the demo cannot silently rot.
4. Reference it from the README quick-start as the verbatim walkthrough.

**Risk:** demo drift as cairn evolves. CI is the guard — if the demo breaks, the build breaks.

### 2.4 — Methodology post
**Done when:** a standalone artifact legible to someone who has never installed cairn.

The sub-tasks (tracked in [`task.md`](task.md) § 2.4):
1. Write `docs/methodology-precise-vs-fuzzy.md` (or a blog-ready markdown): the false-positive methodology, the measured numbers from 2.1, and the interpretation.
2. Cite the three corpora with their pinned SHAs so the numbers are reproducible.
3. Link it from the README (under the resolution-labels evidence section) and from benchmarks.md.

**Risk:** none beyond depending on 2.1's numbers being in.

## Definition of done (phase level)

Phase 2 lands when **all four** hold:

1. benchmarks.md tables are filled for three corpora, with pinned SHAs + version/hardware noted.
2. `cairn verify <doc>` exists and prints the verdict; MCP `ask_compass` / `wiki generate` return the verdict additively.
3. The self-demo script runs green in CI and is referenced from the README quick-start.
4. The methodology post exists, cites 2.1's numbers, and is linked from README + benchmarks.md.

## Scope guard (anti-creep)

| Tempting addition | Belongs in |
|-------------------|------------|
| Tune the resolver to improve 2.1's recall | Phase 4 (follow-up after measuring) |
| Wire scip-clang / rust-analyzer | Phase 4 |
| Graph-grounded memory recall | Phase 3 (3.1) |
| Tighten the critic (symbols block) | Deliberate spec change, later phase |
| Add new eval query sets beyond fixtures/queries.yaml | Phase 4 |
