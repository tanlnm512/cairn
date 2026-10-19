//! `cairn dashboard`: loopback-only read-only web dashboard (dashboard.py port).

use std::net::{IpAddr, SocketAddr};
use std::path::{Path, PathBuf};
use std::process::ExitCode;

pub struct DashboardArgs {
    pub db: Option<String>,
    pub host: String,
    pub port: u16,
}

/// True when `host` is a loopback name or address (the dashboard is localhost-only).
pub fn is_loopback_host(host: &str) -> bool {
    if host == "localhost" {
        return true;
    }
    host.parse::<IpAddr>()
        .map(|ip| ip.is_loopback())
        .unwrap_or(false)
}

fn resolve_db(db: Option<&str>) -> PathBuf {
    db.map(PathBuf::from)
        .unwrap_or_else(|| crate::store_paths::resolve_store(None).db)
}

/// Dashboard CLI contract test hook: the banner lines for a resolved db.
fn banner_lines(host: &str, port: u16, db: &Path) -> Vec<String> {
    let mut lines = vec![
        format!(
            "cairn dashboard: http://{host}:{port}  (db: {})",
            db.display()
        ),
        "  read-only; Ctrl-C to stop".to_string(),
    ];
    if !db.exists() {
        lines.push(format!(
            "  note: db not found at {} — views show the empty state; run \
             `cairn build` to index this workspace",
            db.display()
        ));
    }
    lines
}

/// Start the loopback-only read-only web dashboard; blocks until interrupted.
pub fn run(args: DashboardArgs) -> ExitCode {
    if !is_loopback_host(&args.host) {
        // Usage errors exit 2 like the Python click.UsageError path.
        let mut cmd = clap::Command::new("cairn dashboard");
        let error = cmd.error(
            clap::error::ErrorKind::InvalidValue,
            format!(
                "refusing to bind --host {}: the dashboard is localhost-only",
                args.host
            ),
        );
        error.exit();
    }
    let db = resolve_db(args.db.as_deref());
    for line in banner_lines(&args.host, args.port, &db) {
        println!("{line}");
    }
    let bind: SocketAddr = SocketAddr::new(
        args.host
            .parse::<IpAddr>()
            .unwrap_or(IpAddr::from([127, 0, 0, 1])),
        args.port,
    );
    let runtime = match tokio::runtime::Runtime::new() {
        Ok(runtime) => runtime,
        Err(e) => {
            eprintln!("cairn: error: {e}");
            return ExitCode::FAILURE;
        }
    };
    let db_path = db.to_string_lossy().into_owned();
    let serving = runtime.block_on(serve(bind, db_path));
    match serving {
        Err(e) => {
            eprintln!("cairn: error: {e}");
            ExitCode::FAILURE
        }
        Ok(()) => {
            // Ctrl-C is the documented stop: exit 0.
            println!("dashboard stopped");
            ExitCode::SUCCESS
        }
    }
}

async fn serve(bind: SocketAddr, db_path: String) -> std::io::Result<()> {
    let app = axum::Router::new().route(
        "/",
        axum::routing::get(move || {
            let db_path = db_path.clone();
            async move { index_page(&db_path) }
        }),
    );
    let listener = tokio::net::TcpListener::bind(bind).await?;
    axum::serve(listener, app)
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await
}

/// Minimal read-only overview. Template parity over the full route surface is
/// the Phase 3b dashboard workstream; this page serves the same banner facts.
fn index_page(db_path: &str) -> String {
    let (symbols, files, edges, parse_errors) = store_counts(Path::new(db_path));
    format!(
        "<!doctype html>\n<html><head><title>cairn dashboard</title></head>\n\
         <body>\n<h1>cairn dashboard</h1>\n<p>read-only; served from \
         <code>{db_path}</code></p>\n<ul>\n<li>symbols: {symbols}</li>\n\
         <li>files: {files}</li>\n<li>edges: {edges}</li>\n\
         <li>parse errors: {parse_errors}</li>\n</ul>\n</body></html>\n"
    )
}

fn store_counts(db_path: &Path) -> (i64, i64, i64, i64) {
    let Ok(conn) = cairn_store::conn::open_read_only(db_path) else {
        return (0, 0, 0, 0);
    };
    let count = |sql: &str| -> i64 {
        conn.query_row(sql, [], |row| row.get::<_, i64>(0))
            .unwrap_or(0)
    };
    (
        count("SELECT COUNT(*) FROM symbols"),
        count("SELECT COUNT(*) FROM files"),
        count("SELECT COUNT(*) FROM edges"),
        count("SELECT COUNT(*) FROM parse_errors"),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn loopback_guard_accepts_only_localhost_binds() {
        assert!(is_loopback_host("localhost"));
        assert!(is_loopback_host("127.0.0.1"));
        assert!(is_loopback_host("::1"));
        assert!(!is_loopback_host("0.0.0.0"));
        assert!(!is_loopback_host("192.168.1.10"));
        assert!(!is_loopback_host("example.com"));
    }

    #[test]
    fn banner_carries_the_missing_db_note() {
        let lines = banner_lines("127.0.0.1", 8501, Path::new("/nonexistent/store.kg"));
        assert_eq!(
            lines[0],
            "cairn dashboard: http://127.0.0.1:8501  (db: /nonexistent/store.kg)"
        );
        assert_eq!(lines[1], "  read-only; Ctrl-C to stop");
        assert!(lines[2].contains("db not found at /nonexistent/store.kg"));
        let lines = banner_lines(
            "127.0.0.1",
            8501,
            Path::new(std::env::temp_dir().to_string_lossy().as_ref()),
        );
        assert_eq!(lines.len(), 2, "no note when the db path exists: {lines:?}");
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn dashboard_serves_the_overview_over_http() {
        let db_path =
            std::env::temp_dir().join(format!("cairn-dashboard-{}.kg", std::process::id()));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind");
        let addr = listener.local_addr().expect("addr");
        drop(listener);
        let server = tokio::spawn(serve(addr, db_path.to_string_lossy().into_owned()));
        // Bounded retry until the listener is live.
        let mut body = None;
        for _ in 0..50 {
            if let Some((_, bytes)) = cairn_daemon::lifecycle::http_get(
                "127.0.0.1",
                addr.port(),
                "/",
                std::time::Duration::from_secs(2),
            ) {
                body = Some(String::from_utf8_lossy(&bytes).into_owned());
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(50)).await;
        }
        let body = body.expect("dashboard body");
        assert!(body.contains("<h1>cairn dashboard</h1>"), "body {body}");
        server.abort();
    }
}
