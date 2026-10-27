//! Closure performance bench: rebuild wall time, peak WAL, single-transaction insert bound.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use clap::Parser;
use regex::Regex;
use rusqlite::Connection;
use serde_json::{Value, json};

use crate::audit_status::py_json_pretty;
use crate::parity::{
    DB_NAME, Halt, cargo_bin, exit_code, make_scratch, pinned_stores, refuse_live_store,
    rust_workspace, target_dir,
};

const WAL_POLL: Duration = Duration::from_millis(1);
const MIB: f64 = 1_048_576.0;

fn note(msg: &str) {
    eprintln!("[closure-bench] {msg}");
}

fn wal_path(db: &Path) -> PathBuf {
    PathBuf::from(format!("{}-wal", db.display()))
}

fn remove_sidecars(db: &Path) {
    for suffix in ["-wal", "-shm"] {
        let _ = fs::remove_file(format!("{}{suffix}", db.display()));
    }
}

/// Peak-size tracker for one `-wal` sidecar while the subject runs.
struct WalSampler {
    path: PathBuf,
    peak: Arc<AtomicU64>,
    halt: Arc<AtomicBool>,
    handle: JoinHandle<()>,
}

impl WalSampler {
    fn start(db: &Path) -> Self {
        let path = wal_path(db);
        let peak = Arc::new(AtomicU64::new(0));
        let halt = Arc::new(AtomicBool::new(false));
        let (watch, peak_slot, stop) = (path.clone(), Arc::clone(&peak), Arc::clone(&halt));
        let handle = thread::spawn(move || {
            while !stop.load(Ordering::Relaxed) {
                if let Ok(meta) = fs::metadata(&watch) {
                    peak_slot.fetch_max(meta.len(), Ordering::Relaxed);
                }
                thread::sleep(WAL_POLL);
            }
        });
        Self {
            path,
            peak,
            halt,
            handle,
        }
    }

    fn stop(self) -> u64 {
        self.halt.store(true, Ordering::Relaxed);
        let _ = self.handle.join();
        let last = fs::metadata(&self.path).map_or(0, |meta| meta.len());
        self.peak.load(Ordering::Relaxed).max(last)
    }
}

fn ensure_cairn_binary() -> Result<PathBuf, Halt> {
    let status = Command::new("cargo")
        .args(["build", "--release", "-p", "cairn-cli"])
        .current_dir(rust_workspace())
        .env("PATH", path_with_cargo_bin())
        .status()
        .map_err(|err| format!("cannot run cargo: {err}"))?;
    if !status.success() {
        return Err(format!("cargo build of cairn-cli failed: {status}").into());
    }
    Ok(target_dir().join("release").join("cairn"))
}

fn path_with_cargo_bin() -> std::ffi::OsString {
    let current = std::env::var_os("PATH").unwrap_or_default();
    let paths = std::iter::once(cargo_bin()).chain(std::env::split_paths(&current));
    std::env::join_paths(paths).unwrap_or(current)
}

struct Row {
    rows: u64,
    levels: u64,
    peak_frontier: u64,
    closure_ms: f64,
    wall_ms: f64,
    peak_wal_bytes: u64,
}

impl Row {
    fn to_value(&self) -> Value {
        json!({
            "rows": self.rows,
            "levels": self.levels,
            "peak_frontier": self.peak_frontier,
            "closure_ms": self.closure_ms,
            "wall_ms": self.wall_ms,
            "peak_wal_bytes": self.peak_wal_bytes,
        })
    }
}

fn round1(value: f64) -> f64 {
    (value * 10.0).round() / 10.0
}

fn run_closure(cairn: &Path, db: &Path) -> Result<Row, Halt> {
    let sampler = WalSampler::start(db);
    let started = Instant::now();
    let mut cmd = Command::new(cairn);
    cmd.arg("closure").arg("--db").arg(db);
    for key in [
        "CAIRN_DB",
        "CAIRN_KNOWLEDGE",
        "CAIRN_WORKSPACE",
        "CAIRN_HOME",
    ] {
        cmd.env_remove(key);
    }
    cmd.env("PATH", path_with_cargo_bin());
    let output = cmd.output();
    let wall_s = started.elapsed().as_secs_f64();
    let peak_wal = sampler.stop();
    let output = output.map_err(|err| format!("cannot run {}: {err}", cairn.display()))?;
    let stdout = String::from_utf8_lossy(&output.stdout).into_owned();
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("cairn closure failed: {}", stderr.trim()).into());
    }
    let lines: Vec<&str> = stdout.lines().filter(|line| !line.is_empty()).collect();
    if lines.len() < 2 {
        return Err(format!("unexpected closure output: {stdout:?}").into());
    }
    let outcome: Value = serde_json::from_str(lines[0])
        .map_err(|err| format!("unexpected closure output: {err}"))?;
    let timings = Regex::new(r"timings: closure=([0-9.]+)ms").map_err(|err| err.to_string())?;
    let closure_ms: f64 = timings
        .captures(lines[1])
        .and_then(|caps| caps[1].parse().ok())
        .ok_or_else(|| format!("no timings line in {stdout:?}"))?;
    let field = |key: &str| outcome[key].as_u64().unwrap_or(0);
    Ok(Row {
        rows: field("rows"),
        levels: field("levels"),
        peak_frontier: field("peak_frontier"),
        closure_ms,
        wall_ms: round1(wall_s * 1000.0),
        peak_wal_bytes: peak_wal,
    })
}

struct Unbatched {
    rows: i64,
    wall_ms: f64,
    peak_wal_bytes: u64,
    gain_x: Option<f64>,
}

/// Replay the same rows in ONE transaction with autocheckpoint off.
fn unbatched_wal_peak(used_db: &Path) -> Result<Unbatched, Halt> {
    let probe = used_db.with_extension("probe.kg");
    fs::copy(used_db, &probe).map_err(|err| format!("copy probe: {err}"))?;
    remove_sidecars(&probe);
    let measured = unbatched_replay(&probe);
    remove_sidecars(&probe);
    let _ = fs::remove_file(&probe);
    let (rows, wall_s, peak_wal) = measured.map_err(|err| format!("unbatched probe: {err}"))?;
    Ok(Unbatched {
        rows,
        wall_ms: round1(wall_s * 1000.0),
        peak_wal_bytes: peak_wal,
        gain_x: None,
    })
}

fn unbatched_replay(probe: &Path) -> rusqlite::Result<(i64, f64, u64)> {
    let conn = Connection::open(probe)?;
    conn.execute_batch("PRAGMA wal_autocheckpoint=0")?;
    let rows = conn.query_row("SELECT COUNT(*) FROM transitive_edges", [], |row| {
        row.get(0)
    })?;
    conn.execute_batch(
        "DROP TABLE IF EXISTS closure_shadow;
         CREATE TABLE closure_shadow AS
           SELECT source_id, target_name, target_id, distance FROM transitive_edges;
         PRAGMA wal_checkpoint(TRUNCATE);",
    )?;
    let sampler = WalSampler::start(probe);
    let started = Instant::now();
    let replay = conn.execute_batch(
        "BEGIN;
         DELETE FROM transitive_edges;
         INSERT INTO transitive_edges (source_id, target_name, target_id, distance)
           SELECT source_id, target_name, target_id, distance FROM closure_shadow;
         COMMIT;",
    );
    let wall_s = started.elapsed().as_secs_f64();
    let peak_wal = sampler.stop();
    replay?;
    conn.execute_batch("DROP TABLE closure_shadow; PRAGMA wal_checkpoint(TRUNCATE);")?;
    Ok((rows, wall_s, peak_wal))
}

fn median(mut values: Vec<f64>) -> f64 {
    values.sort_by(f64::total_cmp);
    let mid = values.len() / 2;
    if values.len() % 2 == 1 {
        values[mid]
    } else {
        (values[mid - 1] + values[mid]) / 2.0
    }
}

/// Median closure rows/s across iterations; 0.0 when no iteration has a positive closure time.
fn median_throughput(rows: &[Row]) -> Value {
    let rates: Vec<f64> = rows
        .iter()
        .filter(|row| row.closure_ms > 0.0)
        .map(|row| row.rows as f64 / (row.closure_ms / 1000.0))
        .collect();
    if rates.is_empty() {
        json!(0.0)
    } else {
        json!(median(rates).round() as i64)
    }
}

struct Block {
    store: String,
    cairn_exe: String,
    iterations: usize,
    per_iteration: Vec<Row>,
    median_closure_ms: f64,
    max_peak_wal_bytes: u64,
    median_rows_per_s: Value,
    unbatched: Option<Unbatched>,
}

impl Block {
    fn to_value(&self) -> Value {
        let mut block = json!({
            "store": self.store,
            "cairn_exe": self.cairn_exe,
            "iterations": self.iterations,
            "per_iteration": self.per_iteration.iter().map(Row::to_value).collect::<Vec<_>>(),
            "median_closure_ms": self.median_closure_ms,
            "max_peak_wal_bytes": self.max_peak_wal_bytes,
            "median_rows_per_s": self.median_rows_per_s,
        });
        if let Some(probe) = &self.unbatched {
            block["unbatched_single_tx"] = json!({
                "rows": probe.rows,
                "wall_ms": probe.wall_ms,
                "peak_wal_bytes": probe.peak_wal_bytes,
                "gain_x": probe.gain_x,
            });
        }
        block
    }
}

fn print_report(block: &Block) {
    println!(
        "store={} iterations={} rows={}",
        block.store, block.iterations, block.per_iteration[0].rows
    );
    for row in &block.per_iteration {
        println!(
            "  closure={:>9.1}ms wall={:>9.1}ms peak_wal={:>7.1}MiB levels={} peak_frontier={}",
            row.closure_ms,
            row.wall_ms,
            row.peak_wal_bytes as f64 / MIB,
            row.levels,
            row.peak_frontier
        );
    }
    println!(
        "median_closure_ms={:?} max_peak_wal_MiB={:.1} median_rows_per_s={}",
        block.median_closure_ms,
        block.max_peak_wal_bytes as f64 / MIB,
        block.median_rows_per_s
    );
    if let Some(probe) = &block.unbatched {
        let gain = probe
            .gain_x
            .map_or("None".to_string(), |gain| format!("{gain:?}"));
        println!(
            "unbatched_single_tx: rows={} peak_wal={:.1}MiB gain_x={gain}",
            probe.rows,
            probe.peak_wal_bytes as f64 / MIB
        );
    }
}

/// Gate: median closure time and peak WAL within tolerance; 0 pass, 1 fail.
fn compare(block: &Block, baseline_path: &Path, tolerance: f64) -> Result<u8, Halt> {
    let text = fs::read_to_string(baseline_path)
        .map_err(|err| format!("read {}: {err}", baseline_path.display()))?;
    let baseline: Value = serde_json::from_str(&text)
        .map_err(|err| format!("parse {}: {err}", baseline_path.display()))?;
    let base_store = baseline["store"].as_str().unwrap_or_default();
    if base_store != block.store {
        println!("compare: baseline store {base_store} != {}", block.store);
        return Ok(1);
    }
    let checks = [
        ("median_closure_ms", json!(block.median_closure_ms)),
        ("max_peak_wal_bytes", json!(block.max_peak_wal_bytes)),
    ];
    let mut failures = Vec::new();
    for (label, now) in checks {
        let base = baseline[label].as_f64().unwrap_or(0.0);
        let limit = base * tolerance;
        let regressed = now.as_f64().unwrap_or(0.0) > limit;
        println!(
            "compare {label:<20} baseline={} now={now} limit={limit:.1} {}",
            baseline[label],
            if regressed { "REGRESSED" } else { "ok" }
        );
        if regressed {
            failures.push(format!(
                "{label}: {now} > {limit:.1} ({} x {tolerance:?})",
                baseline[label]
            ));
        }
    }
    if failures.is_empty() {
        println!(
            "compare: closure gate within {tolerance:?}x on {}",
            block.store
        );
        return Ok(0);
    }
    for failure in failures {
        println!("FAIL {failure}");
    }
    Ok(1)
}

#[derive(Parser)]
#[command(
    name = "closure-bench",
    about = "Closure performance bench: rebuild wall time, peak WAL, insert bound."
)]
struct Args {
    #[arg(long, default_value = "self", value_parser = ["self", "yarl", "attrs"])]
    store: String,
    /// Measured rebuilds on fresh store copies.
    #[arg(long, default_value_t = 5, value_parser = clap::value_parser!(u32).range(1..))]
    iterations: u32,
    /// Drive this cairn binary instead of building from the tree.
    #[arg(long)]
    cairn_exe: Option<PathBuf>,
    /// Skip the single-transaction insert-bound probe.
    #[arg(long)]
    skip_unbatched: bool,
    /// Write the result block to this path.
    #[arg(long)]
    json: Option<PathBuf>,
    /// Gate against a frozen baseline JSON (with --tolerance).
    #[arg(long)]
    compare: Option<PathBuf>,
    /// Max median-ms/peak-WAL ratio vs baseline.
    #[arg(long, default_value_t = 3.0)]
    tolerance: f64,
    #[arg(long)]
    keep_scratch: bool,
}

fn measure(args: &Args, scratch: &Path, pinned: &Path) -> Result<Block, Halt> {
    let db = scratch.join(DB_NAME);
    refuse_live_store(&db, &scratch.join(".knowledge"))?;
    let cairn = match &args.cairn_exe {
        Some(exe) => exe.clone(),
        None => ensure_cairn_binary()?,
    };
    let iterations = args.iterations as usize;
    let mut per_iteration = Vec::with_capacity(iterations);
    for idx in 0..iterations {
        remove_sidecars(&db);
        fs::copy(pinned, &db).map_err(|err| format!("copy {}: {err}", pinned.display()))?;
        let row = run_closure(&cairn, &db)?;
        note(&format!(
            "  iter {}/{iterations}: {} rows, closure={:.1}ms, peak_wal={:.1}MiB",
            idx + 1,
            row.rows,
            row.closure_ms,
            row.peak_wal_bytes as f64 / MIB
        ));
        per_iteration.push(row);
    }
    let max_peak_wal_bytes = per_iteration
        .iter()
        .map(|r| r.peak_wal_bytes)
        .max()
        .unwrap_or(0);
    let unbatched = if args.skip_unbatched {
        None
    } else {
        let mut probe = unbatched_wal_peak(&db)?;
        probe.gain_x = (max_peak_wal_bytes > 0).then(|| {
            (probe.peak_wal_bytes as f64 / max_peak_wal_bytes as f64 * 100.0).round() / 100.0
        });
        Some(probe)
    };
    Ok(Block {
        store: args.store.clone(),
        cairn_exe: cairn.display().to_string(),
        iterations,
        median_closure_ms: round1(median(per_iteration.iter().map(|r| r.closure_ms).collect())),
        max_peak_wal_bytes,
        median_rows_per_s: median_throughput(&per_iteration),
        per_iteration,
        unbatched,
    })
}

fn execute(args: &Args) -> Result<u8, Halt> {
    let pinned = pinned_stores().join(&args.store).join(DB_NAME);
    if !pinned.is_file() {
        return Err(format!("missing pinned store {}", pinned.display()).into());
    }
    let scratch = make_scratch(&format!("cairn-closure-bench-{}-", args.store))?;
    note(&format!("{}: scratch {}", args.store, scratch.display()));
    let measured = measure(args, &scratch, &pinned);
    if args.keep_scratch {
        note(&format!("scratch kept: {}", scratch.display()));
    } else {
        let _ = fs::remove_dir_all(&scratch);
    }
    let block = measured?;
    print_report(&block);
    if let Some(path) = &args.json {
        if let Some(parent) = path.parent().filter(|p| !p.as_os_str().is_empty()) {
            fs::create_dir_all(parent)
                .map_err(|err| format!("mkdir {}: {err}", parent.display()))?;
        }
        let body = py_json_pretty(&block.to_value(), true) + "\n";
        fs::write(path, body).map_err(|err| format!("write {}: {err}", path.display()))?;
        note(&format!("wrote {}", path.display()));
    }
    match &args.compare {
        Some(path) => compare(&block, path, args.tolerance),
        None => Ok(0),
    }
}

pub fn run(args: &[String]) -> ExitCode {
    let args = match Args::try_parse_from(
        std::iter::once("closure-bench".to_string()).chain(args.iter().cloned()),
    ) {
        Ok(args) => args,
        Err(err) => {
            let _ = err.print();
            return ExitCode::from(u8::try_from(err.exit_code()).unwrap_or(2));
        }
    };
    exit_code(execute(&args))
}
