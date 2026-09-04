# Tasks: tribal-memory-loop-closure

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.
**Before-audit**: pending — the orchestrator writes `passed @ <sha>` here

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1     | 2     | 0    |
| 2     | 2     | 0    |
| 3     | 8     | 0    |
| 4     | 4     | 0    |
| 5     | 3     | 0    |
| **Σ** | 19    | 0    |

## Phase 1: Explore memory integration (FR-001, FR-002, FR-003)
<!-- Checkpoint: explore()'s MCP response contains a `=== Tribal memory ===`
     section (populated or `(none)`), capped at 3 entries (title + "How to
     apply:" line only), and each rendered memory records a memory_refs row
     via a real per-session id. Verify: `grep -n "Tribal memory"
     src/cairn/mcp_server/tools_graph.py` (baseline 0 matches → expect ≥1)
     plus the new pytest passing. -->
- [ ] T001 [P] Add failing tests for explore's tribal-memory section and
  reference recording in a new/existing `tests/test_*explore*memory*.py` (or
  alongside `tools_graph.py`'s existing explore tests): assert the
  `=== Tribal memory ===` header appears (populated with title + "How to
  apply:" line, and `(none)` when no match, per TC-001/TC-002/TC-004), assert
  a `memory_refs` row is recorded only for the ≤3 rendered memories and not
  for unshown matches (TC-003/TC-005), and a concurrent-call case for
  TC-005's no-corruption boundary. Red before T002. (FR-001, FR-002, FR-003)
- [ ] T002 [P] Implement the tribal-memory section + reference recording in
  `src/cairn/mcp_server/tools_graph.py::explore` per tech-spec A1/D-001/D-002:
  add `_session_id()` to `src/cairn/mcp_server/_server_core.py` (beside
  `_bundle()`, `_server_core.py:222`) returning
  `f"mcp-{os.getpid()}-{date.today().isoformat()}"` and use it in both
  `explore` (new) and `recall_memory` (replacing the hardcoded `"mcp"`
  literal, `tools_memory.py:94`); inside `explore`'s existing `try:` block,
  after `result = queries.explore(conn, query)`, compute
  `seed_names = [s["name"] for s in result["seeds"] if s.get("name")][:5]`,
  call `search_memory(conn, _bundle(), " ".join(seed_names), tier="tribal",
  session_id=None)` (D-001 — no auto-recording), take `shown = mems[:3]`,
  call `record_references_batch(conn, [(c.concept_id, query) for c in
  shown], _session_id())` before `conn.close()`, guarding the ref write
  under `_read_only_mode()` (`_server_core.py:90`) to avoid a false
  contention signal (A1 pitfall); render `=== Tribal memory (N) ===` /
  `(none)` after `conn.close()` using
  `re.search(r"^How to apply:\s*(.+)$", body, re.M)` with a fallback to
  `c.description`; update `explore`'s tool docstring example section list.
  Turns T001 green. (FR-001, FR-002, FR-003)

## Phase 2: Scoring formula rebalance (FR-005)
<!-- Checkpoint: WEIGHTS drops critic_score/authority, remaining 5 terms
     renormalize to sum 1.0 (÷0.7, D-003); synthetic-signal test shows wider
     spread than today's two-cluster distribution. Verify:
     `python3 -m pytest tests/test_memory_lifecycle.py -k "weight or formula" -q` -->
- [ ] T003 [P] Add/update failing tests in `tests/test_memory_lifecycle.py`
  pinning the new `WEIGHTS` (no `critic_score`/`authority` keys; values
  `graph_verification=0.357`, `cross_session_refs=0.286`,
  `agent_confidence=0.214`, `freshness=0.0715`, `reinforcement=0.0715`, sum
  == 1.0 within 1e-9) and a synthetic-signals spread test (US3/AC2, TC-011)
  showing more than 2 distinct scores across varying `cross_session_refs`
  inputs; update the pinned literal-value tests
  `TestScoringWeights::test_freshness_weight_reduced` (`:185-186`) and
  `TestSevenSignalScore::test_compute_score_includes_reinforcement`
  (`expected` at `:277`, new value `0.607`, computed per tech-spec A3) in
  the same task. Red before T004. (FR-005)
- [ ] T004 [P] Implement the renormalized `WEIGHTS` and `compute_score` in
  `src/cairn/memory/scoring.py` per tech-spec A3/D-003/D-004: drop
  `WEIGHTS["critic_score"]`/`WEIGHTS["authority"]`, set the 5 surviving
  weights to the ÷0.7 values above, remove `compute_score`'s two dropped
  weighted terms while leaving `score_memory`/`apply_score` still computing
  and persisting `critic_score`/`authority` as unweighted diagnostics
  (D-004 — do **not** edit `promotion.py:436`/`:685`); do **not** delete
  `_authority()` (`scoring.py:226`, still used by
  `tests/test_import_validation.py:16,55-57`); rewrite `scoring.py`'s module
  docstring (lines 1-8, "7-signal" → "8 computed, 5 weighted"). Turns T003
  green. (FR-005)

## Phase 3: Hook lifecycle — session-start + auto-capture correctness (FR-004, FR-006, FR-007, FR-008, FR-009)
<!-- Checkpoint: post_tool_failure captures only on 2nd occurrence of a
     (tool_name, normalized_error) signature and is wired into
     _HOOK_ENTRYPOINTS/client configs; capture_memory force-routes
     session-bookkeeping-shaped titles/bodies to raw tier; session_end reads
     transcript_path, parses the JSONL, queues a memory-extract task on
     non-empty transcripts; session_start emits top score-ranked tribal
     memories once per session (or nothing when empty). Verify:
     `grep -n "post_tool_failure" src/cairn/agent_install/_common.py` (0→1);
     `grep -rn "transcript_path" src/cairn/` (0→≥1). Internal chain per
     plan.md (shared-file ownership on claude_hooks.py/_common.py, not data
     dependency): FR-006 → FR-007 → FR-009 → FR-004. FR-008 is
     line-disjoint in promotion.py from Phase 2's FR-005 writes — [P]. -->
- [ ] T005 Add failing tests for the recurrence gate (TC-012/TC-013/TC-014)
  and hook registration round-trip (TC-015): a `failure_signature()`
  normalizer test in a new `tests/test_memory_recurrence.py` (lowercase,
  digit-run collapse, path/hex/UUID stripping); a `note_failure_signature`
  test against a `tmp_path` DB asserting it returns the count *before* this
  occurrence; a `cairn memory record --recurrence-key <sig>` CLI test
  asserting first occurrence records nothing, second occurrence captures
  (explicit `--db`/`--knowledge` `tmp_path` overrides, per C-04); a
  `_HOOK_ENTRYPOINTS` test asserting `"post_tool_failure"` is present and
  written to a fixture client config, with `cairn.hooks.claude_hooks
  subprocess.Popen` patched at the call site (lazy import inside the test
  function, per survey § Test isolation conventions). Red before T006.
  (FR-006, FR-007)
- [ ] T006 (after T005) Implement the recurrence gate per tech-spec
  A4/D-005: append the additive-only
  `CREATE TABLE IF NOT EXISTS memory_failure_signatures (sig TEXT PRIMARY
  KEY, tool_name TEXT NOT NULL, occurrences INTEGER NOT NULL DEFAULT 1,
  first_seen TIMESTAMP NOT NULL, last_seen TIMESTAMP NOT NULL)` to
  `src/cairn/graph/schema.py` beside the `memory_refs` block
  (`schema.py:120-128`), no `MIGRATIONS` entry needed; add new
  `src/cairn/memory/recurrence.py` with `failure_signature(tool_name,
  error) -> str` (stdlib-only normalizer → sha256 truncated to 16 hex
  chars) and `note_failure_signature(conn, sig, tool_name) -> int`
  (SELECT-then-INSERT/UPDATE, returns prior count); add `--recurrence-key
  TEXT` to `cairn memory record` in `src/cairn/cli/memory.py`, calling
  `note_failure_signature` first and exiting quietly with no capture when
  the prior count is 0; in `src/cairn/hooks/claude_hooks.py::post_tool_failure`
  (`:106-171`), lazily import `failure_signature`, compute the signature
  from the already-privacy-filtered `safe_error` text, and append
  `--recurrence-key <sig>` to the existing `subprocess.Popen` argv; add
  `"post_tool_failure"` to `_HOOK_ENTRYPOINTS` (`_common.py:182`) and both
  its markers (legacy + current) to `_hook_markers()` (`_common.py:170`),
  and wire a `PostToolUse` entry in `claude_hooks_block()`
  (`clients/claude.py:46-60`). Turns T005 green. (FR-006, FR-007)
- [ ] T007 (after T006) Add failing tests for FR-009's session_end fix
  (TC-018/TC-019): a JSONL fixture at a `transcript_path`-shaped file mixing
  `user`/`assistant`/other record types (per tech-spec A6's observed shapes)
  asserting `session_end` queues a `memory-extract` task with the payload's
  real `session_id` (not the hardcoded literal), and an empty/missing
  `transcript_path` case asserting no task is queued and no error is
  raised; patch `cairn.hooks.claude_hooks.subprocess.Popen` at the call
  site with an explicit `tmp_path` db/knowledge override (C-04). Red before
  T008. (FR-009)
- [ ] T008 (after T007) Implement the `transcript_path` fix in
  `src/cairn/hooks/claude_hooks.py::session_end` (`:85-104`) per tech-spec
  A6/D-007: replace `data.get("messages", [])` with reading
  `data.get("transcript_path") or ""`; on missing/unreadable path, keep
  today's `"(no transcript; nothing to capture)"` early return; otherwise
  read the file line-by-line, `json.loads` each non-empty line inside a
  `try/except (json.JSONDecodeError, ValueError): continue`; keep records
  where `rec.get("type") in ("user", "assistant")` and `isinstance(rec.get("message"), dict)`;
  flatten each to `{"role", "content"}` (str content as-is, else join
  `block["text"]` for `type == "text"` blocks), dropping empty-text
  entries; keep the last ~80 messages; `json.dumps(...)` and pipe to the
  unchanged `["memory", "capture", "--session-transcript-stdin", "--session-id", …]`
  call, passing the payload's real `session_id` (fallback `"claude"`)
  instead of the hardcoded literal; wrap the whole read/parse in a broad
  guard so the hook never raises. Turns T007 green. (FR-009)
- [ ] T009 (after T008) Add failing tests for FR-004's session-start hook
  (TC-006/TC-007/TC-008): a populated-store case asserting `session_start()`
  emits `cairn memory digest --limit 5`'s output; an empty-store case
  asserting nothing is emitted when the CLI's output is empty or equals
  `"No tribal memories yet."`; a coupling test pinning that exact sentinel
  string against `cli/memory.py`'s `memory_digest` on an empty bundle; a
  `_HOOK_ENTRYPOINTS`/`_hook_markers()` test for `"session_start"`; a
  `claude_hooks_block()` test asserting a `SessionStart` block with no
  matcher is written. Red before T010. (FR-004)
- [ ] T010 (after T009) Implement `session_start()` in
  `src/cairn/hooks/claude_hooks.py` per tech-spec A2 (G1 resolved — event
  key is exactly `"SessionStart"`, no matcher required, stdout becomes
  plain-text context automatically): call `_run_cg(["memory", "digest",
  "--limit", "5"], timeout=15)` and write its result to stdout, emitting
  nothing when the output is empty or equals the sentinel `"No tribal
  memories yet."`; keep the module's "path-free"/no `cairn.*` import
  contract; add `"session_start"` to `_HOOK_ENTRYPOINTS`, both
  `cairn.hooks.claude_hooks session_start` and legacy
  `src.hooks.claude_hooks session_start` markers to `_hook_markers()`, and
  register the `"SessionStart"` event block (no matcher) in
  `claude_hooks_block()`; Cursor is explicitly out of scope for this FR
  (no session-start-shaped event exists in `cursor_hooks.py`/`cursor.py`
  per survey — do not add one). Turns T009 green. (FR-004)
- [ ] T011 [P] Add failing tests for FR-008's capture-time triage
  (TC-016/TC-017): the 4 real bookkeeping titles from survey.md
  (`T007 pins get_repo_head display seam for T008`, `...landed on
  feature/arch-review-improvements`, `polaris compass campaign: 240
  source-module compasses done, 157...`, `agent_runtime comment-trim house
  style extended repo-wide (2026-09-01, 4 commits)`) asserting
  `capture_memory` force-routes them to `raw` tier regardless of requested
  tier, plus the 5 durable-knowledge titles from survey.md (`never evict
  numpy...`, `kotlin grammar is the vendored...`, `registry bypass
  probe...`, `test seams bind fakes...`, `pip target dir shared...`)
  asserting they are **not** force-routed (negative fixtures are mandatory
  per plan.md § Risks) — using an explicit `tmp_path` db/knowledge
  override, never `~/.cairn`. Red before T012. (FR-008)
- [ ] T012 [P] Implement `_is_session_bookkeeping(title, body)` in
  `src/cairn/memory/promotion.py` per tech-spec A5/D-006: add the module-
  level regexes `_TASK_ID_RE`, `_BRANCH_RE`, `_DATED_COUNT_RE`,
  `_PROGRESS_COUNT_RE` exactly as specified in tech-spec A5 (first three
  run against title+body, `_PROGRESS_COUNT_RE` against title only); in
  `capture_memory` (`:20-96`), just before `store_memory`, override
  `if _is_session_bookkeeping(title, body): tier = "raw"` and set
  `concept.extensions["memory_triage"] = "session-bookkeeping"`; run the
  triage on the already-redacted (`strip_private_data`) text so it can
  never disagree with scoring; no signature change to `capture_memory`.
  Turns T011 green. (FR-008)

## Phase 4: Observability — L4 eval + doctor check (FR-010, FR-011)
<!-- Checkpoint: eval.py accepts "L4" in VALID_LEVELS with
     evaluate_l4_query/_retrieve_l4 reporting recall@k/MRR against a
     ground-truth situation→memory dataset; cairn doctor gains a check
     WARNing when a workspace has ≥1 tribal memory older than 30 days with
     zero memory_refs in that window. Verify: `grep -n "VALID_LEVELS"
     src/cairn/eval.py` shows "L4"; `grep -n "^def _check_"
     src/cairn/cli/system.py` shows the new check. -->
- [ ] T013 [P] Add failing tests for the L4 eval level (TC-021): a
  `tests/eval/memory/ground_truth/queries.jsonl` + `expectations.tsv` pair
  (D-008 shape, `symbol_id` written as `memory/tribal#<slug>` so
  `parse_symbol_id` validates) seeded against a `tmp_path` bundle, and a
  test asserting running the evaluation at `"L4"` reports numeric recall@k
  and MRR without routing through `_retrieve_l5` and without raising
  `KeyError` from the stats dict. Red before T014. (FR-010)
- [ ] T014 [P] Implement the L4 eval level in `src/cairn/eval.py` per
  tech-spec A7/D-008: `VALID_LEVELS = frozenset({"L1", "L4", "L5"})`; add
  `_retrieve_l4(conn, bundle_root, query, k)` mirroring `_retrieve_l5`'s
  normalization via `search_memory(conn, OKFBundle(bundle_root), query,
  tier="tribal", session_id=None)[:k]` mapped to
  `{"name": c.concept_id, "file_path": ""}` (session_id=None is
  load-bearing — an eval sweep must not inflate `cross_session_refs`); add
  `evaluate_l4_query(conn, bundle_root, query, expect, k)` mirroring
  `evaluate_l5_query`'s substring-rank shape; add an explicit
  `elif graded.level == "L4"` branch to `evaluate_graded_query` (today
  `if L1 … else <L5>`); add an `"L4"` bucket to `_run_graded_evaluation`'s
  and `run_evaluation`'s `stats` dicts; in `src/cairn/cli/system.py`, widen
  `eval_cmd`'s `--corpus` `Choice(["L1", "L5"])` to include `"L4"` and the
  `["L1", "L5"]` render loop to include `"L4"`. Turns T013 green.
  (FR-010)
- [ ] T015 [P] Add failing tests for the doctor memory-staleness check
  (TC-022/TC-023): a `tmp_path` workspace fixture with ≥1 tribal memory
  file mtime-aged past 30 days and 0 `memory_refs` rows in that window
  asserting `cairn doctor` reports WARN with a detail mentioning
  write-only memory; a fixture where all tribal memories have a recent
  `memory_refs` row asserting PASS (no WARN); update the two exact ordered
  result-name lists in `tests/test_doctor.py` (`expected` at `:125`, the
  inline list at `:917`) to include the new check name in the same task.
  Red before T016. (FR-011)
- [ ] T016 (after T014) Implement `_check_memory_staleness(conn, db)` in
  `src/cairn/cli/system.py` per tech-spec A8/D-009 (chained after T014 only
  for file ownership — T014 edits `eval_cmd`'s `--corpus`/render loop, this
  task adds a wholly new `_check_memory_staleness`-shaped function; no data dependency, own
  the file sequentially to avoid a merge collision): add
  `MEMORY_REF_WINDOW_DAYS = 30` beside `STALE_BUILD_DAYS` (`:650-654`);
  resolve the bundle as `Path(db).parent / ".knowledge"`; count tribal
  memories older than the window by file mtime over
  `<knowledge>/memory/tribal/*.md` (no YAML parse); count refs via
  `SELECT COUNT(*) FROM memory_refs WHERE referenced_at >= ?` against an
  ISO-8601 cutoff; verdicts: no tribal dir/no old memories → PASS; old
  memories and 0 refs in window → WARN ("N tribal memories older than 30d,
  0 references recorded in that window — memory is write-only", hint
  pointing at `explore`/`recall_memory`); otherwise PASS with the ref
  count; add the check to `_run_doctor`'s list after
  `_check_tool_health(conn)` and before `_check_config()`, and a matching
  `_result("memory_staleness", _WARN, "database unavailable (see schema)")`
  row to `_db_unavailable_results`; the check must never raise (wrap the
  filesystem walk); update the "10 health checks" banner (`:632-637`),
  `doctor`'s docstring, and `docs/cli-reference.md:92`'s "10 checks" line
  to reflect 11. Turns T015 green. (FR-011)

## Phase 5: MCP surface trim + docs (FR-012, FR-013)
<!-- Checkpoint (tech-spec D-010 supersedes plan.md's original
     "still defined but undecorated" wording — the six functions are
     DELETED outright, not merely un-decorated, since tools_memory.py is
     imported for registration side effects only): a failing test
     enumerating registered @mcp.tool names and asserting the 6 names are
     absent, before removing the six functions outright; D-012 adds
     `--db` to `cairn memory demote` in the same phase so the removal is
     truly behavior-preserving (without it, memory_embeddings rows orphan
     on every demote). Verify: `grep -n "memory_promote\|memory_demote\|
     memory_evolve\|memory_decay\|memory_delete\|memory_digest"
     src/cairn/mcp_server/tools_memory.py` shows 0 matches; `grep -n
     "28 tools\|22 tools" README.md docs/mcp-tools.md` shows 22, not 28. -->
- [ ] T017 Add failing tests for the MCP tool trim + D-012's demote fix
  (TC-024/TC-025): update `tests/test_status_resource_health.py:281`
  (`_EXPECTED_TOOL_COUNT == 28` → `22`); rename/retarget
  `tests/test_mcp_wiki_tool.py::test_wiki_generate_is_the_28th_registered_tool`
  (`:68`, `== 28` at `:74`); update `tests/test_agent_surface.py`'s
  `assert len(registered) == 28` (`:455`-ish → 22), its `_TOOL_MODULES` map
  (`:532-538`, drop the six names), its hint-invariant case naming
  `memory_digest` (`:740`, update `recall_memory`'s no-results string
  reference), and `test_tool_count_string_matches_server` (`:392-430`,
  requires `agent_install/_common.py:440`'s blurb updated in lockstep —
  land that doc edit in this same task per tech-spec A9's "must land in
  the same task" note); update `tests/test_mcp_connection_leaks.py:15-16`'s
  docstring inventory; add a test that `cairn memory demote` accepts a
  `--db` option and threads a conn into `demote_memory` (D-012) so
  `memory_embeddings` stays coherent after MCP removal; assert the six CLI
  verbs (`evolve`, `digest`, `promote`, `decay`, `demote`, `forget`) still
  work. Red before T018. (FR-012)
- [ ] T018 (after T017) Implement the MCP tool trim + D-012's demote fix
  per tech-spec A9/D-010/D-011/D-012, landing the three test-enforced doc
  files in this same task per tech-spec's explicit instruction: delete
  `memory_digest` (`:22`), `memory_evolve` (`:218`), `memory_promote`
  (`:254`), `memory_demote` (`:281`), `memory_delete` (`:315`),
  `memory_decay` (`:360`) — functions and `@mcp.tool` decorators, outright
  — from `src/cairn/mcp_server/tools_memory.py`; set
  `_EXPECTED_TOOL_COUNT = 22` in `src/cairn/mcp_server/server.py:56`;
  update `tools_memory.py`'s module docstring (`:1-2`); fix
  `recall_memory`'s no-results string that currently steers agents to
  `memory_digest()` (`tools_memory.py:106-108`); add `--db` (default
  `DEFAULT_DB_PATH`) to `cairn memory demote` in `src/cairn/cli/memory.py`
  and thread the opened conn into `demote_memory(..., conn=conn)` (D-012);
  update the three test-enforced doc files in the same commit:
  `src/cairn/agent_install/_common.py:440` ("28 tools across 4 layers:
  graph (9), knowledge base + compass (5), memory (8), knowledge (6)" →
  "22 … memory (2) …"), `src/cairn/agent_integration/skill/SKILL.md:35`
  (memory tool list → `recall_memory, record_memory` only), and
  `src/cairn/agent_integration/skill/references/tools.md:24,27-31` (remove
  the six bullets). Turns T017 green. (FR-012)
- [ ] T019 [P] Update the remaining 8 doc/prose files to the 22-tool count
  per tech-spec A9's full table (landing right after T018 — no test
  enforces these, but they complete FR-013's doc-count claim):
  `README.md:22,199,292` ("28 tools" → "22 tools" in all three spots,
  and re-verify the "recalled alongside graph results" claim per US5/
  TC-020 now that Phase 1 has landed — narrow the claim if any named tool
  still doesn't do it); `docs/mcp-tools.md:3,13,21` ("28 tools" → "22
  tools") and its per-tool table rows `:50-55` (keep `record_memory`/
  `recall_memory`, remove the four rows for the six deleted tools, and
  change the section header "L4 — Memory (`tools_memory.py`, 8)" to `2`);
  `AGENTS.md:9` ("28 tools across 4 layers ... memory (8)" → 22/2);
  `src/cairn/mcp_server/server.py:3` ("Implements 28 tools" → 22);
  `src/cairn/mcp_server/__init__.py:7` ("The 28 tools live in split
  modules" → 22); `docs/architecture.md:29` ("exactly 28 tools (verified
  at boot)" → 22); the generated diagram assets
  (`docs/diagrams/system-architecture.html:128`, `.svg:75`,
  `system-architecture-dark.html:82`, `readme-architecture.html:75`,
  `.svg:66`, `readme-architecture-dark.html:73`) — update the "28" string
  in each, or record the deferral explicitly if they are build-generated
  and regenerated by a separate process; every doc names the delete verb
  as `cairn memory forget`, never `cairn memory delete` (D-011). Do not
  edit `src/cairn_intel.egg-info/PKG-INFO` (build output). (FR-013)

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `- [x]` done + proof note:
      done <date> — <test/command that proves it>
- Dropped: `- [ ] ~~T004~~ dropped <date> (D-###)` — never delete the line;
  dropped tasks stay visible with the decision that killed them
- `[P]` = parallelizable (default — no shared files, no upstream task);
  chained tasks note `(after T###)` and name the exact interface they
  consume from their upstream — symbols, signatures, file formats; serial
  runs need a reason, parallel runs need none
- Fix rounds append `(fix <n>/5)` to the entry — the cap survives resume
  only if the count lives here, in the status holder
- Every task cites its FR-###; tasks with no FR are scope creep — fix the
  spec first
