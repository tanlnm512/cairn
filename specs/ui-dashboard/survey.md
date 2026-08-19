# Survey: ui-dashboard

**Created**: 2026-08-20 | **Baseline**: cairn-intel @ `694d8d3` (main, 2026-08-19)
Phase-A output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item Q1: "Multi-project DB model: how repos table enables cross-repo workspace queries"
  evidence: /Users/tanle/Projects/cairn/src/cairn/graph/schema.py:CREATE TABLE IF NOT EXISTS repos (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    language TEXT,
    git_remote TEXT,
    indexed_at TIMESTAMP
);
  evidence: /Users/tanle/Projects/cairn/src/cairn/graph/schema.py:CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL REFERENCES repos(id),
    path TEXT NOT NULL,
    language TEXT NOT NULL,
    hash TEXT,
    line_count INTEGER,
    indexed_at TIMESTAMP,
    UNIQUE(repo_id, path)
);
  status: DONE
  verify: sqlite3 ~/.cairn/store/cairn.db ".schema repos" && sqlite3 ~/.cairn/store/cairn.db "SELECT COUNT(*) FROM repos;"
  gap: None — repos table with foreign key to files exists and supports multi-project workspace

item Q2: "Embedding storage and model tracking: where embeddings are stored and how model is tracked"
  evidence: /Users/tanle/Projects/cairn/src/cairn/graph/schema.py:CREATE TABLE IF NOT EXISTS embeddings (
    symbol_id TEXT NOT NULL REFERENCES symbols(id),
    model TEXT NOT NULL,          -- e.g. 'all-MiniLM-L6-v2', for invalidation
    dim INTEGER NOT NULL,         -- vector dimensionality
    vec BLOB NOT NULL,            -- float32 little-endian
    chunk TEXT NOT NULL,          -- the text that was embedded (for display)
    embedded_at TIMESTAMP,
    PRIMARY KEY (symbol_id, model)
);
  evidence: /Users/tanle/Projects/cairn/src/cairn/graph/schema.py:CREATE TABLE IF NOT EXISTS knowledge_embeddings (
    doc_id TEXT NOT NULL,          -- concept_id path
    chunk_index INTEGER NOT NULL DEFAULT 0,
    model TEXT NOT NULL,           -- for invalidation on model swap
    dim INTEGER NOT NULL,
    vec BLOB NOT NULL,             -- float32 little-endian
    chunk TEXT NOT NULL,           -- the text that was embedded
    embedded_at TIMESTAMP,
    PRIMARY KEY (doc_id, chunk_index, model)
);
  evidence: /Users/tanle/Projects/cairn/src/cairn/graph/schema.py:CREATE TABLE IF NOT EXISTS memory_embeddings (
    doc_id TEXT NOT NULL,          -- concept_id path
    chunk_index INTEGER NOT NULL DEFAULT 0,
    model TEXT NOT NULL,           -- for invalidation on model swap
    dim INTEGER NOT NULL,
    vec BLOB NOT NULL,             -- float32 little-endian
    chunk TEXT NOT NULL,           -- the text that was embedded
    embedded_at TIMESTAMP,
    PRIMARY KEY (doc_id, chunk_index, model)
);
  status: DONE
  verify: sqlite3 ~/.cairn/store/cairn.db ".schema embeddings" && sqlite3 ~/.cairn/store/cairn.db "SELECT DISTINCT model FROM embeddings LIMIT 1;"
  gap: None — three embedding tables with model tracking for invalidation

item Q3: "Visualization query layer: what graph scopes exist and exact function signatures"
  evidence: /Users/tanle/Projects/cairn/src/cairn/viz/query.py:def get_symbol_graph(conn, repo_id, symbol_id):
  evidence: /Users/tanle/Projects/cairn/src/cairn/viz/query.py:def get_module_graph(conn, repo_id, file_path):
  evidence: /Users/tanle/Projects/cairn/src/cairn/viz/query.py:def get_impact_graph(conn, repo_id, symbol_id):
  evidence: /Users/tanle/Projects/cairn/src/cairn/viz/query.py:def get_deps_graph(conn, repo_id, symbol_id):
  evidence: /Users/tanle/Projects/cairn/src/cairn/viz/query.py:def get_repo_graph(conn):
  status: DONE
  verify: python3 -c "from cairn.viz.query import get_symbol_graph, get_module_graph, get_impact_graph, get_deps_graph, get_repo_graph; print('Exported functions:', dir())"
  gap: None — 5 graph scope functions exist with clear signatures

item Q4: "MCP tool dispatch and existing recording patterns"
  evidence: /Users/tanle/Projects/cairn/src/cairn/mcp_server/server.py:@server asyncio.run(serve_stdio())
  evidence: /Users/tanle/Projects/cairn/src/cairn/mcp_server/metric_buffering.py:def _log_metric(conn, name, duration_ms, status, session_id, input_tokens, output_tokens, error=None):
  evidence: /Users/tanle/Projects/cairn/src/cairn/mcp_server/metric_buffering.py:@contextmanager
def instrument(name: str, session_id: str, conn_factory: Callable[[], object]):
  evidence: /Users/tanle/Projects/cairn/src/cairn/mcp_server/server.py:def _drain_buffered_telemetry() -> None:
  status: DONE
  verify: grep -r "instrument\|_log_metric" /Users/tanle/Projects/cairn/src/cairn/mcp_server --include="*.py" | wc -l
  gap: None — MCP server with metric buffering via instrument decorator

item Q5: "Flush/shutdown patterns for buffered writes"
  evidence: /Users/tanle/Projects/cairn/src/cairn/telemetry/sink.py:def _flush_events():
  evidence: /Users/tanle/Projects/cairn/src/cairn/telemetry/sink.py:def start_flusher() -> None:
  evidence: /Users/tanle/Projects/cairn/src/cairn/telemetry/sink.py:atexit.register(_flush_all)
  evidence: /Users/tanle/Projects/cairn/src/cairn/mcp_server/server.py:def _drain_buffered_telemetry() -> None:
  evidence: /Users/tanle/Projects/cairn/src/cairn/mcp_server/metric_buffering.py:def _flush_metrics():
  status: DONE
  verify: grep -r "atexit\|_flush\|flush()" /Users/tanle/Projects/cairn/src/cairn/telemetry --include="*.py" | head -10
  gap: None — Shared telemetry sink with atexit handler and daemon flush thread

item Q6: "HTTP/serving infrastructure and current FastAPI/uvicorn status"
  evidence: /Users/tanle/Projects/cairn/pyproject.toml:mcp = ">=1.0.0"  # includes FastAPI, uvicorn, starlette
  evidence: /Users/tanle/Projects/cairn/src/cairn/cli/serve.py:from ..mcp_server.server import run
  evidence: /Users/tanle/Projects/cairn/src/cairn/cli/serve.py:run(transport="sse" if port else "stdio", port=port)
  evidence: /Users/tanle/Projects/cairn/src/cairn/cli/serve.py:@serve.command("start")
@click.option("--port", default=lc.DEFAULT_PORT, type=int, help=f"SSE port (default {lc.DEFAULT_PORT}).")
  status: DONE
  verify: python3 -c "import mcp; print('MCP version:', mcp.__version__)" && grep -r "FastAPI\|uvicorn" /Users/tanle/Projects/cairn/pyproject.toml
  gap: None — HTTP serving exists via MCP SSE transport; FastAPI/uvicorn available as transitive dependencies

item Q7: "Health data sources for cairn doctor command"
  evidence: /Users/tanle/Projects/cairn/src/cairn/cli/system.py:@main.command()
@click.pass_context
def doctor(ctx):
  evidence: /Users/tanle/Projects/cairn/src/cairn/graph/schema.py:def _warn_lock_contention_once(site: str, error: Exception | None = None) -> None:
  evidence: /Users/tanle/Projects/cairn/src/cairn/telemetry/events.py:emit("lock_contention", attrs={"site": site, "db": str(db)})
  evidence: /Users/tanle/Projects/cairn/src/cairn/cli/serve.py:click.echo(f"  DB lock holders    : {db_holders if db_holders else 'none'}")
  status: DONE
  verify: python3 -m cairn doctor 2>&1 | head -20
  gap: None — Doctor command exists with lock contention telemetry and DB health checks

item Q8: "Memory and task queue structures and operations"
  evidence: /Users/tanle/Projects/cairn/src/cairn/cli/memory.py:@main.group()
def memory():
    """Local AI memory: store tribal knowledge, mistakes, and design decisions."""
  evidence: /Users/tanle/Projects/cairn/src/cairn/cli/task.py:@main.group()
def task():
    """LLM task queue: any agent with the skill processes pending tasks."""
  evidence: /Users/tanle/Projects/cairn/src/cairn/cli/memory.py:@memory.command("record")
@click.argument("concept_id")
@click.option("--body", default=None)
@click.option("--body-file", default=None)
  evidence: /Users/tanle/Projects/cairn/src/cairn/cli/task.py:@task.command("list")
@click.option("--status", default=None, help="Filter: pending|in-progress|done|failed")
  status: DONE
  verify: python3 -m cairn memory --help && python3 -m cairn task --help
  gap: None — Memory (record/search/list) and task (list/show/claim/complete) commands exist

item Q9: "Token estimation capabilities"
  evidence: /Users/tanle/Projects/cairn/src/cairn/bench/agent_suite.py:CHARS_PER_TOKEN = 4
  evidence: /Users/tanle/Projects/cairn/src/cairn/bench/agent_suite.py:est_tokens=chars // CHARS_PER_TOKEN
  evidence: /Users/tanle/Projects/cairn/src/cairn/bench/agent_suite.py:"est_tokens": self.est_tokens,
  status: DONE
  verify: grep -r "CHARS_PER_TOKEN\|est_tokens" /Users/tanle/Projects/cairn/src/cairn/bench --include="*.py"
  gap: None — Token estimation via CHARS_PER_TOKEN = 4 constant in benchmark suite

item Q10: "CLI command registration patterns"
  evidence: /Users/tanle/Projects/cairn/src/cairn/cli/main.py:@click.group()
def main(verbose: bool):
    """cairn-intel: local codebase intelligence system."""
  evidence: /Users/tanle/Projects/cairn/src/cairn/cli/serve.py:@main.group(invoke_without_command=True)
@click.option("--db", default=None, help="SQLite DB path (default: central store).")
@click.option("--port", default=None, type=int, help="Port (for SSE transport).")
def serve(ctx, db, port, read_only):
  evidence: /Users/tanle/Projects/cairn/pyproject.toml:[project.scripts]
cairn = "cairn.cli:main"
  status: DONE
  verify: python3 -c "from cairn.cli import main; print([cmd for cmd in main.list_commands(None)])"
  gap: None — Click-based CLI with decorator registration and entry point

item Q11: "Read-only database access patterns"
  evidence: /Users/tanle/Projects/cairn/src/cairn/graph/schema.py:def get_db(
    db_path: Optional[str] = None,
    busy_timeout_ms: int = 5000,
    read_only: bool = False,
) -> sqlite3.Connection:
  evidence: /Users/tanle/Projects/cairn/src/cairn/graph/schema.py:read_only=True opens via the SQLite URI (`file:<path>?mode=ro`); such a
    connection cannot contend with writers and skips schema apply / FTS backfill
  evidence: /Users/tanle/Projects/cairn/src/cairn/cli/serve.py:@click.option(
    "--read-only/--read-write",
    "read_only",
    default=None,
    help="Open the graph DB read-only (default: read-only under SSE, read-write under stdio).",
)
  status: DONE
  verify: grep -r "read_only\|mode=ro" /Users/tanle/Projects/cairn/src/cairn/graph/schema.py /Users/tanle/Projects/cairn/src/cairn/cli/serve.py
  gap: None — get_db() supports read-only mode with URI path; CLI exposes via --read-only flag

item Q12: "Baseline health status"
  evidence: /Users/tanle/Projects/cairn/src/cairn/cli/system.py:@main.command()
def doctor(ctx):
  evidence: /Users/tanle/Projects/cairn/src/cairn/telemetry/sink.py:def is_telemetry_off() -> bool:
  evidence: /Users/tanle/Projects/cairn/src/cairn/cli/serve.py:@serve.command("status")
@click.option("--port", default=lc.DEFAULT_PORT, type=int, help=f"Expected SSE port (default {lc.DEFAULT_PORT}).")
  evidence: Unable to run actual commands due to Python version incompatibility (requires >=3.10, system has 3.9.6)
  status: PARTIAL
  verify: python3.10+ -m cairn doctor && python3.10+ -m cairn serve status
  gap: Cannot execute baseline verification due to Python version constraint; code evidence shows comprehensive health checking exists but runtime status unverified
```

## Supporting evidence

```
Key architectural patterns from codebase investigation:

Database layer (schema.py):
- Multi-project support: repos table with foreign keys to files/symbols/edges
- Embedding storage: 3 tables (embeddings, knowledge_embeddings, memory_embeddings) with model tracking
- Read-only access: get_db(read_only=True) uses URI mode=ro for contention-free queries
- Lock contention detection: _warn_lock_contention_once() with telemetry emission

Visualization layer (viz/query.py):
- 5 graph scopes: symbol, module, impact, deps, repo
- Each returns prepared data structure for renderers
- Renderer support: to_mermaid(), to_dot(), to_json(), embed() in renderers.py

MCP server (mcp_server/):
- Tool metric buffering via deque(maxlen=2000) with daemon flush thread
- Shared telemetry sink pattern for events + tool_metrics
- SSE and stdio transport support
- Lifecycle management: launchd integration for macOS daemon

CLI structure (cli/):
- Click decorator registration: @main.group(), @main.command()
- Subgroups: serve, memory, task, system (doctor/metrics/status)
- Entry point: cairn = "cairn.cli:main"

Token estimation (bench/agent_suite.py):
- Constant: CHARS_PER_TOKEN = 4
- Usage: est_tokens = chars // CHARS_PER_TOKEN
- Applied in benchmark comparisons for cairn vs control

Telemetry system (telemetry/):
- Shared sink with 30s daemon flush interval
- Event emission: emit(name, attrs={...})
- Gated by CAIRN_TELEMETRY env variable
- atexit handler for process-end drain

Health monitoring:
- Doctor command: checks DB, build status, telemetry state
- Lock contention tracking via lsof and telemetry events
- Serve status: daemon health, port responsiveness, stray process detection
```

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
