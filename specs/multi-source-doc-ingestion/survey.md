# Survey: multi-source-doc-ingestion

**Created**: 2026-08-26 | **Baseline**: 0.14.4 @ fb7ef04f15cda0f9a9439dca865f325b40e4e425
Phase-A output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.
(Placed on disk by the orchestrator, verbatim from the surveyor agent's
output — the Explore agent type has no write tool.)

## Items

```
item FR-001: "Repo doc tree allowlist walk with skip-list and logged reasons"
  evidence:   No matches for allowlist/skip-list patterns in src/cairn/knowledge/
    $ grep -rn 'allowlist\|skip.list\|SKIP_LIST\|skip_list' src/cairn/knowledge/ --include='*.py'
    (empty)
  status:     TODO
  verify:     grep -rn 'allowlist\|skip_list\|SKIP_LIST' src/cairn/knowledge/ --include='*.py'
  gap:        No repo-scanning doc walker, no skip-list, no reason-logging for ingestion skips.
              The graph scanner (src/cairn/graph/scanner.py:102) has a DEFAULT_SKIP_DIRS for code
              files, but that is for code indexing, not knowledge ingestion, and has no skip-reason
              logging contract.
```

```
item FR-002: "Directly-fed markdown files and directories through same parse/classify/stage path"
  evidence:   No fed-file path exists. The existing `import_directory` takes a dir_path and
              applies uniform doc_type with no classification:
    src/cairn/knowledge/store.py:279:        for md_file in sorted(Path(dir_path).rglob("*.md")):
    src/cairn/knowledge/store.py:295:            title = md_file.stem.replace("-", " ").replace("_", " ").title()
  status:     TODO
  verify:     grep -rn 'fed\|--file\|--dir' src/cairn/cli/knowledge.py | grep -i 'markdown\|feed'
  gap:        No mechanism to feed individual files. `cairn knowledge import <dir>` exists but
              has no classification, no frontmatter parsing, and no staging.
```

```
item FR-003: "PDF/docx conversion to markdown via pure-python optional extra"
  evidence:   No PDF/docx code or dependency anywhere:
    $ grep -rn 'pdf\|docx\|pdfminer\|python.docx\|pymupdf\|pypdf' src/cairn/ --include='*.py'
    (empty)
    $ grep -rn 'ingest' pyproject.toml
    (empty, exit code 1)
  status:     TODO
  verify:     grep -rn 'pdf\|docx' src/cairn/ --include='*.py'; grep 'ingest' pyproject.toml
  gap:        No conversion code, no `cairn[ingest]` extra. PyYAML is already a core dep
              (pyproject.toml:44: "pyyaml>=6.0"). The extras mechanism exists (watch, test, dev,
              semantic, ann, scip, otlp at pyproject.toml:65-137) but no `ingest` extra.
```

```
item FR-004: "Parse YAML frontmatter + inline Status markers, classify via doc-kind map"
  evidence:   OKFConcept._split_frontmatter exists (src/cairn/okf/concept.py:213-227) but parses
              OKF frontmatter only (type, title, tags, etc.), not source-doc frontmatter for
              classification. No doc-kind-to-doc_type map exists:
    $ grep -rn 'doc.kind\|doc_type_map\|classify_doc\|ADR.*decision\|FEAT.*spec' src/cairn/knowledge/ --include='*.py'
    (empty)
    $ grep -rn '\*\*Status:\*\*\|## Status' src/cairn/ --include='*.py'
    src/cairn/llm/tasks.py:487:    lines.append(f"**Status:** {task.status}  ")
    (that is LLM task output formatting, not parsing)
  status:     TODO
  verify:     grep -rn 'doc_type_map\|classify' src/cairn/knowledge/ --include='*.py'
  gap:        No source-doc frontmatter parsing (the existing _split_frontmatter is OKF-internal).
              No inline Status marker parsing. No doc-kind-to-doc_type classification map.
              No minimal-parser fallback for malformed YAML.
```

```
item FR-005: "Skip draft/proposed/review/superseded/deprecated docs unless --include-drafts"
  evidence:   No draft-skipping or --include-drafts flag:
    $ grep -rn 'include.drafts\|include_drafts\|--include-drafts' src/cairn/ --include='*.py'
    (empty)
              Existing status lifecycle is forward-only (active->superseded->archived) per
    src/cairn/knowledge/store.py:195:  DOC_STATUSES = ("active", "superseded", "arched"...)
    (verbatim: DOC_STATUSES = ("active", "superseded", "archived"))
              This is the knowledge-store lifecycle, not source-doc status parsing.
  status:     TODO
  verify:     grep -rn 'include.drafts' src/cairn/ --include='*.py'
  gap:        No source-doc status parsing, no draft-skip logic, no --include-drafts flag.
```

```
item FR-006: "Stage OKF outbox (one valid OKF md per doc) + manifest JSON"
  evidence:   No outbox/staging/manifest code:
    $ grep -rn 'outbox\|manifest.json\|stage' src/cairn/knowledge/ --include='*.py'
    (empty)
  status:     TODO
  verify:     grep -rn 'outbox\|manifest' src/cairn/knowledge/ --include='*.py'
  gap:        No staging directory, no manifest JSON generation, no OKF outbox concept.
              The existing `add_document` writes directly to the bundle.
```

```
item FR-007: "Stable identities: title, slug, tags, affects_repos, doc_source, description"
  evidence:   Partial building blocks exist:
              - slugify: src/cairn/okf/utils.py:13-20:  def slugify(text: str) -> str:
              - add_document writes doc_source='imported': src/cairn/knowledge/store.py:105
              - add_document writes affects_repos from param: src/cairn/knowledge/store.py:142
              - add_document sets description=title (not extracted): src/cairn/knowledge/store.py:151:  description=title,  # one-line summary; caller can override via body
              - No stable ID prefix, no ({repo}) suffix on collision, no source-tag union:
    $ grep -rn 'slug.*collision\|collision.*slug\|repo.*suffix\|cross.repo.*slug' src/cairn/ --include='*.py'
    (empty)
  status:     PARTIAL
  verify:     grep -n 'description=title' src/cairn/knowledge/store.py
  gap:        slugify exists but no stable-ID title pattern, no collision suffix, no tag union,
              no real description extraction (currently defaults to title).
```

```
item FR-008: "Dry-run default (stage only); when approved: knowledge add + embed + verify"
  evidence:   Subcommands exist but no ingest orchestration:
              - knowledge add: src/cairn/cli/knowledge.py:18-57
              - knowledge embed: src/cairn/cli/knowledge.py:138-185 (batch-size option exists, default 64)
              - validate: src/cairn/cli/validate.py:11-25 (cairn validate, not cairn knowledge validate)
              - knowledge list: src/cairn/cli/knowledge.py:118-135
              - No dry-run/approve gate for ingestion pipeline:
    $ grep -rn 'dry.run\|approve' src/cairn/knowledge/ --include='*.py'
    (empty)
  status:     PARTIAL
  verify:     .venv/bin/cairn knowledge --help
  gap:        Individual subcommands (add, embed, list) exist. No orchestration that chains
              them. No dry-run default / approve gate. No verify step (list count vs manifest,
              validate, smoke searches).
```

```
item FR-009: "Idempotent: re-runs overwrite same concept ids, never duplicate"
  evidence:   Atomic overwrite exists at the file level:
    src/cairn/okf/concept.py:159:            os.replace(tmp_path, path)
    src/cairn/okf/bundle.py:129:        concept.to_file(str(path))
              The concept_id is deterministic from slugified title:
    src/cairn/knowledge/store.py:131:    slug = slugify(title)
    src/cairn/knowledge/store.py:133:    concept_id = f"knowledge/{safe_doc_type}/{slug}"
              So same title + same doc_type = same concept_id = overwrite via os.replace.
  status:     PARTIAL
  verify:     .venv/bin/pytest tests/test_import_validation.py::TestImportDirectoryValidation::test_import_directory_normal_files_succeed -q
              (PASS: 35 passed in 6.42s for all knowledge tests)
  gap:        Idempotent overwrite works at the file-write level. However, embeddings are
              keyed by (doc_id, model) so re-embed after overwrite skips (by design). The
              missing piece is: there is no explicit dedup check at the ingest pipeline level
              (the spec's ingest subcommand does not exist yet), so the idempotency is only
              proven for the underlying primitives, not for the pipeline as a whole.
```

```
item FR-010: "Per-workspace config overrides for classification/skip-list"
  evidence:   cairn.json exists for graph config (exclude/include/repo_namespaces/scip):
    src/cairn/graph/config.py:69:    def load_config(root: Union[str, Path]) -> CairnConfig:
    src/cairn/graph/config.py:77:        path = root / "cairn.json"
              Recognized keys are exclude, include, repo_namespaces, scip only:
    src/cairn/graph/config.py:63-66:  _EXCLUDE_KEY = "exclude" / _INCLUDE_KEY = "include" / _REPO_NAMESPACES_KEY = "repo_namespaces" / _SCIP_KEY = "scip"
              No knowledge/ingest config sections:
    $ grep -rn 'cairn.json.*knowledge\|knowledge.*config\|ingest.*config' src/cairn/graph/config.py
    (empty)
  status:     TODO
  verify:     grep -n '_EXCLUDE_KEY\|_SCIP_KEY' src/cairn/graph/config.py
  gap:        cairn.json loading mechanism exists but only recognizes graph-scoped keys.
              No knowledge/ingest config keys (skip-list overrides, classification overrides).
```

```
item FR-011: "Ship as cairn knowledge ingest subcommand"
  evidence:   The `knowledge` Click group exists with 9 subcommands, no `ingest`:
    src/cairn/cli/knowledge.py:12-15:  @main.group() / def knowledge():
    $ .venv/bin/cairn knowledge --help
    Commands: add, embed, export, impact, import, list, remove, search, status, workflow
    $ grep -rn '@knowledge.command.*ingest\|def.*ingest' src/cairn/cli/knowledge.py
    (empty)
  status:     TODO
  verify:     .venv/bin/cairn knowledge --help 2>&1 | grep ingest; echo $?
  gap:        No `ingest` subcommand. The `import` subcommand exists but is the old
              uniform-type bulk importer (FR-001 rationale explains why it's inadequate).
```

```
item FR-012: "Generic for any cairn workspace out of the box"
  evidence:   Workspace resolution is generic (cwd/ancestor/env):
    src/cairn/paths.py:160:    def resolve_workspace(explicit=None) -> Path:
    src/cairn/paths.py:180:    def resolve_store(workspace=None) -> StorePaths:
              Store resolution is per-workspace via CAIRN_HOME/<key>:
    src/cairn/paths.py:29-31:  CAIRN_HOME = Path(os.environ.get("CAIRN_HOME", str(Path.home() / ".cairn")))
  status:     PARTIAL
  verify:     grep -n 'resolve_store' src/cairn/cli/knowledge.py | head -5
  gap:        Infrastructure is generic. The gap is that no ingest-specific code exists to
              be generic or polaris-specific -- this FR will be satisfied by design if the
              implementation uses the existing resolve_store() and avoids polaris hard-coding.
              Currently untestable because the code does not exist.
```

## Supporting evidence

### `cairn knowledge` CLI group (src/cairn/cli/knowledge.py)

- Group registration: `src/cairn/cli/knowledge.py:12-15` -- `@main.group()` `def knowledge():`
- 9 subcommands: add (L18), import (L60), search (L87), list (L118), embed (L138), impact (L188), remove (L226), status (L254), export (L281), workflow group (L317)
- Store resolution pattern (used by every subcommand):
  ```
  src/cairn/cli/knowledge.py:44:    store = resolve_store()
  src/cairn/cli/knowledge.py:45:    store.ensure()
  src/cairn/cli/knowledge.py:46:    bundle = OKFBundle(str(store.knowledge))
  ```
- Arg patterns: `--file`/`--body` (add), `--type doc_type` (add, import), `--tags` (comma-split), `--affects` (comma-split), `--batch-size` (embed, default 64), `--dry-run` (workflow sync only)
- No cwd-based workspace override at the CLI level beyond what `resolve_store()` provides (which is cwd-aware per paths.py:160-177)
- `scripts/ingest_docs.py` does NOT exist (confirmed via `ls scripts/ingest_docs.py` -- empty)

### Knowledge store API (src/cairn/knowledge/store.py)

- `add_document` signature: `src/cairn/knowledge/store.py:93-106`
  ```python
  def add_document(bundle, title, body, doc_type, tags=None, affects_modules=None,
                    affects_repos=None, resource=None, owner=None, epic_link=None,
                    steps=None, doc_source="manual") -> str:
  ```
  Returns concept_id string. Extensions written: tier, doc_status="active", doc_owner, doc_source, epic_link, affects_modules, affects_repos.
- Privacy redaction chokepoint: `src/cairn/knowledge/store.py:127-128`
  ```python
  title = strip_private_data(title)
  body = strip_private_data(body)
  ```
  Runs BEFORE slugify (L131) so secret-shaped titles never leak into concept_id.
- `import_directory`: `src/cairn/knowledge/store.py:265-311` -- walks `Path(dir_path).rglob("*.md")`, derives title from filename stem, applies uniform doc_type, sets doc_source="imported", enforces IMPORT_MAX_FILE_SIZE=10MB.
- `list_documents`: `src/cairn/knowledge/store.py:162-183` -- filters by type/status/tag prefix.
- `get_document`: `src/cairn/knowledge/store.py:186-191` -- reads by concept_id.
- `update_status`: `src/cairn/knowledge/store.py:198-224` -- forward-only lifecycle (active->superseded->archived).
- `delete_document`: `src/cairn/knowledge/store.py:227-262` -- removes .md file + embedding rows.
- `embed_knowledge` lives at: `src/cairn/graph/embeddings.py:1263` -- `def embed_knowledge(conn, bundle, batch_size=64, progress=None)`
- OKF module exports: `src/cairn/knowledge/__init__.py:11-15` -- `add_document, search_knowledge, add_workflow, trace_workflow`

### OKF serializer contract (src/cairn/okf/concept.py to_markdown)

- `to_markdown`: `src/cairn/okf/concept.py:166-197`
- Frontmatter key order/shape (verbatim):
  ```python
  fm["type"] = self.type                          # required
  fm["title"] = self.title                        # if not None
  fm["description"] = self.description            # if not None
  fm["resource"] = self.resource                  # if not None
  fm["tags"] = self.tags                          # if truthy
  fm["generated"] = {"by": ..., "at": ...}        # always
  fm["status"] = self.status                      # if not None (OKF v0.2 status, NOT doc_status)
  fm["stale_after"] = self.stale_after            # if not None
  fm["sources"] = self.sources                    # if truthy
  fm["verified"] = self.verified                  # if truthy
  fm["okf_version"] = OKF_VERSION                 # always ("0.2")
  fm.update(self.extensions)                      # remaining keys (tier, doc_status, etc.)
  ```
- Serialization: `yaml.safe_dump(fm, sort_keys=False, allow_unicode=True)`
- Wire format: `---\n{yaml_block}\n---\n\n{body}`
- `slugify` in `src/cairn/okf/utils.py:13-20`: `re.compile(r"[^a-z0-9]+").sub("-", text.lower()).strip("-")[:60]`

### Existing frontmatter/status parsing, doc-walking, skip-list, classification

- Only OKF-internal frontmatter parsing exists: `src/cairn/okf/concept.py:213-227` (`_split_frontmatter`). No source-doc frontmatter parsing for classification.
- No inline `**Status:**` / `## Status` marker parsing for ingestion.
- No doc-kind-to-doc_type classification map.
- Graph scanner has `DEFAULT_SKIP_DIRS` at `src/cairn/graph/scanner.py:102` for code indexing, not knowledge ingestion.
- `cairn init --import-docs` (`src/cairn/cli/core.py:127-139`) calls `import_directory` with uniform doc_type="spec" and no classification.
- No skip-list with reason-logging exists for knowledge ingestion.

### Config surface (cairn.json / workspace config)

- `src/cairn/graph/config.py:69` -- `load_config(root)` reads `cairn.json` at workspace root.
- Recognized keys only: `exclude`, `include`, `repo_namespaces`, `scip` (L63-66).
- No knowledge/ingest config keys. No per-workspace classification or skip-list overrides.

### Dependency surface (pyproject.toml)

- Extras mechanism exists: `pyproject.toml:65-137` -- watch, test, dev, semantic, ann, scip, otlp.
- No `ingest` extra. No PDF/docx converter dependencies.
- PyYAML is a core dependency: `pyproject.toml:44: "pyyaml>=6.0"`
- `pathspec` is a core dependency: `pyproject.toml:47: "pathspec>=0.12"`

### Test layout

- 151 test files in tests/ (re-counted: `find tests/ -name '*.py' -not -name '__init__.py' -not -name 'conftest.py' | wc -l` = 151).
- Knowledge-related tests: `tests/test_knowledge_status.py`, `tests/test_knowledge_workflow.py`, `tests/test_import_validation.py`.
- Fixture pattern: `tempfile.TemporaryDirectory()` -> `OKFBundle(tmp)` (see tests/test_knowledge_status.py:21-24).
- DB fixture: `tests/conftest.py:97-117` -- `fresh_db` (in-memory SQLite with schema). `hash_backend` (L120-134) for semantic tests without torch.
- Hermetic env: `tests/conftest.py:36-94` -- `_hermetic_env` autouse fixture patches HOME, CAIRN_HOME, agent CLIs.
- CLI test pattern: `click.testing.CliRunner` (see tests/test_cli_init_rail.py:13, tests/test_uninstall_cmd.py:10).
- Existing knowledge tests pass: `.venv/bin/pytest tests/test_import_validation.py tests/test_knowledge_status.py tests/test_knowledge_workflow.py -q` -> `35 passed in 6.42s`.

### Privacy redaction chokepoint (strip_private_data)

- Location: `src/cairn/memory/privacy.py:57-70` -- `def strip_private_data(input_text: str) -> str:`
- Called in `add_document` at `src/cairn/knowledge/store.py:127-128` before slugify.
- Also called in `_redact_step_descriptions` at `src/cairn/knowledge/store.py:36` for workflow step descriptions.
- Pattern-based: `<private>` tags, URI credentials, API keys, bearer tokens, JWTs, etc.
- Tests pass: `.venv/bin/pytest tests/test_redaction_chokepoints.py -q` -> `42 passed in 6.51s`.

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
