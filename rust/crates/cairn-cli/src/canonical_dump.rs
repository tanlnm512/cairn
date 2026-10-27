//! `cairn canonical-dump` subcommand: the canonical store dump CLI surface.

use std::collections::BTreeSet;
use std::io::Write as _;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use cairn_store::conn::open_read_only;
use cairn_store::dump::{DumpError, DumpOptions, TableClass, classify_tables, dump_store};

/// Run the subcommand; exits 0 on success, 1 on dump failure, 2 on usage errors.
pub fn run(args: &[String]) -> ExitCode {
    let parsed = match parse_args(args) {
        Ok(parsed) => parsed,
        Err(msg) => {
            eprintln!("error: {msg}");
            return ExitCode::from(2);
        }
    };
    execute(*parsed)
}

struct Parsed {
    db: PathBuf,
    out: Option<PathBuf>,
    opts: DumpOptions,
    list_tables: bool,
}

fn parse_args(args: &[String]) -> Result<Box<Parsed>, String> {
    let mut db: Option<PathBuf> = None;
    let mut out: Option<PathBuf> = None;
    let mut map_paths: Vec<(String, String)> = Vec::new();
    let mut only: Option<BTreeSet<String>> = None;
    let mut list_tables = false;

    let mut i = 0;
    while i < args.len() {
        let arg = args[i].as_str();
        let (flag, glued) = match arg.split_once('=') {
            Some((f, v)) if f == "--out" || f == "--map-path" || f == "--tables" => (f, Some(v)),
            _ => (arg, None),
        };
        match (flag, glued) {
            ("-o" | "--out", v) => out = Some(PathBuf::from(take_value(v, args, &mut i, flag)?)),
            ("--map-path", v) => {
                let val = take_value(v, args, &mut i, flag)?;
                let Some((src, dst)) = val.split_once('=') else {
                    return Err(format!("--map-path needs FROM=TO, got: {val}"));
                };
                map_paths.push((src.to_string(), dst.to_string()));
            }
            ("--tables", v) => {
                let val = take_value(v, args, &mut i, flag)?;
                only = Some(
                    val.split(',')
                        .map(str::trim)
                        .filter(|t| !t.is_empty())
                        .map(str::to_string)
                        .collect(),
                );
            }
            ("--list-tables", _) => list_tables = true,
            _ if arg.starts_with('-') => return Err(format!("unrecognized flag: {arg}")),
            _ => {
                if db.is_some() {
                    return Err(format!("unexpected extra argument: {arg}"));
                }
                db = Some(PathBuf::from(arg));
            }
        }
        i += 1;
    }

    let Some(db) = db else {
        return Err("the following arguments are required: db".to_string());
    };
    if !db.is_file() {
        return Err(format!("store not found: {}", db.display()));
    }
    Ok(Box::new(Parsed {
        db,
        out,
        opts: DumpOptions { map_paths, only },
        list_tables,
    }))
}

fn take_value<'a>(
    glued: Option<&'a str>,
    args: &'a [String],
    i: &mut usize,
    flag: &str,
) -> Result<&'a str, String> {
    if let Some(v) = glued {
        return Ok(v);
    }
    *i += 1;
    args.get(*i)
        .map(String::as_str)
        .ok_or_else(|| format!("flag {flag} needs a value"))
}

fn execute(parsed: Parsed) -> ExitCode {
    let Parsed {
        db,
        out,
        opts,
        list_tables,
    } = parsed;

    if list_tables {
        return match list_table_classes(&db) {
            Ok(lines) => {
                println!("{}", lines.join("\n"));
                ExitCode::SUCCESS
            }
            Err(e) => fail(e),
        };
    }

    if let Some(only) = &opts.only {
        let unknown: Vec<String> = only.difference(&content_tables(&db)).cloned().collect();
        if !unknown.is_empty() {
            eprintln!("error: not content tables: {}", unknown.join(", "));
            return ExitCode::from(2);
        }
    }

    let (_, lines) = match dump_store(&db, &opts) {
        Ok(result) => result,
        Err(e) => return fail(e),
    };
    let mut text = lines.join("\n");
    text.push('\n');
    match out {
        Some(path) => {
            if let Err(e) = std::fs::write(&path, text) {
                eprintln!("error: write {}: {e}", path.display());
                return ExitCode::FAILURE;
            }
            eprintln!("wrote {} ({} lines)", path.display(), lines.len());
        }
        None => {
            let stdout = std::io::stdout();
            let mut handle = stdout.lock();
            if let Err(e) = handle.write_all(text.as_bytes()) {
                eprintln!("error: stdout: {e}");
                return ExitCode::FAILURE;
            }
        }
    }
    ExitCode::SUCCESS
}

fn fail(e: DumpError) -> ExitCode {
    eprintln!("error: {e}");
    ExitCode::FAILURE
}

fn content_tables(db: &Path) -> BTreeSet<String> {
    open_read_only(db)
        .and_then(|conn| classify_tables(&conn))
        .map(|classes| {
            classes
                .into_iter()
                .filter(|(_, class)| *class == TableClass::Content)
                .map(|(name, _)| name)
                .collect()
        })
        .unwrap_or_default()
}

fn list_table_classes(db: &Path) -> Result<Vec<String>, DumpError> {
    let conn = open_read_only(db).map_err(DumpError::Sqlite)?;
    let classes = classify_tables(&conn).map_err(DumpError::Sqlite)?;
    let mut lines: Vec<String> = classes
        .iter()
        .map(|(name, class)| format!("{name}\t{}", class.as_str()))
        .collect();
    lines.sort();
    Ok(lines)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn argvec(vals: &[&str]) -> Vec<String> {
        vals.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn map_path_needs_from_equals_to() {
        let args = argvec(&["store.kg", "--map-path", "novalue"]);
        match parse_args(&args) {
            Ok(_) => panic!("expected usage error"),
            Err(msg) => assert!(msg.contains("FROM=TO"), "{msg}"),
        }
    }

    #[test]
    fn missing_db_is_a_usage_error() {
        let args = argvec(&["/definitely/not/here.kg"]);
        match parse_args(&args) {
            Ok(_) => panic!("expected usage error"),
            Err(msg) => assert!(msg.contains("store not found"), "{msg}"),
        }
    }

    #[test]
    fn unknown_flag_is_a_usage_error() {
        let args = argvec(&["--bogus"]);
        match parse_args(&args) {
            Ok(_) => panic!("expected usage error"),
            Err(msg) => assert!(msg.contains("--bogus"), "{msg}"),
        }
    }

    #[test]
    fn glued_and_separated_values_parse_alike() {
        let dir = std::env::temp_dir().join(format!(
            "cairn-cli-parse-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        ));
        std::fs::create_dir_all(&dir).expect("temp dir");
        let store = dir.join("store.kg");
        std::fs::write(&store, b"").expect("store file");
        let a = parse_args(&argvec(&[
            store.to_str().expect("utf8"),
            "--out=x.txt",
            "--map-path=a=b",
            "--tables",
            "t1,t2",
        ]));
        let b = parse_args(&argvec(&[
            store.to_str().expect("utf8"),
            "-o",
            "x.txt",
            "--map-path",
            "a=b",
            "--tables",
            "t1,t2",
        ]));
        let (Ok(a), Ok(b)) = (a, b) else {
            panic!("both parses should succeed");
        };
        assert_eq!(a.out, b.out);
        assert_eq!(a.opts.map_paths, b.opts.map_paths);
        assert_eq!(a.opts.only, b.opts.only);
        assert!(!a.list_tables);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
