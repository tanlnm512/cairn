# Survey: embedding-server-backend

**Created**: 2026-08-27 | **Baseline**: docs/embedding-server-backend-spec @ `51a56d8`
Phase-A output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item S01: "Backend dispatch plumbing (_backend_name, _effective_backend, cache, reset, embeddings_available, _embed dispatch, current_model)"
  evidence:
    src/cairn/graph/embeddings.py:297-298: "def _backend_name() -> str:\n    return (os.environ.get(\"CAIRN_EMBED_BACKEND\") or \"local\").strip().lower()"
    src/cairn/graph/embeddings.py:311: "_EFFECTIVE_BACKEND_CACHE: dict = {\"effective\": None}"
    src/cairn/graph/embeddings.py:314-320: "def reset_backend_cache() -> None:\n    _EFFECTIVE_BACKEND_CACHE[\"effective\"] = None"
    src/cairn/graph/embeddings.py:323-341: "def _effective_backend() -> str:\n    ...\n    backend = _backend_name()\n    if backend == \"local\":\n        try:\n            import sentence_transformers\n            _EFFECTIVE_BACKEND_CACHE[\"effective\"] = \"local\"\n        except ImportError:\n            _EFFECTIVE_BACKEND_CACHE[\"effective\"] = \"hash\"\n    else:\n        _EFFECTIVE_BACKEND_CACHE[\"effective\"] = backend"
    src/cairn/graph/embeddings.py:65-83: "def embeddings_available() -> bool:\n    backend = _backend_name()\n    if backend == \"hash\": return True\n    if backend == \"openai\": return bool(os.environ.get(\"OPENAI_API_KEY\"))\n    # local -- fall back to hash when sentence_transformers missing\n    try:\n        import sentence_transformers\n        return True\n    except ImportError:\n        _EFFECTIVE_BACKEND_CACHE[\"effective\"] = \"hash\"\n        return True"
    src/cairn/graph/embeddings.py:806-813: "def _embed(texts) -> Tuple[List[bytes], int]:\n    backend = _effective_backend()\n    if backend == \"hash\": return _embed_hash(texts)\n    if backend == \"openai\": return _embed_openai(texts)\n    return _embed_local(texts)"
    src/cairn/graph/embeddings.py:39-57: "def current_model(corpus: str = \"code\") -> str:\n    backend = _effective_backend()\n    if backend == \"hash\": return HASH_MODEL\n    if backend == \"openai\": return os.environ.get(\"CAIRN_EMBED_OPENAI_MODEL\", \"text-embedding-3-small\")\n    env_name = _CORPUS_MODEL_ENV.get(corpus)\n    ...\n    return (os.environ.get(\"CAIRN_EMBED_LOCAL_MODEL\") or DEFAULT_LOCAL_MODEL).strip()"
    src/cairn/graph/embeddings.py:33-36: "_CORPUS_MODEL_ENV = {\n    \"knowledge\": \"CAIRN_EMBED_KNOWLEDGE_MODEL\",\n    \"memory\": \"CAIRN_EMBED_MEMORY_MODEL\",\n}"
  status: DONE
  verify: uv run --extra dev pytest tests/test_embedding_backend_quality.py -k 'test_true_when_local_configured_but_unavailable or test_false_when_explicit_hash or test_false_when_openai_backend' -q
  gap: _effective_backend() has no branch for server/omlx/ollama; embeddings_available() has no server probe; _embed() has no server branch; current_model() has no server stamp path. New server env vars (CAIRN_EMBED_BASE_URL, CAIRN_EMBED_SERVER_MODEL, CAIRN_EMBED_API_KEY, CAIRN_EMBED_TIMEOUT, CAIRN_EMBED_SERVER_BATCH, CAIRN_EMBED_MODEL_STAMP) do not exist anywhere in the codebase.

item S02: "openai backend (_embed_openai): hardcoded URL, API key requirement, response parsing"
  evidence:
    src/cairn/graph/embeddings.py:732-753: "def _embed_openai(texts: Sequence[str]) -> Tuple[List[bytes], int]:\n    import urllib.request\n    import json\n    api_key = os.environ.get(\"OPENAI_API_KEY\")\n    if not api_key:\n        raise RuntimeError(\"CAIRN_EMBED_BACKEND=openai requires OPENAI_API_KEY\")\n    model = current_model()\n    url = \"https://api.openai.com/v1/embeddings\"\n    payload = json.dumps({\"model\": model, \"input\": list(texts)}).encode(\"utf-8\")\n    req = urllib.request.Request(\n        url,\n        data=payload,\n        headers={\"Authorization\": f\"Bearer {api_key}\", \"Content-Type\": \"application/json\"},\n    )\n    with urllib.request.urlopen(req, timeout=60) as resp:\n        body = json.loads(resp.read().decode(\"utf-8\"))\n    data = body[\"data\"]\n    data.sort(key=lambda d: d[\"index\"])\n    dim = len(data[0][\"embedding\"])\n    blobs = [_floats_to_blob(d[\"embedding\"]) for d in data]\n    return blobs, dim"
  status: DONE
  verify: uv run --extra dev pytest tests/test_embedding_backend_quality.py -k 'test_false_when_openai_backend' -q  # 1 passed
  gap: FR-009 requires this function byte-for-byte unchanged when the server backend is added. The server backend must be a NEW _embed_server function, not a modification of _embed_openai.

item S03: "Model-stamp machinery: stamps stored per row, purge_stale_models callers, vec0 table naming, rebuild_index call sites"
  evidence:
    src/cairn/graph/embeddings.py:1074-1082: "conn.execute(\n    \"INSERT INTO embeddings \n    (symbol_id, model, dim, vec, chunk, content_hash, embedded_at) \n    VALUES (?, ?, ?, ?, ?, ?, ?) \n    ON CONFLICT(symbol_id, model) DO UPDATE SET ...\",\n    (sid, model, dim, blob, chunk, chash, now),\n)"
    src/cairn/graph/embeddings.py:682: "def purge_stale_models(conn: sqlite3.Connection, active_model: Optional[str] = None) -> int:"
    # grep -rn 'purge_stale_models' src/ --include='*.py' returned ONLY the definition line — ZERO call sites
    src/cairn/graph/ann_index.py:146: "_SOURCE_PREFIX = {\"embeddings\": \"vec_\", \"embeddings_mv\": \"vecmv_\"}"
    src/cairn/graph/ann_index.py:149-165: "def _table_name(model: str, source: str = \"embeddings\") -> str:\n    ...\n    safe = re.sub(r\"[^a-zA-Z0-9_]\", \"_\", model)\n    return f\"{_SOURCE_PREFIX[source]}{safe}\""
    # rebuild_index call sites (2):
    src/cairn/cli/embed.py:154: "idx_summary = ann.rebuild_index(conn, emb.current_model())"
    src/cairn/cli/embed.py:160: "ann.rebuild_index(conn, emb.current_model(), source=\"embeddings_mv\")"
  status: DONE
  verify: grep -rn 'purge_stale_models' src/ --include='*.py'  # 1 hit (definition only)
  gap: purge_stale_models is defined but NEVER called anywhere in the codebase. The server stamp format `server/{netloc}/{model}` will flow through _table_name via re.sub (netloc contains `:` and `/` which become `_`), producing e.g. `vec_localhost_8000_bge-m3`. No special handling needed.

item S04: "semantic_search: embed_query call sites, exception handling (or lack thereof), BM25/FTS5 provenance, degraded fields"
  evidence:
    src/cairn/graph/semantic.py:769: "q_blob, q_dim = emb.embed_query(dense_text)"
    # NO try/except wraps line 769 — confirmed: embed errors propagate uncaught out of _run_pass into semantic_search, which also has no catch around _run_pass (line 1012: `candidates = _run_pass(_dense_query, ())`)
    src/cairn/graph/semantic.py:995: "\"provenance\": \"bm25\","
    src/cairn/graph/semantic.py:589: "Every result carries `provenance` (`"semantic"`, `"bm25"`, or `"fused(bm25+semantic)"`)"
    # No 'degraded' key exists on semantic_search result dicts. Internal telemetry flags only:
    src/cairn/graph/semantic.py:668-670: "_fusion_degraded = False\n_rerank_degraded = False"
    # grep -rn '"degraded"' src/cairn/graph/semantic.py — zero hits (only _fusion_degraded/_rerank_degraded internal flags)
    # compass tool uses degraded on its own result dict (not from semantic_search):
    src/cairn/mcp_server/tools_compass.py:166: "if result.get(\"degraded\"):\n    out.append(\"(Showing fallback results ...\")"
  status: DONE
  verify: uv run --extra dev pytest tests/test_embedding_backend_quality.py -k 'test_provenance' -q  # 5 passed
  gap: Spec FR-012 requires catching dense-leg embed errors inside semantic_search. Today, emb.embed_query at semantic.py:769 is unwrapped — errors propagate uncaught. The `degraded` field does not exist on result dicts today and must be added for FR-012/FR-013.

item S05: "model_warmup.py: local-only gating, guards (never raise, PYTEST_CURRENT_TEST, offline env)"
  evidence:
    src/cairn/graph/model_warmup.py:81-105: "def warm_models_in_background() -> ...:\n    if _warm_disabled() or _inside_pytest():\n        return None\n    ..."
    src/cairn/graph/model_warmup.py:141-151: "def _inside_pytest() -> bool:\n    return bool(os.environ.get(\"PYTEST_CURRENT_TEST\"))"
    src/cairn/graph/model_warmup.py:154-173: "def _warm_embedder() -> None:\n    from . import embeddings\n    if embeddings._effective_backend() != \"local\" or not embeddings.model_is_cached():\n        return\n    _load_with_offline_guard(embeddings._get_local_model)"
    src/cairn/graph/model_warmup.py:108-132: "def warm_models() -> None:\n    try:\n        _warm_embedder()\n    except Exception as exc:\n        _LOGGER.warning(\"model warm-up: embedding model load failed (non-fatal): %s\", exc, ...)\n    try:\n        _warm_reranker()\n    except Exception as exc:\n        _LOGGER.warning(\"model warm-up: reranker load failed (non-fatal): %s\", exc, ...)"
  status: DONE
  verify: uv run --extra dev pytest tests/test_model_warmup.py -k 'openai' -q  # 1 passed (test_openai_backend_skips_embed_model)
  gap: _warm_embedder gates on `_effective_backend() != "local"` — a server backend would skip warmup entirely. FR-006 requires warming server backends with a tiny probe. The existing guards (never raise, PYTEST_CURRENT_TEST, offline env) are reusable; a new _warm_server step is needed.

item S06: "embed_buffering.py: flush retry loop semantics"
  evidence:
    src/cairn/mcp_server/embed_buffering.py:83-141: "def _flush() -> None:\n    ...\n    try:\n        conn = _conn_factory()\n        bundle = _bundle_factory()\n        emb.embed_memory_concepts(conn, bundle, batch)\n        conn.commit()\n    except Exception:\n        _FAILURES += 1\n        if _FAILURES >= _WARN_AFTER:\n            logger.warning(\"memory embed flush has failed %d consecutive times...\", ...)\n            if not _STALL_EVENT_SENT:\n                _STALL_EVENT_SENT = True\n                from cairn.telemetry import EMBED_FLUSH_STALLED, emit as _emit\n                _emit(EMBED_FLUSH_STALLED, failures=_failures_bucket(_FAILURES))\n        ...\n        return\n    ...\n    _FAILURES = 0\n    _STALL_EVENT_SENT = False\n    with _LOCK:\n        for cid in batch:\n            try:\n                _QUEUE.remove(cid)\n            except ValueError:\n                pass\n"
    src/cairn/mcp_server/embed_buffering.py:153-160: "def _loop():\n    while True:\n        time.sleep(_FLUSH_INTERVAL)\n        _flush()\n"
  status: DONE
  verify: uv run --extra dev pytest tests/test_embed_flush_stalled.py -q  # 5 passed
  gap: Retry is infinite (no max retry count, no drop). Every 15s the flusher re-attempts the same batch. Transient server errors are tolerated by design. A server backend embed failure inside embed_memory_concepts would be caught by this retry loop. No special handling needed for server errors — they're already retried indefinitely.

item S07: "Telemetry pattern: HASH_FALLBACK event + emit, events.py catalog, reason-enum precedents"
  evidence:
    src/cairn/telemetry/events.py:42: "HASH_FALLBACK = \"hash_fallback\""
    src/cairn/telemetry/events.py:138-164: "def emit(name: str, **attrs: Any) -> None:\n    if sink.is_telemetry_off() or sink.is_read_only(): return\n    try:\n        ts = time.time()\n        attrs_json = _coerce_attrs(attrs)\n        session_id = _session_id()\n        sink.enqueue(ts, name, session_id, attrs_json)\n        otel.record(ts, name, session_id, attrs_json)\n    except Exception:\n        logger.debug(\"emit(%s) failed\", name, exc_info=True)"
    src/cairn/telemetry/events.py:179-194: "def warn_once(key: str, warn_logger: logging.Logger, msg: str) -> None:\n    if sink.is_telemetry_off(): return\n    with _WARN_LOCK:\n        if key in _WARNED: return\n        _WARNED.add(key)\n    warn_logger.warning(msg)"
    src/cairn/telemetry/events.py:41-54: "ANN_FALLBACK = \"ann_fallback\"\nHASH_FALLBACK = \"hash_fallback\"\nLOCK_CONTENTION = \"lock_contention\"\n...\nRERANK_SKIPPED = \"rerank_skipped\""
    # Event catalog has 11 entries: ANN_FALLBACK, HASH_FALLBACK, LOCK_CONTENTION, TRUNCATE_RESULT, EMPTY_RESULT, SEMANTIC_BACKEND, TASK_LIFECYCLE, STRAY_SWEPT, SEMANTIC_UNAVAILABLE, EMBED_FLUSH_STALLED, RERANK_SKIPPED
    # grep -rn 'EMBED_SERVER_DEGRADED' src/ — zero hits
    src/cairn/telemetry/__init__.py:27-41: re-exports from events.py; __all__ lists 10 event names (no RERANK_SKIPPED — imported directly by semantic.py)
  status: DONE
  verify: uv run --extra dev pytest tests/test_semantic_events.py -q  # 10 passed
  gap: EMBED_SERVER_DEGRADED event does not exist yet. Must be added to events.py catalog and __init__.py __all__. The warn_once + emit pattern is the template. Reason-enum precedent: semantic_unavailable uses `_SEMANTIC_REASONS = frozenset({"unavailable", "no_embeddings", "error"})` (events.py:220).

item S08: "Doctor: check structure (_result, PASS/WARN/FAIL, 8 checks, ann check as shape reference)"
  evidence:
    src/cairn/cli/system.py:727-729: "_PASS = \"PASS\"\n_WARN = \"WARN\"\n_FAIL = \"FAIL\""
    src/cairn/cli/system.py:740-742: "def _result(name: str, status: str, detail: str, hint: str | None = None) -> dict:\n    return {\"name\": name, \"status\": status, \"detail\": detail, \"hint\": hint}"
    src/cairn/cli/system.py:1231-1266: "def _run_doctor(db: str) -> list[dict]:\n    ...\n    return [\n        _check_schema(conn),\n        _check_embeddings(conn),\n        _check_ann(conn),\n        _check_freshness(conn),\n        _check_parse_errors(conn),\n        _check_concurrency(conn),\n        _check_tool_health(conn),\n        _check_config(),\n    ]"
    # 8 checks total: schema, embeddings, ann, freshness, parse_errors, concurrency, tool_health, config
    src/cairn/cli/system.py:845-864: "def _check_embeddings(conn) -> dict:\n    ...\n    if is_hash_fallback():\n        return _result(\"embeddings\", _WARN, \"hash backend active -- token-overlap vectors, retrieval degraded\",\n            hint=\"install once: `cairn embed --install-deps`\")\n    return _result(\"embeddings\", _PASS, f\"backend: {configured}\")"
  status: DONE
  verify: uv run --extra dev pytest tests/test_doctor.py -q  # 27 passed
  gap: No server-backend doctor checks exist. FR-007 requires probe, model-listing, parity-sample, and latency checks. The _check_embeddings shape is the template. _check_config (line 1191-1206) echoes only env vars — no config.json surface.

item S09: "Dashboard: app.py route table (zero POST routes), templates, loopback-only binding, no settings surface"
  evidence:
    # Route count: 17 Route entries + 1 Mount (static)
    src/cairn/dashboard/app.py:583-606: "routes = [\n    Route(\"/\", landing, ...),\n    Route(\"/workspaces\", workspaces_overview, ...),\n    Route(\"/projects\", projects, ...),\n    Route(\"/graph\", graph, ...),\n    Route(\"/graph/candidates\", graph_candidates, ...),\n    Route(\"/graph/suggest\", graph_suggest, ...),\n    Route(\"/graph/neighbors\", graph_neighbors, ...),\n    Route(\"/history\", history, ...),\n    Route(\"/history.csv\", history_csv, ...),\n    Route(\"/history.json\", history_json, ...),\n    Route(\"/tokens\", tokens, ...),\n    Route(\"/tokens.csv\", tokens_csv, ...),\n    Route(\"/tokens.json\", tokens_json, ...),\n    Route(\"/chains\", chains, ...),\n    Route(\"/health\", health, ...),\n    Route(\"/memory\", memory, ...),\n    Route(\"/tasks\", tasks, ...),\n    Mount(\"/static\", ...),\n]"
    # grep 'methods=["POST"]' src/cairn/dashboard/app.py — zero hits (all routes are GET-only by default)
    # Templates: 12 files (base.html, index.html, workspaces.html, projects.html, graph.html, health.html, history.html, memory.html, tasks.html, tokens.html, chains.html, window_control.html, _links.html)
    # grep -rn 'setting' src/cairn/dashboard/templates/ — zero hits
    src/cairn/dashboard/app.py:32-33: "DEFAULT_HOST = \"127.0.0.1\"\nDEFAULT_PORT = 8765"
    src/cairn/cli/dashboard.py:20-31: "def _require_loopback(host: str) -> None:\n    if host == \"localhost\": return\n    ...\n    if not loopback:\n        raise click.UsageError(\"refusing to bind --host {host}: the dashboard is localhost-only\")"
  status: DONE
  verify: uv run --extra dev pytest tests/test_dashboard_app.py -q  # 69 passed
  gap: Zero POST routes exist. FR-011 requires loopback-only POST routes for settings. No settings.html or embeddings status template exists. No /settings route exists. The loopback guard in dashboard.py:20-31 is reusable.

item S10: "Config substrate: no ~/.cairn/config.json parser exists; only workspaces.json"
  evidence:
    # grep -rn 'config.json' src/ --include='*.py' | grep -v agent_install | grep -v reranker | head — zero hits for a cairn config file parser
    src/cairn/paths.py:29-31: "CAIRN_HOME = Path(\n    os.environ.get(\"CAIRN_HOME\", str(Path.home() / \".cairn\"))\n)"
    src/cairn/paths.py:33: "REGISTRY_FILE = CAIRN_HOME / \"workspaces.json\""
    src/cairn/paths.py:120-128: "def _load_registry() -> dict[str, str]:\n    if not REGISTRY_FILE.exists(): return {}\n    try:\n        data = json.loads(REGISTRY_FILE.read_text(encoding=\"utf-8\"))\n        return data if isinstance(data, dict) else {}\n    except (json.JSONDecodeError, OSError):\n        return {}"
    src/cairn/paths.py:91: "# _inject_shared_libs() called at import time of this module"
  status: TODO
  verify: grep -rn 'config.json' src/cairn/paths.py  # zero hits (only workspaces.json)
  gap: No ~/.cairn/config.json file parser, reader, or mtime re-read mechanism exists. FR-010 requires a full config substrate (env > file > default, mtime re-read, reset_backend_cache invalidation). CAIRN_HOME is bound at import time (paths.py:29-31) — a new config loader must read from the same CAIRN_HOME.

item S11: "install_hint() text and docs coverage of embedding backends"
  evidence:
    src/cairn/graph/embeddings.py:86-92: "def install_hint() -> str:\n    return (\n        \"Semantic search requires the 'semantic' extra. \"\n        \"Install it with: pip install 'cairn-intel[semantic]' \"\n        \"(or set CAIRN_EMBED_BACKEND=hash for a dep-free smoke test).\"\n    )"
    # docs/configuration.md line 50: "CAIRN_RERANK_MIN_MARGIN, CAIRN_ANN_BACKEND, CAIRN_EMBED_BACKEND,"
    # docs/configuration.md line 83: "embeddings fall back to a deterministic hash backend and retrieval still"
    # docs/retrieval.md line 74: "| `CAIRN_EMBED_BACKEND` | `local` | `local` (sentence-transformers) / `hash` / `openai` |"
    # No mention of server/omlx/ollama in either doc
  status: DONE
  verify: grep -n 'server\|omlx\|ollama' docs/configuration.md docs/retrieval.md  # zero hits
  gap: install_hint() mentions only `[semantic]` extra and hash fallback — no server path. docs/configuration.md and docs/retrieval.md list only local/hash/openai backends. FR-008 requires updating all three.

item S12: "Existing tests: coverage map for openai, hash fallback, backend switching, semantic provenance, doctor, dashboard"
  evidence:
    # Test files found (27 relevant):
    #   tests/test_embedding_backend_quality.py — 13 tests (is_hash_fallback, reset_backend_cache, provenance)
    #   tests/test_doctor.py — 27 tests
    #   tests/test_dashboard_app.py — 69 tests
    #   tests/test_model_warmup.py — 23 tests (including test_openai_backend_skips_embed_model)
    #   tests/test_semantic_events.py — 10 tests
    #   tests/test_embed_flush_stalled.py — 5 tests
    #   tests/test_semantic_unavailable.py — 12 tests
    #   tests/test_staleness_banner.py — 4 tests
    #   tests/test_ann_fallback_warning.py — uses reset_backend_cache
    #   tests/test_semantic_enrichment.py, test_fusion.py, test_rerank_gating.py, test_reranker.py
    #   tests/test_embeddings_mv.py, test_memory_embeddings.py, test_embeddings_freshness.py
    #   tests/test_embed_cli_download_model.py, test_embed_commit_tracking.py, test_ensure_semantic_deps.py
    #   tests/test_ann_index.py, test_ann_incremental.py, test_ann_vecmv.py
    #   tests/test_dashboard_data.py, test_dashboard_export.py, test_dashboard_live_soak.py,
    #   test_dashboard_readonly.py, test_dashboard_scale.py, test_dashboard_theme.py, test_dashboard_workspaces.py
    # CliRunner convention (from tests/test_knowledge_cli.py):
    #   from click.testing import CliRunner
    #   result = CliRunner().invoke(knowledge, [\"add\", ...])
    #   assert result.exit_code == 0, result.stdout
  status: DONE
  verify: uv run --extra dev pytest tests/test_doctor.py tests/test_dashboard_app.py tests/test_semantic_events.py tests/test_embed_flush_stalled.py tests/test_semantic_unavailable.py tests/test_staleness_banner.py -q
  gap: No tests exist for: server backend, _embed_server, parity check, fallback ladder, config.json substrate, dashboard POST routes, EMBED_SERVER_DEGRADED telemetry. reset_backend_cache is used in 7+ test files as a fixture (conftest.py:132-134).

item S13: "CLI embed surface (embed.py): options, exit codes, no server-specific options"
  evidence:
    src/cairn/cli/embed.py:10-42: "@main.command()\n@click.option(\"--db\", ...)
@click.option(\"--batch-size\", default=64, ...)
@click.option(\"--limit\", ...)
@click.option(\"--no-reap\", ...)
@click.option(\"--build-index\", ...)
@click.option(\"--install-deps\", ...)
@click.option(\"--download-model\", ...)
@click.option(\"--multivector\", ...)\ndef embed(db, batch_size, limit, no_reap, build_index, install_deps, download_model, multivector):"
    # Exit code 1 on unavailable: src/cairn/cli/embed.py:53,66,78 (sys.exit(1))
    # Exit code 1 on empty index: src/cairn/cli/embed.py:262 (sys.exit(1))
    # No --adopt-server-model, no server-specific options
  status: DONE
  verify: grep -n 'sys.exit' src/cairn/cli/embed.py  # lines 53, 66, 78, 262
  gap: No server-specific CLI options exist. FR-012's `cairn embed --adopt-server-model` does not exist. The embed command exits 1 on embeddings_available() failure — a server-down scenario would need to exit 1 with the remediation message.
```

## Supporting evidence

### Backend dispatch consumer inventory
- `_embed()` at `embeddings.py:806` — sole dispatch point for embed_all, embed_symbols, embed_knowledge, embed_memory, embed_memory_concepts, embed_query
- `current_model()` at `embeddings.py:39` — called by embed_all, embed_symbols, embed_knowledge, embed_memory, embed_memory_concepts, purge_stale_models, ann_query, _check_ann, _check_embeddings, CLI embed
- `embeddings_available()` at `embeddings.py:65` — called by CLI embed (line 74), CLI semantic (line 245), embed.py --download-model (line 63)
- `_effective_backend()` at `embeddings.py:323` — called by current_model, is_hash_fallback, embeddings_available, _embed, _warm_embedder
- `reset_backend_cache()` at `embeddings.py:314` — called by ensure_semantic_deps, 7+ test files, conftest.py

### Stamping flow
- Stamps are plain strings in the `model` column of embeddings/knowledge_embeddings/memory_embeddings/embeddings_mv tables
- vec0 table names: `vec_<safe-model>` (embeddings), `vecmv_<safe-model>` (embeddings_mv) via `_table_name()` at `ann_index.py:149`
- `safe = re.sub(r"[^a-zA-Z0-9_]", "_", model)` — a stamp `server/127.0.0.1:8000/bge-m3` becomes table name `vec_server_127_0_0_1_8000_bge-m3`

### purge_stale_models — defined, never called
- Definition: `embeddings.py:682`
- grep across all src/ found zero call sites (only the def line itself)
- The model-swap invalidation today relies on `current_model()` changing the stamp, which causes `embed_all` to see all rows as stale (content_hash mismatch under new model) and re-embed them. `purge_stale_models` is dead code.

### semantic_search dense-leg error propagation (spec claim verified)
- `emb.embed_query(dense_text)` at `semantic.py:769` has no try/except
- `_run_pass` at `semantic.py:1012` (`candidates = _run_pass(...)`) has no try/except
- The only exception handling in semantic_search is around RRF fusion (line 893-1009) and rerank (implicit via `rrk.rerank` returning `(final, False)` on failure)
- Confirmed: embed errors propagate uncaught out of semantic_search, as the spec claims

### Dashboard route count
- 16 `Route()` entries + 1 `Mount()` (static) = 17 route objects in the `routes` list (app.py:583-606)
- All routes use Starlette's default (GET only) — zero POST routes
- 12 HTML templates in `src/cairn/dashboard/templates/`
- No settings.html, no embeddings status view, no POST endpoints

### Config substrate
- `~/.cairn/workspaces.json` is the only JSON config file (paths.py:33, _load_registry at line 120)
- `~/.cairn/config.json` does not exist as a concept anywhere in the codebase
- `CAIRN_HOME` is bound at module import time (paths.py:29-31): `Path(os.environ.get("CAIRN_HOME", str(Path.home() / ".cairn")))`
- `SHARED_LIB = CAIRN_HOME / "lib"` (paths.py:45)
- `shared_lib_path()` returns `SHARED_LIB / _abi_tag()` or `CAIRN_LIB` override (paths.py:54-63)

### Telemetry event catalog (11 events)
1. `ANN_FALLBACK` (events.py:41)
2. `HASH_FALLBACK` (events.py:42)
3. `LOCK_CONTENTION` (events.py:43)
4. `TRUNCATE_RESULT` (events.py:44)
5. `EMPTY_RESULT` (events.py:45)
6. `SEMANTIC_BACKEND` (events.py:46)
7. `TASK_LIFECYCLE` (events.py:47)
8. `STRAY_SWEPT` (events.py:48)
9. `SEMANTIC_UNAVAILABLE` (events.py:49)
10. `EMBED_FLUSH_STALLED` (events.py:50)
11. `RERANK_SKIPPED` (events.py:54) — not in `__init__.py __all__`, imported directly by semantic.py

### Doctor check count
- 8 checks: schema, embeddings, ann, freshness, parse_errors, concurrency, tool_health, config
- _check_config (system.py:1191-1206) echoes 7 env vars, always PASS
- _check_embeddings (system.py:845-864) tests is_hash_fallback() only
- No server-backend doctor checks

### CliRunner test convention
- `from click.testing import CliRunner`
- `result = CliRunner().invoke(command, [args])`
- `assert result.exit_code == 0, result.stdout`
- Fixture pattern: `cli_env(tmp_path, monkeypatch)` setting CAIRN_DB/CAIRN_KNOWLEDGE
- Example file: `tests/test_knowledge_cli.py`

### install_hint() current text
- `embeddings.py:86-92`: mentions only `[semantic]` extra and `CAIRN_EMBED_BACKEND=hash`
- No mention of server/omlx/ollama path

### Docs coverage
- `docs/configuration.md:50` — mentions CAIRN_EMBED_BACKEND in env var list
- `docs/configuration.md:83` — mentions hash fallback
- `docs/retrieval.md:74` — table row: `CAIRN_EMBED_BACKEND | local | local / hash / openai`
- Neither doc mentions server, omlx, ollama, or CAIRN_EMBED_BASE_URL

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
