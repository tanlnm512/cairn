# Test Cases: indexing-exact-rate

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-13

Black-box, business-language verification traced to requirements. Case text
stays in product terms (CLI verbs, the workspace's graph database, printed
output); exact runnable commands live only in the pass conditions.

## Runner conventions

- The measuring binary is the repository venv's CLI, which resolves this
  tree: commands assign `B=/Users/tanle/Projects/cairn/.venv/bin/cairn`
  first. The `cairn` found on PATH is a stale 0.20.0 install and is never
  used (survey, Supporting evidence).
- Out-of-suite measurements pin the graph store to a throwaway directory
  (`H=$(mktemp -d ...)`, then `CAIRN_HOME=$H`) — never the live store
  (survey item CONV). The store database sits one level below the store
  root; `DB=$(find "$H" -mindepth 2 -name .kg)` selects it, skipping the
  zero-byte marker file at the root (layout observed this session).
- The one suite-level guard uses the hermetic runner from survey item CONV:
  `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest ...`.
- Commands assume a POSIX shell with bash-compatible `$( )` substitution.
- After a command, `# → ...` states the expected observable; it is not part
  of the command.

## TC-001 — A fresh build indexes nothing under the excluded vendored areas
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** the repository's committed workspace config excludes the vendored dashboard asset chunks and the third-party benchmark corpora
- **When** a fresh graph build completes on this repository
- **Then** the index contains zero symbols under either excluded area.
- **Pass condition**: `B=/Users/tanle/Projects/cairn/.venv/bin/cairn && H=$(mktemp -d /tmp/qa-tc001.XXXXXX) && cd /Users/tanle/Projects/cairn && CAIRN_HOME=$H $B build >/dev/null && DB=$(find "$H" -mindepth 2 -name .kg) && sqlite3 "$DB" "SELECT COUNT(*) FROM symbols s JOIN files f ON s.file_id=f.id WHERE f.path LIKE 'src/cairn/dashboard/static/chunks/%' OR f.path LIKE 'benchmarks/datasource/%';"` # → 0 (survey FR-006b: without exclusion these areas carry 13,232 symbols across 179 files)

## TC-002 — Excluded files are recorded as configuration-excluded, per area
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** the committed exclusions from TC-001
- **When** the build scans the repository
- **Then** files under each excluded area are recorded in the build's skip log under the configuration-exclusion reason — both areas, not just one.
- **Pass condition**: `B=/Users/tanle/Projects/cairn/.venv/bin/cairn && H=$(mktemp -d /tmp/qa-tc002.XXXXXX) && cd /Users/tanle/Projects/cairn && CAIRN_HOME=$H $B build >/dev/null && DB=$(find "$H" -mindepth 2 -name .kg) && sqlite3 "$DB" "SELECT CASE WHEN path LIKE 'src/cairn/dashboard/static/chunks/%' THEN 'chunks' ELSE 'datasource' END a, COUNT(*) FROM skipped_files WHERE reason='config_exclude' GROUP BY a;"` # → exactly two rows, each at least 1 (survey FR-006b: 103 chunk files, 76 datasource files)

## TC-003 — No workspace config means no configuration exclusions (standing guard)
- **Story**: US1 · **Traces to**: FR-001
- **Given** a copy of this repository whose workspace config file has been removed
- **When** a build runs on the copy
- **Then** nothing is excluded by configuration — indexing behaves exactly as before the config existed.
- **Pass condition**: `C=$(mktemp -d /tmp/qa-tc003.XXXXXX) && rsync -a --exclude=.venv /Users/tanle/Projects/cairn/ "$C"/ && rm -f "$C/cairn.json" && cd "$C" && B=/Users/tanle/Projects/cairn/.venv/bin/cairn && H=$(mktemp -d) && CAIRN_HOME=$H $B build >/dev/null && DB=$(find "$H" -mindepth 2 -name .kg) && sqlite3 "$DB" "SELECT COUNT(*) FROM skipped_files WHERE reason='config_exclude';"` # → 0 (mirror of survey FR-006a's no-config build)

## TC-004 — Exclusions shrink the ambiguous share of the candidate pool
- **Story**: US1 · **Traces to**: FR-001, FR-005, AC2
- **Given** two builds of the same tree — one from a copy with the workspace config removed, one from the repository with the committed exclusions
- **When** the statistics command reports resolution shares for each build
- **Then** the excluded build's ambiguous share, printed as a percentage, is strictly smaller than the no-config build's.
- **Pass condition**: `B=/Users/tanle/Projects/cairn/.venv/bin/cairn && C=$(mktemp -d /tmp/qa-tc004.XXXXXX) && rsync -a --exclude=.venv /Users/tanle/Projects/cairn/ "$C"/ && rm -f "$C/cairn.json" && cd "$C" && H1=$(mktemp -d) && CAIRN_HOME=$H1 $B build >/dev/null && D1=$(find "$H1" -mindepth 2 -name .kg) && cd /Users/tanle/Projects/cairn && H2=$(mktemp -d) && CAIRN_HOME=$H2 $B build >/dev/null && D2=$(find "$H2" -mindepth 2 -name .kg) && A=$($B stats --db "$D1" | tr -d ',' | grep -i ambiguous | grep -oE '[0-9]+(\.[0-9]+)?%' | head -1 | tr -d %) && X=$($B stats --db "$D2" | tr -d ',' | grep -i ambiguous | grep -oE '[0-9]+(\.[0-9]+)?%' | head -1 | tr -d %) && awk -v a="$A" -v x="$X" 'BEGIN { printf "ambiguous share: %s -> %s\n", a, x; if (x + 0 >= a + 0) exit 1 }'` # → exit 0 with the share printed, e.g. "ambiguous share: 28.8 -> 24.2" (survey FR-006a/b pool counts: 17,649 of 61,195 vs 4,786 of 19,786)

## TC-005 — Benchmark and evaluation corpus discovery is unaffected by the workspace config
- **Story**: US1 · **Traces to**: FR-001
- **Given** the repository with its committed exclusions covering the benchmark corpora area
- **When** the benchmark and evaluation tooling resolves its corpora
- **Then** the corpus manifest and the baselines root still resolve from disk exactly as they did before the config existed.
- **Pass condition**: `cd /Users/tanle/Projects/cairn && uv run python -c "from cairn.bench.datasource import default_manifest_path, default_baselines_root; p = default_manifest_path(); print(p, p.is_file()); print(default_baselines_root())"` # → prints the corpus manifest path, True, and the baselines root (survey FR-001d verify; re-run this session)

## TC-006 — Precise caller and impact results are unchanged outside the excluded areas
- **Story**: US1 · **Traces to**: FR-001
- **Given** the two builds from TC-004 — same tree, without and with the committed exclusions
- **When** precise caller lookup and recursive impact analysis run for a repository symbol whose callers all live in the repository's own code
- **Then** both builds return identical results: the same callers at the same files and lines, and the same impact sets at the same depths.
- **Pass condition**: `B=/Users/tanle/Projects/cairn/.venv/bin/cairn && C=$(mktemp -d /tmp/qa-tc006.XXXXXX) && rsync -a --exclude=.venv /Users/tanle/Projects/cairn/ "$C"/ && rm -f "$C/cairn.json" && cd "$C" && H1=$(mktemp -d) && CAIRN_HOME=$H1 $B build >/dev/null && D1=$(find "$H1" -mindepth 2 -name .kg) && cd /Users/tanle/Projects/cairn && H2=$(mktemp -d) && CAIRN_HOME=$H2 $B build >/dev/null && D2=$(find "$H2" -mindepth 2 -name .kg) && P='import json,sys; print("\n".join(sorted(str((r["file_path"], r["caller_name"], r["edge_line"], r["edge_kind"])) for r in json.load(sys.stdin))))' && I='import json,sys; print("\n".join(sorted(str((e["file"], e["symbol"], e["depth"])) for e in json.load(sys.stdin)["impacted"])))' && $B callers get_stats --db "$D1" --json | python3 -c "$P" > /tmp/qa-tc006-a1.txt && $B callers get_stats --db "$D2" --json | python3 -c "$P" > /tmp/qa-tc006-a2.txt && diff /tmp/qa-tc006-a1.txt /tmp/qa-tc006-a2.txt && $B impact get_stats --db "$D1" --json | python3 -c "$I" > /tmp/qa-tc006-b1.txt && $B impact get_stats --db "$D2" --json | python3 -c "$I" > /tmp/qa-tc006-b2.txt && diff /tmp/qa-tc006-b1.txt /tmp/qa-tc006-b2.txt && echo precise-results-unchanged` # → both diffs empty and the marker printed (comparison method validated against the survey's two builds this session; the symbol's five precise callers and 18 impacted symbols matched exactly)

## TC-007 — An update embeds newly added symbols without a manual embed pass
- **Story**: US2 · **Traces to**: FR-002, AC3
- **Given** a small indexed workspace that has completed an embed pass
- **When** a new source symbol is added and an incremental update runs (no separate embed command issued)
- **Then** the new symbol has an embedding row under the model the pass used, so semantic search sees it immediately.
- **Pass condition**: `B=/Users/tanle/Projects/cairn/.venv/bin/cairn && W=$(mktemp -d /tmp/qa-tc007.XXXXXX) && cd "$W" && git init -q . && printf 'def qa_one():\n    """QA fixture docstring."""\n    return 1\n' > m.py && H=$(mktemp -d) && CAIRN_HOME=$H $B build >/dev/null && CAIRN_HOME=$H $B embed >/dev/null && printf '\n\ndef qa_two():\n    return 2\n' >> m.py && CAIRN_HOME=$H $B update >/dev/null && DB=$(find "$H" -mindepth 2 -name .kg) && sqlite3 "$DB" "SELECT COUNT(*) FROM embeddings e JOIN symbols s ON e.symbol_id=s.id WHERE s.name='qa_two';"` # → 1 (measured 0 on the current tree this session — the failing-first baseline)

## TC-008 — An update re-embeds the symbols it changed
- **Story**: US2 · **Traces to**: FR-002, AC3
- **Given** the indexed, embedded workspace from TC-007's setup
- **When** an existing symbol's body changes and an incremental update runs
- **Then** that symbol still has an embedding row afterwards — the update replaces the stale embedding rather than leaving the symbol unembedded.
- **Pass condition**: `B=/Users/tanle/Projects/cairn/.venv/bin/cairn && W=$(mktemp -d /tmp/qa-tc008.XXXXXX) && cd "$W" && git init -q . && printf 'def qa_one():\n    """QA fixture docstring."""\n    return 1\n' > m.py && H=$(mktemp -d) && CAIRN_HOME=$H $B build >/dev/null && CAIRN_HOME=$H $B embed >/dev/null && printf 'def qa_one():\n    """QA fixture docstring."""\n    return 11\n' > m.py && CAIRN_HOME=$H $B update >/dev/null && DB=$(find "$H" -mindepth 2 -name .kg) && sqlite3 "$DB" "SELECT COUNT(*) FROM embeddings e JOIN symbols s ON e.symbol_id=s.id WHERE s.name='qa_one';"` # → 1 (pre-fix behavior deletes the old embedding and writes none — survey FR-002 note)

## TC-009 — An update succeeds and stays observable when no semantic backend exists
- **Story**: US2 · **Traces to**: FR-003, AC4
- **Given** an indexed workspace, newly added symbols, and an embedding backend that cannot load (forced by selecting the server backend with no endpoint — verified this session to fail the availability check)
- **When** an incremental update runs
- **Then** the update completes successfully (exit status zero — never the embed command's hard failure), and the deferred embeds are observable afterwards in the system health report.
- **Pass condition**: `B=/Users/tanle/Projects/cairn/.venv/bin/cairn && W=$(mktemp -d /tmp/qa-tc009.XXXXXX) && cd "$W" && git init -q . && printf 'def qa_one():\n    return 1\n' > m.py && H=$(mktemp -d) && CAIRN_HOME=$H $B build >/dev/null && DOCL=$(mktemp) && printf '\n\ndef qa_two():\n    return 2\n' >> m.py && CAIRN_EMBED_BACKEND=server CAIRN_HOME=$H $B update && CAIRN_EMBED_BACKEND=server CAIRN_HOME=$H $B doctor > "$DOCL" 2>&1; grep -ciE 'unembedded|deferred|not.{0,12}embed|coverage' "$DOCL"` # → update exits 0 (measured this session; the embed command itself exits 1 under this forcing) and the grep prints at least 1 — a health line naming the deferred or unembedded symbols

## TC-010 — A flag-less embed pass builds both multivector kinds
- **Story**: US3 · **Traces to**: FR-004, AC5
- **Given** a freshly built graph over a workspace containing a documented symbol
- **When** the embed command runs with no flags
- **Then** name-only and docstring-only vector rows both exist, and the multivector index is built alongside the single-vector one.
- **Pass condition**: `B=/Users/tanle/Projects/cairn/.venv/bin/cairn && W=$(mktemp -d /tmp/qa-tc010.XXXXXX) && cd "$W" && git init -q . && printf 'def qa_one():\n    """QA fixture docstring."""\n    return 1\n' > m.py && H=$(mktemp -d) && CAIRN_HOME=$H $B build >/dev/null && CAIRN_HOME=$H $B embed >/dev/null && DB=$(find "$H" -mindepth 2 -name .kg) && sqlite3 "$DB" "SELECT DISTINCT vector_kind FROM embeddings_mv ORDER BY 1;" && sqlite3 "$DB" "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'vecmv%';"` # → first query prints exactly two rows — docstring and name; second prints at least 1

## TC-011 — The opt-out flag restores the single-vector build and is documented
- **Story**: US3 · **Traces to**: FR-004, AC5
- **Given** a freshly built graph
- **When** the embed command runs with the multivector opt-out flag
- **Then** no multivector rows or index are created while the single-vector index is fully built, and the opt-out flag appears in the command's help text.
- **Pass condition**: `B=/Users/tanle/Projects/cairn/.venv/bin/cairn && W=$(mktemp -d /tmp/qa-tc011.XXXXXX) && cd "$W" && git init -q . && printf 'def qa_one():\n    return 1\n' > m.py && H=$(mktemp -d) && CAIRN_HOME=$H $B build >/dev/null && CAIRN_HOME=$H $B embed --no-multivector >/dev/null && DB=$(find "$H" -mindepth 2 -name .kg) && MV=$(sqlite3 "$DB" "SELECT COUNT(*) FROM embeddings_mv;") && EM=$(sqlite3 "$DB" "SELECT COUNT(*) FROM embeddings;") && DOC=$($B embed --help | grep -c -- '--no-multivector') && echo "mv=$MV embeddings=$EM help_lines=$DOC" && [ "$MV" = 0 ] && [ "$EM" -gt 0 ] && [ "$DOC" -ge 1 ]` # → mv=0, embeddings greater than 0, help_lines at least 1

## TC-012 — Query-side multivector opt-in behavior is unchanged (standing guard)
- **Story**: US3 · **Traces to**: FR-004
- **Given** the build default now stores multivectors
- **When** the existing query-side multivector suite runs hermetically
- **Then** queries that do not opt in per query never read multivector rows and keep their exact request shapes — the build-default flip leaves the query-side opt-in untouched.
- **Pass condition**: `cd /Users/tanle/Projects/cairn && CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_multivector_query.py -q` # → all pass (23 passed when re-run this session; survey FR-004 rules the query-side flag-off pins stay valid)

## TC-013 — Statistics print all three resolution shares beside edge totals
- **Story**: US4 · **Traces to**: FR-005, AC6
- **Given** any built graph — here, a fresh build of this repository
- **When** the statistics command runs
- **Then** the output names all three resolution outcomes — exact, ambiguous, unresolved — alongside the edge totals.
- **Pass condition**: `B=/Users/tanle/Projects/cairn/.venv/bin/cairn && H=$(mktemp -d /tmp/qa-tc013.XXXXXX) && cd /Users/tanle/Projects/cairn && CAIRN_HOME=$H $B build >/dev/null && DB=$(find "$H" -mindepth 2 -name .kg) && O=$($B stats --db "$DB") && echo "$O" | grep -qi exact && echo "$O" | grep -qi ambiguous && echo "$O" | grep -qi unresolved && echo "$O" | grep -qi edge && echo three-shares-beside-edge-totals` # → the marker prints (the current output contains no "exact" at all — survey FR-005 verify)

## TC-014 — Printed shares agree with the stored edges under the candidate-pool definition
- **Story**: US4 · **Traces to**: FR-005, FR-006, AC6
- **Given** a built graph
- **When** the statistics command prints the exact share
- **Then** that share equals the exact share of the calls-and-references candidate pool computed directly from the stored edges — the same measurement definition the acceptance targets use.
- **Pass condition**: `B=/Users/tanle/Projects/cairn/.venv/bin/cairn && H=$(mktemp -d /tmp/qa-tc014.XXXXXX) && cd /Users/tanle/Projects/cairn && CAIRN_HOME=$H $B build >/dev/null && DB=$(find "$H" -mindepth 2 -name .kg) && E=$($B stats --db "$DB" | tr -d ',' | grep -i exact | grep -oE '[0-9]+(\.[0-9]+)?%' | head -1 | tr -d %) && S=$(sqlite3 "$DB" "SELECT ROUND(100.0*SUM(CASE WHEN resolution='exact' THEN 1 ELSE 0 END)/COUNT(*),1) FROM edges WHERE kind IN ('calls','references') AND resolution IN ('exact','ambiguous');") && awk -v e="$E" -v s="$S" 'BEGIN { if (e - s > 0.5) exit 1; if (s - e > 0.5) exit 1; printf "stats=%s sql=%s\n", e, s }'` # → prints both values and exits 0 (tolerance covers one-vs-two-decimal rendering; survey FR-006a's pool query is the SQL half)

## TC-015 — Statistics on an edgeless graph print zero shares without failing
- **Story**: US4 · **Traces to**: FR-005, AC6
- **Given** a workspace whose single module defines no call or reference edges
- **When** the statistics command runs
- **Then** it exits successfully and still prints all three share labels — as zeros, not an error.
- **Pass condition**: `B=/Users/tanle/Projects/cairn/.venv/bin/cairn && W=$(mktemp -d /tmp/qa-tc015.XXXXXX) && cd "$W" && git init -q . && printf 'x = 1\n' > m.py && H=$(mktemp -d) && CAIRN_HOME=$H $B build >/dev/null && O=$(CAIRN_HOME=$H $B stats) && echo "$O" | grep -qi exact && echo "$O" | grep -qi ambiguous && echo "$O" | grep -qi unresolved && echo zero-edge-shares-printed` # → the marker prints and the command exits 0 (the build summary already renders zero shares for such a workspace — observed this session; the statistics command must not trip over an empty pool)

## TC-016 — Acceptance: exact share of the candidate pool meets the target
- **Story**: US1 · **Traces to**: FR-006
- **Given** the implemented tree with its committed exclusions
- **When** the candidate-pool share is measured over a fresh build — calls-and-references edges with at least one same-name indexed candidate
- **Then** at least 75 percent are exact and at most 25 percent ambiguous (target re-derived pre-approval from the measured exclusion ceiling — provenance in the spec's FR-006); a measured shortfall below 75 percent adjudicates as a fresh decision re-deriving the threshold, and this case then reads the re-derived number — never a silent pass or fail.
- **Pass condition**: `B=/Users/tanle/Projects/cairn/.venv/bin/cairn && H=$(mktemp -d /tmp/qa-tc016.XXXXXX) && cd /Users/tanle/Projects/cairn && CAIRN_HOME=$H $B build >/dev/null && DB=$(find "$H" -mindepth 2 -name .kg) && sqlite3 "$DB" "SELECT ROUND(100.0*SUM(CASE WHEN resolution='exact' THEN 1 ELSE 0 END)/COUNT(*),2), COUNT(*) FROM edges WHERE kind IN ('calls','references') AND resolution IN ('exact','ambiguous');"` # → at least 75.00 over the printed pool size (survey FR-006b projection: 75.81 over 19,786 post-exclusion — 0.81 points of margin above the re-derived bar)

## TC-017 — Acceptance: embedding coverage is complete after an embed pass
- **Story**: US2 · **Traces to**: FR-006
- **Given** a fresh build of this repository with the committed exclusions
- **When** one embed pass completes
- **Then** every indexed symbol has an embedding row — none left pending.
- **Pass condition**: `B=/Users/tanle/Projects/cairn/.venv/bin/cairn && H=$(mktemp -d /tmp/qa-tc017.XXXXXX) && cd /Users/tanle/Projects/cairn && CAIRN_HOME=$H $B build >/dev/null && CAIRN_HOME=$H $B embed >/dev/null && DB=$(find "$H" -mindepth 2 -name .kg) && sqlite3 "$DB" "SELECT COUNT(*) FROM symbols s WHERE NOT EXISTS (SELECT 1 FROM embeddings e WHERE e.symbol_id = s.id);"` # → 0 (survey FR-006b: the live store measured 10,880 of 20,296 embedded — the gap this target closes)

## Coverage matrix
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001      | TC-001, TC-002, TC-003, TC-004, TC-005, TC-006 | auto |
| FR-002      | TC-007, TC-008 | auto |
| FR-003      | TC-009 | auto |
| FR-004      | TC-010, TC-011, TC-012 | auto |
| FR-005      | TC-004, TC-013, TC-014, TC-015 | auto |
| FR-006      | TC-014, TC-016, TC-017 | auto |

## Acceptance-criterion coverage
| Acceptance criterion | Test cases |
|----------------------|------------|
| AC1 | TC-001, TC-002, TC-003, TC-005, TC-006 |
| AC2 | TC-004 |
| AC3 | TC-007, TC-008 |
| AC4 | TC-009 |
| AC5 | TC-010, TC-011, TC-012 |
| AC6 | TC-013, TC-014, TC-015 |
| FR-006 acceptance targets | TC-016, TC-017 |

## Evidence anchors
| Survey item | Anchored by |
|-------------|------------|
| FR-001a, FR-001b | TC-001, TC-002, TC-003 |
| FR-001c | TC-001 (the noise inventory the exclusion removes) |
| FR-001d | TC-005 |
| FR-002 | TC-007, TC-008 |
| FR-003 | TC-009 |
| FR-004 | TC-010, TC-011, TC-012 |
| FR-005 | TC-013, TC-014, TC-015 |
| FR-006a, FR-006b | TC-004, TC-014, TC-016, TC-017 |
| CONV | runner conventions above; TC-012 |
