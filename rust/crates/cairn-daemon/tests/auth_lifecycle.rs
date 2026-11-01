//! Auth resolution + launchd plist + stray-sweep contract tests (the 1c parity floor).

use std::collections::HashSet;
#[cfg(unix)]
use std::path::Path;
use std::path::PathBuf;
#[cfg(unix)]
use std::process::Command;
use std::time::Duration;

use cairn_daemon::auth;
use cairn_daemon::healthz::HealthzState;
#[cfg(unix)]
use cairn_daemon::lifecycle::SweepCtx;
use cairn_daemon::lifecycle::{self, cg_bin_from, is_cairn_serve_cmdline, render_plist};
use serde_json::Value;

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .expect("repo root")
}

// --- auth --------------------------------------------------------------------

/// One serialized function: every auth assertion that touches process env.
#[test]
fn auth_resolution_contract() {
    // Explicit > env, empty explicit falls through, empty env is no key.
    unsafe {
        std::env::remove_var("CAIRN_MCP_API_KEY");
        assert_eq!(auth::resolve_api_key(None), None);
        std::env::set_var("CAIRN_MCP_API_KEY", "envkey");
        assert_eq!(auth::resolve_api_key(None).as_deref(), Some("envkey"));
        assert_eq!(
            auth::resolve_api_key(Some("explicit")).as_deref(),
            Some("explicit")
        );
        assert_eq!(auth::resolve_api_key(Some("")).as_deref(), Some("envkey"));
        std::env::set_var("CAIRN_MCP_API_KEY", "");
        assert_eq!(auth::resolve_api_key(None), None);

        // env > persisted file; the persisted file is the fallback alone.
        let home = tempfile_dir("auth-home");
        std::fs::create_dir_all(&home).expect("home dir");
        std::env::set_var("CAIRN_HOME", &home);
        assert_eq!(auth::persisted_api_key(), None);
        std::fs::write(home.join("mcp-api-key"), "  persisted-key  \n").expect("key file");
        assert_eq!(auth::persisted_api_key().as_deref(), Some("persisted-key"));
        std::env::set_var("CAIRN_MCP_API_KEY", "envkey");
        assert_eq!(auth::client_key().as_deref(), Some("envkey"));
        std::env::remove_var("CAIRN_MCP_API_KEY");
        assert_eq!(auth::client_key().as_deref(), Some("persisted-key"));
        std::fs::write(home.join("mcp-api-key"), "").expect("empty key file");
        assert_eq!(auth::persisted_api_key(), None);
        assert_eq!(auth::client_key(), None);
        std::env::remove_var("CAIRN_HOME");
        std::fs::remove_dir_all(&home).ok();
    }
}

fn tempfile_dir(tag: &str) -> PathBuf {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let dir = std::env::temp_dir().join(format!("cairn-lc-{}-{nanos}-{tag}", std::process::id()));
    std::fs::create_dir_all(&dir).expect("temp dir");
    dir
}

// --- plist template ----------------------------------------------------------

#[test]
fn plist_template_execs_the_named_binary() {
    let plist = render_plist(
        "/opt/cairn/bin/cairn",
        9911,
        "127.0.0.1",
        Some("/ws"),
        Some("/store/.kg"),
        Some("/store/.knowledge"),
        false,
        Some("k"),
    );
    assert_eq!(plist.label, "dev.cairn.mcp");
    assert_eq!(plist.program_arguments[0], "/opt/cairn/bin/cairn");
    assert_eq!(
        plist.program_arguments[1..].to_vec(),
        vec![
            "serve".to_string(),
            "run".to_string(),
            "--port".to_string(),
            "9911".to_string(),
            "--host".to_string(),
            "127.0.0.1".to_string(),
        ]
    );
    let env: HashSet<(String, String)> = plist.environment_variables.iter().cloned().collect();
    for pair in [
        ("CAIRN_WORKSPACE", "/ws"),
        ("CAIRN_DB", "/store/.kg"),
        ("CAIRN_KNOWLEDGE", "/store/.knowledge"),
        ("CAIRN_MCP_API_KEY", "k"),
    ] {
        assert!(
            env.contains(&(pair.0.to_string(), pair.1.to_string())),
            "env missing {pair:?}: {env:?}"
        );
    }
    assert!(plist.run_at_load && plist.keep_alive);
    assert_eq!(plist.stdout_path, plist.stderr_path);
}

#[test]
fn plist_read_only_flag_and_optional_env() {
    let plist = render_plist("cairn", 9876, "0.0.0.0", None, None, None, true, None);
    assert!(plist.program_arguments.contains(&"--read-only".to_string()));
    let env: HashSet<(String, String)> = plist.environment_variables.iter().cloned().collect();
    assert!(env.contains(&("CAIRN_READ_ONLY".to_string(), "1".to_string())));
    assert!(!env.iter().any(|(k, _)| k == "CAIRN_WORKSPACE"));
    assert!(!env.iter().any(|(k, _)| k == "CAIRN_MCP_API_KEY"));
}

#[test]
fn plist_xml_is_launchd_loadable() {
    let plist = render_plist(
        "/opt/cairn/bin/cairn",
        9876,
        "127.0.0.1",
        Some("/ws & co"),
        Some("/store/.kg"),
        None,
        false,
        None,
    );
    let dir = tempfile_dir("plist");
    let path = dir.join(format!("{}.plist", lifecycle::LABEL));
    let written = lifecycle::write_plist_file(&path, &plist).expect("write plist");
    assert_eq!(written, path);
    let xml = std::fs::read_to_string(&path).expect("plist bytes");
    assert!(xml.contains("<key>Label</key>"));
    assert!(xml.contains("<string>/ws &amp; co</string>"));
    #[cfg(target_os = "macos")]
    {
        // plutil is the launchd-format arbiter on the daemon's home platform.
        let status = Command::new("plutil")
            .arg("-lint")
            .arg(&path)
            .status()
            .expect("plutil");
        assert!(status.success(), "plutil -lint rejected {}", path.display());
    }
    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn cg_bin_prefers_existing_candidates() {
    let dir = tempfile_dir("cgbin");
    let bin = dir.join("cairn");
    std::fs::write(&bin, b"#!/bin/sh\n").expect("bin stub");
    // env candidate exists -> wins even with a running binary offered.
    assert_eq!(
        cg_bin_from(
            Some(&bin.to_string_lossy()),
            Some("/nonexistent/cairn"),
            None,
            &dir
        ),
        bin.to_string_lossy()
    );
    // env empty/missing -> running candidate; nothing exists -> launchd PATH fallback.
    assert_eq!(
        cg_bin_from(
            Some("/nonexistent/cairn"),
            Some(&bin.to_string_lossy()),
            None,
            &dir
        ),
        bin.to_string_lossy()
    );
    assert_eq!(
        cg_bin_from(None, Some(&bin.to_string_lossy()), None, &dir),
        bin.to_string_lossy()
    );
    assert_eq!(cg_bin_from(None, None, None, &dir), "cairn");
    std::fs::remove_dir_all(&dir).ok();
}

// --- stray-sweep cmdline classifier ------------------------------------------

#[test]
fn serve_cmdline_classifier_matrix() {
    assert!(is_cairn_serve_cmdline(
        "/usr/local/bin/cairn serve run --port 9876"
    ));
    assert!(is_cairn_serve_cmdline("cairn serve run"));
    assert!(is_cairn_serve_cmdline("cairn serve run --read-only"));
    assert!(!is_cairn_serve_cmdline(
        "/usr/bin/python3 /opt/bin/cairn serve run"
    ));
    // Lifecycle subcommands are transient CLI invocations, never servers.
    for sub in ["start", "stop", "status", "restart"] {
        assert!(
            !is_cairn_serve_cmdline(&format!("cairn serve {sub}")),
            "subcommand {sub} must not be classified as a server"
        );
    }
    assert!(!is_cairn_serve_cmdline("cairn build"));
    assert!(!is_cairn_serve_cmdline("vim cairn serve run"));
    assert!(!is_cairn_serve_cmdline("cairn"));
    assert!(!is_cairn_serve_cmdline("serve run"));
    assert!(!is_cairn_serve_cmdline(""));
}

// --- stray-sweep integration (real processes, real lsof) ----------------------

#[cfg(unix)]
mod strays {
    use super::*;
    use std::os::unix::process::CommandExt;

    /// Spawn a live process whose ps cmdline is `cairn serve <last_arg>` (arg0
    /// via sh's name-based script dispatch) holding `db_path` open on fd 9.
    /// The handle is returned because the test process is the parent: after a
    /// kill the pid is only observable as reaped (a zombie answers `ps`).
    fn spawn_fake_daemon(dir: &Path, db_path: &Path, last_arg: &str) -> std::process::Child {
        // sh invoked with argv0=cairn treats argv[1] ("serve") as the script.
        let script = dir.join("serve");
        std::fs::write(
            &script,
            format!("exec 9<\"{}\"\nsleep 60\n", db_path.display()),
        )
        .expect("write fake serve script");
        Command::new("/bin/sh")
            .arg("serve")
            .arg(last_arg)
            .current_dir(dir)
            .arg0("cairn")
            .spawn()
            .expect("spawn fake daemon")
    }

    fn sweep_ctx<'a>(db_path: &'a Path, pidfile_pid: Option<u32>) -> SweepCtx<'a> {
        SweepCtx {
            db_path,
            pidfile_pid,
            protected: HashSet::from([std::process::id()]),
        }
    }

    fn wait_exited(child: &mut std::process::Child, timeout: Duration) -> bool {
        let deadline = std::time::Instant::now() + timeout;
        while std::time::Instant::now() < deadline {
            if child.try_wait().expect("try_wait").is_some() {
                return true;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        false
    }

    #[test]
    fn strays_are_found_and_killed_only_when_verified() {
        let dir = tempfile_dir("strays");
        let db_path = dir.join("store.kg");
        std::fs::write(&db_path, b"sqlite").expect("db stub");

        // A lifecycle-subcommand invocation holding the same db is never a stray.
        let mut exempt = spawn_fake_daemon(&dir, &db_path, "stop");
        // A real `cairn serve` invocation holding the db is.
        let mut stray = spawn_fake_daemon(&dir, &db_path, "run");

        // Wait until ps actually reports the stray's serve cmdline (spawn-vs-ps race).
        let deadline = std::time::Instant::now() + Duration::from_secs(10);
        while std::time::Instant::now() < deadline {
            if lifecycle::pid_cmdline(stray.id())
                .map(|c| is_cairn_serve_cmdline(&c))
                .unwrap_or(false)
            {
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        let stray_cmdline = lifecycle::pid_cmdline(stray.id()).expect("stray cmdline");
        assert!(
            is_cairn_serve_cmdline(&stray_cmdline),
            "fake daemon cmdline {stray_cmdline:?} must classify as a server"
        );
        let exempt_cmdline = lifecycle::pid_cmdline(exempt.id()).expect("exempt cmdline");
        assert!(
            !is_cairn_serve_cmdline(&exempt_cmdline),
            "lifecycle subcommand {exempt_cmdline:?} must not classify as a server"
        );

        let stray_pid = stray.id();
        let found = lifecycle::find_strays_ctx(&sweep_ctx(&db_path, None));
        assert_eq!(
            found,
            vec![stray_pid],
            "exactly the serve cmdline holding the db is a stray; found {found:?}"
        );

        // pidfile protection: recording the stray's pid in a verified pidfile exempts it.
        let found = lifecycle::find_strays_ctx(&sweep_ctx(&db_path, Some(stray_pid)));
        assert!(found.is_empty(), "pidfile-protected stray must be exempt");

        let killed = lifecycle::sweep_strays_ctx(&sweep_ctx(&db_path, None), true);
        assert_eq!(killed, 1);
        assert!(
            wait_exited(&mut stray, Duration::from_secs(10)),
            "stray pid {stray_pid} survived the sweep"
        );
        exempt.kill().expect("kill exempt fake");
        let _ = exempt.wait();
        std::fs::remove_dir_all(&dir).ok();
    }
}

// --- healthz payload ----------------------------------------------------------

#[test]
fn healthz_payload_carries_the_reuse_guard_fields() {
    let store_src = repo_root().join("rust/golden/spike/search-store.kg");
    let dir = tempfile_dir("healthz");
    let db_path = dir.join("store.kg");
    std::fs::copy(&store_src, &db_path).expect("seed store");
    let loopback: std::net::IpAddr = "127.0.0.1".parse().expect("loopback ip");

    let state = HealthzState::new(db_path.to_string_lossy(), false);
    let payload: Value =
        serde_json::from_str(&state.payload(loopback).to_string()).expect("json payload");
    assert_eq!(payload["status"], "ok", "payload {payload}");
    assert_eq!(payload["store_reachable"], true);
    assert_eq!(payload["read_only"], false);
    assert_eq!(payload["db"], db_path.to_string_lossy().as_ref() as &str);
    assert_eq!(payload["impl"], "rust");
    assert!(
        payload["uptime_seconds"].as_f64().is_some_and(|s| s >= 0.0),
        "uptime_seconds must be a non-negative number: {payload}"
    );
    assert!(
        payload["degradations"].is_u64(),
        "degradations is a count: {payload}"
    );

    // Non-loopback peers learn the served store's file name, never its path.
    let remote: std::net::IpAddr = "192.0.2.9".parse().expect("remote ip");
    let payload: Value =
        serde_json::from_str(&state.payload(remote).to_string()).expect("json payload");
    assert_eq!(payload["db"], "store.kg");
    assert!(
        !payload["db"]
            .as_str()
            .unwrap()
            .contains(&*dir.to_string_lossy()),
        "the unauthenticated payload must not leak the store directory: {payload}"
    );

    // An unreachable served store degrades to unhealthy with the db path intact.
    let missing = HealthzState::new(dir.join("missing.kg").to_string_lossy(), true);
    let payload: Value =
        serde_json::from_str(&missing.payload(loopback).to_string()).expect("json payload");
    assert_eq!(payload["status"], "unhealthy");
    assert_eq!(payload["store_reachable"], false);
    assert_eq!(payload["read_only"], true);

    std::fs::remove_dir_all(&dir).ok();
}

// --- reuse probe: mcp_url + healthz over the wire -------------------------------

#[test]
fn mcp_url_is_the_http_mcp_path() {
    assert_eq!(
        lifecycle::mcp_url(9876, "127.0.0.1"),
        "http://127.0.0.1:9876/mcp"
    );
    assert_eq!(
        lifecycle::mcp_url(9000, "localhost"),
        "http://localhost:9000/mcp"
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn healthz_probe_reads_a_live_daemon() {
    let dir = tempfile_dir("probe");
    let db_path = dir.join("store.kg");
    std::fs::copy(
        repo_root().join("rust/golden/spike/search-store.kg"),
        &db_path,
    )
    .expect("seed store");
    let (addr, handle) = cairn_daemon::server::serve(db_path).await.expect("serve");
    let host = addr.ip().to_string();
    let port = addr.port();

    assert!(lifecycle::healthz_responds(port, &host));
    let payload = lifecycle::healthz_json(port, &host).expect("healthz json");
    assert_eq!(payload["impl"], "rust");
    assert_eq!(payload["status"], "ok");

    // No daemon on the port: the probe degrades to None/false, never an error.
    assert!(!lifecycle::healthz_responds(port + 1, &host));
    assert!(lifecycle::healthz_json(port + 1, &host).is_none());

    handle.stop();
}

#[test]
fn http_get_reads_a_chunked_response_over_a_socket() {
    fn response(headers: &str, body: &[u8]) -> Option<(u16, Vec<u8>)> {
        let mut raw = format!("HTTP/1.1 200 OK\r\n{headers}\r\n\r\n").into_bytes();
        raw.extend_from_slice(body);
        let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind");
        let port = listener.local_addr().expect("addr").port();
        let payload = raw.clone();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept");
            let mut buf = [0u8; 1024];
            let _ = std::io::Read::read(&mut stream, &mut buf);
            std::io::Write::write_all(&mut stream, &payload).expect("write");
        });
        let result = lifecycle::http_get("127.0.0.1", port, "/healthz", Duration::from_secs(5));
        server.join().expect("server thread");
        result
    }

    // Framing matrix lives in cairn-query http; here one chunked round trip
    // proves the daemon wrapper reads a response over a socket.
    assert_eq!(
        response("Transfer-Encoding: chunked", b"5\r\nhello\r\n0\r\n\r\n").expect("chunked"),
        (200, b"hello".to_vec())
    );
    assert!(lifecycle::http_get("127.0.0.1", 1, "/healthz", Duration::from_millis(300)).is_none());
}

// --- bind discovery -------------------------------------------------------------

#[test]
fn args_bind_extracts_the_serve_run_pair() {
    let args = [
        "cairn", "serve", "run", "--port", "9001", "--host", "0.0.0.0",
    ];
    assert_eq!(
        lifecycle::args_bind(&args),
        (Some(9001), Some("0.0.0.0".to_string()))
    );
    assert_eq!(
        lifecycle::args_bind(&["cairn", "serve", "run"]),
        (None, None)
    );
    assert_eq!(
        lifecycle::args_bind(&["--host", "localhost"]),
        (None, Some("localhost".to_string()))
    );
    // A non-numeric --port is not a bind pair.
    assert_eq!(lifecycle::args_bind(&["--port", "abc"]), (None, None));
}

#[test]
fn plist_program_arguments_round_trips_the_written_plist() {
    let plist = render_plist(
        "/opt/cairn/bin/cairn",
        9123,
        "127.0.0.1",
        Some("/ws & co"),
        Some("/store/.kg"),
        None,
        false,
        None,
    );
    let dir = tempfile_dir("plistargs");
    let path = dir.join("probe.plist");
    lifecycle::write_plist_file(&path, &plist).expect("write plist");
    assert_eq!(
        lifecycle::plist_program_arguments(&path).expect("args"),
        vec![
            "/opt/cairn/bin/cairn",
            "serve",
            "run",
            "--port",
            "9123",
            "--host",
            "127.0.0.1",
        ]
    );
    assert_eq!(
        lifecycle::plist_program_arguments(&dir.join("absent.plist")),
        None
    );
    std::fs::remove_dir_all(&dir).ok();
}
