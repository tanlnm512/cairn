# Test Cases: pr-tooling

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-04
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.
"Prepared workspace" = the cairn workspace with a built store (`cairn update`
current), so impact reflects the local index.
"Stubbed gh" = for the duration of one command, a temp dir is prepended to
PATH holding a fake `gh` that prints pinned fixture responses for read-only
calls and never touches the network; where a case logs calls, the stub also
records its arguments to the file named by `CAIRN_STUB_LOG`. No TC makes a
network call.

## TC-001 — PR list shows every field a reviewer triages by
- **Story**: US1 · **Traces to**: FR-001 (AC1)
- **Given** gh is authenticated (stubbed) with two open PRs, one green and approved, one red and awaiting review
- **When** the user runs `cairn prs`
- **Then** the command succeeds and each PR shows its number, title, branch, author, CI state, and review decision — both fixture PRs appear with all six fields
- **Pass condition**: `d=$(mktemp -d) && printf '%b' "#!/bin/sh\ncase \"\$1 \$2\" in\n\"pr list\") echo '[{\"number\":42,\"title\":\"Cap traversal depth\",\"headRefName\":\"fix/depth-cap\",\"author\":{\"login\":\"ada\"},\"statusCheckRollup\":[{\"state\":\"SUCCESS\"}],\"reviewDecision\":\"APPROVED\"},{\"number\":43,\"title\":\"Add compass export\",\"headRefName\":\"feat/export\",\"author\":{\"login\":\"linus\"},\"statusCheckRollup\":[{\"state\":\"FAILURE\"}],\"reviewDecision\":\"REVIEW_REQUIRED\"}]' ;;\nesac\n" > "$d/gh" && chmod +x "$d/gh" && PATH="$d:$PATH" cairn prs > "$d/out" 2>&1 && grep -q 42 "$d/out" && grep -q "Cap traversal depth" "$d/out" && grep -q "fix/depth-cap" "$d/out" && grep -q ada "$d/out" && grep -Eiq "success|green" "$d/out" && grep -Eiq "approved" "$d/out" && grep -q 43 "$d/out" && grep -Eiq "failure|failing|red" "$d/out" && grep -Eiq "review required|required" "$d/out"`

## TC-002 — No open PRs is a clean empty state, not an error
- **Story**: US1 · **Traces to**: FR-001 (AC1)
- **Given** gh is authenticated (stubbed) and the workspace has zero open PRs
- **When** the user runs `cairn prs`
- **Then** the command exits 0, prints a human-readable empty state, and renders no PR rows
- **Pass condition**: `d=$(mktemp -d) && printf '%b' "#!/bin/sh\ncase \"\$1 \$2\" in\n\"pr list\") echo '[]' ;;\nesac\n" > "$d/gh" && chmod +x "$d/gh" && PATH="$d:$PATH" cairn prs > "$d/out" 2>&1 && grep -Eiq "no open|no pull|none|0 open" "$d/out"`

## TC-003 — A long PR list renders completely
- **Story**: US1 · **Traces to**: FR-001
- **Given** gh is authenticated (stubbed) with 50 open PRs
- **When** the user runs `cairn prs`
- **Then** the command succeeds and all 50 PRs appear — the list is not truncated or sampled
- **Pass condition**: `d=$(mktemp -d) && printf '%b' "#!/bin/sh\ncase \"\$1 \$2\" in\n\"pr list\") awk 'BEGIN{printf \"[\";for(i=1;i<=50;i++)printf \"%s{\\\"number\\\":%d,\\\"title\\\":\\\"T%02d\\\",\\\"headRefName\\\":\\\"branch%02d\\\",\\\"author\\\":{\\\"login\\\":\\\"u%02d\\\"},\\\"statusCheckRollup\\\":[{\\\"state\\\":\\\"SUCCESS\\\"}],\\\"reviewDecision\\\":\\\"APPROVED\\\"}\",(i>1?\",\":\"\"),i,i,i,i;printf \"]\"}' ;;\nesac\n" > "$d/gh" && chmod +x "$d/gh" && PATH="$d:$PATH" cairn prs > "$d/out" 2>&1 && test "$(grep -o "T[0-9][0-9]" "$d/out" | wc -l | tr -d " ")" -ge 50`

## TC-004 — gh absent fails cleanly with install guidance and no partial table
- **Story**: US1 · **Traces to**: FR-002 (AC2)
- **Given** gh is not installed (no `gh` on the command search path) and stub output is impossible
- **When** the user runs `cairn prs`
- **Then** the command exits non-zero with guidance naming gh (install or authenticate), and prints no PR table rows
- **Pass condition**: `o=$(mktemp -d) && if env PATH="/usr/bin:/bin" "$(command -v cairn)" prs > "$o/out" 2>&1; then false; fi && grep -qi gh "$o/out" && grep -Eiq "install|authenticat|login" "$o/out"`

## TC-005 — gh unauthenticated fails cleanly with auth guidance and no partial table
- **Story**: US1 · **Traces to**: FR-002 (AC2)
- **Given** gh is installed but unauthenticated (stubbed gh refuses with its auth-login message)
- **When** the user runs `cairn prs`
- **Then** the command exits non-zero, passes gh's guidance through, and prints no PR rows
- **Pass condition**: `d=$(mktemp -d) && printf '%b' "#!/bin/sh\ncase \"\$1 \$2\" in\n\"pr list\") echo \"gh: To get started with GitHub CLI, please run: gh auth login\" >&2\nexit 4 ;;\nesac\n" > "$d/gh" && chmod +x "$d/gh" && if PATH="$d:$PATH" cairn prs > "$d/out" 2>&1; then false; fi && grep -qi "auth login" "$d/out" && ! grep -q 42 "$d/out"`

## TC-006 — gh output missing expected fields errors with the gh version named
- **Story**: US1 · **Traces to**: FR-002
- **Given** stubbed gh answers with JSON shaped unlike any known release (required fields absent)
- **When** the user runs `cairn prs`
- **Then** the command exits non-zero with a defensive-parse error that names the gh version (the stub reports 2.63.0), and prints no partial table
- **Pass condition**: `d=$(mktemp -d) && printf '%b' "#!/bin/sh\ncase \"\$1 \$2\" in\n\"--version\"*) echo 'gh version 2.63.0 (2024-11-19)' ;;\n\"pr list\"*) echo '{\"unexpected\":true}' ;;\nesac\n" > "$d/gh" && chmod +x "$d/gh" && if PATH="$d:$PATH" cairn prs > "$d/out" 2>&1; then false; fi && grep -q "2.63" "$d/out" && ! grep -qi "traceback" "$d/out"`

## TC-007 — Impact resolves a PR's changed files to symbols with caller counts
- **Story**: US2 · **Traces to**: FR-003 (AC1)
- **Given** a prepared workspace and a stubbed open PR whose diff (against its base branch) touches a source file of this workspace that the index knows
- **When** the user runs `cairn prs --impact 42`
- **Then** the changed file resolves to changed symbols and each prints with its caller count from the live graph
- **Pass condition**: `d=$(mktemp -d) && printf '%b' "#!/bin/sh\ncase \"\$1 \$2\" in\n\"pr list\") echo '[{\"number\":42,\"title\":\"Cap traversal depth\",\"headRefName\":\"fix/depth-cap\",\"author\":{\"login\":\"ada\"},\"statusCheckRollup\":[{\"state\":\"SUCCESS\"}],\"reviewDecision\":\"APPROVED\"}]' ;;\n\"pr view\"*) echo '{\"baseRefName\":\"main\"}' ;;\n\"pr diff\"*) printf '%b' 'diff --git a/src/cairn/graph/traversal.py b/src/cairn/graph/traversal.py\n--- a/src/cairn/graph/traversal.py\n+++ b/src/cairn/graph/traversal.py\n@@ -157,6 +157,7 @@\n def impact_analysis(\n+    # fixture touch for PR 42\n     pass\n' ;;\nesac\n" > "$d/gh" && chmod +x "$d/gh" && PATH="$d:$PATH" cairn prs --impact 42 > "$d/out" 2>&1 && grep -q "traversal.py" "$d/out" && grep -Eiq "caller" "$d/out"`

## TC-008 — Unindexed changed files are listed, never silently dropped
- **Story**: US2 · **Traces to**: FR-003 (AC2)
- **Given** a stubbed open PR whose diff touches one indexed source file and one file with no indexed symbols (a new prose document)
- **When** impact runs for that PR
- **Then** the unindexed file is explicitly listed as unindexed alongside the resolved symbols
- **Pass condition**: `d=$(mktemp -d) && printf '%b' "#!/bin/sh\ncase \"\$1 \$2\" in\n\"pr list\") echo '[{\"number\":42,\"title\":\"Cap traversal depth\",\"headRefName\":\"fix/depth-cap\",\"author\":{\"login\":\"ada\"},\"statusCheckRollup\":[{\"state\":\"SUCCESS\"}],\"reviewDecision\":\"APPROVED\"}]' ;;\n\"pr view\"*) echo '{\"baseRefName\":\"main\"}' ;;\n\"pr diff\"*) printf '%b' 'diff --git a/src/cairn/graph/traversal.py b/src/cairn/graph/traversal.py\n--- a/src/cairn/graph/traversal.py\n+++ b/src/cairn/graph/traversal.py\n@@ -157,6 +157,7 @@\n def impact_analysis(\n+    # fixture touch for PR 42\n     pass\ndiff --git a/docs/design-note.md b/docs/design-note.md\nnew file mode 100644\n--- /dev/null\n+++ b/docs/design-note.md\n@@ -0,0 +1,2 @@\n+Design note prose.\n' ;;\nesac\n" > "$d/gh" && chmod +x "$d/gh" && PATH="$d:$PATH" cairn prs --impact 42 > "$d/out" 2>&1 && grep -q "design-note.md" "$d/out" && grep -Eiq "unindexed|not indexed|no symbols" "$d/out"`

## TC-009 — A PR with an empty diff yields an empty impact, not a failure
- **Story**: US2 · **Traces to**: FR-003 (AC1)
- **Given** a stubbed open PR whose diff against its base is empty
- **When** the user runs `cairn prs --impact 42`
- **Then** the command exits 0 and reports an impact section with no changed symbols — no crash, no error
- **Pass condition**: `d=$(mktemp -d) && printf '%b' "#!/bin/sh\ncase \"\$1 \$2\" in\n\"pr list\") echo '[{\"number\":42,\"title\":\"Cap traversal depth\",\"headRefName\":\"fix/depth-cap\",\"author\":{\"login\":\"ada\"},\"statusCheckRollup\":[{\"state\":\"SUCCESS\"}],\"reviewDecision\":\"APPROVED\"}]' ;;\n\"pr view\"*) echo '{\"baseRefName\":\"main\"}' ;;\n\"pr diff\"*) echo '' ;;\nesac\n" > "$d/gh" && chmod +x "$d/gh" && PATH="$d:$PATH" cairn prs --impact 42 > "$d/out" 2>&1 && ! grep -qi "traceback" "$d/out"`

## TC-010 — Impact states which store it reflects and how old it is
- **Story**: US2 · **Traces to**: FR-007
- **Given** a prepared workspace whose store was last built some time ago, and a stubbed open PR
- **When** impact runs for that PR
- **Then** the impact output names the local store/index it reflects and states its build age — it never implies the numbers are fresh from the PR branch (standing guard: fails if impact ever presents local-index numbers as branch-fresh)
- **Pass condition**: `d=$(mktemp -d) && printf '%b' "#!/bin/sh\ncase \"\$1 \$2\" in\n\"pr list\") echo '[{\"number\":42,\"title\":\"Cap traversal depth\",\"headRefName\":\"fix/depth-cap\",\"author\":{\"login\":\"ada\"},\"statusCheckRollup\":[{\"state\":\"SUCCESS\"}],\"reviewDecision\":\"APPROVED\"}]' ;;\n\"pr view\"*) echo '{\"baseRefName\":\"main\"}' ;;\n\"pr diff\"*) printf '%b' 'diff --git a/src/cairn/graph/traversal.py b/src/cairn/graph/traversal.py\n--- a/src/cairn/graph/traversal.py\n+++ b/src/cairn/graph/traversal.py\n@@ -157,6 +157,7 @@\n def impact_analysis(\n+    # fixture touch for PR 42\n     pass\n' ;;\nesac\n" > "$d/gh" && chmod +x "$d/gh" && PATH="$d:$PATH" cairn prs --impact 42 > "$d/out" 2>&1 && grep -Eiq "store|index|local" "$d/out" && grep -Eiq "age|built|updated|old|as of|stale" "$d/out"`

## TC-011 — PR pairs rank by shared community, community named
- **Story**: US3 · **Traces to**: FR-004 (AC1)
- **Given** a prepared workspace where community tables exist (`cairn communities` has run), with two overlapping PR pairs: one sharing a community heavily, one sharing little
- **When** the user runs `cairn prs --conflicts`
- **Then** both pairs are reported, the heavy-overlap pair ranks first, and each reported pair names the community its members share
- **Pass condition**: manual — with community tables present, read the `--conflicts` ranking: the pair touching more of the same community appears above the pair touching less, and every reported pair names its shared community (runnable only once the symbol-communities tables exist on this workspace)

## TC-012 — No community data omits the section with a hint, exit 0
- **Story**: US3 · **Traces to**: FR-004 (AC2)
- **Given** a prepared workspace where community tables are absent or empty, and stubbed open PRs
- **When** the user runs `cairn prs --conflicts`
- **Then** the command exits 0, omits the conflicts section, and prints a hint to run `cairn communities` — graceful degradation, no failure (this spec consumes community tables, never builds them)
- **Pass condition**: `d=$(mktemp -d) && printf '%b' "#!/bin/sh\ncase \"\$1 \$2\" in\n\"pr list\") echo '[{\"number\":42,\"title\":\"Cap traversal depth\",\"headRefName\":\"fix/depth-cap\",\"author\":{\"login\":\"ada\"},\"statusCheckRollup\":[{\"state\":\"SUCCESS\"}],\"reviewDecision\":\"APPROVED\"},{\"number\":43,\"title\":\"Add compass export\",\"headRefName\":\"feat/export\",\"author\":{\"login\":\"linus\"},\"statusCheckRollup\":[{\"state\":\"FAILURE\"}],\"reviewDecision\":\"REVIEW_REQUIRED\"}]' ;;\nesac\n" > "$d/gh" && chmod +x "$d/gh" && PATH="$d:$PATH" cairn prs --conflicts > "$d/out" 2>&1 && grep -Eiq "communities" "$d/out"`

## TC-013 — JSON payload carries the list and the requested impact
- **Story**: US4 · **Traces to**: FR-005 (AC1)
- **Given** a prepared workspace with a stubbed open PR whose diff touches an indexed source file
- **When** an agent runs `cairn prs --json --impact 42`
- **Then** the output parses as JSON and the payload carries the PR list section (fixture PR title present) and the impact section (changed file present)
- **Pass condition**: `d=$(mktemp -d) && printf '%b' "#!/bin/sh\ncase \"\$1 \$2\" in\n\"pr list\") echo '[{\"number\":42,\"title\":\"Cap traversal depth\",\"headRefName\":\"fix/depth-cap\",\"author\":{\"login\":\"ada\"},\"statusCheckRollup\":[{\"state\":\"SUCCESS\"}],\"reviewDecision\":\"APPROVED\"}]' ;;\n\"pr view\"*) echo '{\"baseRefName\":\"main\"}' ;;\n\"pr diff\"*) printf '%b' 'diff --git a/src/cairn/graph/traversal.py b/src/cairn/graph/traversal.py\n--- a/src/cairn/graph/traversal.py\n+++ b/src/cairn/graph/traversal.py\n@@ -157,6 +157,7 @@\n def impact_analysis(\n+    # fixture touch for PR 42\n     pass\n' ;;\nesac\n" > "$d/gh" && chmod +x "$d/gh" && PATH="$d:$PATH" cairn prs --json --impact 42 > "$d/out" 2>&1 && python3 -m json.tool "$d/out" > /dev/null && grep -q "Cap traversal depth" "$d/out" && grep -q "traversal.py" "$d/out"`

## TC-014 — JSON payload carries the conflicts hint when community data is absent
- **Story**: US4 · **Traces to**: FR-005 (AC1), FR-004 (AC2)
- **Given** a prepared workspace without community tables, with stubbed open PRs
- **When** an agent runs `cairn prs --json --conflicts`
- **Then** the output parses as JSON, exits 0, and the payload carries the communities hint instead of a conflicts ranking
- **Pass condition**: `d=$(mktemp -d) && printf '%b' "#!/bin/sh\ncase \"\$1 \$2\" in\n\"pr list\") echo '[{\"number\":42,\"title\":\"Cap traversal depth\",\"headRefName\":\"fix/depth-cap\",\"author\":{\"login\":\"ada\"},\"statusCheckRollup\":[{\"state\":\"SUCCESS\"}],\"reviewDecision\":\"APPROVED\"}]' ;;\nesac\n" > "$d/gh" && chmod +x "$d/gh" && PATH="$d:$PATH" cairn prs --json --conflicts > "$d/out" 2>&1 && python3 -m json.tool "$d/out" > /dev/null && grep -Eiq "communities" "$d/out"`

## TC-015 — Standing guard: every gh call the command makes is read-only
- **Story**: US1 · **Traces to**: FR-006
- **Given** a stubbed gh that logs the arguments of every call it receives, with open PRs present
- **When** the full command runs with all sections (`cairn prs --impact 42 --conflicts`)
- **Then** gh was called, and every logged call is a read-only one (list/view/diff/status shape) — never a mutating subcommand such as merge, close, edit, comment, review, or create (standing guard: fails if the default path ever grows a gh mutation)
- **Pass condition**: `d=$(mktemp -d) && printf '%b' "#!/bin/sh\nprintf '%s\n' \"\$*\" >> \"\$CAIRN_STUB_LOG\"\ncase \"\$1 \$2\" in\n\"pr list\") echo '[{\"number\":42,\"title\":\"Cap traversal depth\",\"headRefName\":\"fix/depth-cap\",\"author\":{\"login\":\"ada\"},\"statusCheckRollup\":[{\"state\":\"SUCCESS\"}],\"reviewDecision\":\"APPROVED\"}]' ;;\n\"pr view\"*) echo '{\"baseRefName\":\"main\"}' ;;\n\"pr diff\"*) printf '%b' 'diff --git a/src/cairn/graph/traversal.py b/src/cairn/graph/traversal.py\n--- a/src/cairn/graph/traversal.py\n+++ b/src/cairn/graph/traversal.py\n@@ -157,6 +157,7 @@\n def impact_analysis(\n+    # fixture touch for PR 42\n     pass\n' ;;\nesac\n" > "$d/gh" && chmod +x "$d/gh" && CAIRN_STUB_LOG="$d/log" PATH="$d:$PATH" cairn prs --impact 42 --conflicts > "$d/out" 2>&1 && test -s "$d/log" && ! grep -Evq '^(pr (list|view|diff|status)( |$)|--version)' "$d/log"`

## TC-016 — Standing guard: the store is byte-identical after a run
- **Story**: US1 · **Traces to**: FR-006
- **Given** a prepared workspace with stubbed open PRs and the store database checksummed
- **When** the full command runs (`cairn prs --impact 42 --conflicts`)
- **Then** the store database checksum after the run equals the checksum before — the command writes nothing to the store (standing guard: fails if the read-only command ever grows a store write)
- **Pass condition**: manual — checksum the store database (path reported by `cairn config --json`) before and after `cairn prs --impact 42 --conflicts` under a stubbed gh; the two checksums must be identical

## TC-017 — Abuse case: a token in the environment never leaks through the command
- **Story**: US1 · **Traces to**: NFR-001, FR-006
- **Given** an attacker-controlled environment where a GitHub token is set (`GH_TOKEN` carrying a sentinel secret), with stubbed gh logging its arguments
- **When** the user runs `cairn prs`
- **Then** the command succeeds, and the sentinel appears nowhere — not in the command's output, not in any argument passed to gh (gh's own configuration is the only token channel)
- **Pass condition**: `d=$(mktemp -d) && printf '%b' "#!/bin/sh\nprintf '%s\n' \"\$*\" >> \"\$CAIRN_STUB_LOG\"\ncase \"\$1 \$2\" in\n\"pr list\") echo '[{\"number\":42,\"title\":\"Cap traversal depth\",\"headRefName\":\"fix/depth-cap\",\"author\":{\"login\":\"ada\"},\"statusCheckRollup\":[{\"state\":\"SUCCESS\"}],\"reviewDecision\":\"APPROVED\"}]' ;;\nesac\n" > "$d/gh" && chmod +x "$d/gh" && CAIRN_STUB_LOG="$d/log" GH_TOKEN=sekrit-sentinel-9 PATH="$d:$PATH" cairn prs > "$d/out" 2>&1 && ! grep -q sekrit-sentinel-9 "$d/out" "$d/log"`

## TC-018 — Impact completes inside the existing per-invocation cost profile
- **Story**: US2 · **Traces to**: NFR-003
- **Given** a prepared workspace and a stubbed open PR whose diff touches a heavily-connected source file
- **When** impact runs for that PR
- **Then** it terminates (depth caps hold — no unbounded traversal) and completes in well under a minute on the bounded workspace; at scale, the existing review-pack cost profile remains the standing verify
- **Pass condition**: `d=$(mktemp -d) && printf '%b' "#!/bin/sh\ncase \"\$1 \$2\" in\n\"pr list\") echo '[{\"number\":42,\"title\":\"Cap traversal depth\",\"headRefName\":\"fix/depth-cap\",\"author\":{\"login\":\"ada\"},\"statusCheckRollup\":[{\"state\":\"SUCCESS\"}],\"reviewDecision\":\"APPROVED\"}]' ;;\n\"pr view\"*) echo '{\"baseRefName\":\"main\"}' ;;\n\"pr diff\"*) printf '%b' 'diff --git a/src/cairn/graph/traversal.py b/src/cairn/graph/traversal.py\n--- a/src/cairn/graph/traversal.py\n+++ b/src/cairn/graph/traversal.py\n@@ -157,6 +157,7 @@\n def impact_analysis(\n+    # fixture touch for PR 42\n     pass\n' ;;\nesac\n" > "$d/gh" && chmod +x "$d/gh" && s=$(date +%s) && PATH="$d:$PATH" cairn prs --impact 42 > /dev/null 2>&1 && test $(( $(date +%s) - s )) -lt 30` (bounded proof; standing at-scale verify: the existing blast/impact test suite, `python3 -m pytest tests/ -q -k "blast and not infra"`)

## TC-019 — gh failures surface gh's own message, cleanly
- **Story**: US1 · **Traces to**: NFR-004, FR-002
- **Given** stubbed gh fails with its rate-limit message and a non-zero exit
- **When** the user runs `cairn prs`
- **Then** the command exits non-zero, the error carries gh's own message (rate limit text), and no traceback and no partial table appear
- **Pass condition**: `d=$(mktemp -d) && printf '%b' "#!/bin/sh\ncase \"\$1 \$2\" in\n\"pr list\") echo \"gh: API rate limit exceeded for installation ID 1181. Please wait 24 minutes and try again.\" >&2\nexit 2 ;;\nesac\n" > "$d/gh" && chmod +x "$d/gh" && if PATH="$d:$PATH" cairn prs > "$d/out" 2>&1; then false; fi && grep -qi "rate limit" "$d/out" && ! grep -qi "traceback" "$d/out"`

## TC-020 — Concurrent invocations both succeed
- **Story**: US1 · **Traces to**: FR-006, FR-001
- **Given** a prepared workspace with stubbed open PRs
- **When** two `cairn prs` runs execute at the same time
- **Then** both exit 0 and both render the PR list — read-only access needs no locking and neither run starves or corrupts the other
- **Pass condition**: `d=$(mktemp -d) && printf '%b' "#!/bin/sh\ncase \"\$1 \$2\" in\n\"pr list\") echo '[{\"number\":42,\"title\":\"Cap traversal depth\",\"headRefName\":\"fix/depth-cap\",\"author\":{\"login\":\"ada\"},\"statusCheckRollup\":[{\"state\":\"SUCCESS\"}],\"reviewDecision\":\"APPROVED\"}]' ;;\nesac\n" > "$d/gh" && chmod +x "$d/gh" && (PATH="$d:$PATH" cairn prs > "$d/a" 2>&1 & p1=$!; PATH="$d:$PATH" cairn prs > "$d/b" 2>&1 & p2=$!; wait "$p1" && wait "$p2" && grep -q 42 "$d/a" && grep -q 42 "$d/b")`

## Coverage matrix
<!-- Every FR and applicable NFR appears; `check.py` fails a requirement with no TC. -->
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001      | TC-001, TC-002, TC-003 | auto |
| FR-002      | TC-004, TC-005, TC-006 | auto |
| FR-003      | TC-007, TC-008, TC-009 | auto |
| FR-004      | TC-011, TC-012, TC-014 | manual + auto |
| FR-005      | TC-013, TC-014 | auto |
| FR-006      | TC-015, TC-016, TC-020 | auto + manual |
| FR-007      | TC-010 | auto |
| NFR-001     | TC-017 | auto |
| NFR-003     | TC-018 | auto (bounded; standing at-scale verify named in Then) |
| NFR-004     | TC-019 | auto |

Not applicable (recorded in spec.md with reasons): NFR-002 (privacy), NFR-005 (observability), NFR-006 (accessibility) — no TCs required.

AC trace: US1-AC1 → TC-001, TC-002, TC-003 · US1-AC2 → TC-004, TC-005 · US2-AC1 → TC-007, TC-009 · US2-AC2 → TC-008 · US3-AC1 → TC-011 · US3-AC2 → TC-012, TC-014 · US4-AC1 → TC-013, TC-014.
