# Survey: rationale-nodes

**Created**: 2026-10-05 | **Baseline**: 0.21.2 @ e9d9074
The survey node's output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item S1: "No rationale table or extraction exists anywhere"
  evidence:   grep -rn "rationale" src/cairn tests   (no match — verified this session)
  status:     TODO
  verify:     grep -rn "rationale" src/cairn --include="*.py"   (exit 1)
  gap:        additive `rationale` table + per-language comment extraction + CLI + explore section

item S2: "Parser registry is per-language wheel modules; kotlin is vendored"
  evidence:   src/cairn/parsers/_registry.py:107-116 mapping — "kotlin": "cairn._tree_sitter_kotlin", "java": "tree_sitter_java", "python": "tree_sitter_python", ... "cpp": "tree_sitter_cpp"
  status:     DONE
  verify:     python3 -c "from cairn.parsers._registry import is_language_available as f; print([l for l in ('python','go','rust','java','kotlin','c','cpp','csharp','ruby','typescript') if f(l)])"
  gap:        comment node types per grammar are NOT mapped anywhere — must be verified per language with a tiny parse (tech-spec evidence)

item S3: "Shared walk machinery exists for extraction hooks"
  evidence:   src/cairn/parsers/base.py:98 `class TreeSitterParserBase:` (scope stacks, node helpers); src/cairn/parsers/generic_tree_sitter.py:51 `def _visit(self, node: Node, source: bytes, parsed: ParsedFile) -> None:`
  status:     DONE
  verify:     python3 -m pytest tests/ -k parser -q
  gap:        comment nodes currently fall through _visit unmatched (only java.py:296 references line_comment, to EXCLUDE it from an enumeration)

item S4: "ParsedFile carries symbols/edges/imports — the extraction output rides the same dataclass"
  evidence:   src/cairn/parsers/base.py:86 `def parse(self, path: str) -> ParsedFile:`; ParsedFile fields (path, language, hash, line_count, symbols, edges, imports) per the dataclass in base.py
  status:     DONE
  verify:     grep -n "class ParsedFile" -A 12 src/cairn/parsers/base.py
  gap:        add rationale list to ParsedFile + per-language marker extraction

item S5: "Symbol/edge persistence is centralized in graph/repository.py"
  evidence:   src/cairn/graph/repository.py:26 `"""INSERT INTO symbols` :49 `"""INSERT INTO edges`
  status:     DONE
  verify:     grep -n "INTO symbols" src/cairn/graph/repository.py
  gap:        rationale INSERT joins the same persistence layer; FTS-style triggers in schema.py:81-99 show the additive-table pattern for builder-populated content

item S6: "Incremental reindex deletes file-scoped rows then re-parses — rationale deletion rides file_id"
  evidence:   src/cairn/graph/incremental.py:100 `"DELETE FROM edges WHERE source_id IN (SELECT id FROM symbols WHERE file_id = ?)"` :157 `cur.execute("DELETE FROM symbols WHERE file_id = ?", (file_id,))`
  status:     DONE
  verify:     python3 -m pytest tests/ -q -k incremental and not infra
  gap:        add DELETE FROM rationale WHERE file_id = ? alongside; re-derive from the fresh parse

item S7: "Additive-table convention documented in schema with named precedent"
  evidence:   src/cairn/graph/schema.py:110-113 "Additive-only: plain CREATE TABLE IF NOT EXISTS rides the idempotent executescript in _apply_schema with NO MIGRATIONS entry"; table precedents at :196 embeddings, :217 embeddings_mv, :114 term_df
  status:     DONE
  verify:     sed -n '110,115p' src/cairn/graph/schema.py
  gap:        `rationale` table follows this pattern verbatim

item S8: "explore has a section-append rendering precedent to copy"
  evidence:   src/cairn/mcp_server/tools_graph.py:602 `# --- Tribal memory section ---` :603 `out.append(f"=== Tribal memory ({len(tribal)}) ===")`
  status:     DONE
  verify:     grep -n "Tribal memory" src/cairn/mcp_server/tools_graph.py
  gap:        a "Rationale" section appends the same way, gated on rows

item S9: "CLI wiring is import-side-effect; a `cairn grep` command already exists as a content-surface neighbor"
  evidence:   src/cairn/cli/__init__.py:1-12 `from . import agents ... knowledge` (one import per module); src/cairn/cli/grep.py:11 `@main.command(name="grep")` calling graph_grep
  status:     DONE
  verify:     cairn --help
  gap:        new cli/rationale.py module + one import line; `cairn rationale` sits beside grep as a read surface
```

## Supporting evidence

- Attribution spans: Symbol rows carry line_start/line_end (schema.py:36-48 `CREATE TABLE IF NOT EXISTS symbols` with `line_start`, `line_end` columns) — span containment for FR-002 keys off these.
- Skipped/parse-error plumbing shows per-file failure isolation precedent (builder.py:72/:283 skipped_files inserts; :1262 parse_errors) — FR-006's no-fail-on-unmapped-language rides this shape.
- Builder bulk insert path: repository.py is the persistence choke point; builder calls it per parsed file (grep `repository` in builder.py for call sites — verify in tech-spec).

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
