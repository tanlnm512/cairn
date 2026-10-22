//! The read-only dashboard: axum router over the embedded templates
//! (app.py + routes/* port; same route surface, store seam, and htmx
//! fragment splits as the Starlette app).

pub mod assets;
pub mod data;
pub mod env;
pub mod kb;
pub mod markdown;
pub mod settings;
pub mod shell;
pub mod workspaces;

use std::collections::HashMap;

use axum::Router;
use axum::extract::{Form, Path as AxumPath, State};
use axum::http::{HeaderMap, StatusCode, Uri};
use axum::response::{Html, IntoResponse, Json, Response};
use axum::routing::{get, post};
use minijinja::Environment;
use rusqlite::Connection;
use serde_json::{Value, json};

use crate::store_paths;

// --- request params ---

/// Decoded query params (first and multi-value access).
pub struct Params {
    pairs: Vec<(String, String)>,
}

impl Params {
    pub fn parse(query: &str) -> Self {
        let pairs = query
            .split('&')
            .filter(|part| !part.is_empty())
            .map(|part| match part.split_once('=') {
                Some((key, value)) => (decode_component(key), decode_component(value)),
                None => (decode_component(part), String::new()),
            })
            .collect();
        Self { pairs }
    }

    pub fn get(&self, key: &str) -> Option<&str> {
        self.pairs
            .iter()
            .find(|(k, _)| k == key)
            .map(|(_, v)| v.as_str())
    }

    pub fn all(&self, key: &str) -> Vec<&str> {
        self.pairs
            .iter()
            .filter(|(k, _)| k == key)
            .map(|(_, v)| v.as_str())
            .collect()
    }

    fn str_or(&self, key: &str) -> String {
        self.get(key).unwrap_or("").to_string()
    }
}

/// `urllib.parse.unquote_plus` for one component.
fn decode_component(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'+' => {
                out.push(b' ');
                index += 1;
            }
            b'%' if index + 2 < bytes.len() + 1 && index + 2 < bytes.len() + 1 => {
                let hex = bytes.get(index + 1..index + 3);
                match hex.and_then(|h| u8::from_str_radix(std::str::from_utf8(h).ok()?, 16).ok()) {
                    Some(byte) => {
                        out.push(byte);
                        index += 3;
                    }
                    None => {
                        out.push(b'%');
                        index += 1;
                    }
                }
            }
            byte => {
                out.push(byte);
                index += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// True when this htmx request wants a fragment; history restores veto it.
fn is_hx_request(headers: &HeaderMap) -> bool {
    let restore = headers
        .get("HX-History-Restore-Request")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .trim()
        .eq_ignore_ascii_case("true");
    if restore {
        return false;
    }
    headers
        .get("HX-Request")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .trim()
        .eq_ignore_ascii_case("true")
}

// --- app state ---

#[derive(Clone)]
pub struct AppState {
    db_path: Option<String>,
    knowledge_dir: Option<String>,
    environment: Environment<'static>,
}

impl AppState {
    pub fn new(db_path: Option<String>, knowledge_dir: Option<String>) -> Self {
        Self {
            db_path,
            knowledge_dir,
            environment: env::environment(),
        }
    }
}

/// Store enumeration over the effective CAIRN_HOME.
fn enumerate_stores() -> Vec<Value> {
    match store_paths::cairn_home() {
        Some(home) => workspaces::enumerate_stores(&home),
        None => Vec::new(),
    }
}

/// The default knowledge dir (the central store's bundle).
fn default_knowledge_dir() -> String {
    store_paths::resolve_store(None)
        .knowledge
        .to_string_lossy()
        .into_owned()
}

struct Selection {
    db: Option<String>,
    knowledge: String,
    store_key: String,
}

enum SelectionError {
    MissingDatabase(#[allow(dead_code)] String),
}

/// Resolve request/form input to a validated registry store selection.
fn resolve_selection(
    state: &AppState,
    params: Option<&Params>,
    form_store: Option<&str>,
) -> Result<Selection, SelectionError> {
    let mut store_key = params
        .and_then(|p| p.get("store"))
        .unwrap_or("")
        .trim()
        .to_string();
    if store_key.is_empty() {
        store_key = form_store.unwrap_or("").trim().to_string();
    }
    if store_key.is_empty() {
        return Ok(Selection {
            db: state.db_path.clone(),
            knowledge: state
                .knowledge_dir
                .clone()
                .unwrap_or_else(default_knowledge_dir),
            store_key: String::new(),
        });
    }
    let home = store_paths::current_cairn_home();
    let stores = enumerate_stores();
    let state_value = stores
        .iter()
        .find(|row| row["key"].as_str() == Some(store_key.as_str()))
        .and_then(|row| row["state"].as_str())
        .unwrap_or("");
    if state_value != "populated" {
        return Err(SelectionError::MissingDatabase(store_key));
    }
    Ok(Selection {
        db: Some(
            home.join(&store_key)
                .join(".kg")
                .to_string_lossy()
                .into_owned(),
        ),
        knowledge: home
            .join(&store_key)
            .join(".knowledge")
            .to_string_lossy()
            .into_owned(),
        store_key,
    })
}

/// Open the selected store read-only or report the missing db path.
fn open_selected(selection: &Selection) -> Result<Connection, String> {
    data::open_store(selection.db.as_deref())
}

impl AppState {
    /// Render a template with shell context; store enumeration stays stat-only.
    fn render(
        &self,
        path: &str,
        name: &str,
        context: Value,
        store_key: &str,
        status: StatusCode,
    ) -> Response {
        let mut context = context;
        if context.get("embed_banner").is_none()
            && let Value::Object(map) = &mut context
        {
            map.insert("embed_banner".into(), Value::from(settings::embed_banner()));
        }
        if context.get("shell").is_none()
            && let Value::Object(map) = &mut context
        {
            map.insert(
                "shell".into(),
                shell::shell_context(
                    &enumerate_stores(),
                    store_key,
                    path,
                    self.db_path.as_deref(),
                ),
            );
        }
        if let Value::Object(map) = &mut context {
            map.insert("request".into(), json!({"url": {"path": path}}));
        }
        let rendered = self
            .environment
            .get_template(name)
            .and_then(|template| template.render(minijinja::value::Serde(&context)));
        match rendered {
            Ok(body) => (status, Html(body)).into_response(),
            Err(err) => (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("cairn dashboard: template error in {name}: {err}"),
            )
                .into_response(),
        }
    }
}

/// The missing-database page (the Starlette exception handler's body).
fn missing_db_response(db_path: &str) -> Response {
    Html(format!(
        "<html><head><title>cairn dashboard</title></head><body>\n<h1>cairn dashboard</h1>\n\
         <p>No graph database found at <code>{}</code>.</p>\n\
         <p>Run <code>cairn build</code> to index this workspace, then refresh.</p>\n\
         <p><a href=\"/workspaces\">Open the workspaces overview</a> to pick an \
         available store.</p>\n</body></html>\n",
        markdown::html_escape(db_path, false)
    ))
    .into_response()
}

fn selection_missing_response(store_key: &str) -> Response {
    missing_db_response(store_key)
}

fn not_found_page(title: &str, body: &str, back_href: &str, back_label: &str) -> Response {
    (
        StatusCode::NOT_FOUND,
        Html(format!(
            "<html><head><title>cairn dashboard</title></head><body>\n<h1>{title}</h1>\n\
             <p>{body}</p>\n<p><a href=\"{back_href}\">{back_label}</a></p>\n</body></html>\n"
        )),
    )
        .into_response()
}

// --- route handlers: core ---

async fn landing(State(state): State<AppState>, uri: Uri, headers: HeaderMap) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let _ = &headers;
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let db_label = state
        .db_path
        .clone()
        .unwrap_or_else(|| "central store".into());
    state.render(
        uri.path(),
        "index.html",
        json!({"db_path": db_label, "store_key": selection.store_key}),
        &selection.store_key,
        StatusCode::OK,
    )
}

async fn favicon(State(_state): State<AppState>) -> Response {
    match assets::static_file("/favicon.svg") {
        Some(body) => (
            [(axum::http::header::CONTENT_TYPE, "image/svg+xml")],
            body.to_vec(),
        )
            .into_response(),
        None => (StatusCode::NOT_FOUND, "Not Found").into_response(),
    }
}

async fn static_asset(uri: Uri) -> Response {
    let path = uri.path().trim_start_matches("/static/");
    match assets::static_file(&format!("/{path}")) {
        Some(body) => {
            let content_type = assets::static_content_type(path);
            (
                [
                    (axum::http::header::CONTENT_TYPE, content_type),
                    (axum::http::header::CACHE_CONTROL, "no-cache"),
                ],
                body.to_vec(),
            )
                .into_response()
        }
        None => (StatusCode::NOT_FOUND, "Not Found").into_response(),
    }
}

async fn workspaces_overview(State(state): State<AppState>, uri: Uri) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let home = store_paths::current_cairn_home();
    let rows = workspaces::probe_stores(
        &home,
        &workspaces::enumerate_stores(&home),
        workspaces::PROBE_MAX_OPENS,
    );
    state.render(
        uri.path(),
        "workspaces.html",
        json!({
            "stores": rows,
            "launch_db": state.db_path.clone().unwrap_or_default(),
            "store_key": selection.store_key,
        }),
        &selection.store_key,
        StatusCode::OK,
    )
}

async fn projects(State(state): State<AppState>, uri: Uri) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let Ok(conn) = open_selected(&selection) else {
        return selection_missing_response(selection.db.as_deref().unwrap_or(""));
    };
    let rows = data::list_projects(&conn);
    drop(conn);
    state.render(
        uri.path(),
        "projects.html",
        json!({"projects": rows, "store_key": selection.store_key}),
        &selection.store_key,
        StatusCode::OK,
    )
}

async fn health(State(state): State<AppState>, uri: Uri) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let Ok(conn) = open_selected(&selection) else {
        return selection_missing_response(selection.db.as_deref().unwrap_or(""));
    };
    let db_path = selection
        .db
        .clone()
        .unwrap_or_else(|| "central store".into());
    let health = data::get_health(&conn, &db_path);
    drop(conn);
    state.render(
        uri.path(),
        "health.html",
        json!({"health": health, "db_path": db_path, "store_key": selection.store_key}),
        &selection.store_key,
        StatusCode::OK,
    )
}

// --- route handlers: graph ---

fn parse_depth_param(raw: &str) -> Option<i64> {
    if !raw.is_empty() && raw.bytes().all(|b| b.is_ascii_digit()) {
        return raw.parse::<i64>().ok().filter(|d| *d != 0).or(Some(0));
    }
    None
}

async fn graph(State(state): State<AppState>, uri: Uri) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let scope = params.get("scope").unwrap_or("module");
    let scope = if data::GRAPH_SCOPES.contains(&scope) {
        scope
    } else {
        "module"
    };
    let focus = params.str_or("focus").trim().to_string();
    let repo = params.str_or("repo").trim().to_string();
    let depth_raw = params.str_or("depth").trim().to_string();
    let depth = parse_depth_param(&depth_raw);
    let include_tests = matches!(params.get("tests"), Some("1" | "on" | "true"));
    let mut layout = params.str_or("layout");
    if layout.is_empty() || (layout != "force" && layout != "hier") {
        layout = "force".into();
    }
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let Ok(conn) = open_selected(&selection) else {
        return selection_missing_response(selection.db.as_deref().unwrap_or(""));
    };
    let graph_data = viz_get_graph(&conn, scope, &focus, &repo, depth, include_tests);
    drop(conn);
    state.render(
        uri.path(),
        "graph.html",
        json!({
            "graph": graph_data,
            "scopes": data::GRAPH_SCOPES,
            "scope": scope,
            "focus": focus,
            "repo": repo,
            "depth": if depth.is_some() { Value::from(depth_raw) } else { Value::from("") },
            "layout": layout,
            "include_tests": include_tests,
            "store_key": selection.store_key,
        }),
        &selection.store_key,
        StatusCode::OK,
    )
}

/// The viz scope dispatch (data.get_graph port).
fn viz_get_graph(
    conn: &Connection,
    scope: &str,
    focus: &str,
    repo: &str,
    depth: Option<i64>,
    include_tests: bool,
) -> Value {
    use cairn_query::graph as viz;
    match scope {
        "symbol" => {
            if focus.trim().is_empty() {
                viz::get_symbol_overview(conn, include_tests, "symbol").unwrap_or(Value::Null)
            } else {
                viz::get_symbol_graph(conn, focus).unwrap_or(Value::Null)
            }
        }
        "module" => viz::get_module_graph(conn, focus, include_tests).unwrap_or(Value::Null),
        "impact" => {
            if focus.trim().is_empty() {
                viz::get_symbol_overview(conn, include_tests, "impact").unwrap_or(Value::Null)
            } else {
                let depth = depth.unwrap_or(3);
                viz::get_impact_graph(conn, focus, depth).unwrap_or(Value::Null)
            }
        }
        "deps" => viz::get_deps_graph(conn, &[]).unwrap_or(Value::Null),
        "repo" => viz::get_repo_graph(conn, repo).unwrap_or(Value::Null),
        _ => Value::Null,
    }
}

async fn graph_candidates(State(state): State<AppState>, uri: Uri) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let Ok(conn) = open_selected(&selection) else {
        return selection_missing_response(selection.db.as_deref().unwrap_or(""));
    };
    let result = data::symbol_candidates(&conn, params.get("name").unwrap_or("").trim());
    drop(conn);
    Json(result).into_response()
}

async fn graph_suggest(State(state): State<AppState>, uri: Uri) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let Ok(conn) = open_selected(&selection) else {
        return selection_missing_response(selection.db.as_deref().unwrap_or(""));
    };
    let result = data::symbol_suggest(&conn, params.get("name").unwrap_or("").trim());
    drop(conn);
    Json(result).into_response()
}

async fn graph_neighbors(State(state): State<AppState>, uri: Uri) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let Ok(conn) = open_selected(&selection) else {
        return selection_missing_response(selection.db.as_deref().unwrap_or(""));
    };
    let names: Vec<String> = params
        .all("name")
        .into_iter()
        .map(str::trim)
        .filter(|name| !name.is_empty())
        .map(String::from)
        .collect();
    let names: Vec<String> = {
        let mut seen = Vec::new();
        for name in names {
            if !seen.contains(&name) {
                seen.push(name);
            }
        }
        seen
    };
    let result = cairn_query::graph::get_symbol_neighbors(&conn, &names).unwrap_or(Value::Null);
    drop(conn);
    Json(result).into_response()
}

async fn graph_inspect(State(state): State<AppState>, uri: Uri, headers: HeaderMap) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let name = params.get("name").unwrap_or("").to_string();
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let Ok(conn) = open_selected(&selection) else {
        return selection_missing_response(selection.db.as_deref().unwrap_or(""));
    };
    let result = data::inspect_symbol(&conn, &name);
    drop(conn);
    if is_hx_request(&headers) {
        let mut context = result;
        if let Value::Object(map) = &mut context {
            map.insert("store_key".into(), Value::from(selection.store_key.clone()));
        }
        return state.render(
            uri.path(),
            "graph_inspect_panel.html",
            context,
            &selection.store_key,
            StatusCode::OK,
        );
    }
    Json(result).into_response()
}

async fn palette_results(State(state): State<AppState>, uri: Uri) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let query = params.str_or("q").trim().to_string();
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let palette = shell::shell_context(
        &enumerate_stores(),
        &selection.store_key,
        "/",
        state.db_path.as_deref(),
    )["palette"]
        .clone();
    let lowered = query.to_lowercase();
    let mut rows: Vec<Value> = Vec::new();
    for view in palette["views"]
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or_default()
    {
        let label = view["label"].as_str().unwrap_or("");
        if lowered.is_empty() || label.to_lowercase().contains(&lowered) {
            rows.push(json!({
                "label": label,
                "hint": "view",
                "href": view["href"],
            }));
        }
    }
    for workspace in palette["workspaces"]
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or_default()
    {
        let label = workspace["label"].as_str().unwrap_or("");
        if lowered.is_empty() || label.to_lowercase().contains(&lowered) {
            rows.push(json!({
                "label": label,
                "hint": "workspace",
                "store_key": workspace["key"],
            }));
        }
    }
    if query.chars().count() >= 2 {
        let Ok(conn) = open_selected(&selection) else {
            return selection_missing_response(selection.db.as_deref().unwrap_or(""));
        };
        let suggested = data::symbol_suggest(&conn, &query);
        drop(conn);
        for candidate in suggested["matches"]
            .as_array()
            .map(Vec::as_slice)
            .unwrap_or_default()
            .iter()
            .take(8)
        {
            let name = candidate["name"].as_str().unwrap_or("");
            let mut href = format!("/graph?scope=symbol&focus={}", shell::urlencode(name));
            if !selection.store_key.is_empty() {
                href.push_str("&store=");
                href.push_str(&shell::urlencode(&selection.store_key));
            }
            let mut hint = candidate["kind"].as_str().unwrap_or("").to_string();
            if let Some(file) = candidate["file"].as_str() {
                hint.push_str(" — ");
                hint.push_str(file);
            }
            rows.push(json!({"label": name, "hint": hint, "href": href}));
        }
        if suggested["truncated"] == true {
            rows.push(json!({"label": "more matches…", "hint": "keep typing to narrow"}));
        }
    }
    state
        .environment
        .get_template("palette_results.html")
        .and_then(|template| template.render(minijinja::value::Serde(&json!({"rows": rows}))))
        .map(|body| Html(body).into_response())
        .unwrap_or_else(|err| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("cairn dashboard: template error in palette_results.html: {err}"),
            )
                .into_response()
        })
}

// --- route handlers: communities ---

async fn communities(State(state): State<AppState>, uri: Uri, headers: HeaderMap) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let raw = params.str_or("community").trim().to_string();
    let community_id = if !raw.is_empty() && raw.bytes().all(|b| b.is_ascii_digit()) {
        raw.parse::<i64>()
            .ok()
            .filter(|id| *id >= 0 && *id < i64::MAX)
    } else {
        None
    };
    let fragment = is_hx_request(&headers);
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let Ok(conn) = open_selected(&selection) else {
        return selection_missing_response(selection.db.as_deref().unwrap_or(""));
    };
    let listing = if fragment {
        Vec::new()
    } else {
        data::get_communities(&conn)
    };
    let selected = community_id.and_then(|id| data::get_community_members(&conn, id));
    drop(conn);
    let mut context = json!({
        "communities": listing,
        "selected": selected,
        "community_id": community_id,
        "store_key": selection.store_key,
    });
    if fragment {
        context["fragment"] = Value::from(true);
        return state.render(
            uri.path(),
            "communities.html",
            context,
            &selection.store_key,
            StatusCode::OK,
        );
    }
    state.render(
        uri.path(),
        "communities.html",
        context,
        &selection.store_key,
        StatusCode::OK,
    )
}

// --- route handlers: history / tokens / chains + exports ---

/// RFC 4180 text with the Python csv module's minimal quoting and CRLF rows.
fn csv_text(rows: &[Value]) -> String {
    let mut out = String::new();
    let Some(first) = rows.first() else {
        return out;
    };
    let headers: Vec<String> = first
        .as_object()
        .map(|map| map.keys().cloned().collect())
        .unwrap_or_default();
    let quote = |field: &str| -> String {
        if field.contains([',', '"', '\n', '\r']) {
            format!("\"{}\"", field.replace('"', "\"\""))
        } else {
            field.to_string()
        }
    };
    let header_line: Vec<String> = headers.iter().map(|h| quote(h)).collect();
    out.push_str(&header_line.join(","));
    out.push_str("\r\n");
    for row in rows {
        let fields: Vec<String> = headers
            .iter()
            .map(|key| match row.get(key) {
                None | Some(Value::Null) => String::new(),
                Some(Value::String(s)) => quote(s),
                Some(other) => other.to_string(),
            })
            .collect();
        out.push_str(&fields.join(","));
        out.push_str("\r\n");
    }
    out
}

/// Attachment filename: view name plus the active filter hints.
fn export_filename(view: &str, ext: &str, hints: &[(String, Option<String>)]) -> String {
    let mut parts = vec![view.to_string()];
    for (key, value) in hints {
        if let Some(value) = value {
            parts.push(format!("{key}-{value}"));
        }
    }
    let joined = parts.join("-");
    let mut safe = String::new();
    for ch in joined.chars() {
        if ch.is_ascii_alphanumeric() || matches!(ch, '.' | '_' | '-') {
            safe.push(ch);
        } else {
            safe.push('_');
        }
    }
    format!("{}.{ext}", &safe[..safe.len().min(120)])
}

type ExportRows = (Vec<Value>, Vec<(String, Option<String>)>);

fn history_export_rows(state: &AppState, params: &Params) -> Result<ExportRows, Box<Response>> {
    let tool = params.str_or("tool").trim().to_string();
    let session = params.str_or("session").trim().to_string();
    let source = params.str_or("source").trim().to_string();
    let (window, since) = data::resolve_window(params.get("window"), now_secs());
    let Ok(selection) = resolve_selection(state, Some(params), None) else {
        return Err(Box::new(selection_missing_response(
            params.str_or("store").as_str(),
        )));
    };
    let Ok(conn) = open_selected(&selection) else {
        return Err(Box::new(selection_missing_response(
            selection.db.as_deref().unwrap_or(""),
        )));
    };
    let result = data::list_history(
        &conn,
        data::HistoryQuery {
            tool_name: non_empty(&tool).as_deref(),
            session_id: non_empty(&session).as_deref(),
            source: non_empty(&source).as_deref(),
            since,
            before: None,
            after: None,
            limit: data::EXPORT_ROW_LIMIT,
        },
    );
    drop(conn);
    Ok((
        result["rows"].as_array().cloned().unwrap_or_default(),
        vec![
            ("tool".into(), non_empty(&tool)),
            ("session".into(), non_empty(&session)),
            ("source".into(), non_empty(&source)),
            (
                "window".into(),
                if window == "all" { None } else { Some(window) },
            ),
        ],
    ))
}

fn tokens_export_rows(state: &AppState, params: &Params) -> Result<ExportRows, Box<Response>> {
    let (window, since) = data::resolve_window(params.get("window"), now_secs());
    let Ok(selection) = resolve_selection(state, Some(params), None) else {
        return Err(Box::new(selection_missing_response(
            params.str_or("store").as_str(),
        )));
    };
    let Ok(conn) = open_selected(&selection) else {
        return Err(Box::new(selection_missing_response(
            selection.db.as_deref().unwrap_or(""),
        )));
    };
    let rows = data::get_tool_tokens(&conn, since)["entries"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    drop(conn);
    Ok((
        rows,
        vec![(
            "window".into(),
            if window == "all" { None } else { Some(window) },
        )],
    ))
}

fn non_empty(value: &str) -> Option<String> {
    if value.is_empty() {
        None
    } else {
        Some(value.to_string())
    }
}

pub(crate) fn now_secs() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn csv_response(rows: Vec<Value>, filename: String) -> Response {
    let mut response = (
        [(axum::http::header::CONTENT_TYPE, "text/csv")],
        csv_text(&rows),
    )
        .into_response();
    response.headers_mut().insert(
        axum::http::header::CONTENT_DISPOSITION,
        format!("attachment; filename=\"{filename}\"")
            .parse()
            .unwrap(),
    );
    response
}

fn json_attachment(rows: Vec<Value>, filename: String) -> Response {
    let mut response = Json(rows).into_response();
    response.headers_mut().insert(
        axum::http::header::CONTENT_DISPOSITION,
        format!("attachment; filename=\"{filename}\"")
            .parse()
            .unwrap(),
    );
    response
}

async fn history(State(state): State<AppState>, uri: Uri, headers: HeaderMap) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let tool = params.str_or("tool").trim().to_string();
    let session = params.str_or("session").trim().to_string();
    let source = params.str_or("source").trim().to_string();
    let before = params.str_or("before").trim().to_string();
    let after = params.str_or("after").trim().to_string();
    let (window, since) = data::resolve_window(params.get("window"), now_secs());
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let Ok(conn) = open_selected(&selection) else {
        return selection_missing_response(selection.db.as_deref().unwrap_or(""));
    };
    let result = data::list_history(
        &conn,
        data::HistoryQuery {
            tool_name: non_empty(&tool).as_deref(),
            session_id: non_empty(&session).as_deref(),
            source: non_empty(&source).as_deref(),
            since,
            before: non_empty(&before).as_deref(),
            after: non_empty(&after).as_deref(),
            limit: data::HISTORY_PAGE_SIZE,
        },
    );
    drop(conn);
    let context = json!({
        "calls": result["rows"],
        "tool": tool,
        "session": session,
        "source": source,
        "before": before,
        "after": after,
        "window": window,
        "next_cursor": result["next"],
        "prev_cursor": result["prev"],
        "store_key": selection.store_key,
    });
    if is_hx_request(&headers) {
        // Render only the polled region; filters and cursor ride its request URL.
        return state.render(
            uri.path(),
            "history_region.html",
            context,
            &selection.store_key,
            StatusCode::OK,
        );
    }
    state.render(
        uri.path(),
        "history.html",
        context,
        &selection.store_key,
        StatusCode::OK,
    )
}

async fn history_csv(State(state): State<AppState>, uri: Uri) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    match history_export_rows(&state, &params) {
        Ok((rows, hints)) => csv_response(rows, export_filename("history", "csv", &hints)),
        Err(response) => *response,
    }
}

async fn history_json(State(state): State<AppState>, uri: Uri) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    match history_export_rows(&state, &params) {
        Ok((rows, hints)) => json_attachment(rows, export_filename("history", "json", &hints)),
        Err(response) => *response,
    }
}

async fn tokens(State(state): State<AppState>, uri: Uri, headers: HeaderMap) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let (window, since) = data::resolve_window(params.get("window"), now_secs());
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let Ok(conn) = open_selected(&selection) else {
        return selection_missing_response(selection.db.as_deref().unwrap_or(""));
    };
    let tools = data::get_tool_tokens(&conn, since);
    drop(conn);
    let context = json!({
        "tools": tools["entries"],
        "tools_meta": {
            "token_mode": tools["token_mode"],
            "calibrated": tools["calibrated"],
            "chars_per_token": tools["chars_per_token"],
        },
        "window": window,
        "store_key": selection.store_key,
    });
    if is_hx_request(&headers) {
        return state.render(
            uri.path(),
            "tokens_region.html",
            context,
            &selection.store_key,
            StatusCode::OK,
        );
    }
    state.render(
        uri.path(),
        "tokens.html",
        context,
        &selection.store_key,
        StatusCode::OK,
    )
}

async fn tokens_csv(State(state): State<AppState>, uri: Uri) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    match tokens_export_rows(&state, &params) {
        Ok((rows, hints)) => csv_response(rows, export_filename("tokens", "csv", &hints)),
        Err(response) => *response,
    }
}

async fn tokens_json(State(state): State<AppState>, uri: Uri) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    match tokens_export_rows(&state, &params) {
        Ok((rows, hints)) => json_attachment(rows, export_filename("tokens", "json", &hints)),
        Err(response) => *response,
    }
}

async fn chains(State(state): State<AppState>, uri: Uri, headers: HeaderMap) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let (window, since) = data::resolve_window(params.get("window"), now_secs());
    let expand = params.str_or("expand").trim().to_string();
    let session = params.str_or("session").trim().to_string();
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let Ok(conn) = open_selected(&selection) else {
        return selection_missing_response(selection.db.as_deref().unwrap_or(""));
    };
    let result = data::get_session_chains(
        &conn,
        since,
        non_empty(&session).as_deref(),
        non_empty(&expand).as_deref(),
    );
    drop(conn);
    let context = json!({
        "chains": result["chains"],
        "chains_truncated": result["truncated"],
        "total_chains": result["total_chains"],
        "expand": expand,
        "gap_minutes": data::SESSION_GAP_S / 60,
        "session": session,
        "window": window,
        "store_key": selection.store_key,
    });
    if is_hx_request(&headers) {
        return state.render(
            uri.path(),
            "chains_region.html",
            context,
            &selection.store_key,
            StatusCode::OK,
        );
    }
    state.render(
        uri.path(),
        "chains.html",
        context,
        &selection.store_key,
        StatusCode::OK,
    )
}

// --- route handlers: memory + tasks ---

async fn memory(State(state): State<AppState>, uri: Uri) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let mut memory_type = params.str_or("type");
    if memory_type.trim().is_empty() {
        memory_type = "all".into();
    }
    memory_type = memory_type.trim().to_string();
    if !data::MEMORY_TYPES.contains(&memory_type.as_str()) {
        memory_type = "all".into();
    }
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let entries = kb::get_recent_memories(
        &selection.knowledge,
        if memory_type == "all" {
            None
        } else {
            Some(&memory_type)
        },
    );
    state.render(
        uri.path(),
        "memory.html",
        json!({
            "memories": entries,
            "memory_types": data::MEMORY_TYPES,
            "memory_type": memory_type,
            "store_key": selection.store_key,
        }),
        &selection.store_key,
        StatusCode::OK,
    )
}

async fn memory_detail(
    State(state): State<AppState>,
    AxumPath((tier, slug)): AxumPath<(String, String)>,
    uri: Uri,
) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let memory_id = format!("memory/{tier}/{slug}");
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let Some(memory) = kb::get_memory_detail(&selection.knowledge, &memory_id) else {
        return not_found_page(
            "Memory not found",
            "No memory exists at this id.",
            "/memory",
            "Back to memory",
        );
    };
    state.render(
        uri.path(),
        "memory_detail.html",
        json!({"memory": memory, "store_key": selection.store_key}),
        &selection.store_key,
        StatusCode::OK,
    )
}

async fn tasks(State(state): State<AppState>, uri: Uri, headers: HeaderMap) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let mut status = params.str_or("status");
    if status.trim().is_empty() {
        status = "all".into();
    }
    status = status.trim().to_string();
    if !data::TASK_STATUSES.contains(&status.as_str()) {
        status = "all".into();
    }
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let entries = kb::get_task_queue(
        &selection.knowledge,
        if status == "all" { None } else { Some(&status) },
    );
    let context = json!({
        "tasks": entries,
        "statuses": data::TASK_STATUSES,
        "status": status,
        "store_key": selection.store_key,
    });
    if is_hx_request(&headers) {
        return state.render(
            uri.path(),
            "tasks_results.html",
            context,
            &selection.store_key,
            StatusCode::OK,
        );
    }
    state.render(
        uri.path(),
        "tasks.html",
        context,
        &selection.store_key,
        StatusCode::OK,
    )
}

// --- route handlers: knowledge ---

async fn knowledge(State(state): State<AppState>, uri: Uri, headers: HeaderMap) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let family_param = params.str_or("family");
    let family_param = if family_param.trim().is_empty() {
        "all".to_string()
    } else {
        family_param.trim().to_string()
    };
    let status_param = params.str_or("status");
    let status_param = if status_param.trim().is_empty() {
        "all".to_string()
    } else {
        status_param.trim().to_string()
    };
    let tag = params.str_or("tag").trim().to_string();
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let Ok(conn) = open_selected(&selection) else {
        return selection_missing_response(selection.db.as_deref().unwrap_or(""));
    };
    let mut family = family_param.clone();
    let mut status = status_param.clone();
    let mut result = kb::list_knowledge_docs(
        Some(&conn),
        &selection.knowledge,
        (family != "all").then_some(family.as_str()),
        (status != "all").then_some(status.as_str()),
        non_empty(&tag).as_deref(),
    );
    let mut families: Vec<String> = data::KNOWLEDGE_FAMILIES
        .iter()
        .map(|s| s.to_string())
        .collect();
    families.extend(
        result["families"]
            .as_object()
            .map(|map| map.keys().cloned().collect::<Vec<_>>())
            .unwrap_or_default(),
    );
    let mut statuses: Vec<String> = data::DOC_STATUSES.iter().map(|s| s.to_string()).collect();
    statuses.extend(
        result["statuses"]
            .as_object()
            .map(|map| map.keys().cloned().collect::<Vec<_>>())
            .unwrap_or_default(),
    );
    if family != "all" && !families.contains(&family) {
        family = "all".into();
    }
    if status != "all" && !statuses.contains(&status) {
        status = "all".into();
    }
    if (family != family_param) || (status != status_param) {
        result = kb::list_knowledge_docs(
            Some(&conn),
            &selection.knowledge,
            (family != "all").then_some(family.as_str()),
            (status != "all").then_some(status.as_str()),
            non_empty(&tag).as_deref(),
        );
    }
    drop(conn);
    families.sort();
    statuses.sort();
    let page_context = json!({
        "docs": result["docs"],
        "families": result["families"],
        "statuses": result["statuses"],
        "total": result["total"],
        "family": family,
        "status": status,
        "tag": tag,
        "family_options": families,
        "status_options": statuses,
        "store_key": selection.store_key,
    });
    if is_hx_request(&headers) {
        return state.render(
            uri.path(),
            "knowledge_results.html",
            page_context,
            &selection.store_key,
            StatusCode::OK,
        );
    }
    state.render(
        uri.path(),
        "knowledge.html",
        page_context,
        &selection.store_key,
        StatusCode::OK,
    )
}

async fn knowledge_graph(State(state): State<AppState>, uri: Uri) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let Ok(conn) = open_selected(&selection) else {
        return selection_missing_response(selection.db.as_deref().unwrap_or(""));
    };
    let graph = kb::get_knowledge_graph(Some(&conn), &selection.knowledge);
    drop(conn);
    state.render(
        uri.path(),
        "knowledge_graph.html",
        json!({"graph": graph, "store_key": selection.store_key}),
        &selection.store_key,
        StatusCode::OK,
    )
}

async fn knowledge_graph_inspect(State(state): State<AppState>, uri: Uri) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let doc_id = params.str_or("doc").trim().to_string();
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let doc = if doc_id.is_empty() {
        None
    } else {
        match open_selected(&selection) {
            Ok(conn) => {
                let doc =
                    kb::get_knowledge_graph_inspect(Some(&conn), &selection.knowledge, &doc_id);
                drop(conn);
                doc
            }
            Err(_) => return selection_missing_response(selection.db.as_deref().unwrap_or("")),
        }
    };
    state
        .environment
        .get_template("knowledge_graph_panel.html")
        .and_then(|template| {
            template.render(minijinja::value::Serde(&json!(
                {"doc": doc, "doc_id": doc_id, "store_key": selection.store_key}
            )))
        })
        .map(|body| Html(body).into_response())
        .unwrap_or_else(|err| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("cairn dashboard: template error in knowledge_graph_panel.html: {err}"),
            )
                .into_response()
        })
}

async fn knowledge_doc(
    State(state): State<AppState>,
    AxumPath((family, slug)): AxumPath<(String, String)>,
    uri: Uri,
) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let doc_id = format!("knowledge/{family}/{slug}");
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let workspace = std::env::current_dir().ok().map(|cwd| {
        store_paths::resolve_workspace(Some(&cwd))
            .to_string_lossy()
            .into_owned()
    });
    let Ok(conn) = open_selected(&selection) else {
        return selection_missing_response(selection.db.as_deref().unwrap_or(""));
    };
    let doc = kb::get_knowledge_doc_detail(
        Some(&conn),
        &selection.knowledge,
        &doc_id,
        workspace.as_deref(),
        &selection.store_key,
    );
    drop(conn);
    let Some(doc) = doc else {
        return not_found_page(
            "Knowledge document not found",
            "No knowledge document exists at this id.",
            "/knowledge",
            "Back to the catalog",
        );
    };
    state.render(
        uri.path(),
        "knowledge_doc.html",
        json!({"doc": doc, "store_key": selection.store_key}),
        &selection.store_key,
        StatusCode::OK,
    )
}

// --- route handlers: wiki ---

fn wiki_unreadable(state: &AppState, uri: &Uri, error: &str, store_key: &str) -> Response {
    state.render(
        uri.path(),
        "wiki_unreadable.html",
        json!({"manifest_error": error, "store_key": store_key}),
        store_key,
        StatusCode::OK,
    )
}

async fn wiki(State(state): State<AppState>, uri: Uri, headers: HeaderMap) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let repo = params.str_or("repo").trim().to_string();
    let state_filter = params.str_or("state").trim().to_string();
    let query = params.str_or("q").trim().to_string();
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let pages = match kb::get_wiki_pages(&selection.knowledge, non_empty(&repo).as_deref()) {
        Ok(pages) => pages,
        Err(error) => {
            // A malformed manifest renders the explicit unreadable state.
            let context = json!({
                "pages": [],
                "page_states": kb::DERIVED_STATES,
                "filters": {"repo": repo, "state": state_filter, "q": query},
                "store_key": selection.store_key,
                "manifest_error": error,
            });
            if is_hx_request(&headers) {
                return state.render(
                    uri.path(),
                    "wiki_results.html",
                    context,
                    &selection.store_key,
                    StatusCode::OK,
                );
            }
            return state.render(
                uri.path(),
                "wiki.html",
                context,
                &selection.store_key,
                StatusCode::OK,
            );
        }
    };
    let mut pages = pages;
    if kb::DERIVED_STATES.contains(&state_filter.as_str()) {
        pages.retain(|page| page["state"].as_str() == Some(state_filter.as_str()));
    }
    if !query.is_empty() {
        let needle = query.to_lowercase();
        pages.retain(|page| {
            page["title"]
                .as_str()
                .unwrap_or("")
                .to_lowercase()
                .contains(&needle)
                || page["page_id"]
                    .as_str()
                    .unwrap_or("")
                    .to_lowercase()
                    .contains(&needle)
                || page["description"]
                    .as_str()
                    .unwrap_or("")
                    .to_lowercase()
                    .contains(&needle)
        });
    }
    let context = json!({
        "pages": pages,
        "page_states": kb::DERIVED_STATES,
        "filters": {"repo": repo, "state": state_filter, "q": query},
        "store_key": selection.store_key,
    });
    if is_hx_request(&headers) {
        return state.render(
            uri.path(),
            "wiki_results.html",
            context,
            &selection.store_key,
            StatusCode::OK,
        );
    }
    state.render(
        uri.path(),
        "wiki.html",
        context,
        &selection.store_key,
        StatusCode::OK,
    )
}

async fn wiki_page(
    State(state): State<AppState>,
    AxumPath(page_id): AxumPath<String>,
    uri: Uri,
) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let page = match kb::get_wiki_page(&selection.knowledge, &page_id, None, "") {
        Ok(page) => page,
        Err(error) => return wiki_unreadable(&state, &uri, &error, &selection.store_key),
    };
    let Some(page) = page else {
        return not_found_page(
            "Wiki page not found",
            "No rendered article exists for this page id.",
            "/wiki",
            "Back to the wiki",
        );
    };
    // Legacy one-segment URL: temporary redirect to the canonical repo-qualified URL.
    let mut target = format!(
        "/wiki/{}/{}",
        shell::urlencode(page["repo"].as_str().unwrap_or("")),
        shell::urlencode(page["page_id"].as_str().unwrap_or(""))
    );
    if !selection.store_key.is_empty() {
        target.push_str("?store=");
        target.push_str(&shell::urlencode(&selection.store_key));
    }
    (StatusCode::TEMPORARY_REDIRECT, [("Location", target)]).into_response()
}

async fn wiki_page_repo(
    State(state): State<AppState>,
    AxumPath((repo, page_id)): AxumPath<(String, String)>,
    uri: Uri,
) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let page = match kb::get_wiki_page(
        &selection.knowledge,
        &page_id,
        Some(&repo),
        &selection.store_key,
    ) {
        Ok(page) => page,
        Err(error) => return wiki_unreadable(&state, &uri, &error, &selection.store_key),
    };
    let promoted = match kb::get_wiki_pages(&selection.knowledge, Some(&repo)) {
        Ok(pages) => pages
            .into_iter()
            .filter(|page| page["promoted"] == true)
            .collect::<Vec<_>>(),
        Err(error) => return wiki_unreadable(&state, &uri, &error, &selection.store_key),
    };
    let Some(page) = page else {
        return not_found_page(
            "Wiki page not found",
            "No rendered article exists for this page id.",
            "/wiki",
            "Back to the wiki",
        );
    };
    let index = promoted
        .iter()
        .position(|p| p["page_id"].as_str() == Some(page_id.as_str()));
    let prev_page = match index {
        Some(index) if index > 0 => Some(promoted[index - 1].clone()),
        _ => None,
    };
    let next_page = index.and_then(|index| promoted.get(index + 1).cloned());
    let html = page["html"].as_str().unwrap_or("").to_string();
    let has_mermaid = html.contains("class=\"mermaid\"");
    state.render(
        uri.path(),
        "wiki_page.html",
        json!({
            "page": page,
            "prev": prev_page,
            "has_mermaid": has_mermaid,
            "next": next_page,
            "store_key": selection.store_key,
        }),
        &selection.store_key,
        StatusCode::OK,
    )
}

// --- route handlers: settings / embeddings / database ---

async fn embeddings(State(state): State<AppState>, uri: Uri) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let probe_ok = settings::server_probe_once();
    let backend = cairn_query::tools_search::backend_name();
    let stamp = match data::current_model_stamp() {
        Some(stamp) => stamp,
        None => "unresolved (ValueError: no resolvable embedding backend)".to_string(),
    };
    let conn = open_selected(&selection);
    let corpora = match &conn {
        Ok(conn) => settings::embeddings_rows(conn),
        Err(_) => Vec::new(),
    };
    drop(conn);
    let cfg = settings::settings_context(&selection.store_key)["cfg"].clone();
    state.render(
        uri.path(),
        "embeddings.html",
        json!({
            "backend": backend,
            "stamp": stamp,
            "is_server": settings::is_server_family(&backend),
            "probe_ok": probe_ok.map(Value::from).unwrap_or(Value::Null),
            "rung": Value::Null,
            "corpora": corpora,
            "cfg": cfg,
            "store_key": selection.store_key,
        }),
        &selection.store_key,
        StatusCode::OK,
    )
}

async fn database(State(state): State<AppState>, uri: Uri) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let Ok(conn) = open_selected(&selection) else {
        return selection_missing_response(selection.db.as_deref().unwrap_or(""));
    };
    let schema = data::get_database_schema(&conn);
    drop(conn);
    state.render(
        uri.path(),
        "database.html",
        json!({"schema": schema, "store_key": selection.store_key}),
        &selection.store_key,
        StatusCode::OK,
    )
}

async fn settings_page(State(state): State<AppState>, uri: Uri) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    state.render(
        uri.path(),
        "settings.html",
        settings::settings_context(&selection.store_key),
        &selection.store_key,
        StatusCode::OK,
    )
}

type SettingsForm = HashMap<String, String>;

fn form_value<'a>(form: &'a SettingsForm, key: &str) -> &'a str {
    form.get(key).map(String::as_str).unwrap_or("")
}

fn numeric_settings_error(submitted: &HashMap<String, String>) -> Option<String> {
    for (key, what) in settings::SETTINGS_NUMERIC {
        let value = submitted.get(key).map(String::as_str).unwrap_or("");
        if value.is_empty() {
            continue;
        }
        let ok = match key {
            "CAIRN_EMBED_TIMEOUT" => value.parse::<f64>().is_ok(),
            _ => value.parse::<i64>().is_ok(),
        };
        if !ok {
            return Some(format!("Refused: {key} must be {what}."));
        }
    }
    None
}

fn base_url_needs_confirmation(submitted: &HashMap<String, String>, form: &SettingsForm) -> bool {
    let current_base = cairn_query::tools_search::config_value("CAIRN_EMBED_BASE_URL")
        .unwrap_or_default()
        .trim()
        .to_string();
    submitted
        .get("CAIRN_EMBED_BASE_URL")
        .map(String::as_str)
        .unwrap_or("")
        != current_base
        && !form.contains_key("confirm_base_url")
}

/// Split submitted knobs into writable values and keys dropped as invalid.
fn save_values(submitted: &HashMap<String, String>) -> (Vec<(String, String)>, Vec<String>) {
    let mut values = Vec::new();
    let mut dropped = Vec::new();
    for (key, value) in submitted {
        if key == "CAIRN_EMBED_API_KEY" {
            if !value.is_empty() {
                values.push((key.clone(), value.clone()));
            }
        } else if key == "CAIRN_EMBED_BACKEND" {
            if settings::SETTINGS_BACKENDS.contains(&value.as_str()) {
                values.push((key.clone(), value.clone()));
            } else {
                dropped.push(key.clone());
            }
        } else if !value.is_empty() {
            values.push((key.clone(), value.clone()));
        }
    }
    (values, dropped)
}

fn read_only_mode() -> bool {
    matches!(
        std::env::var("CAIRN_READ_ONLY")
            .unwrap_or_default()
            .trim()
            .to_lowercase()
            .as_str(),
        "1" | "true" | "yes"
    )
}

async fn settings_save(
    State(state): State<AppState>,
    uri: Uri,
    headers: HeaderMap,
    Form(form): Form<SettingsForm>,
) -> Response {
    let store_param = form_value(&form, "store").to_string();
    if settings::cross_site_rejected(
        headers.get("Host").and_then(|v| v.to_str().ok()),
        headers.get("Sec-Fetch-Site").and_then(|v| v.to_str().ok()),
        headers.get("Origin").and_then(|v| v.to_str().ok()),
        headers.get("Referer").and_then(|v| v.to_str().ok()),
    ) {
        return (StatusCode::FORBIDDEN, "cross-site request rejected").into_response();
    }
    let params = Params::parse(uri.query().unwrap_or(""));
    let Ok(selection) = resolve_selection(&state, Some(&params), Some(&store_param)) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let store_key = selection.store_key.clone();
    if read_only_mode() {
        return state.render(
            uri.path(),
            "settings.html",
            settings::settings_context_full(
                &store_key,
                false,
                "Refused: CAIRN_READ_ONLY is set; nothing was saved.",
                None,
            ),
            &store_key,
            StatusCode::FORBIDDEN,
        );
    }
    let submitted: HashMap<String, String> = settings::SETTINGS_KEYS
        .iter()
        .filter(|key| form.contains_key(**key))
        .map(|key| (key.to_string(), form_value(&form, key).trim().to_string()))
        .collect();
    if let Some(error) = numeric_settings_error(&submitted) {
        return state.render(
            uri.path(),
            "settings.html",
            settings::settings_context_full(&store_key, false, &error, None),
            &store_key,
            StatusCode::BAD_REQUEST,
        );
    }
    if base_url_needs_confirmation(&submitted, &form) {
        return state.render(
            uri.path(),
            "settings.html",
            settings::settings_context_full(
                &store_key,
                false,
                "Refused: a base-URL change requires the 'Confirm base-URL change' checkbox.",
                None,
            ),
            &store_key,
            StatusCode::BAD_REQUEST,
        );
    }
    let (values, dropped) = save_values(&submitted);
    if !dropped.is_empty() && values.is_empty() {
        let error = format!(
            "Refused: no savable value for {}; nothing was saved.",
            dropped.join(", ")
        );
        return state.render(
            uri.path(),
            "settings.html",
            settings::settings_context_full(&store_key, false, &error, None),
            &store_key,
            StatusCode::BAD_REQUEST,
        );
    }
    if let Err(reason) = settings::set_config_values(&values) {
        let error = format!("Could not write ~/.cairn/config.json: {reason}; nothing was saved.");
        return state.render(
            uri.path(),
            "settings.html",
            settings::settings_context_full(&store_key, false, &error, None),
            &store_key,
            StatusCode::INTERNAL_SERVER_ERROR,
        );
    }
    let error = if dropped.is_empty() {
        String::new()
    } else {
        format!("Saved, dropping {}: no savable value.", dropped.join(", "))
    };
    state.render(
        uri.path(),
        "settings.html",
        settings::settings_context_full(&store_key, true, &error, None),
        &store_key,
        StatusCode::OK,
    )
}

async fn settings_parity_check(
    State(state): State<AppState>,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    if settings::cross_site_rejected(
        headers.get("Host").and_then(|v| v.to_str().ok()),
        headers.get("Sec-Fetch-Site").and_then(|v| v.to_str().ok()),
        headers.get("Origin").and_then(|v| v.to_str().ok()),
        headers.get("Referer").and_then(|v| v.to_str().ok()),
    ) {
        return (StatusCode::FORBIDDEN, "cross-site request rejected").into_response();
    }
    let params = Params::parse(uri.query().unwrap_or(""));
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let parity = match open_selected(&selection) {
        Ok(conn) => match data::current_model_stamp() {
            Some(stamp) => settings::check_parity(&conn, &stamp),
            None => json!({
                "passed": false, "mean": "\u{2014}", "sampled": null,
                "reason": "ValueError: no resolvable embedding backend",
            }),
        },
        Err(_) => json!({
            "passed": false, "mean": "\u{2014}", "sampled": null,
            "reason": "sqlite read failed: no readable store",
        }),
    };
    state.render(
        uri.path(),
        "settings.html",
        settings::settings_context_full(&selection.store_key, false, "", Some(parity)),
        &selection.store_key,
        StatusCode::OK,
    )
}

// --- route handlers: editor JSON API ---

async fn editor_symbol(State(state): State<AppState>, uri: Uri) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let depth = data::parse_editor_depth(params.get("depth"));
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let Ok(conn) = open_selected(&selection) else {
        return selection_missing_response(selection.db.as_deref().unwrap_or(""));
    };
    let payload = data::editor_symbol_payload(&conn, params.get("name").unwrap_or(""), depth);
    drop(conn);
    Json(payload).into_response()
}

async fn editor_file(State(state): State<AppState>, uri: Uri) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let path = params.get("path").unwrap_or("").to_string();
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    Json(kb::editor_file_payload(&selection.knowledge, &path)).into_response()
}

async fn editor_status(State(state): State<AppState>, uri: Uri) -> Response {
    let params = Params::parse(uri.query().unwrap_or(""));
    let Ok(selection) = resolve_selection(&state, Some(&params), None) else {
        return selection_missing_response(params.str_or("store").as_str());
    };
    let Ok(conn) = open_selected(&selection) else {
        return selection_missing_response(selection.db.as_deref().unwrap_or(""));
    };
    let payload = data::editor_status_payload(&conn);
    drop(conn);
    Json(payload).into_response()
}

// --- router ---

/// The dashboard router (same route surface as the Starlette app).
pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/", get(landing))
        .route("/favicon.ico", get(favicon))
        .route("/static/{*path}", get(static_asset))
        .route("/workspaces", get(workspaces_overview))
        .route("/projects", get(projects))
        .route("/health", get(health))
        .route("/graph", get(graph))
        .route("/graph/candidates", get(graph_candidates))
        .route("/graph/suggest", get(graph_suggest))
        .route("/graph/neighbors", get(graph_neighbors))
        .route("/graph/inspect", get(graph_inspect))
        .route("/palette/results", get(palette_results))
        .route("/communities", get(communities))
        .route("/history", get(history))
        .route("/history.csv", get(history_csv))
        .route("/history.json", get(history_json))
        .route("/tokens", get(tokens))
        .route("/tokens.csv", get(tokens_csv))
        .route("/tokens.json", get(tokens_json))
        .route("/chains", get(chains))
        .route("/memory", get(memory))
        .route("/memory/{tier}/{slug}", get(memory_detail))
        .route("/tasks", get(tasks))
        .route("/knowledge", get(knowledge))
        .route("/knowledge/graph", get(knowledge_graph))
        .route("/knowledge/graph/inspect", get(knowledge_graph_inspect))
        .route("/knowledge/{family}/{slug}", get(knowledge_doc))
        .route("/wiki", get(wiki))
        .route("/wiki/{repo}/{page_id}", get(wiki_page_repo))
        .route("/wiki/{page_id}", get(wiki_page))
        .route("/embeddings", get(embeddings))
        .route("/database", get(database))
        .route("/settings", get(settings_page))
        .route("/settings/save", post(settings_save))
        .route("/settings/parity-check", post(settings_parity_check))
        .route("/editor/symbol", get(editor_symbol))
        .route("/editor/file", get(editor_file))
        .route("/editor/status", get(editor_status))
        .with_state(state)
}
