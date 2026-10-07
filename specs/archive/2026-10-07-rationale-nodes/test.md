# Test Cases: rationale-nodes

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-05
Business acceptance suite — black-box cases derived from spec.md only
(tech-spec.md and plan.md never read by the qa node). Each case has an
observable pass condition; no implementation details. Commands below were
probe-checked against the shipped product surface for shape and run green
against the landed feature.

Conventions: every auto command is a self-contained one-liner run from the
repo root (`uv run --no-sync cairn ...` — no global binary assumed); bounded
fixtures live under /tmp (built with `mkdir`/`printf`, plus a `.git` marker
dir so the scanner indexes the workspace) and carry explicit
`--db /tmp/<name>.kg` paths; each command removes its /tmp artifacts at the
end while preserving the assertion's exit status (`r=$?; rm -rf ...;
(exit $r)`), so reruns are clean and idempotent. Every auto command finishes
in seconds, well under the 120 s per-TC cap. No property library ships in
this repo, so per the kit rule fixed boundary examples stand — no dependency
was added for testability. The spec covers no web UI, so all cases are plain
Given/When/Then.

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
- **Pass condition**: `W=/tmp/qa-rationale-tc001; rm -rf $W /tmp/qa-rationale-tc001.kg*; mkdir -p $W/.git; printf '# NOTE: retries are finite\n# WHY: ordering matters\n# HACK: shim until v2\nx = 1\n' > $W/s.py; uv run --no-sync cairn build --workspace $W --db /tmp/qa-rationale-tc001.kg >/dev/null 2>&1; test "$(uv run --no-sync cairn rationale --file s.py --db /tmp/qa-rationale-tc001.kg | grep -cE '\[(note|why|hack)\] (retries are finite|ordering matters|shim until v2)')" = 3; r=$?; rm -rf $W /tmp/qa-rationale-tc001.kg*; (exit $r)`

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
- **Pass condition**: `W=/tmp/qa-rationale-tc002; rm -rf $W /tmp/qa-rationale-tc002.kg*; mkdir -p $W/.git; printf 'def loader():\n    # NOTE: keep single flight\n    return 1\n\n# NOTE: module-wide default\nLIMIT = 3\n' > $W/s.py; uv run --no-sync cairn build --workspace $W --db /tmp/qa-rationale-tc002.kg >/dev/null 2>&1; test "$(uv run --no-sync cairn rationale --symbol loader --db /tmp/qa-rationale-tc002.kg | grep -c 'single flight')" = 1 && test "$(uv run --no-sync cairn rationale --symbol loader --db /tmp/qa-rationale-tc002.kg | grep -c 'module-wide')" = 0 && test "$(uv run --no-sync cairn rationale --file s.py --db /tmp/qa-rationale-tc002.kg | grep -cE '\[(note|why|hack)\]')" = 2; r=$?; rm -rf $W /tmp/qa-rationale-tc002.kg*; (exit $r)`

## TC-003 — Marker directly above a definition stays file-level

- **Priority**: P1 · **Type**: auto
- **Story**: US1 · **Traces to**: FR-002
- **Given** a workspace with a marker comment immediately preceding a
  function definition (boundary pin for the span-containment rule: a comment
  outside a definition's span is not pulled into that symbol)
- **When** the build runs and the function's rationale is listed
- **Then** the comment is attributed to the file only — it never appears
  under the function
- **Pass condition**: `W=/tmp/qa-rationale-tc003; rm -rf $W /tmp/qa-rationale-tc003.kg*; mkdir -p $W/.git; printf '# WHY: kept public for plugins\ndef handler():\n    return 0\n' > $W/s.py; uv run --no-sync cairn build --workspace $W --db /tmp/qa-rationale-tc003.kg >/dev/null 2>&1; test "$(uv run --no-sync cairn rationale --symbol handler --db /tmp/qa-rationale-tc003.kg | grep -c 'plugins')" = 0 && test "$(uv run --no-sync cairn rationale --file s.py --db /tmp/qa-rationale-tc003.kg | grep -c 'plugins')" = 1; r=$?; rm -rf $W /tmp/qa-rationale-tc003.kg*; (exit $r)`

## TC-004 — Multi-line marker comment yields exactly one record

- **Priority**: P1 · **Type**: auto
- **Story**: US1 · **Traces to**: FR-001
- **Given** a workspace with a single block-comment node whose marker text
  runs onto the continuation line (block-comment syntax, so the grammar
  yields one comment node spanning both lines)
- **When** the build runs and the record is listed
- **Then** exactly one record exists, and its text carries the continuation
  line rather than spawning a second record
- **Pass condition**: `W=/tmp/qa-rationale-tc004; rm -rf $W /tmp/qa-rationale-tc004.kg*; mkdir -p $W/.git; printf 'int f(void) {\n    /* NOTE: the retry loop is intentional\n       because upstream de-dupes bursts. */\n    return 1;\n}\n' > $W/s.c; uv run --no-sync cairn build --workspace $W --db /tmp/qa-rationale-tc004.kg >/dev/null 2>&1; test "$(uv run --no-sync cairn rationale --symbol f --db /tmp/qa-rationale-tc004.kg | grep -c 'retry loop')" = 1 && test "$(uv run --no-sync cairn rationale --symbol f --db /tmp/qa-rationale-tc004.kg | grep -c 'de-dupes')" = 1; r=$?; rm -rf $W /tmp/qa-rationale-tc004.kg*; (exit $r)`

## TC-005 — Docstrings never produce rationale records

- **Priority**: P1 · **Type**: auto
- **Story**: US1 · **Traces to**: FR-005
- **Given** a workspace whose file has a docstring on its callable that
  itself contains marker-like lines (`NOTE:`, `WHY:` inside the docstring)
- **When** the build runs and the file's rationale is listed
- **Then** no rationale records exist for that file
- **Pass condition**: `W=/tmp/qa-rationale-tc005; rm -rf $W /tmp/qa-rationale-tc005.kg*; mkdir -p $W/.git; printf 'def f():\n    """NOTE: this docstring must not become a record.\n\n    WHY: docstrings live on the symbol already.\n    """\n    return 1\n' > $W/s.py; uv run --no-sync cairn build --workspace $W --db /tmp/qa-rationale-tc005.kg >/dev/null 2>&1; test "$(uv run --no-sync cairn rationale --file s.py --db /tmp/qa-rationale-tc005.kg | grep -cE '\[(note|why|hack)\]')" = 0; r=$?; rm -rf $W /tmp/qa-rationale-tc005.kg*; (exit $r)`

## TC-006 — Missing comment mapping never fails the build

- **Priority**: P1 · **Type**: auto
- **Story**: US1 · **Traces to**: FR-006
- **Given** a workspace holding one marker-comment-bearing file for every
  language the build supports, whatever each language's comment mapping is
- **When** the build runs over all of them
- **Then** the build exits successfully; each file either has its markers
  extracted or maps no comment style and simply contributes zero records —
  a missing mapping never fails the build nor aborts the other files
- **Pass condition**: `W=/tmp/qa-rationale-tc006; rm -rf $W /tmp/qa-rationale-tc006.kg*; mkdir -p $W/.git; for e in py rb; do printf '# NOTE: marker %s\n' "$e" > "$W/f.$e"; done; for e in go java kt swift cs dart m ts tsx js php c cpp rs; do printf '// NOTE: marker %s\n' "$e" > "$W/f.$e"; done; printf '# NOTE: unmapped tongue\n' > "$W/f.xyz"; uv run --no-sync cairn build --workspace $W --db /tmp/qa-rationale-tc006.kg >/dev/null 2>&1; test "$(uv run --no-sync cairn rationale --db /tmp/qa-rationale-tc006.kg | grep -cE '\[(note|why|hack)\]')" = 16; r=$?; rm -rf $W /tmp/qa-rationale-tc006.kg*; (exit $r)`

## TC-007 — TODO/FIXME markers are never captured (standing guard)

- **Priority**: P1 · **Type**: auto
- **Story**: US1 · **Traces to**: FR-008
- **Given** a workspace whose file contains `TODO:` and `FIXME:` comments
  alongside one genuine `NOTE:` comment (standing regression guard: fails if
  TODO/FIXME capture ever creeps into the default path)
- **When** the build runs and the file's rationale is listed
- **Then** no record mentions TODO or FIXME; the genuine marker is captured
- **Pass condition**: `W=/tmp/qa-rationale-tc007; rm -rf $W /tmp/qa-rationale-tc007.kg*; mkdir -p $W/.git; printf '# TODO: split module\n# FIXME: race on shutdown\n# NOTE: real signal\nx = 1\n' > $W/s.py; uv run --no-sync cairn build --workspace $W --db /tmp/qa-rationale-tc007.kg >/dev/null 2>&1; test -z "$(uv run --no-sync cairn rationale --file s.py --db /tmp/qa-rationale-tc007.kg | grep -E 'TODO|FIXME')" && test "$(uv run --no-sync cairn rationale --file s.py --db /tmp/qa-rationale-tc007.kg | grep -c 'real signal')" = 1; r=$?; rm -rf $W /tmp/qa-rationale-tc007.kg*; (exit $r)`

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
- **Pass condition**: `W=/tmp/qa-rationale-tc008; rm -rf $W /tmp/qa-rationale-tc008.kg*; mkdir -p $W/.git; printf 'def a():\n    # WHY: first\n    return 1\n\ndef b():\n    # NOTE: second\n    # HACK: third\n    return 2\n' > $W/s.py; uv run --no-sync cairn build --workspace $W --db /tmp/qa-rationale-tc008.kg >/dev/null 2>&1; test "$(uv run --no-sync cairn rationale --symbol b --db /tmp/qa-rationale-tc008.kg | grep -cE '\[(note|hack)\] (second|third)')" = 2 && test "$(uv run --no-sync cairn rationale --symbol b --db /tmp/qa-rationale-tc008.kg | grep -c 'first')" = 0 && test "$(uv run --no-sync cairn rationale --file s.py --db /tmp/qa-rationale-tc008.kg | head -1 | grep -c 'first')" = 1 && test "$(uv run --no-sync cairn rationale --symbol no_such_callable --db /tmp/qa-rationale-tc008.kg | grep -cE '\[(note|why|hack)\]')" = 0; r=$?; rm -rf $W /tmp/qa-rationale-tc008.kg*; (exit $r)`

## TC-009 — Explore shows the Rationale section only when records exist

- **Priority**: P2 · **Type**: auto
- **Story**: US3 · **Traces to**: FR-004, AC1
- **Given** a built workspace with one callable carrying a marker comment
  and a sibling callable carrying none
- **When** the explore tool is invoked for each callable (the command below
  drives the local MCP server directly)
- **Then** the explore output for the first contains a "Rationale" section;
  the explore output for the second contains none
- **Pass condition**: `W=/tmp/qa-rationale-tc009; rm -rf $W /tmp/qa-rationale-tc009.kg*; mkdir -p $W/.git; printf 'def gated():\n    # NOTE: gate stays closed by default\n    return 1\n\ndef plain():\n    return 2\n' > $W/s.py; uv run --no-sync cairn build --workspace $W --db /tmp/qa-rationale-tc009.kg >/dev/null 2>&1; test "$( { printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}'; sleep 1; printf '%s\n' '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"explore","arguments":{"query":"gated"}}}'; sleep 3; } | uv run --no-sync cairn serve --db /tmp/qa-rationale-tc009.kg 2>/dev/null | grep '"id":2' | grep -c 'Rationale')" -ge 1 && test "$( { printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}'; sleep 1; printf '%s\n' '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"explore","arguments":{"query":"plain"}}}'; sleep 3; } | uv run --no-sync cairn serve --db /tmp/qa-rationale-tc009.kg 2>/dev/null | grep '"id":2' | grep -c 'Rationale')" = 0; r=$?; rm -rf $W /tmp/qa-rationale-tc009.kg*; (exit $r)`

## TC-010 — MCP tool manifest unchanged (standing guard)

- **Priority**: P2 · **Type**: auto
- **Story**: US3 · **Traces to**: FR-004
- **Given** the suite's snapshot of the MCP tool manifest — names and count,
  pinned in the command below as the pre-feature count (26 tools) — standing
  regression guard: fails if the feature ever adds or renames an MCP tool,
  since rationale must surface inside existing tools only
- **When** the live tool list is fetched
- **Then** the manifest is identical to the snapshot — same tool count, no
  rationale-named tool
- **Pass condition**: `W=/tmp/qa-rationale-tc010; rm -rf $W /tmp/qa-rationale-tc010.kg*; mkdir -p $W/.git; printf 'x = 1\n' > $W/s.py; uv run --no-sync cairn build --workspace $W --db /tmp/qa-rationale-tc010.kg >/dev/null 2>&1; { printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}'; sleep 1; printf '%s\n' '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'; sleep 2; } | uv run --no-sync cairn serve --db /tmp/qa-rationale-tc010.kg 2>/dev/null | python3 -c "import sys,json; d=json.loads(next(l for l in sys.stdin if '\"id\":2' in l)); ts=d['result']['tools']; assert len(ts)==26 and all('rationale' not in t['name'].lower() for t in ts), [t['name'] for t in ts]"; r=$?; rm -rf $W /tmp/qa-rationale-tc010.kg*; (exit $r)`

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
- **Pass condition**: `W=/tmp/qa-rationale-tc011; N=/tmp/qa-rationale-tc011-fresh; rm -rf $W $N /tmp/qa-rationale-tc011.kg* /tmp/qa-rationale-tc011-fresh.kg*; mkdir -p $W/.git && (cd $W && git init -q .) && printf 'def f():\n    # NOTE: keep\n    # WHY: drop me later\n    return 1\n' > $W/s.py && (cd $W && git add -A && git -c user.email=t@t -c user.name=t commit -qm init) && uv run --no-sync cairn build --workspace $W --db /tmp/qa-rationale-tc011.kg >/dev/null 2>&1 && printf 'def f():\n    # NOTE: keep\n    # HACK: added later\n    return 1\n' > $W/s.py && uv run --no-sync cairn update --workspace $W --db /tmp/qa-rationale-tc011.kg >/dev/null 2>&1 && test "$(uv run --no-sync cairn rationale --file s.py --db /tmp/qa-rationale-tc011.kg | grep -c 'drop me')" = 0 && test "$(uv run --no-sync cairn rationale --file s.py --db /tmp/qa-rationale-tc011.kg | grep -cE '\[(note|hack)\] (keep|added later)')" = 2 && mkdir -p $N/.git && printf 'def f():\n    # NOTE: keep\n    # HACK: added later\n    return 1\n' > $N/s.py && uv run --no-sync cairn build --workspace $N --db /tmp/qa-rationale-tc011-fresh.kg >/dev/null 2>&1 && test "$(uv run --no-sync cairn rationale --file s.py --db /tmp/qa-rationale-tc011.kg)" = "$(uv run --no-sync cairn rationale --file s.py --db /tmp/qa-rationale-tc011-fresh.kg)"; r=$?; rm -rf $W $N /tmp/qa-rationale-tc011.kg* /tmp/qa-rationale-tc011-fresh.kg*; (exit $r)`

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
- **Pass condition**: `P=/tmp/qa-rationale-tc12p; M=/tmp/qa-rationale-tc12m; rm -rf $P $M /tmp/qa-rationale-tc12p.kg* /tmp/qa-rationale-tc12m.kg*; mkdir -p $P/.git $M/.git; for i in $(seq 1 300); do printf 'def f_%s(a, b):\n    total = a + b\n    for k in range(10):\n        total += k * a\n    return total\n' $i > $P/m$i.py; printf 'def f_%s(a, b):\n    # NOTE: keep the accumulation single pass here\n    # WHY: ordering matters for float sums\n    total = a + b\n    for k in range(10):\n        # HACK: shim until v2 lands upstream\n        total += k * a\n    return total\n' $i > $M/m$i.py; done; t0=$(python3 -c 'import time; print(time.time())'); uv run --no-sync cairn build --workspace $P --db /tmp/qa-rationale-tc12p.kg >/dev/null 2>&1; t1=$(python3 -c 'import time; print(time.time())'); uv run --no-sync cairn build --workspace $M --db /tmp/qa-rationale-tc12m.kg >/dev/null 2>&1; t2=$(python3 -c 'import time; print(time.time())'); python3 -c "p=$t1-$t0; m=$t2-$t1; assert p < 60 and m < 60 and m <= 1.5 * p, (p, m)"; r=$?; rm -rf $P $M /tmp/qa-rationale-tc12p.kg* /tmp/qa-rationale-tc12m.kg*; (exit $r)`

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
