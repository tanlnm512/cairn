//! Lifecycle/ops commands (system status/sync/metrics/report, upgrade,
//! uninstall, version, download-reranker ports).

use std::collections::BTreeMap;
use std::io::Write as _;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};
use std::time::Instant;

use regex::Regex;
use rusqlite::Connection;
use serde_json::{Map, Value, json};

use crate::agent_install;
use crate::cli_rows;
use crate::cmds_doctor;
use crate::display;
use crate::py_json;
use crate::store_paths;

// --- version -----------------------------------------------------------------

pub fn run_version() -> ExitCode {
    display::info(&format!("cairn-intel {}", env!("CARGO_PKG_VERSION")));
    ExitCode::SUCCESS
}

// --- status ------------------------------------------------------------------

pub struct StatusArgs {
    pub db: Option<String>,
    pub knowledge: Option<String>,
}

pub fn run_status(args: StatusArgs) -> ExitCode {
    let db = db_arg(&args.db);
    let Ok(conn) = cairn_store::schema::open_initialized(Path::new(&db)) else {
        return crate::cmds_core::command_error(&format!("cannot open {db}"));
    };
    let stats = cli_rows::get_stats(&conn);
    let knowledge_path = args
        .knowledge
        .clone()
        .map_or_else(|| store_paths::resolve_store(None).knowledge, PathBuf::from);
    let bundle = cairn_query::tools_km::Bundle::new(&knowledge_path);
    let compass_n = bundle.list_concepts(Some("compass/")).len();
    let wiki_n = bundle.list_concepts(Some("wiki/")).len();

    display::kv(
        "graph",
        &format!(
            "{} repos · {} symbols · {} edges",
            stats.repos,
            crate::cmds_core::fmt_int(stats.symbols),
            crate::cmds_core::fmt_int(stats.edges),
        ),
    );
    display::kv("compass", &format!("{compass_n} files"));
    display::kv("wiki", &format!("{wiki_n} articles"));
    for tier in cairn_query::tools_km::TIERS {
        let mems = cairn_query::tools_km::list_memories(&bundle, Some(tier), None);
        let avg = if mems.is_empty() {
            0.0
        } else {
            mems.iter()
                .map(|m| m.ext_f64("memory_score").unwrap_or(0.0))
                .sum::<f64>()
                / mems.len() as f64
        };
        // Python rounds the tier average to 3 decimals before the .2 display.
        let avg = format!("{avg:.3}").parse::<f64>().unwrap_or(avg);
        display::kv(
            &format!("  {tier}"),
            &format!("{:>4} (avg {avg:.2})", mems.len()),
        );
    }

    let pending = pending_sync_rows(&conn);
    if !pending.is_empty() {
        display::warning(&format!("Pending sync: {} files", pending.len()));
        for path in pending.iter().take(20) {
            display::dim(&format!("  {}", crate::cmds_core::shorten(path)));
        }
        if pending.len() > 20 {
            display::dim(&format!("  ... and {} more", pending.len() - 20));
        }
    }
    let (total, rows) = parse_errors(&conn);
    if total > 0 {
        display::warning(&format!("Parse errors: {total}"));
        for (file_path, message) in &rows {
            let mut message = message.clone().unwrap_or_default();
            if message.chars().count() > 100 {
                message = format!("{}...", message.chars().take(100).collect::<String>());
            }
            display::dim(&format!(
                "  {} — {message}",
                crate::cmds_core::shorten(file_path)
            ));
        }
        if total as usize > rows.len() {
            display::dim(&format!("  ... and {} more", total as usize - rows.len()));
        }
    }
    ExitCode::SUCCESS
}

fn db_arg(db: &Option<String>) -> String {
    db.clone().unwrap_or_else(|| {
        store_paths::resolve_store(None)
            .db
            .to_string_lossy()
            .into_owned()
    })
}

fn pending_sync_rows(conn: &Connection) -> Vec<String> {
    let Ok(mut stmt) = conn.prepare("SELECT path FROM pending_sync ORDER BY changed_at DESC")
    else {
        return Vec::new();
    };
    stmt.query_map([], |row| row.get::<_, String>(0))
        .map(|rows| rows.flatten().collect())
        .unwrap_or_default()
}

type ParseErrorRow = (String, Option<String>);

fn parse_errors(conn: &Connection) -> (i64, Vec<ParseErrorRow>) {
    let Ok(total) = conn.query_row("SELECT COUNT(*) FROM parse_errors", [], |r| {
        r.get::<_, i64>(0)
    }) else {
        return (0, Vec::new());
    };
    let Ok(mut stmt) = conn.prepare(
        "SELECT file_path, error_message FROM parse_errors \
         ORDER BY timestamp DESC LIMIT 5",
    ) else {
        return (total, Vec::new());
    };
    let rows = stmt
        .query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, Option<String>>(1)?))
        })
        .map(|rows| rows.flatten().collect())
        .unwrap_or_default();
    (total, rows)
}

// --- sync --------------------------------------------------------------------

pub struct SyncArgs {
    pub workspace: Option<String>,
    pub db: Option<String>,
}

pub fn run_sync(args: SyncArgs) -> ExitCode {
    let db = db_arg(&args.db);
    let workspace = args.workspace.clone().unwrap_or_else(|| {
        store_paths::resolve_workspace(None)
            .to_string_lossy()
            .into_owned()
    });
    let Ok(mut conn) = cairn_store::schema::open_initialized(Path::new(&db)) else {
        return crate::cmds_core::command_error(&format!("cannot open {db}"));
    };
    let changed = cairn_build::incremental::detect_changed(&conn, &workspace);
    if changed.is_empty() {
        display::success("No changes detected. Graph is up to date.");
        return ExitCode::SUCCESS;
    }
    let started = std::time::SystemTime::now();
    let lock = match cairn_store::swap::build_lock(Path::new(&db)) {
        Ok(lock) => lock,
        Err(e) => return crate::cmds_core::command_error(&e),
    };
    let closure_built = table_has_rows(&conn, "transitive_edges");
    let timer = Instant::now();
    let description = format!("Syncing {} files", changed.len());
    plain_progress_draw(&description, 0, changed.len(), 0.0, "files");
    let result = cairn_build::incremental::reindex_paths(&mut conn, &workspace, &changed);
    plain_progress_draw(
        &description,
        changed.len(),
        changed.len(),
        timer.elapsed().as_secs_f64(),
        "files",
    );
    println!();
    let mut errors = result.errors.clone();
    if result.reindexed > 0 || result.deleted > 0 {
        // An absent closure stays absent, mirroring the python prestate rule;
        // the dataflow and scip-overlay legs wait on the 4c-3 builders.
        if closure_built
            && let Err(e) = cairn_build::closure::build_transitive_closure(&mut conn, 4)
        {
            errors.push(format!("transitive_closure: {e}"));
        }
    }
    drop(lock);
    display::success(&format!(
        "Synced: {} reindexed, {} deleted",
        result.reindexed, result.deleted
    ));
    if !errors.is_empty() {
        display::warning(&format!("{} errors", errors.len()));
        for error in errors.iter().take(5) {
            display::dim(&format!("  {error}"));
        }
    }
    record_build_run(
        &db,
        "sync",
        started,
        timer.elapsed().as_secs_f64(),
        Some(result.reindexed as i64),
        None,
        result.deleted,
    );
    ExitCode::SUCCESS
}

/// One non-TTY `_PlainTextProgress` draw: determinate when `total` > 0,
/// count-only otherwise; callers emit the trailing newline on stop.
pub fn plain_progress_draw(
    description: &str,
    completed: usize,
    total: usize,
    elapsed_s: f64,
    unit: &str,
) {
    let pct = (100 * completed).checked_div(total);
    if let Some(pct) = pct {
        let filled = 20 * completed / total;
        let bar = format!("{}{}", "█".repeat(filled), "░".repeat(20 - filled));
        print!(
            "\r{description}  [{bar}] {}/{} {unit}  {pct}%  {elapsed_s:.0}s",
            crate::cmds_core::fmt_int(completed as i64),
            crate::cmds_core::fmt_int(total as i64)
        );
    } else {
        print!(
            "\r{description}  {} {unit}  {elapsed_s:.0}s",
            crate::cmds_core::fmt_int(completed as i64)
        );
    }
    let _ = std::io::stdout().flush();
}

fn table_has_rows(conn: &Connection, table: &str) -> bool {
    conn.query_row(&format!("SELECT 1 FROM {table} LIMIT 1"), [], |_| Ok(()))
        .is_ok()
}

fn telemetry_off() -> bool {
    std::env::var("CAIRN_TELEMETRY")
        .map(|v| v.trim().eq_ignore_ascii_case("off"))
        .unwrap_or(false)
}

/// Record a build_runs row; `files`/`symbols` are column choices per kind
/// (build/sync carry files, embed carries symbols).
pub fn record_build_run(
    db: &str,
    kind: &str,
    started: std::time::SystemTime,
    duration_s: f64,
    files: Option<i64>,
    symbols: Option<i64>,
    skipped: usize,
) {
    if telemetry_off() {
        return;
    }
    let Ok(conn) = cairn_store::schema::open_initialized(Path::new(db)) else {
        return;
    };
    let epoch = started
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| (d.as_secs() as i64, d.subsec_micros()))
        .unwrap_or((0, 0));
    let started_at = cairn_build::timefmt::iso_from_epoch(epoch.0, epoch.1);
    let workers: Option<i64> = std::env::var("CAIRN_WORKERS")
        .ok()
        .and_then(|raw| raw.parse::<i64>().ok())
        .map(|n| n.clamp(1, 256));
    let session = std::env::var("CAIRN_SESSION").unwrap_or_else(|_| "unknown".into());
    let _ = conn.execute(
        "INSERT INTO build_runs (kind, started_at, duration_s, phase_timings, repos, files, \
         symbols, edges, resolution_exact, resolution_ambiguous, resolution_unresolved, \
         parse_errors, skipped, workers, session_id) \
         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rusqlite::params![
            kind,
            started_at,
            duration_s,
            Option::<String>::None,
            Option::<i64>::None,
            files,
            symbols,
            Option::<i64>::None,
            Option::<i64>::None,
            Option::<i64>::None,
            Option::<i64>::None,
            Option::<i64>::None,
            skipped as i64,
            workers,
            session,
        ],
    );
}

// --- metrics -----------------------------------------------------------------

pub struct MetricsArgs {
    pub db: Option<String>,
    pub tool: Option<String>,
    pub as_json: bool,
    pub builds: bool,
    pub quality: bool,
    pub contention: bool,
    pub tasks: bool,
}

pub fn run_metrics(args: MetricsArgs) -> ExitCode {
    if !(args.builds || args.quality || args.contention || args.tasks) {
        return metrics_default(&args);
    }
    let db = db_arg(&args.db);
    let Ok(conn) = cairn_store::schema::open_initialized(Path::new(&db)) else {
        return crate::cmds_core::command_error(&format!("cannot open {db}"));
    };
    let mut sections: Vec<(&str, Value)> = Vec::new();
    if args.builds {
        sections.push((
            "builds",
            Value::Array(
                gather_builds(&conn)
                    .into_iter()
                    .map(Value::Object)
                    .collect(),
            ),
        ));
    }
    if args.quality {
        sections.push(("quality", gather_quality(&conn)));
    }
    if args.contention {
        sections.push((
            "contention",
            Value::Array(
                gather_contention(&conn)
                    .into_iter()
                    .map(Value::Object)
                    .collect(),
            ),
        ));
    }
    if args.tasks {
        sections.push(("tasks", gather_tasks(&conn)));
    }
    if args.as_json {
        if sections.len() == 1 {
            println!("{}", py_json::dumps(&sections[0].1));
        } else {
            let mut obj = Map::new();
            for (key, value) in &sections {
                obj.insert((*key).into(), value.clone());
            }
            println!("{}", py_json::dumps(&Value::Object(obj)));
        }
        return ExitCode::SUCCESS;
    }
    for (i, (kind, value)) in sections.iter().enumerate() {
        if i > 0 {
            println!();
        }
        match *kind {
            "builds" => render_builds(value.as_array().map(Vec::as_slice).unwrap_or(&[])),
            "quality" => render_quality(value),
            "tasks" => render_tasks(value),
            _ => render_contention(value.as_array().map(Vec::as_slice).unwrap_or(&[])),
        }
    }
    ExitCode::SUCCESS
}

fn metrics_default(args: &MetricsArgs) -> ExitCode {
    let db = db_arg(&args.db);
    let Ok(conn) = cairn_store::schema::open_initialized(Path::new(&db)) else {
        return crate::cmds_core::command_error(&format!("cannot open {db}"));
    };
    let mut sql = String::from(
        "SELECT tool_name, COUNT(*) AS calls, AVG(duration_ms) AS avg_ms, \
         SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors \
         FROM tool_metrics ",
    );
    if args.tool.is_some() {
        sql.push_str("WHERE tool_name = ? ");
    }
    sql.push_str("GROUP BY tool_name ORDER BY calls DESC");
    let mut stmt = match conn.prepare(&sql) {
        Ok(stmt) => stmt,
        Err(_) => {
            display::info("No tool metrics recorded yet.");
            return ExitCode::SUCCESS;
        }
    };
    let rows: Vec<(String, i64, Option<f64>, i64)> = match &args.tool {
        Some(tool) => stmt
            .query_map([tool.as_str()], metric_row)
            .map(|rows| rows.flatten().collect())
            .unwrap_or_default(),
        None => stmt
            .query_map([], metric_row)
            .map(|rows| rows.flatten().collect())
            .unwrap_or_default(),
    };
    if rows.is_empty() {
        display::info("No tool metrics recorded yet.");
        return ExitCode::SUCCESS;
    }
    if args.as_json {
        let items: Vec<Value> = rows
            .iter()
            .map(|(tool_name, calls, avg_ms, errors)| {
                json!({
                    "tool_name": tool_name,
                    "calls": calls,
                    "avg_ms": avg_ms.map(Value::from).unwrap_or(Value::Null),
                    "errors": errors,
                })
            })
            .collect();
        println!("{}", py_json::dumps(&Value::Array(items)));
        return ExitCode::SUCCESS;
    }
    let table_rows: Vec<Vec<String>> = rows
        .iter()
        .map(|(tool_name, calls, avg_ms, errors)| {
            let err_pct = if *calls > 0 {
                *errors as f64 / *calls as f64 * 100.0
            } else {
                0.0
            };
            vec![
                tool_name.clone(),
                crate::cmds_core::fmt_int(*calls),
                format!("{:.1}", avg_ms.unwrap_or(0.0)),
                crate::cmds_core::fmt_int(*errors),
                format!("{err_pct:.1}%"),
            ]
        })
        .collect();
    print_table(
        None,
        &["tool", "calls", "avg ms", "errors", "err %"],
        &table_rows,
    );
    ExitCode::SUCCESS
}

fn metric_row(row: &rusqlite::Row) -> rusqlite::Result<(String, i64, Option<f64>, i64)> {
    Ok((
        row.get::<_, String>(0)?,
        row.get::<_, i64>(1)?,
        row.get::<_, Option<f64>>(2)?,
        row.get::<_, i64>(3)?,
    ))
}

const BUILDS_LIMIT: i64 = 20;

fn gather_builds(conn: &Connection) -> Vec<Map<String, Value>> {
    let Ok(mut stmt) = conn.prepare(
        "SELECT kind, started_at, duration_s, repos, files, symbols, edges, \
         resolution_exact, resolution_ambiguous, resolution_unresolved, \
         parse_errors, skipped, workers \
         FROM build_runs ORDER BY started_at DESC LIMIT ?",
    ) else {
        return Vec::new();
    };
    let columns = [
        "kind",
        "started_at",
        "duration_s",
        "repos",
        "files",
        "symbols",
        "edges",
        "resolution_exact",
        "resolution_ambiguous",
        "resolution_unresolved",
        "parse_errors",
        "skipped",
        "workers",
    ];
    stmt.query_map([BUILDS_LIMIT], |row| {
        let mut obj = Map::new();
        for (idx, key) in columns.into_iter().enumerate() {
            obj.insert(key.into(), value_of(row, idx)?);
        }
        Ok(obj)
    })
    .map(|rows| rows.flatten().collect())
    .unwrap_or_default()
}

fn count_events(conn: &Connection, name: &str) -> i64 {
    conn.query_row(
        "SELECT COUNT(*) FROM events WHERE name = ?",
        [name],
        |row| row.get::<_, i64>(0),
    )
    .unwrap_or(0)
}

/// Distinct-value counts of `attr_key` across `name` events, first-seen order.
fn attr_counts(conn: &Connection, name: &str, attr_key: &str) -> Map<String, Value> {
    let mut counts = Map::new();
    let Ok(mut stmt) = conn.prepare("SELECT attrs FROM events WHERE name = ?") else {
        return counts;
    };
    let Ok(rows) = stmt.query_map([name], |row| row.get::<_, Option<String>>(0)) else {
        return counts;
    };
    for attrs in rows.flatten().flatten() {
        let Some(value) = attr_value(&attrs, attr_key) else {
            continue;
        };
        let entry = counts.get(&value).and_then(Value::as_i64).unwrap_or(0) + 1;
        counts.insert(value, json!(entry));
    }
    counts
}

fn attr_value(raw: &str, key: &str) -> Option<String> {
    let attrs: Value = serde_json::from_str(raw).ok()?;
    let value = attrs.get(key)?;
    if value.is_null() {
        return None;
    }
    match value {
        Value::String(s) => Some(s.clone()),
        Value::Number(n) => Some(n.to_string()),
        Value::Bool(b) => Some(if *b { "True".into() } else { "False".into() }),
        _ => None,
    }
}

fn gather_quality(conn: &Connection) -> Value {
    let empty_by_kind = attr_counts(conn, "empty_result", "query_kind");
    let empty_semantic = empty_by_kind
        .get("semantic_search")
        .and_then(Value::as_i64)
        .unwrap_or(0) as f64;
    let semantic_total = count_events(conn, "semantic_backend") as f64;
    json!({
        "empty_results": count_events(conn, "empty_result"),
        "empty_by_kind": Value::Object(empty_by_kind),
        "semantic_total": semantic_total as i64,
        "empty_result_rate": if semantic_total > 0.0 {
            json!(empty_semantic / semantic_total)
        } else {
            Value::Null
        },
        "truncations": count_events(conn, "truncate_result"),
        "truncations_by_tool": Value::Object(attr_counts(conn, "truncate_result", "tool")),
        "backend_mix": Value::Object(attr_counts(conn, "semantic_backend", "backend")),
    })
}

fn gather_contention(conn: &Connection) -> Vec<Map<String, Value>> {
    let Ok(mut stmt) = conn.prepare("SELECT ts, attrs FROM events WHERE name = ? ORDER BY ts ASC")
    else {
        return Vec::new();
    };
    let Ok(rows) = stmt.query_map(["lock_contention"], |row| {
        Ok((value_of(row, 0)?, row.get::<_, Option<String>>(1)?))
    }) else {
        return Vec::new();
    };
    let mut sites: BTreeMap<String, Map<String, Value>> = BTreeMap::new();
    for (ts, attrs) in rows.flatten() {
        let site = attrs
            .as_deref()
            .and_then(|raw| attr_value(raw, "site"))
            .unwrap_or_else(|| "<unknown>".into());
        let entry = sites.entry(site.clone()).or_insert_with(|| {
            let mut obj = Map::new();
            obj.insert("site".into(), json!(site));
            obj.insert("count".into(), json!(0));
            obj
        });
        entry.insert(
            "count".into(),
            json!(entry["count"].as_i64().unwrap_or(0) + 1),
        );
        entry.insert("last_ts".into(), ts);
    }
    let mut out: Vec<Map<String, Value>> = sites.into_values().collect();
    out.sort_by(|a, b| {
        let count = b["count"]
            .as_i64()
            .unwrap_or(0)
            .cmp(&a["count"].as_i64().unwrap_or(0));
        count.then_with(|| {
            a["site"]
                .as_str()
                .unwrap_or("")
                .cmp(b["site"].as_str().unwrap_or(""))
        })
    });
    out
}

fn gather_tasks(conn: &Connection) -> Value {
    json!({
        "total": count_events(conn, "task_lifecycle"),
        "by_event": Value::Object(attr_counts(conn, "task_lifecycle", "event")),
        "by_kind": Value::Object(attr_counts(conn, "task_lifecycle", "task_kind")),
    })
}

fn fmt_ts(value: &Value) -> String {
    match value {
        Value::Null => "—".into(),
        Value::Number(n) => n
            .as_f64()
            .map(epoch_to_display)
            .unwrap_or_else(|| n.to_string()),
        Value::String(s) => match cmds_doctor::parse_ts(s) {
            Some(epoch) => epoch_to_display(epoch),
            None => s.clone(),
        },
        other => other.to_string(),
    }
}

/// `datetime.utcfromtimestamp`-shaped "%Y-%m-%d %H:%M:%S".
fn epoch_to_display(epoch: f64) -> String {
    let secs = epoch.floor() as i64;
    let days = secs.div_euclid(86400);
    let (year, month, day) = civil_from_days(days);
    let rem = secs.rem_euclid(86400);
    format!(
        "{year:04}-{month:02}-{day:02} {:02}:{:02}:{:02}",
        rem / 3600,
        (rem % 3600) / 60,
        rem % 60
    )
}

/// Howard Hinnant's days-to-civil.
fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

fn fmt_dur(value: &Value) -> String {
    value
        .as_f64()
        .map(|v| format!("{v:.1}s"))
        .unwrap_or("—".into())
}

fn fmt_int_opt(value: &Value) -> String {
    value
        .as_i64()
        .map(crate::cmds_core::fmt_int)
        .unwrap_or("—".into())
}

fn fmt_resolution(row: &Value) -> String {
    let obj = row.as_object();
    let parts: Vec<Value> = [
        "resolution_exact",
        "resolution_ambiguous",
        "resolution_unresolved",
    ]
    .iter()
    .map(|key| {
        obj.and_then(|o| o.get(*key))
            .cloned()
            .unwrap_or(Value::Null)
    })
    .collect();
    if parts.iter().all(Value::is_null) {
        return "—".into();
    }
    parts.iter().map(fmt_int_opt).collect::<Vec<_>>().join("/")
}

fn render_builds(rows: &[Value]) {
    if rows.is_empty() {
        display::info("No build runs recorded yet.");
        return;
    }
    let get = |row: &Map<String, Value>, key: &str| row.get(key).cloned().unwrap_or(Value::Null);
    let table_rows: Vec<Vec<String>> = rows
        .iter()
        .filter_map(Value::as_object)
        .map(|row| {
            vec![
                get(row, "kind").as_str().unwrap_or("None").into(),
                fmt_ts(&get(row, "started_at")),
                fmt_dur(&get(row, "duration_s")),
                fmt_int_opt(&get(row, "repos")),
                fmt_int_opt(&get(row, "files")),
                fmt_int_opt(&get(row, "symbols")),
                fmt_int_opt(&get(row, "edges")),
                fmt_resolution(&Value::Object(row.clone())),
                fmt_int_opt(&get(row, "parse_errors")),
                fmt_int_opt(&get(row, "skipped")),
            ]
        })
        .collect();
    print_table(
        Some("Build runs"),
        &[
            "kind",
            "started",
            "dur",
            "repos",
            "files",
            "symbols",
            "edges",
            "resolution",
            "errs",
            "skip",
        ],
        &table_rows,
    );
}

fn sorted_counts(counts: &Map<String, Value>) -> String {
    let mut ordered: Vec<(&String, &Value)> = counts.iter().collect();
    ordered.sort_by(|a, b| {
        b.1.as_i64()
            .unwrap_or(0)
            .cmp(&a.1.as_i64().unwrap_or(0))
            .then_with(|| a.0.cmp(b.0))
    });
    ordered
        .iter()
        .map(|(k, v)| format!("{k}: {}", v.as_i64().unwrap_or(0)))
        .collect::<Vec<_>>()
        .join(", ")
}

fn render_quality(data: &Value) {
    let zero = |key: &str| data.get(key).and_then(Value::as_i64).unwrap_or(0);
    if zero("semantic_total") == 0 && zero("empty_results") == 0 && zero("truncations") == 0 {
        display::info("No quality events recorded yet.");
        return;
    }
    let rate = data.get("empty_result_rate").and_then(Value::as_f64);
    let rate_str = match rate {
        Some(rate) => format!("{:.1}%", rate * 100.0),
        None => "n/a".into(),
    };
    println!("Quality signals");
    let by_kind = data.get("empty_by_kind").and_then(Value::as_object);
    let empty_semantic = by_kind
        .and_then(|m| m.get("semantic_search"))
        .and_then(Value::as_i64)
        .unwrap_or(0);
    display::kv(
        "empty results (semantic)",
        &format!("{empty_semantic} / {} ({rate_str})", zero("semantic_total")),
    );
    if let Some(by_kind) = by_kind
        && !by_kind.is_empty()
    {
        display::kv("empty by kind", &sorted_counts(by_kind));
    }
    display::kv("truncations", &format!("{}", zero("truncations")));
    let mix = data.get("backend_mix").and_then(Value::as_object);
    match mix {
        Some(mix) if !mix.is_empty() => {
            let total: i64 = mix.values().filter_map(Value::as_i64).sum();
            display::kv(
                "backend mix",
                &format!("{total} calls — {}", sorted_counts(mix)),
            );
        }
        _ => display::kv("backend mix", "no semantic calls recorded"),
    }
}

fn render_contention(rows: &[Value]) {
    if rows.is_empty() {
        display::info("No lock-contention events recorded.");
        return;
    }
    let table_rows: Vec<Vec<String>> = rows
        .iter()
        .filter_map(Value::as_object)
        .map(|row| {
            vec![
                row.get("site").and_then(Value::as_str).unwrap_or("").into(),
                crate::cmds_core::fmt_int(row.get("count").and_then(Value::as_i64).unwrap_or(0)),
                fmt_ts(row.get("last_ts").unwrap_or(&Value::Null)),
            ]
        })
        .collect();
    print_table(
        Some("Lock contention by site"),
        &["site", "count", "last seen"],
        &table_rows,
    );
}

fn render_tasks(data: &Value) {
    let total = data.get("total").and_then(Value::as_i64).unwrap_or(0);
    if total == 0 {
        display::info("No task-lifecycle events recorded yet.");
        return;
    }
    println!("Task queue lifecycle");
    display::kv("events (total)", &crate::cmds_core::fmt_int(total));
    for (label, key) in [("by event", "by_event"), ("by kind", "by_kind")] {
        match data.get(key).and_then(Value::as_object) {
            Some(counts) if !counts.is_empty() => {
                display::kv(label, &sorted_counts(counts));
            }
            _ => display::kv(label, "none recorded"),
        }
    }
}

/// rich Table rendering for non-TTY output (the display.print_table contract).
fn print_table(title: Option<&str>, columns: &[&str], rows: &[Vec<String>]) {
    println!("{}", render_table(title, columns, rows));
}

fn render_table(title: Option<&str>, columns: &[&str], rows: &[Vec<String>]) -> String {
    const RIGHT_ALIGNED: [&str; 7] = ["count", "n", "calls", "symbols", "edges", "files", "repos"];
    let widths: Vec<usize> = columns
        .iter()
        .enumerate()
        .map(|(idx, col)| {
            let mut width = col.chars().count();
            for row in rows {
                width = width.max(row.get(idx).map(|c| c.chars().count()).unwrap_or(0));
            }
            width
        })
        .collect();
    let total_width: usize = widths.iter().sum::<usize>() + widths.len() * 3 + 1;
    let mut out = String::new();
    if let Some(title) = title {
        let text = format!(" {title} ");
        if text.chars().count() < total_width {
            let pad = total_width - text.chars().count();
            let lead = pad / 2;
            out.push_str(&" ".repeat(lead));
            out.push_str(&text);
            out.push_str(&" ".repeat(pad - lead));
        } else {
            out.push_str(title);
        }
        out.push('\n');
    }
    let border = |left: &str, join: &str, right: &str, fill: char| {
        let mut line = String::from(left);
        for (idx, width) in widths.iter().enumerate() {
            if idx > 0 {
                line.push_str(join);
            }
            line.push_str(&fill.to_string().repeat(width + 2));
        }
        line.push_str(right);
        line
    };
    out.push_str(&border("┏", "┳", "┓", '━'));
    out.push('\n');
    let header: Vec<String> = columns
        .iter()
        .zip(&widths)
        .map(|(col, width)| format!("┃ {:<width$} ", col, width = width))
        .collect();
    out.push_str(&header.join(""));
    out.push_str("┃\n");
    out.push_str(&border("┡", "╇", "┩", '━'));
    out.push('\n');
    for row in rows {
        let cells: Vec<String> = row
            .iter()
            .zip(columns)
            .zip(&widths)
            .map(|((cell, col), width)| {
                let right = RIGHT_ALIGNED.contains(&col.to_lowercase().as_str());
                let cell = cell as &str;
                if right {
                    format!("│ {:>width$} ", cell, width = width)
                } else {
                    format!("│ {:<width$} ", cell, width = width)
                }
            })
            .collect();
        out.push_str(&cells.join(""));
        out.push_str("│\n");
    }
    out.push_str(&border("└", "┴", "┘", '─'));
    out
}

// --- report ------------------------------------------------------------------

pub struct ReportArgs {
    pub db: Option<String>,
    pub as_json: bool,
    pub out: Option<String>,
}

const REPORT_LIMIT: i64 = 20;
const ERROR_EVENTS: [&str; 3] = ["ann_fallback", "hash_fallback", "lock_contention"];

pub fn run_report(args: ReportArgs) -> ExitCode {
    let db = db_arg(&args.db);
    let bundle = build_report(&db);
    let text = if args.as_json {
        py_json::dumps(&bundle)
    } else {
        render_report(&bundle)
    };
    println!("{text}");
    if let Some(out_path) = &args.out {
        match std::fs::write(out_path, format!("{text}\n")) {
            Ok(()) => eprintln!("wrote report to {out_path}"),
            Err(e) => eprintln!("warning: could not write --out {out_path}: {e}"),
        }
    }
    ExitCode::SUCCESS
}

fn sqlite_version() -> String {
    Connection::open_in_memory()
        .and_then(|conn| {
            conn.query_row("SELECT sqlite_version()", [], |row| row.get::<_, String>(0))
        })
        .unwrap_or_default()
}

fn build_report(db: &str) -> Value {
    let conn = open_report_conn(db);
    let mut versions = Map::new();
    versions.insert("cairn".into(), json!(env!("CARGO_PKG_VERSION")));
    versions.insert("python".into(), Value::Null);
    versions.insert(
        "platform".into(),
        json!(format!(
            "{}-{} (rust)",
            std::env::consts::OS,
            std::env::consts::ARCH
        )),
    );
    versions.insert("sqlite".into(), json!(sqlite_version()));
    let user_version = conn
        .as_ref()
        .and_then(|conn| {
            conn.query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))
                .ok()
        })
        .map(Value::from)
        .unwrap_or(Value::Null);
    versions.insert("db_schema_user_version".into(), user_version);
    let versions = scrub_strings(versions);

    let recent_errors = gather_recent_errors(conn.as_ref());
    let resolution = report_resolution(conn.as_ref());
    let doctor: Vec<Value> = cmds_doctor::run_doctor(db)
        .into_iter()
        .map(|r| {
            let mut obj = Map::new();
            obj.insert("name".into(), json!(r.name));
            obj.insert("status".into(), json!(r.status));
            obj.insert("detail".into(), Value::String(scrub(&r.detail)));
            obj.insert(
                "hint".into(),
                r.hint
                    .as_deref()
                    .map(|h| Value::String(scrub(h)))
                    .unwrap_or(Value::Null),
            );
            Value::Object(obj)
        })
        .collect();

    let mut bundle = Map::new();
    bundle.insert("generated_at".into(), json!(now_iso()));
    bundle.insert("versions".into(), Value::Object(versions));
    bundle.insert("doctor".into(), Value::Array(doctor));
    bundle.insert("recent_errors".into(), recent_errors);
    bundle.insert("resolution".into(), resolution);
    bundle.insert("quality_gates".into(), quality_gates());
    bundle.insert("config".into(), Value::Object(report_config()));
    Value::Object(bundle)
}

fn now_iso() -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    cairn_build::timefmt::iso_from_epoch(now.as_secs() as i64, now.subsec_micros())
}

fn open_report_conn(db: &str) -> Option<Connection> {
    if !Path::new(db).exists() {
        return None;
    }
    cairn_store::conn::open_read_only(Path::new(db)).ok()
}

fn gather_recent_errors(conn: Option<&Connection>) -> Value {
    let mut events: Vec<Value> = Vec::new();
    let mut tool_errors: Vec<Value> = Vec::new();
    let Some(conn) = conn else {
        return json!({"events": events, "tool_errors": tool_errors});
    };
    let placeholders = ERROR_EVENTS
        .iter()
        .map(|_| "?")
        .collect::<Vec<_>>()
        .join(",");
    if let Ok(mut stmt) = conn.prepare(&format!(
        "SELECT ts, name, session_id, attrs FROM events \
         WHERE name IN ({placeholders}) ORDER BY ts DESC LIMIT ?"
    )) {
        let mut params: Vec<&dyn rusqlite::ToSql> = ERROR_EVENTS
            .iter()
            .map(|name| name as &dyn rusqlite::ToSql)
            .collect();
        params.push(&REPORT_LIMIT);
        if let Ok(rows) = stmt.query_map(params.as_slice(), |row| {
            Ok((
                value_of(row, 0)?,
                row.get::<_, String>(1)?,
                value_of(row, 2)?,
                value_of(row, 3)?,
            ))
        }) {
            for (ts, name, session_id, attrs) in rows.flatten() {
                events.push(Value::Object(scrub_strings(
                    [
                        ("ts", ts),
                        ("name", json!(name)),
                        ("session_id", session_id),
                        ("attrs", attrs),
                    ]
                    .into_iter()
                    .map(|(k, v)| (k.to_string(), v))
                    .collect(),
                )));
            }
        }
    }
    let tool_rows = conn.prepare(
        "SELECT tool_name, invoked_at, duration_ms, status, error_message \
         FROM tool_metrics WHERE status = 'error' ORDER BY invoked_at DESC LIMIT ?",
    );
    if let Ok(mut stmt) = tool_rows {
        let rows = stmt.query_map([REPORT_LIMIT], |row| {
            Ok((
                row.get::<_, String>(0)?,
                value_of(row, 1)?,
                value_of(row, 2)?,
                row.get::<_, String>(3)?,
                value_of(row, 4)?,
            ))
        });
        let rows: Vec<_> = rows.into_iter().flatten().flatten().collect();
        for (tool_name, invoked_at, duration_ms, status, error_message) in rows {
            tool_errors.push(Value::Object(scrub_strings(
                [
                    ("tool_name", json!(tool_name)),
                    ("invoked_at", invoked_at),
                    ("duration_ms", duration_ms),
                    ("status", json!(status)),
                    ("error_message", error_message),
                ]
                .into_iter()
                .map(|(k, v)| (k.to_string(), v))
                .collect(),
            )));
        }
    }
    json!({"events": events, "tool_errors": tool_errors})
}

fn value_of(row: &rusqlite::Row, idx: usize) -> rusqlite::Result<Value> {
    Ok(match row.get_ref(idx)? {
        rusqlite::types::ValueRef::Null => Value::Null,
        rusqlite::types::ValueRef::Integer(n) => json!(n),
        rusqlite::types::ValueRef::Real(f) => json!(f),
        rusqlite::types::ValueRef::Text(t) => {
            json!(String::from_utf8_lossy(t).into_owned())
        }
        rusqlite::types::ValueRef::Blob(b) => json!(b.to_vec()),
    })
}

fn report_resolution(conn: Option<&Connection>) -> Value {
    let Some(conn) = conn else {
        return json!({});
    };
    let Ok(mut stmt) = conn.prepare(
        "SELECT f.language AS language, \
                SUM(e.resolution = 'exact') AS exact_n, \
                SUM(e.resolution = 'ambiguous') AS ambiguous_n, \
                SUM(e.resolution = 'unresolved') AS unresolved_n \
         FROM files f \
         LEFT JOIN symbols s ON s.file_id = f.id \
         LEFT JOIN edges e ON e.source_id = s.id \
              AND e.kind IN ('calls','references') \
              AND e.resolution IN ('exact','ambiguous','unresolved') \
         GROUP BY f.language ORDER BY f.language",
    ) else {
        return json!({});
    };
    let Ok(rows) = stmt.query_map([], |row| {
        Ok((
            row.get::<_, Option<String>>(0)?,
            row.get::<_, Option<i64>>(1)?,
            row.get::<_, Option<i64>>(2)?,
            row.get::<_, Option<i64>>(3)?,
        ))
    }) else {
        return json!({});
    };
    let mut obj = Map::new();
    for (language, exact_n, ambiguous_n, unresolved_n) in rows.flatten() {
        let language = language.unwrap_or_default();
        let exact = exact_n.unwrap_or(0);
        let ambiguous = ambiguous_n.unwrap_or(0);
        let unresolved = unresolved_n.unwrap_or(0);
        let total = exact + ambiguous + unresolved;
        obj.insert(
            language,
            json!({
                "exact": exact,
                "ambiguous": ambiguous,
                "unresolved": unresolved,
                "total": total,
                "exact_pct": if total > 0 { json!(exact as f64 / total as f64) } else { json!(0.0) },
                "ambiguous_pct": if total > 0 { json!(ambiguous as f64 / total as f64) } else { json!(0.0) },
                "unresolved_pct": if total > 0 { json!(unresolved as f64 / total as f64) } else { json!(0.0) },
            }),
        );
    }
    Value::Object(obj)
}

fn quality_gates() -> Value {
    let Some(root) = source_root() else {
        return json!({
            "audit_status": {"status": "unavailable"},
            "comment_style": {"status": "unavailable"},
        });
    };
    json!({
        "audit_status": audit_quality_gate(&root),
        "comment_style": comment_quality_gate(&root),
    })
}

fn source_root() -> Option<PathBuf> {
    let cwd = std::env::current_dir().ok()?;
    let cwd = cwd.canonicalize().unwrap_or(cwd);
    let mut candidate: Option<&Path> = Some(&cwd);
    while let Some(dir) = candidate {
        if dir.join("rust/crates/xtask/Cargo.toml").is_file() {
            return Some(dir.to_path_buf());
        }
        candidate = dir.parent();
    }
    None
}

fn audit_quality_gate(root: &Path) -> Value {
    match collect_audit_status(root) {
        Ok(status) => {
            let mut by_priority = Map::new();
            for priority in ["P0", "P1", "P2", "P3"] {
                by_priority.insert(priority.into(), json!(status.remaining[priority]));
            }
            json!({
                "total": status.total,
                "remaining": status.remaining_total,
                "by_priority": Value::Object(by_priority),
            })
        }
        Err(_) => json!({"status": "error"}),
    }
}

#[derive(Debug)]
struct AuditStatus {
    remaining: BTreeMap<&'static str, i64>,
    total: i64,
    remaining_total: i64,
}

/// Remaining-vs-total audit-finding counts from docs/audits (audit_status port).
fn collect_audit_status(root: &Path) -> Result<AuditStatus, String> {
    let audits_dir = root.join("docs").join("audits");
    let mut documents: BTreeMap<String, PathBuf> = BTreeMap::new();
    let mut sidecars: BTreeMap<String, PathBuf> = BTreeMap::new();
    let entries = std::fs::read_dir(&audits_dir).map_err(|_| "no audit documents".to_string())?;
    for entry in entries.flatten() {
        let path = entry.path();
        let Some(name) = path.file_name().and_then(|n| n.to_str()) else {
            continue;
        };
        if let Some(stem) = name.strip_suffix(".md") {
            documents.insert(stem.to_string(), path);
        } else if let Some(stem) = name.strip_suffix(".status.json") {
            sidecars.insert(stem.to_string(), path);
        }
    }
    if documents.is_empty() && sidecars.is_empty() {
        return Err("no audit documents under docs/audits".into());
    }
    let priorities = ["P0", "P1", "P2", "P3"];
    let mut total_by_priority: BTreeMap<&'static str, i64> =
        priorities.iter().map(|p| (*p, 0)).collect();
    let mut fixed_by_priority: BTreeMap<&'static str, i64> =
        priorities.iter().map(|p| (*p, 0)).collect();
    let mut stems: Vec<String> = documents.keys().chain(sidecars.keys()).cloned().collect();
    stems.sort();
    stems.dedup();
    for stem in &stems {
        let doc_rel = format!("docs/audits/{stem}.md");
        let side_rel = format!("docs/audits/{stem}.status.json");
        let doc_path = documents
            .get(stem)
            .ok_or_else(|| format!("missing audit document: {doc_rel}"))?;
        let side_path = sidecars
            .get(stem)
            .ok_or_else(|| format!("missing status sidecar: {side_rel}"))?;
        let text =
            std::fs::read_to_string(doc_path).map_err(|_| format!("{doc_rel}: unreadable"))?;
        let findings = parse_audit_index(&text, &doc_rel)?;
        let known: BTreeMap<&str, &str> = findings
            .iter()
            .flat_map(|(priority, ids)| ids.iter().map(move |id| (id.as_str(), *priority)))
            .collect();
        let sidecar_text =
            std::fs::read_to_string(side_path).map_err(|_| format!("{side_rel}: unreadable"))?;
        for finding_id in load_audit_sidecar(&sidecar_text, &side_rel, &known)? {
            let priority = known[finding_id.as_str()];
            *fixed_by_priority.get_mut(priority).expect("p0-p3") += 1;
        }
        for (priority, ids) in &findings {
            *total_by_priority.get_mut(priority).expect("p0-p3") += ids.len() as i64;
        }
    }
    let mut remaining: BTreeMap<&'static str, i64> = BTreeMap::new();
    let mut total = 0;
    let mut remaining_total = 0;
    for priority in priorities {
        let t = total_by_priority[priority];
        let fixed = fixed_by_priority[priority];
        remaining.insert(priority, t - fixed);
        total += t;
        remaining_total += t - fixed;
    }
    Ok(AuditStatus {
        remaining,
        total,
        remaining_total,
    })
}

fn parse_audit_index(text: &str, rel: &str) -> Result<Vec<(&'static str, Vec<String>)>, String> {
    let index_heading = Regex::new(r"^## Findings index\b").expect("static");
    let priority_heading = Regex::new(r"^### (P[0-3])\b.*\((\d+)\)\s*$").expect("static");
    let separator_cell = Regex::new(r"^:?-+:?$").expect("static");
    let finding_id = Regex::new(r"^(?:C|Q|SY|S)\d+$").expect("static");
    let lines: Vec<&str> = text.lines().collect();
    let start = lines
        .iter()
        .position(|line| index_heading.is_match(line.trim_end()))
        .ok_or_else(|| format!("{rel}: no findings-index section"))?
        + 1;
    let end = lines[start..]
        .iter()
        .position(|line| line.starts_with("## "))
        .map(|offset| start + offset)
        .unwrap_or(lines.len());
    let mut findings: Vec<(&'static str, Vec<String>)> = Vec::new();
    let mut declared: BTreeMap<&'static str, i64> = BTreeMap::new();
    let mut heading_counts: BTreeMap<&'static str, i64> = BTreeMap::new();
    let mut seen: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
    let mut current: Option<&'static str> = None;
    for line in &lines[start..end] {
        if let Some(captures) = priority_heading.captures(line.trim_end()) {
            let priority: &'static str = match &captures[1] {
                "P0" => "P0",
                "P1" => "P1",
                "P2" => "P2",
                _ => "P3",
            };
            let declared_count: i64 = captures[2].parse().unwrap_or(-1);
            declared.insert(priority, declared_count);
            *heading_counts.entry(priority).or_insert(0) += 1;
            findings.retain(|(p, _)| *p != priority);
            findings.push((priority, Vec::new()));
            current = Some(priority);
        } else if line.starts_with('#') {
            current = None;
        } else if let Some(priority) = current {
            let trimmed = line.trim();
            if let Some(body) = trimmed.strip_prefix('|').and_then(|b| b.strip_suffix('|')) {
                let cells: Vec<String> = body.split('|').map(|c| c.trim().to_string()).collect();
                if cells.is_empty() || cells[0].eq_ignore_ascii_case("id") {
                    continue;
                }
                if cells
                    .iter()
                    .filter(|c| !c.is_empty())
                    .all(|c| separator_cell.is_match(c))
                {
                    continue;
                }
                let id = &cells[0];
                if !finding_id.is_match(id) {
                    return Err(format!(
                        "{rel}: invalid finding ID '{id}' in {priority} index"
                    ));
                }
                if !seen.insert(id.clone()) {
                    return Err(format!("{rel}: duplicate finding ID {id} in index"));
                }
                findings
                    .iter_mut()
                    .find(|(p, _)| *p == priority)
                    .expect("heading pushed the bucket")
                    .1
                    .push(id.clone());
            }
        }
    }
    for priority in ["P0", "P1", "P2", "P3"] {
        let count = heading_counts.get(priority).copied().unwrap_or(0);
        if count != 1 {
            return Err(format!(
                "{rel}: {priority} findings-index heading occurs {count} times"
            ));
        }
        let rows = findings
            .iter()
            .find(|(p, _)| *p == priority)
            .map(|(_, ids)| ids.len() as i64)
            .unwrap_or(0);
        let declared = declared.get(priority).copied().unwrap_or(0);
        if rows != declared {
            return Err(format!(
                "{rel}: {priority} declares {declared} findings, index has {rows}"
            ));
        }
    }
    Ok(findings)
}

fn load_audit_sidecar(
    text: &str,
    rel: &str,
    known: &BTreeMap<&str, &str>,
) -> Result<Vec<String>, String> {
    let data: Value = serde_json::from_str(text).map_err(|_| format!("{rel}: invalid JSON"))?;
    let Some(obj) = data.as_object() else {
        return Err(format!("{rel}: top level must be a JSON object"));
    };
    if obj.get("schema_version").and_then(Value::as_i64) != Some(1) {
        return Err(format!(
            "{rel}: unsupported schema_version {}",
            obj.get("schema_version")
                .map(|v| v.to_string())
                .unwrap_or_default()
        ));
    }
    let Some(fixed) = obj.get("fixed").and_then(Value::as_array) else {
        return Err(format!("{rel}: fixed must be a list of finding IDs"));
    };
    let mut ids: Vec<String> = Vec::new();
    for item in fixed {
        let Some(id) = item.as_str() else {
            return Err(format!("{rel}: fixed must be a list of finding IDs"));
        };
        if ids.iter().any(|seen| seen == id) {
            return Err(format!("{rel}: duplicate fixed finding ID"));
        }
        ids.push(id.to_string());
    }
    if let Some(first) = ids.iter().find(|id| !known.contains_key(id.as_str())) {
        return Err(format!("{rel}: unknown fixed finding ID {first}"));
    }
    Ok(ids)
}

fn comment_quality_gate(root: &Path) -> Value {
    // The checker is the checkout's own xtask; no cargo means no gate.
    let Ok(output) = Command::new("cargo")
        .args(["xtask", "comment-style", "--json", "--root"])
        .arg(root)
        .current_dir(root)
        .output()
    else {
        return json!({"status": "unavailable"});
    };
    let parsed: Option<Value> = Some(output)
        .filter(|o| o.status.success())
        .and_then(|o| serde_json::from_slice(&o.stdout).ok());
    match parsed {
        Some(status) if status.get("ok").and_then(Value::as_bool) == Some(true) => {
            json!({"remaining": status.get("remaining").cloned().unwrap_or(Value::Null)})
        }
        _ => json!({"status": "error"}),
    }
}

fn sidecar_python() -> Option<PathBuf> {
    cairn_query::sidecar::python()
}

fn report_config() -> Map<String, Value> {
    let env_or = |name: &str, default: &str| {
        std::env::var(name)
            .ok()
            .map(Value::String)
            .unwrap_or_else(|| json!(default))
    };
    let mut config = Map::new();
    config.insert("workers".into(), env_or("CAIRN_WORKERS", "<unset>"));
    config.insert("read_only".into(), env_or("CAIRN_READ_ONLY", "<unset>"));
    config.insert("fusion".into(), env_or("CAIRN_FUSION", "<unset>"));
    config.insert(
        "ann_backend".into(),
        env_or("CAIRN_ANN_BACKEND", "<unset (=sqlite-vec)>"),
    );
    config.insert(
        "embed_backend".into(),
        json!(cmds_doctor::knob_source("CAIRN_EMBED_BACKEND", "<unset (=local)>").0),
    );
    config.insert(
        "telemetry".into(),
        env_or("CAIRN_TELEMETRY", "<unset (=on)>"),
    );
    config.insert(
        "log_level".into(),
        env_or("CAIRN_LOG_LEVEL", "<unset (=WARNING)>"),
    );
    scrub_strings(config)
}

fn scrub_strings(map: Map<String, Value>) -> Map<String, Value> {
    map.into_iter()
        .map(|(key, value)| {
            (
                key,
                match value {
                    Value::String(text) => Value::String(scrub(&text)),
                    other => other,
                },
            )
        })
        .collect()
}

fn scrub(text: &str) -> String {
    redact_paths(&privacy::strip_private_data(text))
}

mod privacy {
    //! memory.privacy.strip_private_data port (secret scrub for report fields).

    use regex::Regex;

    const SECRET_PATTERNS: [&str; 14] = [
        r#"(?i)(?:api[_-]?key|secret|token|password|credential|auth)[\s]*[=:]\s*["']?[A-Za-z0-9_\-/.+]{20,}["']?"#,
        r"(?i)Bearer\s+[A-Za-z0-9._\-+/=]{20,}",
        r"(?i)sk-proj-[A-Za-z0-9\-_]{20,}",
        r"(?i)(?:sk|pk|rk|ak)-[A-Za-z0-9][A-Za-z0-9\-_]{19,}",
        r"(?i)sk-ant-[A-Za-z0-9\-_]{20,}",
        r"(?i)gh[pus]_[A-Za-z0-9]{36,}",
        r"(?i)github_pat_[A-Za-z0-9_]{22,}",
        r"(?i)xoxb-[A-Za-z0-9\-]+",
        r"AKIA[0-9A-Z]{16}",
        r"AIza[A-Za-z0-9\-_]{35}",
        r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
        r"npm_[A-Za-z0-9]{36}",
        r"(?i)glpat-[A-Za-z0-9\-_]{20,}",
        r"(?i)dop_v1_[A-Za-z0-9]{64}",
    ];

    pub fn strip_private_data(text: &str) -> String {
        let tag = Regex::new(r"(?is)<private>.*?</private>").expect("static");
        let uri = Regex::new(
            r"(?i)\b(postgres(?:ql)?|mysql|mariadb|rediss?|mongodb(?:\+srv)?|amqps?|https?|ftps?)://[^/@\s:]*:[^/@\s]+@",
        )
        .expect("static");
        let mut result = tag.replace_all(text, "[REDACTED]").into_owned();
        result = uri
            .replace_all(&result, "${1}://[REDACTED_SECRET]@")
            .into_owned();
        for pattern in SECRET_PATTERNS {
            let regex = Regex::new(pattern).expect("static");
            result = regex.replace_all(&result, "[REDACTED_SECRET]").into_owned();
        }
        result
    }
}

/// Collapse absolute filesystem paths to `[PATH]/<basename>` (the python
/// lookbehind regexes, hand-rolled: rust regex has no lookaround).
fn redact_paths(text: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let is_path_char = |c: char| !c.is_whitespace() && c != '"' && c != '\'';
    let mut out = String::new();
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        let prev = if i > 0 { Some(chars[i - 1]) } else { None };
        let posix_start = c == '/'
            && prev.is_none_or(|p| {
                !(p.is_alphanumeric() || p == '_' || p == ':' || p == '/' || p == '.' || p == '-')
            });
        let home_start = c == '~'
            && chars.get(i + 1) == Some(&'/')
            && prev.is_none_or(|p| !(p.is_alphanumeric() || p == '_'));
        if (posix_start || home_start)
            && let Some(end) = match_path(&chars, i, is_path_char)
        {
            let matched: String = chars[i..end].iter().collect();
            out.push_str(&redact_match(&matched));
            i = end;
        } else {
            out.push(c);
            i += 1;
        }
    }
    out
}

/// End index of a `/seg/.../tail` or `~/tail` match starting at `start`.
fn match_path(chars: &[char], start: usize, is_path_char: impl Fn(char) -> bool) -> Option<usize> {
    if chars[start] == '~' {
        let mut end = start + 2;
        while end < chars.len() && is_path_char(chars[end]) {
            end += 1;
        }
        return (end > start + 2).then_some(end);
    }
    let mut i = start;
    let mut segments = 0;
    loop {
        let mut j = i + 1;
        let segment_start = j;
        while j < chars.len() && is_path_char(chars[j]) && chars[j] != '/' {
            j += 1;
        }
        if j == segment_start {
            break;
        }
        if j < chars.len() && chars[j] == '/' {
            segments += 1;
            i = j;
        } else {
            break;
        }
    }
    if segments == 0 {
        return None;
    }
    let mut end = i + 1;
    while end < chars.len() && is_path_char(chars[end]) {
        end += 1;
    }
    Some(end)
}

fn redact_match(matched: &str) -> String {
    // Python takes the last split segment verbatim: a trailing slash leaves
    // an empty leaf, collapsing the whole match to bare "[PATH]".
    let leaf = matched.split(['\\', '/']).next_back().unwrap_or("");
    if leaf.is_empty() {
        "[PATH]".into()
    } else {
        format!("[PATH]/{leaf}")
    }
}

fn render_report(bundle: &Value) -> String {
    let mut lines: Vec<String> = vec![
        "cairn report — redacted diagnostic bundle".into(),
        "# Secrets scrubbed via memory.privacy.strip_private_data; absolute paths collapsed to [PATH]/<basename>. Safe to paste into a GitHub issue.".into(),
        format!("generated: {}", bundle["generated_at"].as_str().unwrap_or("")),
        String::new(),
    ];
    let v = &bundle["versions"];
    let field = |key: &str| -> String {
        match &v[key] {
            Value::Null => "None".into(),
            Value::String(s) => s.clone(),
            other => other.to_string(),
        }
    };
    lines.push("## Versions".into());
    lines.push(format!("cairn: {}", field("cairn")));
    lines.push(format!("python: {}", field("python")));
    lines.push(format!("platform: {}", field("platform")));
    lines.push(format!("sqlite: {}", field("sqlite")));
    lines.push(format!(
        "db_schema_user_version: {}",
        field("db_schema_user_version")
    ));
    lines.push(String::new());
    lines.push("## Doctor".into());
    for r in bundle["doctor"].as_array().unwrap_or(&Vec::new()) {
        lines.push(format!(
            "{:<4} {}: {}",
            r["status"].as_str().unwrap_or(""),
            r["name"].as_str().unwrap_or(""),
            r["detail"].as_str().unwrap_or("")
        ));
        if r["hint"].is_string() {
            lines.push(format!("      hint: {}", r["hint"].as_str().unwrap_or("")));
        }
    }
    lines.push(String::new());
    let re_ = &bundle["recent_errors"];
    let events = re_["events"].as_array().cloned().unwrap_or_default();
    lines.push(format!("## Recent error events ({})", events.len()));
    if events.is_empty() {
        lines.push("  none".into());
    } else {
        for e in &events {
            let attrs = e["attrs"].as_str().unwrap_or("");
            lines.push(format!(
                "  {} {} {attrs}",
                fmt_ts(&e["ts"]),
                e["name"].as_str().unwrap_or("")
            ));
        }
    }
    lines.push(String::new());
    let tool_errors = re_["tool_errors"].as_array().cloned().unwrap_or_default();
    lines.push(format!("## Recent tool errors ({})", tool_errors.len()));
    if tool_errors.is_empty() {
        lines.push("  none".into());
    } else {
        for t in &tool_errors {
            lines.push(format!(
                "  {} {} {}",
                fmt_ts(&t["invoked_at"]),
                t["tool_name"].as_str().unwrap_or(""),
                t["error_message"].as_str().unwrap_or("")
            ));
        }
    }
    lines.push(String::new());
    lines.push("## Resolution".into());
    if let Some(resolution) = bundle["resolution"].as_object()
        && !resolution.is_empty()
    {
        for (language, counts) in resolution {
            let pct = |key: &str| format!("{:.1}%", counts[key].as_f64().unwrap_or(0.0) * 100.0);
            lines.push(format!(
                "{}: {} exact ({}), {} ambiguous ({}), {} unresolved ({})",
                language,
                counts["exact"],
                pct("exact_pct"),
                counts["ambiguous"],
                pct("ambiguous_pct"),
                counts["unresolved"],
                pct("unresolved_pct"),
            ));
        }
    } else {
        lines.push("  none".into());
    }
    lines.push(String::new());
    lines.push("## Quality gates".into());
    let quality = &bundle["quality_gates"];
    let audit = &quality["audit_status"];
    if audit.get("status").is_some() {
        lines.push(format!(
            "audit_status: {}",
            audit["status"].as_str().unwrap_or("error")
        ));
    } else {
        lines.push(format!(
            "audit_status total: {}/{} remaining",
            audit["remaining"], audit["total"]
        ));
        if let Some(by_priority) = audit["by_priority"].as_object() {
            for (priority, remaining) in by_priority {
                lines.push(format!("audit_status {priority}: {remaining} remaining"));
            }
        }
    }
    let comment = &quality["comment_style"];
    if comment.get("status").is_some() {
        lines.push(format!(
            "comment_style: {}",
            comment["status"].as_str().unwrap_or("error")
        ));
    } else {
        lines.push(format!("comment_style remaining: {}", comment["remaining"]));
    }
    lines.push(String::new());
    lines.push("## Config".into());
    if let Some(config) = bundle["config"].as_object() {
        for (key, value) in config {
            let rendered = match value {
                Value::Null => "None".into(),
                Value::String(s) => s.clone(),
                other => other.to_string(),
            };
            lines.push(format!("{key}: {rendered}"));
        }
    }
    lines.join("\n")
}

// --- upgrade -----------------------------------------------------------------

pub struct UpgradeArgs {
    pub check: bool,
}

pub fn run_upgrade(args: UpgradeArgs) -> ExitCode {
    let current = env!("CARGO_PKG_VERSION");
    let latest = pypi_latest();
    let Some(latest) = latest else {
        if args.check {
            display::info(&format!("cairn-intel {current} (could not reach PyPI)"));
        } else {
            display::warning(
                "Cannot check for upgrades (PyPI unreachable). \
                 Install manually: pip install --upgrade cairn-intel",
            );
        }
        return ExitCode::SUCCESS;
    };
    if args.check {
        display::info(&format!("cairn-intel {current} (latest: {latest})"));
        return ExitCode::SUCCESS;
    }
    if is_up_to_date(current, &latest) {
        display::success(&format!("cairn-intel {current} (already up to date)"));
        return ExitCode::SUCCESS;
    }
    let method = detect_install_method();
    display::info(&format!(
        "Upgrading cairn-intel {current} -> {latest} (via {method})"
    ));
    if !reinstall(&method, &latest) {
        return ExitCode::FAILURE;
    }
    ExitCode::SUCCESS
}

/// Latest published version via the python stdlib HTTPS client; the binary
/// ships no TLS stack, so the probe rides the interpreter every install has.
fn pypi_latest() -> Option<String> {
    let python = sidecar_python()?;
    let script = "import json, urllib.request; \
                  req = urllib.request.Request('https://pypi.org/pypi/cairn-intel/json', \
                  headers={'Accept': 'application/json'}); \
                  print(json.load(urllib.request.urlopen(req, timeout=5))['info']['version'])";
    let output = Command::new(&python).arg("-c").arg(script).output().ok()?;
    if !output.status.success() {
        return None;
    }
    let version = String::from_utf8_lossy(&output.stdout).trim().to_string();
    (!version.is_empty()).then_some(version)
}

fn is_up_to_date(current: &str, latest: &str) -> bool {
    let parse = |v: &str| -> Vec<u64> {
        v.trim_start_matches('v')
            .split('.')
            .map(|part| part.split(['-', '+']).next().unwrap_or(""))
            .map(|part| part.parse::<u64>().unwrap_or(0))
            .collect()
    };
    let (c, l) = (parse(current), parse(latest));
    if c == l {
        return true;
    }
    let width = c.len().max(l.len());
    let pad = |v: &[u64]| {
        let mut out = v.to_vec();
        out.resize(width, 0);
        out
    };
    let (c, l) = (pad(&c), pad(&l));
    if c == l {
        return true;
    }
    c > l
}

/// The `.venv` root this binary runs from, mirroring python's
/// pkg_root/.venv-is-an-ancestor-of-sys.executable check.
fn exe_venv_root() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    exe.ancestors()
        .find(|path| path.file_name().is_some_and(|n| n == ".venv"))
        .map(Path::to_path_buf)
}

/// Source-tree build debris of the python package (build/, dist/, *.egg-info
/// under the cairn package root), via the sidecar interpreter.
fn stale_artifacts() -> Vec<PathBuf> {
    let Some(python) = sidecar_python() else {
        return Vec::new();
    };
    let script = "from importlib.util import find_spec; \\
                  spec = find_spec('cairn'); \\
                  print(spec.origin if spec and spec.origin else '')";
    let Ok(output) = Command::new(&python).arg("-c").arg(script).output() else {
        return Vec::new();
    };
    if !output.status.success() {
        return Vec::new();
    }
    let origin = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if origin.is_empty() {
        return Vec::new();
    }
    // python's _pkg_root: three levels above the cairn package.
    let Some(pkg_root) = Path::new(&origin).ancestors().nth(3) else {
        return Vec::new();
    };
    let mut found = Vec::new();
    for name in ["build", "dist"] {
        let path = pkg_root.join(name);
        if path.exists() {
            found.push(path);
        }
    }
    if let Ok(entries) = std::fs::read_dir(pkg_root) {
        let mut egg_info: Vec<PathBuf> = entries
            .flatten()
            .map(|entry| entry.path())
            .filter(|path| path.extension().is_some_and(|e| e == "egg-info"))
            .collect();
        egg_info.sort();
        found.extend(egg_info);
    }
    found
}

fn detect_install_method() -> String {
    // `uv tool list` vs `pipx list`: only uv nests the listing under `tool`.
    for (tool, list_args) in [
        ("uv", ["tool", "list"].as_slice()),
        ("pipx", ["list"].as_slice()),
    ] {
        if let Ok(output) = Command::new(tool).args(list_args).output()
            && output.status.success()
            && String::from_utf8_lossy(&output.stdout).contains("cairn-intel")
        {
            return tool.into();
        }
    }
    if exe_venv_root().is_some() {
        return "venv".into();
    }
    if let Ok(exe) = std::env::current_exe() {
        let exe = exe.to_string_lossy();
        if exe.contains("venv") || exe.contains("virtualenv") || exe.contains(".local") {
            return "pip".into();
        }
    }
    "unknown".into()
}

fn reinstall(method: &str, version: &str) -> bool {
    let spec = format!("cairn-intel=={version}");
    let cmd: Vec<String> = match method {
        "uv" => vec![
            "uv".into(),
            "tool".into(),
            "install".into(),
            "--force".into(),
            spec,
        ],
        "pipx" => vec!["pipx".into(), "install".into(), "--force".into(), spec],
        "pip" | "venv" => {
            let python = sidecar_python()
                .map(|p| p.to_string_lossy().into_owned())
                .unwrap_or_else(|| "python3".into());
            vec![
                python,
                "-m".into(),
                "pip".into(),
                "install".into(),
                "--upgrade".into(),
                spec,
            ]
        }
        _ => {
            display::warning(&format!(
                "Cannot auto-upgrade (unknown install method). \
                 Run manually: pip install --upgrade {spec}"
            ));
            return false;
        }
    };
    display::dim(&format!("Running: {}", cmd.join(" ")));
    match Command::new(&cmd[0]).args(&cmd[1..]).output() {
        Ok(output) if output.status.success() => {
            display::success(&format!("Upgraded cairn-intel -> {version}"));
            true
        }
        Ok(output) => {
            installer_failure(&cmd, &output.stdout, &output.stderr);
            false
        }
        Err(_) => {
            installer_failure(&cmd, &[], &[]);
            false
        }
    }
}

fn installer_failure(cmd: &[String], stdout: &[u8], stderr: &[u8]) {
    let _ = std::io::stdout().write_all(stdout);
    let _ = std::io::stderr().write_all(stderr);
    display::warning(&format!(
        "Upgrade failed. The full installer output is above; \
         to retry manually: {}",
        cmd.join(" ")
    ));
}

// --- uninstall ---------------------------------------------------------------

pub struct UninstallArgs {
    pub full: bool,
    pub agents_only: bool,
    pub hooks_only: bool,
    pub graph_only: bool,
    pub package_only: bool,
    pub clients: Vec<String>,
    pub scope: String,
    pub workspace: Option<String>,
    pub dry_run: bool,
    pub yes: bool,
}

/// One uninstall prompt; a closed stdin sets `aborted` (click's Abort
/// contract), an answered "n" only skips the step.
fn confirm_step(auto_yes: bool, step: &str, aborted: &mut bool) -> bool {
    if auto_yes {
        return true;
    }
    print!("  Remove {step}? [y/N]: ");
    let _ = std::io::stdout().flush();
    let mut answer = String::new();
    let answer = match std::io::stdin().read_line(&mut answer) {
        Ok(0) => ConfirmAnswer::Eof,
        Ok(_) => ConfirmAnswer::from_line(Some(&answer)),
        Err(_) => ConfirmAnswer::Eof,
    };
    if let ConfirmAnswer::Eof = answer {
        *aborted = true;
    }
    matches!(answer, ConfirmAnswer::Yes)
}

#[derive(Debug, PartialEq, Eq)]
enum ConfirmAnswer {
    Yes,
    No,
    Eof,
}

impl ConfirmAnswer {
    /// The prompt decision for one stdin line; None/empty means closed stdin.
    fn from_line(line: Option<&str>) -> Self {
        match line {
            None | Some("") => Self::Eof,
            // click's BoolParamType true set.
            Some(text)
                if matches!(
                    text.trim().to_lowercase().as_str(),
                    "y" | "yes" | "t" | "true" | "1" | "on"
                ) =>
            {
                Self::Yes
            }
            Some(_) => Self::No,
        }
    }
}

pub fn run_uninstall(args: UninstallArgs) -> ExitCode {
    if args.full && (args.agents_only || args.hooks_only || args.graph_only || args.package_only) {
        return crate::cmds_core::usage_error(
            "--full cannot be combined with --agents-only/--hooks-only/--graph-only/--package-only.",
        );
    }
    if !["workspace", "global", "all"].contains(&args.scope.as_str()) {
        return crate::cmds_core::usage_error(&format!(
            "Invalid value for '--scope': '{}'",
            args.scope
        ));
    }
    for client in &args.clients {
        if !(agent_install::CLIENTS.contains(&client.as_str()) || client == "all") {
            return crate::cmds_core::usage_error(&format!(
                "Invalid value for '--client <CLIENTS>': '{client}'"
            ));
        }
    }
    let ws = match &args.workspace {
        Some(ws) => ws.clone(),
        None => std::env::var("CAIRN_WORKSPACE").unwrap_or_else(|_| {
            std::env::current_dir()
                .map(|cwd| cwd.to_string_lossy().into_owned())
                .unwrap_or_default()
        }),
    };
    let any_only = args.agents_only || args.hooks_only || args.graph_only || args.package_only;
    let do_agents = args.full || args.agents_only || !any_only;
    let do_hooks = args.full || args.hooks_only || !any_only;
    let do_graph = args.full || args.graph_only || !any_only;
    let do_package = args.full || args.package_only || !any_only;

    println!();
    println!("Cairn Uninstaller");
    if args.dry_run {
        println!("(dry-run: nothing will be deleted)");
    }
    println!();

    let mut prompt_aborted = false;

    let agents_scope = if args.full {
        "all"
    } else {
        args.scope.as_str()
    };
    if do_agents
        && (args.dry_run
            || confirm_step(
                args.yes || args.full,
                "agent integrations",
                &mut prompt_aborted,
            ))
    {
        remove_agents(&ws, &args.clients, args.dry_run, agents_scope);
        println!();
    }
    if prompt_aborted {
        eprintln!("Aborted!");
        return ExitCode::FAILURE;
    }
    if do_hooks
        && (args.dry_run || confirm_step(args.yes || args.full, "git hooks", &mut prompt_aborted))
    {
        remove_hooks(&ws, args.dry_run);
        println!();
    }
    if prompt_aborted {
        eprintln!("Aborted!");
        return ExitCode::FAILURE;
    }
    if do_graph
        && (args.dry_run
            || confirm_step(
                args.yes || args.full,
                "graph and knowledge data",
                &mut prompt_aborted,
            ))
    {
        remove_store(&ws, args.full, args.dry_run);
        println!();
    }
    if prompt_aborted {
        eprintln!("Aborted!");
        return ExitCode::FAILURE;
    }
    if do_package
        && (args.dry_run
            || confirm_step(args.yes || args.full, "cairn binary", &mut prompt_aborted))
    {
        remove_binary(args.dry_run);
        println!();
    }
    if prompt_aborted {
        eprintln!("Aborted!");
        return ExitCode::FAILURE;
    }
    println!();
    if args.dry_run {
        println!("Dry-run complete — nothing was deleted");
    } else {
        println!("Uninstall complete");
    }
    println!();
    println!("  Remaining (never removed):");
    println!("    CLAUDE.md, AGENTS.md  — instruction files (created create-if-absent only)");
    println!("    Source code            — your repos are untouched");
    println!();
    println!("  To reinstall: pip install cairn-intel  (or ./scripts/install.sh)");
    ExitCode::SUCCESS
}

fn remove_agents(ws: &str, clients: &[String], dry_run: bool, scope: &str) {
    println!("➜ Agent integrations");
    let workspace = store_paths::resolve_path(Path::new(ws));
    if dry_run {
        let names: Vec<String> = if !clients.is_empty() {
            let selected: std::collections::BTreeSet<&str> = if clients.iter().any(|c| c == "all") {
                agent_install::CLIENTS.iter().copied().collect()
            } else {
                clients.iter().map(String::as_str).collect()
            };
            selected.into_iter().map(String::from).collect()
        } else {
            agent_install::detect_clients(&workspace)
                .into_iter()
                .filter(|d| d.detected)
                .map(|d| d.client.to_string())
                .collect()
        };
        println!("  would: strip cairn wiring (scope: {scope})");
        if names.is_empty() {
            println!("  (no clients detected — nothing to remove)");
            return;
        }
        for client in names {
            println!("    would: strip wiring for {client}");
        }
        println!("  would: remove cross-tool .agents/ copies (workspace scope)");
        return;
    }
    let Ok(report) = agent_install::uninstall(
        &workspace,
        if clients.is_empty() {
            None
        } else {
            Some(clients)
        },
        scope,
    ) else {
        return;
    };
    if report.results.is_empty() {
        println!("  (no clients detected — nothing to remove)");
        return;
    }
    for res in &report.results {
        println!("  {}:", res.client);
        if res.written.is_empty() {
            println!("    (nothing to remove)");
        } else {
            for path in &res.written {
                println!("    removed: {path}");
            }
        }
    }
    if let Some(cross_tool) = &report.cross_tool
        && !cross_tool.written.is_empty()
    {
        println!("  cross-tool (.agents/):");
        for path in &cross_tool.written {
            println!("    removed: {path}");
        }
    }
    println!("  ✓ done");
}

fn remove_hooks(ws: &str, dry_run: bool) {
    println!("➜ Git hooks");
    let workspace = store_paths::resolve_path(Path::new(ws));
    let repos = agent_install::discover_hook_repos(&workspace);
    if dry_run {
        println!(
            "  would scan {} repo(s) for git hooks (post-commit + post-checkout)",
            repos.len()
        );
        return;
    }
    let removed = agent_install::uninstall_git_hooks(&repos);
    if removed.is_empty() {
        println!("  (none found)");
    } else {
        println!("  ✓ removed from {}: {}", removed.len(), removed.join(", "));
    }
}

fn cairn_home_raw() -> PathBuf {
    store_paths::cairn_home().unwrap_or_else(|| PathBuf::from(".cairn"))
}

fn home_has_stores(home: &Path) -> bool {
    let Ok(entries) = std::fs::read_dir(home) else {
        return false;
    };
    for entry in entries.flatten() {
        let name = entry.file_name();
        if name == "workspaces.json" || name == ".DS_Store" {
            continue;
        }
        return true;
    }
    false
}

fn dir_size(path: &Path) -> u64 {
    let mut total = 0;
    let Ok(entries) = std::fs::read_dir(path) else {
        return total;
    };
    for entry in entries.flatten() {
        let Ok(meta) = entry.metadata() else {
            continue;
        };
        if meta.is_dir() {
            total += dir_size(&entry.path());
        } else {
            total += meta.len();
        }
    }
    total
}

fn remove_store(ws: &str, full: bool, dry_run: bool) {
    println!("➜ Graph and knowledge data");
    let workspace = store_paths::resolve_path(Path::new(ws));
    let home = cairn_home_raw();
    let target: Option<(PathBuf, bool)> = if full {
        (home.exists() && home_has_stores(&home)).then_some((home, true))
    } else {
        let store = home.join(store_paths::store_key(&workspace));
        store.exists().then_some((store, false))
    };
    let Some((target, whole_home)) = target else {
        if full {
            println!("  (no cairn store found — nothing to remove)");
        } else {
            println!(
                "  (no cairn store found for workspace {} — nothing to remove)",
                workspace.display()
            );
        }
        return;
    };
    let label = if whole_home {
        let count = std::fs::read_dir(&target)
            .map(|entries| entries.flatten().filter(|e| e.path().is_dir()).count())
            .unwrap_or(0);
        format!(
            "{} (entire cairn home, {count} workspace(s))",
            target.display()
        )
    } else {
        target.display().to_string()
    };
    let size = crate::cmds_core::human_bytes(dir_size(&target) as f64);
    println!("  removing: {label}");
    println!("  size:     {size}");
    if dry_run {
        println!("  would: rm -rf {}", target.display());
        return;
    }
    let _ = std::fs::remove_dir_all(&target);
    if target.exists() {
        println!(
            "  warning: {} could not be fully removed (check permissions); \
             leaving it and its registry entry in place",
            target.display()
        );
        return;
    }
    if !whole_home {
        let reg_path = cairn_home_raw().join("workspaces.json");
        if reg_path.exists() {
            prune_registry(&reg_path, &workspace);
        }
    }
    println!("  ✓ done");
}

fn prune_registry(reg_path: &Path, workspace: &Path) {
    let mut reg = store_paths::load_registry();
    let key = workspace.to_string_lossy().into_owned();
    reg.remove(&key);
    if let Err(e) = store_paths::save_registry(&reg) {
        println!(
            "  warning: registry prune failed ({e}); \
             {} still references the removed store",
            reg_path.display()
        );
    }
}

fn remove_binary(dry_run: bool) {
    println!("➜ cairn binary");
    let installed_via = detect_install_method();
    let stale = stale_artifacts();
    if installed_via == "unknown" {
        if stale.is_empty() {
            println!("  (cairn not found via uv/pipx/pip/venv — nothing to remove)");
            return;
        }
        println!("  installed via: unknown (stale build artifacts only)");
    } else {
        println!("  installed via: {installed_via}");
    }
    for path in &stale {
        println!("  stale: {path}", path = path.display());
    }
    let python = sidecar_python()
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_else(|| "python3".into());
    let venv_root = exe_venv_root();
    if dry_run {
        let cmd: Vec<String> = match installed_via.as_str() {
            "uv" => vec![
                "uv".into(),
                "tool".into(),
                "uninstall".into(),
                "cairn-intel".into(),
            ],
            "pipx" => vec!["pipx".into(), "uninstall".into(), "cairn-intel".into()],
            "pip" => vec![
                python,
                "-m".into(),
                "pip".into(),
                "uninstall".into(),
                "-y".into(),
                "cairn-intel".into(),
            ],
            _ => vec![format!(
                "rm -rf {}",
                venv_root
                    .map(|p| p.display().to_string())
                    .unwrap_or_else(|| "<pkg>/.venv".into())
            )],
        };
        println!("  would: {}", cmd.join(" "));
        for path in &stale {
            println!("  would: rm -rf {path}", path = path.display());
        }
        return;
    }
    match installed_via.as_str() {
        "uv" => {
            let _ = Command::new("uv")
                .args(["tool", "uninstall", "cairn-intel"])
                .status();
        }
        "pipx" => {
            let _ = Command::new("pipx")
                .args(["uninstall", "cairn-intel"])
                .status();
        }
        "pip" => {
            let _ = Command::new(&python)
                .args(["-m", "pip", "uninstall", "-y", "cairn-intel"])
                .status();
        }
        "venv" => {
            if let Some(venv) = venv_root {
                let _ = std::fs::remove_dir_all(venv);
            }
        }
        _ => {}
    }
    for path in &stale {
        let _ = std::fs::remove_dir_all(path);
    }
    println!("  ✓ done");
}

// --- download-reranker --------------------------------------------------------

pub struct DownloadRerankerArgs {
    pub model: Option<String>,
}

const RERANKER_UNAVAILABLE: &str = "download-reranker is unavailable: this build has no native cross-encoder reranker, so semantic_search runs without the rerank stage.";

pub fn run_download_reranker(_args: DownloadRerankerArgs) -> ExitCode {
    eprintln!("{RERANKER_UNAVAILABLE}");
    ExitCode::from(2)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn render_table_matches_rich_non_tty_shape() {
        let expected = "   Build runs    \n\
                        ┏━━━━━━━┳━━━━━━━┓\n\
                        ┃ kind  ┃ count ┃\n\
                        ┡━━━━━━━╇━━━━━━━┩\n\
                        │ build │     1 │\n\
                        └───────┴───────┘";
        assert_eq!(
            render_table(
                Some("Build runs"),
                &["kind", "count"],
                &[vec!["build".to_string(), "1".to_string()]],
            ),
            expected
        );
        let untitled = "┏━━━━━━━┳━━━━━━━┓\n\
                        ┃ site  ┃ count ┃\n\
                        ┡━━━━━━━╇━━━━━━━┩\n\
                        │ alpha │     2 │\n\
                        └───────┴───────┘";
        assert_eq!(
            render_table(
                None,
                &["site", "count"],
                &[vec!["alpha".into(), "2".into()]]
            ),
            untitled
        );
    }

    #[test]
    fn civil_from_days_matches_known_dates() {
        assert_eq!(civil_from_days(0), (1970, 1, 1));
        assert_eq!(civil_from_days(19_000), (2022, 1, 8));
        assert_eq!(epoch_to_display(1_761_704_899.844), "2025-10-29 02:28:19");
    }

    #[test]
    fn redact_paths_collapses_absolute_paths() {
        assert_eq!(
            redact_paths("store not found at /tmp/xyz/missing.kg -- run"),
            "store not found at [PATH]/missing.kg -- run"
        );
        assert_eq!(redact_paths("see ~/notes/todo.txt"), "see [PATH]/todo.txt");
        assert_eq!(
            redact_paths("relative paths/data.yml stay"),
            "relative paths/data.yml stay"
        );
        assert_eq!(redact_paths("https://host/p/a"), "https://host/p/a");
        assert_eq!(redact_paths("a/b/c and /x/y"), "a/b/c and [PATH]/y");
        assert_eq!(redact_paths("at /tmp/x/ end"), "at [PATH] end");
    }

    #[test]
    fn privacy_strip_redacts_secrets() {
        assert_eq!(
            privacy::strip_private_data(
                "token = 'ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345' and <private>x</private>"
            ),
            "[REDACTED_SECRET] and [REDACTED]"
        );
        assert_eq!(
            privacy::strip_private_data("https://user:pass@example.com/"),
            "https://[REDACTED_SECRET]@example.com/"
        );
    }

    fn temp_dir(tag: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "cairn-{tag}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ))
    }

    #[test]
    fn collect_audit_status_errors_without_audits() {
        let dir = temp_dir("audit-empty");
        std::fs::create_dir_all(&dir).unwrap();
        assert!(collect_audit_status(&dir).is_err());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn collect_audit_status_counts_priorities() {
        let dir = temp_dir("audit-ok");
        let audits = dir.join("docs").join("audits");
        std::fs::create_dir_all(&audits).unwrap();
        std::fs::write(
            audits.join("a.md"),
            "## Findings index\n\n### P0 (2)\n\n| id | note |\n|----|------|\n\
             | C1 | one |\n| C2 | two |\n\n### P1 (1)\n\n| id | note |\n|----|------|\n\
             | C3 | x |\n\n### P2 (0)\n\n### P3 (0)\n\n## Other\n",
        )
        .unwrap();
        std::fs::write(
            audits.join("a.status.json"),
            r#"{"schema_version": 1, "fixed": ["C1"]}"#,
        )
        .unwrap();
        let status = collect_audit_status(&dir).expect("parses");
        assert_eq!(status.total, 3);
        assert_eq!(status.remaining_total, 2);
        assert_eq!(status.remaining["P0"], 1);
        assert_eq!(status.remaining["P1"], 1);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn collect_audit_status_rejects_declaration_mismatch() {
        let dir = temp_dir("audit-bad");
        let audits = dir.join("docs").join("audits");
        std::fs::create_dir_all(&audits).unwrap();
        std::fs::write(
            audits.join("a.md"),
            "## Findings index\n\n### P0 (3)\n\n| id |\n|----|\n| C1 |\n\n## Other\n",
        )
        .unwrap();
        std::fs::write(
            audits.join("a.status.json"),
            r#"{"schema_version": 1, "fixed": []}"#,
        )
        .unwrap();
        let err = collect_audit_status(&dir).unwrap_err();
        assert!(err.contains("declares 3 findings"), "{err}");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn is_up_to_date_compares_dotted_versions() {
        assert!(is_up_to_date("0.23.0", "0.23.0"));
        assert!(is_up_to_date("0.24.0", "0.23.9"));
        assert!(is_up_to_date("0.23", "0.23.0"));
        assert!(!is_up_to_date("0.22.9", "0.23.0"));
    }

    #[test]
    fn confirm_answer_maps_lines_and_eof() {
        use ConfirmAnswer as Answer;
        assert_eq!(ConfirmAnswer::from_line(Some("y\n")), Answer::Yes);
        assert_eq!(ConfirmAnswer::from_line(Some("YES\n")), Answer::Yes);
        assert_eq!(ConfirmAnswer::from_line(Some("1\n")), Answer::Yes);
        assert_eq!(ConfirmAnswer::from_line(Some("on\n")), Answer::Yes);
        assert_eq!(ConfirmAnswer::from_line(Some("n\n")), Answer::No);
        assert_eq!(ConfirmAnswer::from_line(Some("no\n")), Answer::No);
        assert_eq!(ConfirmAnswer::from_line(Some("\n")), Answer::No);
        assert_eq!(ConfirmAnswer::from_line(Some("")), Answer::Eof);
        assert_eq!(ConfirmAnswer::from_line(None), Answer::Eof);
    }

    /// A sync over a store whose closure was built must rebuild it: a stale
    /// closure row cannot survive the reindex, or downstream ancestor and
    /// impact queries read pre-edit topology.
    #[test]
    fn sync_rebuilds_a_built_closure_after_reindex() {
        use cairn_build::builder::build_graph;
        use rusqlite::Connection;

        let root = temp_dir("sync-closure");
        let repo = root.join("gitrepo");
        std::fs::create_dir_all(&repo).expect("repo dir");
        let git = |args: &[&str]| {
            let status = std::process::Command::new("git")
                .args(args)
                .current_dir(&repo)
                .env("GIT_AUTHOR_NAME", "t")
                .env("GIT_AUTHOR_EMAIL", "t@example.com")
                .env("GIT_COMMITTER_NAME", "t")
                .env("GIT_COMMITTER_EMAIL", "t@example.com")
                .status()
                .expect("run git");
            assert!(status.success(), "git {args:?} failed");
        };
        git(&["init", "-q"]);
        std::fs::write(
            repo.join("a.py"),
            "def alpha():\n    return beta()\n\n\ndef beta():\n    return 1\n",
        )
        .expect("a");
        git(&["add", "."]);
        git(&["commit", "-qm", "first"]);
        let db = root.join("store.kg").to_string_lossy().into_owned();
        build_graph(repo.to_str().expect("ws"), &db, None, Some(1)).expect("build");
        {
            let mut conn = Connection::open(&db).expect("open");
            cairn_build::closure::build_transitive_closure(&mut conn, 4).expect("closure");
            conn.execute(
                "INSERT INTO transitive_edges (source_id, target_name, target_id, distance) \
                 VALUES ('stale-source', 'stale-name', 'stale-target', 1)",
                [],
            )
            .expect("seed stale row");
        }
        std::fs::write(
            repo.join("a.py"),
            "def alpha_two():\n    return beta()\n\n\ndef beta():\n    return 1\n",
        )
        .expect("edit");

        let code = run_sync(SyncArgs {
            workspace: Some(repo.to_string_lossy().into_owned()),
            db: Some(db.clone()),
        });
        assert_eq!(code, ExitCode::SUCCESS);

        let conn = Connection::open(&db).expect("reopen");
        let stale: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM transitive_edges WHERE source_id = 'stale-source'",
                [],
                |row| row.get(0),
            )
            .expect("count stale");
        assert_eq!(stale, 0, "the stale closure row survived the sync");
        let rows: i64 = conn
            .query_row("SELECT COUNT(*) FROM transitive_edges", [], |row| {
                row.get(0)
            })
            .expect("count closure");
        assert!(rows > 0, "closure rebuild produced no rows");
        let _ = std::fs::remove_dir_all(&root);
    }
}
