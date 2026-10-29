//! Per-tool p50/p95 latency harness for the Rust daemon over the pinned stores.

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::Instant;

use cairn_query::json_compat::dumps_sorted;
use clap::Parser;
use regex::Regex;
use serde_json::{Map, Value, json};

use crate::parity::{
    DB_NAME, Halt, RustClient, RustDaemon, daemon_env, default_out_dir, ensure_serve_binary,
    entries_from_fixtures, exit_code, load_pinned_store, make_scratch, random_hex,
    refuse_live_store,
};

const PERTURB_TITLE_TOOLS: [&str; 2] = ["knowledge_add", "record_memory"];

fn note(msg: &str) {
    eprintln!("[baseline] {msg}");
}

/// Nearest-rank percentile of a non-empty sample list.
fn percentile(samples: &[f64], pct: usize) -> f64 {
    let mut ordered = samples.to_vec();
    ordered.sort_by(f64::total_cmp);
    let rank = (ordered.len() * pct).div_ceil(100).clamp(1, ordered.len());
    ordered[rank - 1]
}

fn millis(seconds: f64) -> f64 {
    (seconds * 1_000_000.0).round() / 1000.0
}

struct Row {
    rounds: usize,
    errors: u64,
    min_ms: f64,
    p50_ms: f64,
    p95_ms: f64,
    max_ms: f64,
}

type Results = BTreeMap<String, Result<Row, &'static str>>;

struct Block {
    store: String,
    rounds: usize,
    warmup: usize,
    catalog_tools: usize,
    serve_exe: String,
    tools: Results,
}

impl Block {
    fn to_value(&self) -> Value {
        let tools: Map<String, Value> = self
            .tools
            .iter()
            .map(|(tool, row)| {
                let value = match row {
                    Ok(row) => json!({
                        "rounds": row.rounds,
                        "errors": row.errors,
                        "min_ms": row.min_ms,
                        "p50_ms": row.p50_ms,
                        "p95_ms": row.p95_ms,
                        "max_ms": row.max_ms,
                    }),
                    Err(msg) => json!({ "error": msg }),
                };
                (tool.clone(), value)
            })
            .collect();
        json!({
            "store": self.store,
            "rounds": self.rounds,
            "warmup": self.warmup,
            "catalog_tools": self.catalog_tools,
            "serve_exe": self.serve_exe,
            "tools": tools,
        })
    }
}

fn perturb(args: &mut Map<String, Value>, round: i64) {
    let title = args.get("title").and_then(Value::as_str).unwrap_or("");
    let titled = format!("{title} p95bench{round}");
    args.insert("title".into(), Value::String(titled));
}

/// Run the entry's setup probe; returns timed args carrying the fresh id, or None on setup failure.
fn resolve_setup_args(
    client: &mut RustClient,
    entry: &crate::parity::Entry,
    round: i64,
) -> Result<Option<Map<String, Value>>, Halt> {
    let mut args = entry.args.clone();
    let Some(setup) = &entry.setup else {
        return Ok(Some(args));
    };
    let mut setup_args = setup.args.as_object().cloned().unwrap_or_default();
    if PERTURB_TITLE_TOOLS.contains(&setup.tool.as_str()) {
        perturb(&mut setup_args, round);
    }
    let (is_error, text) = client.call(&setup.tool, &Value::Object(setup_args))?;
    if is_error {
        return Ok(None);
    }
    let regex = Regex::new(&setup.id_regex).map_err(|err| format!("bad id_regex: {err}"))?;
    let Some(id) = regex.captures(&text).and_then(|caps| caps.get(1)) else {
        return Ok(None);
    };
    args.insert("doc_id".into(), Value::String(id.as_str().to_string()));
    Ok(Some(args))
}

fn measure(
    store: &str,
    scratch: &Path,
    rounds: usize,
    warmup: usize,
    serve_exe: Option<&Path>,
) -> Result<(Results, usize), Halt> {
    let workspace = load_pinned_store(store, scratch)?;
    let db = scratch.join(DB_NAME);
    refuse_live_store(&db, &scratch.join(".knowledge"))?;
    let out_dir = match store {
        "self" => default_out_dir(),
        other => default_out_dir().join(other),
    };
    let entries = entries_from_fixtures(&out_dir, scratch, &workspace)?;
    let exe = match serve_exe {
        Some(exe) => exe.to_path_buf(),
        None => ensure_serve_binary()?,
    };
    let api_key = random_hex(18);
    let env = daemon_env(scratch, &workspace, &api_key);
    let mut daemon = RustDaemon::spawn(&exe, &db, &workspace, &env)?;
    if !daemon.wait_ready() {
        return Err(format!("daemon never became healthy: {}", daemon.diagnostics()).into());
    }
    let url = daemon.url().ok_or("daemon reported no url")?;
    let mut client = RustClient::new(&url, &api_key)?;
    let catalog: BTreeSet<String> = client.start()?.into_iter().collect();
    let missing: Vec<&str> = entries
        .iter()
        .map(|entry| entry.tool.as_str())
        .filter(|tool| !catalog.contains(*tool))
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    if !missing.is_empty() {
        return Err(format!("daemon catalog missing tools: {missing:?}").into());
    }

    let mut samples: BTreeMap<&str, Vec<f64>> = BTreeMap::new();
    let mut errors: BTreeMap<&str, u64> = BTreeMap::new();
    let (warmup_rounds, timed_rounds) = (warmup as i64, rounds as i64);
    for round in -warmup_rounds..timed_rounds {
        for entry in &entries {
            let tool = entry.tool.as_str();
            let Some(mut args) = resolve_setup_args(&mut client, entry, round)? else {
                *errors.entry(tool).or_default() += 1;
                samples.entry(tool).or_default();
                note(&format!(
                    "  round {round}: {tool}: setup failed; round skipped"
                ));
                continue;
            };
            if PERTURB_TITLE_TOOLS.contains(&tool) {
                perturb(&mut args, round);
            }
            let started = Instant::now();
            let (is_error, _text) = client.call(tool, &Value::Object(args))?;
            let elapsed = started.elapsed().as_secs_f64();
            if round >= 0 {
                samples.entry(tool).or_default().push(elapsed);
            }
            if is_error {
                *errors.entry(tool).or_default() += 1;
            }
        }
        if round >= 0 {
            note(&format!("  {store}: round {}/{rounds} done", round + 1));
        }
    }
    drop(daemon);

    let mut results = Results::new();
    for entry in &entries {
        let tool = entry.tool.as_str();
        let tool_samples = samples.get(tool).map(Vec::as_slice).unwrap_or_default();
        let row = if tool_samples.is_empty() {
            Err("no timed samples (setup always failed)")
        } else {
            let min = tool_samples.iter().copied().fold(f64::INFINITY, f64::min);
            let max = tool_samples.iter().copied().fold(0.0, f64::max);
            Ok(Row {
                rounds: tool_samples.len(),
                errors: errors.get(tool).copied().unwrap_or(0),
                min_ms: millis(min),
                p50_ms: millis(percentile(tool_samples, 50)),
                p95_ms: millis(percentile(tool_samples, 95)),
                max_ms: millis(max),
            })
        };
        results.insert(entry.tool.clone(), row);
    }
    Ok((results, catalog.len()))
}

fn print_report(block: &Block) {
    println!(
        "store={} rounds={} catalog_tools={}",
        block.store, block.rounds, block.catalog_tools
    );
    println!(
        "{:<20} {:>9} {:>9} {:>9} {:>4}",
        "tool", "p50_ms", "p95_ms", "max_ms", "err"
    );
    for (tool, row) in &block.tools {
        match row {
            Err(msg) => println!(
                "{tool:<20} {:>9} {:>9} {:>9} {:>4}  {msg}",
                "-", "-", "-", -1
            ),
            Ok(row) => println!(
                "{tool:<20} {:>9.3} {:>9.3} {:>9.3} {:>4}",
                row.p50_ms, row.p95_ms, row.max_ms, row.errors
            ),
        }
    }
}

/// Gate: every baseline tool's p95 stays within tolerance; 0 pass, 1 fail.
fn compare(block: &Block, baseline_path: &Path, tolerance: f64) -> Result<u8, Halt> {
    let text = fs::read_to_string(baseline_path)
        .map_err(|err| format!("read {}: {err}", baseline_path.display()))?;
    let baseline: Value = serde_json::from_str(&text)
        .map_err(|err| format!("parse {}: {err}", baseline_path.display()))?;
    let candidates: Vec<&Value> = if baseline.get("store").is_some_and(|s| !s.is_null()) {
        vec![&baseline]
    } else {
        baseline["stores"]
            .as_array()
            .into_iter()
            .flatten()
            .collect()
    };
    let Some(base_tools) = candidates
        .into_iter()
        .find(|candidate| candidate["store"].as_str() == Some(block.store.as_str()))
        .and_then(|candidate| candidate["tools"].as_object())
    else {
        println!("compare: baseline has no block for store {}", block.store);
        return Ok(1);
    };
    let mut failures = Vec::new();
    for (tool, base) in base_tools {
        let Some(base_p95) = base.get("p95_ms").and_then(Value::as_f64) else {
            continue;
        };
        let Some(Ok(row)) = block.tools.get(tool) else {
            failures.push(format!("{tool}: missing from this run"));
            continue;
        };
        let limit = base_p95 * tolerance;
        let regressed = row.p95_ms > limit;
        println!(
            "compare {tool:<20} baseline_p95={base_p95:>9.3}ms now={:>9.3}ms limit={limit:>9.3}ms {}",
            row.p95_ms,
            if regressed { "REGRESSED" } else { "ok" }
        );
        if regressed {
            failures.push(format!(
                "{tool}: p95 {:?}ms > {limit:.3}ms ({base_p95:?}ms x {tolerance:?})",
                row.p95_ms
            ));
        }
    }
    if failures.is_empty() {
        println!(
            "compare: all {} baseline tools within {tolerance:?}x on {}",
            base_tools.len(),
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
    name = "baseline",
    about = "Per-tool p95 latency harness for the Rust daemon over the pinned stores."
)]
struct Args {
    #[arg(long, value_parser = ["self", "yarl", "attrs"])]
    store: String,
    /// Timed rounds per tool.
    #[arg(long, default_value_t = 25, value_parser = clap::value_parser!(u32).range(1..))]
    rounds: u32,
    /// Untimed warmup rounds.
    #[arg(long, default_value_t = 2)]
    warmup: u32,
    /// Serve this example binary instead of building from the tree.
    #[arg(long)]
    serve_exe: Option<PathBuf>,
    /// Write the result block to this path.
    #[arg(long)]
    json: Option<PathBuf>,
    /// Gate against a frozen baseline JSON (with --tolerance).
    #[arg(long)]
    compare: Option<PathBuf>,
    /// Max p95 ratio vs baseline per tool.
    #[arg(long, default_value_t = 1.5)]
    tolerance: f64,
}

fn execute(args: &Args) -> Result<u8, Halt> {
    let (rounds, warmup) = (args.rounds as usize, args.warmup as usize);
    let scratch = make_scratch(&format!("cairn-baseline-{}-", args.store))?;
    note(&format!("{}: scratch {}", args.store, scratch.display()));
    let measured = measure(
        &args.store,
        &scratch,
        rounds,
        warmup,
        args.serve_exe.as_deref(),
    );
    let _ = fs::remove_dir_all(&scratch);
    let (tools, catalog_tools) = measured?;
    let block = Block {
        store: args.store.clone(),
        rounds,
        warmup,
        catalog_tools,
        serve_exe: args
            .serve_exe
            .as_ref()
            .map_or_else(|| "built-from-tree".into(), |p| p.display().to_string()),
        tools,
    };
    print_report(&block);
    if let Some(path) = &args.json {
        if let Some(parent) = path.parent().filter(|p| !p.as_os_str().is_empty()) {
            fs::create_dir_all(parent)
                .map_err(|err| format!("mkdir {}: {err}", parent.display()))?;
        }
        let body = dumps_sorted(&block.to_value()) + "\n";
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
        std::iter::once("baseline".to_string()).chain(args.iter().cloned()),
    ) {
        Ok(args) => args,
        Err(err) => {
            let _ = err.print();
            return ExitCode::from(u8::try_from(err.exit_code()).unwrap_or(2));
        }
    };
    exit_code(execute(&args))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn percentile_is_nearest_rank() {
        let samples: Vec<f64> = (1..=20).map(f64::from).collect();
        assert_eq!(percentile(&samples, 50), 10.0);
        assert_eq!(percentile(&samples, 95), 19.0);
        assert_eq!(percentile(&[7.0], 95), 7.0);
    }
}
