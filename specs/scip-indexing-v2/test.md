# Test Cases: scip-indexing-v2

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-20
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.

Fixture contract (test assets this suite owns; all paths repo-root-relative,
each build finishes in seconds on a laptop):
- Fixture family root `tests/fixtures/scip-indexing/`, provisioned as git
  repositories by `tests/fixtures/scip-indexing/provision.sh` (idempotent:
  `git init` + initial commit per workspace).
- `covered/` — small sources where `in_index.py` is covered by the committed
  index and `outside_index.py` is covered by no index; the index includes
  calls into the standard library (out-of-workspace targets). `covered-off/`
  is the byte-identical twin workspace with no index configured.
- `opaque/` + `opaque-off/` — swift-like sources with a committed
  opaque-USR-shape index (index-side symbol strings are useless for matching;
  only positions are trustworthy).
- `multirepo/` — two repositories (`repo-a/`, `repo-b/`) under one workspace,
  one committed index with documents for files in both repos.
- `drifted/` + `drifted-off/` — sources with a committed index whose
  occurrence positions do not match the sources (position-join rate below the
  anomaly threshold).
- `heavy/` + `heavy-off/` — sources with a committed index contributing ~5×
  the tree-sitter call-edge volume.
- `autogen/` — sources with the index configured but absent; `bin/scip-python`
  is a stub indexer that writes the committed index to the configured path and
  appends one line to `bin/calls.log`; `bin-fail/scip-python` exits 1.
- `seven-langs/` — all seven registry languages configured, indexes absent,
  no indexer binaries on PATH.
- `update/` — sources with a committed index covering `tracked.py`.

DB probes use the spec's own data vocabulary (`edges.source`,
`edges.resolution`, `edges.kind`, `symbols`, `files.repo_id`, the skip
surface's four pre-existing reasons). All auto commands are single-line and
finish well under the 120 s audit cap.

## TC-001 — Covered files get exact edges from the configured index
- **Story**: US1 · **Traces to**: FR-001, FR-002, AC1
- **Given** the covered workspace with its index configured
- **When** a build runs
- **Then** the covered file's call/reference edges come from the index,
  marked exact and index-sourced, with no tree-sitter call/reference edges
  left behind for that file (replacement, not union)
- **Pass condition**: `sh tests/fixtures/scip-indexing/provision.sh && rm -f /tmp/scipqa-tc001.db && uv run --no-sync cairn build --workspace tests/fixtures/scip-indexing/covered --db /tmp/scipqa-tc001.db && [ "$(sqlite3 /tmp/scipqa-tc001.db "select count(*) from edges e join symbols s on e.source_id=s.id join files f on s.file_id=f.id where f.path like '%in_index.py' and e.kind in ('calls','references') and e.source='scip' and e.resolution='exact'")" -gt 0 ] && [ "$(sqlite3 /tmp/scipqa-tc001.db "select count(*) from edges e join symbols s on e.source_id=s.id join files f on s.file_id=f.id where f.path like '%in_index.py' and e.kind in ('calls','references') and coalesce(e.source,'') != 'scip'")" = "0" ]`

## TC-002 — Index on vs off: symbols and structure identical (standing guard)
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** the covered workspace and its no-index twin (identical sources)
- **When** both are built and compared
- **Then** the symbol population is identical and every symbol remains
  tree-sitter-sourced — the index adds no symbols and no structure edges
- **Pass condition**: `sh tests/fixtures/scip-indexing/provision.sh && rm -f /tmp/scipqa-tc002-on.db /tmp/scipqa-tc002-off.db && uv run --no-sync cairn build --workspace tests/fixtures/scip-indexing/covered --db /tmp/scipqa-tc002-on.db && uv run --no-sync cairn build --workspace tests/fixtures/scip-indexing/covered-off --db /tmp/scipqa-tc002-off.db && [ "$(sqlite3 /tmp/scipqa-tc002-on.db 'select count(*) from symbols')" = "$(sqlite3 /tmp/scipqa-tc002-off.db 'select count(*) from symbols')" ] && [ "$(sqlite3 /tmp/scipqa-tc002-on.db "select count(*) from edges where kind='contains'")" = "$(sqlite3 /tmp/scipqa-tc002-off.db "select count(*) from edges where kind='contains'")" ] && [ "$(sqlite3 /tmp/scipqa-tc002-on.db "select count(*) from symbols where coalesce(source,'') != 'tree_sitter'")" = "0" ]`

## TC-003 — Files outside every index keep their tree-sitter edges (boundary)
- **Story**: US1 · **Traces to**: FR-002, AC1
- **Given** the covered workspace, where one source file is covered by no
  index document
- **When** a build runs with the index configured
- **Then** the uncovered file keeps its tree-sitter call/reference edges
  unchanged — nothing index-sourced touches it
- **Pass condition**: `sh tests/fixtures/scip-indexing/provision.sh && rm -f /tmp/scipqa-tc003.db && uv run --no-sync cairn build --workspace tests/fixtures/scip-indexing/covered --db /tmp/scipqa-tc003.db && [ "$(sqlite3 /tmp/scipqa-tc003.db "select count(*) from edges e join symbols s on e.source_id=s.id join files f on s.file_id=f.id where f.path like '%outside_index.py' and e.source='scip'")" = "0" ] && [ "$(sqlite3 /tmp/scipqa-tc003.db "select count(*) from edges e join symbols s on e.source_id=s.id join files f on s.file_id=f.id where f.path like '%outside_index.py' and e.kind in ('calls','references')")" -gt 0 ]`

## TC-004 — Opaque-USR index joins by position into the single symbol population
- **Story**: US1 · **Traces to**: FR-003, AC2
- **Given** the opaque workspace, whose committed index carries unusable
  symbol strings (swift-like opaque USRs) but valid positions
- **When** a build runs
- **Then** index edges attach to the existing tree-sitter symbols — the
  symbol population is byte-identical to the no-index twin (no duplicate or
  disconnected symbols), and exact index-sourced edges exist (the historical
  ~0%-merge failure does not recur)
- **Pass condition**: `sh tests/fixtures/scip-indexing/provision.sh && rm -f /tmp/scipqa-tc004-on.db /tmp/scipqa-tc004-off.db && uv run --no-sync cairn build --workspace tests/fixtures/scip-indexing/opaque --db /tmp/scipqa-tc004-on.db && uv run --no-sync cairn build --workspace tests/fixtures/scip-indexing/opaque-off --db /tmp/scipqa-tc004-off.db && [ "$(sqlite3 /tmp/scipqa-tc004-on.db 'select count(*) from symbols')" = "$(sqlite3 /tmp/scipqa-tc004-off.db 'select count(*) from symbols')" ] && [ "$(sqlite3 /tmp/scipqa-tc004-on.db "select count(*) from edges where source='scip' and resolution='exact'")" -gt 0 ]`

## TC-005 — Out-of-workspace targets are tagged unresolved
- **Story**: US1 · **Traces to**: FR-003
- **Given** the covered workspace, whose index includes calls into the
  standard library / external packages (definitions outside the workspace)
- **When** a build runs
- **Then** those index-sourced edges carry the existing unresolved label and
  no workspace target — using the resolution vocabulary, never a false binding
- **Pass condition**: `sh tests/fixtures/scip-indexing/provision.sh && rm -f /tmp/scipqa-tc005.db && uv run --no-sync cairn build --workspace tests/fixtures/scip-indexing/covered --db /tmp/scipqa-tc005.db && [ "$(sqlite3 /tmp/scipqa-tc005.db "select count(*) from edges where source='scip' and resolution='unresolved' and target_id is null")" -gt 0 ]`

## TC-006 — Multi-repo workspace attributes each document to its own repo
- **Story**: US1 · **Traces to**: FR-004
- **Given** one workspace holding two repositories and a single committed
  index with documents for files in both
- **When** a build runs
- **Then** index-sourced data appears under both repositories' identities —
  never everything under one repo id
- **Pass condition**: `sh tests/fixtures/scip-indexing/provision.sh && rm -f /tmp/scipqa-tc006.db && uv run --no-sync cairn build --workspace tests/fixtures/scip-indexing/multirepo --db /tmp/scipqa-tc006.db && [ "$(sqlite3 /tmp/scipqa-tc006.db "select count(distinct f.repo_id) from edges e join symbols s on e.source_id=s.id join files f on s.file_id=f.id where e.source='scip'")" = "2" ]`

## TC-007 — Anomalous position-join retains tree-sitter edges, recorded observably
- **Story**: US1 · **Traces to**: FR-005
- **Given** the drifted workspace, whose committed index positions miss the
  sources (join rate below the anomaly threshold)
- **When** a build runs
- **Then** the build succeeds, the file keeps its tree-sitter edges (edge mix
  equals the no-index twin), zero index-sourced edges land, and the anomaly
  is recorded observably in the build output — never a silent degradation
- **Pass condition**: `sh tests/fixtures/scip-indexing/provision.sh && rm -f /tmp/scipqa-tc007-on.db /tmp/scipqa-tc007-off.db /tmp/scipqa-tc007.log && uv run --no-sync cairn build --workspace tests/fixtures/scip-indexing/drifted --db /tmp/scipqa-tc007-on.db > /tmp/scipqa-tc007.log 2>&1 && grep -qi anomal /tmp/scipqa-tc007.log && uv run --no-sync cairn build --workspace tests/fixtures/scip-indexing/drifted-off --db /tmp/scipqa-tc007-off.db && [ "$(sqlite3 /tmp/scipqa-tc007-on.db "select count(*) from edges where source='scip'")" = "0" ] && [ "$(sqlite3 /tmp/scipqa-tc007-on.db "select count(*) from edges where kind='calls'")" = "$(sqlite3 /tmp/scipqa-tc007-off.db "select count(*) from edges where kind='calls'")" ]`

## TC-008 — Missing protobuf runtime degrades to tree-sitter with the [scip] hint
- **Story**: US1 · **Traces to**: FR-006
- **Given** the covered workspace with its index configured, run in an
  environment where the protobuf runtime import fails (the runtime-absent
  arm of the fallback)
- **When** a build runs
- **Then** the build succeeds via tree-sitter (edges present, none
  index-sourced) and the output reports the `[scip]` install hint — no crash
- **Pass condition**: `sh tests/fixtures/scip-indexing/provision.sh && mkdir -p /tmp/scipqa-nopb/google/protobuf && printf 'raise ImportError("protobuf unavailable")\n' > /tmp/scipqa-nopb/google/protobuf/__init__.py && rm -f /tmp/scipqa-tc008.db /tmp/scipqa-tc008.log && PYTHONPATH=/tmp/scipqa-nopb uv run --no-sync cairn build --workspace tests/fixtures/scip-indexing/covered --db /tmp/scipqa-tc008.db > /tmp/scipqa-tc008.log 2>&1 && grep -F "[scip]" /tmp/scipqa-tc008.log && [ "$(sqlite3 /tmp/scipqa-tc008.db "select count(*) from edges where source='scip'")" = "0" ] && [ "$(sqlite3 /tmp/scipqa-tc008.db 'select count(*) from edges')" -gt 0 ]`

## TC-009 — Missing index is generated exactly once when the indexer is present
- **Story**: US3 · **Traces to**: FR-007, AC6
- **Given** the autogen workspace: index configured but absent, and the
  stub indexer for its language on PATH
- **When** a build runs
- **Then** the indexer runs exactly once, the index file appears at the
  configured path, and its edges import (exact index-sourced edges exist)
- **Pass condition**: `sh tests/fixtures/scip-indexing/provision.sh && rm -f tests/fixtures/scip-indexing/autogen/index.scip tests/fixtures/scip-indexing/autogen/bin/calls.log /tmp/scipqa-tc009.db && PATH="tests/fixtures/scip-indexing/autogen/bin:$PATH" uv run --no-sync cairn build --workspace tests/fixtures/scip-indexing/autogen --db /tmp/scipqa-tc009.db && test -f tests/fixtures/scip-indexing/autogen/index.scip && [ "$(wc -l < tests/fixtures/scip-indexing/autogen/bin/calls.log)" = "1" ] && [ "$(sqlite3 /tmp/scipqa-tc009.db "select count(*) from edges where source='scip' and resolution='exact'")" -gt 0 ]`

## TC-010 — An existing index is never rebuilt (standing guard)
- **Story**: US3 · **Traces to**: FR-007
- **Given** the autogen workspace with the index file already present and the
  stub indexer still on PATH (it would fire if asked)
- **When** a build runs
- **Then** the existing index file is untouched (not rewritten) and the
  indexer is not invoked at all
- **Pass condition**: `sh tests/fixtures/scip-indexing/provision.sh && cp tests/fixtures/scip-indexing/autogen/committed-index.scip tests/fixtures/scip-indexing/autogen/index.scip 2>/dev/null || true; rm -f tests/fixtures/scip-indexing/autogen/bin/calls.log && touch /tmp/scipqa-tc010-ref && rm -f /tmp/scipqa-tc010.db && PATH="tests/fixtures/scip-indexing/autogen/bin:$PATH" uv run --no-sync cairn build --workspace tests/fixtures/scip-indexing/autogen --db /tmp/scipqa-tc010.db && test ! -f tests/fixtures/scip-indexing/autogen/bin/calls.log && [ -z "$(find tests/fixtures/scip-indexing/autogen/index.scip -newer /tmp/scipqa-tc010-ref 2>/dev/null)" ]`

## TC-011 — Indexer binary missing: build succeeds with an observable fallback record
- **Story**: US3 · **Traces to**: FR-008, AC7
- **Given** the autogen workspace: index configured but absent, no indexer
  binary for the language anywhere on PATH
- **When** a build runs verbosely
- **Then** the build succeeds via tree-sitter, a fallback/skip record beyond
  the four pre-existing skip reasons is observable on the skip surface, and
  the tool's install hint surfaces under `-v`
- **Pass condition**: `[ -z "$(command -v scip-python)" ] && sh tests/fixtures/scip-indexing/provision.sh && rm -f tests/fixtures/scip-indexing/autogen/index.scip /tmp/scipqa-tc011.db /tmp/scipqa-tc011.log && uv run --no-sync cairn build -v --workspace tests/fixtures/scip-indexing/autogen --db /tmp/scipqa-tc011.db > /tmp/scipqa-tc011.log 2>&1 && grep -qi install /tmp/scipqa-tc011.log && [ "$(sqlite3 /tmp/scipqa-tc011.db 'select count(*) from edges')" -gt 0 ] && [ "$(sqlite3 /tmp/scipqa-tc011.db "select count(*) from skipped_files where reason not in ('default_skip','gitignored','config_exclude','size_cap')")" -gt 0 ]`

## TC-012 — Indexer exits nonzero: build still succeeds with fallback record
- **Story**: US3 · **Traces to**: FR-008, AC7
- **Given** the autogen workspace: index configured but absent, and a failing
  stub indexer (exit 1) on PATH
- **When** a build runs verbosely
- **Then** the build succeeds via tree-sitter, the failure is recorded on the
  skip/fallback surface, and the install hint surfaces under `-v`
- **Pass condition**: `sh tests/fixtures/scip-indexing/provision.sh && rm -f tests/fixtures/scip-indexing/autogen/index.scip /tmp/scipqa-tc012.db /tmp/scipqa-tc012.log && PATH="tests/fixtures/scip-indexing/autogen/bin-fail:$PATH" uv run --no-sync cairn build -v --workspace tests/fixtures/scip-indexing/autogen --db /tmp/scipqa-tc012.db > /tmp/scipqa-tc012.log 2>&1 && grep -qi install /tmp/scipqa-tc012.log && [ "$(sqlite3 /tmp/scipqa-tc012.db 'select count(*) from edges')" -gt 0 ] && [ "$(sqlite3 /tmp/scipqa-tc012.db "select count(*) from skipped_files where reason not in ('default_skip','gitignored','config_exclude','size_cap')")" -gt 0 ]`

## TC-013 — Indexer times out: build succeeds within the bounded timeout's aftermath
- **Story**: US3 · **Traces to**: FR-008, AC7
- **Given** the autogen workspace with a stub indexer that sleeps past the
  bounded generation timeout and then never produces an index
- **When** a build runs verbosely
- **Then** the build terminates (the timeout bounds the wait), succeeds via
  tree-sitter, records the timeout on the skip/fallback surface, and shows
  the install hint under `-v` — no hang, no crash
- **Pass condition**: human observation — run `PATH=<stub-timeout-dir>:$PATH uv run --no-sync cairn build -v --workspace tests/fixtures/scip-indexing/autogen --db /tmp/scipqa-tc013.db` with a stub that sleeps past the bounded timeout recorded in tech-spec; observe the build return within roughly that timeout plus normal build time, exit 0, with the fallback record and install hint in the output (the timeout arm is deliberately not automated: the bounded timeout value is a tech-spec record, and a sleeping stub sized to a real timeout risks the audit's 120 s per-TC cap)

## TC-014 — Registry ships all seven languages, each degrading independently
- **Story**: US3 · **Traces to**: FR-009
- **Given** the seven-langs workspace: indexes configured but absent for
  swift, java, kotlin, typescript, python, go, and rust, and no indexer
  binaries on PATH
- **When** a build runs verbosely
- **Then** the build succeeds (one language's missing tool never aborts the
  others) and the verbose output names each of the seven languages in its
  per-language fallback reporting
- **Pass condition**: `sh tests/fixtures/scip-indexing/provision.sh && rm -f /tmp/scipqa-tc014.db /tmp/scipqa-tc014.log && uv run --no-sync cairn build -v --workspace tests/fixtures/scip-indexing/seven-langs --db /tmp/scipqa-tc014.db > /tmp/scipqa-tc014.log 2>&1 && for l in swift java kotlin typescript python go rust; do grep -qiw "$l" /tmp/scipqa-tc014.log || exit 1; done`

## TC-015 — Update flips a changed covered file to tree-sitter and invokes no indexer
- **Story**: US4 · **Traces to**: FR-010, AC8
- **Given** the update workspace built with its index (a covered file has
  index-sourced edges), the index file then removed, the stub indexer on
  PATH, and the covered file edited
- **When** an incremental update runs
- **Then** the changed file's edges are re-resolved by tree-sitter (nothing
  index-sourced remains for it; tree-sitter edges observable) and the stub
  indexer was never invoked
- **Pass condition**: `sh tests/fixtures/scip-indexing/provision.sh && rm -f /tmp/scipqa-tc015.db && uv run --no-sync cairn build --workspace tests/fixtures/scip-indexing/update --db /tmp/scipqa-tc015.db && rm -f tests/fixtures/scip-indexing/update/index.scip tests/fixtures/scip-indexing/update/bin/calls.log && printf '\ndef added_for_update_probe():\n    return 7\n' >> tests/fixtures/scip-indexing/update/tracked.py && PATH="tests/fixtures/scip-indexing/update/bin:$PATH" uv run --no-sync cairn update --workspace tests/fixtures/scip-indexing/update --db /tmp/scipqa-tc015.db && test ! -f tests/fixtures/scip-indexing/update/bin/calls.log && [ "$(sqlite3 /tmp/scipqa-tc015.db "select count(*) from edges e join symbols s on e.source_id=s.id join files f on s.file_id=f.id where f.path like '%tracked.py' and e.source='scip'")" = "0" ] && [ "$(sqlite3 /tmp/scipqa-tc015.db "select count(*) from edges e join symbols s on e.source_id=s.id join files f on s.file_id=f.id where f.path like '%tracked.py' and e.kind in ('calls','references')")" -gt 0 ]`

## TC-016 — The next full build restores index edges for the updated file
- **Story**: US4 · **Traces to**: FR-010, AC8
- **Given** the update workspace in TC-015's post-update state (file now
  tree-sitter-sourced), with the stub indexer on PATH so the missing index
  can be regenerated
- **When** a full build runs
- **Then** the file's index-sourced exact edges are back
- **Pass condition**: `PATH="tests/fixtures/scip-indexing/update/bin:$PATH" uv run --no-sync cairn build --workspace tests/fixtures/scip-indexing/update --db /tmp/scipqa-tc015.db && [ "$(sqlite3 /tmp/scipqa-tc015.db "select count(*) from edges e join symbols s on e.source_id=s.id join files f on s.file_id=f.id where f.path like '%tracked.py' and e.source='scip' and e.resolution='exact'")" -gt 0 ]`

## TC-017 — Redesigned closure is byte-identical on non-SCIP graphs (shared machinery gate)
- **Story**: US2 · **Traces to**: FR-011, AC5
- **Given** any build without a SCIP index (the pre-redesign graph shape)
- **When** the closure machinery builds and maintains
- **Then** multi-hop results stay byte-identical to the current behavior —
  the existing closure parity/collision regression gates keep passing
  (re-run this session: 2 passed, and 2 passed with 60 deselected)
- **Pass condition**: `uv run --no-sync pytest tests/test_dataflow_transitive_closure.py -q && uv run --no-sync pytest tests/test_incremental_derived.py -k "matches_full_rebuild or drops_rows" -q`

## TC-018 — Closure completes on a ~5×-edge index within the bounded audit window
- **Story**: US2 · **Traces to**: FR-011, AC4
- **Given** the heavy workspace, whose committed index contributes ~5× the
  tree-sitter call-edge volume of its no-index twin
- **When** a full build (including the closure) runs
- **Then** the build completes in seconds at fixture scale — the historical
  stall must not recur even at multiplied edge volume — with a non-empty
  closure and the edge uplift present. Standing at-scale verify (beyond the
  audit's 120 s cap, run as measurement-harness work): the scaling-suite
  closure op against the recorded wall-time/memory budget
- **Pass condition**: `sh tests/fixtures/scip-indexing/provision.sh && rm -f /tmp/scipqa-tc018-on.db /tmp/scipqa-tc018-off.db && uv run --no-sync cairn build --workspace tests/fixtures/scip-indexing/heavy --db /tmp/scipqa-tc018-on.db && uv run --no-sync cairn build --workspace tests/fixtures/scip-indexing/heavy-off --db /tmp/scipqa-tc018-off.db && [ "$(sqlite3 /tmp/scipqa-tc018-on.db 'select count(*) from transitive_edges')" -gt 0 ] && [ "$(sqlite3 /tmp/scipqa-tc018-on.db "select count(*) from edges where kind='calls'")" -gt "$(sqlite3 /tmp/scipqa-tc018-off.db "select count(*) from edges where kind='calls'")" ]`

## TC-019 — Closure budget is enforced as a scaling-suite gate
- **Story**: US2 · **Traces to**: FR-012, AC4
- **Given** the scaling suite with the closure budget number and its
  measurement recorded as a tech-spec decision
- **When** a scaling-suite run's closure op exceeds the recorded budget
- **Then** the gate fails the run (nonzero exit, reported failure) — the
  budget is enforced, not advisory
- **Pass condition**: human observation — standing verify `uv run --no-sync cairn bench --suite perf` (beyond the audit's 120 s per-TC cap at real scale): confirm the closure op carries the recorded budget and that an over-budget closure fails the suite rather than printing an advisory note; the budget number and its decision record are tech-spec territory and audited there

## TC-020 — Stats surface shows index provenance and measurable exact-share uplift
- **Story**: US1 · **Traces to**: FR-013, AC3
- **Given** index-on and index-off builds of the identical covered tree
- **When** stats are reported for both
- **Then** the resolution-share surface mentions the index provenance
  (per-language exact share measurable on/off), and the covered language's
  exact share of call edges is strictly higher with the index on
- **Pass condition**: `sh tests/fixtures/scip-indexing/provision.sh && rm -f /tmp/scipqa-tc020-on.db /tmp/scipqa-tc020-off.db && uv run --no-sync cairn build --workspace tests/fixtures/scip-indexing/covered --db /tmp/scipqa-tc020-on.db && uv run --no-sync cairn build --workspace tests/fixtures/scip-indexing/covered-off --db /tmp/scipqa-tc020-off.db && uv run --no-sync cairn stats --db /tmp/scipqa-tc020-on.db | grep -i scip && on=$(sqlite3 /tmp/scipqa-tc020-on.db "select cast(sum(resolution='exact') as real)/count(*) from edges where kind='calls'") && off=$(sqlite3 /tmp/scipqa-tc020-off.db "select cast(sum(resolution='exact') as real)/count(*) from edges where kind='calls'") && [ "$(awk "BEGIN{print ($on > $off)}")" = "1" ]`

## TC-021 — Zero false-exact conversions against the retrieval ground truth
- **Story**: US1 · **Traces to**: FR-014, AC3
- **Given** index-on and index-off builds of the same tree, and the
  retrieval ground-truth corpus for it
- **When** the retrieval evaluation runs against both builds
- **Then** precision on the ground truth with the index on is at least the
  index-off precision (zero false-exact conversions), and wherever the
  index's binding and the resolver's would disagree, the edge follows the
  index and the disagreement is visible as a counted record — never silently
  resolved either way
- **Pass condition**: human observation — run `uv run --no-sync cairn eval --db <index-on-db> --queries <ground-truth> --json` and the same against the index-off DB (the eval JSON's metric shape is not fixed by the spec; a human compares precision, and the report/json shape is pinned at implementation time so this can be automated then — the zero-false-exact contract itself is the observation)

## TC-022 — Manual import lands under the same overlay rules
- **Story**: US5 · **Traces to**: FR-015, AC9
- **Given** an already-built database for the covered twin workspace
  (tree-sitter only)
- **When** the committed index is imported manually
- **Then** edges land exactly as a configured build would (covered file gets
  exact index-sourced edges; AC1/AC2 semantics hold) and the symbol
  population is unchanged by the import
- **Pass condition**: `sh tests/fixtures/scip-indexing/provision.sh && rm -f /tmp/scipqa-tc022.db && uv run --no-sync cairn build --workspace tests/fixtures/scip-indexing/covered-off --db /tmp/scipqa-tc022.db && before=$(sqlite3 /tmp/scipqa-tc022.db 'select count(*) from symbols') && uv run --no-sync cairn import-scip tests/fixtures/scip-indexing/covered/index.scip --workspace tests/fixtures/scip-indexing/covered-off --db /tmp/scipqa-tc022.db && [ "$(sqlite3 /tmp/scipqa-tc022.db 'select count(*) from symbols')" = "$before" ] && [ "$(sqlite3 /tmp/scipqa-tc022.db "select count(*) from edges e join symbols s on e.source_id=s.id join files f on s.file_id=f.id where f.path like '%in_index.py' and e.source='scip' and e.resolution='exact'")" -gt 0 ]`

## TC-023 — Config echoes the scip key
- **Story**: US5 · **Traces to**: FR-015
- **Given** a workspace with the index configured
- **When** the resolved configuration is printed as JSON
- **Then** the scip key is echoed in the resolved output
- **Pass condition**: `(cd tests/fixtures/scip-indexing/covered && uv run --no-sync cairn config --json) | grep -i scip`

## TC-024 — End-to-end SCIP toolchain guide ships
- **Story**: US3 · **Traces to**: FR-016
- **Given** the shipped documentation
- **When** the SCIP guide is read
- **Then** it covers per-indexer install including the npm-not-pip
  scip-python case and the macOS-only scip-swift case, plus configuration,
  generation semantics, fallback behavior, and the overlay model
- **Pass condition**: `test -f docs/scip.md && grep -qi scip-python docs/scip.md && grep -qi npm docs/scip.md && grep -qi scip-swift docs/scip.md && grep -Eqi "macos|mac os" docs/scip.md && grep -qi fallback docs/scip.md && grep -qi overlay docs/scip.md && grep -qi config docs/scip.md`

## TC-025 — The [scip] extra installs the protobuf runtime
- **Story**: US3 · **Traces to**: FR-017
- **Given** the packaged extras
- **When** the environment is materialized with the scip extra in an
  isolated project environment
- **Then** the extra resolves (undefined extras fail) and the protobuf
  runtime it carries imports cleanly (the decision record for the extra's
  contents is a tech-spec decision audited there)
- **Pass condition**: `UV_PROJECT_ENVIRONMENT=/tmp/scipqa-tc025-venv uv run --extra scip python -c "import google.protobuf"`

## Coverage matrix
<!-- Every FR appears; `check.py` fails an FR with no TC. -->
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001      | TC-001, TC-002 | auto |
| FR-002      | TC-001, TC-003 | auto |
| FR-003      | TC-004, TC-005 | auto |
| FR-004      | TC-006 | auto |
| FR-005      | TC-007 | auto |
| FR-006      | TC-008 | auto |
| FR-007      | TC-009, TC-010 | auto |
| FR-008      | TC-011, TC-012, TC-013 | auto + manual |
| FR-009      | TC-014 | auto |
| FR-010      | TC-015, TC-016 | auto |
| FR-011      | TC-017, TC-018 | auto |
| FR-012      | TC-019 | manual |
| FR-013      | TC-020 | auto |
| FR-014      | TC-021 | manual |
| FR-015      | TC-022, TC-023 | auto |
| FR-016      | TC-024 | auto |
| FR-017      | TC-025 | auto |

AC coverage: AC1 → TC-001/002/003 · AC2 → TC-004 · AC3 → TC-020/021 ·
AC4 → TC-018/019 · AC5 → TC-017 · AC6 → TC-009 · AC7 → TC-011/012/013 ·
AC8 → TC-015/016 · AC9 → TC-022.
