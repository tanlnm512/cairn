# Tasks: Benchmark Datasource — pinned baselines for future comparison

Companion: [spec.md](spec.md) · [plan.md](plan.md). Status reflects code
state on 0.11.0 @ `58c2a66` (surveyed 2026-08-16), not intent.

## Burndown

| Status | Count |
|--------|-------|
| done | 0 |
| partial | 0 |
| todo | 16 |
| **total** | **16** |

---

## DS-1 — Datasource manifest + T1 pinning

Done when: a `benchmarks/datasource/manifest.json` versions the T1
generator (seed, sizes, complexity, generator git-sha) and a CI check
regenerates T1 and asserts a content hash, so any corpus drift is a
deliberate, versioned act.

- [ ] **D1.1 — Manifest schema.** `benchmarks/datasource/manifest.json` with
      `dataset_version` (DS-v1), `t1` block (generator sha, DEFAULT_SEED,
      sizes, complexity profiles, expected file/symbol/edge counts), `t2`
      and `t3` blocks added by later tasks.
      verify: manifest round-trips (`python -c "json.load(...)"`), counts
      match a fresh `generate_corpus` run.
- [ ] **D1.2 — T1 content-hash CI check.** Job step regenerates the corpus
      from the manifest and asserts a deterministic tree hash (seeded,
      path-order-independent).
      verify: CI green twice in a row on unchanged code; red when the seed
      is edited (test the failure once in a scratch branch).

## DS-2 — T2 real-code snapshot vendored

Done when: a real multi-language repo snapshot (≤ 3 MB, permissive license,
provenance manifest naming upstream repo + commit + license) lives under
`benchmarks/datasource/t2/`, is exercised by a build+query smoke test, and
its license/attribution is recorded in `NOTICE`.

- [ ] **D2.1 — Selection.** Pick the snapshot (candidate shape: a small
      real Python+one-other-language repo with genuine call depth; record
      WHY in the manifest — fan-in/out distribution, docstring realism,
      size).
      verify: selection note in manifest; `du -sh` ≤ 3 MB.
- [ ] **D2.2 — Vendoring.** Copy in at the pinned upstream commit; strip
      VCS internals and binaries; add provenance (repo, commit, license
      text or link) + NOTICE entry.
      verify: `uv run cairn build --workspace benchmarks/datasource/t2/...`
      green; `find_definition` smoke returns the known symbol.
- [ ] **D2.3 — Size guard.** CI step fails if `benchmarks/datasource/`
      exceeds its budget (5 MB total).
      verify: guard triggers on an oversized dummy file in a scratch branch.

## DS-3 — Ground-truth QA set

Done when: ≥ 50 L1 queries (definition/callers/impact/flow with
hand-verified expected symbols) and ≥ 20 L5 queries are committed under
`benchmarks/datasource/t2/ground_truth/` with a schema `eval.py` consumes,
and a validation script re-verifies every expectation against a freshly
built graph (no stale answers).

- [ ] **D3.1 — Schema + adapter.** Ground-truth file format (query, kind,
      expected symbol ids/files, rationale, author-verified flag) and an
      `eval.py` adapter so L1/L5 runs consume it directly.
      verify: `eval.py` reports recall/MRR over the file (even with a
      partial set).
- [ ] **D3.2 — Author 50 L1 + 20 L5 entries.** Verified against a fresh
      build of the T2 snapshot; each entry cites the snapshot file proving
      the expectation.
      verify: `uv run python scripts/verify_ground_truth.py` exit 0.
- [ ] **D3.3 — Staleness guard.** The validator runs in CI (advisory) so a
      resolution change that invalidates an expectation surfaces
      immediately with the failing query id.
      verify: intentionally break one expectation in a scratch branch →
      advisory comment names it.

## DS-4 — Baseline artifacts + compare resolution

Done when: `benchmarks/baselines/DS-v1/` holds perf/scaling/agent/
retrieval JSON artifacts stamped with dataset version, cairn version, and
machine profile; `cairn bench --compare --baseline DS-v1` resolves them;
machine-profile mismatch emits the warning header.

- [ ] **D4.1 — Machine profile stamping.** `report.py` artifacts gain a
      `profile` block (arch, cpu count, runner class: reference-local |
      ci-ubuntu-latest) + `dataset_version` + `cairn_version`.
      verify: a saved baseline JSON contains all three.
- [ ] **D4.2 — `--baseline` resolution.** `cairn bench --compare
      --baseline DS-v1` loads the committed artifact (per suite); profile
      mismatch prints a warning header before the table.
      verify: compare renders against DS-v1; forced-mismatch fixture shows
      the warning.
- [ ] **D4.3 — Record DS-v1 baselines.** Generate perf/scaling/agent/
      retrieval artifacts on the reference machine, commit under
      `benchmarks/baselines/DS-v1/`.
      verify: all four artifacts present; regeneration command documented.

## DS-5 — Reference tables generated

Done when: the three `_fill_` families in `docs/benchmarks.md` (retrieval
quality, perf, scaling) are generated from the committed baselines by a
documented one-command regeneration, and hand-editing those tables is no
longer possible without breaking a CI check.

- [ ] **D5.1 — Table generator.** Script renders the three table families
      from baseline JSON into `docs/benchmarks.md` between sentinel
      markers.
      verify: regeneration is byte-idempotent on unchanged baselines.
- [ ] **D5.2 — CI guard.** Step regenerates and diffs — hand-edited tables
      fail the check.
      verify: `_fill_` count in benchmarks.md reaches zero; scratch-branch
      hand-edit turns the check red.

## DS-6 — CI wiring

Done when: the rolling-cache advisory job additionally reports against the
committed DS-v1 baseline (labeled by dataset version), the
retrieval-quality eval runs on T2 in CI (advisory), and T3 manifests + a
documented local command cover the scale tier.

- [ ] **D6.1 — Advisory comment gains "vs DS-v1".** The bench job's PR
      comment shows both comparisons (rolling and pinned), labeled.
      verify: comment on a test PR contains both lines.
- [ ] **D6.2 — Retrieval eval in CI.** L1/L5 eval on T2 runs advisory,
      posting recall/MRR.
      verify: green run posts numbers; zero network access.
- [ ] **D6.3 — T3 manifest + local command.** Manifest entries for 2–3
      pinned external repos at scale points; documented
      `cairn bench --suite scaling --dataset t3/<name>` local flow (fetches
      by pin, caches outside the repo).
      verify: one local end-to-end T3 run recorded in the PR description.

---

## Post-phase hygiene

- `cairn update` + `record_memory(type="decision")` for the T2 selection
  rationale and the dataset-versioning policy (new dir per version, never
  edit a shipped baseline).
