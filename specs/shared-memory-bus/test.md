# Test Cases: shared-memory-bus

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-17
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details. Derived from
spec.md only — tech-spec.md and plan.md were never read (implementation
blindness is the point).

## How to read (suite-level harness notes)

- Unless a TC's Given says otherwise, every TC runs in a **fresh fixture
  workspace**: an initialized cairn store with two simulated agent sessions,
  `agentA` and `agentB`, neither having prior activity. Memories a TC treats
  as pre-existing are seeded through the shipped record flow before it runs.
  Each auto pass condition creates its fixture workspace as a throwaway
  store (`T=$(mktemp -d)`, passed via `--db`/`--knowledge`), so TCs are
  self-isolating and never touch the default store.
- Auto pass-condition commands invoke the repo venv binary
  `/Users/tanle/Projects/cairn/.venv/bin/cairn`, not a PATH shim.
- `cairn memory share [MEMORY_ID] --agent <id> --symbols a,b,c` is the
  spec-pinned share surface (FR-001); a share of a seeded memory passes that
  memory's id as MEMORY_ID, and a recall surfaces shared entries only when
  the recalling agent's own `--agent` id is supplied (read-through
  attribution).
- `cairn memory search <term>` in a TC means "a recall for `<term>` through
  any shipped recall surface". If the shipped spelling differs, substitute
  it — the promise tested is the recall result, not the spelling.
- The overlap check is written `cairn memory check --agent <id> --symbols
  a,b,c`. The spec names no spelling for this surface; the shipped spelling
  governs — substitute whatever the shipped help documents. The promise
  tested is the warning text, not the command name.
- Commands whose contract is "the product rejects this" are wrapped with
  `; test $? -ne 0`, so the pass-condition line exits 0 exactly when the
  product correctly refused.

## TC-001 — Shared memory surfaces in another agent's recall

- **Story**: US1 · **Traces to**: FR-001
- **Given** agentA holds a learning about `auth` (fixture-seeded) and agentB
  has a fresh session that never recorded it.
- **When** agentA shares that learning to `auth,session`, then agentB recalls
  for `auth`.
- **Then** agentB's recall surfaces the learning; no registration or any
  other setup step was needed for either agent id beforehand.

**Pass condition**: `bash -c 'T=$(mktemp -d) && mkdir -p "$T/k" && M=$(/Users/tanle/Projects/cairn/.venv/bin/cairn memory record pattern "Auth token rule" --body "agentA learning about auth token refresh" --db "$T/g.db" --knowledge "$T/k" | sed -E "s/.* -> (.*) \(score=.*/\1/") && /Users/tanle/Projects/cairn/.venv/bin/cairn memory share "$M" --agent agentA --symbols auth,session --db "$T/g.db" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory search auth --agent agentB --db "$T/g.db" --knowledge "$T/k" | grep "shared by agentA on .auth"'`
The listed output includes agentA's learning. Both agent ids above are
first-use ids: nothing ran before the share in this fixture.

## TC-002 — Re-sharing the same symbols does not duplicate

- **Story**: US1 · **Traces to**: FR-001
- **Given** agentA shared its `auth` learning to `auth` once and agentB's
  recall surfaces exactly one copy.
- **When** agentA shares the same learning to the same symbol list again.
- **Then** a further recall still surfaces exactly one copy — no duplicates.

**Pass condition**: `bash -c 'T=$(mktemp -d) && mkdir -p "$T/k" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory share --agent agentA --symbols auth --db "$T/g.db" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory share --agent agentA --symbols auth --db "$T/g.db" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory search auth --db "$T/g.db" --knowledge "$T/k"'`
The recall output contains the shared learning exactly once.

## TC-003 — Boundary: empty symbol list is not a silent broadcast

- **Story**: US1 · **Traces to**: FR-001
- **Given** agentA holds a learning and intends to share it.
- **When** agentA shares with an empty symbol list.
- **Then** the command either fails with a clear validation message or
  succeeds while explicitly reporting that no symbols were shared; it never
  crashes and never makes the learning visible for symbols that were not
  listed.

**Pass condition**: human observation — run the share with an empty `--symbols` value and inspect the outcome against the Then above.

## TC-004 — Boundary: a wide symbol list is fully effective

- **Story**: US1 · **Traces to**: FR-001
- **Given** agentA holds one learning and shares it to a comma-separated list
  of 50 distinct symbols.
- **When** agentB recalls for any one of the listed symbols (say the 38th).
- **Then** the recall surfaces the learning — every symbol in the list
  carries the share.

**Pass condition**: `bash -c 'T=$(mktemp -d) && mkdir -p "$T/k" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory share --agent agentA --symbols $(python3 -c "print(\",\".join(\"sym%02d\" % i for i in range(50)))") --db "$T/g.db" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory search sym37 --db "$T/g.db" --knowledge "$T/k"'`
The recall output includes the shared learning.

## TC-005 — Overlap warning names the other agent and the shared symbols

- **Story**: US2 · **Traces to**: FR-002
- **Given** agentA recently shared memory for `auth,session`.
- **When** agentB checks its intended edit set `auth,users` for overlap.
- **Then** the check warns, naming agentA and the overlapping symbol `auth`;
  `users`, which has no overlap, is not reported as overlapping. (If the
  shipped check command is spelled differently, substitute it — see How to
  read.)

**Pass condition**: `bash -c 'T=$(mktemp -d) && mkdir -p "$T/k" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory share --agent agentA --symbols auth,session --db "$T/g.db" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory check --agent agentB --symbols auth,users --db "$T/g.db"'`
The output names agentA and `auth` as the overlap.

## TC-006 — No overlap, no warning

- **Story**: US2 · **Traces to**: FR-002
- **Given** agentA recently shared memory for `auth` only.
- **When** agentB checks an edit set with no overlap (`parser,wiki`).
- **Then** the check completes without any warning naming agentA or any
  symbol.

**Pass condition**: `bash -c 'T=$(mktemp -d) && mkdir -p "$T/k" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory share --agent agentA --symbols auth --db "$T/g.db" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory check --agent agentB --symbols parser,wiki --db "$T/g.db"'`
The output contains no overlap warning.

## TC-007 — An agent's own activity is not an overlap

- **Story**: US2 · **Traces to**: FR-002
- **Given** agentB itself recently shared memory for `auth` (and no other
  agent did).
- **When** agentB checks `auth`.
- **Then** no warning is produced: the overlap contract is against *other*
  agents' activity, and one's own recent activity must not block one's own
  edit.

**Pass condition**: `bash -c 'T=$(mktemp -d) && mkdir -p "$T/k" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory share --agent agentB --symbols auth --db "$T/g.db" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory check --agent agentB --symbols auth --db "$T/g.db"'`
The output contains no overlap warning.

## TC-008 — Edit activity alone triggers the overlap warning

- **Story**: US2 · **Traces to**: FR-002
- **Given** agentA's recent session edited symbols in this workspace (its
  editing activity was recorded by the running environment), with no memory
  shared for those symbols.
- **When** agentB checks an edit set that includes those symbols.
- **Then** the warning names agentA and the overlapping symbols — edit
  activity, not only shared memory, counts as overlap.

**Pass condition**: human observation — perform a short editing session as agentA on a fixture symbol, then run the overlap check as agentB for that symbol and confirm the warning names agentA.

## TC-009 — Standing guard: surfacing needs no polling or setup step

- **Story**: US1 · **Traces to**: FR-003
- **Given** agentA holds a learning about `session` and agentB has a fresh
  session.
- **When** agentA shares to `session` and agentB immediately recalls — with
  NO command in between: no subscribe, no refresh, no registration, no
  polling.
- **Then** that first bare recall already surfaces the learning. If any
  intermediate step ever becomes required for correctness, this guard fails.
  An optional notification mechanism may exist, but the recall alone must
  suffice.

**Pass condition**: `bash -c 'T=$(mktemp -d) && mkdir -p "$T/k" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory share --agent agentA --symbols session --db "$T/g.db" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory search session --db "$T/g.db" --knowledge "$T/k"'`
The recall output includes the learning; the two commands above are adjacent
by construction, so nothing but the recall produced it.

## TC-010 — Standing guard: single-agent memory behavior is unchanged

- **Story**: US3 · **Traces to**: FR-004
- **Given** a workspace where sharing is never used and no `--agent` value is
  ever supplied.
- **When** the pre-existing memory operations run (listing, stats, and the
  record-then-search round-trip through the shipped surfaces).
- **Then** every memory subcommand that existed before this feature still
  runs, none demands an agent id, output format matches the feature-absent
  baseline, and a fixture-seeded memory still appears in listing and recall.

**Pass condition**: `bash -c 'T=$(mktemp -d) && mkdir -p "$T/k" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory record pattern "Baseline single-agent memory" --body "fixture memory recorded before the baseline listing" --db "$T/g.db" --knowledge "$T/k" >/dev/null && /Users/tanle/Projects/cairn/.venv/bin/cairn memory list --db "$T/g.db" --knowledge "$T/k" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory stats --knowledge "$T/k"'`
Both succeed with no `--agent` option, and the fixture-seeded memory appears
in the listing; its output shape matches the pre-feature baseline.

## TC-011 — Standing guard: store keeps WAL journal mode and busy timeout

- **Story**: US2 · **Traces to**: FR-005
- **Given** the store's connection setup as shipped today.
- **When** the store opens for writing.
- **Then** the journal mode is WAL and a busy timeout is set on every
  connection — the concurrency substrate the share and overlap surfaces rely
  on must not regress. Cites the survey S1 verify command verbatim, as the
  sanctioned standing guard.

**Pass condition**: `sed -n '837,855p' src/cairn/graph/schema.py`
Human observation: the connection setup still contains the WAL journal-mode
setting and the busy-timeout setting.

## TC-012 — Two agents writing concurrently do not collide or wedge

- **Story**: US1 · **Traces to**: FR-005
- **Given** agentA and agentB working the same workspace at the same time.
- **When** both share memories concurrently (different symbols), started
  simultaneously in the background.
- **Then** both shares succeed with no lock error; a recall for each symbol
  surfaces the respective shared memory; the store reports consistent stats
  afterwards.

**Pass condition**: `bash -c 'T=$(mktemp -d) && mkdir -p "$T/k"; /Users/tanle/Projects/cairn/.venv/bin/cairn memory share --agent agentA --symbols auth --db "$T/g.db" >"$T/a.log" 2>&1 & /Users/tanle/Projects/cairn/.venv/bin/cairn memory share --agent agentB --symbols parser --db "$T/g.db" >"$T/b.log" 2>&1 & wait && grep -q "Shared for agent .agentA." "$T/a.log" && grep -q "Shared for agent .agentB." "$T/b.log" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory search auth --db "$T/g.db" --knowledge "$T/k" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory search parser --db "$T/g.db" --knowledge "$T/k" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory stats --knowledge "$T/k"'`
Both recalls surface their entries and stats completes without error; no
"database is locked" failure appears anywhere in the output.

## TC-013 — Concurrent shares of the same symbol lose nothing

- **Story**: US1 · **Traces to**: FR-005
- **Given** agentA and agentB each hold a learning and both target the same
  symbol `hot` at the same moment.
- **When** both shares run concurrently.
- **Then** both succeed, and a recall for `hot` reflects both agents' entries
  — no lost update, no corruption.

**Pass condition**: `bash -c 'T=$(mktemp -d) && mkdir -p "$T/k"; /Users/tanle/Projects/cairn/.venv/bin/cairn memory share --agent agentA --symbols hot --db "$T/g.db" >"$T/a.log" 2>&1 & /Users/tanle/Projects/cairn/.venv/bin/cairn memory share --agent agentB --symbols hot --db "$T/g.db" >"$T/b.log" 2>&1 & wait && grep -q "Shared for agent .agentA." "$T/a.log" && grep -q "Shared for agent .agentB." "$T/b.log" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory search hot --db "$T/g.db" --knowledge "$T/k"'`
Both agents' entries are represented in the recall result for `hot`.

## TC-014 — Per-agent namespaces keep own memories while reading through shares

- **Story**: US1 · **Traces to**: FR-005
- **Given** agentA holds learning m1 (shared to `auth`) and agentB holds
  learning m2 (shared to `parser`).
- **When** agentB recalls for `auth` and then for `parser`.
- **Then** the `auth` recall surfaces agentA's m1 (read-through of the share)
  and the `parser` recall still surfaces agentB's own m2 — an agent's own
  namespace stays intact while shared content reads through.

**Pass condition**: `bash -c 'T=$(mktemp -d) && mkdir -p "$T/k" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory share --agent agentA --symbols auth --db "$T/g.db" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory share --agent agentB --symbols parser --db "$T/g.db" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory search auth --db "$T/g.db" --knowledge "$T/k" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory search parser --db "$T/g.db" --knowledge "$T/k"'`
The `auth` recall includes m1 and the `parser` recall includes m2.

## TC-015 — Empty agent id is rejected at the share and check surfaces

- **Story**: US2 · **Traces to**: FR-006
- **Given** the share surface (and identically the overlap-check surface).
- **When** an agent id that is empty — or whitespace-only — is supplied.
- **Then** the command exits non-zero with a clear validation message naming
  the agent-id requirement; nothing is shared or recorded under the blank
  identity.

**Pass condition**: `bash -c 'T=$(mktemp -d) && mkdir -p "$T/k" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory share --agent "" --symbols auth --db "$T/g.db"; test $? -ne 0'`
Exits 0 exactly when the empty id was refused; the whitespace-only form
(`--agent "   "`) must refuse identically.

## TC-016 — Over-length agent id is rejected

- **Story**: US2 · **Traces to**: FR-006
- **Given** the share surface's length cap on agent ids.
- **When** an agent id far beyond any reasonable cap (300 characters) is
  supplied.
- **Then** the command exits non-zero with a clear validation message; no
  truncated or oversized identity is stored.

**Pass condition**: `bash -c 'T=$(mktemp -d) && mkdir -p "$T/k" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory share --agent $(python3 -c "print(\"a\"*300)") --symbols auth --db "$T/g.db"; test $? -ne 0'`
Exits 0 exactly when the over-length id was refused.

## TC-017 — Hostile characters in an agent id never pass through raw

- **Story**: US2 · **Traces to**: FR-006
- **Given** agent ids containing path separators, quotes, or newlines.
- **When** such an id is supplied at the share or check surface.
- **Then** the surface either rejects it with a clear validation message or
  accepts only a sanitized form; no raw hostile id is echoed in any warning,
  listing, or stored identity.

**Pass condition**: `bash -c 'T=$(mktemp -d) && mkdir -p "$T/k" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory share --agent "../evil\" --x" --symbols auth --db "$T/g.db"; test $? -ne 0 && /Users/tanle/Projects/cairn/.venv/bin/cairn memory check --agent agentB --symbols auth --db "$T/g.db" | grep "No overlap"'`
Exits 0 exactly when the hostile id — traversal separators, a double quote,
a space, and an embedded decoy flag — was refused with a clean non-zero
usage error and a follow-up check by a benign agent finds nothing recorded
under it; the id grammar rejects the newline form by the same rule.

## Coverage matrix

| Requirement | Test cases | Type (auto/manual) | Basis (survey) |
|-------------|------------|--------------------|----------------|
| FR-001 | TC-001, TC-002, TC-003, TC-004 | auto (TC-003 manual) | new surface — no share command exists today |
| FR-002 | TC-005, TC-006, TC-007, TC-008, TC-015, TC-016, TC-017 | auto (TC-008 manual) | new surface — no overlap query exists; FR-006 validation cases double-cover the check surface |
| FR-003 | TC-009 (standing guard), TC-001 (demonstrates) | auto | new recall integration |
| FR-004 | TC-010 (standing guard) | auto | existing behavior baseline |
| FR-005 | TC-011, TC-012, TC-013, TC-014 | auto | WAL + busy timeout already shipped (S1) — TC-011 is its regression guard; namespaces/sharing new |
| FR-006 | TC-015, TC-016, TC-017 | auto | new surface — no `--agent` option exists today |

Untestable FRs: none — every FR has at least one observable pass condition.
`⚠ MISSING` count: 0.
