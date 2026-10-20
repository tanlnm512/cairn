//! `cairn build-graph` subcommand: the Rust builder entry the G2 gate drives.

use std::process::ExitCode;

use cairn_build::builder::build_graph;

/// Run the subcommand; prints the summary JSON and stage timings to stdout.
pub fn run(args: &[String]) -> ExitCode {
    let parsed = match parse_args(args) {
        Ok(parsed) => parsed,
        Err(msg) => {
            eprintln!("error: {msg}");
            return ExitCode::from(2);
        }
    };
    match build_graph(
        &parsed.workspace,
        &parsed.db,
        parsed.repo.as_deref(),
        parsed.workers,
    ) {
        Ok((summary, timings)) => {
            match serde_json::to_string(&summary) {
                Ok(json) => println!("{json}"),
                Err(e) => {
                    eprintln!("error: summary serialization failed: {e}");
                    return ExitCode::FAILURE;
                }
            }
            println!(
                "timings: scan={:.1}ms parse={:.1}ms insert={:.1}ms resolve={:.1}ms imports={:.1}ms total={:.1}ms",
                timings.scan_ms,
                timings.parse_ms,
                timings.insert_ms,
                timings.resolve_ms,
                timings.imports_ms,
                timings.total_ms,
            );
            ExitCode::SUCCESS
        }
        Err(msg) => {
            eprintln!("error: {msg}");
            ExitCode::FAILURE
        }
    }
}

struct Parsed {
    workspace: String,
    db: String,
    repo: Option<String>,
    workers: Option<usize>,
}

fn parse_args(args: &[String]) -> Result<Parsed, String> {
    let mut workspace: Option<String> = None;
    let mut db: Option<String> = None;
    let mut repo: Option<String> = None;
    let mut workers: Option<usize> = None;
    let mut i = 0;
    while i < args.len() {
        let arg = args[i].as_str();
        let (flag, glued) = match arg.split_once('=') {
            Some((f, v))
                if f == "--workspace" || f == "--db" || f == "--repo" || f == "--workers" =>
            {
                (f, Some(v))
            }
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
            "--workspace" => workspace = Some(value(&mut i)?),
            "--db" => db = Some(value(&mut i)?),
            "--repo" => repo = Some(value(&mut i)?),
            "--workers" => {
                workers = Some(
                    value(&mut i)?
                        .parse()
                        .map_err(|_| "--workers needs a number")?,
                )
            }
            other => return Err(format!("unknown argument: {other}")),
        }
        i += 1;
    }
    let workspace = workspace.ok_or("--workspace is required")?;
    let db = db.ok_or("--db is required")?;
    Ok(Parsed {
        workspace,
        db,
        repo,
        workers,
    })
}
