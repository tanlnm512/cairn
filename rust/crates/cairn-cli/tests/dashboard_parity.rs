//! Route-parity tests: the rust dashboard serves the same route surface as
//! the Starlette app over a synthetic store (the C-4-style contract for 3b).

use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard, OnceLock};

use axum::body::Body;
use axum::http::{Request, StatusCode};
use tower::ServiceExt;

use cairn_cli::dashboard::{AppState, router};
use cairn_store::schema;

fn env_lock() -> MutexGuard<'static, ()> {
    static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    LOCK.get_or_init(|| Mutex::new(()))
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// One populated store under a scratch CAIRN_HOME: graph db + knowledge
/// bundle + registry entry, dropped when the guard is.
struct Fixture {
    home: PathBuf,
    db: PathBuf,
    knowledge: PathBuf,
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.home);
    }
}

fn write_concept(knowledge: &Path, id: &str, frontmatter: &str, body: &str) {
    let path = knowledge.join(format!("{id}.md"));
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, format!("---\n{frontmatter}---\n{body}")).unwrap();
}

fn fixture(tag: &str) -> Fixture {
    let home = std::env::temp_dir().join(format!(
        "cairn-dashit-{tag}-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    let key = "0123456789abcdef".to_string();
    let store = home.join(&key);
    let db = store.join(".kg");
    let knowledge = store.join(".knowledge");
    std::fs::create_dir_all(&knowledge).unwrap();
    let conn = schema::open_initialized(&db).expect("open initialized store");
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    conn.execute_batch(
        &format!(
        r#"
        INSERT INTO repos (id, name, path, language, indexed_at)
            VALUES ('r1', 'yarl', '/work/yarl', 'python', '2026-10-01T00:00:00Z');
        INSERT INTO files (id, repo_id, path, language, indexed_at)
            VALUES ('f1', 'r1', 'src/app.py', 'python', '2026-10-01T00:00:00Z'),
                   ('f2', 'r1', 'src/util.py', 'python', '2026-10-01T00:00:00Z');
        INSERT INTO symbols (id, file_id, name, qualified_name, kind, line_start, line_end, docstring)
            VALUES ('s1', 'f1', 'run_app', 'app.run_app', 'function', 10, 20, 'Entry point.'),
                   ('s2', 'f1', 'App', 'app.App', 'class', 1, 8, NULL),
                   ('s3', 'f2', 'helper', 'util.helper', 'function', 3, 5, NULL),
                   ('s4', 'f2', 'test_helper', 'util.test_helper', 'function', 7, 9, NULL);
        INSERT INTO edges (id, source_id, target_id, kind, line)
            VALUES ('e1', 's1', 's3', 'calls', 12),
                   ('e2', 's3', 's2', 'calls', 4);
        INSERT INTO tool_metrics (tool_name, session_id, invoked_at, duration_ms, status, req_chars, resp_chars, args_summary, source)
            VALUES ('search_symbols', 'sess1', {now}.0 - 7200.0, 12.5, 'ok', 800, 1600, '{{"name": "run_app"}}', 'mcp'),
                   ('search_symbols', 'sess1', {now}.0 - 7140.0, 8.0, 'ok', 400, 900, '{{"name": "app"}}', 'mcp'),
                   ('explore', 'sess1', {now}.0 - 5400.0, 200.0, 'error', 900, 100, '{{}}', 'cli'),
                   ('explore', 'sess2', {now}.0 - 600.0, 40.0, 'ok', 1200, 3000, '{{}}', 'cli');
        INSERT INTO build_runs (kind, started_at, duration_s)
            VALUES ('full', '2026-10-01T00:00:00Z', 3.2);
        INSERT INTO communities (id, label, size) VALUES (1, 'core', 2);
        INSERT INTO symbol_communities (community_id, symbol_id, structural_degree)
            VALUES (1, 's1', 3), (1, 's3', 1);
        INSERT INTO knowledge_edges (doc_id, related_id, relation, kind)
            VALUES ('knowledge/business-rule/auth-rule', 'knowledge/spec/auth-spec', 'relates', 'curated');
        INSERT INTO knowledge_doc_refs (doc_id, ref, ref_kind, verified)
            VALUES ('knowledge/business-rule/auth-rule', 'run_app', 'symbol', 1);
        "#
    ))
    .expect("seed store");
    drop(conn);

    // Knowledge bundle: docs, memories, a promoted wiki page, a task, a compass entry.
    write_concept(
        &knowledge,
        "knowledge/business-rule/auth-rule",
        "type: business-rule\ntitle: Auth rule\ntags:\n  - auth\ngenerated:\n  at: 2026-10-02T00:00:00Z\n",
        "Tokens expire after `run_app` windows.\n\n## Details\n\nbody text\n",
    );
    write_concept(
        &knowledge,
        "knowledge/spec/auth-spec",
        "type: spec\ntitle: Auth spec\ndoc_status: active\ngenerated:\n  at: 2026-10-03T00:00:00Z\n",
        "spec body\n",
    );
    write_concept(
        &knowledge,
        "memory/tribal/alpha",
        "type: decision\ntitle: Alpha decision\nmemory_type: decision\nmemory_supersedes:\n  - memory/raw/beta\ngenerated:\n  at: 2026-10-04T00:00:00Z\n",
        "# Alpha\n\nThe decision body.\n",
    );
    write_concept(
        &knowledge,
        "memory/raw/beta",
        "type: pattern\ntitle: Beta pattern\n",
        "old\n",
    );
    write_concept(
        &knowledge,
        "wiki/pages/yarl/overview",
        "type: Wiki-Article\ntitle: Overview\ngenerated:\n  at: 2026-10-05T00:00:00Z\ncommit_sha: headsha\n",
        "# Overview\n\nWiki prose about `run_app`.\n",
    );
    write_concept(
        &knowledge,
        "_tasks/task1",
        "type: Task\ntask_kind: wiki-page\nresource: yarl/overview\nstatus: done\ngenerated:\n  at: 2026-10-05T00:00:00Z\n",
        "",
    );
    write_concept(
        &knowledge,
        "compass/src",
        "type: Compass\ntitle: Src guide\nresource: src\n",
        "navigation notes\n",
    );
    std::fs::create_dir_all(knowledge.join("_wiki")).unwrap();
    std::fs::write(
        knowledge.join("_wiki/manifest.json"),
        r#"{"schema": "cairn-wiki-manifest-3", "pages": {"yarl/overview": {"title": "Overview", "description": "d", "input_hash": "h", "seeds": {"symbols": ["run_app"]}}}}"#,
    )
    .unwrap();

    std::fs::write(
        home.join("workspaces.json"),
        r#"{"/work/yarl": "0123456789abcdef"}"#,
    )
    .unwrap();
    Fixture {
        home,
        db,
        knowledge,
    }
}

/// Point CAIRN_HOME at the fixture and build the router; the env guard is
/// released on drop, before the fixture cleanup.
struct Serving {
    app: axum::Router,
    // Holds the env lock for the test's whole lifetime: every request
    // handler reads CAIRN_HOME, so the fixture window must be exclusive.
    #[allow(dead_code)]
    env: MutexGuard<'static, ()>,
    #[allow(dead_code)]
    fixture: Fixture,
}

impl Serving {
    fn new(tag: &str) -> Serving {
        let env = env_lock();
        let fixture = fixture(tag);
        // SAFETY: single-threaded test process section under env_lock.
        unsafe { std::env::set_var("CAIRN_HOME", &fixture.home) };
        let app = router(AppState::new(
            Some(fixture.db.to_string_lossy().into_owned()),
            Some(fixture.knowledge.to_string_lossy().into_owned()),
        ));
        Serving { app, env, fixture }
    }

    async fn get(&self, uri: &str) -> (StatusCode, String, axum::http::HeaderMap) {
        let response = self
            .app
            .clone()
            .oneshot(Request::builder().uri(uri).body(Body::empty()).unwrap())
            .await
            .expect("response");
        let status = response.status();
        let headers = response.headers().clone();
        let body = axum::body::to_bytes(response.into_body(), 1 << 22)
            .await
            .map(|bytes| String::from_utf8_lossy(&bytes).into_owned())
            .unwrap_or_default();
        (status, body, headers)
    }

    async fn post(&self, uri: &str, headers: &[(&str, &str)], form: &str) -> (StatusCode, String) {
        let mut builder = Request::builder()
            .method("POST")
            .uri(uri)
            .header("content-type", "application/x-www-form-urlencoded");
        for (key, value) in headers {
            builder = builder.header(*key, *value);
        }
        let response = self
            .app
            .clone()
            .oneshot(builder.body(Body::from(form.to_string())).unwrap())
            .await
            .expect("response");
        let status = response.status();
        let body = axum::body::to_bytes(response.into_body(), 1 << 22)
            .await
            .map(|bytes| String::from_utf8_lossy(&bytes).into_owned())
            .unwrap_or_default();
        (status, body)
    }
}

impl Drop for Serving {
    fn drop(&mut self) {
        // SAFETY: the lock is still held via self.env.
        unsafe { std::env::remove_var("CAIRN_HOME") };
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn core_routes_serve_the_shell() {
    let server = Serving::new("core");
    let (status, body, _) = server.get("/").await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("<h1>Cairn Dashboard</h1>"));
    assert!(body.contains("/static/app.css?v="));

    let (status, body, _) = server.get("/?store=0123456789abcdef").await;
    assert_eq!(status, StatusCode::OK);
    // minijinja escapes "/" as &#x2f; in attributes (browsers read it the same).
    assert!(
        body.contains("graph?store=0123456789abcdef"),
        "store seam rides nav: {body}"
    );

    let (status, body, headers) = server.get("/static/app.css").await;
    assert_eq!(status, StatusCode::OK, "static css");
    assert_eq!(
        headers.get("content-type").unwrap(),
        "text/css; charset=utf-8"
    );
    assert_eq!(headers.get("cache-control").unwrap(), "no-cache");
    assert!(!body.is_empty());

    let (status, body, _) = server.get("/favicon.ico").await;
    assert_eq!(status, StatusCode::OK, "favicon");
    assert!(body.contains("<svg"), "favicon body");

    let (status, body, _) = server.get("/workspaces").await;
    assert_eq!(status, StatusCode::OK, "workspaces");
    assert!(body.contains("yarl"), "workspace label: {body}");

    let (status, body, _) = server.get("/projects").await;
    assert_eq!(status, StatusCode::OK, "projects: {body}");
    assert!(
        body.contains("yarl") && body.contains("badge-not"),
        "{body}"
    );

    let (status, body, _) = server.get("/health").await;
    assert_eq!(status, StatusCode::OK, "health");
    assert!(
        body.contains("tool_metrics"),
        "health shows retention knobs"
    );

    let (status, _, _) = server.get("/definitely-not-a-route").await;
    assert_eq!(status, StatusCode::NOT_FOUND);
}

#[tokio::test(flavor = "multi_thread")]
async fn unknown_store_falls_back_to_the_missing_db_page() {
    let server = Serving::new("missing");
    let (status, body, _) = server.get("/?store=ffffffffffffffff").await;
    assert_eq!(
        status,
        StatusCode::OK,
        "the missing-db page is a 200 like Starlette"
    );
    assert!(body.contains("No graph database found"), "{body}");
}

#[tokio::test(flavor = "multi_thread")]
async fn graph_routes_parity() {
    let server = Serving::new("graph");

    for scope in ["module", "symbol", "impact", "deps", "repo"] {
        let (status, body, _) = server.get(&format!("/graph?scope={scope}")).await;
        assert_eq!(status, StatusCode::OK, "scope {scope}: {body}");
        assert!(
            body.contains("vis-network.min.js"),
            "scope {scope} ships the canvas"
        );
    }
    let (status, body, _) = server.get("/graph?scope=symbol&focus=run_app").await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("run_app"), "{body}");

    let (status, body, _) = server.get("/graph/candidates?name=run_app").await;
    assert_eq!(status, StatusCode::OK);
    assert!(
        body.contains("\"run_app\"") && body.contains("\"truncated\":false"),
        "{body}"
    );

    let (status, body, _) = server.get("/graph/suggest?name=run").await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("\"run_app\""), "{body}");

    let (status, body, _) = server.get("/graph/neighbors?name=run_app").await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("\"helper\""), "{body}");

    let (status, body, _) = server.get("/graph/inspect?name=run_app").await;
    assert_eq!(status, StatusCode::OK);
    assert!(
        body.contains("\"found\":true") && body.contains("\"callers\""),
        "{body}"
    );
    // HX requests get the panel fragment.
    let response = server
        .app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/graph/inspect?name=run_app")
                .header("HX-Request", "true")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let fragment = axum::body::to_bytes(response.into_body(), 1 << 22)
        .await
        .unwrap();
    let fragment = String::from_utf8_lossy(&fragment);
    assert!(!fragment.contains("<html"), "fragment only: {fragment}");

    let (status, body, _) = server.get("/palette/results?q=run").await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("run_app"), "{body}");
    let (status, body, _) = server.get("/palette/results?q=graph").await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("Graph"), "{body}");
}

#[tokio::test(flavor = "multi_thread")]
async fn history_tokens_chains_routes_parity() {
    let server = Serving::new("activity");
    let (status, body, _) = server.get("/history").await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert!(
        body.contains("search_symbols") && body.contains("explore"),
        "{body}"
    );

    let (status, body, _) = server.get("/history?tool=search&window=24h").await;
    assert_eq!(status, StatusCode::OK, "tool+window: {body}");
    assert!(body.contains("search_symbols"), "{body}");

    let (status, body, _) = server.get("/history?session=sess1").await;
    assert_eq!(status, StatusCode::OK, "{body}");
    let sess2_rows = body.matches("sess2").count();
    let sess1_rows = body.matches("sess1").count();
    assert!(
        sess1_rows > 0 && sess2_rows <= sess1_rows,
        "session filter: sess1={sess1_rows} sess2={sess2_rows}"
    );

    let (status, body, headers) = server.get("/history.csv").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(headers.get("content-type").unwrap(), "text/csv");
    assert!(body.starts_with("id,tool_name,session_id,"), "{body}");
    assert!(body.contains("\r\n"), "csv line terminator");

    let (status, body, headers) = server.get("/history.json").await;
    assert_eq!(status, StatusCode::OK);
    assert!(
        headers
            .get("content-disposition")
            .unwrap()
            .to_str()
            .unwrap()
            .contains("attachment")
    );
    assert!(body.contains("search_symbols"), "{body}");

    let (status, body, _) = server.get("/tokens").await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("Estimation mode: heuristic"), "{body}");

    let (status, body, _) = server.get("/tokens.csv").await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.starts_with("tool_name,"), "{body}");

    let (status, body, _) = server.get("/chains").await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("sess1"), "{body}");

    let (status, _, _) = server.get("/tokens.json").await;
    assert_eq!(status, StatusCode::OK);
}

#[tokio::test(flavor = "multi_thread")]
async fn communities_database_editor_routes_parity() {
    let server = Serving::new("sys");

    let (status, body, _) = server.get("/communities").await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert!(body.contains("core"), "{body}");

    let (status, body, _) = server.get("/communities?community=1").await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("app.run_app"), "{body}");

    let (status, body, _) = server.get("/database").await;
    assert_eq!(status, StatusCode::OK);
    assert!(
        body.contains("tool_metrics") && body.contains("symbols"),
        "{body}"
    );

    let (status, body, _) = server.get("/editor/symbol?name=run_app").await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("\"callees\":{\"count\":1}"), "{body}");

    let (status, body, _) = server.get("/editor/symbol?name=helper").await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("\"callers\":{\"count\":1}"), "{body}");
    assert!(
        body.contains("\"run_app\""),
        "blast radius names the caller: {body}"
    );

    let (status, body, _) = server.get("/editor/status").await;
    assert_eq!(status, StatusCode::OK);
    assert!(
        body.contains("\"indexed\":true") && body.contains("\"symbols\":4"),
        "{body}"
    );

    let (status, body, _) = server.get("/editor/file?path=src/app.py").await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("Src guide"), "compass hit: {body}");
}

#[tokio::test(flavor = "multi_thread")]
async fn memory_and_tasks_routes_parity() {
    let server = Serving::new("memory");
    let (status, body, _) = server.get("/memory").await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert!(body.contains("Alpha decision"), "{body}");

    let (status, body, _) = server.get("/memory?type=pattern").await;
    assert_eq!(status, StatusCode::OK);
    assert!(
        body.contains("Beta pattern") && !body.contains("Alpha decision"),
        "{body}"
    );

    let (status, body, _) = server.get("/memory/tribal/alpha").await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("The decision body"), "{body}");
    assert!(body.contains("Beta pattern"), "supersedes neighbor renders");

    let (status, body, _) = server.get("/memory/tribal/nope").await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert!(body.contains("Memory not found"), "{body}");

    let (status, body, _) = server.get("/tasks").await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("wiki-page"), "{body}");
}

#[tokio::test(flavor = "multi_thread")]
async fn knowledge_routes_parity() {
    let server = Serving::new("knowledge");
    let (status, body, _) = server.get("/knowledge").await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert!(
        body.contains("Auth rule") && body.contains("Auth spec"),
        "{body}"
    );

    let (status, body, _) = server.get("/knowledge?family=spec").await;
    assert_eq!(status, StatusCode::OK, "family=spec: {body}");
    assert!(
        body.contains("Auth spec") && !body.contains("Auth rule"),
        "{body}"
    );

    let (status, body, _) = server.get("/knowledge/business-rule/auth-rule").await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("Tokens expire"), "rendered body");
    assert!(
        body.contains("graph?scope=symbol&amp;focus=run_app"),
        "symbol ref deep link"
    );
    assert!(body.contains("Auth spec"), "related doc renders");

    let (status, body, _) = server.get("/knowledge/business-rule/missing").await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert!(body.contains("Knowledge document not found"), "{body}");

    let (status, body, _) = server.get("/knowledge/graph").await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("knowledge-graph.js"), "{body}");

    let (status, body, _) = server
        .get("/knowledge/graph/inspect?doc=knowledge/spec/auth-spec")
        .await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("Auth spec"), "{body}");
}

#[tokio::test(flavor = "multi_thread")]
async fn wiki_routes_parity() {
    let server = Serving::new("wiki");
    let (status, body, _) = server.get("/wiki").await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert!(
        body.contains("Overview") && body.contains("promoted"),
        "{body}"
    );

    let (status, body, _) = server.get("/wiki?state=promoted&q=overview").await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("Overview"), "{body}");

    let (status, body, _) = server.get("/wiki/yarl/overview").await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("Wiki prose"), "{body}");
    assert!(
        body.contains("scope=symbol&amp;focus=run_app"),
        "ref map rides the body"
    );
    assert!(
        body.contains("badge-fresh")
            || body.contains("badge-unknown")
            || body.contains("badge-stale"),
        "staleness chip renders: {body}"
    );

    let (status, _, headers) = server.get("/wiki/overview").await;
    assert_eq!(
        status,
        StatusCode::TEMPORARY_REDIRECT,
        "legacy URL redirects"
    );
    assert_eq!(headers.get("location").unwrap(), "/wiki/yarl/overview");

    let (status, body, _) = server.get("/wiki/yarl/missing").await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert!(body.contains("Wiki page not found"), "{body}");
}

#[tokio::test(flavor = "multi_thread")]
async fn settings_routes_parity_and_cross_site_guard() {
    let server = Serving::new("settings");
    let (status, body, _) = server.get("/settings").await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("CAIRN_EMBED_BACKEND"), "{body}");
    assert!(!body.contains("secret-value"), "the api key never prefills");

    let (status, body, _) = server.get("/embeddings").await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("corpus"), "{body}");

    // Same-origin POST saves.
    let (status, body) = server
        .post(
            "/settings/save",
            &[
                ("host", "127.0.0.1:8765"),
                ("sec-fetch-site", "same-origin"),
            ],
            "CAIRN_EMBED_BACKEND=hash&CAIRN_EMBED_TIMEOUT=30",
        )
        .await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert!(body.contains("Settings saved"), "{body}");

    // Numeric validation refuses garbage.
    let (status, body) = server
        .post(
            "/settings/save",
            &[
                ("host", "127.0.0.1:8765"),
                ("sec-fetch-site", "same-origin"),
            ],
            "CAIRN_EMBED_TIMEOUT=soon",
        )
        .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert!(body.contains("must be a number of seconds"), "{body}");

    // Base-URL changes need the confirm checkbox.
    let (status, body) = server
        .post(
            "/settings/save",
            &[
                ("host", "127.0.0.1:8765"),
                ("sec-fetch-site", "same-origin"),
            ],
            "CAIRN_EMBED_BASE_URL=http://127.0.0.1:9999/v1",
        )
        .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert!(body.contains("Confirm base-URL change"), "{body}");

    // Unknown backends are dropped, and a drop-only submit is refused.
    let (status, body) = server
        .post(
            "/settings/save",
            &[
                ("host", "127.0.0.1:8765"),
                ("sec-fetch-site", "same-origin"),
            ],
            "CAIRN_EMBED_BACKEND=electric",
        )
        .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert!(body.contains("no savable value"), "{body}");

    // Cross-site POSTs are rejected before any handler logic.
    let (status, body) = server
        .post(
            "/settings/save",
            &[("host", "127.0.0.1:8765"), ("sec-fetch-site", "same-site")],
            "CAIRN_EMBED_BACKEND=hash",
        )
        .await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(body, "cross-site request rejected");

    let (status, body) = server
        .post(
            "/settings/parity-check",
            &[("host", "evil.example"), ("sec-fetch-site", "same-origin")],
            "",
        )
        .await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    let _ = body;

    let (status, body) = server
        .post(
            "/settings/parity-check",
            &[
                ("host", "127.0.0.1:8765"),
                ("sec-fetch-site", "same-origin"),
            ],
            "",
        )
        .await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("parity"), "{body}");
}

#[tokio::test(flavor = "multi_thread")]
async fn settings_save_surfaces_the_config_parse_reason() {
    let server = Serving::new("cfgparse");
    let config = server.fixture.home.join("config.json");
    std::fs::write(&config, "{not json").unwrap();

    let (status, body) = server
        .post(
            "/settings/save",
            &[
                ("host", "127.0.0.1:8765"),
                ("sec-fetch-site", "same-origin"),
            ],
            "CAIRN_EMBED_SERVER_MODEL=new-value",
        )
        .await;

    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR, "{body}");
    assert!(body.contains("not valid JSON"), "{body}");
    assert!(body.contains("nothing was saved"), "{body}");
    assert_eq!(
        std::fs::read_to_string(&config).unwrap(),
        "{not json",
        "the malformed file is never overwritten"
    );
}
