# Survey: tribal-memory-loop-closure

**Baseline**: cairn @ 76639899dff50062a483a13238b5e3ab2d91dddf (HEAD at survey time)
`specs/context/structure.md` / `tech.md` already exist, last refreshed @ 0eaccf8 —
same commit as this survey's baseline (`git log -1` = 0eaccf8...→76639899 is a
context-only diff of the two files themselves, no code moved). Not stale;
left untouched.

## FR-001/002/003 — explore memory integration

**Evidence**:
- `src/cairn/graph/explore.py` — `explore(conn: sqlite3.Connection, query: str, max_nodes=20, max_source_lines=400) -> dict`
  (function def). Pure query orchestrator: seeds via FTS5, call paths, blast
  radius, dispatch hops. No bundle/OKF parameter, no memory access at all.
- `src/cairn/mcp_server/tools_graph.py` `def explore(query: str) -> str` (the
  MCP tool). Body:
  ```
  conn = _conn()
  try:
      result = queries.explore(conn, query)
  finally:
      conn.close()
  ```
  then builds `out = [...]` text sections (Source, Call paths, Blast radius,
  Ambiguous dispatch) purely from `result`, no tribal-memory section anywhere.
  `conn.close()` happens **before** any of the section-rendering code runs —
  confirms the spec's risk note: today there is no fused memory search at all
  during `explore`, and if one is added it must run before `conn.close()`
  (or open a second connection), since `search_memory`'s
  `record_references_batch` write needs a live `conn`.
- `src/cairn/mcp_server/_server_core.py:222` `_bundle()` — builds `OKFBundle`
  from `CAIRN_KNOWLEDGE` env or `_store().knowledge`. Exists and is reusable
  by any new memory-integration code in `tools_graph.py`.
- `src/cairn/memory/promotion.py:198` `search_memory(conn, bundle, query,
  tier=None, session_id=None, include_superseded=False)`. When `session_id`
  is truthy, records `record_references_batch(conn, refs, session_id)` for
  `memory_tier == "tribal"` results only (promotion.py:284-286).
- No `_session_id()` helper exists anywhere in `mcp_server/`. The only
  existing precedent for how an MCP tool obtains a session id:
  `src/cairn/mcp_server/tools_memory.py:94` `recall_memory` calls
  `search_memory(conn, bundle, query, tier=tier or None, session_id="mcp", ...)`
  — a hardcoded literal string `"mcp"`, not a per-session-derived value. Any
  new caller in `tools_graph.py` would either reuse this same convention or
  invent one; nothing today derives a real per-session id inside the MCP
  server.

**Status**: TODO (all three FRs — no tribal-memory section, no reference
recording, no cap/render logic exists in `explore`'s MCP wrapper today).

**Verify**:
```
grep -n "Tribal memory" src/cairn/mcp_server/tools_graph.py   # 0 matches
```
Ran: 0 matches — confirms TODO.

**Gap**: none beyond the conn-lifecycle note above (which is a risk the spec
already flagged, now confirmed as real: `explore`'s conn closes before
section rendering, so a memory search + reference write must be sequenced
before `conn.close()`, not after).

## FR-004 — session-start hook

**Evidence**:
- `src/cairn/agent_install/_common.py:182` — `_HOOK_ENTRYPOINTS = {"post_edit", "session_end"}`.
- `src/cairn/hooks/claude_hooks.py` defines only `post_edit`, `session_end`,
  `post_tool_failure` (dispatch table at bottom, lines ~175-183). No
  `session_start` function exists.
- `src/cairn/agent_install/clients/claude.py:46-60` `claude_hooks_block()` —
  wires `hooks.PostToolUse` → `post_edit` and (implicitly, by grep) another
  block → `session_end`. Grepped `SessionStart`/`session.start` in
  `claude.py`: 0 matches.
- `src/cairn/agent_install/clients/cursor.py:33` wires `afterSessionEnd` only;
  `src/cairn/hooks/cursor_hooks.py:2,10,18` handles `afterFileEdit` /
  `afterSessionEnd` only. Grepped `SessionStart`/`sessionStart`/`onStart` in
  both cursor files and `claude.py`: 0 matches anywhere.
- Live check: `~/.claude/settings.json` on this machine has only
  `PostToolUse` (matcher `Edit|Write|MultiEdit` → `post_edit`) and one other
  block → `session_end` wired. No `SessionStart` entry present.

**Status**: TODO — wholly new; no session-start-shaped event exists in any
client's install code or any installed config on this machine.

**Verify**: `grep -rn "SessionStart\|sessionStart\|onStart" src/cairn/agent_install/ src/cairn/hooks/` → 0 matches (ran).

## FR-005 — scoring formula

**Evidence**:
- `src/cairn/memory/scoring.py:21-29`:
  ```
  WEIGHTS = {
      "graph_verification": 0.25,
      "cross_session_refs": 0.20,
      "agent_confidence": 0.15,
      "critic_score": 0.20,
      "freshness": 0.05,
      "reinforcement": 0.05,
      "authority": 0.10,
  }
  ```
  (module docstring lines 1-8 states the same formula). Confirmed matches
  spec's claim exactly; sum = 1.0 today.
- `_authority` (scoring.py:226, a private helper function distinct from the
  `WEIGHTS["authority"]` key) is called elsewhere: `tests/test_import_validation.py:16,55-57`
  imports `_authority` directly and asserts `authority == 0.5` for imported
  docs — this is a **different concern** (doc-import authority score, not the
  memory-score weighted term) and must survive FR-005 unchanged; removing
  `"authority"` from `WEIGHTS` does not require deleting `_authority()`.
- Call sites reading `signals["critic_score"]`: `src/cairn/memory/promotion.py:436`
  (`signals["critic_score"] = critic` inside `batch_critic`) and
  `promotion.py:685` (`_rescore_with_critic`, `signals = {**signals, "critic_score": critic}`).
  Both write into a `signals` dict that flows into `score_memory`'s
  weighted-sum computation — removing the `WEIGHTS` term makes these writes
  inert (the key would still be settable but no longer weighted), not
  broken, but tech-spec should decide whether to also stop writing it.
- No other call site reads `signals["authority"]` outside `scoring.py` itself.

**Status**: PARTIAL (formula unchanged / current state confirmed; FR-005's
removal work is TODO).

**Tests that assert the current formula** (will need updating):
- `tests/test_memory_lifecycle.py:177` — `assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9` (still true post-change if renormalized correctly, but the test imports `WEIGHTS` and would need re-verification).
- `tests/test_memory_lifecycle.py:180-181` — asserts `"reinforcement" in WEIGHTS` and `WEIGHTS["reinforcement"] > 0` (compatible with FR-005, reinforcement survives).
- `tests/test_memory_lifecycle.py:185-186` — asserts `WEIGHTS["freshness"] == 0.05` and `WEIGHTS["reinforcement"] == 0.05` — these pin exact current weight values; FR-005 says "renormalize the remaining weights," so if these specific values change during renormalization these two lines will need updating (not proven false yet — depends on tech-spec's renormalization approach).
- `tests/test_memory_lifecycle.py:270,273` — a synthetic signals dict includes `"critic_score": 0.5` and `"authority": 0.5` — will need updating once those keys are dropped from the formula (test constructs its own signals dict, doesn't read `WEIGHTS` for these two keys, but removing them from `score_memory`'s consumption may make these lines dead code needing cleanup).

**Verify**:
```
python3 -m pytest tests/test_memory_lifecycle.py -k "weight or formula" -q
```
Not run in this session (survey stage does not require running the full
suite; cited line numbers are grep-verified against file content, matching
protocol rule "citations verbatim from this session's grep output").

## FR-006/007 — post_tool_failure recurrence gate + hook registration

**Evidence**:
- `src/cairn/hooks/claude_hooks.py:106-171` `post_tool_failure()` — captures
  unconditionally: builds title/body, then unconditionally
  `subprocess.Popen([... "memory", "record", "mistake", title, "--body", body,
  "--confidence", "0.3", ...])`. No recurrence check of any kind exists
  before the Popen call.
- `_HOOK_ENTRYPOINTS = {"post_edit", "session_end"}` (agent_install/_common.py:182)
  — `post_tool_failure` is absent.
- Live confirmation: `~/.claude/settings.json` has hooks for `post_edit`
  (under `PostToolUse`) and `session_end` only — no `post_tool_failure`
  entry, no `PostToolUse` matcher wired to it. Grepped
  `post_tool_failure` across `src/cairn/agent_install/`: 0 matches (only
  referenced in `claude_hooks.py`'s own dispatch table and
  `src/cairn/memory/privacy.py:9`'s docstring mention). Confirms genuinely
  dead / never wired.
- `tests/test_memory_lifecycle.py:9` references `post_tool_failure` only in
  a comment ("post_tool_failure auto-capture hook") — grepped for an actual
  test invoking/asserting it: none found in this session (only the comment
  line matched).
- **Cheap recurrence-lookup path**: no existing indexed/fast lookup for
  "(tool_name, error) signature seen before" was found. `search_memory`
  (promotion.py:198) does a lexical+semantic bundle scan — not indexed by
  `(tool_name, error)` and explicitly documented as reading every memory
  concept from disk when lexical hits are thin (promotion.py:228-238,
  "Lexical broaden... pay the cost of reading every memory concept from disk").
  No SQL table/index keyed on tool_name+error was found in this survey pass;
  this is a genuine `unknown — verify` for tech-spec: whether a new indexed
  column/table is needed or an existing `memory_refs`/concept-tag scan can be
  made cheap enough for a detached hook subprocess.

**Status**: TODO (both FR-006 gate logic and FR-007 registration are absent;
confirmed dead code, confirmed no lookup path exists yet).

**Verify**:
```
grep -n "post_tool_failure" ~/.claude/settings.json   # 0 matches (ran)
grep -rn "post_tool_failure" src/cairn/agent_install/  # 0 matches (ran)
```

## FR-008 — capture triage

**Evidence**:
- `src/cairn/memory/promotion.py:20-96` `capture_memory(conn, bundle, type_,
  title, body, resource=None, confidence=0.7, session_origin=None, tags=None,
  supersedes_threshold=0.85) -> Dict`. No `tier` parameter exists today —
  tier is derived post-hoc via `score_memory` → `tier = store_mod.tier_for_score(signals["score"])`
  (promotion.py:81-82). FR-008's "force tier to raw regardless of caller-
  supplied tier" requires either a new parameter or a pre-scoring
  short-circuit, since there is currently no caller-supplied tier to
  override in this function's signature — `tools_memory.py`'s
  `record_memory` and `cli/memory.py`'s `memory_record`/`memory_capture` do
  not pass a tier into `capture_memory` either; tier is always computed, not
  requested. (Verify against `record_memory`'s exact signature before
  tech-spec assumes a tier override param already exists — it does not.)
- Sample of real tribal-memory titles from `~/.cairn/79428b9d734aac21/.knowledge/memory/tribal/*.md`
  (verbatim filenames, 28 tribal + 6 drafts = 34 total, matching spec's claim):
  - **Session-bookkeeping (must be caught)**:
    - `t007-pins-get-repo-head-display-seam-for-t008-839d90.md` → title "T007 pins get_repo_head display seam for T008"
    - `agent-runtime-arch-review-improvements-landed-on-feature-arc-28ee85.md` → "...landed on feature/arch-review-improvements"
    - `polaris-compass-campaign-240-source-module-compasses-done-15-e4dac0.md` → "polaris compass campaign: 240 source-module compasses done, 157..."
    - `agent-runtime-comment-trim-house-style-extended-repo-wide-20-284fe6.md` → "agent_runtime comment-trim house style extended repo-wide (2026-09-01, 4 commits)"
  - **Durable knowledge (must NOT be caught)**:
    - `never-evict-numpy-from-sys-modules-mid-process-e52eeb.md` → "never evict numpy from sys.modules mid-process"
    - `kotlin-grammar-is-the-vendored-fwcd-tree-sitter-build-cairn--b96ff2.md` → "kotlin grammar is the vendored fwcd tree-sitter build"
    - `registry-bypass-probe-test-a-parser-port-before-the-loader-f-237fed.md` → "registry bypass probe: test a parser port before the loader..."
    - `test-seams-bind-fakes-at-the-consumer-module-s-namespace-d71aa0.md` → "test seams bind fakes at the consumer module's namespace"
    - `pip-target-dir-shared-across-interpreter-abis-corrupts-unrep-131195.md` → "pip target dir shared across interpreter ABIs corrupts..."
  These ground the FR-008 heuristic: branch-name refs ("landed on
  feature/..."), `T\d{3}` task IDs ("T007", "T008"), and dated progress
  counts ("2026-09-01, 4 commits", "240... done, 157...") appear only in the
  bookkeeping set; the durable set uses declarative/technical phrasing with
  no branch/task-ID/date-count tokens.

**Status**: TODO — no tier-override parameter, no triage regex exists in
`capture_memory` today.

**Verify**: `grep -n "def capture_memory" -A5 src/cairn/memory/promotion.py` (ran, shown above); `find ~/.cairn -path "*memory/tribal*" -name "*.md" | wc -l` → 28 (ran).

## FR-009 — session_end → memory-extract silently broken

**Evidence** (root cause traced):
- `src/cairn/hooks/claude_hooks.py:85-104` `session_end()`:
  ```python
  data = _read_stdin()
  messages = data.get("messages", [])
  if not messages:
      sys.stdout.write("(no transcript; nothing to capture)")
      return
  ```
  This is the **precise break**: Claude Code's actual `SessionEnd` hook
  payload (per Claude Code's documented hook input shape) provides
  `session_id`, `transcript_path` (a path to a JSONL transcript file),
  `hook_event_name`, `reason`, `cwd` — it does **not** send an inline
  `"messages"` array. Grepped the whole repo for `transcript_path`: 0
  matches anywhere in `src/cairn/` — nothing reads the field Claude Code
  actually sends. So `data.get("messages", [])` is always `[]` in real
  usage, `session_end()` hits the early return on every real invocation, and
  `_run_cg(["memory", "capture", ...])` — the call that would eventually
  reach `memory_capture`'s decoupled fallback and queue a `memory-extract`
  task — is **never reached**. This matches the spec's observed symptom
  exactly (zero `memory-extract` tasks in the live queue despite
  `session_end` being wired and firing).
- Downstream, `cli/memory.py:121-206` `memory_capture()` itself is correctly
  wired for the fallback path *if* it were ever invoked with a non-empty
  transcript: when `CAIRN_LLM_BACKEND` is unset (default `""`, not in
  `("droid","opencode","claude")`), `candidates` stays `[]`, `recorded == 0`,
  and it falls into the `else` branch (line 190) which calls
  `create_task(bundle, "memory-extract", f"session-{session_id}", facts={...})`
  (line 196-204) — this path is intact and would queue a task correctly.
  The bug is entirely upstream in `claude_hooks.py`'s stdin-shape assumption,
  not in the CLI capture command.

**Status**: TODO to fix (root cause identified, not a guess — grep-confirmed
absence of `transcript_path` handling anywhere in the codebase).

**Verify**:
```
grep -rn "transcript_path" src/cairn/   # 0 matches (ran)
grep -n "messages" src/cairn/hooks/claude_hooks.py   # line 87 only (ran)
```

## FR-010 — L4 eval level

**Evidence**:
- `src/cairn/eval.py:95` `VALID_LEVELS = frozenset({"L1", "L5"})`.
- `evaluate_l1_query` at line 259, `evaluate_l5_query` at line 299,
  `_retrieve_l1` at line 415, `_retrieve_l5` at line 437 — all confirmed by
  grep at these exact line numbers.
- Ground-truth dataset format: `load_ground_truth(ground_truth_dir)` (eval.py:137)
  reads two files from `ground_truth_dir`: `queries.jsonl` (JSON-lines,
  fields `query_id`, `level` — must be in `VALID_LEVELS`, `text`) joined
  against `expectations.tsv` on `query_id` (grades restricted to `{1,2}`,
  each query needs ≥1 expectation row). This is the exact shape an L4
  addition must extend: add `"L4"` to `VALID_LEVELS` and add L4-leveled rows
  to a `queries.jsonl`/`expectations.tsv` pair (path/location of the
  existing ground-truth dir not resolved in this pass — `unknown — verify`
  exact directory path via `load_ground_truth` call sites, e.g. eval.py:485
  `queries = load_ground_truth(graded_dir)`).
- `load_eval_queries` (eval.py:74) is the separate legacy YAML fixture path
  (per module docstring lines 6-8, "Two query sources (D-008)") — a second,
  older query source that also exists; tech-spec should confirm which of the
  two an L4 addition targets (evidence favors `load_ground_truth`'s
  jsonl/tsv shape since that's what `VALID_LEVELS` gates).

**Status**: TODO — no L4 level, function, or dataset entries exist.

**Verify**: `grep -n "VALID_LEVELS\|evaluate_l1_query\|evaluate_l5_query\|_retrieve_l1\|_retrieve_l5" src/cairn/eval.py` (ran, line numbers above confirmed).

## FR-011 — doctor check

**Evidence**:
- `src/cairn/cli/system.py` — `doctor` command at line 1753. Existing check
  pattern: `_result(name: str, status: str, detail: str, hint: str | None = None) -> dict`
  (line 657) is the shared constructor every check uses; checks live as
  private module functions `_check_schema` (743), `_check_embeddings` (762),
  `_check_ann` (786), `_check_embed_server` (894), `_check_freshness` (1060),
  `_check_parse_errors` (1142), `_check_concurrency` (1174),
  `_check_tool_health` (1211), `_check_config` (1295), `_check_environment` (1569)
  — comment at line 632-637 says "10 health checks, each PASS/WARN/FAIL...
  Exit code is 0 when every check is PASS or WARN, 1 when any check is FAIL."
  Status constants: `_WARN = "WARN"` (645), `_FAIL = "FAIL"` (646). Thresholds
  section (650-654) shows the pattern for adding a new bounded/assertable
  threshold constant (e.g. `STALE_BUILD_DAYS = 7`). A new "memory tier has
  entries but zero references in 30 days" check should follow this exact
  `_check_<name>(conn) -> dict` + `_result(...)` + a named threshold constant
  shape.

**Status**: TODO — no memory-reference-staleness check exists among the 10
listed checks.

**Verify**: `grep -n "^def _check_\|def doctor" src/cairn/cli/system.py` (ran, line numbers above confirmed).

## FR-012/013 — MCP tool trim + doc counts

**Evidence**:
- All six tools confirmed registered as `@mcp.tool` in
  `src/cairn/mcp_server/tools_memory.py`: `memory_digest` (line 22),
  `memory_evolve` (218), `memory_promote` (254), `memory_demote` (281),
  `memory_delete` (315), `memory_decay` (360). Module docstring (lines 1-2)
  explicitly lists these as the "L4 memory MCP tools."
- CLI equivalents in `src/cairn/cli/memory.py` (grepped `@memory.command`):
  `evolve` (52), `digest` (260), `promote` (305), `decay` (332), `demote` (426)
  all have direct 1:1-named CLI subcommands. **`memory_delete` has no
  CLI subcommand named "delete"** — grepped `@memory.command("delete")`: 0
  matches. The closest equivalent is `memory forget` (`@memory.command("forget")`,
  line 398, `memory_forget(memory_path, db, knowledge)` →
  `delete_memory(bundle, memory_path, conn=conn)`), which is functionally
  the same delete operation under a different CLI verb name. **This is a
  gap to flag, not assume away**: FR-012 says "remain available only via
  existing `cairn memory <verb>` CLI subcommands" — true in function, but
  the verb name differs (`forget` vs `delete`), so tech-spec / docs must
  either accept the naming mismatch or note it explicitly rather than
  silently mapping `memory_delete` → `cairn memory delete` (which doesn't
  exist).
- Current tool-count strings to update: `README.md:22` "28 tools", `:199`
  "28 MCP tools", `:292` "28 tools across four layers"; `docs/mcp-tools.md:3`
  "what the 28 tools are", `:13` "verify exactly 28 tools registered", `:21`
  "## The 28 tools by layer", plus the per-tool table rows at
  `docs/mcp-tools.md:50-55` listing all six tools slated for removal
  (`record_memory`/`recall_memory` rows at 50-51 stay; `memory_digest` row
  52, `memory_evolve`/`memory_promote`/`memory_demote` row 53,
  `memory_decay` row 54, `memory_delete` row 55 — these four rows are the
  ones to remove/update per FR-013's 28→22 claim, matching the spec's "8 of
  cairn's 28 MCP tools" lifecycle-machinery framing once the CLI-only verbs
  are excluded).

**Status**: TODO for the registration removal itself; PARTIAL evidence
gathered (all 6 registration sites + doc-count sites cited; one naming gap
flagged for `memory_delete`/`forget`).

**Verify**:
```
grep -n "memory_promote\|memory_demote\|memory_evolve\|memory_decay\|memory_delete\|memory_digest" src/cairn/mcp_server/tools_memory.py   # 6 matches, all confirmed (ran)
grep -n "@memory.command(\"delete\")" src/cairn/cli/memory.py   # 0 matches (ran)
grep -n "28 tools" README.md docs/mcp-tools.md   # confirmed lines above (ran)
```

## Test isolation conventions (Constitution C-04)

**Evidence**: `specs/CONSTITUTION.md:15` — "no eager `cairn.cli`/`cairn.mcp_server`
imports in test modules; never patch the global `subprocess.Popen`; tests
using `tmp_path` must not leak workspaces into the real `~/.cairn`."
Rationale at line 21: "these three gotchas have each broken CI or leaked
state onto dev machines empirically." Implementers of FR-006/007 (which
Popen-spawn a subprocess in `post_tool_failure`) and FR-009 (which patches
`session_end`'s subprocess call) must respect this: import `cairn.hooks.claude_hooks`
lazily inside test functions, patch `subprocess.Popen` at the module/call
site (`cairn.hooks.claude_hooks.subprocess.Popen`), not globally, and any
`tmp_path`-based memory-store test must pass an explicit `--db`/`--knowledge`
override rather than relying on `DEFAULT_DB_PATH`/`~/.cairn` defaults.

## Self-check

Ran: `python3 /Users/lnmtan/.claude/skills/spec-to-code/scripts/check.py specs/tribal-memory-loop-closure --repo /Users/lnmtan/Projects/others/cairn --survey-only`
