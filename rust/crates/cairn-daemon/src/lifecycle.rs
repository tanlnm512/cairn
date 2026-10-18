//! Daemon lifecycle: the launchd plist template and stray-sweep semantics.

use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

/// LaunchAgent label of the shared daemon.
pub const LABEL: &str = "dev.cairn.mcp";
/// Retired SSE-label agent unloaded before the first load of the current label.
pub const LEGACY_LABEL: &str = "dev.cairn.sse";
pub const DEFAULT_PORT: u16 = 9876;
pub const DEFAULT_HOST: &str = "127.0.0.1";
const LAUNCHCTL_TIMEOUT: Duration = Duration::from_secs(15);
const PROCBE_TIMEOUT: Duration = Duration::from_secs(5);

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
    std::env::var("HOME")
        .ok()
        .filter(|h| !h.trim().is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/"))
}

// --- plist template ---------------------------------------------------------

/// Absolute path of the cairn executable to launch: CAIRN_BIN, then this
/// process when it is named `cairn`, then PATH lookup, then the ~/.local install.
pub fn cg_bin() -> String {
    let running = std::env::current_exe()
        .ok()
        .filter(|exe| exe.file_name().is_some_and(|n| n == "cairn"))
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
    let path = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path) {
        let cand = dir.join("cairn");
        if cand.is_file() {
            return Some(cand.to_string_lossy().into_owned());
        }
    }
    None
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
        .is_some_and(|n| n == "cairn");
    if !bin_is_cairn || tokens[1] != "serve" {
        return false;
    }
    !(tokens.len() > 2 && SERVE_LIFECYCLE_SUBCOMMANDS.contains(&tokens[2]))
}

/// Full command line of `pid`; None when unreadable (never identify-and-kill blindly).
pub fn pid_cmdline(pid: u32) -> Option<String> {
    #[cfg(target_os = "linux")]
    {
        let raw = std::fs::read(format!("/proc/{pid}/cmdline")).ok()?;
        if !raw.is_empty() {
            return Some(
                String::from_utf8_lossy(&raw)
                    .split('\0')
                    .filter(|t| !t.is_empty())
                    .collect::<Vec<_>>()
                    .join(" ")
                    .trim()
                    .to_string(),
            );
        }
    }
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
             hold the DB via lsof; not killing unverified `cairn serve` candidates"
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
    Command::new("kill")
        .args(["-s", signal, &pid.to_string()])
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

/// Liveness probe (signal 0): true while the pid exists and is signallable.
fn pid_alive(pid: u32) -> bool {
    Command::new("kill")
        .args(["-0", &pid.to_string()])
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
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
