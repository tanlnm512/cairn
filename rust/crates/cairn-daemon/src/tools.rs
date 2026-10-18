//! The MCP tool surface: catalog and dispatch over the cairn-query ports.

use std::path::{Path, PathBuf};

use rmcp::model::{Tool, ToolAnnotations};
use serde_json::Value;

use cairn_query::tools_federation as federations;
use cairn_query::tools_flow as flow;
use cairn_query::tools_km as km;
use cairn_query::tools_nav as nav;
use cairn_query::tools_repo as repo;
use cairn_query::tools_search as search;
use cairn_query::tools_wiki as wiki;
use rusqlite::Connection;

/// Per-call context a server instance resolves once and dispatch threads through.
#[derive(Clone)]
pub struct ToolCtx {
    pub db_path: PathBuf,
    pub workspace: PathBuf,
    pub knowledge_path: PathBuf,
    pub read_only: bool,
    /// `mcp-<pid>-<date>` when writable; None gates recall's read-through refs write.
    pub session_id: Option<String>,
}

impl ToolCtx {
    pub fn new(db_path: &Path, workspace: &Path, knowledge_path: &Path, read_only: bool) -> Self {
        let session_id = if read_only {
            None
        } else {
            Some(format!(
                "mcp-{}-{}",
                std::process::id(),
                km::iso_utc_date_now()
            ))
        };
        Self {
            db_path: db_path.to_path_buf(),
            workspace: workspace.to_path_buf(),
            knowledge_path: knowledge_path.to_path_buf(),
            read_only,
            session_id,
        }
    }

    fn conn(&self) -> Result<Connection, String> {
        if self.read_only {
            cairn_store::conn::open_read_only(&self.db_path)
        } else {
            Connection::open(&self.db_path)
        }
        .map_err(|e| format!("store open failed: {e}"))
    }

    fn bundle(&self) -> km::Bundle {
        km::Bundle::new(self.knowledge_path.clone())
    }
}

/// Tool failure surfaced as an isError tool result (the FastMCP exception path).
pub struct ToolFailure(pub String);

impl From<String> for ToolFailure {
    fn from(message: String) -> Self {
        Self(message)
    }
}

fn open_read_conn(ctx: &ToolCtx) -> Result<Connection, ToolFailure> {
    cairn_store::conn::open_read_only(&ctx.db_path)
        .map_err(|e| ToolFailure(format!("store open failed: {e}")))
}

// --- argument extraction -----------------------------------------------------

fn arg_str<'a>(args: &'a Value, key: &str) -> Option<&'a str> {
    args.get(key).and_then(Value::as_str)
}

fn arg_str_or<'a>(args: &'a Value, key: &str, default: &'a str) -> &'a str {
    arg_str(args, key).unwrap_or(default)
}

fn arg_i64_or(args: &Value, key: &str, default: i64) -> i64 {
    args.get(key).and_then(Value::as_i64).unwrap_or(default)
}

fn arg_bool_or(args: &Value, key: &str, default: bool) -> bool {
    args.get(key).and_then(Value::as_bool).unwrap_or(default)
}

// --- catalog -----------------------------------------------------------------

#[allow(clippy::field_reassign_with_default)]
fn text_tool(
    name: &str,
    description: &str,
    properties: Value,
    required: &[&str],
    read_only: bool,
    destructive: bool,
    idempotent: bool,
) -> Tool {
    let Value::Object(props) = properties else {
        unreachable!("properties is an object literal");
    };
    let mut schema = serde_json::Map::new();
    schema.insert("type".into(), Value::String("object".into()));
    schema.insert("properties".into(), Value::Object(props));
    if !required.is_empty() {
        schema.insert(
            "required".into(),
            Value::Array(
                required
                    .iter()
                    .map(|r| Value::String((*r).into()))
                    .collect(),
            ),
        );
    }
    let mut tool = Tool::new(name.to_string(), description.to_string(), schema);
    let mut annotations = ToolAnnotations::default();
    annotations.read_only_hint = Some(read_only);
    annotations.destructive_hint = Some(destructive);
    annotations.idempotent_hint = Some(idempotent);
    tool.annotations = Some(annotations);
    tool
}

fn prop(name: &str, type_: &str, description: &str) -> Value {
    serde_json::json!({ name: { "type": type_, "description": description } })
}

fn prop_default(name: &str, type_: &str, description: &str, default: Value) -> Value {
    let mut value = prop(name, type_, description);
    value[name]["default"] = default;
    value
}

/// The full registered tool surface (one entry per ported cairn-query tool).
pub fn catalog() -> Vec<Tool> {
    vec![
        text_tool(
            "search_symbols",
            "Lexical symbol search (FTS5/BM25, wildcards, optional kind filter) — the default discovery entry point.",
            serde_json::json!({
                "pattern": { "type": "string", "description": "Symbol name pattern with optional trailing * wildcard." },
                "kind": prop_default("kind", "string", "Filter by symbol kind (function, class, ...).", Value::String(String::new())),
            }),
            &["pattern"],
            true,
            false,
            true,
        ),
        text_tool(
            "semantic_search",
            "Search symbols by meaning (BM25+vector fusion by default, CAIRN_FUSION=0 for cosine; optional rerank).",
            serde_json::json!({
                "query": prop("query", "string", "Natural-language query."),
                "limit": prop_default("limit", "integer", "Maximum matches to return (1-1000).", serde_json::json!(20)),
                "rerank": { "type": "boolean", "description": "Force on/off the cross-encoder rerank stage when enabled.", "nullable": true },
            }),
            &["query"],
            true,
            false,
            true,
        ),
        text_tool(
            "find_definition",
            "Find where a symbol is defined (file:line, kind, qualified name) with exact→qualified→substring fallback.",
            serde_json::json!({
                "name": prop("name", "string", "Symbol name to locate."),
                "limit": prop_default("limit", "integer", "Maximum rows (1-1000).", serde_json::json!(50)),
            }),
            &["name"],
            true,
            false,
            true,
        ),
        text_tool(
            "get_callers",
            "Who calls this symbol? Precise by default, auto-retrying fuzzy when precise is empty; structured=True returns typed rows.",
            serde_json::json!({
                "name": prop("name", "string", "Symbol name."),
                "fuzzy": prop_default("fuzzy", "boolean", "Include unresolved name-match candidates.", Value::Bool(false)),
                "limit": prop_default("limit", "integer", "Maximum rows (1-1000).", serde_json::json!(200)),
            }),
            &["name"],
            true,
            false,
            true,
        ),
        text_tool(
            "get_callees",
            "What does this function/symbol call? Precise by default, fuzzy adds unresolved edges; structured=True returns typed rows.",
            serde_json::json!({
                "name": prop("name", "string", "Symbol name."),
                "fuzzy": prop_default("fuzzy", "boolean", "Include unresolved name-match candidates.", Value::Bool(false)),
                "limit": prop_default("limit", "integer", "Maximum rows (1-1000).", serde_json::json!(200)),
            }),
            &["name"],
            true,
            false,
            true,
        ),
        text_tool(
            "impact_analysis",
            "Recursive caller impact up to depth (precise by default; fuzzy adds unresolved edges; cached reads precomputed dataflow).",
            serde_json::json!({
                "name": prop("name", "string", "Symbol to analyze blast radius for."),
                "depth": prop_default("depth", "integer", "Traversal depth (1-10).", serde_json::json!(5)),
                "fuzzy": prop_default("fuzzy", "boolean", "Traverse unresolved name-match edges too.", Value::Bool(false)),
                "limit": prop_default("limit", "integer", "Maximum impacted rows (1-1000).", serde_json::json!(500)),
            }),
            &["name"],
            true,
            false,
            true,
        ),
        text_tool(
            "path",
            "Trace the shortest structural path between two symbol patterns; fuzzy follows ambiguous hops.",
            serde_json::json!({
                "from_pattern": prop("from_pattern", "string", "Origin symbol pattern (substring, case-insensitive)."),
                "to_pattern": prop("to_pattern", "string", "Destination symbol pattern (substring, case-insensitive)."),
                "fuzzy": prop_default("fuzzy", "boolean", "Follow ambiguous/unresolved hops within a repo.", Value::Bool(false)),
                "max_depth": prop_default("max_depth", "integer", "Maximum hops (1-10).", serde_json::json!(4)),
                "limit": prop_default("limit", "integer", "Maximum paths shown (1-1000).", serde_json::json!(50)),
            }),
            &["from_pattern", "to_pattern"],
            true,
            false,
            true,
        ),
        text_tool(
            "explore",
            "Answer 'how does X work' in one call: matching source, call paths, blast radius, memory, rationale, and taint warnings.",
            serde_json::json!({
                "query": prop("query", "string", "Symbol name or natural-language question."),
            }),
            &["query"],
            true,
            false,
            true,
        ),
        text_tool(
            "trace_flow",
            "Execution order for an entry point: ordered chain, branch points, terminal calls.",
            serde_json::json!({
                "entry": prop("entry", "string", "Entry-point symbol name (HTTP handler, CLI command, ...)."),
                "max_depth": prop_default("max_depth", "integer", "Deepest call hop to follow.", serde_json::json!(8)),
            }),
            &["entry"],
            true,
            false,
            true,
        ),
        text_tool(
            "cross_repo_deps",
            "Cross-repo dependency map: what the repo depends on and what depends on it.",
            serde_json::json!({
                "repo": prop("repo", "string", "Repo name in the graph."),
                "limit": prop_default("limit", "integer", "Max rows per section (1-1000).", serde_json::json!(50)),
            }),
            &["repo"],
            true,
            false,
            true,
        ),
        text_tool(
            "visualize_graph",
            "Generate a Mermaid/DOT/JSON diagram of a graph scope (symbol|module|impact|repo|deps).",
            serde_json::json!({
                "scope": prop_default("scope", "string", "Diagram scope: symbol|impact|module|repo|deps.", Value::String("symbol".into())),
                "symbol": prop_default("symbol", "string", "Focal symbol for symbol/impact scopes.", Value::String(String::new())),
                "module": prop_default("module", "string", "Module for the module scope.", Value::String(String::new())),
                "repo": prop_default("repo", "string", "Repo for the repo scope.", Value::String(String::new())),
                "depth": prop_default("depth", "integer", "Depth for the impact scope (1-10).", serde_json::json!(3)),
                "format": prop_default("format", "string", "Output format: mermaid|dot|json.", Value::String("mermaid".into())),
            }),
            &[],
            true,
            false,
            true,
        ),
        text_tool(
            "recall_memory",
            "Search past decisions/patterns/mistakes/workarounds by symbol or title tokens, showing live refs-verified fractions; tier/include_superseded/as_of/agent filter.",
            serde_json::json!({
                "query": prop("query", "string", "Symbol name or title tokens."),
                "tier": prop_default("tier", "string", "Filter: decision|pattern|mistake|workaround.", Value::String(String::new())),
                "include_superseded": prop_default("include_superseded", "boolean", "Include the full revision history.", Value::Bool(false)),
                "as_of": { "type": "string", "description": "Point-in-time recall (YYYY-MM-DD).", "nullable": true },
                "agent": { "type": "string", "description": "Merge shared memories from this agent id.", "nullable": true },
            }),
            &["query"],
            true,
            false,
            true,
        ),
        text_tool(
            "record_memory",
            "Capture a learning (type: decision|pattern|mistake|workaround); Why:/How to apply: body lines make it durable; stance optional.",
            serde_json::json!({
                "type": prop("type", "string", "decision|pattern|mistake|workaround."),
                "title": prop("title", "string", "Short title."),
                "body": prop("body", "string", "The learning; Why:/How to apply: lines for decisions/mistakes/workarounds."),
                "resource": prop_default("resource", "string", "Optional resource path or URL.", Value::String(String::new())),
                "confidence": prop_default("confidence", "number", "0.0-1.0.", serde_json::json!(0.7)),
            }),
            &["type", "title", "body"],
            false,
            false,
            false,
        ),
        text_tool(
            "knowledge_add",
            "Ingest a business document as knowledge. doc_type: business-rule | spec | decision.",
            serde_json::json!({
                "title": prop("title", "string", "Document title."),
                "body": prop("body", "string", "Document body."),
                "doc_type": prop("doc_type", "string", "business-rule | spec | decision."),
                "tags": prop_default("tags", "string", "Comma-separated tags.", Value::String(String::new())),
                "affects_modules": prop_default("affects_modules", "string", "Comma-separated modules.", Value::String(String::new())),
                "affects_repos": prop_default("affects_repos", "string", "Comma-separated repos.", Value::String(String::new())),
                "resource": prop_default("resource", "string", "Optional resource path.", Value::String(String::new())),
                "epic_link": prop_default("epic_link", "string", "Optional epic link.", Value::String(String::new())),
            }),
            &["title", "body", "doc_type"],
            false,
            false,
            false,
        ),
        text_tool(
            "knowledge_search",
            "Search business knowledge docs by meaning.",
            serde_json::json!({
                "query": prop("query", "string", "Meaning-based query."),
                "limit": prop_default("limit", "integer", "Maximum docs (1-1000).", serde_json::json!(20)),
            }),
            &["query"],
            true,
            false,
            true,
        ),
        text_tool(
            "knowledge_delete",
            "Delete a knowledge document and its embedding rows. Irreversible.",
            serde_json::json!({
                "doc_id": prop("doc_id", "string", "Concept id of the document."),
            }),
            &["doc_id"],
            false,
            true,
            true,
        ),
        text_tool(
            "knowledge_status",
            "Update doc_status on a knowledge document (active → superseded → archived).",
            serde_json::json!({
                "doc_id": prop("doc_id", "string", "Concept id of the document."),
                "new_status": prop("new_status", "string", "active | superseded | archived."),
            }),
            &["doc_id", "new_status"],
            false,
            false,
            false,
        ),
        text_tool(
            "trace_workflow",
            "Trace a procedural workflow's ordered steps by title, slug, or concept_id.",
            serde_json::json!({
                "ref": prop("ref", "string", "Workflow title, slug, or concept id."),
            }),
            &["ref"],
            true,
            false,
            true,
        ),
        text_tool(
            "get_compass",
            "Get the compass navigation guide for a module. Returns the OKF compass body.",
            serde_json::json!({
                "module": prop("module", "string", "Module name."),
            }),
            &["module"],
            true,
            false,
            true,
        ),
        text_tool(
            "search_knowledge",
            "Search the knowledge base (wiki, compass, patterns, memory).",
            serde_json::json!({
                "query": prop("query", "string", "Query string."),
                "type_filter": prop_default("type_filter", "string", "Wiki|Pattern|Compass|Memory or empty for all.", Value::String(String::new())),
                "limit": prop_default("limit", "integer", "Maximum results.", serde_json::json!(10)),
                "full_body": prop_default("full_body", "boolean", "Return full concept bodies.", Value::Bool(false)),
            }),
            &["query"],
            true,
            false,
            true,
        ),
        text_tool(
            "ask_compass",
            "Natural language across all layers. Routes to graph/wiki/compass/memory.",
            serde_json::json!({
                "query": prop("query", "string", "Natural language question, e.g. 'where do we handle retries'."),
                "file_path": prop_default("file_path", "string", "Optional source file for file-path-aware mode.", Value::String(String::new())),
            }),
            &["query"],
            true,
            false,
            true,
        ),
        text_tool(
            "repo_map",
            "Return a deterministic repository orientation map (clusters, hubs, hotspots, dropped counts).",
            serde_json::json!({}),
            &[],
            true,
            false,
            true,
        ),
        text_tool(
            "file_api",
            "Return every stored symbol in one indexed file (no bodies); repo disambiguates duplicate relative paths.",
            serde_json::json!({
                "path": prop("path", "string", "File path as stored in the graph (workspace-relative)."),
                "repo": { "type": "string", "description": "Repo id when the path exists in several repos.", "nullable": true },
            }),
            &["path"],
            true,
            false,
            true,
        ),
        text_tool(
            "generate_flow",
            "Generate a flow compass (and optionally a workflow) from a call-graph trace.",
            serde_json::json!({
                "entry": prop("entry", "string", "Entry-point symbol name."),
                "as_workflow": prop_default("as_workflow", "boolean", "Also write a Knowledge-workflow doc.", Value::Bool(false)),
                "max_steps": prop_default("max_steps", "integer", "Workflow step cap.", serde_json::json!(20)),
            }),
            &["entry"],
            false,
            false,
            true,
        ),
        text_tool(
            "federated_search",
            "Search every registered store by meaning, merging rank-fused hits attributed per workspace; unavailable stores are named, never fatal.",
            serde_json::json!({
                "query": prop("query", "string", "Natural-language phrase."),
                "limit": prop_default("limit", "integer", "Maximum merged hits (1-1000).", serde_json::json!(20)),
            }),
            &["query"],
            true,
            false,
            true,
        ),
        text_tool(
            "wiki_generate",
            "Plan the deterministic wiki and queue one critic-gated task per page (refine_catalog queues the catalog task instead).",
            serde_json::json!({
                "repo": prop("repo", "string", "Repo id in the graph."),
                "pages": prop_default("pages", "integer", "Page-plan cap including the overview (1-50).", serde_json::json!(10)),
                "refine_catalog": prop_default("refine_catalog", "boolean", "Queue the catalog refinement task instead.", Value::Bool(false)),
                "diagrams": prop_default("diagrams", "boolean", "Carry the diagrams flag into page work orders.", Value::Bool(false)),
                "force": prop_default("force", "boolean", "Re-queue every page even when covered.", Value::Bool(false)),
            }),
            &["repo"],
            false,
            false,
            false,
        ),
    ]
}

// --- dispatch ----------------------------------------------------------------

/// Run one named tool; unknown names and store failures surface as ToolFailure.
pub fn dispatch(ctx: &ToolCtx, name: &str, args: &Value) -> Result<String, ToolFailure> {
    match name {
        "search_symbols" => {
            let conn = open_read_conn(ctx)?;
            let kind = arg_str_or(args, "kind", "");
            let kind = if kind.is_empty() { None } else { Some(kind) };
            Ok(search::search_symbols_response(
                &conn,
                arg_str(args, "pattern").ok_or_else(|| {
                    ToolFailure("search_symbols requires a string pattern".into())
                })?,
                kind,
            ))
        }
        "semantic_search" => {
            let conn = open_read_conn(ctx)?;
            let limit = arg_i64_or(args, "limit", 20).clamp(1, 1000) as usize;
            let rerank = args.get("rerank").and_then(Value::as_bool);
            Ok(search::semantic_search_response(
                &conn,
                arg_str(args, "query")
                    .ok_or_else(|| ToolFailure("semantic_search requires a string query".into()))?,
                limit,
                rerank,
            ))
        }
        "find_definition" => {
            let conn = open_read_conn(ctx)?;
            nav::find_definition_text(
                &conn,
                arg_str(args, "name")
                    .ok_or_else(|| ToolFailure("find_definition requires a string name".into()))?,
                arg_i64_or(args, "limit", 50),
                "",
            )
            .map_err(|e| ToolFailure(e.to_string()))
        }
        "get_callers" | "get_callees" => {
            let conn = open_read_conn(ctx)?;
            let symbol = arg_str(args, "name")
                .ok_or_else(|| ToolFailure(format!("{name} requires a string name")))?;
            let fuzzy = arg_bool_or(args, "fuzzy", false);
            let limit = arg_i64_or(args, "limit", 200);
            let result = if name == "get_callers" {
                nav::callers_text(&conn, symbol, fuzzy, limit, "")
            } else {
                nav::callees_text(&conn, symbol, fuzzy, limit, "")
            };
            result.map_err(|e| ToolFailure(e.to_string()))
        }
        "impact_analysis" => {
            let conn = open_read_conn(ctx)?;
            let namespaces = cairn_query::graph::resolve_namespaces(&ctx.workspace, None);
            let ns_refs: Vec<(&str, &str)> = namespaces
                .iter()
                .map(|(k, v)| (k.as_str(), v.as_str()))
                .collect();
            nav::impact_analysis_text(
                &conn,
                arg_str(args, "name")
                    .ok_or_else(|| ToolFailure("impact_analysis requires a string name".into()))?,
                arg_i64_or(args, "depth", 5),
                arg_bool_or(args, "fuzzy", false),
                arg_i64_or(args, "limit", 500),
                "",
                &ns_refs,
            )
            .map_err(|e| ToolFailure(e.to_string()))
        }
        "path" => {
            let conn = open_read_conn(ctx)?;
            flow::path_text(
                &conn,
                arg_str(args, "from_pattern")
                    .ok_or_else(|| ToolFailure("path requires a string from_pattern".into()))?,
                arg_str(args, "to_pattern")
                    .ok_or_else(|| ToolFailure("path requires a string to_pattern".into()))?,
                arg_bool_or(args, "fuzzy", false),
                arg_i64_or(args, "max_depth", 4),
                arg_i64_or(args, "limit", 50),
            )
            .map_err(|e| ToolFailure(e.to_string()))
        }
        "repo_map" => {
            let conn = open_read_conn(ctx)?;
            repo::repo_map_text(&conn).map_err(|e| ToolFailure(e.to_string()))
        }
        "file_api" => {
            let conn = open_read_conn(ctx)?;
            repo::file_api_text(
                &conn,
                arg_str(args, "path")
                    .ok_or_else(|| ToolFailure("file_api requires a string path".into()))?,
                arg_str(args, "repo"),
            )
            .map_err(|e| ToolFailure(e.0))
        }
        "explore" => {
            // A writable-capable conn: explore credits the tribal memories it
            // renders (analytics write, the Python `_conn()` contract).
            let conn = ctx.conn()?;
            flow::explore_text(
                &conn,
                Some(&ctx.bundle()),
                &ctx.workspace,
                arg_str(args, "query")
                    .ok_or_else(|| ToolFailure("explore requires a string query".into()))?,
                ctx.read_only,
                ctx.session_id.as_deref(),
            )
            .map_err(|e| ToolFailure(e.to_string()))
        }
        "trace_flow" => {
            let conn = open_read_conn(ctx)?;
            flow::trace_flow_text(
                &conn,
                arg_str(args, "entry")
                    .ok_or_else(|| ToolFailure("trace_flow requires a string entry".into()))?,
                arg_i64_or(args, "max_depth", 8),
            )
            .map_err(|e| ToolFailure(e.to_string()))
        }
        "cross_repo_deps" => {
            let conn = open_read_conn(ctx)?;
            flow::cross_repo_deps_text(
                &conn,
                &ctx.workspace,
                arg_str(args, "repo")
                    .ok_or_else(|| ToolFailure("cross_repo_deps requires a string repo".into()))?,
                arg_i64_or(args, "limit", 50),
            )
            .map_err(|e| ToolFailure(e.to_string()))
        }
        "visualize_graph" => {
            let conn = open_read_conn(ctx)?;
            flow::visualize_graph_text(
                &conn,
                &ctx.workspace,
                flow::VizQuery {
                    scope: arg_str_or(args, "scope", "symbol"),
                    symbol: arg_str_or(args, "symbol", ""),
                    module: arg_str_or(args, "module", ""),
                    repo: arg_str_or(args, "repo", ""),
                    depth: arg_i64_or(args, "depth", 3),
                    format: arg_str_or(args, "format", "mermaid"),
                },
            )
            .map_err(|e| ToolFailure(e.to_string()))
        }
        "recall_memory" => {
            let conn = ctx.conn()?;
            km::recall_memory(
                &conn,
                &ctx.bundle(),
                arg_str(args, "query")
                    .ok_or_else(|| ToolFailure("recall_memory requires a string query".into()))?,
                arg_str_or(args, "tier", ""),
                arg_bool_or(args, "include_superseded", false),
                arg_str(args, "as_of"),
                arg_str(args, "agent"),
                ctx.session_id.as_deref(),
            )
            .map_err(|e| ToolFailure(e.0))
        }
        "record_memory" => {
            let conn = ctx.conn()?;
            km::record_memory(
                &conn,
                &ctx.bundle(),
                arg_str(args, "type")
                    .ok_or_else(|| ToolFailure("record_memory requires a string type".into()))?,
                arg_str(args, "title")
                    .ok_or_else(|| ToolFailure("record_memory requires a string title".into()))?,
                arg_str(args, "body")
                    .ok_or_else(|| ToolFailure("record_memory requires a string body".into()))?,
                arg_str_or(args, "resource", ""),
                args.get("confidence")
                    .and_then(Value::as_f64)
                    .unwrap_or(0.7),
                arg_str(args, "stance"),
            )
            .map_err(|e| ToolFailure(e.0))
        }
        "knowledge_add" => km::knowledge_add(
            &ctx.bundle(),
            arg_str(args, "title")
                .ok_or_else(|| ToolFailure("knowledge_add requires a string title".into()))?,
            arg_str(args, "body")
                .ok_or_else(|| ToolFailure("knowledge_add requires a string body".into()))?,
            arg_str(args, "doc_type")
                .ok_or_else(|| ToolFailure("knowledge_add requires a string doc_type".into()))?,
            arg_str_or(args, "tags", ""),
            arg_str_or(args, "affects_modules", ""),
            arg_str_or(args, "affects_repos", ""),
            arg_str_or(args, "resource", ""),
            arg_str_or(args, "epic_link", ""),
            ctx.read_only,
        )
        .map_err(|e| ToolFailure(e.0)),
        "knowledge_search" => {
            let conn = open_read_conn(ctx)?;
            km::knowledge_search(
                &conn,
                &ctx.bundle(),
                arg_str(args, "query").ok_or_else(|| {
                    ToolFailure("knowledge_search requires a string query".into())
                })?,
                arg_i64_or(args, "limit", 20),
            )
            .map_err(|e| ToolFailure(e.0))
        }
        "knowledge_delete" => {
            let conn = ctx.conn()?;
            km::knowledge_delete(
                &conn,
                &ctx.bundle(),
                arg_str(args, "doc_id").ok_or_else(|| {
                    ToolFailure("knowledge_delete requires a string doc_id".into())
                })?,
                ctx.read_only,
            )
            .map_err(|e| ToolFailure(e.0))
        }
        "knowledge_status" => km::knowledge_status(
            &ctx.bundle(),
            arg_str(args, "doc_id")
                .ok_or_else(|| ToolFailure("knowledge_status requires a string doc_id".into()))?,
            arg_str(args, "new_status").ok_or_else(|| {
                ToolFailure("knowledge_status requires a string new_status".into())
            })?,
            ctx.read_only,
        )
        .map_err(|e| ToolFailure(e.0)),
        "trace_workflow" => km::trace_workflow_tool(
            &ctx.bundle(),
            arg_str(args, "ref")
                .ok_or_else(|| ToolFailure("trace_workflow requires a string ref".into()))?,
        )
        .map_err(|e| ToolFailure(e.0)),
        "get_compass" => km::get_compass(
            &ctx.bundle(),
            arg_str(args, "module")
                .ok_or_else(|| ToolFailure("get_compass requires a string module".into()))?,
        )
        .map_err(|e| ToolFailure(e.0)),
        "search_knowledge" => km::search_knowledge(
            &ctx.bundle(),
            arg_str(args, "query")
                .ok_or_else(|| ToolFailure("search_knowledge requires a string query".into()))?,
            arg_str_or(args, "type_filter", ""),
            arg_i64_or(args, "limit", 10),
            arg_bool_or(args, "full_body", false),
        )
        .map_err(|e| ToolFailure(e.0)),
        "ask_compass" => {
            let conn = open_read_conn(ctx)?;
            km::ask_compass(
                &conn,
                &ctx.bundle(),
                arg_str(args, "query")
                    .ok_or_else(|| ToolFailure("ask_compass requires a string query".into()))?,
                arg_str_or(args, "file_path", ""),
            )
            .map_err(|e| ToolFailure(e.0))
        }
        "generate_flow" => {
            let conn = ctx.conn()?;
            km::generate_flow_tool(
                &ctx.bundle(),
                &conn,
                arg_str(args, "entry")
                    .ok_or_else(|| ToolFailure("generate_flow requires a string entry".into()))?,
                arg_bool_or(args, "as_workflow", false),
                arg_i64_or(args, "max_steps", km::DEFAULT_FLOW_STEP_LIMIT as i64) as usize,
                ctx.read_only,
            )
            .map_err(|e| ToolFailure(e.0))
        }
        "federated_search" => federations::federated_search_text(
            arg_str(args, "query")
                .ok_or_else(|| ToolFailure("federated_search requires a string query".into()))?,
            arg_i64_or(args, "limit", 20),
        )
        .map_err(ToolFailure),
        "wiki_generate" => {
            let conn = open_read_conn(ctx)?;
            wiki::wiki_generate_text(
                &conn,
                &ctx.bundle(),
                arg_str(args, "repo")
                    .ok_or_else(|| ToolFailure("wiki_generate requires a string repo".into()))?,
                arg_i64_or(args, "pages", 10),
                arg_bool_or(args, "refine_catalog", false),
                arg_bool_or(args, "diagrams", false),
                arg_bool_or(args, "force", false),
            )
            .map_err(|e| ToolFailure(e.0))
        }
        other => Err(ToolFailure(format!("Unknown tool: {other}"))),
    }
}
