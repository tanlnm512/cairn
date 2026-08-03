# Codegraph: Full Tool Reference

Full signatures and descriptions for every MCP tool exposed by the `codegraph`
server. SKILL.md keeps only a name index — come here for the details.

## Layer 1: Structural Graph & Hybrid Retrieval
- `explore(query)` -- **Recommended first call.** Performs 3-stage hybrid search (BM25 + BAAI/bge-m3 FP16 vectors + Cross-Encoder reranking) + 1-hop AST callers/callees + blast radius in one call
- `semantic_search(query, limit=20, include_callers=False, structured=False)` -- 3-stage hybrid semantic search (bge-m3 dense 1024d + BM25 + sqlite-vec ANN index + Cross-Encoder rerank). `structured=True` returns a typed result object instead of the rendered text.
- `find_definition(name)` -- Where a symbol is defined (supports Tree-sitter AST & SCIP compiler-grade exact bindings)
- `get_callers(name, fuzzy=False, limit=200, structured=False)` -- Who calls this function (precise by default; fuzzy re-enables name-only matching). `structured=True` returns a typed result object instead of the rendered text.
- `get_callees(name, fuzzy=False, limit=200, structured=False)` -- What this function calls (precise drops stdlib/external; fuzzy includes them). `structured=True` returns a typed result object instead of the rendered text.
- `impact_analysis(name, depth=5, fuzzy=False, cached=False, limit=500, structured=False)` -- Recursive what-breaks (within-repo). `cached=True` uses the precomputed dataflow index (run `cg dataflow build` first) — the default `cached=False` walks the live caller graph. `structured=True` returns a typed result object instead of the rendered text. See `references/golden-rules.md` Rule 6 before calling this on a common/lifecycle name.
- `search_symbols(pattern, kind="", structured=False)` -- Lexical BM25 FTS5 symbol search. `structured=True` returns a typed result object instead of the rendered text.
- `cross_repo_deps(repo, limit=50)` -- Cross-repo dependency map
- `visualize_graph(scope, symbol?, module?, repo?, depth=3, format="mermaid")` -- Mermaid/DOT/JSON diagram of a symbol/module/impact/repo/deps scope

## Layer 2/3: Knowledge Base + Compass
- `search_knowledge(query, type_filter="", limit=10, full_body=False)` -- Search wiki, compass, patterns, memory. Use `type_filter="Wiki"` for wiki articles, `type_filter="Pattern"` for non-obvious patterns, `type_filter="Compass"` for module guides, or leave empty for all.
- `get_compass(module)` -- Get navigation guide for a module
- `trace_flow(entry, max_depth=8)` -- Trace the downward call chain from an entry-point symbol. Returns ordered call sequence, branch points (fan-out), and terminal calls (side effects). Read-only.
- `generate_flow(entry, as_workflow=False, max_steps=20)` -- Generate a flow compass from a call-graph trace (critic-gated). With `as_workflow=True`, also generates a Knowledge-workflow doc with ordered, editable procedural steps.

## Layer 4: Memory
- `memory_digest(limit=10)` -- Top tribal memories for session orientation (the recommended first memory call in a new session)
- `recall_memory(query, tier?)` -- Search past decisions, patterns, mistakes (symbol/title-keyed; see `references/tool-behaviors.md`)
- `record_memory(type, title, body, resource?, confidence?)` -- Capture a learning
- `memory_promote(memory_path)` -- Promote a memory to a higher tier (raw→drafts→tribal)
- `memory_demote(memory_path, tier="raw")` -- Demote a memory to a lower tier (rejects promotions)
- `memory_delete(memory_path)` -- Permanently delete a memory and its refs (irreversible)
- `memory_decay(raw_max_days=7, tribal_max_stale=90)` -- Auto-expire old raw, archive stale tribal

## Router
- `ask_compass(query, file_path="")` -- Natural language across all layers (the entry point). Pass `file_path` alone for auto-context loading (compass + wiki + memory for a file).

## Layer 5: Knowledge Documents
- `knowledge_add(title, body, doc_type, tags, affects_modules, affects_repos)` -- Ingest a business document (policy, spec, design doc). The PO's ingestion path.
- `knowledge_search(query, limit)` -- Search knowledge docs by meaning + bridge to code graph via affects_repos
- `knowledge_delete(doc_id)` -- Delete a knowledge document and its embeddings (irreversible)
- `knowledge_status(doc_id, new_status)` -- Update doc_status (active → superseded → archived)
- `trace_workflow(ref)` -- Trace ordered procedural workflow steps referenced by a knowledge doc
