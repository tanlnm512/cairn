//! Daemon lifecycle: launchd plist, stray sweep, healthz reuse probe, daemon start.

use std::collections::HashSet;
use std::io::{Read as _, Write as _};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use serde_json::Value;

/// LaunchAgent label of the shared daemon.
pub const LABEL: &str = "dev.cairn.mcp";
/// Retired SSE-label agent unloaded before the first load of the current label.
pub const LEGACY_LABEL: &str = "dev.cairn.sse";
pub const DEFAULT_PORT: u16 = 9876;
pub const DEFAULT_HOST: &str = "127.0.0.1";
/// Selects the implementation a daemon start launches (`rust` or `python`).
pub const DAEMON_IMPL_ENV: &str = "CAIRN_DAEMON_IMPL";
/// Path of the Rust daemon binary a rust-impl start execs.
pub const RUST_BIN_ENV: &str = "CAIRN_RUST_BIN";
const LAUNCHCTL_TIMEOUT: Duration = Duration::from_secs(15);
const PROCBE_TIMEOUT: Duration = Duration::from_secs(5);
const HEALTHZ_TIMEOUT: Duration = Duration::from_secs(2);
const START_LOCK_TIMEOUT: Duration = Duration::from_secs(120);
static LEGACY_UNLOADED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// `cairn serve <sub>` lifecycle subcommands: transient CLI invocations, never
/// servers -- the stray sweeper must not target them.
pub const SERVE_LIFECYCLE_SUBCOMMANDS: [&str; 4] = ["start", "stop", "status", "restart"];

pub fn is_macos() -> bool {
    cfg!(target_os = "macos")
}

pub fn agents_dir() -> PathBuf {
    home_dir().join("Library").join("LaunchAgents")
}

pub fn plist_path() -> PathBuf {
    agents_dir().join(format!("{LABEL}.plist"))
}

pub fn log_path() -> PathBuf {
    home_dir()
        .join("Library")
        .join("Logs")
        .join("cairn-mcp.log")
}

/// CAIRN_HOME for daemon state, resolved at call time (env override wins).
pub fn daemon_home() -> PathBuf {
    crate::auth::cairn_home()
}

pub fn daemon_log_path() -> PathBuf {
    if is_macos() {
        log_path()
    } else {
        daemon_home().join("cairn-mcp.log")
    }
}

pub fn pidfile_path() -> PathBuf {
    daemon_home().join("mcp-daemon.pid")
}

pub fn pidfile_pid() -> Option<u32> {
    let text = std::fs::read_to_string(pidfile_path()).ok()?;
    text.trim().parse::<u32>().ok()
}

fn home_dir() -> PathBuf {
    cairn_store::home::platform_home().unwrap_or_else(|| PathBuf::from("/"))
}

/// True when `name` is this engine's binary file name (`cairn`, or
/// `cairn.exe` on Windows).
pub fn is_cairn_binary_name(name: &std::ffi::OsStr) -> bool {
    if name == "cairn" {
        return true;
    }
    #[cfg(windows)]
    if name == "cairn.exe" {
        return true;
    }
    false
}

// --- plist template ---------------------------------------------------------

/// Absolute path of the cairn executable to launch: CAIRN_BIN, then this
/// process when it is named `cairn`, then PATH lookup, then the ~/.local install.
pub fn cg_bin() -> String {
    let running = std::env::current_exe()
        .ok()
        .filter(|exe| exe.file_name().is_some_and(is_cairn_binary_name))
        .map(|exe| exe.to_string_lossy().into_owned());
    cg_bin_from(
        std::env::var("CAIRN_BIN").ok().as_deref(),
        running.as_deref(),
        which_cairn().as_deref(),
        &home_dir(),
    )
}

/// Pure core of `cg_bin`: first existing candidate wins, else `"cairn"` for launchd's PATH.
pub fn cg_bin_from(
    cairn_bin_env: Option<&str>,
    running_bin: Option<&str>,
    which: Option<&str>,
    home: &Path,
) -> String {
    let local_install = home
        .join(".local")
        .join("bin")
        .join("cairn")
        .to_string_lossy()
        .into_owned();
    for cand in [
        cairn_bin_env,
        running_bin,
        which,
        Some(local_install.as_str()),
    ] {
        if let Some(c) = cand
            && Path::new(c).exists()
        {
            return c.to_string();
        }
    }
    "cairn".to_string()
}

fn which_cairn() -> Option<String> {
    cairn_store::exec::which("cairn").map(|p| p.to_string_lossy().into_owned())
}

/// The LaunchAgent plist for a read-write-by-default `cairn serve run` daemon.
pub struct LaunchdPlist {
    pub label: String,
    pub program_arguments: Vec<String>,
    pub environment_variables: Vec<(String, String)>,
    pub run_at_load: bool,
    pub keep_alive: bool,
    pub stdout_path: String,
    pub stderr_path: String,
}

/// Render the plist for `bin` (the exec'd cairn binary; pass the Rust binary
/// when impl=rust) serving the given store.
#[allow(clippy::too_many_arguments)]
pub fn render_plist(
    bin: &str,
    port: u16,
    host: &str,
    workspace: Option<&str>,
    db_path: Option<&str>,
    knowledge_path: Option<&str>,
    read_only: bool,
    api_key: Option<&str>,
) -> LaunchdPlist {
    let mut env: Vec<(String, String)> = vec![(
        "PATH".to_string(),
        std::env::var("PATH").unwrap_or_else(|_| "/usr/local/bin:/usr/bin:/bin".to_string()),
    )];
    // Propagate a non-default CAIRN_HOME so the daemon resolves config and
    // shared libs under the same store the invoking shell used.
    if let Ok(home) = std::env::var("CAIRN_HOME")
        && !home.trim().is_empty()
    {
        env.push(("CAIRN_HOME".to_string(), home));
    }
    for (key, value) in [
        ("CAIRN_WORKSPACE", workspace),
        ("CAIRN_DB", db_path),
        ("CAIRN_KNOWLEDGE", knowledge_path),
        // launchd does not inherit the shell env: without this a keyed
        // non-loopback daemon exits at the bind guard and crash-loops.
        ("CAIRN_MCP_API_KEY", api_key),
    ] {
        if let Some(v) = value {
            env.push((key.to_string(), v.to_string()));
        }
    }
    let mut args = vec![
        bin.to_string(),
        "serve".to_string(),
        "run".to_string(),
        "--port".to_string(),
        port.to_string(),
        "--host".to_string(),
        host.to_string(),
    ];
    if read_only {
        args.push("--read-only".to_string());
        env.push(("CAIRN_READ_ONLY".to_string(), "1".to_string()));
    }
    let log = log_path().to_string_lossy().into_owned();
    LaunchdPlist {
        label: LABEL.to_string(),
        program_arguments: args,
        environment_variables: env,
        run_at_load: true,
        keep_alive: true,
        stdout_path: log.clone(),
        stderr_path: log,
    }
}

fn xml_escape(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

/// Serialize the plist as launchd-loadable XML (string/bool/array/dict values only).
pub fn plist_xml(plist: &LaunchdPlist) -> String {
    let mut out = String::from(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n\
         <!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \
         \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n\
         <plist version=\"1.0\">\n<dict>\n",
    );
    let put_str = |out: &mut String, key: &str, value: &str| {
        out.push_str(&format!(
            "  <key>{key}</key>\n  <string>{}</string>\n",
            xml_escape(value)
        ));
    };
    put_str(&mut out, "Label", &plist.label);
    out.push_str("  <key>ProgramArguments</key>\n  <array>\n");
    for arg in &plist.program_arguments {
        out.push_str(&format!("    <string>{}</string>\n", xml_escape(arg)));
    }
    out.push_str("  </array>\n");
    out.push_str("  <key>EnvironmentVariables</key>\n  <dict>\n");
    for (key, value) in &plist.environment_variables {
        put_str(&mut out, &xml_escape(key), value);
    }
    out.push_str("  </dict>\n");
    out.push_str(&format!(
        "  <key>RunAtLoad</key>\n  <{}/>\n",
        if plist.run_at_load { "true" } else { "false" }
    ));
    out.push_str(&format!(
        "  <key>KeepAlive</key>\n  <{}/>\n",
        if plist.keep_alive { "true" } else { "false" }
    ));
    put_str(&mut out, "StandardOutPath", &plist.stdout_path);
    put_str(&mut out, "StandardErrorPath", &plist.stderr_path);
    out.push_str("</dict>\n</plist>\n");
    out
}

/// Write the rendered plist to `path`; returns the path written.
pub fn write_plist_file(path: &Path, plist: &LaunchdPlist) -> std::io::Result<PathBuf> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(path, plist_xml(plist))?;
    Ok(path.to_path_buf())
}

// --- stray sweep ------------------------------------------------------------

/// True when `cmdline` is a real `cairn serve` server invocation (anchored,
/// subcommands excluded); recognizes both a native binary and a shebang exec.
pub fn is_cairn_serve_cmdline(cmdline: &str) -> bool {
    let mut tokens: Vec<&str> = cmdline.split_whitespace().collect();
    if tokens.len() >= 3
        && Path::new(tokens[0])
            .file_name()
            .is_some_and(|n| n.to_string_lossy().starts_with("python"))
    {
        // Shebang exec: console scripts run as `python <path>/cairn serve ...`.
        tokens.remove(0);
    }
    if tokens.len() < 2 {
        return false;
    }
    let bin_is_cairn = Path::new(tokens[0])
        .file_name()
        .is_some_and(is_cairn_binary_name);
    if !bin_is_cairn || tokens[1] != "serve" {
        return false;
    }
    !(tokens.len() > 2 && SERVE_LIFECYCLE_SUBCOMMANDS.contains(&tokens[2]))
}

/// Full command line of `pid`; None when unreadable (never identify-and-kill blindly).
#[cfg(target_os = "linux")]
pub fn pid_cmdline(pid: u32) -> Option<String> {
    pid_cmdline_procfs(pid).or_else(|| pid_cmdline_ps(pid))
}

/// Full command line of `pid` via PowerShell CIM; None when unreadable.
#[cfg(windows)]
pub fn pid_cmdline(pid: u32) -> Option<String> {
    let script = format!("(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine");
    let output = run_with_timeout(
        Command::new("powershell").args(["-NoProfile", "-NonInteractive", "-Command", &script]),
        PROCBE_TIMEOUT,
    )?;
    if !output.status.success() {
        return None;
    }
    let line = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if line.is_empty() { None } else { Some(line) }
}

/// Full command line of `pid` via ps; None when unreadable.
#[cfg(not(any(target_os = "linux", windows)))]
pub fn pid_cmdline(pid: u32) -> Option<String> {
    pid_cmdline_ps(pid)
}

/// Whitespace-joined `/proc/<pid>/cmdline`; None when absent or empty.
#[cfg(target_os = "linux")]
fn pid_cmdline_procfs(pid: u32) -> Option<String> {
    let raw = std::fs::read(format!("/proc/{pid}/cmdline")).ok()?;
    if raw.is_empty() {
        return None;
    }
    Some(
        String::from_utf8_lossy(&raw)
            .split('\0')
            .filter(|t| !t.is_empty())
            .collect::<Vec<_>>()
            .join(" ")
            .trim()
            .to_string(),
    )
}

/// `ps -p <pid> -o command=` output; None when unreadable or empty.
#[cfg(not(windows))]
fn pid_cmdline_ps(pid: u32) -> Option<String> {
    let output = run_with_timeout(
        Command::new("ps").args(["-p", &pid.to_string(), "-o", "command="]),
        PROCBE_TIMEOUT,
    )?;
    if !output.status.success() {
        return None;
    }
    let line = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if line.is_empty() { None } else { Some(line) }
}

fn run_with_timeout(cmd: &mut Command, timeout: Duration) -> Option<std::process::Output> {
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
    let mut child = cmd.spawn().ok()?;
    let deadline = Instant::now() + timeout;
    loop {
        match child.try_wait() {
            Ok(Some(_)) => return child.wait_with_output().ok(),
            Ok(None) => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    return None;
                }
                std::thread::sleep(Duration::from_millis(10));
            }
            Err(_) => return None,
        }
    }
}

/// Pids whose cmdline mentions `cairn serve` — a deliberately over-matching superset.
#[cfg(windows)]
pub fn pgrep_candidates() -> Vec<u32> {
    let script = "Get-CimInstance Win32_Process -Filter \"CommandLine LIKE '%cairn serve%'\" \
                  | ForEach-Object { $_.ProcessId }";
    let Some(output) = run_with_timeout(
        Command::new("powershell").args(["-NoProfile", "-NonInteractive", "-Command", script]),
        PROCBE_TIMEOUT,
    ) else {
        return Vec::new();
    };
    if !output.status.success() {
        return Vec::new();
    }
    String::from_utf8_lossy(&output.stdout)
        .split_whitespace()
        .filter_map(|t| t.parse::<u32>().ok())
        .collect()
}

/// Pids whose cmdline mentions `cairn serve` — a deliberately over-matching superset.
#[cfg(not(windows))]
pub fn pgrep_candidates() -> Vec<u32> {
    let Some(output) = run_with_timeout(
        Command::new("pgrep").args(["-f", "cairn serve"]),
        PROCBE_TIMEOUT,
    ) else {
        return Vec::new();
    };
    if !output.status.success() {
        return Vec::new();
    }
    String::from_utf8_lossy(&output.stdout)
        .split_whitespace()
        .filter_map(|t| t.parse::<u32>().ok())
        .collect()
}

/// Pids holding `db_path` open; None means verification failed (never conflated with empty).
pub fn db_holder_pids(db_path: &Path) -> Option<HashSet<u32>> {
    #[cfg(windows)]
    {
        // No lsof equivalent: report unverified so the sweep never kills blind.
        let _ = db_path;
        None
    }
    #[cfg(not(windows))]
    {
        let output = run_with_timeout(
            Command::new("lsof").args(["-F", "p"]).arg(db_path),
            PROCBE_TIMEOUT,
        )?;
        // lsof exits 1 when the file is open by no one; stderr set means real failure.
        let code = output.status.code();
        let failed = match code {
            Some(0) => false,
            Some(1) => !String::from_utf8_lossy(&output.stderr).trim().is_empty(),
            _ => true,
        };
        if failed {
            return None;
        }
        let mut holders = HashSet::new();
        for line in String::from_utf8_lossy(&output.stdout).lines() {
            let line = line.trim();
            if let Some(pid) = line.strip_prefix('p')
                && let Ok(pid) = pid.parse::<u32>()
            {
                holders.insert(pid);
            }
        }
        Some(holders)
    }
}

/// Direct child pids of `ppid` (best-effort, empty on failure).
pub fn children_of(ppid: u32) -> HashSet<u32> {
    let Some(output) = run_with_timeout(
        Command::new("pgrep").args(["-P", &ppid.to_string()]),
        PROCBE_TIMEOUT,
    ) else {
        return HashSet::new();
    };
    if !output.status.success() {
        return HashSet::new();
    }
    String::from_utf8_lossy(&output.stdout)
        .split_whitespace()
        .filter_map(|t| t.parse::<u32>().ok())
        .collect()
}

fn launchctl(args: &[&str]) -> Option<std::process::Output> {
    if !is_macos() {
        return None;
    }
    let mut cmd = Command::new("launchctl");
    cmd.args(args);
    run_with_timeout(&mut cmd, LAUNCHCTL_TIMEOUT)
}

/// True if the LaunchAgent is currently loaded.
pub fn is_loaded() -> bool {
    launchctl(&["list", LABEL])
        .map(|o| o.status.success())
        .unwrap_or(false)
}

/// PID of the running daemon, or None; parses both launchctl output formats.
pub fn running_pid() -> Option<u32> {
    let output = launchctl(&["list", LABEL])?;
    if !output.status.success() {
        return None;
    }
    let stdout = String::from_utf8_lossy(&output.stdout);
    // Modern plist-style output: "PID" = 6155;
    for window in stdout.match_indices("\"PID\"") {
        let rest = &stdout[window.0..];
        if let Some(eq) = rest.find('=')
            && let Some(semi) = rest.find(';')
            && eq < semi
            && let Ok(pid) = rest[eq + 1..semi].trim().parse::<u32>()
        {
            return Some(pid);
        }
    }
    // Tab-separated output: PID<Tab>Status<Tab>Label.
    for line in stdout.lines() {
        let parts: Vec<&str> = line.split('\t').collect();
        if parts.len() >= 3
            && let Ok(pid) = parts[0].trim().parse::<u32>()
        {
            return Some(pid);
        }
    }
    None
}

/// Inputs to the stray sweep that callers with their own daemon bookkeeping override.
pub struct SweepCtx<'a> {
    pub db_path: &'a Path,
    /// Pid recorded in the daemon pidfile (None when absent/unparsable).
    pub pidfile_pid: Option<u32>,
    /// Pids to never target: this process, the launchd daemon and its children.
    pub protected: HashSet<u32>,
}

/// Find `cairn serve` pids not managed by launchd that hold this db; never
/// kills unverified pids (empty on lsof failure).
pub fn find_strays_ctx(ctx: &SweepCtx<'_>) -> Vec<u32> {
    let mut protected = ctx.protected.clone();
    if let Some(pf) = ctx.pidfile_pid
        && !protected.contains(&pf)
        && let Some(cmdline) = pid_cmdline(pf)
        && is_cairn_serve_cmdline(&cmdline)
    {
        protected.insert(pf);
    }

    let server_pids: Vec<u32> = pgrep_candidates()
        .into_iter()
        .filter(|pid| !protected.contains(pid))
        .filter(|pid| {
            pid_cmdline(*pid)
                .map(|c| is_cairn_serve_cmdline(&c))
                .unwrap_or(false)
        })
        .collect();
    if server_pids.is_empty() {
        return Vec::new();
    }
    let Some(holders) = db_holder_pids(ctx.db_path) else {
        eprintln!(
            "cairn: stray sweep skipped -- could not verify which processes \
             hold the DB; not killing unverified `cairn serve` candidates"
        );
        return Vec::new();
    };
    server_pids
        .into_iter()
        .filter(|pid| holders.contains(pid))
        .collect()
}

/// Find strays against the real environment (launchd pid, pidfile, own pid).
pub fn find_strays(db_path: &Path) -> Vec<u32> {
    let mut protected: HashSet<u32> = HashSet::new();
    protected.insert(std::process::id());
    if let Some(daemon_pid) = running_pid() {
        protected.insert(daemon_pid);
        protected.extend(children_of(daemon_pid));
    }
    find_strays_ctx(&SweepCtx {
        db_path,
        pidfile_pid: pidfile_pid(),
        protected,
    })
}

fn send_signal(pid: u32, signal: &str) -> bool {
    #[cfg(windows)]
    {
        // Windows has no signals: TERM is a best-effort close, KILL is force
        // with the child tree. A non-force kill of a console process reports
        // failure while the process lives, so only spawn failure is "not sent".
        let mut args: Vec<&str> = vec!["/T", "/PID"];
        if signal.eq_ignore_ascii_case("KILL") {
            args.insert(0, "/F");
        }
        Command::new("taskkill")
            .args(&args)
            .arg(pid.to_string())
            .status()
            .map(|_| true)
            .unwrap_or(false)
    }
    #[cfg(not(windows))]
    {
        Command::new("kill")
            .args(["-s", signal, &pid.to_string()])
            .status()
            .map(|s| s.success())
            .unwrap_or(false)
    }
}

/// Liveness probe (signal 0): true while the pid exists and is signallable.
fn pid_alive(pid: u32) -> bool {
    #[cfg(windows)]
    {
        let Some(output) = run_with_timeout(
            Command::new("tasklist").args(["/NH", "/FI", &format!("PID eq {pid}")]),
            PROCBE_TIMEOUT,
        ) else {
            return false;
        };
        tasklist_reports_pid(&String::from_utf8_lossy(&output.stdout), pid)
    }
    #[cfg(not(windows))]
    {
        Command::new("kill")
            .args(["-0", &pid.to_string()])
            .status()
            .map(|s| s.success())
            .unwrap_or(false)
    }
}

/// True when `tasklist /NH` output has a row whose image runs as `pid`.
#[cfg(windows)]
fn tasklist_reports_pid(stdout: &str, pid: u32) -> bool {
    let wanted = pid.to_string();
    stdout.lines().any(|line| {
        line.split_whitespace()
            .nth(1)
            .is_some_and(|field| field == wanted)
    })
}

/// SIGTERM, wait, then SIGKILL; `cmd_check` re-verification aborts on pid reuse.
pub fn terminate_pid(pid: u32, timeout: Duration, cmd_check: Option<&dyn Fn(&str) -> bool>) {
    let still_ours = || match cmd_check {
        None => true,
        Some(check) => pid_cmdline(pid).map(|c| check(&c)).unwrap_or(false),
    };
    if !still_ours() {
        return;
    }
    if !send_signal(pid, "TERM") {
        return; // gone or unsignallable, like the ProcessLookupError/PermissionError gate
    }
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if !pid_alive(pid) {
            return;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    if !still_ours() {
        return; // pid died + was reused during the TERM->KILL wait: abort
    }
    let _ = send_signal(pid, "KILL");
}

/// Find and kill stray `cairn serve` processes; returns the count killed.
pub fn sweep_strays_ctx(ctx: &SweepCtx<'_>, log: bool) -> usize {
    let strays = find_strays_ctx(ctx);
    for pid in &strays {
        terminate_pid(*pid, Duration::from_secs(5), Some(&is_cairn_serve_cmdline));
        if log {
            eprintln!("cairn: stray sweeper killed orphan cairn serve pid {pid}");
        }
    }
    strays.len()
}

/// Find and kill strays against the real environment; returns the count killed.
pub fn sweep_strays(db_path: &Path, log: bool) -> usize {
    let mut protected: HashSet<u32> = HashSet::new();
    protected.insert(std::process::id());
    if let Some(daemon_pid) = running_pid() {
        protected.insert(daemon_pid);
        protected.extend(children_of(daemon_pid));
    }
    sweep_strays_ctx(
        &SweepCtx {
            db_path,
            pidfile_pid: pidfile_pid(),
            protected,
        },
        log,
    )
}

// --- reuse probe: mcp_url + bounded HTTP GET /healthz ------------------------

pub fn mcp_url(port: u16, host: &str) -> String {
    format!("http://{host}:{port}/mcp")
}

fn xml_unescape(text: &str) -> String {
    text.replace("&quot;", "\"")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
}

/// Bounded HTTP/1.1 GET returning (status, body); None on any transport or
/// parse failure. Loopback-tool grade: no TLS, no redirects, no keep-alive.
pub fn http_get(host: &str, port: u16, path: &str, timeout: Duration) -> Option<(u16, Vec<u8>)> {
    use std::net::ToSocketAddrs as _;
    let addr = (host, port).to_socket_addrs().ok()?.next()?;
    let mut stream = TcpStream::connect_timeout(&addr, timeout).ok()?;
    stream.set_read_timeout(Some(timeout)).ok()?;
    stream.set_write_timeout(Some(timeout)).ok()?;
    let request =
        format!("GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n");
    stream.write_all(request.as_bytes()).ok()?;
    let mut raw = Vec::new();
    stream.read_to_end(&mut raw).ok()?;
    parse_http_response(&raw)
}

/// Split a head-of-line-blocked response into (status, body), decoding
/// content-length, chunked, and read-to-end framings.
fn parse_http_response(raw: &[u8]) -> Option<(u16, Vec<u8>)> {
    let header_end = raw.windows(4).position(|w| w == b"\r\n\r\n")?;
    let head = String::from_utf8_lossy(&raw[..header_end]);
    let mut lines = head.lines();
    let status = lines
        .next()?
        .split_whitespace()
        .nth(1)?
        .parse::<u16>()
        .ok()?;
    let mut content_length: Option<usize> = None;
    let mut chunked = false;
    for line in lines {
        let Some((name, value)) = line.split_once(':') else {
            continue;
        };
        let value = value.trim();
        if name.eq_ignore_ascii_case("content-length") {
            content_length = value.parse().ok();
        } else if name.eq_ignore_ascii_case("transfer-encoding")
            && value.eq_ignore_ascii_case("chunked")
        {
            chunked = true;
        }
    }
    let body = &raw[header_end + 4..];
    let body = if chunked {
        decode_chunked(body)?
    } else if let Some(len) = content_length {
        body.get(..len.min(body.len())).unwrap_or(body).to_vec()
    } else {
        body.to_vec()
    };
    Some((status, body))
}

fn decode_chunked(body: &[u8]) -> Option<Vec<u8>> {
    let mut out = Vec::new();
    let mut rest = body;
    loop {
        let line_end = rest.windows(2).position(|w| w == b"\r\n")?;
        let size_text = String::from_utf8_lossy(&rest[..line_end]);
        let size = usize::from_str_radix(size_text.split(';').next()?.trim(), 16).ok()?;
        rest = &rest[line_end + 2..];
        if size == 0 {
            return Some(out);
        }
        if rest.len() < size {
            return None;
        }
        out.extend_from_slice(&rest[..size]);
        rest = rest.get(size + 2..).unwrap_or(&[]);
    }
}

/// True when the daemon answers GET /healthz with 200 within `HEALTHZ_TIMEOUT`.
pub fn healthz_responds(port: u16, host: &str) -> bool {
    matches!(
        http_get(host, port, "/healthz", HEALTHZ_TIMEOUT),
        Some((200, _))
    )
}

/// Parsed /healthz payload, or None when the daemon does not answer 200.
pub fn healthz_json(port: u16, host: &str) -> Option<Value> {
    let (_, body) = http_get(host, port, "/healthz", HEALTHZ_TIMEOUT)?;
    serde_json::from_slice(&body).ok()
}

// --- impl selection -----------------------------------------------------------

/// Implementation a start launches: CAIRN_DAEMON_IMPL when set, else `rust`
/// when the Rust daemon binary is available, else `python` (the PyPI
/// launcher, which execs the same Rust engine).
pub fn daemon_impl() -> &'static str {
    let chosen = std::env::var(DAEMON_IMPL_ENV)
        .unwrap_or_default()
        .trim()
        .to_owned();
    if !chosen.is_empty() {
        return if chosen.eq_ignore_ascii_case("rust") {
            "rust"
        } else {
            "python"
        };
    }
    if rust_bin().is_some() {
        "rust"
    } else {
        "python"
    }
}

/// Rust daemon binary path: CAIRN_RUST_BIN, else this process when named
/// `cairn` (the Rust binary is itself the Rust daemon); None when neither.
pub fn rust_bin() -> Option<String> {
    if let Ok(cand) = std::env::var(RUST_BIN_ENV)
        && !cand.trim().is_empty()
        && Path::new(&cand).exists()
    {
        return Some(cand);
    }
    std::env::current_exe()
        .ok()
        .filter(|exe| exe.file_name().is_some_and(is_cairn_binary_name))
        .map(|exe| exe.to_string_lossy().into_owned())
}

/// Binary a daemon start execs: the Rust binary when impl=rust, else cg_bin().
pub fn daemon_bin() -> Result<String, String> {
    if daemon_impl() == "python" {
        return Ok(cg_bin());
    }
    rust_bin().ok_or_else(|| {
        format!(
            "{DAEMON_IMPL_ENV}=rust selected but no Rust daemon binary found; \
             set {RUST_BIN_ENV} to the Rust cairn binary."
        )
    })
}

// --- bind discovery -------------------------------------------------------------

/// Extract the (--port, --host) pair from a `serve run` argv; Nones when absent.
pub fn args_bind(args: &[&str]) -> (Option<u16>, Option<String>) {
    let mut port = None;
    let mut host = None;
    let mut iter = args.iter();
    while let Some(tok) = iter.next() {
        if *tok == "--port"
            && let Some(next) = iter.next()
            && let Ok(p) = next.parse::<u16>()
        {
            port = Some(p);
        } else if *tok == "--host"
            && let Some(next) = iter.next()
        {
            host = Some((*next).to_string());
        }
    }
    (port, host)
}

/// ProgramArguments strings of the LaunchAgent plist at `path`, when parseable.
pub fn plist_program_arguments(path: &Path) -> Option<Vec<String>> {
    let text = std::fs::read_to_string(path).ok()?;
    let start = text.find("<key>ProgramArguments</key>")? + "<key>ProgramArguments</key>".len();
    let rest = &text[start..];
    let array = rest.get(..rest.find("</array>")?)?;
    let mut args = Vec::new();
    let mut search = array;
    while let Some(open) = search.find("<string>") {
        let after = &search[open + "<string>".len()..];
        let close = after.find("</string>")?;
        args.push(xml_unescape(&after[..close]));
        search = &after[close + "</string>".len()..];
    }
    Some(args)
}

/// (port, host) the managed daemon was started with, from its plist or argv;
/// Nones when undiscoverable.
pub fn daemon_bind() -> (Option<u16>, Option<String>) {
    let args: Vec<String> = if is_macos() {
        plist_program_arguments(&plist_path()).unwrap_or_default()
    } else {
        match pidfile_pid().and_then(pid_cmdline) {
            Some(cmdline) => cmdline.split_whitespace().map(String::from).collect(),
            None => Vec::new(),
        }
    };
    let refs: Vec<&str> = args.iter().map(String::as_str).collect();
    args_bind(&refs)
}

// --- launchd load / unload --------------------------------------------------------

fn unload_legacy_label_once() {
    if LEGACY_UNLOADED.swap(true, std::sync::atomic::Ordering::Relaxed) {
        return;
    }
    let legacy_plist = agents_dir().join(format!("{LEGACY_LABEL}.plist"));
    if !legacy_plist.exists() {
        return;
    }
    let _ = launchctl(&["unload", &legacy_plist.to_string_lossy()]);
    // Without this, launchd re-launches the retired agent at every login.
    let _ = std::fs::remove_file(&legacy_plist);
}

/// Load (and start) the LaunchAgent; Err carries the failure reason.
pub fn load() -> Result<(), String> {
    if !is_macos() {
        return Err("LaunchAgent daemon management is macOS-only.".to_string());
    }
    let path = plist_path();
    if !path.exists() {
        return Err(format!(
            "{} not found. Run `cairn serve start` first to create it.",
            path.display()
        ));
    }
    unload_legacy_label_once();
    match launchctl(&["load", &path.to_string_lossy()]) {
        None => Err(format!(
            "launchctl load timed out after {}s",
            LAUNCHCTL_TIMEOUT.as_secs()
        )),
        Some(output) if output.status.success() => Ok(()),
        Some(_) => Err("launchctl load failed".to_string()),
    }
}

/// Unload (and stop) the LaunchAgent; success includes already-unloaded.
pub fn unload() -> Result<(), String> {
    if !is_macos() {
        return Err("LaunchAgent daemon management is macOS-only.".to_string());
    }
    let path = plist_path();
    if !path.exists() {
        return Ok(()); // nothing to unload
    }
    match launchctl(&["unload", &path.to_string_lossy()]) {
        // unload returns nonzero if already unloaded; treat as success.
        None => Err(format!(
            "launchctl unload timed out after {}s",
            LAUNCHCTL_TIMEOUT.as_secs()
        )),
        Some(_) => Ok(()),
    }
}

// --- daemon start -------------------------------------------------------------------

/// Store wiring a daemon start needs for the plist/env it launches with.
pub struct StoreBinding {
    pub workspace: PathBuf,
    pub db: PathBuf,
    pub knowledge: PathBuf,
}

/// Bounded exclusive flock over `path`; Err carries the timeout/failure text.
pub fn lockfile_guard(
    path: &Path,
    timeout: Duration,
    poll: Duration,
) -> Result<std::fs::File, String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let file = std::fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .read(true)
        .open(path)
        .map_err(|e| e.to_string())?;
    let deadline = Instant::now() + timeout;
    loop {
        match file.try_lock() {
            Ok(()) => return Ok(file),
            Err(std::fs::TryLockError::WouldBlock) => {
                if Instant::now() >= deadline {
                    return Err(format!(
                        "timed out after {}s waiting for the daemon start lock ({})",
                        timeout.as_secs(),
                        path.display()
                    ));
                }
                std::thread::sleep(poll);
            }
            Err(error) => return Err(error.to_string()),
        }
    }
}

fn wait_for_healthz(port: u16, host: &str) -> bool {
    for _ in 0..60 {
        if healthz_responds(port, host) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    false
}

fn write_pidfile(pid: u32) {
    let path = pidfile_path();
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let _ = std::fs::write(&path, format!("{pid}\n"));
}

/// Kill a verified pidfile daemon that holds this db but is not serving healthz.
fn cycle_wedged_daemon(port: u16, host: &str, db_path: &Path) {
    let Some(pf_pid) = pidfile_pid() else {
        return;
    };
    if healthz_responds(port, host) {
        return;
    }
    let Some(cmdline) = pid_cmdline(pf_pid) else {
        return;
    };
    if !is_cairn_serve_cmdline(&cmdline) {
        return;
    }
    let Some(holders) = db_holder_pids(db_path) else {
        return;
    };
    if !holders.contains(&pf_pid) {
        return;
    }
    terminate_pid(
        pf_pid,
        Duration::from_secs(5),
        Some(&is_cairn_serve_cmdline),
    );
    let _ = std::fs::remove_file(pidfile_path());
}

/// Launch a detached `serve run` child (the non-macOS daemonize fallback).
#[allow(clippy::too_many_arguments)]
fn spawn_daemonized(
    bin: &str,
    port: u16,
    host: &str,
    store: &StoreBinding,
    api_key: Option<&str>,
) -> std::io::Result<u32> {
    let log_path = daemon_log_path();
    if let Some(parent) = log_path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let log = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)?;
    let log_err = log.try_clone()?;
    let mut cmd = Command::new(bin);
    cmd.args(["serve", "run", "--port", &port.to_string(), "--host", host])
        .stdin(Stdio::null())
        .stdout(Stdio::from(log))
        .stderr(Stdio::from(log_err))
        .env("CAIRN_WORKSPACE", &store.workspace)
        .env("CAIRN_DB", &store.db)
        .env("CAIRN_KNOWLEDGE", &store.knowledge);
    if let Some(key) = api_key {
        cmd.env("CAIRN_MCP_API_KEY", key);
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt as _;
        cmd.process_group(0);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt as _;
        const DETACHED_PROCESS: u32 = 0x0000_0008;
        const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
        cmd.creation_flags(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP);
    }
    Ok(cmd.spawn()?.id())
}

fn ensure_running_launchd(
    port: u16,
    host: &str,
    api_key: Option<&str>,
    store: &StoreBinding,
) -> Result<(Option<u32>, String, bool), String> {
    if is_loaded() {
        if healthz_responds(port, host) {
            return Ok((running_pid(), mcp_url(port, host), false));
        }
        // KeepAlive respawn window: the job is loaded but not yet serving --
        // wait it out before any write/load attempt.
        if wait_for_healthz(port, host) {
            return Ok((running_pid(), mcp_url(port, host), false));
        }
        unload()?; // wedged job: cycle it before a fresh load
    }
    sweep_strays(&store.db, true);
    let plist = render_plist(
        &daemon_bin()?,
        port,
        host,
        Some(&store.workspace.to_string_lossy()),
        Some(&store.db.to_string_lossy()),
        Some(&store.knowledge.to_string_lossy()),
        false,
        api_key,
    );
    write_plist_file(&plist_path(), &plist).map_err(|e| e.to_string())?;
    load()?;
    if !wait_for_healthz(port, host) {
        return Err("daemon loaded but healthz not responding".to_string());
    }
    Ok((running_pid(), mcp_url(port, host), true))
}

fn ensure_running_daemonize(
    port: u16,
    host: &str,
    api_key: Option<&str>,
    store: &StoreBinding,
) -> Result<(Option<u32>, String, bool), String> {
    if healthz_responds(port, host) {
        return Ok((pidfile_pid(), mcp_url(port, host), false));
    }
    cycle_wedged_daemon(port, host, &store.db);
    sweep_strays(&store.db, true);
    let pid = spawn_daemonized(&daemon_bin()?, port, host, store, api_key)
        .map_err(|e| format!("failed to start the daemon: {e}"))?;
    write_pidfile(pid);
    if !wait_for_healthz(port, host) {
        return Err("daemon started but healthz not responding".to_string());
    }
    Ok((Some(pid), mcp_url(port, host), true))
}

/// Bring up the shared HTTP daemon; Ok carries (pid, mcp_url, started).
pub fn ensure_daemon_running(
    port: u16,
    host: &str,
    api_key: Option<&str>,
    store: &StoreBinding,
) -> Result<(Option<u32>, String, bool), String> {
    let _lock = lockfile_guard(
        &daemon_home().join("mcp-daemon.lock"),
        START_LOCK_TIMEOUT,
        Duration::from_millis(50),
    )?;
    if is_macos() {
        ensure_running_launchd(port, host, api_key, store)
    } else {
        ensure_running_daemonize(port, host, api_key, store)
    }
}

/// DB-lock holder identities as `command(pid)` strings, sorted; empty when lsof is unavailable.
pub fn db_holder_identities(db_path: &Path) -> Vec<String> {
    let Some(output) = run_with_timeout(Command::new("lsof").arg(db_path), PROCBE_TIMEOUT) else {
        return Vec::new();
    };
    if !output.status.success() {
        return Vec::new();
    }
    let mut holders: Vec<String> = String::from_utf8_lossy(&output.stdout)
        .lines()
        .skip(1)
        .filter_map(|line| {
            let mut cols = line.split_whitespace();
            let command = cols.next()?;
            let pid = cols.next()?;
            Some(format!("{command}({pid})"))
        })
        .collect();
    holders.sort();
    holders.dedup();
    holders
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[cfg(windows)]
    fn tasklist_output_reports_only_exact_pid_rows() {
        let stdout = "cairn.exe                  1234 Console                    1     4,096 K\r\n\
                      INFO: No tasks are running which match the specified criteria.\r\n";
        assert!(tasklist_reports_pid(stdout, 1234));
        assert!(
            !tasklist_reports_pid(stdout, 123),
            "prefix pid must not match"
        );
        assert!(!tasklist_reports_pid(stdout, 12345));
        assert!(!tasklist_reports_pid(
            "INFO: No tasks are running.\r\n",
            1234
        ));
    }

    #[test]
    fn cairn_binary_names_match_per_platform() {
        assert!(is_cairn_binary_name(std::ffi::OsStr::new("cairn")));
        assert!(!is_cairn_binary_name(std::ffi::OsStr::new("cairn-agent")));
        if cfg!(windows) {
            assert!(is_cairn_binary_name(std::ffi::OsStr::new("cairn.exe")));
        } else {
            assert!(!is_cairn_binary_name(std::ffi::OsStr::new("cairn.exe")));
        }
    }
}
