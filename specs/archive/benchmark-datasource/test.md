# Test Cases: benchmark-datasource

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-16
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.

Conventions:
- "The datasource manifest" / "the T1 manifest" = the committed versioning
  record for the synthetic corpus; "the T3 manifest" = the pinned external-repo
  record. "A bench artifact" = any JSON a bench run saves.
- Pass conditions naming "the CI job" refer to the checks this spec wires into
  CI; pass conditions naming exit codes assume the agent-facing CLI contract
  (exit 0 = success, non-zero = failure, output/JSON fields observable).
- "Scratch experiment" = performed on a throwaway copy/branch, never committed;
  the committed state must show the passing side.
- Cases marked **regression guard** protect machinery that is PARTIAL today;
  their pass condition cites the pre-existing verify command recorded in
  survey.md.

## US1 — Attributable regression comparison

### TC-001 — T1 manifest records the full generation recipe
- **Story**: US1 · **Traces to**: FR-001
- **Given** the committed datasource manifest for the synthetic corpus
- **When** the manifest is inspected and its recipe is replayed (regenerate the corpus exactly as the manifest prescribes)
- **Then** the manifest names the generator's version, the randomness seed, every corpus size, the complexity profile, and the expected file/symbol/edge counts — and a regenerated corpus produces exactly the recorded counts
- **Pass condition**: human reads the manifest: all five fact groups present, none blank; a regeneration run at the manifest's own recipe reports counts equal to the manifest's expected counts (no "N/A" or missing entry).

### TC-002 — Regenerated corpus hashes to the manifest value on any runner (AC2 happy path)
- **Story**: US1 · **Traces to**: FR-001, AC2
- **Given** the committed T1 manifest with its recorded content hash
- **When** CI regenerates the corpus from scratch on a runner, at every size the manifest declares (including the largest)
- **Then** the regenerated corpus's content hash equals the manifest's recorded hash and the verification job passes
- **Pass condition**: the CI corpus-verification job exits 0; its log shows the computed hash matching the manifest value at each declared size.

### TC-003 — Any drift between generator and manifest fails the job (AC2 failure path)
- **Story**: US1 · **Traces to**: FR-001, AC2
- **Given** the committed T1 manifest
- **When** any generation input changes without the manifest being updated (scratch experiment: bump the seed, change a size, or edit the generator, leaving the manifest untouched)
- **Then** the hash check fails and the CI job fails
- **Pass condition**: scratch experiment — the verification exits non-zero and its message points at the manifest/hash mismatch; the untouched committed state exits 0.

### TC-004 — Hash is path-order independent across runner environments
- **Story**: US1 · **Traces to**: FR-001, AC2
- **Given** the same manifest recipe, regenerated on two different runner environments (different OS/filesystem, hence different file enumeration order)
- **When** both regenerated corpora are hashed
- **Then** the two hashes are identical and both match the manifest
- **Pass condition**: run the regeneration+hash check on two environments (e.g. a macOS local run and the Linux CI job); the printed hashes are equal — no diff.

### TC-005 — Same recipe always yields the same corpus (determinism substrate) — regression guard
- **Story**: US1 · **Traces to**: FR-001
- **Given** the corpus generator's documented determinism (output depends only on seed, size, complexity) — the load-bearing PARTIAL machinery
- **When** the identical recipe is run twice into two fresh locations
- **Then** the two generated trees are content-identical
- **Pass condition**: two fresh generations at the same seed/size/complexity compare equal (`diff -r` of the two trees reports no differences); this must keep holding after the manifest/hash work lands.

### TC-006 — Every bench artifact carries dataset version, cairn version, and machine profile
- **Story**: US1 · **Traces to**: FR-004
- **Given** a bench run saving a machine-readable artifact
- **When** the saved artifact is inspected
- **Then** it records the dataset version, the cairn version, and a machine profile covering architecture, CPU count, and runner class
- **Pass condition**: run a small bench with JSON save; the saved artifact contains all three stamp groups; the cairn version in the artifact equals the version the installed CLI reports.

### TC-007 — Compare resolves a committed baseline by dataset version and headers it (AC1 happy path)
- **Story**: US1 · **Traces to**: FR-004, AC1
- **Given** committed baselines under the baselines directory for a named dataset version (e.g. DS-v1)
- **When** `cairn bench --compare --baseline <DS-version>` runs
- **Then** the comparison renders with a dataset-version header naming the resolved baseline and completes with its documented exit semantics (non-zero only on regression, as before)
- **Pass condition**: command output contains a visible dataset-version header (the requested version appears in the rendered comparison); absent a regression, exit 0.

### TC-008 — Unknown dataset version fails with a clear error
- **Story**: US1 · **Traces to**: FR-004
- **Given** no baseline committed for version "does-not-exist"
- **When** `cairn bench --compare --baseline does-not-exist` runs
- **Then** the command fails promptly with a message naming the missing version (and ideally the versions that do exist)
- **Pass condition**: non-zero exit; the error text mentions "does-not-exist"; no partial/garbage comparison is rendered.

### TC-009 — Machine-profile mismatch triggers a loud warning (AC1 failure path)
- **Story**: US1 · **Traces to**: FR-004, AC1
- **Given** a baseline stamped with a machine profile different from the current machine (different architecture or runner class)
- **When** `cairn bench --compare --baseline <version>` runs
- **Then** a loud, unmissable warning about the profile mismatch renders alongside the dataset-version header
- **Pass condition**: comparing against a baseline stamped with a different runner class prints a clearly-marked warning (visible in plain terminal output and CI logs, not buried in JSON) naming the mismatched profile fields.

### TC-010 — Matching profile produces no false warning
- **Story**: US1 · **Traces to**: FR-004, AC1
- **Given** a baseline whose stamp matches the current machine profile
- **When** the same compare runs
- **Then** no mismatch warning appears
- **Pass condition**: output of the matching-profile compare contains no mismatch-warning marker (contrast with TC-009 on the same surface).

### TC-011 — Profile mismatch in CI stays advisory — standing guard
- **Story**: US1 · **Traces to**: FR-004
- **Given** the standing posture that CI comparisons remain advisory (a profile mismatch warns, never gates)
- **When** a CI bench comparison encounters a machine-profile mismatch against the committed baseline
- **Then** the CI job does not fail because of the mismatch — the warning is rendered and the comparison completes
- **Pass condition**: a CI run whose runner profile differs from the committed baseline's stamp stays green on the comparison step while printing the mismatch warning; introducing a hard gate on the mismatch would flip this guard red.

### TC-012 — Existing comparison semantics survive the extension — regression guard
- **Story**: US1 · **Traces to**: FR-004
- **Given** the existing advisory compare machinery (thresholds, regression exit signal) is PARTIAL but load-bearing
- **When** the pre-existing bench suites run
- **Then** they pass unchanged — the `--baseline` extension did not alter documented compare behavior (regression flagging and its exit code)
- **Pass condition**: pre-existing verify command from survey.md passes: `.venv/bin/python -m pytest tests/test_bench.py tests/test_agent_suite.py -p no:cacheprovider -q` → all passed (34 as of survey).

## US2 — Realistic, licensed benchmark content

### TC-013 — Vendored snapshot builds green and answers a known-symbol query (AC3)
- **Story**: US2 · **Traces to**: FR-002, AC3
- **Given** the vendored real-code snapshot in the datasource tree
- **When** the CI build+query smoke test runs against it
- **Then** the graph builds without error and a query for a symbol known to exist in the snapshot returns that symbol
- **Pass condition**: the CI smoke job exits 0; its query step's visible output includes the expected known symbol (name present in the printed/JSON result).

### TC-014 — Snapshot exercises genuine multi-language call shapes
- **Story**: US2 · **Traces to**: FR-002, AC3
- **Given** the vendored snapshot claims real multi-language code with genuine call relationships
- **When** the snapshot tree is inspected and a cross-file callers query for a known symbol is run on the built graph
- **Then** the tree contains source in at least two languages, and the callers query returns at least one caller defined in a different file than the callee
- **Pass condition**: human observation of the snapshot tree (≥2 languages present); the smoke (or an equivalent query run) prints a caller whose location differs from the callee's file.

### TC-015 — Provenance record names upstream repo, commit, and license
- **Story**: US2 · **Traces to**: FR-002
- **Given** the vendored snapshot
- **When** its provenance record is inspected
- **Then** it names the upstream repository, the exact upstream commit, and the license, and that license is a permissive one permitting vendoring with attribution
- **Pass condition**: human reads the provenance record shipped with the snapshot: all three fields present and concrete (a real URL, a full commit id, a named permissive license).

### TC-016 — NOTICE carries attribution for the vendored content
- **Story**: US2 · **Traces to**: FR-002
- **Given** the repository's NOTICE file
- **When** inspected after the snapshot is vendored
- **Then** it contains a vendored-content attribution section naming the upstream project and its license
- **Pass condition**: human observation — NOTICE has a section attributable to the vendored snapshot (upstream name + license visible), distinct from the pre-existing dependency listing.

### TC-017 — Snapshot respects its individual size budget
- **Story**: US2 · **Traces to**: FR-002, AC4
- **Given** the size budget check with a ≤ 3 MB limit for the vendored snapshot
- **When** the snapshot tree is measured
- **Then** it is at most 3 MB and the check passes
- **Pass condition**: the size check exits 0 on the committed tree; a scratch copy padded past 3 MB makes the same check exit non-zero.

### TC-018 — Datasource tree respects its total budget or CI fails (AC4 failure path)
- **Story**: US2 · **Traces to**: FR-002, AC4
- **Given** the CI size budget check scoped to the whole datasource tree (5 MB total)
- **When** the datasource tree exceeds the budget
- **Then** CI fails
- **Pass condition**: scratch branch — add padding files to the datasource tree until it exceeds 5 MB; the CI size job fails (non-zero). The committed tree measures under budget and the job passes.

## US3 — Quality trend lines

### TC-019 — Ground-truth set meets size thresholds and schema (AC5 given)
- **Story**: US3 · **Traces to**: FR-003, AC5
- **Given** the committed ground-truth query set for the vendored snapshot
- **When** its entries are counted and inspected
- **Then** there are at least 50 code-level (L1) queries and at least 20 knowledge-level (L5) queries; every L1 entry carries its expected symbols and a rationale that cites the snapshot
- **Pass condition**: a counting run (validator or CI check) reports L1 ≥ 50 and L5 ≥ 20 with no schema-incomplete entry; human spot-checks ≥5 rationales and finds each references something actually present in the snapshot.

### TC-020 — Code queries cover all four required kinds
- **Story**: US3 · **Traces to**: FR-003
- **Given** the L1 ground-truth entries must span definition, callers, impact, and flow question kinds
- **When** the entries are tallied by kind
- **Then** each of the four kinds has at least one query
- **Pass condition**: the tally (validator report or a count over the query set) shows ≥1 query per kind; no kind is absent.

### TC-021 — Validator re-verifies every expectation on a fresh build (AC5 happy path)
- **Story**: US3 · **Traces to**: FR-003, AC5
- **Given** a freshly built graph over the vendored snapshot (built now, not a cached graph)
- **When** the ground-truth validator runs
- **Then** every expectation verifies; the validator exits 0 with an all-green summary
- **Pass condition**: the documented validator command exits 0 on a fresh build; its report shows zero unverified expectations.

### TC-022 — Validator names the stale entry instead of failing silently (AC5 failure path)
- **Story**: US3 · **Traces to**: FR-003, AC5
- **Given** a ground-truth entry made stale (scratch experiment: an expectation pointing at a symbol absent from the snapshot)
- **When** the validator runs against a fresh build
- **Then** it names the stale entry (query + missing expectation) and exits non-zero
- **Pass condition**: scratch experiment — validator output identifies the tampered entry by its query text and missing expected symbol, exit non-zero; the committed set passes (exit 0).

### TC-023 — Existing recall/MRR evaluation harness still works end-to-end — regression guard
- **Story**: US3 · **Traces to**: FR-003
- **Given** the existing evaluation command (the PARTIAL harness the ground truth will feed)
- **When** it runs with JSON output against a built graph
- **Then** it reports L1 and L5 recall/MRR figures and exits 0 — still consumable after the new ground truth replaces the old query set
- **Pass condition**: pre-existing verify command from survey.md passes: `.venv/bin/python -m pytest tests/test_cli_smoke.py -p no:cacheprovider -q` → all passed (covers the evaluation CLI end-to-end); additionally `cairn eval --json` against the new ground truth prints an L1/L5 block and exits 0.

## US4 — Generated reference tables

### TC-024 — Generator replaces every placeholder cell (AC6)
- **Story**: US4 · **Traces to**: FR-005, AC6
- **Given** committed baseline artifacts for a dataset version
- **When** the reference-table generator runs against the benchmark doc
- **Then** all placeholder cells in the three reference-table families (retrieval quality, perf, scaling) are replaced by real values drawn from the baselines; if a family's baseline is missing the generator fails loudly rather than re-emitting placeholders
- **Pass condition**: after generation, a search for the placeholder marker in the doc returns zero occurrences; each of the three tables shows numeric values; a scratch run with one baseline removed exits non-zero with a message naming the missing family.

### TC-025 — Regeneration is byte-idempotent (AC6)
- **Story**: US4 · **Traces to**: FR-005, AC6
- **Given** a doc whose tables were already generated from the committed baselines
- **When** the generator runs again with no new baselines
- **Then** the doc is byte-identical to before the second run
- **Pass condition**: checksum the doc, run the generator, checksum again — identical; `git diff` after the second run is empty.

### TC-026 — Table values trace to committed baselines, not invention
- **Story**: US4 · **Traces to**: FR-005
- **Given** a committed baseline artifact and the generated table
- **When** a sampled table cell is compared with the baseline's corresponding recorded value
- **Then** they are equal (same number, derived only by documented rounding/units)
- **Pass condition**: human or CI cross-check of at least one cell per family against the baseline JSON it claims to come from — values match.

### TC-027 — A hand edit between the sentinels fails the CI check (AC6 failure path)
- **Story**: US4 · **Traces to**: FR-005, AC6
- **Given** the generated tables sit between sentinel markers and a CI check guards them
- **When** a cell inside a marked region is hand-edited (scratch experiment) without regenerating
- **Then** the CI check fails
- **Pass condition**: scratch edit of one cell → the doc check exits non-zero naming the modified region; the untouched doc → exits 0.

### TC-028 — Generation never touches content outside the marked regions
- **Story**: US4 · **Traces to**: FR-005
- **Given** the benchmark doc contains prose and hand-authored tables alongside the three generated families
- **When** the generator runs
- **Then** only the content between the sentinel markers changes; everything outside is byte-identical
- **Pass condition**: `git diff` of the doc after a regeneration that did change table content shows hunks only inside marked regions; surrounding lines show no modifications.

## US5 — Scale coverage without vendoring

### TC-029 — T3 manifest pins at least two scale points
- **Story**: US5 · **Traces to**: FR-006
- **Given** the committed T3 manifest for external scale repositories
- **When** inspected
- **Then** it contains at least two entries at distinct scale points, each pinning a repository URL and an exact commit
- **Pass condition**: human reads the manifest — ≥2 entries, each with a concrete URL and a full commit id, at ≥2 different declared sizes/scale points.

### TC-030 — Local command fetches exactly the pinned commit (AC7)
- **Story**: US5 · **Traces to**: FR-006, AC7
- **Given** a T3 manifest entry and the documented local command
- **When** the command runs
- **Then** the fetched content is exactly the pinned commit — never the upstream default branch head — and the command succeeds
- **Pass condition**: after the run, the fetched checkout's commit id equals the manifest pin (visible in the command's output or the checkout); exit 0; a later upstream move does not change what is fetched for the same pin.

### TC-031 — Scale-run results record which manifest entry produced them (AC7)
- **Story**: US5 · **Traces to**: FR-006, AC7
- **Given** a completed T3 scale run
- **When** its result artifact is inspected
- **Then** it names the manifest entry used — repository, pinned commit, and scale point
- **Pass condition**: the run's saved result contains fields identifying the manifest entry (repo + commit + size); the values equal the manifest's.

### TC-032 — Unreachable pin fails loudly, no silent fallback
- **Story**: US5 · **Traces to**: FR-006
- **Given** a manifest entry whose pinned commit cannot be fetched (scratch experiment: point an entry at a bogus commit)
- **When** the local command runs
- **Then** it fails with an error naming the entry and never falls back to fetching the default branch
- **Pass condition**: scratch experiment — non-zero exit, message names the failing entry/commit; the fetched location (if any partial state exists) does not correspond to an upstream HEAD.

### TC-033 — CI never fetches T3 content — standing guard
- **Story**: US5 · **Traces to**: FR-006
- **Given** the standing constraint that CI stays network-free for T3 (the pinned-fetch command is local-only, documented as such)
- **When** the CI workflows run
- **Then** no CI job performs the T3 fetch or requires external repository hosts
- **Pass condition**: CI passes with no T3 fetch step (workflow inspection shows the scale-fetch command absent from all CI jobs; CI runs green in an environment where external git hosts are unreachable); any future wiring of the T3 fetch into a CI job makes this guard fail by observation.

## Coverage matrix
<!-- Every FR appears; `check.py` fails an FR with no TC. -->

| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001      | TC-001, TC-002, TC-003, TC-004, TC-005 | TC-002/003/004 auto (CI); TC-001/005 auto w/ human spot-check |
| FR-002      | TC-013, TC-014, TC-015, TC-016, TC-017, TC-018 | TC-013/017/018 auto (CI); TC-014/015/016 manual (human inspection) |
| FR-003      | TC-019, TC-020, TC-021, TC-022, TC-023 | TC-020/021/022/023 auto; TC-019 auto (counts) + manual (rationale spot-check) |
| FR-004      | TC-006, TC-007, TC-008, TC-009, TC-010, TC-011, TC-012 | TC-006..010 auto (CLI observables); TC-011 auto (CI posture); TC-012 auto (existing suite) |
| FR-005      | TC-024, TC-025, TC-026, TC-027, TC-028 | TC-024/025/027/028 auto (checksums/CI); TC-026 auto-or-manual (cross-check) |
| FR-006      | TC-029, TC-030, TC-031, TC-032, TC-033 | TC-030/031/032 auto (local command); TC-029 manual; TC-033 auto (standing CI guard) |

## Acceptance-criteria trace

| AC | Story | Covers |
|----|-------|--------|
| AC1 | US1 | TC-007 (header happy path), TC-009 (loud mismatch warning), TC-010 (no false warning), TC-006 (stamps make the header possible) |
| AC2 | US1 | TC-002 (hash matches), TC-003 (job fails on drift), TC-004 (any runner) |
| AC3 | US2 | TC-013 (builds green + known-symbol answer), TC-014 (genuine multi-language shapes) |
| AC4 | US2 | TC-017 (3 MB snapshot budget), TC-018 (5 MB total budget → CI fails) |
| AC5 | US3 | TC-021 (every expectation verifies), TC-022 (names the stale entry), TC-019 (≥50/≥20 givens) |
| AC6 | US4 | TC-024 (placeholders replaced), TC-025 (byte-idempotent), TC-027 (hand-edit fails CI) |
| AC7 | US5 | TC-030 (fetch by pinned commit), TC-031 (results record the manifest entry) |

## Notes for implementers (no scope change)

- Untestable FRs: none — every FR has an observable surface (CLI output/exit
  codes, CI jobs, committed artifacts, or human-inspectable records).
- Spec-smell watch (minor, not blocking): FR-002's "genuine call shapes" is
  only observably testable via proxies (provenance of real upstream code +
  cross-file caller query — TC-014/TC-015); if reviewers want stronger
  verification, the spec would need a measurable definition of "genuine".
- Standing guards worth CI wiring even though nothing today violates them:
  TC-011 (advisory posture preserved) and TC-033 (CI never fetches T3) — both
  exist to fail loudly if the constraint regresses.
