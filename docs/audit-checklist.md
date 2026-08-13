# Scope Audit Checklist

> Periodic, scope-based audits of whole areas of the codebase — **not** per-PR
> review. Run these on a cadence (quarterly) or after heavy churn in an area.
> For the per-change review gate, see [`review-checklist.md`](review-checklist.md).

**How the two differ:**
- `review-checklist.md` — *change-driven*: "this PR is safe to merge." Blast
  radius of a diff. Run every PR.
- `audit-checklist.md` (this file) — *area-driven*: "this subsystem is sound."
  Deep correctness/integrity of a whole scope. Run periodically or on trigger.

**TL;DR — every scope audit answers four questions:**
1. **Correctness** — does the scope do what it claims, including failure modes?
2. **Integrity** — are writes atomic, migrations ordered, no silent corruption?
3. **Precedent** — have bugs recurred here? Check `docs/BUGS.md` for the pattern.
4. **Coverage** — do tests actually exercise the failure modes that bit us before?

---

## Index

| # | Scope | Area | Key files (LOC) | Recurrence signal in BUGS.md | Default cadence |
|---|-------|------|-----------------|------------------------------|-----------------|
| 1 | Data integrity & transactions | graph / memory stores | `schema.py`, `builder.py`, `incremental.py`, `memory/store.py`, `memory/promotion.py` | 4 entries (partial-write, schema-order, stale-resolution, tier-collision) | Quarterly |
| 2 | Secret redaction & privacy | memory / knowledge layer | `memory/privacy.py`, `tools_memory.py`, `knowledge/store.py`, `knowledge/workflow.py` | 2 entries (unredacted secrets, missing scope-guard) | Quarterly |
| 3 | Concurrency & locking | MCP server / embeddings | `mcp_server/lifecycle.py`, `_server_core.py`, `embeddings.py`, `embed_buffering.py` | live branch (`feat/...-mcp-locking-fixes`); stdio-leak memory | After churn in area |
| 4 | Resolver / fusion / retrieval | graph intelligence | `resolver.py`, `fusion.py`, `semantic.py`, `lexical.py`, `ann_index.py`, `retrieval/` | 2 entries (fusion silently skipped, dead dict key) | Quarterly |
| 5 | Parser correctness | parsers/ | `scip_importer.py`, `kotlin.py`, `typescript.py`, `php.py`, `routes.py` | 3 entries (fake-resolution, attribute-pollution, var-drop) | After adding a parser |
| 6 | Agent-install file-write safety | agent_install/ | `merge.py`, `_common.py`, `detect.py`, `clients/*` | 1 entry (comments-only code drift) | After client changes |
| 7 | Supply chain & CI gates | tooling | `pyproject.toml`, `uv.lock`, `.github/workflows/`, `.pre-commit-config.yaml` | — | Quarterly |
| 8 | Doc / test consistency | docs / tests | `README.md`, `CHANGELOG.md`, `docs/`, `tests/` | docs-version-drift memory | Per release |

> **Tier 1 = scopes 1–3.** `BUGS.md` recurrence + the live locking branch put the
> trust/integrity cluster at the highest blast radius. Run those together.

---

## How to run an audit (meta-procedure)

1. **Branch off:** `git switch -c audit/<scope>-<YYYY-MM-DD>`.
2. **Load context the cairn way** (per AGENTS.md):
   - `explore("<scope entry symbol>")` — get the source + call paths in one call.
   - `ask_compass(file_path="<key file>")` — module guide + memory for each file.
   - `recall_memory("<symbol>")` — past decisions/mistakes in the area.
   - `search_knowledge("<topic>", type_filter="Wiki")` — architecture docs.
3. **Walk the scope's checklist** below. For each failed box, file a finding
   (format in §"Recording findings").
4. **Regression-test:** if a finding is a correctness/integrity bug, add a test
   that fails before the fix.
5. **Record:** `record_memory` for any new pattern/mistake; update the BUGS.md
   index + entry if it's a new bug class.
6. **Ship:** follow [`contribution-workflow.md`](contribution-workflow.md)
   (pre-commit → conventional commit → PR with audit checklist).

### Recording findings

One line per finding in the audit's summary, then a section:
`[P1/P2/P3] <scope> — <file:line> — <symptom> → <root cause> → <fix or ticket>`.
P1 = corrupts data / security / data loss. P2 = wrong answers / silent
misbehavior. P3 = hygiene / drift.

---

## Scope 1 — Data integrity & transaction safety

**What:** every persistent write to the SQLite graph + memory stores. This is
where `BUGS.md` shows the densest recurrence: silent corruption that agents then
build on.

**Load context:** `explore("clear_repo")`, `explore("_apply_migration")`,
`recall_memory("schema-init")`, `recall_memory("scip-import-partial-write")`.

For each DB-writing module (`schema.py`, `builder.py`, `incremental.py`,
`memory/store.py`, `memory/promotion.py`, `cross_repo.py`):

- [ ] **Transactions** — every multi-statement write is wrapped in a single
      transaction (`BEGIN…COMMIT` or a context manager). No partial persists on
      exception mid-sequence.
- [ ] **Rollback on failure** — a failed import/build leaves the DB in its prior
      state. No partial-write-then-commit pattern
      (precedent: `scip-import-partial-write-no-rollback`).
- [ ] **Migration ordering** — the "schema initialized" flag is set *after*
      migrations succeed, never before
      (precedent: `schema-init-flag-before-migration`).
- [ ] **No orphaned edges** — bulk deletes (`_clear_repo`, repo re-index) null
      `target_id` *and* clear/downgrade `resolution`, not just one
      (precedent: `clear-repo-stale-exact-resolution`).
- [ ] **Idempotency** — re-running a build/import on the same input produces the
      same graph, no duplicate rows or overwritten captures
      (precedent: `raw-memory-tier-id-collision`).
- [ ] **Connection lifecycle** — connections/cursors are closed on all paths
      (incl. exceptions); no handle leak across `build_graph` runs.
- [ ] **Parameterized queries** — no f-string/`%` SQL interpolation. (bandit
      `B608` is suppressed repo-wide with rationale; verify it still holds.)

---

## Scope 2 — Secret redaction & privacy

**What:** every path that persists untrusted input (memory captures, knowledge
docs). The recurring failure mode is *two codepaths diverging* — hook path
redacts, MCP path doesn't.

**Load context:** `explore("record_memory")`, `recall_memory("unredacted-secrets")`,
`recall_memory("knowledge-status-scope-guard")`.

- [ ] **Single redaction source** — CLI, MCP, and hook entry points all route
      through one redactor (`memory/privacy.py`). No copy of the logic elsewhere
      (precedent: `record-memory-unredacted-secrets`).
- [ ] **Redaction before persistence** — redaction happens *before* the write,
      not just before display. Check the store layer, not just the tool layer.
- [ ] **Namespace / scope guards** — `knowledge_status`, archive, and delete
      operations namespace-guard so they can't act on docs outside their scope
      (precedent: `knowledge-status-missing-scope-guard`).
- [ ] **PII patterns** — redactor covers the patterns that matter for this repo
      (keys, tokens, connection strings). Add a test per pattern.
- [ ] **No raw capture in logs** — logging/telemetry paths don't echo unredacted
      captures (`metric_buffering.py`, `structured.py`).
- [ ] **Regression tests** — one test per redaction entry point (CLI + MCP + hook).

---

## Scope 3 — Concurrency & locking

**What:** file/process locking between the MCP server and the graph builder, and
process lifecycle (the live `feat/...-mcp-locking-fixes` branch; stdio-leak
memory note).

**Load context:** `explore("flock")`, `explore("serve start")`,
`recall_memory("mcp-stdio-leak")`.

- [ ] **Lock release on exception** — every `flock`/lock acquire is paired with
      release in a `try/finally`. No lock held across a raised exception.
- [ ] **Lock scope** — locks protect the *minimum* critical section, not whole
      tool handlers (deadlock/contention risk).
- [ ] **No deadlock cycle** — graph builder and MCP server can't both hold locks
      the other needs. Map the lock acquisition order.
- [ ] **Process lifecycle** — server start/stop (stdio *and* SSE daemon) reaps
      children; no orphaned/hung processes after stop
      (precedent: `mcp-stdio-leak-issue` memory).
- [ ] **Concurrent build + query** — a graph rebuild mid-query doesn't return
      torn/inconsistent results.
- [ ] **Buffer flush on shutdown** — `embed_buffering.py` / `metric_buffering.py`
      flush buffered writes before process exit; no lost embeddings on crash.
- [ ] **Lock tests exist and pass** — `tests/test_*lock*` cover contention and
      release-on-exception.

---

## Scope 4 — Resolver / fusion / retrieval correctness

**What:** the engine that makes the product work. Subtle and failure-prone — a
swallowed `.get()` once disabled fusion entirely.

**Load context:** `explore("resolve")`, `explore("semantic_search")`,
`recall_memory("rrf-fusion-silently-skipped")`, `recall_memory("fusion")`,
`search_knowledge("precise vs fuzzy", type_filter="Wiki")`.

- [ ] **Fusion actually runs** — under default config (`CAIRN_FUSION=1`), RRF
      fusion combines BM25 + vector. Verify it isn't silently skipped by a
      swallowed `.get()` / wrong default
      (precedent: `rrf-fusion-silently-skipped`).
- [ ] **Score semantics** — documented: returned `score` is a rank-fusion number
      under fusion, cosine similarity only with `CAIRN_FUSION=0`
      (see AGENTS.md §"Tool Quirks"). No code treats it as the other.
- [ ] **Precise vs fuzzy** — precise follows only resolved edges; empty precise
      ≠ unused. Fuzzy is a candidate list, verified before trust.
- [ ] **Resolution labels honest** — `exact`/`ambiguous`/`unresolved` reflect
      resolver truth; no `exact` with NULL target
      (precedent: `scip-importer-fake-resolution`).
- [ ] **ANN fallback** — sqlite-vec fails load → silent degrade to brute-force is
      logged/observable, not silent-and-invisible.
- [ ] **Dead dict keys / unreachable branches** — no cross-repo bridge line that
      never prints (precedent: `dead-depends-on-key-in-knowledge-search`).
- [ ] **Determinism** — same query → same ranking (modulo legitimate tie-breaks).

---

## Scope 5 — Parser correctness

**What:** the 14 languages (12 tree-sitter parser modules; c+cpp share c_family,
ts+js share typescript) + SCIP importer. Steady drip of edge cases;
run after adding/touching a parser.

**Load context:** `explore("parse")`, `explore("language inference")`,
`recall_memory("swift-modifier-attribute-pollution")`,
`recall_memory("ts-parser-var-declarator-edge-drop")`.

For each parser (esp. `scip_importer.py`, `kotlin.py`, `typescript.py`, `php.py`):

- [ ] **Resolution labels** — edges labeled `exact` have a non-NULL, correct
      `target_id`. Importer doesn't overclaim resolution.
- [ ] **Idiomatic constructs** — common idioms for the language produce edges, not
      drops: Kotlin `operator fun invoke`, TS `const x = fn()`, JSX refs, Swift
      modifiers (precedents: kotlin-operator-invoke, var-declarator-drop,
      jsx-references, swift-attribute-pollution).
- [ ] **Attribute vs modifier** — modifiers/attributes aren't concatenated into
      symbol names (precedent: `swift-modifier-attribute-pollution`).
- [ ] **Language detection** — `routes.py` / `inference/` detect the right parser
      for ambiguous extensions; inference doesn't misroute.
- [ ] **Golden fixtures** — `tests/fixtures` + `test_golden_parsers.py` cover the
      idioms above, plus a real-world corpus probe (fixtures alone are insufficient).
- [ ] **Error isolation** — a parse error in one file doesn't abort the whole build.

---

## Scope 6 — Agent-install file-write safety

**What:** writes into the *user's* config files across 7 agent clients. Even
"safe" edit tasks have mutated executable code before.

**Load context:** `explore("merge")`, `explore("detect")`,
`recall_memory("comments-only-code-drift")`.

For `merge.py`, `_common.py`, `detect.py`, `clients/*`:

- [ ] **Atomic writes** — config writes go to a temp file + rename, not in-place
      mutation. No torn config on crash.
- [ ] **Backup before edit** — original config backed up before merge; documented
      recovery path.
- [ ] **Merge idempotency** — running install twice doesn't duplicate blocks or
      stack guidance.
- [ ] **Dry-run fidelity** — `--dry-run` reports exactly what the real run writes.
- [ ] **No executable mutation** — comment/guidance edits never touch executable
      code. (`make verify-no-code-change` is the guard; precedent:
      `comments-only-code-drift`.)
- [ ] **Subprocess in clients** — client launchers (`subprocess` sites in
      `clients/*`) don't pass untrusted input to a shell.
- [ ] **Per-client tests** — `test_clients.py` + `test_agent_install_dry.py` cover
      each client's merge path.

---

## Scope 7 — Supply chain & CI gates

**What:** the Layer 0-1 automated defense. If these drift, the whole pipeline weakens.

- [ ] **Dependency pinning** — `mypy` is pinned (rationale documented); check
      whether the others that *should* be pinned are, and that floating pins are
      intentional.
- [ ] **`uv.lock` fresh** — lockfile matches `pyproject.toml`; no stale entries.
- [ ] **pip-audit (hard gate)** — clean; every advisory has a tracked decision.
- [ ] **bandit / mypy (advisory)** — *new* findings since last audit are reviewed,
      not just accumulated. Suppressed `B608` still justified.
- [ ] **CI matrix** — `.github/workflows/ci.yml` runs all gates; the gate list
      matches what AGENTS.md claims (pip-audit, bandit, mypy, PR-title).
- [ ] **pre-commit hooks** — `.pre-commit-config.yaml` revs current; ruff +
      gitleaks + yaml/toml/large-file checks active.
- [ ] **Release pipeline** — tag push triggers release; `release.yml` consistent
      with `release-checklist.md`.

---

## Scope 8 — Doc / test consistency

**What:** the surfaces that drift on every release (~10, per the docs-version-drift
memory). Run per release.

- [ ] **Version refs** — version string consistent across README, CHANGELOG,
      cli-reference, architecture docs, pyproject.
- [ ] **Tool counts / language counts** — "27 tools", "14 languages" etc. match
      reality (recount, don't trust the doc).
- [ ] **BUGS.md structure** — index table present, one TL;DR per entry, each entry
      maps to a regression test (per the structure convention).
- [ ] **Test markers** — `-m core` actually has tagged tests; markers aren't stale.
- [ ] **Tool-quirks table** — AGENTS.md §"Tool Quirks" matches current tool behavior.
- [ ] **Dead links** — every doc cross-link resolves.

---

## Triggers (run an audit outside cadence)

- **A bug fix in the scope** — audit the scope after the 3rd fix in the same area
  (recurrence signal).
- **Heavy churn** — >20% of a scope's LOC changed in a window.
- **Before a release** — run Tier 1 (scopes 1–3) before tagging.
- **New dependency / parser / client** — run the matching scope (5, 6, or 7).
- **Onboarding** — running a scope audit is the fastest way to learn an area.
