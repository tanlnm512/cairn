# Test Cases: rationale-nodes

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-05
Business acceptance suite — black-box cases derived from spec.md only
(tech-spec.md and plan.md never read by the qa node). Each case has an
observable pass condition; no implementation details. Commands below were
probe-checked against the shipped product surface for shape; the
rationale-facing commands (`cairn rationale`, the explore "Rationale"
section) fail until the feature lands — that is the suite doing its job.

Conventions: bounded fixture workspaces (every auto command finishes in
seconds, well under the 120 s per-TC cap), self-contained one-line commands.
No property library ships in this repo, so per the kit rule fixed boundary
examples stand — no dependency was added for testability. The spec covers no
web UI, so all cases are plain Given/When/Then.

## TC-001 — Build captures one record per marker comment of every kind

- **Priority**: P1 · **Type**: auto
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** a workspace with one file containing three comments, one
  beginning `NOTE:`, one `WHY:`, one `HACK:` (in the language's comment
  syntax)
- **When** the normal build runs, then the rationale listing for that file
  is requested
- **Then** exactly three rationale records exist, one per marker comment,
  each carrying its kind tag and its text
- **Pass condition**: `cd "$(mktemp -d)" && printf '# NOTE: retries are finite\n# WHY: ordering matters\n# HACK: shim until v2\nx = 1\n' > s.py && cairn build --workspace . --db g.db >/dev/null && test "$(cairn rationale --file s.py | grep -cE 'NOTE:|WHY:|HACK:')" = 3`

## TC-002 — Attribution: innermost enclosing symbol wins; top level is file-level

- **Priority**: P1 · **Type**: auto
- **Story**: US1 · **Traces to**: FR-002, AC2
- **Given** a workspace with one file holding a marker comment inside a
  function's body and a second marker comment at the top level of the file
- **When** the build runs, then the rationale for that function is listed,
  then the rationale for the whole file
- **Then** the in-body comment is attributed to the function; the top-level
  comment is attributed to the file (no symbol), yet both appear under the
  file listing
- **Pass condition**: `cd "$(mktemp -d)" && printf 'def loader():\n    # NOTE: keep single flight\n    return 1\n\n# NOTE: module-wide default\nLIMIT = 3\n' > s.py && cairn build --workspace . --db g.db >/dev/null && test "$(cairn rationale --symbol loader | grep -c 'single flight')" = 1 && test "$(cairn rationale --symbol loader | grep -c 'module-wide')" = 0 && test "$(cairn rationale --file s.py | grep -c 'NOTE:')" = 2`

## TC-003 — Marker directly above a definition stays file-level

- **Priority**: P1 · **Type**: auto
- **Story**: US1 · **Traces to**: FR-002
- **Given** a workspace with a marker comment immediately preceding a
  function definition (boundary pin for the span-containment rule: a comment
  outside a definition's span is not pulled into that symbol)
- **When** the build runs and the function's rationale is listed
- **Then** the comment is attributed to the file only — it never appears
  under the function
- **Pass condition**: `cd "$(mktemp -d)" && printf '# WHY: kept public for plugins\ndef handler():\n    return 0\n' > s.py && cairn build --workspace . --db g.db >/dev/null && test "$(cairn rationale --symbol handler | grep -c 'plugins')" = 0 && test "$(cairn rationale --file s.py | grep -c 'plugins')" = 1`

## TC-004 — Multi-line marker comment yields exactly one record

- **Priority**: P1 · **Type**: auto
- **Story**: US1 · **Traces to**: FR-001
- **Given** a workspace with a marker comment whose continuation runs onto
  the next comment line
- **When** the build runs and the record is listed
- **Then** exactly one record exists, and its text carries the continuation
  line rather than spawning a second record
- **Pass condition**: `cd "$(mktemp -d)" && printf 'def f():\n    # NOTE: the retry loop is intentional\n    # because upstream de-dupes bursts.\n    return 1\n' > s.py && cairn build --workspace . --db g.db >/dev/null && test "$(cairn rationale --symbol f | grep -c 'retry loop')" = 1 && test "$(cairn rationale --symbol f | grep -c 'de-dupes')" = 1`

## TC-005 — Docstrings never produce rationale records

- **Priority**: P1 · **Type**: auto
- **Story**: US1 · **Traces to**: FR-005
- **Given** a workspace whose file has a docstring on its callable that
  itself contains marker-like lines (`NOTE:`, `WHY:` inside the docstring)
- **When** the build runs and the file's rationale is listed
- **Then** no rationale records exist for that file
- **Pass condition**: `cd "$(mktemp -d)" && printf 'def f():\n    """NOTE: this docstring must not become a record.\n\n    WHY: docstrings live on the symbol already.\n    """\n    return 1\n' > s.py && cairn build --workspace . --db g.db >/dev/null && test -z "$(cairn rationale --file s.py)"`

## TC-006 — Missing comment mapping never fails the build

- **Priority**: P1 · **Type**: auto
- **Story**: US1 · **Traces to**: FR-006
- **Given** a workspace holding one marker-comment-bearing file for every
  language the build supports, whatever each language's comment mapping is
- **When** the build runs over all of them
- **Then** the build exits successfully; each file either has its markers
  extracted or maps no comment style and simply contributes zero records —
  a missing mapping never fails the build nor aborts the other files
- **Pass condition**: `python3 -m unittest tests.test_rationale.UnmappedCommentLanguageTests.test_build_never_fails_on_missing_comment_map`

## TC-007 — TODO/FIXME markers are never captured (standing guard)

- **Priority**: P1 · **Type**: auto
- **Story**: US1 · **Traces to**: FR-008
- **Given** a workspace whose file contains `TODO:` and `FIXME:` comments
  alongside one genuine `NOTE:` comment (standing regression guard: fails if
  TODO/FIXME capture ever creeps into the default path)
- **When** the build runs and the file's rationale is listed
- **Then** no record mentions TODO or FIXME; the genuine marker is captured
- **Pass condition**: `cd "$(mktemp -d)" && printf '# TODO: split module\n# FIXME: race on shutdown\n# NOTE: real signal\nx = 1\n' > s.py && cairn build --workspace . --db g.db >/dev/null && test -z "$(cairn rationale --file s.py | grep -E 'TODO|FIXME')" && test "$(cairn rationale --file s.py | grep -c 'NOTE:')" = 1`

## TC-008 — Query: filters scope, records print line-ordered with kind tags

- **Priority**: P2 · **Type**: auto
- **Story**: US2 · **Traces to**: FR-003, AC1
- **Given** a built workspace whose file has records across several lines
  belonging to different callables — one callable with two records, one with
  a single record, and a name matching nothing
- **When** the rationale listing is requested for the multi-record callable,
  for the whole file, and for the no-match name
- **Then** the callable listing shows only that callable's records; the file
  listing shows all of them ordered by line (earliest line first); every
  record carries its kind tag; the no-match query returns empty output
  without failing
- **Pass condition**: `cd "$(mktemp -d)" && printf 'def a():\n    # WHY: first\n    return 1\n\ndef b():\n    # NOTE: second\n    # HACK: third\n    return 2\n' > s.py && cairn build --workspace . --db g.db >/dev/null && test "$(cairn rationale --symbol b | grep -cE 'NOTE:|HACK:')" = 2 && test "$(cairn rationale --symbol b | grep -c 'first')" = 0 && test "$(cairn rationale --file s.py | head -1 | grep -c 'first')" = 1 && test -z "$(cairn rationale --symbol no_such_callable)"`

## TC-009 — Explore shows the Rationale section only when records exist

- **Priority**: P2 · **Type**: auto
- **Story**: US3 · **Traces to**: FR-004, AC1
- **Given** a built workspace with one callable carrying a marker comment
  and a sibling callable carrying none
- **When** the explore tool is invoked for each callable (the command below
  drives the local MCP server directly)
- **Then** the explore output for the first contains a "Rationale" section;
  the explore output for the second contains none
- **Pass condition**: `cd "$(mktemp -d)" && printf 'def gated():\n    # NOTE: gate stays closed by default\n    return 1\n\ndef plain():\n    return 2\n' > s.py && cairn build --workspace . --db g.db >/dev/null && test "$( { printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}'; sleep 1; printf '%s\n' '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"explore","arguments":{"query":"gated"}}}'; sleep 3; } | cairn serve --db g.db 2>/dev/null | grep '"id":2' | grep -c 'Rationale')" -ge 1 && test "$( { printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}'; sleep 1; printf '%s\n' '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"explore","arguments":{"query":"plain"}}}'; sleep 3; } | cairn serve --db g.db 2>/dev/null | grep '"id":2' | grep -c 'Rationale')" = 0`

## TC-010 — MCP tool manifest unchanged (standing guard)

- **Priority**: P2 · **Type**: auto
- **Story**: US3 · **Traces to**: FR-004
- **Given** the suite's snapshot of the MCP tool manifest taken before this
  feature (names and count) — standing regression guard: fails if the
  feature ever adds or renames an MCP tool, since rationale must surface
  inside existing tools only
- **When** the live tool list is fetched
- **Then** the manifest is identical to the snapshot — same tool count, no
  rationale-named tool
- **Pass condition**: `python3 -m unittest tests.test_rationale.McpSurfaceTests.test_tool_manifest_unchanged`

## TC-011 — Incremental update re-derives rationale; result equals a fresh build

- **Priority**: P2 · **Type**: auto
- **Story**: US4 · **Traces to**: FR-007, AC1
- **Given** a built git workspace whose file carries two marker comments
- **When** one comment is removed and another added, and the incremental
  update runs
- **Then** the file's rationale records are exactly what its current
  comments imply (removed comment gone, added comment present, attribution
  still correct), and the listing is byte-identical to a fresh full build of
  the same final content
- **Pass condition**: `cd "$(mktemp -d)" && git init -q . && printf 'def f():\n    # NOTE: keep\n    # WHY: drop me later\n    return 1\n' > s.py && git add -A && git -c user.email=t@t -c user.name=t commit -qm init && cairn build --workspace . --db g.db >/dev/null && printf 'def f():\n    # NOTE: keep\n    # HACK: added later\n    return 1\n' > s.py && cairn update --workspace . --db g.db >/dev/null && test "$(cairn rationale --file s.py | grep -c 'drop me')" = 0 && test "$(cairn rationale --file s.py | grep -cE 'NOTE:|HACK:')" = 2 && N=$(mktemp -d) && printf 'def f():\n    # NOTE: keep\n    # HACK: added later\n    return 1\n' > "$N/s.py" && (cd "$N" && cairn build --workspace . --db g.db >/dev/null) && test "$(cairn rationale --file s.py)" = "$(cd "$N" && cairn rationale --file s.py)"`

## TC-012 — Rationale extraction adds negligible build overhead

- **Priority**: P2 · **Type**: auto
- **Story**: US1 · **Traces to**: NFR-003
- **Given** two same-size bounded corpora — one of plain files, one the same
  files each carrying marker comments throughout ("negligible" is pinned
  observable as: on a corpus sized so indexing dominates process startup,
  the marker-bearing build costs no more than 1.5x the plain variant's wall
  time — a scan riding the existing walk; a second full pass would breach it)
- **When** each corpus is built in turn
- **Then** the marker-bearing build takes at most 1.5x the plain build's
  wall time; the run is bounded well under the two-minute cap. Standing
  at-scale verify (manual, outside the audit): a full build of this
  repository with the feature enabled stays within its pre-feature wall
  time by the same margin
- **Pass condition**: `python3 -m unittest tests.test_rationale.BuildOverheadTests.test_marker_scan_is_not_a_second_pass`

## Coverage matrix

<!-- Every FR and applicable NFR appears; `check.py` fails a requirement with no TC. -->

| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001      | TC-001, TC-004 | auto           |
| FR-002      | TC-002, TC-003 | auto           |
| FR-003      | TC-008     | auto               |
| FR-004      | TC-009, TC-010 | auto           |
| FR-005      | TC-005     | auto               |
| FR-006      | TC-006     | auto               |
| FR-007      | TC-011     | auto               |
| FR-008      | TC-007     | auto               |
| NFR-003     | TC-012     | auto               |
| NFR-001     | — N/A per spec (local index data only, no new surface) | n/a |
| NFR-002     | — N/A per spec (repo content, nothing leaves the store) | n/a |
| NFR-004     | — N/A per spec (additive, rebuildable, no new failure contract) | n/a |
| NFR-005     | — N/A per spec (no new signal required) | n/a |
| NFR-006     | — N/A per spec (no UI surface touched) | n/a |

Story acceptance-criterion traces: US1 AC1 → TC-001, TC-004 · US1 AC2 →
TC-002, TC-003 · US2 AC1 → TC-008 · US3 AC1 → TC-009 · US4 AC1 → TC-011.

No `⚠ MISSING` rows: every FR and the one applicable NFR have at least one
observable pass condition. Untestable requirements: none.
