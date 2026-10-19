//! `cairn closure` subcommand: streaming transitive-closure rebuild over a store.

use std::path::Path;
use std::process::ExitCode;
use std::time::Instant;

use cairn_build::closure::{CLOSURE_MAX_DEPTH, build_transitive_closure};

/// Run the subcommand; prints the outcome JSON and timing to stdout.
pub fn run(args: &[String]) -> ExitCode {
    let parsed = match parse_args(args) {
        Ok(parsed) => parsed,
        Err(msg) => {
            eprintln!("error: {msg}");
            return ExitCode::from(2);
        }
    };
    let mut conn = match cairn_store::schema::open_initialized(Path::new(&parsed.db)) {
        Ok(conn) => conn,
        Err(e) => {
            eprintln!("error: open store {}: {e}", parsed.db);
            return ExitCode::FAILURE;
        }
    };
    let started = Instant::now();
    match build_transitive_closure(&mut conn, parsed.depth) {
        Ok(outcome) => {
            let json = match serde_json::to_string(&outcome) {
                Ok(json) => json,
                Err(e) => {
                    eprintln!("error: outcome serialization failed: {e}");
                    return ExitCode::FAILURE;
                }
            };
            println!("{json}");
            println!("timings: closure={:.1}ms", elapsed_ms(started));
            ExitCode::SUCCESS
        }
        Err(msg) => {
            eprintln!("error: {msg}");
            ExitCode::FAILURE
        }
    }
}

fn elapsed_ms(t: Instant) -> f64 {
    t.elapsed().as_secs_f64() * 1000.0
}

struct Parsed {
    db: String,
    depth: usize,
}

fn parse_args(args: &[String]) -> Result<Parsed, String> {
    let mut db: Option<String> = None;
    let mut depth = CLOSURE_MAX_DEPTH;
    let mut i = 0;
    while i < args.len() {
        let arg = args[i].as_str();
        let (flag, glued) = match arg.split_once('=') {
            Some((f, v)) if f == "--db" || f == "--depth" => (f, Some(v)),
            _ => (arg, None),
        };
        let value = |i: &mut usize| -> Result<String, String> {
            match glued {
                Some(v) => Ok(v.to_string()),
                None => {
                    *i += 1;
                    args.get(*i)
                        .cloned()
                        .ok_or_else(|| format!("{flag} needs a value"))
                }
            }
        };
        match flag {
            "--db" => db = Some(value(&mut i)?),
            "--depth" => {
                depth = value(&mut i)?
                    .parse()
                    .map_err(|_| "--depth needs a number")?
            }
            other => return Err(format!("unknown argument: {other}")),
        }
        i += 1;
    }
    let db = db.ok_or("--db is required")?;
    Ok(Parsed { db, depth })
}
