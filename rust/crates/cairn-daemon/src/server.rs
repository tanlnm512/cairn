//! Streamable-HTTP MCP server: the cairn tool surface, bearer gate, healthz.

use std::io;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;

use axum::extract::{Request, State};
use axum::http::{HeaderValue, StatusCode, header};
use axum::middleware::Next;
use axum::response::Response;
use rmcp::model::{
    CallToolRequestParams, CallToolResponse, CallToolResult, ContentBlock, ListToolsResult,
    PaginatedRequestParams, ServerCapabilities, ServerConfig,
};
use rmcp::service::RequestContext;
use rmcp::transport::streamable_http_server::{
    StreamableHttpServerConfig, StreamableHttpService, session::local::LocalSessionManager,
};
use rmcp::{ErrorData, RoleServer, ServerHandler};
use serde_json::{Value, json};
use tokio_util::sync::CancellationToken;

use crate::{auth, healthz, tools};

pub const HEALTHZ_PATH: &str = "/healthz";
pub const MCP_PATH: &str = "/mcp";

/// Store wiring + policy for one daemon instance.
#[derive(Clone)]
pub struct DaemonConfig {
    pub db_path: PathBuf,
    pub workspace: PathBuf,
    pub knowledge_path: PathBuf,
    pub read_only: bool,
    /// Pre-resolved bearer key (explicit > env); None serves keyless loopback.
    pub api_key: Option<String>,
}

impl DaemonConfig {
    /// Default wiring for a store path: workspace is cwd, knowledge beside the db.
    pub fn for_store(db_path: PathBuf) -> Self {
        let knowledge = db_path
            .parent()
            .map(|p| p.join(".knowledge"))
            .unwrap_or_else(|| PathBuf::from(".knowledge"));
        let workspace = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
        Self {
            db_path,
            workspace,
            knowledge_path: knowledge,
            read_only: false,
            api_key: auth::resolve_api_key(None),
        }
    }
}

/// The MCP server: every registered cairn-query tool over one store.
#[derive(Clone)]
pub struct CairnServer {
    ctx: tools::ToolCtx,
}

impl ServerHandler for CairnServer {
    fn get_info(&self) -> ServerConfig {
        let mut info = ServerConfig::new(ServerCapabilities::builder().enable_tools().build());
        info.server_info.name = "cairn".to_string();
        info.server_info.version = env!("CARGO_PKG_VERSION").to_string();
        info
    }

    // non_exhaustive result: Default + field mutation is the only construction path.
    #[allow(clippy::field_reassign_with_default)]
    async fn list_tools(
        &self,
        _request: Option<PaginatedRequestParams>,
        _context: RequestContext<RoleServer>,
    ) -> Result<ListToolsResult, ErrorData> {
        let mut result = ListToolsResult::default();
        result.tools = tools::catalog();
        Ok(result)
    }

    async fn call_tool(
        &self,
        request: CallToolRequestParams,
        _context: RequestContext<RoleServer>,
    ) -> Result<CallToolResponse, ErrorData> {
        let name = request.name.to_string();
        let tool_name = name.clone();
        let args = request
            .arguments
            .map(Value::Object)
            .unwrap_or_else(|| json!({}));
        let ctx = self.ctx.clone();
        let outcome = tokio::task::spawn_blocking(move || tools::dispatch(&ctx, &name, &args))
            .await
            .map_err(|e| ErrorData::internal_error(format!("tool task failed: {e}"), None))?;
        let text = match outcome {
            Ok(text) => truncate_result(&tool_name, text),
            Err(failure) => {
                let mut result = CallToolResult::error(vec![ContentBlock::text(failure.0.clone())]);
                result.structured_content = Some(json!({ "result": failure.0 }));
                return Ok(result.into());
            }
        };
        let mut result = CallToolResult::success(vec![ContentBlock::text(text.clone())]);
        result.structured_content = Some(json!({ "result": text }));
        Ok(result.into())
    }
}

/// Hard cap on one tool result, cut at the last newline with a fixed notice
/// (the `@instrument` decorator's central truncation contract).
fn truncate_result(name: &str, result: String) -> String {
    let cap: usize = std::env::var("CAIRN_MAX_RESULT_CHARS")
        .ok()
        .and_then(|raw| raw.trim().parse().ok())
        .unwrap_or(60_000);
    let char_count = result.chars().count();
    if char_count <= cap {
        return result;
    }
    let mut head: String = result.chars().take(cap).collect();
    if let Some(cut) = head.rfind('\n')
        && cut > 0
    {
        head.truncate(cut);
    }
    format!(
        "{head}\n\n[TRUNCATED: '{name}' returned {char_count} chars, over the \
         {cap}-char cap. Narrow the query -- e.g. pass a smaller `limit`, use \
         fuzzy=False, a more specific pattern, or a lower `depth` -- rather \
         than relying on this truncated output.]"
    )
}

fn unauthorized() -> Response {
    let mut response = Response::new(axum::body::Body::from("{\"error\":\"unauthorized\"}"));
    *response.status_mut() = StatusCode::UNAUTHORIZED;
    let headers = response.headers_mut();
    headers.insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("application/json"),
    );
    headers.insert(header::WWW_AUTHENTICATE, HeaderValue::from_static("Bearer"));
    response
}

/// Bearer gate: 401 on bad credentials; /healthz passes through (the middleware contract).
async fn bearer_gate(State(expected): State<String>, req: Request, next: Next) -> Response {
    if req.uri().path() == HEALTHZ_PATH {
        return next.run(req).await;
    }
    let provided = req
        .headers()
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok());
    if auth::bearer_key_ok(provided, &expected) {
        next.run(req).await
    } else {
        unauthorized()
    }
}

/// Handle stopping a running server.
#[derive(Debug)]
pub struct ServeHandle {
    cancellation_token: CancellationToken,
}

impl ServeHandle {
    pub fn stop(&self) {
        self.cancellation_token.cancel();
    }
}

/// Serve on `bind`; loopback binds may run keyless, non-loopback binds require a key.
pub async fn serve_config(
    config: DaemonConfig,
    bind: SocketAddr,
) -> io::Result<(SocketAddr, ServeHandle)> {
    if config.api_key.is_none() && !bind.ip().is_loopback() {
        return Err(io::Error::other(format!(
            "cairn: refusing to serve HTTP on non-loopback host '{bind}' without an \
             API key. Set CAIRN_MCP_API_KEY or pass --api-key."
        )));
    }
    let mut http_config = StreamableHttpServerConfig::default();
    http_config.json_response = true;
    let handle = ServeHandle {
        cancellation_token: http_config.cancellation_token.clone(),
    };
    let ctx = tools::ToolCtx::new(
        &config.db_path,
        &config.workspace,
        &config.knowledge_path,
        config.read_only,
    );
    let healthz_state = Arc::new(healthz::HealthzState::new(
        config.db_path.to_string_lossy(),
        config.read_only,
    ));
    let service: StreamableHttpService<CairnServer, LocalSessionManager> =
        StreamableHttpService::new(
            move || Ok(CairnServer { ctx: ctx.clone() }),
            Arc::new(LocalSessionManager::default()),
            http_config,
        );
    let router = axum::Router::new()
        .route(HEALTHZ_PATH, axum::routing::get(healthz::respond))
        .with_state(healthz_state)
        .nest_service(MCP_PATH, service);
    let router = match config.api_key.clone() {
        Some(key) => router.layer(axum::middleware::from_fn_with_state(key, bearer_gate)),
        None => router,
    };
    serve_on(bind, router, handle).await
}

async fn serve_on(
    bind: SocketAddr,
    router: axum::Router,
    handle: ServeHandle,
) -> io::Result<(SocketAddr, ServeHandle)> {
    let listener = tokio::net::TcpListener::bind(bind).await?;
    let addr = listener.local_addr()?;
    let shutdown = handle.cancellation_token.clone();
    tokio::spawn(async move {
        let _ = axum::serve(
            listener,
            router.into_make_service_with_connect_info::<SocketAddr>(),
        )
        .with_graceful_shutdown(async move { shutdown.cancelled_owned().await })
        .await;
    });
    Ok((addr, handle))
}

/// Serve the spike on 127.0.0.1 at an OS-assigned port; returns the bound address.
pub async fn serve(store_path: PathBuf) -> io::Result<(SocketAddr, ServeHandle)> {
    serve_config(
        DaemonConfig::for_store(store_path),
        "127.0.0.1:0".parse().expect("bind addr"),
    )
    .await
}
