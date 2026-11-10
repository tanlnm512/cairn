# Flow fix plan (F1–F9)

This plan covers 17 work packages (WP1–WP16, with WP6 split into WP6a and WP6b), grouped into four milestones. The findings come from tracing the flows in [business-flows.md](business-flows.md) against the code, treating the code as the source of truth.

## How to use this plan

- **Delivery.** Everything lands as one PR on branch `feat/flow-fix-plan`, with one conventional commit per WP. The WP heading is the commit subject. There are no per-WP PRs and no GitHub issues.
- **Lifetime.** This file is a temporary index. WP16 deletes it and its row in [index.md](index.md) as the last commit of that same PR.
- **Locations.** Code is located by `file` + `symbol`, not by line number. Find a symbol with `cairn def <symbol>` or `rg -n "fn <symbol>"`.
- **Verification.** Every finding was checked against `main` when this plan was written. Before starting a WP, re-run its **Verify** command. If the finding no longer holds, drop the WP as not needed.
- **Shipping.** The PR follows the `AGENTS.md` shipping workflow: `prek run --all-files` → conventional commits + PR title → audit checklist → watch CI → `cairn update` + `record_memory` after merge.
  - The PR body names every flow it touches (`flows: F1–F9`).
  - A commit that adds or removes tests runs `cargo xtask flow-coverage` and includes `coverage.md`.
- **Tests.** Follow the `AGENTS.md` test rules:
  - Each contract has one owner test.
  - Extend existing tables instead of adding near-duplicate tests.
  - A bug-fix test must fail on the code before the fix.
  - Do not add test-only seams.
- **Goldens.** A WP that changes tool output text regenerates the golden it touches in the same commit.

### WP template

Each WP lists:

- **Problem**: what is wrong, from the user's side.
- **Verify**: a command or read that confirms the finding still holds.
- **Change**: what to edit, by file and symbol.
- **Compat**: behavior changes, migration needs, how to roll back.
- **Tests**: which test file owns the test, and the case it adds.
- **Docs**: every doc that must change in the same commit.
- **Done**: what must be observably true when the WP is complete.

## Decisions (adopted)

D1–D5 are adopted as ruled in the Recommendation column.

| # | Decision | Recommendation | Status | Applies to | Record as |
|---|---|---|---|---|---|
| D1 | Does the memory score include `critic_score`? Do memories with no references start lower? | Yes to both. Rebalance the weights so they sum to 1.0. Do not rescore existing memories; new scores apply at the next capture or critic pass | Adopted | WP3 | `record_memory(type="decision")` |
| D2 | Should `update` re-embed changed symbols automatically? | Yes when a non-hash backend is configured. Otherwise report the pending count | Adopted | WP6a | same |
| D3 | Should the `/mcp` write tools (`record_memory`, `knowledge_add`, `knowledge_delete`, `wiki_generate`) stay keyless on loopback? | Keep reads keyless. Writes require an API key or `serve --allow-writes`. `cairn connect` passes `--allow-writes` so agent setups keep working | Adopted | WP9 | same |
| D4 | One daemon per machine serving one store, or many stores? | Keep one store per daemon and refuse foreign-workspace requests. Study multi-store serving separately | Adopted | WP9 | same |
| D5 | What is a stable embedding identity across full builds? | Use (repo, file path, qualified name, body hash, model). Output of the WP6b spike | Adopted | WP6b | same |

## Milestone M0: data loss, crash loops, trust (start in parallel)

### WP1 · `fix(build): take the build lock in update --file and embed` · F1, F3 · S

- **Problem:** `cairn update --file` and `cairn embed` write to the live DB without the build lock. A concurrent full build swaps a new DB file in, and their writes are lost. The comment in `cairn-build/src/incremental.rs::incremental_update` describes this hazard.
- **Verify:**
  - Read `cairn-cli/src/cmds_core.rs::update` (the `--file` branch) and `cmds_core.rs::embed`.
  - Neither calls `swap::build_lock` before opening the connection.
- **Change:**
  - Take `cairn-store/src/swap.rs::build_lock` and open the connection under it, as `incremental_update` already does.
  - For `embed`, take the lock once per batch so a long embedding run does not block builds for its whole duration.
- **Compat:** a command that hits contention now refuses with the existing "build lock held" message instead of silently racing. Roll back by reverting the commit.
- **Tests:** in `cairn-cli/tests/update_file.rs`, add a case: with the lock held, `update --file` is refused.
- **Docs:**
  - `docs/cli-reference.md`: mention lock contention for `update --file` and `embed`.
  - `docs/indexing.md`: add both commands to the concurrency notes.
  - CHANGELOG: Fixed.
- **Done:** while a build holds the lock, neither command writes.

### WP2 · `fix(daemon): stop the launchd crash loop and keep the API key` · F8 · S

- **Problem:**
  - On macOS, a failed start leaves the launchd job loaded with `KeepAlive`, so launchd keeps respawning a daemon that cannot start.
  - `connect --host 0.0.0.0` and `serve restart` pass no API key, and the server refuses a keyless non-loopback bind, so the start fails and loops.
- **Verify:**
  - `cairn-daemon/src/lifecycle.rs::ensure_running_launchd` returns `Err` without calling `unload`.
  - `cairn-cli/src/cmds_connect.rs` passes `api_key: None` to `ensure_daemon_running`.
  - `cairn-cli/src/cmds_serve.rs`'s restart path passes `api_key: None`.
- **Change:**
  - Call `unload` before returning `Err` from the failed-health path.
  - In `connect` and in restart, resolve the key through the `auth::client_key()` chain (`resolve_api_key(None).or_else(persisted_api_key)`). `run_start` already re-resolves the key from the environment, so restart lacks only the persisted-key fallback.
  - In `connect`, refuse a keyless non-loopback host before writing the plist.
- **Compat:** none for loopback users. A remote-bind user without a key now gets an immediate error instead of a crash loop.
- **Tests:**
  - In `cairn-cli/src/cmds_connect.rs`'s `mod tests`, add a case: a keyless non-loopback host is refused before any plist is written.
  - In `cairn-daemon/tests/auth_lifecycle.rs`, extend `auth_resolution_contract` with a row for restart key carry-over.
- **Docs:**
  - `docs/deployment.md`: remote bind requires a key; how to recover a stuck launchd job.
  - `docs/cli-reference.md`: `connect --host`.
  - `SECURITY.md`: the remote-bind rule.
  - CHANGELOG: Fixed.
- **Done:** a failed start leaves no launchd job loaded, and `serve restart` keeps the key.

### WP3 · `fix(knowledge): scrub task results and tighten memory trust` · F5, F6 · M · needs D1

- **Problem:**
  - Wiki, compass and catalog task results are stored verbatim (only `memory-*` results are scrubbed), so secrets and injected text reach `ask_compass`.
  - A memory with no code references gets `graph_verification = 1.0`. With the default 0.5 confidence it scores about 0.536, which lands it in `tribal` on capture.
  - `compute_score` ignores `critic_score`.
  - Any memory whose title matches an existing memory exactly (case-insensitive) supersedes it.
- **Verify:**
  - In `cairn-query/src/tasks.rs::complete_task`, the scrub is guarded by `memory-`.
  - In `cairn-query/src/tools_km.rs`, `graph_verification` returns early when there are zero refs, and `compute_score` sums its weights without `critic_score`.
  - `find_supersession_candidate` has "Tier 1: exact title match".
- **Change:**
  - Run `strip_private_data` on every task result.
  - Give ref-less memories a `graph_verification` of 0.5.
  - Add `critic_score` to `compute_score` and rebalance the weights (D1).
  - Exact-title supersession requires an explicit `supersedes` id. A title match alone creates a new memory and returns `possible_duplicate: <id>`.
- **Compat:**
  - Existing memories keep their current tier. New scores apply at the next capture or `batch_critic` pass.
  - The MCP `record_memory` tool gets an optional `supersedes` field. This is additive.
  - Agents that relied on implicit same-title replacement now get a duplicate warning instead.
- **Tests:**
  - In `cairn-query/tests/km_golden.rs`, add a new `tier_for_score` table with a ref-less memory at default confidence, which must land in `drafts`. Add a same-title case with no `supersedes`, which must not supersede.
  - In `cairn-query/src/tasks.rs`'s `mod tests`, add a case: a wiki result containing a secret is stored scrubbed.
  - Regenerate the `km_golden` expectations.
- **Docs:**
  - `docs/mcp-tools.md`: `record_memory` `supersedes`.
  - `SECURITY.md`: task results are scrubbed.
  - `docs/flows/business-flows.md` F6: scoring and supersession.
  - CHANGELOG: Changed.
- **Done:** ref-less prose never enters `tribal` on capture, and no task result is stored unscrubbed.

### WP4 · `docs(flows): correct business-flows.md` · all flows · S

- **Problem:** the flow map does not match the code in several places.
- **Change:**
  - **Header:** rewrite the sentences that institutionalize `file:line` refs (line numbers "verified against the working tree"; "line numbers drift").
  - **F1/F3:**
    - `embed_all` writes only `embeddings`. vec0 is rebuilt by `--build-index` or the ANN backend.
    - The vec0 tables are defined in `cairn-store/src/vec0.rs`.
    - The SCIP step is `apply_scip_overlay`.
    - The builder's refuse-empty guards are always on, independent of `--staging`.
  - **F2:** the exact → qualified → substring ladder belongs to `find_definition`, not `get_callers`.
  - **F4:** add the knowledge layer (L5) to `ask_compass` routing.
  - **F5:**
    - The critic is skipped for `wiki-catalog`, `doc-link` and `skill-polish`.
    - Describe the actual stale-reclaim conditions.
  - **F6:**
    - `reflect_store` sets the stance and updates `memory_refs_baseline`; it does not move tiers.
    - The tier comes from the score.
    - Tiers are directories, not advisory labels.
    - Add `batch_critic`'s archive path.
  - **F7:** MCP `cross_repo_deps` resolves namespaces from `cairn.json` or the default table only; the env step (`CAIRN_REPO_NAMESPACES`) runs only on the CLI `deps` path.
  - **F8:**
    - Add the `mcp-daemon.lock` file.
    - The launchd plist lives in `~/Library/LaunchAgents` and its log in `~/Library/Logs`.
    - There is one store per daemon.
    - Write tools are exposed.
    - `CAIRN_MAX_RESULT_CHARS` overrides the truncation limit.
  - **F9:** the settings POST writes `~/.cairn/config.json` unless `CAIRN_READ_ONLY` is set.
  - **Everywhere:** replace `file:line` refs with `file::symbol`, per the `AGENTS.md` anti-stale rule.
  - **README env table:** add `CAIRN_MAX_RESULT_CHARS`. It is read in `cairn-daemon/src/server.rs` but appears in neither README nor docs. This is an `AGENTS.md` env-knob rule violation.
- **Tests:** none.
- **Done:** someone re-traces each flow against `main` and finds no contradictions.

## Milestone M1: speed and durable embeddings

### WP5 · `perf(build): make the freshness probe a walk, not a hash` · F1, F2 · M

- **Problem:** every CLI read runs `detect_changed`. Its new-file check calls `scanner::iter_files_and_skips`, which reads and SHA-256-hashes every indexable file in every repo. The cost is O(workspace bytes) per command.
- **Verify:** follow `cairn-cli/src/cmds_core.rs::fresh_conn` → `refresh_probe` → `cairn-build/src/incremental.rs::detect_changed` → `scanner.rs::iter_files_and_skips`, and confirm the `file_sha256` call.
- **Change:**
  - Add a walk-only variant (path, size, mtime) for `detect_changed`. Hash only the candidates whose stat changed.
  - Add a short fixed debounce stamp so back-to-back reads skip the walk. Do not add a new `CAIRN_*` knob.
- **Compat:** none. Detection results are unchanged.
- **Tests:** none new. `cairn-build/tests/incremental_detect.rs` already owns detection correctness and must stay green.
- **Docs:**
  - `docs/indexing.md`: describe how the freshness probe works.
  - CHANGELOG: Performance.
- **Done:**
  - Median wall time of a warm `cairn callers <sym>` on this repo drops by at least 50%. Measure with `hyperfine` and paste the before/after numbers in the PR.
  - No read path hashes unchanged files.
  - `cairn doctor` passes.

### WP6a · `feat(build): re-embed changed symbols after update` · F1, F3 · M · needs D2

- **Problem:** `reindex_paths` deletes the embeddings of changed symbols, and nothing re-creates them. `embeddings::embed_symbols` has no non-test caller. Edited code silently drops out of semantic search.
- **Verify:** `rg -n "embed_symbols\(" rust/crates` should match only the definition and `cairn-build/tests/embeddings_parity.rs`.
- **Change:**
  - When a non-hash backend is configured, call `embed_symbols` for the reindexed ids at the end of `incremental_update`.
  - Otherwise, store the pending count. The staleness banner and `cairn doctor` then show "N symbols awaiting embed".
- **Compat:** `update` gets slower by the embed time of the changed symbols when a real backend is configured. That is the cost of D2.
- **Tests:** in `cairn-build/tests/embeddings_parity.rs`, add a case: edit a symbol, run `update`, and its embedding row exists for the current model. This must fail before the fix.
- **Docs:**
  - `docs/indexing.md`: embeddings after `update`.
  - `docs/cli-reference.md`: `update` output.
  - CHANGELOG: Added.
- **Done:** semantic search finds an edited symbol right after `update`, and `cairn doctor` passes.

### WP6b · `feat(build): carry embeddings across a full build` · F1, F3 · L · needs D5

- **Problem:** `swap::install_staged` carries over only `build_runs`, `events` and `tool_metrics`. Every full build discards `embeddings` and `memory_embeddings` and forces a full re-embed.
- **Verify:** read the carry-over table list in `cairn-store/src/swap.rs`.
- **Change:**
  - Spike first (timebox: 1 day). Confirm that the D5 key is unique and stable across a rebuild of this repo, and write the result to cairn memory.
  - Then copy rows whose key matches into the staged DB before the swap.
- **Compat:**
  - If the key needs a new column, add it through the store's schema migration. Older binaries ignore unknown columns.
  - Rollback: revert the commit. Embeddings are still recomputed by `cairn embed`.
- **Tests:** in `cairn-build/tests/embeddings_parity.rs`, add a case: build → embed → build again. The embedding count is unchanged and zero rows are re-embedded.
- **Docs:**
  - `docs/indexing.md`: describe what survives a full build.
  - `docs/architecture.md`: the store lifecycle.
  - CHANGELOG: Added.
- **Done:** a rebuild of an unchanged workspace triggers no re-embed.

## Milestone M2: consistency and safe state transitions

### WP7 · `refactor(query): one resolver and one row path for callers/impact` · F2, F7 · M

- **Problem:**
  - `get_callers` matches only the exact `symbols.name`, so `Foo.bar` returns nothing.
  - The CLI has its own copies of the queries (`cairn-cli/src/cli_rows.rs::get_callers_rows`, `find_definition_rows`). The CLI and MCP therefore disagree:
    - The CLI has no fuzzy retry and exits FAILURE when nothing is found.
    - The CLI caps results at 200 without saying so.
    - CLI `impact` has no cross-repo section: `cross_repo_xref` runs only on the MCP path. (`cairn deps` exists on the CLI; `impact` does not use it.)
    - The core MCP graph tools ignore `CAIRN_REPO_NAMESPACES` (they call `resolve_namespaces` with no env override); the MCP km and wiki layers honor it, as does the CLI.
  - `pattern_to_fts`/`is_fts_prefix_pattern` exist three times: in `tools_search.rs`, `graph.rs` and `tools_km.rs`.
  - With `fuzzy=true`, the empty-result text says "(checked precise and fuzzy)" although the precise query never ran. On the default path the fuzzy retry always runs before that text is reached.
- **Verify:**
  - `rg -n "fn pattern_to_fts" rust/crates` returns three matches.
  - Running `cairn callers Foo.bar` against a known qualified method returns empty.
- **Change:**
  - Seed callers and impact through `find_definition`'s lookup ladder.
  - Route the CLI through the `cairn-query` data functions, then delete the duplicated `cli_rows` functions.
  - Keep one `pattern_to_fts` (in `tools_search.rs`).
  - Let MCP `cross_repo_deps` and `explore` resolve namespaces the way the CLI does.
  - Print a notice when results hit the limit, and correct the empty-result text.
- **Compat:**
  - CLI `callers` now returns fuzzy candidates and exits 0 when it finds any. This is a breaking change for scripts that check the exit code.
  - CLI `impact` gains a cross-repo section.
- **Tests:**
  - In `cairn-query/tests/nav_golden.rs`, add a qualified-name row.
  - In `cairn-cli/tests/impact_guard.rs`, add a CLI parity row.
  - Regenerate `nav_golden` and `flow_golden`.
- **Docs:**
  - `docs/cli-reference.md`: callers/impact output and exit codes.
  - `docs/mcp-tools.md`: namespace handling.
  - CHANGELOG: **Changed (breaking)**.
- **Done:**
  - MCP and CLI return the same caller set for the same symbol.
  - Exactly one `pattern_to_fts` remains.
  - No `cli_rows` copies of the query functions remain.

### WP8 · `fix(query): harden the task state machine` · F5 · M

- **Problem:**
  - `complete_task` removes the claim marker before the critic runs. A critic error then leaves the task `in-progress` with no marker, and `claim_task` reclaims a task only when a stale marker exists. Such a task cannot be claimed again; `complete_task` and `drop_task` still work on it.
  - The ownership check is skipped when the identity is empty. That is the default, and the task body tells agents to run `cairn task complete {id}` without `--as`.
  - `drop_task` has no ownership check.
  - The revise child is created before the status compare-and-set (CAS), so a lost race still spawns a revise.
  - `queue_enrich_tasks` takes no bundle lock. It has no `force` flag, and its dedupe is unconditional.
  - `queue_attempts` grows without a cap: the increments live in `queue_pages` and `retry_failed_pages`.
  - A chain that ran out of attempts is stored `failed` but reported as dropped.
  - `tools_wiki.rs::derived_state` returns `promoted` before it looks at failures, so a failed regeneration stays hidden.
- **Verify:**
  - Read `tasks.rs::complete_task`, `claim_task` and `drop_task`.
  - Read `tools_wiki.rs::queue_enrich_tasks` (no `lock()`), its task-body template (no `--as`), and `derived_state`.
- **Change:**
  - Remove the marker only after the terminal transition. Also let `claim_task` reclaim an in-progress task that has no marker and a stale `claimed_at`.
  - Default the identity to host:pid:session, and emit `--as <id>` in the task body.
  - Add an ownership check to `drop_task`, with `--force` for operators.
  - Create the revise child inside the lock, after the CAS.
  - Lock `queue_enrich_tasks`.
  - Cap `queue_attempts`, and report `failed` distinctly.
  - Have `derived_state` report `promoted (stale: last regeneration failed)`.
- **Compat:**
  - `task drop` of another agent's claim now needs `--force`.
  - `wiki status` gains the stale-promoted state.
  - `task complete` output reports `failed` where it used to say dropped.
- **Tests:**
  - In `cairn-query/src/tasks.rs`'s `mod tests`, add three cases:
    - A task is reclaimable after a critic error.
    - A drop by a non-owner is refused.
    - No revise child is created when the CAS is lost.
  - In `cairn-query/tests/wiki_plan.rs`, add a stale-promoted state row.
- **Docs:**
  - `docs/cli-reference.md`: `task claim|complete|drop` and `wiki status`.
  - `AGENTS.md`: the task-queue section (`--as`).
  - CHANGELOG: Fixed/Changed.
- **Done:** no task can become unclaimable, and only the owner (or `--force`) can drop or complete a claimed task.

### WP9 · `fix(daemon,dashboard): Host checks, version handshake, write gating` · F8, F9 · M · needs D3, D4

- **Problem:**
  - Dashboard GET routes have no Host or Origin check, so a DNS-rebinding page could read them.
  - `/healthz` returns the DB path to any loopback peer.
  - The daemon reuse check compares only `db`, and `/healthz` has no version. A daemon running an old binary is never replaced.
  - `connect` waits for the connect lock for 30 s (`LOCK_WAIT`), but daemon start can take 120 s (`START_LOCK_TIMEOUT`) plus the unhealthy wait.
  - `connect` errors print to stdout.
  - The write tools are keyless on loopback.
- **Verify:**
  - In `cairn-cli/src/dashboard/mod.rs`, only the POST handlers call `cross_site_rejected`.
  - Read `cairn-daemon/src/healthz.rs::payload`.
  - Compare `cmds_connect.rs::LOCK_WAIT` with `lifecycle.rs::START_LOCK_TIMEOUT`.
- **Change:**
  - Add a Host allowlist layer to the dashboard router, reusing the host logic from `cross_site_rejected`.
  - `/healthz` omits `db` unless the request is authenticated. Reuse then needs either the key or a new `store_id` hash field.
  - Add `version` to `/healthz`. `reuse_decision` restarts the daemon when the version differs or is missing.
  - Set `LOCK_WAIT` to at least `START_LOCK_TIMEOUT` plus the unhealthy wait.
  - Print `connect` errors to stderr with `eprintln!`.
  - Gate write tools per D3, and refuse foreign-workspace requests per D4.
- **Compat:**
  - A new client against an old daemon finds no `version`, restarts the daemon once, and continues.
  - An old client against a new daemon ignores the extra fields. Reuse keys on `store_id` instead of the `db` path.
  - Agents that write over keyless loopback without `connect` need `--allow-writes` (D3).
- **Tests:**
  - In `cairn-cli/tests/dashboard_parity.rs`, add a case: a GET with a foreign Host returns 403.
  - In `cairn-daemon/tests/auth_lifecycle.rs`, extend `healthz_payload_carries_the_reuse_guard_fields` with `version`/`store_id`.
  - In `cmds_connect.rs`'s `mod tests`, add a version-mismatch row for `reuse_decision`.
- **Docs:**
  - `SECURITY.md`: the Host allowlist, `/healthz` disclosure, write gating.
  - `docs/deployment.md`: version handshake, single-store daemon.
  - `docs/mcp-tools.md`: which tools are write tools.
  - `docs/cli-reference.md`: `serve --allow-writes`.
  - README env table if D3 adds a knob.
  - CHANGELOG: **Security**, Changed.
- **Done:**
  - A rebinding Host is rejected on every dashboard route.
  - Upgrading the binary replaces the running daemon on the next `connect`.

### WP10 · `fix(knowledge): lock the memory lifecycle and keep ids consistent` · F6 · M

- **Problem:**
  - `store_memory`, `demote_memory`, `promote_memory`, `decay` and `batch_critic` never take the bundle lock, and neither do their CLI callers in `cmds_knowledge.rs` or the `decay` call inside `cmds_core.rs::update`.
  - A tier move changes `concept_id` but leaves `memory_refs` and some embeddings under the old id.
  - `decay` archives tribal memories by creation age, even ones that are used often.
- **Verify:**
  - `rg -n "bundle.lock\(\)" cairn-query/src/tools_km.rs` shows the lock in capture, reflect, evolve and consolidate, and also in `add_document` (via `add_document_with_slug`), `update_status`, `delete_document` and `purge_archived` — but in none of the five functions above.
  - Read the `decay` age check.
- **Change:**
  - Take `bundle.lock()` inside each of the five functions. The lock is reentrant, so callers are safe.
  - Rewrite `memory_refs` and embedding keys on every id change.
  - Base decay on last-recalled time.
  - Have `update` print the decay summary, and add an `update --no-decay` opt-out.
- **Compat:** decay archives fewer memories. `update` gains a flag.
- **Tests:** in `cairn-query/tests/km_golden.rs`, extend the existing demote/decay section with two cases:
  - A demoted memory is still found by semantic recall under its new id.
  - A recently recalled tribal memory is not archived.
- **Docs:**
  - `docs/cli-reference.md`: `update --no-decay`, `memory decay`.
  - `docs/flows/business-flows.md` F6.
  - CHANGELOG: Fixed.
- **Done:** every memory write path runs under the bundle lock, and no orphaned refs or embeddings remain after tier moves.

## Milestone M3: hygiene (any order)

### WP11 · `perf(query): use the closure fast path at the default depth` · F2 · S

- **Problem:** MCP `impact_analysis` runs `tools_nav.rs::impact_analysis`, which is DFS-only. The closure fast path (`graph.rs::impact_analysis`, gated by `CLOSURE_MAX_DEPTH`) is not wired into it, so no MCP call uses the closure at any depth. `explore` also recomputes a callers query and an impact walk for every seed, with no memoization across seeds.
- **Change:**
  - Wire the closure fast path into `tools_nav.rs::impact_analysis`, keeping the DFS as the fallback.
  - So that the default MCP call takes it, either lower the MCP default depth below `CLOSURE_MAX_DEPTH`, or raise the closure depth if the closure build cost allows it. Measure before choosing.
  - Memoize per-seed results within one `explore` call.
- **Compat:** the default impact depth may change. Call it out.
- **Tests:** `cairn-query/src/graph.rs`'s `mod tests` owns closure ⇔ DFS parity. Add a case at the new default depth.
- **Docs:** `docs/mcp-tools.md` (default depth) and CHANGELOG.
- **Done:** default MCP impact uses the fast path, and the parity case passes.

### WP12 · `fix(embed): make embed_all honest and report mismatches` · F3 · S · after WP6a

- **Problem:**
  - `embed_all` loads every symbol body before it filters out the up-to-date ones, and applies `--limit` only afterwards.
  - It always rebuilds `term_df`.
  - It hardcodes `failed_batches: 0`, and it reports rows cut by `--limit` as "skipped".
  - In `tools_search.rs`, `cosine_scan` silently skips rows whose dimension does not match. The search then shows "index empty" instead of "model/dim mismatch".
  - An unknown backend name silently falls back to Hash.
- **Change:**
  - Filter stale rows in SQL before loading bodies, and apply `--limit` in the query.
  - Skip `rebuild_term_df` when nothing changed.
  - Count real failed batches, and keep committed batches on error.
  - Return a mismatch hint.
  - Error on unknown backend names.
- **Compat:** a misspelled `CAIRN_EMBED_BACKEND` (or equivalent setting) now errors instead of degrading silently.
- **Tests:** in `cairn-query/tests/native_embed.rs`, add a mismatch-hint row. In `embeddings_parity.rs`, add a `--limit` counting row.
- **Docs:** `docs/cli-reference.md` (`embed`), README env table (backend names), CHANGELOG.
- **Done:** `embed` output counts add up, and a misconfiguration is reported explicitly.

### WP13 · `fix(query): disambiguate name collisions and stale stores` · F2, F7 · S

- **Problem:**
  - `get_callers` and impact merge same-named symbols across repos.
  - `trace_flow` silently picks one with `LIMIT 1`.
  - Federation never checks store schema versions.
  - The `repo_build_state='building'` marker is read only by `doctor`.
- **Change:**
  - `trace_flow` lists the candidates when a name is ambiguous.
  - Group callers by repo-qualified symbol.
  - Federation reports `schema_mismatch` per store in `states`.
  - Query responses carry a "rebuild in progress" banner.
- **Compat:** output gains fields and banners. This is additive.
- **Tests:** in `cairn-query/tests/flow_golden.rs`, add an ambiguous-entry row. In `cairn-query/tests/federation_walk.rs`, add a schema-mismatch store row.
- **Docs:** `docs/mcp-tools.md` and CHANGELOG.
- **Done:** a name collision is never resolved silently.

### WP14 · `fix(daemon): graceful shutdown and no blocking on async threads` · F8, F9 · S

- **Problem:**
  - `cmds_serve.rs::run_foreground` waits on `ctrl_c` only, and the server task is never awaited. `launchctl unload` sends SIGTERM, so in-flight calls and bundle-file writes are cut off.
  - `record_tool_metrics` and every dashboard handler run synchronous SQLite calls on async worker threads.
- **Change:**
  - Handle SIGTERM and await the server task with a drain timeout.
  - Move `record_tool_metrics` and the dashboard handlers onto `spawn_blocking`.
- **Compat:** none.
- **Tests:** in `cairn-daemon/tests/tool_metrics.rs`, assert that a metrics row is still written. Shutdown behavior is checked manually and recorded in the PR.
- **Docs:** `docs/deployment.md` (stop semantics) and CHANGELOG.
- **Done:** `cairn serve stop` drains in-flight calls, and `cairn doctor` passes.

### WP15 · `fix(cli): accurate status output` · F1 · S

- **Problem:**
  - `build` prints stage progress after the fact and throws `StageTimings` away.
  - The `--staging` help text claims a property that every build already has.
  - Untracked files are dropped silently when `git diff HEAD` fails, as in a repo with no commits.
  - `update --file` ignores `result.errors` and exits 0.
  - The `--diff-ref` check reports "does not resolve" even when git itself failed to launch.
- **Change:**
  - Print the real `StageTimings`.
  - Reword the `--staging` help.
  - Record the git failure and warn about it.
  - Make `update --file` exit non-zero on errors.
  - Tell a git launch failure apart from an unresolvable ref.
- **Compat:** `update --file` exit code changes when it hits errors. Call it out.
- **Tests:** in `cairn-cli/tests/update_file.rs`, add an error-exit row. In `cairn-build/tests/incremental_detect.rs`, add a row for a repo with no commits.
- **Docs:** `docs/cli-reference.md` (`build`, `update`) and CHANGELOG.
- **Done:** every status line reflects real work, and every error surfaces.

### WP16 · `docs(flows): close out the fix plan` · all flows · S · last

- **Change:**
  - Delete this file and its row in [index.md](index.md), as the last commit of the same PR.
  - Update `chart-2-dev-priorities` and its SVG so they drop the resolved risks.
  - Re-run `cargo xtask flow-coverage`.
  - `rg action-plan` should return nothing outside CHANGELOG.
- **Done:** no plan residue remains in the tree.

## Order and dependencies

```
Day 1:  WP1 ║ WP2 ║ WP3 ║ WP4
M1:     WP5 ║ WP6a → spike D5 → WP6b
M2:     WP7 ║ WP8 ║ WP9 ║ WP10
M3:     WP11 ║ WP13 ║ WP14 ║ WP15 ║ WP12 (after WP6a)
Last:   WP16
```

- **Critical path:** WP6a → D5 spike → WP6b.
- **Conflict hotspots:**
  - `cmds_core.rs` is touched by WP1, WP5, WP7, WP10 and WP15. Commit them in that order.
  - `tasks.rs` is touched by WP3 and WP8.
  - `tools_km.rs` is touched by WP3, WP7 and WP10.
  - `cmds_connect.rs` is touched by WP2 and WP9.
  - `cmds_serve.rs` is touched by WP2, WP9 and WP14.
  - `lifecycle.rs` is touched by WP2 and WP9.
- **Revertable alone:** WP7 and WP9, because they contain breaking or security changes. Keep each commit self-contained.

## Compatibility summary

| WP | User-visible change | Severity | CHANGELOG section |
|---|---|---|---|
| WP2 | A keyless remote bind errors immediately instead of crash-looping | Medium | Fixed |
| WP3 | Duplicate-title memories no longer supersede; new scoring | Medium | Changed |
| WP6a | `update` is slower when a real embed backend is configured | Low | Added |
| WP7 | CLI `callers` exit code and fuzzy output; CLI `impact` cross-repo section | **Breaking** | Changed |
| WP8 | `task drop` needs ownership or `--force`; `failed` reported distinctly | Medium | Changed |
| WP9 | Write tools gated; `/healthz` fields change; daemon auto-restarts on version mismatch | **Security** | Security |
| WP10 | Decay archives fewer memories | Low | Fixed |
| WP11 | Default impact depth may change | Low | Changed |
| WP12 | Unknown embed backend name errors | Low | Changed |
| WP15 | `update --file` exits non-zero on errors | Low | Changed |

## Docs coverage matrix

| Doc | WPs |
|---|---|
| `docs/flows/business-flows.md` | WP3, WP4, WP10 |
| `docs/cli-reference.md` | WP1, WP2, WP6a, WP7, WP8, WP9, WP10, WP12, WP15 |
| `docs/mcp-tools.md` | WP3, WP7, WP9, WP11, WP13 |
| `docs/indexing.md` | WP1, WP5, WP6a, WP6b |
| `docs/architecture.md` | WP6b |
| `docs/deployment.md` | WP2, WP9, WP14 |
| `SECURITY.md` | WP2, WP3, WP9 |
| README env table | WP4, WP9 (if D3 adds a knob), WP12 |
| `AGENTS.md` | WP8 |
| CHANGELOG | every WP except WP4 and WP16 |
| `coverage.md` (generated) | every WP that adds tests |

## Maintenance

- **Status.** The commit log of `feat/flow-fix-plan` is the source of truth: one commit per landed WP. This file only changes when the scope of a WP changes.
- **After each WP commit:**
  - Run `cairn update`.
  - Run `record_memory`: `decision` for D1–D5, `mistake` for the lost-write and crash-loop root causes, `pattern` for the build-lock and walk-only probe.
  - Run `cairn doctor` after WP1, WP5, WP6a/b and WP14.
- **After each milestone:**
  - Update `chart-2-dev-priorities`.
  - Re-run `cairn wiki generate --llm` for the pages covering touched flows so `ask_compass` stops serving the old behavior.
- **If `main` moves:** re-run each WP's **Verify** step before starting it. Drop any WP whose finding no longer holds.

## Definition of done (whole plan)

- [ ] WP1–WP15 each landed as one conventional commit in the single PR on `feat/flow-fix-plan`; the PR names its flows, has the audit checklist filled in, and CI is green.
- [ ] Every bug-fix test was shown to fail on the code before the fix.
- [ ] Every row of the docs coverage matrix is satisfied.
- [ ] D1–D5 are recorded in cairn memory.
- [ ] `cairn doctor` passes on `main`.
- [ ] WP16 is the last commit of that PR and no plan residue remains.
