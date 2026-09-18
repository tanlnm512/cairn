# Test Cases: cross-repo-federation

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-17
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.

Terms used across cases: a *store* is a workspace indexed on this machine and
entered in the machine-wide registry; *federated search* is the new search
surface (agent-facing tool and CLI command) defined by FR-001. Auto pass
conditions run the acceptance suite the implementation wave delivers; every
fixture is a small set of temp stores, sized so each command finishes in
seconds. The standing at-scale check for the same observables is running the
same queries across the machine's full registry — human-run, not part of the
audit.

## TC-001 — One search answers across every registered repo
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** several stores are registered and indexed, each containing code
  relevant to the same topic
- **When** a federated search runs for that topic
- **Then** one merged ranking returns, with relevant hits from every store,
  each hit naming the repo it came from and carrying a score
- **Pass condition**: `/Users/tanle/Projects/cairn/.venv/bin/python -m unittest tests.test_federation.FederatedSearchTests.test_merged_hits_carry_repo_attribution`

## TC-002 — A store without embeddings still contributes
- **Story**: US1 · **Traces to**: FR-001
- **Given** one store indexed with embeddings and a second store indexed with
  a lexical index only, both registered
- **When** a federated search runs for a topic both stores cover
- **Then** the lexical-only store's relevant hits appear in the merged
  results with attribution — the store is served, not dropped
- **Pass condition**: `/Users/tanle/Projects/cairn/.venv/bin/python -m unittest tests.test_federation.FederatedSearchTests.test_lexical_only_store_still_contributes`

## TC-003 — Federated search is reachable from both surfaces
- **Story**: US1 · **Traces to**: FR-001
- **Given** registered, indexed stores; an agent client and a terminal
- **When** the agent lists its available search tools, and a user runs the
  federated search CLI command with the same query
- **Then** federated search is offered as an agent tool, and the CLI command
  returns the same merged, attributed results for the same query
- **Pass condition**: `/Users/tanle/Projects/cairn/.venv/bin/python -m unittest tests.test_federation.FederatedSearchTests.test_tool_and_cli_surfaces_agree`

## TC-004 — A shared embedding backend is an option where compatible
- **Story**: US1 · **Traces to**: FR-002
- **Given** at least two registered stores whose embedding settings are
  compatible with one shared backend
- **When** a federated search runs with the shared-backend option in use
- **Then** results are correct and attributed exactly as when each store
  serves its own — sharing is an option and never changes what callers get
- **Pass condition**: `/Users/tanle/Projects/cairn/.venv/bin/python -m unittest tests.test_federation.EmbeddingBackendTests.test_shared_backend_option_returns_attributed_results`

## TC-005 — Different backends still merge into one ranking
- **Story**: US1 · **Traces to**: FR-002
- **Given** two registered stores indexed with different embedding backends,
  whose raw scores are not comparable
- **When** a federated search runs for a topic both stores cover
- **Then** a single merged ranking draws on both stores, each store's locally
  best answer surfaces near the top, and ordering reflects fused ranks rather
  than mixed raw scores
- **Pass condition**: human observation — index two stores with different
  embedding backends, run one federated search on a topic both cover, and
  confirm both stores' best matches appear attributed in the one merged list.

## TC-006 — One question, all repos, attributed answer
- **Story**: US2 · **Traces to**: FR-003, AC1
- **Given** at least two registered stores, each holding distinct context
  relevant to the question
- **When** `/Users/tanle/Projects/cairn/.venv/bin/cairn ask --all-repos "<question>"` runs
- **Then** the composed answer draws on both stores and every piece of
  context it uses names the repo it came from
- **Pass condition**: `/Users/tanle/Projects/cairn/.venv/bin/python -m unittest tests.test_federation.FederatedAskTests.test_ask_all_repos_attributes_context_per_repo`

## TC-007 — A missing store is named, not skipped
- **Story**: US1 · **Traces to**: FR-004
- **Given** three registered stores, one of which has had its data deleted
- **When** a federated search runs
- **Then** the healthy stores' results return, the missing store is
  explicitly named as unavailable, and the agent-consumable output carries
  the same store report as the human-readable one
- **Pass condition**: `/Users/tanle/Projects/cairn/.venv/bin/python -m unittest tests.test_federation.UnavailableStoreTests.test_missing_store_is_named`

## TC-008 — An unindexed store is named, not skipped
- **Story**: US1 · **Traces to**: FR-004
- **Given** a registered store that has never been indexed, alongside healthy
  registered stores
- **When** a federated search runs
- **Then** healthy results return and the unindexed store is explicitly named
  as unavailable
- **Pass condition**: `/Users/tanle/Projects/cairn/.venv/bin/python -m unittest tests.test_federation.UnavailableStoreTests.test_unindexed_store_is_named`

## TC-009 — A locked store is named, not skipped
- **Story**: US1 · **Traces to**: FR-004
- **Given** a store held by another operation while the query runs, alongside
  healthy registered stores
- **When** a federated search runs
- **Then** the locked store is explicitly named as unavailable and the rest
  of the federation answers
- **Pass condition**: `/Users/tanle/Projects/cairn/.venv/bin/python -m unittest tests.test_federation.UnavailableStoreTests.test_locked_store_is_named`

## TC-010 — No registered stores gets an explicit answer
- **Story**: US1 · **Traces to**: FR-004
- **Given** a machine with no stores registered
- **When** a federated search or an all-repos ask runs
- **Then** the response states plainly that no stores are registered — not a
  crash, not a silent empty success
- **Pass condition**: `/Users/tanle/Projects/cairn/.venv/bin/python -m unittest tests.test_federation.UnavailableStoreTests.test_empty_registry_reports_clearly`

## TC-011 — Existing single-repo behavior never changes (standing guard)
- **Story**: US1, US2 · **Traces to**: FR-005
- **Given** many stores registered on the machine
- **When** any existing command runs without a federation option — the plain
  ask, the existing search-family commands, the structural lookups
- **Then** behavior and results match the single-repo contract exactly; in
  particular nothing from another registered repo ever appears in a default
  path
- **Pass condition**: `/Users/tanle/Projects/cairn/.venv/bin/python -m unittest tests.test_federation.SingleStoreBaselineTests.test_default_commands_stay_single_store`

## TC-012 — Empty questions are rejected before any store is queried
- **Story**: US2 · **Traces to**: FR-003
- **Given** registered, indexed stores
- **When** an all-repos ask runs with an empty or whitespace-only question
  (likewise a federated search with an empty query)
- **Then** a clear input error returns before any store is touched, and the
  failure is routable from the command's exit status alone
- **Pass condition**: `/Users/tanle/Projects/cairn/.venv/bin/python -m unittest tests.test_federation.FederatedAskTests.test_blank_input_rejected_before_querying`

## Coverage matrix
<!-- Every FR appears; `check.py` fails an FR with no TC. -->
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001      | TC-001, TC-002, TC-003 | auto |
| FR-002      | TC-004, TC-005 | auto, manual |
| FR-003      | TC-006, TC-012 | auto |
| FR-004      | TC-007, TC-008, TC-009, TC-010 | auto |
| FR-005      | TC-011 | auto (standing guard) |
