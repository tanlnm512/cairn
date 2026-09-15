# Survey: solid-architecture-refactor

**Baseline**: 0.20.2 @ 06c977da3b0f078169d82fd9ac20a1e3ca89f66e
Context read first: `specs/context/structure.md` (baseline 0.16.0 @ fe7a7f09, refreshed dc9882b/0.20.0),
`specs/context/tech.md`. This is not the repo's first spec — context files not rewritten.

All evidence is from this session's `rg`/`Read` output against the current working tree
(no uncommitted changes at survey time other than `specs/INDEX.md` and this spec dir).

---

## FR-001 — single parser factory for built-in language parsers

**Evidence**:
- `src/cairn/graph/builder.py:45-124` — `get_parser(language)` is a free function
  living in `builder.py` itself (not a dedicated factory module). It holds two
  module-level dicts, `PARSERS: Dict[str, BaseParser] = {}` (builder.py:45, unused —
  never written to) and `_parser_instances: Dict[str, BaseParser] = {}` (builder.py:46),
  and does the construction inline via a 13-armed `if/elif` chain of lazy imports
  (builder.py:68-123), e.g. `from ..parsers.java import JavaParser; _parser_instances["java"] = JavaParser()`.
- Separately, `src/cairn/parsers/_registry.py:29-34` has `get_parser(language) -> Parser`
  (`@functools.lru_cache(maxsize=16)`), but this returns a raw `tree_sitter.Parser`
  (the grammar-level object), not a `BaseParser` subclass instance — a different
  abstraction level from what FR-001 targets (the per-language `BaseParser` adapters
  in `src/cairn/parsers/{python_parser,java,swift,...}.py`).
- No module named `factory.py` or class named `ParserFactory` exists under `src/cairn/parsers/`:
  `rg -n "class \w*Parser" src/cairn/parsers/*.py` lists 17 parser classes
  (base.py:85 `BaseParser`, base.py:103 `TreeSitterParserBase`, plus 15 concrete
  language parsers) and no factory type.

**Status**: TODO — construction of `BaseParser` instances is inline in `graph/builder.py`,
not behind a single factory.

**Verify**: `rg -n "class \w*Parser" src/cairn/parsers/*.py` (run this session, 17 hits,
no `Factory` class); `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_parsers_language_adapter.py -q` → 9 passed.

**Gap**: no factory abstraction exists yet for `BaseParser` construction; `PARSERS` dict
declared at builder.py:45 is dead code (never populated or read — only `_parser_instances` is used).

---

## FR-002 — cached parser instance on repeated resolution

**Evidence**:
- `src/cairn/graph/builder.py:49-50`: `def get_parser(language): if language not in _parser_instances: ...` —
  module-level dict memoizes one instance per language for the process lifetime; line 124
  `return _parser_instances.get(language)` returns the same object on repeat calls.
- Tree-sitter-level caching already formalized: `src/cairn/parsers/_registry.py:29`
  `@functools.lru_cache(maxsize=16)` on `get_parser(language) -> Parser`.

**Status**: DONE (behavior already holds) — but the caching mechanism lives in the same
ad hoc dict as FR-001's construction logic, not inside a factory abstraction.

**Verify**: `rg -n "_parser_instances" src/cairn/graph/builder.py` → builder.py:46,50,71,75,79,83,87,91,95,99,103,107,111,115,119,121,124 (session grep); no automated test found asserting identity across two `get_parser` calls — `unknown — verify` whether a same-instance assertion exists (`rg -n "get_parser" tests/` returned no hits this session).

**Gap**: no test pins the "same cached instance" contract explicitly at the `BaseParser` level (only informally true via the dict).

---

## FR-003 — graph builder delegates parser selection to the factory

**Evidence**:
- `src/cairn/graph/builder.py:798`: `parser = get_parser(language)` — calls the local
  function defined in the same module (builder.py:49), not an external factory.
- `src/cairn/graph/incremental.py:209`: `parser = get_parser(language)` — imports and
  calls the same `builder.get_parser`.
- `rg -n "get_parser\(" src/cairn/ -g '*.py'` (excluding the `def`) → exactly these two
  call sites: builder.py:798, incremental.py:209.

**Status**: TODO — the builder does not "delegate to a parser factory instead of
constructing concrete parsers"; it constructs the concrete parser classes itself inside
`get_parser`, which is co-located in `builder.py`.

**Verify**: `rg -n "get_parser\(" src/cairn/ -g '*.py'` (session run, 2 non-def hits above).

**Gap**: no separation between "parser selection" and "parser construction" — same function does both, in the same file as the graph builder.

---

## FR-004 — embedding requests dispatched through a backend contract

**Evidence**:
- `src/cairn/graph/embeddings.py:1227-1236`:
  ```
  def _embed(texts: Sequence[str]) -> Tuple[List[bytes], int]:
      """Dispatch to the effective backend (after fallback). Returns (blobs, dim)."""
      backend = _effective_backend()
      if backend == "hash":
          return _embed_hash(texts)
      if backend == "openai":
          return _embed_openai(texts)
      if backend == "server":
          return _embed_server(texts)
      return _embed_local(texts)
  ```
  This is a plain function with an `if/elif` chain over string backend names, not a
  Protocol/ABC contract with pluggable backend implementations.
- No `class.*Backend` or `Protocol` definition found in `src/cairn/graph/embeddings.py`:
  `rg -n "class .*Backend|Protocol" src/cairn/graph/embeddings.py` → no matches this session.

**Status**: TODO — no embedding backend contract (Protocol/ABC) exists; dispatch is a
hardcoded string-branch function.

**Verify**: `rg -n "class .*Backend|Protocol" src/cairn/graph/embeddings.py` (0 hits, session run).

---

## FR-005 — embedding backend resolved through a registry, no hardcoded branches in entry point

**Evidence**:
- `src/cairn/graph/embeddings.py:362` `_backend_name()`, and dispatch sites at lines
  64, 66, 68 (`current_model`), 105, 107, 109 (`embeddings_available`), 496
  (`install_hint`-adjacent code), 1230, 1232, 1234 (`_embed`) — all are literal
  `if backend == "hash"` / `"openai"` / `"server"` string comparisons, repeated across
  at least 4 functions (`current_model`, `embeddings_available`, one more at :496, `_embed`).
  `rg -n "if backend ==|elif backend =="  src/cairn/graph/embeddings.py` → lines
  64, 66, 68, 105, 107, 496, 1230, 1232, 1234 (session run).
- No registry (dict-of-constructors or entry-point group, contrast with
  `src/cairn/parsers/_registry.py`'s plugin entry-point pattern) exists for embedding
  backends: `rg -n "_BACKENDS|BACKEND_REGISTRY|register_backend" src/cairn/graph/embeddings.py` → no matches.

**Status**: TODO — backend branches are hardcoded and duplicated across multiple
functions in the embedding entry point (`embeddings.py`), not resolved via a registry.

**Verify**: `rg -n "if backend ==|elif backend ==" src/cairn/graph/embeddings.py` (session run, 9 hits above).

---

## FR-006 — preserve resolution order/fallback/cache invalidation/vector format/dimensions/model identity

**Evidence**:
- Resolution order documented at module docstring `src/cairn/graph/embeddings.py:4-11`:
  env `CAIRN_EMBED_BACKEND` (each knob also reads persistent `$CAIRN_HOME/config.json`,
  env wins) → `local` default, `hash` dep-free fallback, `openai`, `server`/`omlx`/`ollama`.
- Fallback: `embeddings_available()` (embeddings.py:94-127) — local backend falls back to
  hash when `sentence_transformers` missing (embeddings.py:120-127), caches the fallback
  verdict into `_EFFECTIVE_BACKEND_CACHE["effective"]` (embeddings.py:126).
- Cache invalidation: `reset_backend_cache()` referenced at embeddings.py:102 docstring
  ("`reset_backend_cache()` invalidates it").
- Vector format: `_floats_to_blob`/`_vec_to_blob` (embeddings.py:1244-1253) — float32
  little-endian BLOB, `DEFAULT_DIM = 256` (embeddings.py:35) for the hash fallback.
- Model identity: `current_model()` (embeddings.py:44-86) stamps rows per backend,
  including the `server/{netloc}/{model}` derivation (embeddings.py:76-80).

**Status**: DONE (baseline behavior exists and is documented) — this FR is a
preservation constraint on a refactor not yet started; current behavior is the
baseline to preserve, confirmed present.

**Verify**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_embedding_model.py tests/test_embed_ladder.py tests/test_embeddings_freshness.py -q` — unknown — verify (not run this session; listed as existing test files under `tests/`, see `ls tests | grep -i embed` session output: test_embed_cli_adopt.py, test_embed_cli_download_model.py, test_embed_cli_server_down.py, test_embed_commit_tracking.py, test_embed_flush_stalled.py, test_embed_ladder.py, test_embedding_backend_quality.py, test_embedding_model.py, test_embeddings_freshness.py, test_embeddings_mv.py, test_ensure_semantic_deps.py, test_memory_embeddings.py, test_semantic_unavailable.py, test_update_path_embedding.py).

**Gap**: none — this is a preservation FR for the refactor's future work; baseline behavior is present as cited.

---

## FR-007 — dashboard app factory assembles controllers without inline request behavior

**Evidence**:
- `src/cairn/dashboard/app.py` is 1520 lines; `create_app(...)` starts at app.py:223 and
  every route handler is a nested function defined *inside* `create_app`, e.g.
  `async def landing(request: Request)` (app.py:403), `def workspaces_overview` (429),
  `def projects` (439), `def graph` (454), `def graph_candidates` (498),
  `def graph_suggest` (508), `def palette_results` (518), `def graph_neighbors` (576),
  `def graph_inspect` (602), `def health` (624), `def history` (643), `def tokens` (689),
  `def chains` (780), `def memory` (813), `def tasks` (838), `def knowledge_catalog` (861),
  `def knowledge_doc` (931), `def knowledge_graph` (979), `def wiki` (1030),
  `def wiki_page` (1098), `def settings` (1212), `async def settings_save` (1219),
  `def embeddings_status` (1374), `def database` (1409) — 30+ handlers, all defined and
  implemented in-line as closures within the one `create_app` factory function
  (`rg -n "@app\.get|@app\.post|@app\.route|def " src/cairn/dashboard/app.py` session output).

**Status**: TODO — request behavior is implemented inline inside the app factory, not
assembled from separate route-controller units.

**Verify**: `rg -n "def create_app" src/cairn/dashboard/app.py` → app.py:223 (session run); `wc -l src/cairn/dashboard/app.py` → 1520.

---

## FR-008 — preserve dashboard route URL/method/name/response shape/rendering/status semantics

**Evidence**: baseline routes and their handlers exist as enumerated under FR-007
(app.py:403-1497, including `missing_db` exception handler at app.py:1497).

**Status**: DONE (baseline present, confirmed passing) — preservation constraint on
future refactor work.

**Verify**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_dashboard_app.py -q` → **157 passed, 1 warning in 13.56s** (run this session; warning is an unrelated `StarletteDeprecationWarning` re: httpx, test_dashboard_app.py:80).

**Gap**: none at baseline; full dashboard behavior-preservation test surface also includes test_dashboard_accessibility.py, test_dashboard_assets.py, test_dashboard_data.py, test_dashboard_export.py, test_dashboard_graph_retheme.py, test_dashboard_htmx_lists.py, test_dashboard_knowledge.py, test_dashboard_knowledge_graph.py, test_dashboard_live_soak.py, test_dashboard_packaging.py, test_dashboard_readonly.py, test_dashboard_restyle.py, test_dashboard_scale.py, test_dashboard_shell.py, test_dashboard_theme.py, test_dashboard_workspaces.py — listed via `ls tests | grep -i dashboard` this session; not all run (only test_dashboard_app.py run) — unknown — verify for the rest.

---

## FR-009 — semantic search composed from explicit query/retrieval/fusion/rerank/enrichment/telemetry/result-assembly stages

**Evidence**:
- `src/cairn/graph/semantic.py` is 1238 lines; `semantic_search(...)` is one function
  starting at semantic.py:495, with a ~100-line docstring (495-593) documenting fusion,
  rerank, confidence-gating, enrichment (`params.enrich`), multivector (`params.multivector`),
  PRF (`params.prf`) — all as flags/branches inside the single function body, which
  lazy-imports the "stage" modules at its top (semantic.py:594-603):
  `from cairn.graph import embeddings as emb`, `from cairn.graph import reranker as rrk`,
  `from cairn.graph import ann_index as ann`, `from cairn.telemetry import emit, ...`.
- The stage-like modules do exist separately: `src/cairn/graph/reranker.py` (`rerank`
  at reranker.py:388, `rerank_enabled`/`reranker_available` at 91/118),
  `src/cairn/graph/query_enrich.py` (`enrich` at query_enrich.py:298),
  `src/cairn/graph/fusion.py` (RRF fusion, present per structure.md's module map) —
  but they are called inline from within `semantic_search`'s body, not composed via an
  explicit pipeline/stage-list construct.

**Status**: PARTIAL — stage logic exists in separate modules (reranker.py, query_enrich.py,
fusion.py, ann_index.py) but is orchestrated by branching/flags inside one 1238-line
function, not an explicit staged pipeline.

**Verify**: `rg -n "def semantic_search|def retrieve|def fuse|def rerank|def enrich" src/cairn/graph/semantic.py src/cairn/graph/fusion.py src/cairn/graph/reranker.py src/cairn/graph/query_enrich.py` (session run: semantic.py:495 `semantic_search`, reranker.py:91/118/140/388, query_enrich.py:298 `enrich`).

---

## FR-010 — preserve degradation path/result fields/provenance/ranking/telemetry on stage failure

**Evidence**:
- Provenance strings and degraded-result contract documented at semantic.py:585-592:
  `"provenance"` ∈ `{"semantic","bm25","fused(bm25+semantic)"}`; hash-fallback variants
  built at semantic.py:630-632 (`_sem_prov`, `_fused_prov`); degraded flag noted in
  docstring: `results additionally carr[y] "degraded": "embedding-backend"` on hard
  dense-leg embed failure.
- Telemetry: `from cairn.telemetry import emit, SEMANTIC_BACKEND, EMPTY_RESULT` and
  `from cairn.telemetry.events import RERANK_SKIPPED` (semantic.py:602-603).

**Status**: DONE (baseline present) — preservation constraint on future refactor;
current behavior exists as cited.

**Verify**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_semantic_events.py tests/test_semantic_unavailable.py tests/test_semantic_enrichment.py -q` — unknown — verify (not run this session; files confirmed present via `ls tests | grep -i semantic`).

---

## FR-011 — LLM synthesis/extraction contracts with bounded timeout and malformed-output behavior

**Evidence**:
- `src/cairn/llm/client.py:27-33`: `class LLMClient(Protocol)` already exists with
  `synthesize(..., timeout: int = 120)`, `revise`, `judge`, `extract` methods —
  a contract already in place.
- Two backends implement it: `FileQueueBackend` (client.py:44-94, bounded via
  `max_wait`/`poll_interval` params, default `max_wait=600` at client.py:51) and
  `SubprocessBackend` (client.py:97-159, bounded via `subprocess.run(..., timeout=timeout)`
  at client.py:124-129).
- `get_client(bundle)` (client.py:36-41) resolves the backend from
  `os.environ.get("CAIRN_LLM_BACKEND", "file-queue")`.

**Status**: DONE — the `LLMClient` Protocol contract with bounded timeouts already
exists at baseline; FR-011 as stated ("shall expose ... through contracts") is already
satisfied by current code, not a gap to close.

**Verify**: `rg -n "class LLMClient|def synthesize|def extract" src/cairn/llm/client.py` (session run: client.py:27,30,33,68,84,136,155).

---

## FR-012 — both LLM backends skip malformed JSON records consistently in extraction

**Evidence** (baseline INCONSISTENCY found):
- `FileQueueBackend.extract` (client.py:84-94) skips malformed JSON per-line:
  ```
  for line in raw.splitlines():
      line = line.strip()
      if line.startswith("{"):
          try:
              out.append(json.loads(line))
          except json.JSONDecodeError:
              continue
  ```
- `SubprocessBackend.extract` (client.py:155-159) does **not** guard against malformed
  JSON:
  ```
  def extract(self, transcript: str) -> List[Dict[str, Any]]:
      raw = self._exec(_build_prompt("memory-extract", {"transcript": transcript}))
      if not raw:
          return self._fallback.extract(transcript)
      return [json.loads(l) for l in raw.splitlines() if l.strip().startswith("{")]
  ```
  This is a bare list comprehension calling `json.loads` with no `try/except` — a
  malformed `{`-prefixed line raises `json.JSONDecodeError` uncaught, unlike
  `FileQueueBackend.extract`'s per-line skip.

**Status**: TODO — the two backends currently behave inconsistently on malformed JSON
(`FileQueueBackend` skips; `SubprocessBackend` raises). This is a genuine baseline gap,
not just an absent abstraction.

**Verify**: `rg -n "def extract" -A 10 src/cairn/llm/client.py` (session run, client.py:84-94 vs 155-159 above); `grep -rl "SubprocessBackend\|FileQueueBackend\|get_client" tests` → only `tests/test_redaction_chokepoints.py` (session run) — no test currently exercises malformed-JSON handling for either backend's `extract`.

**Gap**: no test coverage for extract-malformed-JSON on either backend; the two backends' behavior actively diverges today.

---

## FR-013 — operational CLI implementations organized by command family and registered health checks

**Evidence**:
- `src/cairn/cli/system.py` is 2145 lines and holds all "system" commands as flat
  `@main.command()`-decorated functions in one file: hits at system.py:22, 421, 491, 534,
  1803, 2104 (`rg -n "^def cmd_|@main.command|@system.command|class.*Check|_CHECKS|def _run_doctor" src/cairn/cli/system.py`
  session output).
- Doctor checks are inline function calls inside `_run_doctor(db: str) -> list[dict]`
  (system.py:1732), not a registry of check objects: structure.md's own description
  confirms `_check_schema` at "743 … `_check_environment` 1569-1656" as functions, not
  registered/pluggable checks (`rg -n "class.*Check|_CHECKS" src/cairn/cli/system.py` → no matches this session).

**Status**: TODO — no command-family organization (single 2145-line file) and no
registered health-check abstraction (`_run_doctor` calls check functions directly, no
check registry/class found).

**Verify**: `wc -l src/cairn/cli/system.py` → 2145 (session run); `rg -n "class.*Check|_CHECKS" src/cairn/cli/system.py` → 0 matches (session run).

---

## FR-014 — preserve command names/output/exit/redaction semantics

**Evidence**:
- `cairn doctor --json` emits the `_result` list `{name, status, detail, hint}`
  (per tech.md, check-name sequence pinned by `test_doctor.py:96-124`).
- Doctor exit codes: 0 on PASS/WARN, 1 on any FAIL (`_run_doctor` system.py:1680 per
  tech.md, doctor command system.py:1750).

**Status**: DONE (baseline present, confirmed passing) — preservation constraint on
future refactor.

**Verify**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_doctor.py -q` → **46 passed in 6.44s** (run this session).

---

## FR-015 — file/symbol/import/edge persistence isolated behind repository seams

**Evidence**:
- No repository abstraction exists: `rg -n "class.*Repository|Repository\(" src/cairn/graph/*.py` →
  0 matches (session run).
- Persistence is inline SQL executed directly inside `graph/builder.py`:
  `INSERT INTO files (...)` at builder.py:836, `INSERT INTO symbols` at builder.py:985,
  `INSERT INTO imports (...)` at builder.py:995, `INSERT INTO edges` at builder.py:1001
  and again at builder.py:1181 (`rg -n "INSERT INTO files|INSERT INTO symbols|INSERT INTO imports|INSERT INTO edges|cur.execute.*INSERT" src/cairn/graph/builder.py` session output).

**Status**: TODO — no repository seam exists; graph indexing writes SQL directly in `builder.py`.

**Verify**: `rg -n "class.*Repository|Repository\(" src/cairn/graph/*.py` (0 hits, session run); `rg -n "INSERT INTO files|INSERT INTO symbols|INSERT INTO imports|INSERT INTO edges" src/cairn/graph/builder.py` (5 hits above, session run).

---

## FR-016 — preserve stored values/generated IDs/transaction boundaries/batch behavior/crash-recovery on insert/reindex

**Evidence**:
- ID generation: `_new_id()` at builder.py:131-132 (`uuid.uuid4().hex`).
- Schema/transaction machinery referenced from `src/cairn/graph/schema.py`:
  `init_db, get_build_db, get_db, backup_to, build_lock, note_contention` imported at
  builder.py:39.

**Status**: DONE (baseline present) — preservation constraint on future refactor;
current behavior exists as cited. Full crash-recovery/batch-behavior test coverage not
independently re-run this session — unknown — verify beyond the cited symbols.

**Verify**: `rg -n "_new_id|build_lock|note_contention" src/cairn/graph/builder.py` (session run: builder.py:39,131-132, plus additional call sites not individually re-verified this session — unknown, verify full call list if this FR becomes load-bearing for tech.md).

**Gap**: no single crash-recovery/batch test was run this session to pin current behavior byte-for-byte; only the presence of the transaction-boundary machinery was confirmed.

---

## FR-017 — refactor introduces no new runtime dependency

**Evidence**:
- Current runtime `dependencies` block: `pyproject.toml:28-63` — tree-sitter (13 grammar
  packages + core), click, pyyaml, mcp, jinja2, pydantic, pathspec, packaging,
  sqlite-vec, numpy, rich, questionary (session `Read` of pyproject.toml:25-65).

**Status**: TODO (not yet applicable — no refactor code has been written; this is a
forward constraint, baseline dependency list captured above as the "no new" reference point).

**Verify**: `sed -n '25,65p' pyproject.toml` (session run, captured above); re-run `pip-audit`/`uv run pytest` dependency diff once refactor PRs land — unknown — verify at that point.

---

## FR-018 — refactor introduces no import cycle and no new static-complexity finding

**Evidence**:
- CI gates per `specs/context/tech.md`: mypy is a HARD gate (`mypy --ignore-missing-imports src`,
  ci.yml:67-68, "runs clean at default settings"); ruff is pyflakes-only (`select = ["F"]`,
  pyproject.toml:184-206) — neither tool is a dedicated cycle-detector or complexity
  linter (no radon/xenon/import-linter config found: `rg -n "radon|xenon|import-linter|importlinter" pyproject.toml .pre-commit-config.yaml` → unknown — verify, not run this session).

**Status**: TODO (not yet applicable — forward constraint on the refactor; no cycle-detection
or complexity-gate tooling identified in the repo yet to measure against).

**Verify**: `rg -n "radon|xenon|import-linter|importlinter" pyproject.toml .pre-commit-config.yaml` — unknown — verify (not run this session).

**Gap**: repo has no confirmed static-complexity or import-cycle CI gate today; unclear what tool this FR's "no new static complexity finding" would be measured against — unknown, verify with the spec author/tech survey.

---

## Self-check

Ran: `python3 /Users/lnmtan/.omp/agent/skills/spec-to-prod/scripts/check.py specs/solid-architecture-refactor --repo . --survey-only`
