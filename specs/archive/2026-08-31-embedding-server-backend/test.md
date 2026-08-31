# Test Cases: Embedding server backend (oMLX / Ollama / OpenAI-compatible)

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-27
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.

Runner conventions used below:
- "Stand-in server" = a scriptable fake speaking the documented
  `/v1/models` + `/v1/embeddings` contract at a loopback address, controllable
  to fail slowly, refuse, throttle, truncate, or die.
- The repository test command is `uv run --extra dev pytest -q`.
- Configuration vocabulary (`CAIRN_EMBED_*`, stamp labels such as
  `server/{netloc}/{model}`) is requirement-level surface quoted from spec.md,
  not implementation detail.
- Standing regression guards (FR-009 family) deliberately cite the existing
  suites named in survey.md so creep in already-shipped backends fails loudly.

Boundary coverage map: empty input (TC-015), huge corpus (TC-006),
concurrent/throttled access (TC-005, TC-027, TC-028), server dies mid-batch
(TC-005, TC-007, TC-019), stalled server (TC-010), model swapped under a
stable id (TC-012).

## US1 — Embed via a local inference server

## TC-001 — All three collections embed through the local server and are labeled with its identity
- **Story**: US1 · **Traces to**: FR-001, FR-004 · AC1
- **Given** a reachable OpenAI-compatible embedding server whose catalog lists the configured model id, with the server backend selected
- **When** a full embedding run executes over non-empty code, knowledge, and memory collections
- **Then** every newly produced vector in all three collections was obtained from the server, and each row carries the producer label `server/{host-and-port}/{model-id}`
- **Pass condition**: with a stand-in server recording every received request and returning real-shaped vectors, run `uv run --extra dev pytest -q`: the scenario asserts requests arrived at the server for all three collections and that stored rows report exactly the label `server/127.0.0.1:{port}/{model}`; no request went to any other producer

## TC-002 — Named server flavors fall back to their documented default addresses
- **Story**: US1 · **Traces to**: FR-001
- **Given** neither an explicit server address nor any other location setting is provided, with the selected flavor being the oMLX preset (or alternatively the Ollama preset)
- **When** an embedding run or a search needing a vector executes
- **Then** requests go to that flavor's documented default local address (oMLX: port 8000; Ollama: port 11434, both under the standard versioned prefix) without further configuration
- **Pass condition**: stand-in servers listening on those exact default addresses receive the traffic; scripted via `uv run --extra dev pytest -q`

## TC-003 — Selecting the generic server flavor without an address fails with a self-explanatory message
- **Story**: US1 · **Traces to**: FR-001
- **Given** `CAIRN_EMBED_BACKEND=server` with `CAIRN_EMBED_BASE_URL` unset
- **When** an operation needs embeddings
- **Then** the operation fails fast and its output names the missing address setting and how to supply it — it does not fall back to another backend and does not produce vectors
- **Pass condition**: invoke the relevant command with those settings; exit status is nonzero and the combined output contains the environment variable name `CAIRN_EMBED_BASE_URL`; checkable automatically

## TC-004 — An optional access token is honored wherever the server expects one
- **Story**: US1 · **Traces to**: FR-001, FR-002
- **Given** a server configured to require a bearer token, and `CAIRN_EMBED_API_KEY` set to it (alternatively: unset or wrong)
- **When** availability is checked and embeddings are requested
- **Then** the token is presented on both the catalog check and embedding requests, and the setup works end to end; with the token wrong or missing, the setup is treated as unavailable with a named degradation (authentication failure called out), never a silent empty answer
- **Pass condition**: stand-in server asserts the Authorization header equals the configured token on every call and rejects others with an OpenAI-style authentication error; both branches verified in `uv run --extra dev pytest -q`

## TC-005 — Transient trouble (resets, 5xx, throttling) is retried patiently and then succeeds
- **Story**: US1 · **Traces to**: FR-003 · AC2
- **Given** a stand-in server programmed to drop connections, return 500-class codes, or answer 429 "slow down" for its first few embedding requests before behaving
- **When** an embedding run executes against it
- **Then** the run waits between attempts (growing pauses, roughly doubling, with some jitter), finishes successfully overall, and the total attempt count per failed request stays within the documented ceiling of 3 retries
- **Pass condition**: the stand-in logs per-request attempt timestamps; the scripted scenario (`uv run --extra dev pytest -q`) shows ≥2 pause gaps between retries for the same batch, growing pause intervals, final success, and never more than 3 retries

## TC-006 — A very large corpus is shipped in bounded-size requests and every text gets its own vector
- **Story**: US1 · **Traces to**: FR-003 · AC1/AC2 scale edge
- **Given** `CAIRN_EMBED_SERVER_BATCH` at its default (32) or a small test value, and an input collection much larger than one request's capacity (hundreds of texts, exercising the "huge corpus" edge)
- **When** embedding runs
- **Then** no single HTTP request ever carries more than the configured maximum number of inputs, requests proceed in order, and each input text maps back to exactly its own returned vector (none swapped, dropped, or duplicated) in stored results
- **Pass condition**: stand-in server records input-list lengths (all ≤ batch cap) and echoes position-tagged vectors; scripted scenario asserts per-request size, count of requests = ceil(n/cap), and stored vector-to-text alignment matches the tags

## TC-007 — A permanent refusal stops the run immediately, quoting the server's own explanation
- **Story**: US1 · **Traces to**: FR-003 · AC2
- **Given** a server that answers an embedding request with a 4xx (other than 429) carrying its own error text, e.g. an unknown-model message listing valid ids
- **When** an embedding run hits that response
- **Then** the run stops on the spot without burning retries, exits nonzero, and the user-visible message contains the server's error text word for word (so an unknown-model reply shows the server's list of available ids)
- **Pass condition**: scripted scenario stands in a 404-with-body server; the command output contains the exact planted sentence from the fake response; attempt count for that request is exactly 1; exit status 1

## TC-008 — Each request respects the configured time limit instead of hanging
- **Story**: US1 · **Traces to**: FR-003 · AC2
- **Given** `CAIRN_EMBED_TIMEOUT` at default 30 seconds (or shortened for the test), and a server that stalls past that limit
- **When** an embedding request runs
- **Then** the client gives up at approximately the configured limit, treats the stall as a retryable failure per the retry policy, and does not wait indefinitely
- **Pass condition**: with a 1-second limit and a 5-second-stalling stand-in, elapsed time per attempt ≈1 s and the retry ladder engages; measured programmatically via `uv run --extra dev pytest -q`

## TC-009 — Inconsistent vector widths inside one response are rejected, nothing partial is stored
- **Story**: US1 · **Traces to**: FR-003
- **Given** a misbehaving server returning one batch whose embeddings disagree in dimensionality
- **When** the batch is processed
- **Then** the batch is rejected with a clear mixed-dimension error and zero rows from that batch are written — stored data never mixes vector spaces
- **Pass condition**: scripted stand-in returns a 1024-float and a 768-float item in one response; the command fails with a dimension-mismatch message and the datastore row count for the run is unchanged

## TC-010 — Server down at query time: search still answers, the gap is named, and the cheap substitute is never used
- **Story**: US1 · **Traces to**: FR-002 · AC3
- **Given** the configured server refuses connections (or its catalog check stalls well past ~2 seconds)
- **When** a semantic search executes during that outage
- **Then** the search still returns useful results, clearly identifies that the dense leg was skipped and why, and never substitutes hash-based lookalike vectors or claims semantic provenance for non-semantic results; repeated searches during the outage are answered promptly rather than re-stalling (the down verdict is remembered briefly and rechecked on later operations)
- **Pass condition**: kill/freeze the stand-in server between requests; run a search: results non-empty, output marks the dense leg unavailable with a degradation notice, and no result claims server-semantic origin; scripted under `uv run --extra dev pytest -q`

## TC-011 — A model id absent from the server's catalog is treated exactly like an outage
- **Story**: US1 · **Traces to**: FR-002 · AC3
- **Given** a healthy server whose `/v1/models` listing does not contain the configured model id
- **When** a query runs
- **Then** the dense leg is skipped with a model-missing degradation (not a crash, not a silent empty result), and no hash vectors appear
- **Pass condition**: stand-in serves a catalog omitting the configured id; search completes with the named degradation visible in output/events; automated via the repo suite

## US2 — Migrate without re-embedding

## TC-012 — Switching producers gated on measured similarity: proof first, and a low score aborts before any write
- **Story**: US2 · **Traces to**: FR-005 · AC1
- **Given** an archive of vectors produced under the previous label and an explicit migration alias pointing at a differently-labeled producer, where the new producer either matches the old weights (score ≈1.0) or is a different model entirely (score well below 0.9)
- **When** the first embedding pass under the alias runs
- **Then** compatibility is measured first — sampled from already-stored texts, compared as average cosine against the stored vectors — BEFORE any new row is written; at 0.98 or above the pass proceeds; below it the run hard-aborts reporting the exact measured score, and not a single row was written under the alias label
- **Pass condition**: two scripted scenarios (stand-in serving same-weights-equivalent vectors vs. unrelated vectors) via `uv run --extra dev pytest -q`: pass case proceeds; fail case aborts with the measured value printed (e.g. "0.61…") and the alias-labeled row count is exactly zero afterwards; ordering asserted by having the fake answer the sampling calls before permitting any storage call

## TC-013 — A proven migration costs seconds: nothing is re-embedded and search stays correct
- **Story**: US2 · **Traces to**: FR-005, FR-004 · AC2
- **Given** the TC-012 pass case completed (alias accepted)
- **When** subsequent searches run against the migrated archive
- **Then** zero previously stored vectors were recomputed or rewritten (their creation times are untouched) and dense search returns the same top answers as before the switch
- **Pass condition**: scripted migration asserts stored-row timestamps are identical pre/post and a canned query's top-3 result identities match the pre-migration baseline; automated in the repo suite

## TC-014 — An explicit custom label is honored end to end, keeping existing upkeep machinery intact
- **Story**: US2 · **Traces to**: FR-004
- **Given** `CAIRN_EMBED_MODEL_STAMP` set to a custom value while producing via the server
- **When** embedding runs under it and later the producer (or label) switches away
- **Then** rows were filed under that exact label (overriding the derived `server/{host}/{model}`); staleness checking, cleanup-on-switch, and per-label search tables all operate correctly, old-label data stays readable, and new-label data lands separately without corruption
- **Pass condition**: scripted scenario stamps rows under the custom value, runs a search restricted to it, performs a producer switch, and confirms old-label data remains readable and new-label data lands separately

## TC-015 — Empty-archive edge: starting fresh under an alias needs no impossible comparison
- **Story**: US2 · **Traces to**: FR-005 · boundary
- **Given** an empty collection (zero stored vectors) and a migration alias configured
- **When** the first embed pass runs
- **Then** no parity comparison is demanded (there is nothing sampled to compare), the run does not crash or wedge, and vectors are produced and stored normally going forward
- **Pass condition**: scripted run against an empty store completes with exit 0 and produces rows under the alias label; automated via `uv run --extra dev pytest -q`

## US3 — Degrade loudly, never silently

## TC-016 — A same-server replacement earns a session-long promotion, with the permanence command spelled out
- **Story**: US3 · **Traces to**: FR-012 · AC1
- **Given** the configured model id vanished from a still-running server that hosts another embedding-capable candidate scoring ≥0.98 against stored vectors (and, counter-wise, a different candidate scoring below)
- **When** a query runs
- **Then** the qualified candidate quietly serves the dense leg for this session using the existing stored vectors (nothing re-embedded), and a notification names the exact follow-up command (or dashboard action) that would make it permanent; the disqualified candidate gets no promotion — instead the notification says a re-embed is required and the dense leg stays dark
- **Pass condition**: scripted pair of scenarios: high-parity candidate → results carry semantic provenance, alias promotion noted, message contains the adopt/permanent instruction; low-parity candidate → no semantic results, message recommends re-embed; zero vector rewrites in both; automated in the repo suite

## TC-017 — The local-model rescue rung fires only with proof, never on faith
- **Story**: US3 · **Traces to**: FR-012 · AC2
- **Given** the server path is dead and a locally cached, locally servable model exists
- **When** a query runs
- **Then** if the local model measures ≥0.98 against stored vectors it serves the dense leg for this session (the measured cross-producer 1.000000 case); if local weights are absent, unloadable, or score below the gate, the ladder falls through to keyword-only rather than pretending
- **Pass condition**: three scripted variants (matching local / missing local / mismatching local) assert semantic-service, fall-through, fall-through respectively; automated via `uv run --extra dev pytest -q`

## TC-018 — Terminal fallback results are honestly labeled keyword-only, tagged degraded, and told how to fix
- **Story**: US3 · **Traces to**: FR-012 · AC2
- **Given** no parity-qualified replacement anywhere (server down, no usable local rescue)
- **When** a semantic search runs
- **Then** results come from the BM25/keyword leg fused as usual, each result carries keyword-only provenance (`provenance="bm25"`), a degradation marker identifying the embedding backend as the cause, and a remediation hint (check the server / re-embed) — and the silent-empty-result trap never occurs when keyword matches exist
- **Pass condition**: scripted outage scenario asserts result dicts show provenance `bm25`, a `degraded="embedding-backend"`-style flag, and a hint string; the repo suite covers this automatically (this provenance behavior already ships and has existing coverage)

## TC-019 — An embedding explosion mid-search surfaces as graceful degradation, not a crash
- **Story**: US3 · **Traces to**: FR-012 · AC2 · boundary
- **Given** a server that accepts the query-embedding request and then dies or returns garbage mid-flight (the historical bug: such an error escaped uncaught and killed the search)
- **When** the user runs a search
- **Then** the search completes with keyword-side results plus the standard degradation notices; no traceback escapes to the caller, and the search itself exits clean
- **Pass condition**: scripted mid-response connection kill; the search invocation returns a well-formed result object (exit 0 for the search path) with degradation markers present; automated under `uv run --extra dev pytest -q`

## TC-020 — The first degradation announces itself once, on every promised channel
- **Story**: US3 · **Traces to**: FR-013 · AC3
- **Given** any degraded or failed server state occurring in a fresh process
- **When** it first occurs
- **Then** exactly one warning log line, exactly one telemetry event carrying a reason from the documented set (`server_down | model_missing | parity_fail | fallback_session_alias | fallback_local | hybrid_only`), an assistant-facing footnote on affected tool answers, a `cairn doctor` entry describing it, and a dashboard banner all appear — and continued degraded activity in the same process adds no duplicate notices
- **Pass condition**: instrumented degradation capture: log lines, telemetry sink events, an invoked tool answer, a doctor run, and a dashboard page fetch — each channel shows exactly one notice for the reason; a second identical incident adds none; automated in the repo suite

## TC-021 — Telemetry counts reasons, not repetitions: one event per reason per process
- **Story**: US3/US5 · **Traces to**: FR-007, FR-013
- **Given** a process experiencing the same degradation repeatedly, then encountering a different degradation reason
- **When** events are inspected afterward
- **Then** the first reason produced exactly one `EMBED_SERVER_DEGRADED` event for the whole process lifetime despite repeated incidents, the new reason got its own single event, and payloads identify host and model but never echo request text
- **Pass condition**: telemetry sink assertion in a scripted run: event multiset == {reason_A: 1, reason_B: 1} after N≥3 incidents; payload keys inspected to exclude input text; automated via the repo suite

## US5 — Operational safety nets

## TC-022 — Doctor grows server diagnostics: reachability, catalog, sample similarity, latency
- **Story**: US5 · **Traces to**: FR-007 · AC2
- **Given** a server backend configured, first against a healthy server then a broken one
- **When** `cairn doctor` runs
- **Then** the report includes four server checks — probe/reachability, model listing presence, a sampled parity estimate, and responsiveness/latency — each individually PASS/WARN/FAIL with a helpful hint on failure; existing check sections remain and overall exit semantics are unchanged
- **Pass condition**: run `cairn doctor` in both scripted states via the established CLI-test convention: healthy state shows four server-named PASS entries; broken state flags the applicable ones FAIL/WARN with hints; prior check names still present; automated via the existing doctor test suite extended for these cases

## TC-023 — Boot warms the server quietly, off the critical path
- **Story**: US5 · **Traces to**: FR-006 · AC1
- **Given** a server backend configured with a slow first request (simulating the server's lazy model load taking seconds)
- **When** the application boots (e.g. the MCP server)
- **Then** a tiny warming request fires in the background so the model loads ahead of the first real query, boot never blocks on it, and a failed or timed-out warm-up produces at most one warning and never breaks startup
- **Pass condition**: scripted: boot against a stand-in that delays 5 s then succeeds — boot completes immediately and the tiny probe arrives in the background; second variant: permanently failing stand-in — boot still healthy, one warning logged, no exception escapes; automated via `uv run --extra dev pytest -q`

## TC-024 — During automated test runs the warm-up politely stands down
- **Story**: US5 · **Traces to**: FR-006 · test-hygiene guard
- **Given** execution inside an automated pytest run (documented guard condition)
- **When** any component triggers background warming
- **Then** no network warm-up request is issued at all — tests never phone a server as a side effect
- **Pass condition**: the existing warm-up test suite (which pins this guard) stays green and gains a server-backend variant asserting zero outbound warm requests under the guard: `uv run --extra dev pytest tests/test_model_warmup.py -q`

## TC-025 — Getting-started guidance advertises the server route as the torch-free option
- **Story**: US5 · **Traces to**: FR-008 · AC3
- **Given** a fresh installation without the heavyweight semantic dependencies
- **When** the user asks for embeddings (or triggers the built-in guidance message explaining what to install)
- **Then** the guidance presents setting the backend to a local server flavor (omlx/ollama/server) as the no-torch option alongside the classic extra-install and hash choices
- **Pass condition**: trigger the guidance text via the CLI (dependent-command invocation) and assert the message names the server/omlx/ollama path explicitly; automated in the repo suite

## TC-026 — The manuals cover the new settings, the oMLX conversion step, and the privacy note
- **Story**: US5 · **Traces to**: FR-008
- **Given** the shipped configuration and retrieval documentation
- **When** a reader looks up embedding backends
- **Then** both documents list the new backend values and their settings variables (base URL, model id, API key, timeout, batch size, stamp override), describe the one-time safetensors conversion needed for oMLX with BAAI weights, and state plainly that embedding input (code text) is sent to the configured URL — localhost by default, remote an explicit choice
- **Pass condition**: human review of `docs/configuration.md` and `docs/retrieval.md` confirms all enumerated items present (spot-checkable: every variable name from this spec's configuration table appears; "safetensors" appears in the oMLX how-to; a privacy paragraph exists) — manual verification

## US4 — Configure and observe from the dashboard

## TC-027 — Saved settings persist, survive restarts, and environment variables still outrank them
- **Story**: US4 · **Traces to**: FR-010, FR-011 · AC1
- **Given** the dashboard open in a browser
- **When** I choose a backend, enter the model id and related values, and press save
- **Then** the values are written to the persistent configuration file at `~/.cairn/config.json` and still take effect after restarting everything; whenever a conflicting environment variable is set, the environment variable wins
- **Pass condition**: GUI or HTTP-level: submit the settings form, inspect `~/.cairn/config.json` contains the values, restart and observe effective values; then set conflicting env values and confirm the effective choice follows the env var; automated via `uv run --extra dev pytest -q`

## TC-028 — Changes land in already-running sessions without a restart
- **Story**: US4 · **Traces to**: FR-010 · AC1 · concurrency edge
- **Given** a long-lived session (agent/assistant process or dashboard) started before a settings change
- **When** the settings file changes underneath it (via dashboard save or direct edit)
- **Then** the running session's next embedding-related operation honors the new values without anyone restarting it; a deliberate refresh affordance exists so tools needing instant certainty can force the freshest read
- **Pass condition**: scripted: start a process pointed at stand-in backend A, flip config to backend B mid-life, perform another operation, observe B receiving traffic; automated via `uv run --extra dev pytest -q`

## TC-029 — Changing the server address demands an explicit yes
- **Story**: US4 · **Traces to**: FR-011 · AC2 · security
- **Given** the dashboard Settings section with an existing saved server address
- **When** I submit a different base URL but skip the explicit confirmation affordance
- **Then** the change is refused, the old address remains in effect, and a message explains the confirmation requirement; supplying the confirmation applies it
- **Pass condition**: scripted HTTP/GUI double-run: submit sans-confirm → config file unchanged + explanatory response; submit with confirm → file updated; automated in the repo suite

## TC-030 — The API key is write-only: never shown again after saving
- **Story**: US4 · **Traces to**: FR-011 · AC2 · security
- **Given** an API key saved through the dashboard
- **When** I reopen the Settings section (or fetch its raw form/state through any read endpoint)
- **Then** the key material never renders back — only a placeholder or masked hint appears
- **Pass condition**: scripted GET of the settings page/data after saving a known key: response body contains no substring of the key; automated via `uv run --extra dev pytest -q`

## TC-031 — The status view answers: what am I running, how much is embedded, is the server alive, what rung am I on
- **Story**: US4 · **Traces to**: FR-011 · AC3 · dashboard walkthrough
- **Given** a dashboard instance over any backend state (healthy server / degraded / plain local)
- **When** I open the Embeddings status view
- **Then** I see, human-readable: the effective backend, the resolved producer label, per-collection vector counts (code / knowledge / memory) with last-embedded times, current probe health of the configured server, and — when degraded — a banner naming the active fallback rung; suitable later for browser automation (navigate → assert on rendered page)
- **Pass condition**: scripted HTML-level check: fetch the status page in each of the three states and assert each listed element is present with correct values (counts matching the datastore, health reflecting actual probe outcome, banner text matching the active rung); automated via `uv run --extra dev pytest -q`

## Standing regression guards (FR-009 — byte-for-byte unchanged neighbors)

## TC-032 — Guard: the built-in local, hash, and openai behaviors never drift
- **Story**: all · **Traces to**: FR-009 · SC-4 · standing guard
- **Given** no server configuration whatsoever (classic setups only)
- **When** the full business test suite runs
- **Then** every pre-existing backend behavior holds: local selection with/without weights, hash substitution and its warning path, openai key gating, provenance labeling, doctor, dashboard, warm-up, flush-retry, and semantic-unavailable suites all pass exactly as before this feature existed
- **Pass condition**: `uv run --extra dev pytest -q` exits 0 with zero changes forced into the pre-existing test files named in survey.md item S12 (test_embedding_backend_quality, test_doctor, test_dashboard_app, test_model_warmup, test_semantic_events, test_embed_flush_stalled, test_semantic_unavailable, test_staleness_banner among them); any edit to those legacy files to accommodate the server backend is a spec violation signal

## TC-033 — Guard: the openai cloud path keeps its fixed destination and mandatory key
- **Story**: all · **Traces to**: FR-009 · standing guard
- **Given** `CAIRN_EMBED_BACKEND=openai`
- **When** operations run without a key configured (and separately, with one)
- **Then** without a key they still refuse with the same explanatory demand as today; with a key they still target exactly the canonical OpenAI endpoint and request shape — the server feature added a sibling path, never touched this one
- **Pass condition**: existing quality-suite selections covering the openai gate remain green (`uv run --extra dev pytest tests/test_embedding_backend_quality.py -k 'test_false_when_openai_backend' -q`) plus a scripted no-key invocation reproducing today's refusal message verbatim; reviewed as part of TC-032's full-suite gate

## Coverage matrix

| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001 | TC-001, TC-002, TC-003, TC-004 | auto |
| FR-002 | TC-004, TC-010, TC-011 | auto |
| FR-003 | TC-005, TC-006, TC-007, TC-008, TC-009 | auto |
| FR-004 | TC-001, TC-013, TC-014 | auto |
| FR-005 | TC-012, TC-013, TC-015 | auto |
| FR-006 | TC-023, TC-024 | auto |
| FR-007 | TC-021, TC-022 | auto |
| FR-008 | TC-025, TC-026 | auto / manual |
| FR-009 | TC-032, TC-033 | auto |
| FR-010 | TC-027, TC-028 | auto |
| FR-011 | TC-027, TC-029, TC-030, TC-031 | auto |
| FR-012 | TC-010, TC-016, TC-017, TC-018, TC-019 | auto |
| FR-013 | TC-020, TC-021 | auto |

No ⚠ MISSING entries: every FR has at least one case with an observable pass
condition. The sole manual-judgment case is TC-026 (documentation review);
its spot-check list makes it mechanical enough to script later if desired.
