# Test Cases: memory-stance-overlay

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-04
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details — the only
product surface named is the user-facing command surface the spec itself
promises.

**Fixtures**: every auto command is a single self-contained line that
provisions its own hermetic scratch store under `/tmp/mso-tcNNN/` — a tiny
git-committed Python workspace, a graph db built from it, and a fresh
knowledge bundle — and addresses that store through the memory verbs'
explicit `--db`/`--knowledge` flags, so nothing outside the scratch dir is
read or written (the one perf case byte-copies this repository's own store
into its scratch dir instead of touching it). Commands run from the
repository root, assert by chained exit statuses, greps, and diffs, and
remove their scratch dir on success. Bodies cite code identifiers with
backticks; the commands compose that character via `printf '\140'` so each
command stays a single literal span. Every auto pass condition finishes in
seconds — well under the 120 s harness cap.

## TC-001 — Reflect runs and reports (P1 · auto)

- **Story**: US1 · **Traces to**: FR-002
- **Given** a fixture store holding two memories that share a cited code identifier
- **When** `cairn memory reflect` runs once
- **Then** it completes and prints a summary of the stance pass — memories evaluated and stances assigned — with no failures reported
- **Pass condition**: `D=/tmp/mso-tc001 && rm -rf $D && mkdir -p $D/ws && printf 'def mso_login():\n    return 1\n' > $D/ws/app.py && git -C $D/ws init -q && git -C $D/ws add -A && git -C $D/ws -c user.email=q@q -c user.name=q commit -qm init && uv run --no-sync cairn build --workspace $D/ws --db $D/graph.db > /dev/null 2>&1 && B=$(printf '\140') && uv run --no-sync cairn memory record pattern "mso-shared" --body "entry uses ${B}mso_login${B} directly" --db $D/graph.db --knowledge $D/knowledge > /dev/null && uv run --no-sync cairn memory record pattern "mso-also" --body "wrapper also calls ${B}mso_login${B}" --db $D/graph.db --knowledge $D/knowledge > /dev/null && uv run --no-sync cairn memory reflect --db $D/graph.db --knowledge $D/knowledge | grep -qE '^Reflected 2 memory\(ies\): [0-9]+ changed, [0-9]+ unchanged, [0-9]+ contested\.$' && rm -rf $D`
  The chain exits 0 only if both records land and the printed summary matches the reported shape — memories evaluated and the changed/unchanged/contested stances — on a single line.

## TC-002 — Verified guidance wins, contradicted guidance is flagged (P1 · auto)

- **Story**: US1 AC1 · **Traces to**: FR-002, FR-003
- **Given** a fixture store with a memory whose citation verifies against the indexed code and a contradicting successor that cites the same identifier (recorded via the revise verb, so the predecessor points at the successor)
- **When** `cairn memory reflect` runs
- **Then** the fully verified memory is marked preferred; the contradicting memory is marked contested and records the verified memory's identity as its contradicting peer
- **Pass condition**: `D=/tmp/mso-tc002 && rm -rf $D && mkdir -p $D/ws && printf 'def mso_login():\n    return 1\n' > $D/ws/app.py && git -C $D/ws init -q && git -C $D/ws add -A && git -C $D/ws -c user.email=q@q -c user.name=q commit -qm init && uv run --no-sync cairn build --workspace $D/ws --db $D/graph.db > /dev/null 2>&1 && B=$(printf '\140') && P1=$(uv run --no-sync cairn memory record decision "mso-pair" --body "guidance: call ${B}mso_login${B} before every write" --db $D/graph.db --knowledge $D/knowledge | grep -oE 'memory/[a-z]+/[a-z0-9-]+' | head -1) && P2=$(uv run --no-sync cairn memory evolve "$P1" --body "contradicting guidance: never call ${B}mso_login${B} inside a write" --db $D/graph.db --knowledge $D/knowledge | sed -E 's/.* -> (memory[^ ]+).*/\1/') && uv run --no-sync cairn memory reflect --db $D/graph.db --knowledge $D/knowledge | grep -q '1 contested.' && uv run --no-sync cairn memory list --db $D/graph.db --knowledge $D/knowledge | grep -qF "stance=contested peer=$P2" && uv run --no-sync cairn memory list --db $D/graph.db --knowledge $D/knowledge | grep -q 'stance=preferred] mso-pair' && rm -rf $D`
  The chain exits 0 only if the reflect summary reports exactly one contested memory, the listing marks the superseded memory contested with the verified memory's path as its peer, and the verified memory renders preferred.

## TC-003 — Reflect twice, same answer (P1 · auto)

- **Story**: US1 AC2 · **Traces to**: FR-006
- **Given** a fixture store seeded with a contradicting pair and already reflected once
- **When** `cairn memory reflect` runs twice more with no store changes in between
- **Then** both runs report the same stances, the same peer references, in the same order (idempotent and deterministic)
- **Pass condition**: `D=/tmp/mso-tc003 && rm -rf $D && mkdir -p $D/ws && printf 'def mso_login():\n    return 1\n' > $D/ws/app.py && git -C $D/ws init -q && git -C $D/ws add -A && git -C $D/ws -c user.email=q@q -c user.name=q commit -qm init && uv run --no-sync cairn build --workspace $D/ws --db $D/graph.db > /dev/null 2>&1 && B=$(printf '\140') && P1=$(uv run --no-sync cairn memory record decision "mso-pair" --body "guidance: call ${B}mso_login${B} before every write" --db $D/graph.db --knowledge $D/knowledge | grep -oE 'memory/[a-z]+/[a-z0-9-]+' | head -1) && uv run --no-sync cairn memory evolve "$P1" --body "contradicting guidance: never call ${B}mso_login${B} inside a write" --db $D/graph.db --knowledge $D/knowledge > /dev/null && uv run --no-sync cairn memory reflect --db $D/graph.db --knowledge $D/knowledge > /dev/null && uv run --no-sync cairn memory reflect --db $D/graph.db --knowledge $D/knowledge > $D/r1.out && uv run --no-sync cairn memory list --db $D/graph.db --knowledge $D/knowledge > $D/l1.out && uv run --no-sync cairn memory reflect --db $D/graph.db --knowledge $D/knowledge > $D/r2.out && uv run --no-sync cairn memory list --db $D/graph.db --knowledge $D/knowledge > $D/l2.out && diff $D/r1.out $D/r2.out && diff $D/l1.out $D/l2.out && rm -rf $D`
  Both diffs are empty — the two reports are identical and the store listing (stances, peers, order) is unchanged between the runs.

## TC-004 — A fresh memory carries no stance until reflect (P1 · auto)

- **Story**: US1 · **Traces to**: FR-001
- **Given** a fixture store where a brand-new prose-only memory is recorded and reflect has not run since
- **When** the store's memory listing is queried for that memory
- **Then** the memory appears with its tier and score as usual and with no stance label (stance is unset by default)
- **Pass condition**: `D=/tmp/mso-tc004 && rm -rf $D && mkdir -p $D/ws && printf 'def mso_plain():\n    return 1\n' > $D/ws/app.py && git -C $D/ws init -q && git -C $D/ws add -A && git -C $D/ws -c user.email=q@q -c user.name=q commit -qm init && uv run --no-sync cairn build --workspace $D/ws --db $D/graph.db > /dev/null 2>&1 && uv run --no-sync cairn memory record pattern "mso-plain-probe" --body "temporary prose body recorded before any reflect pass" --db $D/graph.db --knowledge $D/knowledge > /dev/null && uv run --no-sync cairn memory search "mso-plain-probe" --db $D/graph.db --knowledge $D/knowledge | grep -q 'mso-plain-probe' && ! uv run --no-sync cairn memory search "mso-plain-probe" --db $D/graph.db --knowledge $D/knowledge | grep -q 'stance=' && rm -rf $D`
  Record and search both succeed, the result line for the fresh memory is present, and no line in its search results carries a stance label.

## TC-005 — Stance never overrides or rewrites lifecycle (P1 · auto)

- **Story**: US1 · **Traces to**: FR-001, FR-003
- **Given** a fixture store where a verified memory has been revised by a contradicting successor citing the same identifier, so reflect will contest it; its store record is captured before reflect
- **When** `cairn memory reflect` runs and the record is compared against the capture
- **Then** the memory keeps its promoted tier and score unchanged and its body text is untouched — only the contested stance and peer reference were added (a promoted memory can be contested without being demoted)
- **Pass condition**: `D=/tmp/mso-tc005 && rm -rf $D && mkdir -p $D/ws && printf 'def mso_login():\n    return 1\n' > $D/ws/app.py && git -C $D/ws init -q && git -C $D/ws add -A && git -C $D/ws -c user.email=q@q -c user.name=q commit -qm init && uv run --no-sync cairn build --workspace $D/ws --db $D/graph.db > /dev/null 2>&1 && B=$(printf '\140') && P1=$(uv run --no-sync cairn memory record decision "mso-promoted" --body "uses ${B}mso_login${B} for entry" --db $D/graph.db --knowledge $D/knowledge | grep -oE 'memory/[a-z]+/[a-z0-9-]+' | head -1) && uv run --no-sync cairn memory evolve "$P1" --body "contradicts: avoid ${B}mso_login${B} on entry" --db $D/graph.db --knowledge $D/knowledge > /dev/null && cp $D/knowledge/$P1.md $D/before.md && uv run --no-sync cairn memory reflect --db $D/graph.db --knowledge $D/knowledge | grep -q '1 contested.' && diff $D/before.md $D/knowledge/$P1.md > $D/filediff.out; grep -q '^> memory_stance: ' $D/filediff.out && ! grep -E '^[<>]' $D/filediff.out | grep -qvE '^> memory_stance(_peer)?: ' && uv run --no-sync cairn memory list --db $D/graph.db --knowledge $D/knowledge | grep -q 'tribal .*stance=contested peer=memory/tribal/mso-promoted' && rm -rf $D`
  The record diff must be non-empty with stance lines added, and every changed line must be a stance or peer line — any other difference (title, tier, score, body) fails the chain; the listing still shows the same tier and score on the contested line.

## TC-006 — Stance is visible where memories already surface (P1 · auto)

- **Story**: US2 AC1 · **Traces to**: FR-004
- **Given** the recall and explore surfaces that already render memories, at the tools level where the rendering is pinned by the standing suites
- **When** those rendering suites run against stores with contested and unstanced memories
- **Then** the contested memory renders with its stance inline and the contradicting peer reference; memories without a stance render exactly as they do today
- **Pass condition**: `uv run --no-sync pytest tests/test_memory_stance.py tests/test_explore_memory.py tests/test_memory_stale_flag.py -q`
  The three standing suites own the recall/explore render contracts at the tools level: stance after the refs fraction, the contested peer hint line, and unchanged rendering for unstanced entries.

## TC-007 — Standing guard: reflect changes nothing but stance (P1 · auto)

- **Story**: US1 · **Traces to**: FR-003, FR-006
- **Given** a fixture store seeded with memories containing a known word, fully reflected once, with its full memory listing captured
- **When** reflect runs again on the unchanged store
- **Then** the listing is byte-identical — no title, tier, score, or line order moved; a no-op reflect pass changes nothing at all (guard: if reflect ever starts mutating memory content or lifecycle, this fails)
- **Pass condition**: `D=/tmp/mso-tc007 && rm -rf $D && mkdir -p $D/ws && printf 'def mso_probe():\n    return 1\n' > $D/ws/app.py && git -C $D/ws init -q && git -C $D/ws add -A && git -C $D/ws -c user.email=q@q -c user.name=q commit -qm init && uv run --no-sync cairn build --workspace $D/ws --db $D/graph.db > /dev/null 2>&1 && B=$(printf '\140') && uv run --no-sync cairn memory record pattern "mso-probe-ref" --body "cites ${B}mso_probe${B}" --db $D/graph.db --knowledge $D/knowledge > /dev/null && uv run --no-sync cairn memory record pattern "mso-prose-probe" --body "prose-only body with the word mso_probe in it" --db $D/graph.db --knowledge $D/knowledge > /dev/null && uv run --no-sync cairn memory reflect --db $D/graph.db --knowledge $D/knowledge > /dev/null && uv run --no-sync cairn memory list --db $D/graph.db --knowledge $D/knowledge > $D/before.out && uv run --no-sync cairn memory reflect --db $D/graph.db --knowledge $D/knowledge > /dev/null && uv run --no-sync cairn memory list --db $D/graph.db --knowledge $D/knowledge > $D/after.out && diff $D/before.out $D/after.out && rm -rf $D`
  The diff is empty — the full listing is unchanged across a no-op reflect pass.

## TC-008 — Rotted citations downgrade to tentative (P2 · auto)

- **Story**: US3 AC1 · **Traces to**: FR-005
- **Given** a fixture workspace where a memory cites a code identifier that exists and a reflect pass has recorded that baseline; then the cited identifier is removed from the codebase (an uncommitted worktree change under the memory)
- **When** the workspace index is refreshed and `cairn memory reflect` runs
- **Then** the stale memory is marked tentative — no peer named, since this is staleness, not contradiction
- **Pass condition**: `D=/tmp/mso-tc008 && rm -rf $D && mkdir -p $D/ws && printf 'def mso_rot():\n    return 1\n' > $D/ws/app.py && git -C $D/ws init -q && git -C $D/ws add -A && git -C $D/ws -c user.email=q@q -c user.name=q commit -qm init && uv run --no-sync cairn build --workspace $D/ws --db $D/graph.db > /dev/null 2>&1 && B=$(printf '\140') && uv run --no-sync cairn memory record pattern "mso-rot-probe" --body "cites ${B}mso_rot${B} which exists today" --db $D/graph.db --knowledge $D/knowledge > /dev/null && uv run --no-sync cairn memory reflect --db $D/graph.db --knowledge $D/knowledge > /dev/null && uv run --no-sync cairn memory list --db $D/graph.db --knowledge $D/knowledge | grep -q 'stance=preferred] mso-rot-probe' && printf 'def mso_gone():\n    return 9\n' > $D/ws/app.py && uv run --no-sync cairn update --workspace $D/ws --db $D/graph.db > /dev/null 2>&1 && uv run --no-sync cairn memory reflect --db $D/graph.db --knowledge $D/knowledge > /dev/null && uv run --no-sync cairn memory list --db $D/graph.db --knowledge $D/knowledge | grep -E 'refs-verified=0\.0, stance=tentative\] mso-rot-probe' && rm -rf $D`
  The chain exits 0 only if the memory verified preferred before the rot and renders with a zero verified fraction and the tentative stance — no peer segment — after the index refresh and reflect.

## TC-009 — Reflect completes on the primary workspace store within 30s (P2 · auto)

- **Story**: US1 · **Traces to**: NFR-003
- **Given** this repository's own workspace store at its current size, byte-copied into a scratch dir so the timed run observes the real store without mutating it
- **When** `cairn memory reflect` runs against the copy
- **Then** it completes in under 30 seconds wall time
- **Pass condition**: `D=/tmp/mso-tc009 && rm -rf $D && mkdir -p $D && uv run --no-sync cairn config --json > $D/cfg.json && python3 -c 'import json,shutil,os; c=json.load(open("/tmp/mso-tc009/cfg.json")); shutil.copy2(c["db"],"/tmp/mso-tc009/db"); [shutil.copy2(c["db"]+s,"/tmp/mso-tc009/db"+s) for s in ("-wal","-shm") if os.path.exists(c["db"]+s)]; shutil.copytree(c["knowledge"],"/tmp/mso-tc009/knowledge")' && uv run --no-sync python -c 'import subprocess,time; t=time.monotonic(); r=subprocess.run(["uv","run","--no-sync","cairn","memory","reflect","--db","/tmp/mso-tc009/db","--knowledge","/tmp/mso-tc009/knowledge"],capture_output=True); d=time.monotonic()-t; assert r.returncode==0 and d<30, (r.stdout.decode(), d)' && rm -rf $D`
  The chain exits 0 only if the reflect subprocess exits 0 and finishes within the 30-second budget on the store at its current size.

## TC-010 — Interrupted reflect leaves memory records uncorrupted (P1 · auto)

- **Story**: US1 · **Traces to**: NFR-004
- **Given** a fixture store seeded with three dozen memories containing a known word
- **When** reflect is started and then interrupted (interrupt signal)
- **Then** the store is intact — every pre-run memory is still listed, none garbled, duplicated, or truncated — and a subsequent reflect completes normally
- **Pass condition**: `D=/tmp/mso-tc010 && rm -rf $D && mkdir -p $D/ws && printf 'def mso_i():\n    return 1\n' > $D/ws/app.py && git -C $D/ws init -q && git -C $D/ws add -A && git -C $D/ws -c user.email=q@q -c user.name=q commit -qm init && uv run --no-sync cairn build --workspace $D/ws --db $D/graph.db > /dev/null 2>&1 && for i in $(seq 1 36); do uv run --no-sync cairn memory record pattern "integrity-probe-$i" --body "prose body number $i" --db $D/graph.db --knowledge $D/knowledge > /dev/null; done && ( uv run --no-sync cairn memory reflect --db $D/graph.db --knowledge $D/knowledge & p=$!; sleep 1; kill -INT $p 2>/dev/null; wait $p; true ) && [ "$(uv run --no-sync cairn memory list --db $D/graph.db --knowledge $D/knowledge | grep -c 'integrity-probe-')" -eq 36 ] && uv run --no-sync cairn memory reflect --db $D/graph.db --knowledge $D/knowledge > /dev/null && rm -rf $D`
  After the interrupted run the listing still holds exactly the 36 seeded, well-formed memory lines, and a follow-up reflect exits 0.

## TC-011 — Reflect on an empty store is a clean no-op (P2 · auto)

- **Story**: US1 · **Traces to**: FR-002, FR-006
- **Given** a fixture workspace store with zero memories
- **When** `cairn memory reflect` runs
- **Then** it exits cleanly, reports zero memories evaluated, and assigns no stances — no error, no fabricated output
- **Pass condition**: `D=/tmp/mso-tc011 && rm -rf $D && mkdir -p $D/ws && printf 'def mso_e():\n    return 1\n' > $D/ws/app.py && git -C $D/ws init -q && git -C $D/ws add -A && git -C $D/ws -c user.email=q@q -c user.name=q commit -qm init && uv run --no-sync cairn build --workspace $D/ws --db $D/graph.db > /dev/null 2>&1 && uv run --no-sync cairn memory reflect --db $D/graph.db --knowledge $D/knowledge | grep -qx 'Reflected 0 memory(ies): 0 changed, 0 unchanged, 0 contested.' && rm -rf $D`
  The chain exits 0 only if the summary reports exactly zero memories evaluated, zero stances assigned.

## TC-012 — Mere co-citation is not contradiction (P2 · auto)

- **Story**: US1 · **Traces to**: FR-002
- **Given** two memories citing the same code identifier whose guidance is complementary (one records a decision, the other a related pattern) with no superseding relationship between them
- **When** `cairn memory reflect` runs
- **Then** neither memory is contested — contradiction requires disagreeing guidance, not merely sharing a citation
- **Pass condition**: `D=/tmp/mso-tc012 && rm -rf $D && mkdir -p $D/ws && printf 'def mso_shared():\n    return 1\n' > $D/ws/app.py && git -C $D/ws init -q && git -C $D/ws add -A && git -C $D/ws -c user.email=q@q -c user.name=q commit -qm init && uv run --no-sync cairn build --workspace $D/ws --db $D/graph.db > /dev/null 2>&1 && B=$(printf '\140') && uv run --no-sync cairn memory record decision "mso-comp-decision" --body "decision: the cache layer uses ${B}mso_shared${B} for lookups" --db $D/graph.db --knowledge $D/knowledge > /dev/null && uv run --no-sync cairn memory record pattern "mso-comp-pattern" --body "related pattern: invalidate ${B}mso_shared${B} entries on write" --db $D/graph.db --knowledge $D/knowledge > /dev/null && uv run --no-sync cairn memory reflect --db $D/graph.db --knowledge $D/knowledge | grep -q ' 0 contested.' && ! uv run --no-sync cairn memory list --db $D/graph.db --knowledge $D/knowledge | grep -q contested && rm -rf $D`
  The chain exits 0 only if the reflect summary reports zero contested and no listing line carries a contested stance — the complementary pair ends preferred or unset only.

## TC-013 — Reflect alongside a concurrent recorder (P2 · auto)

- **Story**: US1 · **Traces to**: NFR-004
- **Given** a fixture store where, while `cairn memory reflect` is running, another agent records a new memory (the concurrent-write scenario the spec names as a risk)
- **When** both operations finish
- **Then** the newly recorded memory exists intact and all pre-existing memories remain intact — no write is lost or garbled
- **Pass condition**: `D=/tmp/mso-tc013 && rm -rf $D && mkdir -p $D/ws && printf 'def mso_c():\n    return 1\n' > $D/ws/app.py && git -C $D/ws init -q && git -C $D/ws add -A && git -C $D/ws -c user.email=q@q -c user.name=q commit -qm init && uv run --no-sync cairn build --workspace $D/ws --db $D/graph.db > /dev/null 2>&1 && B=$(printf '\140') && uv run --no-sync cairn memory record pattern "mso-pre" --body "pre-existing memory citing ${B}mso_c${B}" --db $D/graph.db --knowledge $D/knowledge > /dev/null && ( uv run --no-sync cairn memory reflect --db $D/graph.db --knowledge $D/knowledge & r=$!; uv run --no-sync cairn memory record pattern "concurrent-probe" --body "written during a reflect pass" --db $D/graph.db --knowledge $D/knowledge > /dev/null; wait $r; true ) && uv run --no-sync cairn memory search "concurrent-probe" --db $D/graph.db --knowledge $D/knowledge | grep -q 'concurrent-probe' && [ "$(uv run --no-sync cairn memory list --db $D/graph.db --knowledge $D/knowledge | grep -c 'mso-pre')" -eq 1 ] && uv run --no-sync cairn memory reflect --db $D/graph.db --knowledge $D/knowledge > /dev/null && rm -rf $D`
  The chain exits 0 only if the record issued during the reflect run is findable afterwards with its title intact, the pre-existing memory is still listed exactly once, and a follow-up reflect completes cleanly.

## TC-014 — A declared stance renders and survives reflect without a verdict (P1 · auto)

- **Story**: US1 · **Traces to**: FR-002
- **Given** a fixture store where a prose-only memory is recorded with an explicitly declared stance (the record-time prior of the spec's dual stance authority) and nothing in the store can contradict it
- **When** the memory is listed, then `cairn memory reflect` runs with no contradicting evidence, and the memory is listed again
- **Then** the listing line renders the declared stance from the moment of recording, and the declaration survives reflect unchanged — reflect produced no evidence verdict, so the prior stands
- **Pass condition**: `D=/tmp/mso-tc014 && rm -rf $D && mkdir -p $D/ws && printf 'def mso_plain():\n    return 1\n' > $D/ws/app.py && git -C $D/ws init -q && git -C $D/ws add -A && git -C $D/ws -c user.email=q@q -c user.name=q commit -qm init && uv run --no-sync cairn build --workspace $D/ws --db $D/graph.db > /dev/null 2>&1 && uv run --no-sync cairn memory record pattern "mso-declared-probe" --body "prose-only body recorded with an explicit stance declaration before any reflect pass" --stance preferred --db $D/graph.db --knowledge $D/knowledge > /dev/null && uv run --no-sync cairn memory search "mso-declared-probe" --db $D/graph.db --knowledge $D/knowledge | grep -q 'stance=preferred] mso-declared-probe' && uv run --no-sync cairn memory reflect --db $D/graph.db --knowledge $D/knowledge > /dev/null && uv run --no-sync cairn memory search "mso-declared-probe" --db $D/graph.db --knowledge $D/knowledge | grep -q 'stance=preferred] mso-declared-probe' && rm -rf $D`
  The record succeeds, the search line renders the declared preferred stance, and it still renders after the reflect pass — the declaration survives when reflect produces no verdict. (The case where an evidence verdict overrides a declaration is TC-002's territory.)

## Coverage matrix

| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001 (optional stance, unset default, orthogonal to lifecycle) | TC-004, TC-005 | auto |
| FR-002 (dual stance authority: record-time declaration, reflect recomputes from evidence, verdict overrides on conflict) | TC-001, TC-002, TC-011, TC-012, TC-014 | auto |
| FR-003 (stance-only mutation; contested records peer) | TC-002, TC-005, TC-007 | auto |
| FR-004 (inline stance rendering in recall/explore) | TC-006 | auto |
| FR-005 (rotted citations downgrade to tentative) | TC-008 | auto |
| FR-006 (idempotent and deterministic) | TC-003, TC-011 | auto |
| NFR-003 (30s wall on primary workspace store) | TC-009 | auto |
| NFR-004 (interrupt-safe, uncorrupted files) | TC-010, TC-013 | auto |
| NFR-001 (security) | — | not applicable — spec records: local file metadata only, no new surface |
| NFR-002 (privacy) | — | not applicable — spec records: no new data leaves the store |
| NFR-005 (observability) | — | not applicable — spec records: the command's own summary suffices; that summary is the observation surface of TC-001/002/008 |
| NFR-006 (accessibility) | — | not applicable — spec records: no UI surface touched |

Acceptance-criterion coverage: US1 AC1 → TC-002 · US1 AC2 → TC-003 ·
US2 AC1 → TC-006 · US3 AC1 → TC-008. Boundary cases: empty store (TC-011),
concurrent access (TC-013), benign co-citation (TC-012), primary-store scale
(TC-009).

Priorities: P1 × 9 (TC-001–TC-007, TC-010, TC-014), P2 × 5 (TC-008,
TC-009, TC-011–TC-013).

## Notes for the suite owner

- **FR-002's stance authority is resolved as DUAL**: a stance may be
  declared at record time (a prior), reflect recomputes from evidence, the
  evidence verdict overrides a declaration on conflict, and the declaration
  survives when reflect produces no verdict. TC-014 covers the declared
  stance rendering and its survival; the verdict-overrides-declaration
  conflict case stays TC-002's territory.
- Auto stance assertions observe the command's own summary line (pinned to
  the exact reported shape in TC-001/TC-011) and the rendered search/list
  lines; the recall/explore tool-level render surfaces are owned by the
  standing pytest suites in TC-006's pass condition.
- The idempotency contract (FR-006) is a property; with no property library
  assumed, it is pinned by fixed double-run diffs (TC-003, TC-007) — no new
  dependency added for testability.
- TC-007 doubles as the standing regression guard for FR-003: any reflect
  change that starts mutating titles, tiers, scores, or line order fails it.
- Contradiction is exercised through the revise verb (`memory evolve`), the
  user surface that creates a superseding version citing the same verified
  identifier; the superseded predecessor is the memory reflect contests.
