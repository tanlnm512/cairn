# Test Cases: temporal-memory

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-17
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.

## TC-001 — As-of recall returns only memories valid at that date
- **Story**: US1 · **Traces to**: FR-002, AC1
- **Given** a memory store holding a memory recorded today
- **When** recall runs with an as-of date after the memory's creation, and
  again with an as-of date before the store existed
- **Then** the memory returns for the later date and is absent for the
  earlier date — only memories valid at the queried date surface
- **Pass condition**: `bash -c 'T=$(mktemp -d) && /Users/tanle/Projects/cairn/.venv/bin/cairn memory record decision "AsOfQA probe one" --body "qa fixture memory" --db "$T/g.db" --knowledge "$T/k" >/dev/null 2>&1 && /Users/tanle/Projects/cairn/.venv/bin/cairn memory search "AsOfQA probe one" --as-of 2099-12-31 --db "$T/g.db" --knowledge "$T/k" 2>/dev/null | grep -qE "^[[:space:]]*\[.*\] AsOfQA probe one" && ! /Users/tanle/Projects/cairn/.venv/bin/cairn memory search "AsOfQA probe one" --as-of 2000-01-01 --db "$T/g.db" --knowledge "$T/k" 2>/dev/null | grep -qE "^[[:space:]]*\[.*\] AsOfQA probe one"'` exits 0

## TC-002 — Default recall returns only currently-valid memories (standing guard)
- **Story**: US1 · **Traces to**: FR-002, AC1
- **Given** a memory recorded today with no end date on its validity
- **When** recall runs with no date filter, and with an as-of date equal to
  the creation date
- **Then** the memory returns in both cases — default recall serves
  currently-valid memories, and the validity start date is inclusive; a
  memory whose validity has ended must never reappear in default results
  (guard checked end-to-end by TC-005)
- **Pass condition**: `bash -c 'T=$(mktemp -d) && /Users/tanle/Projects/cairn/.venv/bin/cairn memory record decision "DefaultQA probe two" --body "qa fixture memory" --db "$T/g.db" --knowledge "$T/k" >/dev/null 2>&1 && /Users/tanle/Projects/cairn/.venv/bin/cairn memory search "DefaultQA probe two" --db "$T/g.db" --knowledge "$T/k" 2>/dev/null | grep -qE "^[[:space:]]*\[.*\] DefaultQA probe two" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory search "DefaultQA probe two" --as-of $(date +%F) --db "$T/g.db" --knowledge "$T/k" 2>/dev/null | grep -qE "^[[:space:]]*\[.*\] DefaultQA probe two"'` exits 0

## TC-003 — As-of on an empty store is a clean empty result
- **Story**: US1 · **Traces to**: FR-002, FR-001
- **Given** a brand-new store with no memories
- **When** recall runs with an as-of date
- **Then** the result is empty and the command completes without error —
  the validity fields exist on fresh stores too (additive schema)
- **Pass condition**: `bash -c 'T=$(mktemp -d) && mkdir -p "$T/k" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory search "nothing here" --as-of 2000-01-01 --db "$T/g.db" --knowledge "$T/k" >/dev/null 2>&1'` exits 0

## TC-004 — Agent recall surface honors as-of identically
- **Story**: US1 · **Traces to**: FR-002, AC1
- **Given** the same store fixture as TC-001, queried through the agent
  (MCP) recall tool instead of the command line
- **When** the agent requests recall with an as-of date
- **Then** the returned set matches the command-line result for the same
  date — the agent surface filters identically to the human surface
- **Pass condition**: observation — with the TC-001 fixture store connected via MCP, recall_memory called with as-of 2099-12-31 lists the fixture memory and with as-of 2000-01-01 returns none; results identical to TC-001's

## TC-005 — Rename auto-invalidates instead of deleting, with successor link
- **Story**: US2 · **Traces to**: FR-003, AC1
- **Given** a scratch workspace copy holding a memory that references one of
  its code symbols
- **When** the symbol is renamed and the build's stale-reference check runs
- **Then** the memory is not deleted; its validity ends at the build time; a
  link to the renamed successor is recorded; default recall no longer lists
  it, while as-of recall before the build still returns it
- **Pass condition**: observation — after the rename and validation run, the memory list still contains the record marked expired at the build time, a successor reference is shown, an unfiltered search for it returns nothing, and an as-of search dated before the build returns it

## TC-006 — Ambiguous rename invalidates without inventing a successor
- **Story**: US2 · **Traces to**: FR-003
- **Given** a scratch workspace copy where a memory's referenced symbol was
  removed with no unique renamed successor identifiable
- **When** the build's stale-reference check runs
- **Then** the memory's validity still ends at the build time, and no
  successor link is recorded — links form only where one unique successor
  exists
- **Pass condition**: observation — the record is marked expired at the build time and carries no successor reference

## TC-007 — Timeline shows a symbol's memory history
- **Story**: US1 · **Traces to**: FR-004
- **Given** a store holding a memory that references a known symbol
- **When** the timeline for that symbol is requested
- **Then** the listing shows the memory together with its validity dates —
  the temporal history is readable in one view
- **Pass condition**: `bash -c 'T=$(mktemp -d) && /Users/tanle/Projects/cairn/.venv/bin/cairn memory record decision "RetryProbe policy notes" --body "qa fixture for the timeline view" --db "$T/g.db" --knowledge "$T/k" >/dev/null 2>&1 && out=$(/Users/tanle/Projects/cairn/.venv/bin/cairn memory timeline RetryProbe --db "$T/g.db" --knowledge "$T/k" 2>/dev/null) && echo "$out" | grep -q "RetryProbe policy notes" && echo "$out" | grep -qE "[0-9]{4}-[0-9]{2}-[0-9]{2}"'` exits 0

## TC-008 — Timeline for a symbol with no memories is clean
- **Story**: US1 · **Traces to**: FR-004
- **Given** a brand-new store with no memories
- **When** the timeline for any symbol is requested
- **Then** the result is empty and the command completes without error
- **Pass condition**: `bash -c 'T=$(mktemp -d) && mkdir -p "$T/k" && /Users/tanle/Projects/cairn/.venv/bin/cairn memory timeline NoSuchSymbolQA --db "$T/g.db" --knowledge "$T/k" >/dev/null 2>&1'` exits 0

## TC-009 — Existing memories survive the validity-field migration
- **Story**: US1 · **Traces to**: FR-001
- **Given** a store created before the validity fields existed, holding
  memories of known creation dates
- **When** the store is opened after the update
- **Then** it opens cleanly; every pre-existing memory carries a validity
  start equal to its creation time and an open end; all remain returnable
  by default recall; nothing was deleted or re-dated
- **Pass condition**: observation — open a copy of a prior-version fixture store after upgrading: all pre-existing memories list with a validity start matching their recorded creation date, an open validity end, and unchanged content; default recall returns them all

## TC-010 — Temporal fields do not regress recall latency (standing guard)
- **Story**: US1 · **Traces to**: FR-005
- **Given** the committed recall performance baseline
- **When** the performance suite runs against a bounded synthetic corpus and
  compares itself to that baseline
- **Then** no recall-latency regression beyond the threshold is flagged;
  at scale, the full perf suite against the production workspace is the
  standing verify, recorded before/after in the tech spec
- **Pass condition**: `/Users/tanle/Projects/cairn/.venv/bin/cairn bench --suite perf --baseline DS-v1 --n-files 20 --complexity low` exits 0
  The committed-baseline comparison is advisory across machine profiles: a
  machine-profile mismatch is warned, and a threshold breach is reported,
  not failed — only a command error fails this case.

## Coverage matrix
<!-- Every FR appears; `check.py` fails an FR with no TC. -->
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001      | TC-003, TC-009 | manual (TC-009), auto (TC-003) |
| FR-002      | TC-001, TC-002, TC-003, TC-004 | auto (TC-001/002/003), manual (TC-004) |
| FR-003      | TC-005, TC-006 | manual |
| FR-004      | TC-007, TC-008 | auto |
| FR-005      | TC-010 | auto |
