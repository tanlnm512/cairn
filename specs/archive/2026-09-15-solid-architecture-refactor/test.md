# Test: solid-architecture-refactor

**Baseline**: 06c977da3b0f078169d82fd9ac20a1e3ca89f66e (matches survey.md's baseline)
**Inputs read**: `spec.md` (user stories, acceptance criteria, FRs) and `survey.md` (FR
status only, to ground regression pass conditions in existing verify commands).
`tech-spec.md` and `plan.md` were not read — test cases below derive from the promise
in spec.md, not from any chosen implementation, per the QA agent's implementation-blindness
mandate.

## Method note on structural TCs

Several FRs (FR-001, FR-003, FR-005, FR-007, FR-015) describe an internal
composition/seam requirement rather than a change in end-user-visible behavior alone —
this is inherent to an architecture refactor spec, and spec.md's own "Business value"
section names "reduced dispatch/presentation responsibility in the identified central
modules" as a measured success criterion. Where an FR's business promise ("adding a
parser/backend/route/check should not require editing a central module") has no
external observable effect by itself, the pass condition below checks for the
**absence of the current baseline anti-pattern** (an inline branch chain, an inline
SQL write, an inline route handler) that survey.md already pinned with exact evidence
— never a guess at a future class/function name, since that would leak an
implementation choice this suite must stay blind to. Where even an absence-check isn't
safely nameable without presuming a design, the TC is marked **Manual** (a reviewer
checks the promise directly) rather than invented into a false-Auto command.

---

## TC-001 — Parser selection and construction stay behind one resolution point

- **Story**: US1 · **Traces to**: FR-001, FR-003

**Given** the current set of supported languages and a repository containing at least
one file per supported language.
**When** the repository is indexed.
**Then** (a) the parsed symbols, imports, and edges for every file match the
pre-refactor baseline exactly, and (b) the graph builder no longer constructs
concrete parser classes itself (no per-language lazy-import branch chain remains in
the file that builds the graph).

**Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_parsers_language_adapter.py -q && rg -q '^(from \.\.parsers\.factory import get_parser|from cairn\.parsers\.factory import get_parser)$' src/cairn/graph/builder.py && rg -q 'get_parser\s*\(' src/cairn/graph/builder.py && ! rg -q '\b[A-Z][A-Za-z0-9_]*Parser\s*\(' src/cairn/graph/builder.py`
- Parser tests must pass; the current baseline is `9 passed`.
- The factory import and builder call must both match.
- The negated constructor scan must exit `0` only when no concrete parser
  class is constructed in the builder.

**Type**: Auto

---

## TC-002 — Repeated resolution of the same language returns the cached instance

- **Story**: US1 · **Traces to**: FR-002

**Given** a language that has already been resolved once in the current process.
**When** the same language is resolved a second time.
**Then** the second resolution returns the identical parser object as the first — no
new instance is constructed — for the life of the process.

**Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_parser_factory_cache.py -q`
- New regression test (none exists today per survey.md's FR-002 gap note): resolve
  the same language twice and assert `is` identity between the two results, for at
  least two distinct supported languages.

**Type**: Auto

---

## TC-003 — Unsupported language yields no parser and indexing continues (boundary)

- **Story**: US1 · **Traces to**: FR-003

**Given** a file whose language is not in the supported set.
**When** indexing requests a parser for that file.
**Then** indexing receives no parser for that file, does not crash, and continues to
index the remaining supported files in the same run exactly as before.

**Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_build_runs.py -k "unsupported" -q`
- New regression test (probe the `-k` word once added: this suite uses pytest, whose
  `-k` is substring/id matching, not unittest's case-sensitive method-name matching —
  still, confirm the word matches the added test's name before relying on it).

**Type**: Auto

---

## TC-004 — Concurrent resolution of one language stays consistent (boundary)

- **Story**: US1 · **Traces to**: FR-002

**Given** several indexing workers start at the same time and all need the parser for
the same language.
**When** they each request that language's parser concurrently.
**Then** every worker ends up using a parser instance with identical behavior (same
parsed output for the same input) and no worker crashes or receives a partially
constructed parser.

**Pass condition**: Human observation — index a bounded multi-file, single-language
fixture repository using cairn's normal concurrent/incremental indexing path with
several workers active at once; confirm the run completes without error and produces
the same symbol/edge counts as a single-worker run of the same fixture.

**Type**: Manual

---

## TC-005 — Embedding dispatch through the backend contract preserves vectors per backend

- **Story**: US2 · **Traces to**: FR-004

**Given** each backend cairn currently supports (hash, local, openai, server) is
configured in turn.
**When** the same text is embedded under each configuration.
**Then** each backend returns vectors with the same format, dimensionality, and model
identity as the current baseline for that backend.

**Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_embedding_model.py tests/test_embed_ladder.py tests/test_embeddings_freshness.py -q && uv run python -c "import ast,pathlib; b=ast.parse(pathlib.Path('src/cairn/graph/embed_backends.py').read_text()); e=ast.parse(pathlib.Path('src/cairn/graph/embeddings.py').read_text()); assert any(isinstance(n,ast.ClassDef) and n.name=='EmbeddingBackend' for n in b.body); assert any(isinstance(n,(ast.Assign,ast.AnnAssign)) and 'EMBEDDING_BACKENDS' in ast.unparse(n) for n in b.body); assert all(not (isinstance(n,ast.ImportFrom) and (n.module or '').split('.')[-1]=='embeddings') and not (isinstance(n,ast.Import) and any(a.name.split('.')[-1]=='embeddings' for a in n.names)) for n in ast.walk(b)); f=next(n for n in ast.walk(e) if isinstance(n,ast.FunctionDef) and n.name=='_embed'); assert any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='resolve_embedding_backend' for n in ast.walk(f))"`
- Behavioral tests must pass first.
- The structural command must find `EmbeddingBackend` and the
  `EMBEDDING_BACKENDS` registry, find no import of `embeddings.py`, and find
  a registry call inside `_embed`.

**Type**: Auto

---

## TC-006 — No hardcoded backend branch remains in the embedding entry point

- **Story**: US2 · **Traces to**: FR-005

**Given** the embedding entry point that dispatches a request to the effective
backend.
**When** the refactor is complete.
**Then** backend selection happens through one generic (registry-driven) lookup — the
repeated `if backend == "..."` / `elif backend == "..."` string-branch chain that
today appears independently in multiple functions is gone from the entry point.

- **Pass condition**: `! rg -q "if backend ==|elif backend ==" src/cairn/graph/embeddings.py`
- Must exit `0` only because there are no matches (baseline: 9 matches across `current_model`, `embeddings_available`,
  and `_embed`, per survey.md's FR-005 evidence).

**Type**: Auto

---

## TC-007 — Resolution order, fallback, cache invalidation, and model identity preserved

- **Story**: US2 · **Traces to**: FR-006

**Given** the documented resolution order (env var → persisted config → default) and
a backend whose dependency becomes unavailable (e.g. `local` without
`sentence_transformers` installed).
**When** an embedding is requested and the backend cache is later reset.
**Then** the same fallback backend is chosen as today, the cache-reset call re-derives
the effective backend on the next call, and vector format/dimensions/model identity
for the resolved backend are unchanged.

**Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_embed_ladder.py tests/test_embedding_backend_quality.py tests/test_semantic_unavailable.py -q`

**Type**: Auto

---

## TC-008 — Empty and large text batches embed without crashing or format drift (boundary)

- **Story**: US2 · **Traces to**: FR-006

**Given** (a) an empty list of texts to embed, and (b) a batch large enough to span
multiple internal chunks under the current batching behavior.
**When** each is submitted to the embedding entry point.
**Then** the empty batch returns an empty, correctly-typed result (no crash), and the
large batch returns the same vector count, format, and dimensions per item as before,
within the same practical time bound.

**Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_embed_flush_stalled.py tests/test_embeddings_mv.py -q`

**Type**: Auto

---

## TC-009 — Every existing dashboard route preserves its contract

- **Story**: US3 · **Traces to**: FR-008

**Given** the full set of dashboard routes that exist today (landing, workspaces,
projects, graph + graph candidates/suggest/neighbors/inspect, palette results, health,
history, tokens, chains, memory, tasks, knowledge catalog/doc/graph, wiki + wiki page,
settings + settings save, embeddings status, database).
**When** each route is requested with its existing method.
**Then** its URL, method, route name, response shape, rendering, and status code are
unchanged from today.

**Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_dashboard_app.py -q`
- Baseline (per survey.md): 157 passed.

**Type**: Auto

---

## TC-010 — App factory assembles controllers instead of inlining request handling

- **Story**: US3 · **Traces to**: FR-007

**Given** the dashboard application factory that builds the app object.
**When** the refactor is complete.
**Then** the factory function explicitly registers each controller route table
and defines zero route handlers itself.

**Pass condition**: `uv run python -c "import ast,pathlib; s=pathlib.Path('src/cairn/dashboard/app.py').read_text(); t=ast.parse(s); funcs={n.name for n in ast.walk(t) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}; wired=set(); [wired.add(x.id) for c in ast.walk(t) if isinstance(c,ast.Call) and ((isinstance(c.func,ast.Name) and c.func.id=='Route') or (isinstance(c.func,ast.Attribute) and c.func.attr=='add_route')) for x in ([c.args[1]] if len(c.args)>1 else [])+[k.value for k in c.keywords if k.arg=='endpoint'] if isinstance(x,ast.Name)]; routed=[n for n in ast.walk(t) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and (n.name in wired or any(isinstance(d,(ast.Attribute,ast.Name)) and getattr(d,'attr',getattr(d,'id','')) in {'get','post','put','delete','route','websocket'} for d in n.decorator_list))]; assert not routed, routed; calls={c.func.value.id for c in ast.walk(t) if isinstance(c,ast.Call) and isinstance(c.func,ast.Attribute) and c.func.attr=='register' and isinstance(c.func.value,ast.Name)}; assert {'core','graph','history','memory','knowledge','wiki','settings'} <= calls, calls"`
- The AST scan must find zero definitions wired to routes or decorated with
  route HTTP methods in the app module.
- Explicit AST-verified registration calls must be present for all seven
  controller modules.

**Type**: Auto

---

## TC-011 — Unknown dashboard route preserves not-found semantics (boundary)

- **Story**: US3 · **Traces to**: FR-008

**Given** a URL path that does not match any registered dashboard route.
**When** it is requested.
**Then** the response status and body shape match today's not-found behavior exactly
(no new 500, no route accidentally now matching).

**Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_dashboard_app.py -k "not_found or 404" -q`
- Probe the `-k` word against the actual test names once known; if no existing case
  covers this, add one (pytest `-k` is substring matching, not case-sensitive
  unittest method-name matching, but still confirm it selects >0 tests before relying
  on it).

**Type**: Auto

---

## TC-012 — Semantic search stays output-equivalent when composed of explicit stages

- **Story**: US4 · **Traces to**: FR-009

**Given** an existing query and configuration exercising the full pipeline (retrieval,
fusion, rerank, enrichment).
**When** semantic search executes end to end.
**Then** result fields, ranking order, provenance values, and telemetry events emitted
match the current baseline exactly.

**Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_semantic_events.py tests/test_semantic_enrichment.py -q`

**Type**: Auto

---

## TC-013 — Semantic search is composed from named, independently maintainable stages

- **Story**: US4 · **Traces to**: FR-009

**Given** the query, retrieval, fusion, rerank, enrichment, telemetry, and
result-assembly concerns the spec names.
**When** a maintainer needs to change one concern (e.g. reranking).
**Then** they can find and change it as one explicit, independently identifiable
stage, without editing a single large branching function that also implements the
other concerns.

**Pass condition**: Human observation — reviewer confirms semantic search's
implementation calls out to separately named stage units in sequence (not one
function whose body branches on feature flags for every concern at once), matching
the composition spec.md's US4 promises.

**Type**: Manual

---

## TC-014 — A failed stage preserves the existing degradation path and telemetry

- **Story**: US4 · **Traces to**: FR-010

**Given** one retrieval stage (e.g. the embedding backend) is made unavailable.
**When** semantic search runs anyway.
**Then** the existing degradation marker (e.g. `"degraded": "embedding-backend"`),
provenance value, and the corresponding telemetry event are emitted exactly as they
are today.

**Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_semantic_unavailable.py -q`

**Type**: Auto

---

## TC-015 — Multiple stages unavailable at once still degrades gracefully (boundary)

- **Story**: US4 · **Traces to**: FR-010

**Given** more than one retrieval stage is actually configured unavailable at
the same time (e.g. embedding backend *and* reranker both unavailable).
**When** semantic search runs.
**Then** it returns a valid, non-crashing result set. For every stage that is
actually configured unavailable, its existing result marker/provenance and
telemetry event match the corresponding single-stage degraded run exactly. No
combined or aggregate degradation marker is required or invented.

**Pass condition**: Human observation — in a bounded test workspace, first run
and record each configured unavailable stage separately, then make that same
set unavailable together. Run semantic search, confirm it completes without
raising, and compare each stage's existing markers/provenance and telemetry to
its single-stage record; confirm no event is dropped or duplicated and do not
require a synthetic combined marker.

**Type**: Manual

---

## TC-016 — Empty query string is handled unchanged (boundary)

- **Story**: US4 · **Traces to**: FR-009

**Given** an empty (or whitespace-only) query string.
**When** semantic search is invoked with it.
**Then** the response shape (empty-result semantics, status, telemetry) matches
today's baseline for an empty query — no crash, no new error class.

**Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_semantic_events.py -k "empty" -q`
- Probe the `-k` word against actual test names; add a case if none exists today.

**Type**: Auto

---

## TC-017 — Both LLM backends stop within their declared bounded wait

- **Story**: US5 · **Traces to**: FR-011

**Given** each LLM backend (file-queue and subprocess) configured with a short bounded
timeout/max-wait.
**When** synthesis or extraction is requested against a source that never responds in
time.
**Then** each backend stops and returns/raises within its declared bound — neither
backend hangs past its configured wait.

**Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_llm_backend_timeout.py -q`
- New regression test (no existing test file exercises `FileQueueBackend`/
  `SubprocessBackend` timeout bounds directly — confirmed absent from `tests/` this
  session). Must complete well under the pass-condition runtime cap by using a short
  configured bound (seconds), not the production default.

**Type**: Auto

---

## TC-018 — Malformed JSON extraction records are skipped consistently by both backends

- **Story**: US5 · **Traces to**: FR-012

**Given** raw extraction output from either LLM backend that contains one well-formed
JSON record and one malformed (`{`-prefixed but invalid) line.
**When** either backend parses the output into extraction records.
**Then** both backends return only the well-formed record and skip the malformed one
— neither backend raises an uncaught parsing exception.

**Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_llm_extract_malformed.py -q`
- New regression test closing the baseline inconsistency survey.md found (file-queue
  backend already skips malformed lines; subprocess backend today raises
  `json.JSONDecodeError` uncaught — this TC pins the two backends to the same
  behavior going forward).

**Type**: Auto

---

## TC-019 — Fully empty/non-JSON extraction output returns an empty list (boundary)

- **Story**: US5 · **Traces to**: FR-012

**Given** raw extraction output that is empty or contains no `{`-prefixed lines at
all.
**When** either backend parses it.
**Then** both backends return an empty list without raising.

**Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_llm_extract_malformed.py -k "empty" -q`
- Same new test module as TC-018, covering the empty-input case; probe the `-k` word
  once the test is named.

**Type**: Auto

---

## TC-020 — Existing system commands preserve names, output, exit, and redaction

- **Story**: US6 · **Traces to**: FR-014

**Given** every existing `cairn` system command (doctor, status, metrics, sync,
reporting, etc.) in both human and `--json` modes.
**When** each command runs.
**Then** its command name, human-readable output semantics, JSON output contract,
exit code, and redaction of sensitive values are unchanged from today.

**Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_cli_metrics.py tests/test_cli_smoke.py tests/test_doctor.py tests/test_metrics_extensions.py tests/test_report.py tests/test_redaction_chokepoints.py tests/test_status_parse_errors.py tests/test_status_resource_health.py -q`
- This is the complete implemented system-command test surface: metrics,
  command smoke, doctor, extended metrics, report, redaction, status parse
  errors, and status resource health.
- Current baseline: `158 passed`.

**Type**: Auto

---

## TC-021 — Operational CLI is organized by command family with registered health checks

- **Story**: US6 · **Traces to**: FR-013

**Given** the operational CLI's doctor/status/metrics/sync/reporting commands.
**When** a maintainer adds a new doctor health check.
**Then** they register it (e.g. add one item to a check registry) rather than editing
one large central conditional function, and the commands themselves live grouped by
family rather than as 30+ flat functions in a single file.

**Pass condition**: Human observation — reviewer confirms system CLI commands are
grouped by command family (rather than one flat file) and that the doctor's checks
are driven by a registered list of checks rather than by direct inline function calls
baked into one function body, matching survey.md's FR-013 baseline evidence (today:
one 2145-line file, no check registry).

**Type**: Manual

---

## TC-022 — JSON output with zero findings stays well-formed (boundary)

- **Story**: US6 · **Traces to**: FR-014

**Given** a system command run in `--json` mode against a project/workspace with no
findings to report (e.g. doctor run on a fully healthy project).
**When** the command completes.
**Then** it still emits well-formed JSON matching the documented contract (an empty or
all-PASS result list), not an error or malformed payload.

**Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_doctor.py -k "json" -q`

**Type**: Auto

---

## TC-023 — Full and incremental index builds preserve stored values, IDs, and transactions

- **Story**: US7 · **Traces to**: FR-016

**Given** a repository indexed first as a full build and then re-indexed
incrementally after a small change.
**When** both builds run.
**Then** the stored file/symbol/import/edge rows, generated IDs, transaction
boundaries, batch commit behavior, and crash-recovery semantics match the current
baseline exactly.

**Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_build_graph_connection_cleanup.py tests/test_build_graph_decomposition.py tests/test_build_graph_periodic_commit.py tests/test_build_inmemory.py tests/test_build_runs.py -q`

**Type**: Auto

---

## TC-024 — Persistence is isolated behind a repository seam (no raw SQL left in the builder)

- **Story**: US7 · **Traces to**: FR-015

**Given** the graph builder's file/symbol/import/edge writes.
**When** the refactor is complete.
**Then** no raw `INSERT INTO files|symbols|imports|edges` SQL statement remains
written directly inside the graph-building orchestration code — those writes are
reached through a persistence seam instead.

**Pass condition**: `! rg -q 'INSERT INTO[[:space:]]+(files|symbols|imports|edges)\b' src/cairn/graph/builder.py && rg -q 'repository\s*=\s*GraphRepository\s*\(' src/cairn/graph/builder.py && rg -q '\.insert_files\s*\(' src/cairn/graph/builder.py && rg -q '\.insert_symbols\s*\(' src/cairn/graph/builder.py && rg -q '\.insert_imports\s*\(' src/cairn/graph/builder.py && rg -q '\.insert_edges\s*\(' src/cairn/graph/builder.py`
- The negated raw-SQL scan must exit `0` only when there are no matches
  (baseline: 5 matches, per survey.md's FR-015 evidence).
- The positive scans must find repository construction and all four table
  insert methods in use.

**Type**: Auto

---

## TC-025 — Crash mid-batch preserves recovery semantics; reindexing is idempotent (boundary)

- **Story**: US7 · **Traces to**: FR-016

**Given** an index build that is interrupted partway through a batch commit.
**When** the same repository is indexed again afterward.
**Then** the build recovers to a consistent logical graph state: the relative
file paths, symbol identities/names/kinds/locations, import source-target
relations, edge types/endpoints/payloads, and row cardinality match a clean
single-pass build, with no duplicate or orphaned rows. Generated IDs need not
be equal after recovery, but every internal reference must resolve
consistently.

**Pass condition**: Human observation — in a bounded fixture repository, record
the clean build's logical projection excluding generated IDs. Interrupt a build
mid-batch, re-run the same index build, and compare the same projection against
the clean baseline. Confirm referential consistency, unchanged cardinality, and
no duplicate or orphaned rows; do not require UUID equality.

**Type**: Manual

---

## TC-026 — No new runtime dependency is introduced (standing regression guard)

- **Story**: Standing (cross-cutting) · **Traces to**: FR-017

**Given** the dependency list declared for the project today (tree-sitter grammars +
core, click, pyyaml, mcp, jinja2, pydantic, pathspec, packaging, sqlite-vec, numpy,
rich, questionary — per survey.md's FR-017 baseline).
**When** the refactor is complete.
**Then** no package name is added to the declared runtime `dependencies` array
that was not already present at baseline (version bumps of existing packages are
fine; new package names are not). Optional, development, and test dependency
groups are outside this rule.

**Pass condition**: Human observation on `git diff 06c977da3b0f078169d82fd9ac20a1e3ca89f66e..HEAD -- pyproject.toml` — reviewer confirms every added line inside the `dependencies` array is a version/formatting change to an already-listed package, never a new package name.

**Type**: Manual

---

## TC-027 — No new static-quality finding vs. baseline (standing regression guard)

- **Story**: Standing (cross-cutting) · **Traces to**: FR-018

**Given** the project's existing hard CI gates (mypy at default settings, ruff with
the pyflakes rule set).
**When** the refactor is complete.
**Then** both gates still run clean exactly as they do at baseline — no new mypy
error and no new ruff (`F`-rule) finding is introduced by the refactor.

**Pass condition**: `uv run mypy --ignore-missing-imports src && uv run ruff check src && uv run ruff check src --select C901,PLR0912,PLR0913,PLR0915 --output-format json | uv run python -c "import collections,json,sys; counts=collections.Counter(item['code'] for item in json.load(sys.stdin)); limits={'C901':88,'PLR0912':52,'PLR0913':67,'PLR0915':37}; assert all(counts[rule] <= limit for rule,limit in limits.items()), counts"`
- Mypy and the existing Ruff gate must exit `0`, matching the baseline clean
  state.
- Ruff must evaluate exactly `C901`, `PLR0912`, `PLR0913`, and `PLR0915`.
- The JSON gate must cap per-category findings at complexity `88`, branches
  `52`, statements `37`, and arguments `67`.

**Type**: Auto

---

## TC-028 — No new module import cycle is introduced (boundary, standing regression guard)

- **Story**: Standing (cross-cutting) · **Traces to**: FR-018

**Given** the module import graph as it exists today.
**When** the refactor's new seams (factory, registry, controllers, repository,
stages) are wired together.
**Then** no two modules import each other transitively in a cycle that did not exist
before — every new module boundary keeps a one-directional dependency.

**Pass condition**: Human observation — reviewer traces the import direction of each
newly introduced module boundary (factory-to-parsers, registry-to-backends,
dashboard-app-to-controllers, graph-builder-to-repository,
semantic-search-to-search-pipeline/stages)
and confirms none of them is imported back by the module it depends on. Survey.md
notes the repo has no dedicated cycle-detection tool configured today — if one is
added as part of the refactor, its clean run becomes the Auto replacement for this TC.

**Type**: Manual

---

## Coverage matrix

| FR | Requirement (short) | TCs | Type(s) |
|----|----------------------|-----|---------|
| FR-001 | Single parser factory | TC-001 | Auto |
| FR-002 | Cached parser instance on repeat resolution | TC-002, TC-004 | Auto, Manual |
| FR-003 | Builder delegates to factory, not construction | TC-001, TC-003 | Auto, Auto |
| FR-004 | Embedding dispatch through backend contract | TC-005 | Auto |
| FR-005 | Backend resolved via registry, no hardcoded branches | TC-006 | Auto |
| FR-006 | Preserve resolution order/fallback/cache/format/dims/identity | TC-007, TC-008 | Auto, Auto |
| FR-007 | App factory assembles controllers, no inline behavior | TC-010 | Auto |
| FR-008 | Preserve dashboard route contracts | TC-009, TC-011 | Auto, Auto |
| FR-009 | Semantic search composed of explicit stages | TC-012, TC-013, TC-016 | Auto, Manual, Auto |
| FR-010 | Preserve degradation path/fields/provenance/telemetry | TC-014, TC-015 | Auto, Manual |
| FR-011 | LLM contract with bounded timeout | TC-017 | Auto |
| FR-012 | Both backends skip malformed JSON consistently | TC-018, TC-019 | Auto, Auto |
| FR-013 | CLI organized by command family + registered health checks | TC-021 | Manual |
| FR-014 | Preserve command names/output/exit/redaction | TC-020, TC-022 | Auto, Auto |
| FR-015 | Persistence isolated behind repository seams | TC-024 | Auto |
| FR-016 | Preserve stored values/IDs/transactions/batches/recovery | TC-023, TC-025 | Auto, Manual |
| FR-017 | No new runtime dependency | TC-026 | Manual |
| FR-018 | No new import cycle / no new static-complexity finding | TC-027, TC-028 | Auto, Manual |

**Untestable FRs**: none — every FR has at least one observable pass condition or a
scoped manual verification. Note the method caveat above: FR-001, FR-003, FR-005,
FR-007, FR-009, FR-013, FR-015, FR-017, and FR-018 partly rely on absence-of-baseline-
anti-pattern checks or reviewer judgment rather than pure end-to-end behavioral
commands, since they are internal-seam/composition requirements by nature (a genuine
spec property of an architecture refactor, not a testability gap this suite is hiding).

## Story coverage check

- US1 (Parser extension): TC-001, TC-002, TC-003, TC-004
- US2 (Embedding provider isolation): TC-005, TC-006, TC-007, TC-008
- US3 (Dashboard modularity): TC-009, TC-010, TC-011
- US4 (Retrieval pipeline isolation): TC-012, TC-013, TC-014, TC-015, TC-016
- US5 (LLM backend substitution): TC-017, TC-018, TC-019
- US6 (Operational command modularity): TC-020, TC-021, TC-022
- US7 (Graph persistence separation): TC-023, TC-024, TC-025
- Standing/cross-cutting (FR-017, FR-018): TC-026, TC-027, TC-028

Every user story has at least one TC; no story is left without coverage.
