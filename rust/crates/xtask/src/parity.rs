//! `cargo xtask parity`: replay the frozen MCP goldens (over the pinned stores) against the Rust daemon.

use std::collections::BTreeMap;
use std::ffi::OsString;
use std::fs;
use std::hash::{BuildHasher, Hasher, RandomState};
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitCode, Stdio};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread;
use std::time::{Duration, Instant};

use cairn_query::textfmt::title_bool;
use clap::Parser;
use regex::Regex;
use serde_json::{Map, Value, json};

pub(crate) use crate::golden_record::GoldenClient as RustClient;
use crate::golden_record::{self as gr, ScrubRule};

pub(crate) const DB_NAME: &str = "store.kg";
const SERVE_EXAMPLE: &str = "serve_search";
const HEALTHZ_TIMEOUT: Duration = Duration::from_secs(180);
const DIFF_PRINT_CHARS: usize = 300;
const STORES: [&str; 3] = ["self", "yarl", "attrs"];
/// Cross-build tie order is volatile for these tools; rank parity is owned by the search/nav/flow goldens.
const SORT_LINES_TOOLS: [&str; 3] = ["search_symbols", "find_definition", "path"];

/// Repository root (the directory holding `rust/`).
pub(crate) fn repo_root() -> PathBuf {
    let mut root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    for _ in 0..3 {
        root.pop();
    }
    root
}

pub(crate) fn rust_workspace() -> PathBuf {
    repo_root().join("rust")
}

/// Cargo target dir; `CARGO_TARGET_DIR` isolates container builds from host artifacts.
pub(crate) fn target_dir() -> PathBuf {
    std::env::var_os("CARGO_TARGET_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| rust_workspace().join("target"))
}

/// Fail a replay test whose release artifact is absent, unless the caller
/// opted out (CI runs the real replays in rust-parity.yml).
#[cfg(test)]
pub(crate) fn require_release_artifact(artifact: &Path) {
    const ALLOW_MISSING: &str = "CAIRN_ALLOW_MISSING_GOLDENS";
    if std::env::var_os(ALLOW_MISSING).is_some() {
        eprintln!("skipped: {} absent", artifact.display());
        return;
    }
    panic!(
        "release artifact {} absent; build with \
         `cargo build --release -p cairn-cli --examples` or set {ALLOW_MISSING}=1",
        artifact.display()
    );
}

fn home_dir() -> PathBuf {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_default()
}

pub(crate) fn cargo_bin() -> PathBuf {
    home_dir().join(".cargo").join("bin")
}

pub(crate) fn default_out_dir() -> PathBuf {
    repo_root().join("rust").join("golden").join("mcp")
}

/// Checked-in store fixtures (corpus, built store, seeded bundle, manifest) served byte-identically.
pub(crate) fn pinned_stores() -> PathBuf {
    repo_root().join("rust").join("golden").join("stores")
}

/// Why a harness stops early: an exit code already reported, or a message for stderr (exit 1).
pub(crate) enum Halt {
    Exit(u8),
    Error(String),
}

impl From<String> for Halt {
    fn from(msg: String) -> Self {
        Self::Error(msg)
    }
}

impl From<&str> for Halt {
    fn from(msg: &str) -> Self {
        Self::Error(msg.to_string())
    }
}

/// Map a harness result to the process exit code.
pub(crate) fn exit_code(result: Result<u8, Halt>) -> ExitCode {
    match result {
        Ok(code) | Err(Halt::Exit(code)) => ExitCode::from(code),
        Err(Halt::Error(msg)) => {
            eprintln!("{msg}");
            ExitCode::FAILURE
        }
    }
}

fn note(msg: &str) {
    eprintln!("[parity] {msg}");
}

/// Hex string of `bytes` OS-seeded random bytes.
pub(crate) fn random_hex(bytes: usize) -> String {
    let mut out = String::new();
    while out.len() < bytes * 2 {
        let mut hasher = RandomState::new().build_hasher();
        hasher.write_usize(out.len());
        out.push_str(&format!("{:016x}", hasher.finish()));
    }
    out.truncate(bytes * 2);
    out
}

/// Fresh private directory `<tmp>/<prefix><random>`.
pub(crate) fn make_scratch(prefix: &str) -> Result<PathBuf, Halt> {
    #[cfg_attr(not(unix), allow(unused_mut))]
    let mut builder = fs::DirBuilder::new();
    #[cfg(unix)]
    std::os::unix::fs::DirBuilderExt::mode(&mut builder, 0o700);
    loop {
        let path = std::env::temp_dir().join(format!("{prefix}{}", random_hex(4)));
        match builder.create(&path) {
            Ok(()) => return Ok(path),
            Err(err) if err.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(err) => {
                return Err(format!("cannot create scratch {}: {err}", path.display()).into());
            }
        }
    }
}

// --- store wiring -------------------------------------------------------------

/// `shutil.copy2`: contents, permissions and timestamps.
pub(crate) fn copy_file(src: &Path, dst: &Path) -> Result<(), String> {
    let fail = |err: std::io::Error| format!("copy {} -> {}: {err}", src.display(), dst.display());
    fs::copy(src, dst).map_err(fail)?;
    let meta = fs::metadata(src).map_err(fail)?;
    let mut times = fs::FileTimes::new();
    if let Ok(modified) = meta.modified() {
        times = times.set_modified(modified);
    }
    if let Ok(accessed) = meta.accessed() {
        times = times.set_accessed(accessed);
    }
    fs::File::options()
        .write(true)
        .open(dst)
        .and_then(|file| file.set_times(times))
        .map_err(fail)
}

/// `shutil.copytree`: recursive copy into a new directory, symlinks followed.
pub(crate) fn copy_tree(src: &Path, dst: &Path) -> Result<(), String> {
    fs::create_dir_all(dst).map_err(|err| format!("mkdir {}: {err}", dst.display()))?;
    let entries = fs::read_dir(src).map_err(|err| format!("read {}: {err}", src.display()))?;
    for entry in entries {
        let entry = entry.map_err(|err| format!("read {}: {err}", src.display()))?;
        let (from, to) = (entry.path(), dst.join(entry.file_name()));
        if from.is_dir() {
            copy_tree(&from, &to)?;
        } else {
            copy_file(&from, &to)?;
        }
    }
    Ok(())
}

/// Materialize the checked-in pinned store bytes into `scratch`; returns the served workspace.
pub(crate) fn load_pinned_store(store: &str, scratch: &Path) -> Result<PathBuf, Halt> {
    let pinned = pinned_stores().join(store);
    if !pinned.join(DB_NAME).is_file() {
        return Err(format!(
            "missing pinned store {}; the goldens are frozen — restore them from history",
            pinned.join(DB_NAME).display()
        )
        .into());
    }
    let home = scratch.join("home");
    fs::create_dir_all(&home).map_err(|err| format!("mkdir {}: {err}", home.display()))?;
    copy_file(&pinned.join(DB_NAME), &scratch.join(DB_NAME))?;
    copy_tree(&pinned.join("bundle"), &scratch.join(".knowledge"))?;
    copy_tree(&pinned.join("corpus"), &scratch.join("corpus"))?;
    for suffix in ["-wal", "-shm", "-journal"] {
        let _ = fs::remove_file(scratch.join(format!("{DB_NAME}{suffix}")));
    }
    Ok(scratch.join("corpus"))
}

/// Refuse (exit 2) when a served path resolves inside the live store.
pub(crate) fn refuse_live_store(db: &Path, knowledge: &Path) -> Result<(), Halt> {
    let home = std::env::var_os("CAIRN_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| home_dir().join(".cairn"));
    let mut live_paths = vec![gr::realpath(&home)];
    for var in ["CAIRN_DB", "CAIRN_KNOWLEDGE"] {
        if let Some(path) = std::env::var_os(var).filter(|path| !path.is_empty()) {
            live_paths.push(gr::realpath(Path::new(&path)));
        }
    }
    for served in [db, knowledge] {
        let real = gr::realpath(served);
        if let Some(live) = live_paths.iter().find(|live| real.starts_with(live)) {
            let msg = format!(
                "REFUSING replay: the served path {} resolves inside the live store {}; \
                 replay must never touch it",
                served.display(),
                live.display()
            );
            println!("{msg}");
            note(&msg);
            return Err(Halt::Exit(2));
        }
    }
    Ok(())
}

// --- daemon -------------------------------------------------------------------

pub(crate) fn path_with_cargo_bin() -> OsString {
    let current = std::env::var_os("PATH").unwrap_or_default();
    let paths = std::iter::once(cargo_bin()).chain(std::env::split_paths(&current));
    std::env::join_paths(paths).unwrap_or(current)
}

/// Child env for the daemon: pins, scratch store wiring, keyed, cargo bin on PATH.
pub(crate) fn daemon_env(
    scratch: &Path,
    workspace: &Path,
    api_key: &str,
) -> BTreeMap<OsString, OsString> {
    let mut env: BTreeMap<OsString, OsString> = std::env::vars_os().collect();
    for key in [
        "CAIRN_READ_ONLY",
        "CAIRN_MCP_API_KEY",
        "PYTEST_CURRENT_TEST",
        "CAIRN_DB",
        "CAIRN_KNOWLEDGE",
        "CAIRN_WORKSPACE",
        "CAIRN_HOME",
    ] {
        env.remove(&OsString::from(key));
    }
    let mut set = |key: &str, value: OsString| env.insert(key.into(), value);
    for (key, value) in gr::ENV_PINS {
        set(key, value.into());
    }
    set("CAIRN_HOME", scratch.join("home").into());
    set("CAIRN_DB", scratch.join(DB_NAME).into());
    set("CAIRN_KNOWLEDGE", scratch.join(".knowledge").into());
    set("CAIRN_WORKSPACE", workspace.into());
    set("CAIRN_MCP_API_KEY", api_key.into());
    set("PATH", path_with_cargo_bin());
    env
}

/// Keyed `serve_search` example child serving the ported tool surface; stopped on drop.
pub(crate) struct RustDaemon {
    child: Child,
    output: Arc<Mutex<String>>,
    port: Arc<OnceLock<u16>>,
}

impl RustDaemon {
    pub(crate) fn spawn(
        exe: &Path,
        db: &Path,
        workspace: &Path,
        env: &BTreeMap<OsString, OsString>,
    ) -> Result<Self, Halt> {
        let fail = |err: std::io::Error| format!("cannot start {}: {err}", exe.display());
        let (reader, writer) = std::io::pipe().map_err(fail)?;
        let child = Command::new(exe)
            .arg(db)
            .current_dir(workspace)
            .env_clear()
            .envs(env)
            .stdin(Stdio::null())
            .stdout(writer.try_clone().map_err(fail)?)
            .stderr(writer)
            .spawn()
            .map_err(fail)?;
        let output = Arc::new(Mutex::new(String::new()));
        let port = Arc::new(OnceLock::new());
        let (sink, found) = (Arc::clone(&output), Arc::clone(&port));
        thread::spawn(move || {
            let addr = Regex::new(r"http://127\.0\.0\.1:(\d+)/mcp").ok();
            let mut reader = BufReader::new(reader);
            let mut buf = Vec::new();
            while reader.read_until(b'\n', &mut buf).is_ok_and(|n| n > 0) {
                let line = String::from_utf8_lossy(&buf);
                if let Some(port) = addr
                    .as_ref()
                    .and_then(|re| re.captures(&line))
                    .and_then(|caps| caps[1].parse().ok())
                {
                    let _ = found.set(port);
                }
                if let Ok(mut out) = sink.lock() {
                    out.push_str(&line);
                }
                buf.clear();
            }
        });
        Ok(Self {
            child,
            output,
            port,
        })
    }

    pub(crate) fn port(&self) -> Option<u16> {
        self.port.get().copied()
    }

    pub(crate) fn url(&self) -> Option<String> {
        self.port()
            .map(|port| format!("http://127.0.0.1:{port}/mcp"))
    }

    /// True once `/healthz` answers 200; false when the child exits or the deadline passes.
    pub(crate) fn wait_ready(&mut self) -> bool {
        let deadline = Instant::now() + HEALTHZ_TIMEOUT;
        while Instant::now() < deadline {
            if !matches!(self.child.try_wait(), Ok(None)) {
                return false;
            }
            if self.port().is_some_and(healthz_responds) {
                return true;
            }
            thread::sleep(Duration::from_millis(250));
        }
        false
    }

    /// Tail of the daemon's combined stdout/stderr.
    pub(crate) fn diagnostics(&self) -> String {
        let out = self
            .output
            .lock()
            .map(|out| out.clone())
            .unwrap_or_default();
        let chars: Vec<char> = out.chars().collect();
        chars[chars.len().saturating_sub(2000)..].iter().collect()
    }

    fn stop(&mut self) {
        if !matches!(self.child.try_wait(), Ok(None)) {
            return;
        }
        let terminated = cfg!(unix)
            && Command::new("kill")
                .args(["-TERM", &self.child.id().to_string()])
                .status()
                .is_ok_and(|status| status.success());
        if !terminated {
            let _ = self.child.kill();
        }
        let deadline = Instant::now() + Duration::from_secs(10);
        while Instant::now() < deadline {
            if !matches!(self.child.try_wait(), Ok(None)) {
                return;
            }
            thread::sleep(Duration::from_millis(50));
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl Drop for RustDaemon {
    fn drop(&mut self) {
        self.stop();
    }
}

/// True when the daemon answers `GET /healthz` with 200 within two seconds.
pub(crate) fn healthz_responds(port: u16) -> bool {
    let addr = format!("127.0.0.1:{port}");
    gr::http_connect(&addr, Duration::from_secs(2))
        .and_then(|mut conn| {
            let head = format!("GET /healthz HTTP/1.1\r\nHost: {addr}\r\nConnection: close\r\n");
            gr::http_exchange(&mut conn, &head, b"")
        })
        .is_ok_and(|resp| resp.status == 200)
}

/// Build (cargo, incremental) and return the `serve_search` example binary.
pub(crate) fn ensure_serve_binary() -> Result<PathBuf, Halt> {
    let status = Command::new("cargo")
        .args([
            "build",
            "--release",
            "-p",
            "cairn-daemon",
            "--example",
            SERVE_EXAMPLE,
        ])
        .current_dir(rust_workspace())
        .env("PATH", path_with_cargo_bin())
        .status()
        .map_err(|err| format!("cannot run cargo: {err}"))?;
    if !status.success() {
        return Err(format!("cargo build of the {SERVE_EXAMPLE} example failed: {status}").into());
    }
    Ok(target_dir()
        .join("release")
        .join("examples")
        .join(SERVE_EXAMPLE))
}

// --- fixture replay script ------------------------------------------------------

/// Setup probe whose `id_regex` group 1 becomes the entry's `doc_id`.
pub(crate) struct Setup {
    pub(crate) tool: String,
    pub(crate) args: Value,
    pub(crate) id_regex: String,
}

/// One replayed tool call read from a fixture `replay` block.
pub(crate) struct Entry {
    pub(crate) tool: String,
    pub(crate) args: Map<String, Value>,
    pub(crate) seq: i64,
    pub(crate) sort_lines: bool,
    pub(crate) mask_list_lines: bool,
    pub(crate) setup: Option<Setup>,
}

/// Substitute scrub placeholders with this run's paths (inverse of capture).
fn denormalize_obj(obj: &Value, subs: &[(&str, String); 3]) -> Value {
    match obj {
        Value::String(text) => {
            Value::String(subs.iter().fold(text.clone(), |acc, (placeholder, value)| {
                acc.replace(placeholder, value)
            }))
        }
        Value::Array(items) => Value::Array(
            items
                .iter()
                .map(|item| denormalize_obj(item, subs))
                .collect(),
        ),
        Value::Object(map) => Value::Object(
            map.iter()
                .map(|(key, value)| (key.clone(), denormalize_obj(value, subs)))
                .collect(),
        ),
        other => other.clone(),
    }
}

fn object_or_empty(value: Option<&Value>) -> Map<String, Value> {
    value
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default()
}

fn fixture_paths(out_dir: &Path) -> Vec<PathBuf> {
    let mut paths: Vec<PathBuf> = fs::read_dir(out_dir)
        .map(|entries| {
            entries
                .filter_map(Result::ok)
                .map(|entry| entry.path())
                .filter(|path| {
                    path.file_name()
                        .is_some_and(|name| name.to_string_lossy().ends_with(".json"))
                })
                .collect()
        })
        .unwrap_or_default();
    paths.sort();
    paths
}

fn read_json(path: &Path) -> Result<Value, Halt> {
    let text = fs::read_to_string(path).map_err(|err| format!("read {}: {err}", path.display()))?;
    serde_json::from_str(&text).map_err(|err| format!("parse {}: {err}", path.display()).into())
}

/// The replay script read from fixture `replay` blocks (never store-probed), ordered by `seq`.
pub(crate) fn entries_from_fixtures(
    out_dir: &Path,
    scratch: &Path,
    workspace: &Path,
) -> Result<Vec<Entry>, Halt> {
    let paths = fixture_paths(out_dir);
    if paths.is_empty() {
        return Err(format!("no fixtures under {}", out_dir.display()).into());
    }
    let subs = [
        ("<STORE_HOME>", scratch.to_string_lossy().into_owned()),
        (
            "<CAIRN_HOME>",
            scratch.join("home").to_string_lossy().into_owned(),
        ),
        ("<WORKSPACE>", workspace.to_string_lossy().into_owned()),
    ];
    let mut entries = Vec::new();
    for path in paths {
        let fixture = read_json(&path)?;
        let name = path.file_name().unwrap_or_default().to_string_lossy();
        let Some(block) = fixture.get("replay") else {
            return Err(format!(
                "{name}: no replay block; fixtures predate the freeze — restore the frozen fixtures from history"
            )
            .into());
        };
        let flags = object_or_empty(fixture.get("normalization"));
        let flag = |key: &str| gr::is_truthy(flags.get(key));
        let setup = match block
            .get("setup")
            .filter(|setup| gr::is_truthy(Some(setup)))
        {
            Some(setup) => Some(Setup {
                tool: setup["tool"]
                    .as_str()
                    .ok_or(format!("{name}: setup without tool"))?
                    .to_string(),
                args: denormalize_obj(&Value::Object(object_or_empty(setup.get("args"))), &subs),
                id_regex: setup["id_regex"]
                    .as_str()
                    .ok_or(format!("{name}: setup without id_regex"))?
                    .to_string(),
            }),
            None => None,
        };
        let args = denormalize_obj(&Value::Object(object_or_empty(block.get("args"))), &subs);
        entries.push(Entry {
            tool: fixture["tool"]
                .as_str()
                .ok_or(format!("{name}: no tool"))?
                .to_string(),
            args: args.as_object().cloned().unwrap_or_default(),
            seq: block.get("seq").and_then(Value::as_i64).unwrap_or(0),
            sort_lines: flag("sort_lines"),
            mask_list_lines: flag("mask_list_lines"),
            setup,
        });
    }
    entries.sort_by_key(|entry| entry.seq);
    Ok(entries)
}

fn apply_volatility(entries: &mut [Entry]) {
    for entry in entries {
        if SORT_LINES_TOOLS.contains(&entry.tool.as_str()) {
            entry.sort_lines = true;
        }
    }
}

// --- replay + verify --------------------------------------------------------------

struct Capture {
    args: Map<String, Value>,
    response: Value,
}

/// The setup probe's concept id, or why it failed (replayed as the tool's error response).
fn run_setup(client: &mut RustClient, setup: &Setup) -> Result<Result<String, String>, Halt> {
    let (is_error, text) = client.call(&setup.tool, &setup.args)?;
    if is_error {
        return Ok(Err(format!("setup {} failed: {text}", setup.tool)));
    }
    let id_regex = Regex::new(&setup.id_regex)
        .map_err(|err| format!("bad id_regex {:?}: {err}", setup.id_regex))?;
    Ok(
        match id_regex.captures(&text).and_then(|caps| caps.get(1)) {
            Some(id) => Ok(id.as_str().to_string()),
            None => Err(format!(
                "setup {} returned no concept id: {}",
                setup.tool,
                gr::repr_str(&text)
            )),
        },
    )
}

/// Re-run the script; a failed setup probe becomes that tool's error response, not an abort.
fn replay_entries(
    client: &mut RustClient,
    entries: &[Entry],
) -> Result<BTreeMap<String, Capture>, Halt> {
    let mut captures = BTreeMap::new();
    for entry in entries {
        let mut args = entry.args.clone();
        let setup_id = match &entry.setup {
            Some(setup) => run_setup(client, setup)?,
            None => Ok(String::new()),
        };
        let response = match setup_id {
            Ok(id) => {
                if entry.setup.is_some() {
                    args.insert("doc_id".to_string(), Value::String(id));
                }
                client.capture(&entry.tool, &Value::Object(args.clone()))?
            }
            Err(reason) => json!({
                "isError": true,
                "content": [{"type": "text", "text": format!("parity replay failed: {reason}")}],
                "structuredContent": null,
            }),
        };
        let is_error = title_bool(response["isError"].as_bool().unwrap_or(false));
        note(&format!("  {}: replayed isError={is_error}", entry.tool));
        captures.insert(entry.tool.clone(), Capture { args, response });
    }
    Ok(captures)
}

/// The compared fixture fields of one capture, normalized as the recorder did.
fn capture_fixture(cap: &Capture, entry: &Entry, rules: &[ScrubRule]) -> Value {
    json!({
        "schema": gr::SCHEMA,
        "tool": entry.tool,
        "arguments": gr::normalize_obj(&Value::Object(cap.args.clone()), rules, false, false),
        "normalization": {
            "rules": rules.iter().map(|rule| rule.name.clone()).collect::<Vec<_>>(),
            "sort_lines": entry.sort_lines,
            "mask_list_lines": entry.mask_list_lines,
        },
        "response": gr::normalize_response(&cap.response, rules, entry.sort_lines, entry.mask_list_lines),
    })
}

/// Normalization block minus the host-dependent `*_resolved` scrub-rule names.
fn stable_normalization(fixture: &Value) -> Value {
    let mut block = object_or_empty(fixture.get("normalization"));
    if let Some(Value::Array(rules)) = block.get_mut("rules") {
        rules.retain(|rule| {
            !rule
                .as_str()
                .is_some_and(|name| name.ends_with("_resolved"))
        });
    }
    Value::Object(block)
}

fn response_text(fixture: &Value) -> String {
    fixture["response"]["content"]
        .as_array()
        .map(|blocks| {
            blocks
                .iter()
                .map(|block| {
                    block
                        .get("text")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                })
                .collect::<Vec<_>>()
                .join("\n")
        })
        .unwrap_or_default()
}

/// Write the failing pair's content texts for burn-down classification.
fn dump_diff(dump_dir: &Path, tool: &str, golden: &Value, actual: &Value) -> Result<(), Halt> {
    fs::create_dir_all(dump_dir).map_err(|err| format!("mkdir {}: {err}", dump_dir.display()))?;
    for (side, fixture) in [("golden", golden), ("rust", actual)] {
        let path = dump_dir.join(format!("{tool}.{side}.txt"));
        fs::write(&path, response_text(fixture))
            .map_err(|err| format!("write {}: {err}", path.display()))?;
    }
    Ok(())
}

/// (tool, first diff) pairs.
type Failures = Vec<(String, String)>;

/// Per-tool golden diff; returns (passed tools, failures).
fn verify_store(
    out_dir: &Path,
    captures: &BTreeMap<String, Value>,
    dump_dir: Option<&Path>,
) -> Result<(Vec<String>, Failures), Halt> {
    let (mut passed, mut failures) = (Vec::new(), Vec::new());
    for (tool, actual) in captures {
        let path = out_dir.join(format!("{tool}.json"));
        if !path.exists() {
            failures.push((tool.clone(), format!("missing fixture {}", path.display())));
            continue;
        }
        let mut golden = read_json(&path)?;
        // Only the idempotent line sort is re-applied at verify time; the list-line mask is not.
        let sort_lines = gr::is_truthy(golden["normalization"].get("sort_lines"));
        let response = gr::normalize_response(
            golden.get("response").unwrap_or(&json!({})),
            &[],
            sort_lines,
            false,
        );
        let normalization = stable_normalization(&golden);
        if let Some(map) = golden.as_object_mut() {
            map.insert("response".to_string(), response);
            map.insert("normalization".to_string(), normalization);
        }
        let mut actual = actual.clone();
        actual["normalization"] = stable_normalization(&actual);
        match gr::diff_path(
            &gr::compare_scope(&golden),
            &gr::compare_scope(&actual),
            "$",
        ) {
            Some(diff) => {
                failures.push((tool.clone(), diff));
                if let Some(dump_dir) = dump_dir {
                    dump_diff(dump_dir, tool, &golden, &actual)?;
                }
            }
            None => passed.push(tool.clone()),
        }
    }
    for path in fixture_paths(out_dir) {
        let stem = path
            .file_stem()
            .unwrap_or_default()
            .to_string_lossy()
            .into_owned();
        if !captures.contains_key(&stem) {
            failures.push((stem, "fixture without a script entry".to_string()));
        }
    }
    Ok((passed, failures))
}

pub(crate) fn shrink(diff: &str) -> String {
    if diff.chars().count() <= DIFF_PRINT_CHARS {
        return diff.to_string();
    }
    format!(
        "{}…",
        diff.chars().take(DIFF_PRINT_CHARS).collect::<String>()
    )
}

fn repr_list(items: &[&String]) -> String {
    let quoted: Vec<String> = items.iter().map(|item| gr::repr_str(item)).collect();
    format!("[{}]", quoted.join(", "))
}

/// Serve the pinned store bytes to the Rust daemon, replay the fixtures, print the verdict.
fn replay(
    out_dir: &Path,
    scratch: &Path,
    store: &str,
    dump_dir: Option<&Path>,
) -> Result<u8, Halt> {
    let workspace = load_pinned_store(store, scratch)?;
    let db = scratch.join(DB_NAME);
    refuse_live_store(&db, &scratch.join(".knowledge"))?;
    let rules = gr::scrub_rules(scratch, &scratch.join("home"), &workspace);
    let mut entries = entries_from_fixtures(out_dir, scratch, &workspace)?;
    apply_volatility(&mut entries);
    let api_key = random_hex(24);
    let exe = ensure_serve_binary()?;
    let captures = {
        let mut daemon = RustDaemon::spawn(
            &exe,
            &db,
            &workspace,
            &daemon_env(scratch, &workspace, &api_key),
        )?;
        if !daemon.wait_ready() {
            return Err(
                format!("rust daemon never became healthy: {}", daemon.diagnostics()).into(),
            );
        }
        let url = daemon.url().ok_or("rust daemon reported no port")?;
        note(&format!(
            "rust daemon up on :{} (keyed); replaying ...",
            daemon.port().unwrap_or_default()
        ));
        let mut client = RustClient::new(&url, &api_key)?;
        let catalog = client.start()?;
        let mut missing: Vec<&String> = entries
            .iter()
            .map(|entry| &entry.tool)
            .filter(|tool| !catalog.contains(tool))
            .collect();
        missing.sort();
        missing.dedup();
        note(&format!(
            "rust catalog: {} tools (missing: {})",
            catalog
                .iter()
                .collect::<std::collections::BTreeSet<_>>()
                .len(),
            repr_list(&missing)
        ));
        replay_entries(&mut client, &entries)?
    };
    let by_tool: BTreeMap<&str, &Entry> = entries
        .iter()
        .map(|entry| (entry.tool.as_str(), entry))
        .collect();
    let fixtures: BTreeMap<String, Value> = captures
        .iter()
        .filter_map(|(tool, cap)| {
            by_tool
                .get(tool.as_str())
                .map(|entry| (tool.clone(), capture_fixture(cap, entry, &rules)))
        })
        .collect();
    let (passed, failures) = verify_store(out_dir, &fixtures, dump_dir)?;
    for tool in by_tool.keys() {
        match failures.iter().find(|(name, _)| name == tool) {
            Some((_, diff)) => println!("FAIL {tool}: {}", shrink(diff)),
            None => println!("PASS {tool}"),
        }
    }
    println!(
        "{store}: {}/{} tools golden-equal against {}",
        passed.len(),
        by_tool.len(),
        out_dir.display()
    );
    Ok(u8::from(!failures.is_empty()))
}

// --- entry point --------------------------------------------------------------------

#[derive(Parser)]
#[command(
    name = "parity",
    about = "Replay the frozen MCP goldens (rust/golden/mcp, over the pinned stores) against the RUST daemon."
)]
struct Args {
    /// self/yarl/attrs: serve the checked-in pinned store under rust/golden/stores.
    #[arg(long, value_parser = STORES)]
    store: String,
    /// Fixture directory (default: rust/golden/mcp for self, rust/golden/mcp/<store> for corpora).
    #[arg(long)]
    out_dir: Option<PathBuf>,
    /// Keep the scratch store for inspection.
    #[arg(long)]
    keep_scratch: bool,
    /// Directory for failing tools' golden/rust text dumps.
    #[arg(long)]
    dump_diffs: Option<PathBuf>,
}

pub fn run(args: &[String]) -> ExitCode {
    let args = match Args::try_parse_from(
        std::iter::once("parity".to_string()).chain(args.iter().cloned()),
    ) {
        Ok(args) => args,
        Err(err) => {
            let _ = err.print();
            return ExitCode::from(u8::try_from(err.exit_code()).unwrap_or(2));
        }
    };
    let out_dir = args
        .out_dir
        .clone()
        .unwrap_or_else(|| match args.store.as_str() {
            "self" => default_out_dir(),
            store => default_out_dir().join(store),
        });
    let scratch = match make_scratch(&format!("cairn-rust-parity-{}-", args.store)) {
        Ok(scratch) => scratch,
        Err(halt) => return exit_code(Err(halt)),
    };
    note(&format!("scratch {}", scratch.display()));
    let result = replay(&out_dir, &scratch, &args.store, args.dump_diffs.as_deref());
    if args.keep_scratch {
        note(&format!("scratch kept: {}", scratch.display()));
    } else {
        let _ = fs::remove_dir_all(&scratch);
    }
    exit_code(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn replay_of_the_pinned_self_store_is_golden_equal() {
        let serve = target_dir()
            .join("release")
            .join("examples")
            .join(SERVE_EXAMPLE);
        if !serve.exists() {
            require_release_artifact(&serve);
            return;
        }
        let scratch = make_scratch("cairn-rust-parity-test-").unwrap_or_else(|_| panic!("scratch"));
        let result = replay(&default_out_dir(), &scratch, "self", None);
        let _ = fs::remove_dir_all(&scratch);
        assert!(matches!(result, Ok(0)), "self replay is not golden-equal");
    }
}
