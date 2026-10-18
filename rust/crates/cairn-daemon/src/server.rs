//! rmcp streamable-HTTP skeleton serving `search_symbols` only (P0-5 spike, inert).

use std::io;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;

use rmcp::model::{
    CallToolRequestParams, CallToolResponse, CallToolResult, ContentBlock, ListToolsResult,
    PaginatedRequestParams, ServerCapabilities, ServerConfig, Tool, ToolAnnotations,
};
use rmcp::service::RequestContext;
use rmcp::transport::streamable_http_server::{
    StreamableHttpServerConfig, StreamableHttpService, session::local::LocalSessionManager,
};
use rmcp::{ErrorData, RoleServer, ServerHandler};
use tokio_util::sync::CancellationToken;

const SEARCH_LIMIT: i64 = 100;

/// The single-tool MCP server backed by a read-only `.kg` store.
#[derive(Clone)]
pub struct SearchServer {
    store_path: PathBuf,
}

impl SearchServer {
    pub fn new(store_path: PathBuf) -> Self {
        Self { store_path }
    }

    async fn search_text(&self, pattern: &str) -> Result<String, ErrorData> {
        let store = self.store_path.clone();
        let pattern = pattern.to_string();
        tokio::task::spawn_blocking(move || {
            let conn = crate::store::open_read_only(&store)
                .map_err(|e| ErrorData::internal_error(format!("store open failed: {e}"), None))?;
            let rows = crate::store::search_symbols(&conn, &pattern, None, SEARCH_LIMIT)
                .map_err(|e| ErrorData::internal_error(format!("search failed: {e}"), None))?;
            Ok(crate::search::render_search_symbols(&pattern, &rows))
        })
        .await
        .map_err(|e| ErrorData::internal_error(format!("search task failed: {e}"), None))?
    }
}

fn tool_catalog() -> Vec<Tool> {
    let schema = serde_json::json!({
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Symbol name pattern with optional trailing * wildcard.",
            }
        },
        "required": ["pattern"]
    });
    let mut tool = Tool::new(
        "search_symbols",
        "Lexical symbol search (FTS5/BM25, wildcards, optional kind filter).",
        schema.as_object().expect("object literal").clone(),
    );
    let mut annotations = ToolAnnotations::default();
    annotations.read_only_hint = Some(true);
    annotations.destructive_hint = Some(false);
    annotations.idempotent_hint = Some(true);
    tool.annotations = Some(annotations);
    vec![tool]
}

impl ServerHandler for SearchServer {
    fn get_info(&self) -> ServerConfig {
        ServerConfig::new(ServerCapabilities::builder().enable_tools().build())
    }

    // non_exhaustive result: Default + field mutation is the only construction path.
    #[allow(clippy::field_reassign_with_default)]
    async fn list_tools(
        &self,
        _request: Option<PaginatedRequestParams>,
        _context: RequestContext<RoleServer>,
    ) -> Result<ListToolsResult, ErrorData> {
        let mut result = ListToolsResult::default();
        result.tools = tool_catalog();
        Ok(result)
    }

    async fn call_tool(
        &self,
        request: CallToolRequestParams,
        _context: RequestContext<RoleServer>,
    ) -> Result<CallToolResponse, ErrorData> {
        if request.name.as_ref() != "search_symbols" {
            return Err(ErrorData::invalid_params(
                format!("Unknown tool: {}", request.name),
                None,
            ));
        }
        let pattern = request
            .arguments
            .as_ref()
            .and_then(|args| args.get("pattern"))
            .and_then(|v| v.as_str())
            .ok_or_else(|| {
                ErrorData::invalid_params("search_symbols requires a string pattern argument", None)
            })?;
        let text = self.search_text(pattern).await?;
        let mut result = CallToolResult::success(vec![ContentBlock::text(text.clone())]);
        result.structured_content = Some(serde_json::json!({ "result": text }));
        Ok(result.into())
    }
}

/// Handle stopping a running spike server.
pub struct ServeHandle {
    cancellation_token: CancellationToken,
}

impl ServeHandle {
    pub fn stop(&self) {
        self.cancellation_token.cancel();
    }
}

/// Serve the spike on 127.0.0.1 at an OS-assigned port; returns the bound address.
// non_exhaustive config: Default + field mutation is the only construction path.
#[allow(clippy::field_reassign_with_default)]
pub async fn serve(store_path: PathBuf) -> io::Result<(SocketAddr, ServeHandle)> {
    let mut config = StreamableHttpServerConfig::default();
    config.json_response = true;
    let handle = ServeHandle {
        cancellation_token: config.cancellation_token.clone(),
    };
    let service: StreamableHttpService<SearchServer, LocalSessionManager> =
        StreamableHttpService::new(
            move || Ok(SearchServer::new(store_path.clone())),
            Arc::new(LocalSessionManager::default()),
            config,
        );
    let router = axum::Router::new().nest_service("/mcp", service);
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await?;
    let addr = listener.local_addr()?;
    let shutdown = handle.cancellation_token.clone();
    tokio::spawn(async move {
        let _ = axum::serve(listener, router)
            .with_graceful_shutdown(async move { shutdown.cancelled_owned().await })
            .await;
    });
    Ok((addr, handle))
}
