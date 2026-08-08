# Phase 2 — Prove It (Technical Guide)

> **Roadmap milestone:** Month 2 — Prove it.
> **Read alongside:** [`spec.md`](spec.md), [`plan.md`](plan.md).
> **Rule:** every file/symbol reference is verified against the tree. Re-run
> the verification commands before implementing; if they disagree with this
> guide, the guide is wrong.

## 2.1 — Benchmarks

**Existing harness (real, not stubbed):**
- `src/cairn/eval.py` — the eval runner behind `cairn eval`.
- `tests/eval/queries.yaml` — 163 lines, 40 ground-truth queries (30 L1 code +
  10 L5 knowledge), each with a `query` and an `expect` fragment list. A query
  passes if any expected fragment is a case-insensitive substring of any
  retrieved name in the top-k.
- `src/cairn/bench/` — `perf_suite.py` (build/embed/query timings),
  `scaling_suite.py` (corpus-size scaling, includes a `resolve_rate` column).
- `docs/benchmarks.md` — methodology + `_fill_` result templates.

**Metrics to produce (per corpus):**
- **Recall@10** and **MRR** from `cairn eval` (L1 and L5 corpora).
- Build/embed/query latency from `cairn bench --suite perf` (median / p95 /
  ops/sec).
- Scaling from `cairn bench --suite scaling` (build/embed/DB-MB/**
  resolve_rate** per size).
- **Precise-vs-fuzzy false-positive rate** — the methodology number. For a set
  of common symbol names (`invoke`, `get`, `build`, `init`, …): run
  `cairn impact <name>` (precise) and `cairn impact <name> --fuzzy`, then
  `(fuzzy_count - precise_count) / fuzzy_count` averaged across the name set.
  This is the number that quantifies "precise is never inflated by name
  collisions."

**Pitfalls:**
- Embeddings off by default (no torch in the default install). `cairn eval`
  falls back to lexical (`search_symbols`) when embeddings are empty — note
  which path each corpus used, or install `[semantic]` for the real embedding
  numbers. Do not mix the two in one table without labeling.
- The `resolve_rate` from the scaling suite is the % of edges the resolver
  pinned to `exact`. It is *not* the same as the false-positive rate — keep
  them distinct in the writeup.

**Reproducibility:** pin each corpus repo at a commit SHA; record the SHA +
cairn version + hardware in benchmarks.md next to the table. The doc already
warns that matching numbers across machines is misleading.

## 2.2 — Visible critic

**Existing code (the verdict is already surfaced in several places — the gap is structure, not visibility):**
- `src/cairn/compass/critic.py` — `critic_concept(concept, conn)` returns a
  `CriticResult` (`errors`, `warnings`, `quality_score`, `passed`, `__bool__`
  → `passed`).
- `src/cairn/mcp_server/tools_compass.py:197` — the MCP tool is **`generate_flow`**
  (there is no `generate_compass` MCP tool). It calls `critic_concept` at line
  230 and **does surface** `quality_score`, `errors[]`, `warnings[]` in the
  returned *string* (lines 234-243) — but as flattened text, not a structured
  field, and only in the rejection branch.
- `src/cairn/cli/compass.py:170-199` — `cairn compass validate` already loads
  compass concepts, runs `critic_concept`, and prints
  `passed / quality_score / errors[] / warnings[]` per concept. The gap vs.
  `cairn verify <doc>` is: (a) the `verify` name, (b) accepting an arbitrary
  single `<doc-path>` positional (compass validate scans all compass files).
- `src/cairn/cli/validate.py:31-64` — `cairn validate-paths` checks stale
  references across all concept types.
- `src/cairn/cli/wiki.py:32-62` — `cairn wiki generate` already unpacks
  `critic_results` and prints errors/warnings (with `--dry-run` and
  `--show-rejections` flags). `wiki/generator.py:31-42` returns
  `(concepts, critic_results)`.
- `src/cairn/compass/generator.py:303,320` — the CLI generator path runs the
  critic in its revise loop.

**Actual work (narrower than "surface from scratch"):**
- **CLI:** add `cairn verify <doc-path>` — a thin wrapper over the existing
  load→`critic_concept`→print flow in `compass validate`, accepting an
  arbitrary single concept path (any type: compass/wiki/memory). Read-only.
  Natural home: extend `src/cairn/cli/validate.py` or `compass.py`, not a
  net-new file.
- **MCP:** in `tools_compass.py` (`generate_flow`) and the wiki generation
  tool, include the `CriticResult` as a **structured additive dict field** in
  the return, e.g. `"critic": {"passed": bool, "errors": [...],
  "warnings": [...]}`. Today `generate_flow` returns a flattened string;
  promote it to a structured field. Existing return string stays the same for
  backward compat — the dict is additive.

**Pitfall — the asymmetric contract (from Phase 1.3):** file refs → errors
(block), symbol refs → warnings (non-blocking). The verdict must surface
*both* so the caller can tell a hard reject from a soft warn. Do not collapse
them into a single "issues" list.

## 2.3 — "cairn on cairn" demo

**Target symbols (verified to exist in cairn's own tree):**
- `build_graph` — `src/cairn/graph/builder.py` (the build entry point, 926
  lines — a meaty `impact` target).
- `critic_concept` — `src/cairn/compass/critic.py:38` (smaller, good
  `get_callers` demo).
- `_import_protobuf` (the pass-1/pass-2 importer) — `src/cairn/parsers/scip_importer.py:409`
  (public entry points `import_scip_file` :749 / `import_scip_data` :626; good
  cross-module `explore` demo).

**Form:** a checked-in script (or a pytest marked `core`) that:
1. `cairn build` on cairn's own repo (a temp store under `tmp_path`).
2. `explore("build_graph")` → assert non-empty, mentions `builder.py`.
3. `impact("critic_concept")` → assert non-empty caller set.
4. `get_compass(...)` for the compass module → assert a 5-section guide (or
   generate one and run the critic).

**Pitfall:** the demo must build cairn's own repo *from a clean store*, not
reuse the user's `~/.cairn`. Use `CAIRN_HOME` / `tmp_path` to isolate. And
gate it in CI (`-m core`) so it cannot rot — a demo that breaks silently is
worse than no demo.

## 2.4 — Methodology post

**Target file:** `docs/methodology-precise-vs-fuzzy.md` (new), linked from
README (resolution-labels evidence section) and `benchmarks.md`.

**Structure:**
1. The problem: name-collision inflation in fuzzy code graphs.
2. The methodology: precise (`exact` edges only) vs fuzzy (name matches);
  false-positive rate = `(fuzzy - precise) / fuzzy`.
3. The numbers (from 2.1, three corpora, pinned SHAs).
4. Interpretation: precise recall vs fuzzy recall; the "empty precise ≠
  unused" rule.
5. How to reproduce (`cairn eval`, `cairn bench`, the impact commands).

**Pitfall:** do not overclaim. If precise recall is modest on some corpus,
say so — the methodology's credibility is the honesty about the tradeoff, not
a high number.

---

## Verification commands (run before starting each item)

```bash
# Confirm the eval harness + fixtures are real
wc -l tests/eval/queries.yaml
pytest --collect-only tests/ -k eval | head

# Confirm the critic is invoked but verdict discarded in MCP
grep -n "critic_concept" src/cairn/mcp_server/tools_compass.py

# Confirm the demo target symbols exist
grep -rn "def build_graph" src/cairn/graph/builder.py
grep -n "def critic_concept" src/cairn/compass/critic.py
```
