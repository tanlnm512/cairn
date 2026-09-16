# Test Cases: pr-review-loop

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-17
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.
All auto cases run against a fixture workspace and finish in seconds.

## Fixture contract

`tests/fixtures/review-loop/prepare.sh <scenario>` builds (idempotently,
in seconds) a scratch workspace indexed by cairn with a pending change
against `main`:

- Modules: `ledger` (consumed by `reporting` and `api`); `banner` (nothing
  consumes it).
- `pack`, `guard`, `gate`: a mistake memory ("never parse dates with
  regex") and a pattern memory ("prefer exponential backoff for retries"),
  both keyed to `ledger`; a navigation entry for `ledger` (mentions
  `ownership`); an architecture page for `ledger` (mentions `invariant`).
- `banner`: no memories, no navigation entry, no architecture page.
- `quiet`: the `ledger` memories above, but the pending change touches
  `banner` only.
- `types`: a decision memory ("cache-first reads"), keyed to `ledger`, with
  the change touching `ledger`.
- `gate`: the `guard` workspace plus gating turned on for the pre-submit
  check.
- `capture`: recorded review-thread events under
  `tests/fixtures/review-loop/events/`, each referencing pull request 42:
  - `resolved-comment.json` — accepted comment "stop parsing dates with
    regex" on changed `ledger` code;
  - `unresolved-comment.json` — the same comment still open;
  - `orphan-line-comment.json` — accepted comment "do not swallow errors"
    pinned to a line of `changelog.md` outside any module.

## TC-001 — Pack assembles all four context layers

- **Story**: US1 · **Traces to**: FR-001
- **Given** the `pack` workspace, where the pending change touches `ledger`
- **When** the review pack is produced for the diff against `main`
- **Then** one output lists the dependents (`reporting`, `api`) and cites
  the mistake, the navigation entry, and the architecture page — a reviewer
  needs no separate lookups
- **Pass condition**: `bash tests/fixtures/review-loop/prepare.sh pack && cairn review --base main --format text > pack.out && grep -q reporting pack.out && grep -q api pack.out && grep -qi regex pack.out && grep -qi ownership pack.out && grep -qi invariant pack.out && rm pack.out`

## TC-002 — Pack renders in text and in markdown

- **Story**: US1 · **Traces to**: FR-001
- **Given** the `pack` workspace
- **When** the pack is requested as markdown, then as text
- **Then** the markdown rendering is structured markdown (headings), and
  both renderings carry the pack content
- **Pass condition**: `bash tests/fixtures/review-loop/prepare.sh pack && cairn review --base main --format markdown | grep -q '^#' && cairn review --base main --format text | grep -q reporting`

## TC-003 — Change with no dependents

- **Story**: US1 · **Traces to**: FR-001
- **Given** the `banner` workspace, where nothing consumes the changed
  module
- **When** the review pack is produced for the diff against `main`
- **Then** the command exits 0 and the output states the change has no
  dependents
- **Pass condition**: `bash tests/fixtures/review-loop/prepare.sh banner && cairn review --base main > banner.out && grep -qiE "no (downstream |reverse )?dependents|zero dependents" banner.out && rm banner.out`

## TC-004 — Change with no enrichment available

- **Story**: US1 · **Traces to**: FR-001
- **Given** the `banner` workspace, which has no memories, navigation
  entries, or architecture pages for the changed module
- **When** the review pack is produced for the diff against `main`
- **Then** the command exits 0, the dependents statement is present, and no
  memory guidance is cited
- **Pass condition**: `bash tests/fixtures/review-loop/prepare.sh banner && cairn review --base main > banner.out && grep -qiE "no (downstream |reverse )?dependents|zero dependents" banner.out && ! grep -qi regex banner.out && rm banner.out`

## TC-005 — Pre-submit surfaces each matching memory as a warning

- **Story**: US2 · **Traces to**: FR-002
- **Given** the `guard` workspace, where a mistake and a pattern are keyed
  to `ledger` and the pending change touches `ledger`, with gating not
  configured
- **When** the pre-submit check runs
- **Then** each keyed memory surfaces as a warning carrying its recorded
  guidance, and the command exits 0 (matching memories alone do not fail
  the check)
- **Pass condition**: `bash tests/fixtures/review-loop/prepare.sh guard && cairn review --pre-submit > guard.out && grep -qi regex guard.out && grep -qi backoff guard.out && rm guard.out`

## TC-006 — Pre-submit with no keyed matches stays silent

- **Story**: US2 · **Traces to**: FR-002
- **Given** the `quiet` workspace, where recorded memories exist but none
  are keyed to the changed module
- **When** the pre-submit check runs
- **Then** no warnings appear and the command exits 0
- **Pass condition**: `bash tests/fixtures/review-loop/prepare.sh quiet && cairn review --pre-submit > quiet.out && ! grep -qi regex quiet.out && ! grep -qi backoff quiet.out && rm quiet.out`

## TC-007 — Memories of other types never warn

- **Story**: US2 · **Traces to**: FR-002
- **Given** the `types` workspace, where a decision memory is keyed to
  `ledger` and the pending change touches `ledger`
- **When** the pre-submit check runs
- **Then** the decision does not produce a warning and the command exits 0
- **Pass condition**: `bash tests/fixtures/review-loop/prepare.sh types && cairn review --pre-submit > types.out && ! grep -qi "cache-first" types.out && rm types.out`

## TC-008 — Gating configured turns matches into failure

- **Story**: US2 · **Traces to**: FR-002
- **Given** the `gate` workspace, where gating is turned on and a keyed
  mistake matches the change
- **When** the pre-submit check runs
- **Then** the command exits non-zero and the warning is still listed
- **Pass condition**: `bash tests/fixtures/review-loop/prepare.sh gate && { cairn review --pre-submit --gate > gate.out; test $? -ne 0; } && grep -qi regex gate.out && rm gate.out`

## TC-009 — Accepted comment becomes a keyed, linked memory

- **Story**: US3 · **Traces to**: FR-003
- **Given** the `capture` workspace, with an accepted review comment on
  changed `ledger` code, recorded as a thread event on pull request 42
- **When** the capture hook processes the event
- **Then** a memory carrying the comment's guidance exists, is keyed to
  `ledger`, links the pull-request thread, and is visible through the
  standard memory listing
- **Pass condition**: `bash tests/fixtures/review-loop/prepare.sh capture && cairn review --capture-event tests/fixtures/review-loop/events/resolved-comment.json && cairn memory list | grep -qi regex && cairn memory list | grep -q ledger && cairn memory list | grep -q "pull/42"`

## TC-010 — Open (unresolved) comment records nothing

- **Story**: US3 · **Traces to**: FR-003
- **Given** the `capture` workspace, with the same comment still open
- **When** the capture hook processes the event
- **Then** no memory carrying that guidance is recorded
- **Pass condition**: `bash tests/fixtures/review-loop/prepare.sh capture && cairn review --capture-event tests/fixtures/review-loop/events/unresolved-comment.json && ! cairn memory list | grep -qi regex`

## TC-011 — Comment outside any module degrades to file level

- **Story**: US3 · **Traces to**: FR-003
- **Given** the `capture` workspace, with an accepted comment pinned to a
  line of `changelog.md` outside any module
- **When** the capture hook processes the event
- **Then** a memory carrying that guidance is recorded, keyed to the file
  rather than dropped
- **Pass condition**: `bash tests/fixtures/review-loop/prepare.sh capture && cairn review --capture-event tests/fixtures/review-loop/events/orphan-line-comment.json && cairn memory list | grep -qi swallow && cairn memory list | grep -q "changelog.md"`

## TC-012 — Pull-request run posts findings as a PR comment

- **Story**: — · **Traces to**: FR-004
- **Given** a scratch GitHub repository with the review workflow installed
  and a mistake keyed to `ledger`
- **When** a pull request is opened whose change touches `ledger`
- **Then** the workflow run completes and the pull request shows one
  comment listing that mistake's guidance
- **Pass condition**: open such a pull request on the scratch repository and observe the pull request page: within the check run, one comment appears carrying the recorded guidance (human observation; not run by the audit)

## TC-013 — Clean pull request posts no findings

- **Story**: — · **Traces to**: FR-004
- **Given** the same scratch repository with the review workflow installed
- **When** a pull request is opened whose change touches `banner` only
- **Then** the workflow run succeeds and the pull request shows no findings
  comment
- **Pass condition**: open such a pull request on the scratch repository and observe the pull request page: the check succeeds and no findings comment appears (human observation; not run by the audit)

## TC-014 — Standing guard: pack facts match the standalone surfaces

- **Story**: — · **Traces to**: FR-005
- **Given** the `pack` workspace
- **When** the pack's dependents and cited guidance are compared against
  the standalone blast command and the standalone memory search
- **Then** they report the same facts: the pack composes existing surfaces
  rather than reimplementing them, and diverges the moment it stops doing so
- **Pass condition**: `bash tests/fixtures/review-loop/prepare.sh pack && cairn blast --base main | grep -q reporting && cairn review --base main | grep -q reporting && cairn memory search regex | grep -qi regex && cairn review --base main | grep -qi regex && cairn memory search backoff | grep -qi backoff && cairn review --pre-submit | grep -qi backoff`

## Coverage matrix

| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001 | TC-001, TC-002, TC-003, TC-004 | auto |
| FR-002 | TC-005, TC-006, TC-007, TC-008 | auto |
| FR-003 | TC-009, TC-010, TC-011 | auto |
| FR-004 | TC-012, TC-013 | manual |
| FR-005 | TC-014 | auto |

## Acceptance-criteria trace

| AC | TCs |
|---------|----------------|
| US1 AC1 | TC-001, TC-002 |
| US1 AC2 | TC-003 |
| US2 AC1 | TC-005 |
| US3 AC1 | TC-009 |

Untestable FRs: none. FR-004 is observable only through a real pull-request
round-trip, so its cases (TC-012, TC-013) are human-observed rather than
audit-run; every other FR is covered by auto cases.
