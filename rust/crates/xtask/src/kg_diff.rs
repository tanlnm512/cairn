//! `cargo xtask kg-diff`: compare two canonical dumps; exit 0 when equal, 1 with a per-table report.

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::process::ExitCode;

const FORMAT_LINE: &str = "# cairn-canonical-dump 1";

#[derive(Default)]
struct Dump {
    map_paths: Vec<String>,
    tables: BTreeMap<String, (String, Vec<String>)>,
}

fn parse_dump(path: &str, text: &str) -> Result<Dump, String> {
    let mut dump = Dump::default();
    let mut table: Option<String> = None;
    let mut columns = String::new();
    let mut rows: Vec<String> = Vec::new();
    let mut header_done = false;

    for (idx, line) in text.split_terminator('\n').enumerate() {
        let lineno = idx + 1;
        if !header_done {
            if lineno == 1 {
                if line != FORMAT_LINE {
                    return Err(format!(
                        "{path}: not a canonical dump (first line: {line:?})"
                    ));
                }
                continue;
            }
            if line.starts_with("# map-path: ") {
                dump.map_paths.push(line.to_string());
                continue;
            }
            if line.is_empty() {
                continue;
            }
            header_done = true;
        }
        if let Some(name) = line
            .strip_prefix("[table ")
            .and_then(|l| l.strip_suffix(']'))
        {
            if let Some(t) = table.take() {
                dump.tables
                    .insert(t, (std::mem::take(&mut columns), std::mem::take(&mut rows)));
            }
            table = Some(name.to_string());
        } else if let Some(cols) = line.strip_prefix("# columns: ") {
            if table.is_none() {
                return Err(format!(
                    "{path}:{lineno}: columns line outside a table section"
                ));
            }
            columns = cols.to_string();
        } else if line.starts_with('#') {
        } else if !line.is_empty() {
            if table.is_none() || columns.is_empty() {
                return Err(format!("{path}:{lineno}: row outside a table section"));
            }
            if rows.last().is_some_and(|prev| prev.as_str() > line) {
                return Err(format!(
                    "{path}:{lineno}: rows not sorted (dump is not canonical)"
                ));
            }
            rows.push(line.to_string());
        }
    }
    if let Some(t) = table {
        dump.tables.insert(t, (columns, rows));
    }
    Ok(dump)
}

fn truncated(line: &str) -> String {
    line.chars().take(400).collect()
}

fn diff_rows<'a>(a: &'a [String], b: &'a [String]) -> (Vec<&'a str>, Vec<&'a str>) {
    let (mut i, mut j) = (0, 0);
    let (mut only_a, mut only_b) = (Vec::new(), Vec::new());
    while i < a.len() && j < b.len() {
        match a[i].cmp(&b[j]) {
            std::cmp::Ordering::Equal => {
                i += 1;
                j += 1;
            }
            std::cmp::Ordering::Less => {
                only_a.push(a[i].as_str());
                i += 1;
            }
            std::cmp::Ordering::Greater => {
                only_b.push(b[j].as_str());
                j += 1;
            }
        }
    }
    only_a.extend(a[i..].iter().map(String::as_str));
    only_b.extend(b[j..].iter().map(String::as_str));
    (only_a, only_b)
}

fn diff_dumps(
    a: &Dump,
    b: &Dump,
    max_samples: usize,
    only: Option<&BTreeSet<String>>,
) -> (Vec<String>, usize) {
    let mut report = Vec::new();
    let mut diffs = 0;
    if a.map_paths != b.map_paths {
        diffs += 1;
        report.push("header: map-path sets differ".to_string());
        let sa: BTreeSet<_> = a.map_paths.iter().collect();
        let sb: BTreeSet<_> = b.map_paths.iter().collect();
        report.extend(sa.symmetric_difference(&sb).map(|l| format!("  {l}")));
    }
    let wanted = |n: &&String| only.is_none_or(|o| o.contains(*n));
    let names_a: BTreeSet<&String> = a.tables.keys().filter(wanted).collect();
    let names_b: BTreeSet<&String> = b.tables.keys().filter(wanted).collect();
    for name in names_a.difference(&names_b) {
        diffs += 1;
        report.push(format!("only in A: [table {name}]"));
    }
    for name in names_b.difference(&names_a) {
        diffs += 1;
        report.push(format!("only in B: [table {name}]"));
    }
    for name in names_a.intersection(&names_b) {
        let (cols_a, rows_a) = &a.tables[*name];
        let (cols_b, rows_b) = &b.tables[*name];
        if cols_a != cols_b {
            diffs += 1;
            report.push(format!("[table {name}]: columns differ"));
            report.push(format!("  A: {cols_a}"));
            report.push(format!("  B: {cols_b}"));
            continue;
        }
        let (only_a, only_b) = diff_rows(rows_a, rows_b);
        if only_a.is_empty() && only_b.is_empty() {
            continue;
        }
        diffs += 1;
        report.push(format!(
            "[table {name}]: -{}/+{}  (A-only/B-only rows of {}/{})",
            only_a.len(),
            only_b.len(),
            rows_a.len(),
            rows_b.len()
        ));
        report.extend(
            only_a
                .iter()
                .take(max_samples)
                .map(|l| format!("  - {}", truncated(l))),
        );
        report.extend(
            only_b
                .iter()
                .take(max_samples)
                .map(|l| format!("  + {}", truncated(l))),
        );
        if only_a.len() > max_samples {
            report.push(format!("  ... {} more A-only", only_a.len() - max_samples));
        }
        if only_b.len() > max_samples {
            report.push(format!("  ... {} more B-only", only_b.len() - max_samples));
        }
    }
    (report, diffs)
}

struct Args {
    dumps: Vec<String>,
    max_samples: usize,
    only: Option<BTreeSet<String>>,
}

fn parse_args(args: &[String]) -> Result<Args, String> {
    let mut parsed = Args {
        dumps: Vec::new(),
        max_samples: 5,
        only: None,
    };
    let mut it = args.iter();
    while let Some(arg) = it.next() {
        match arg.as_str() {
            "--max-samples" => {
                let v = it.next().ok_or("--max-samples needs a value")?;
                parsed.max_samples = v
                    .parse()
                    .map_err(|_| format!("invalid --max-samples: {v}"))?;
            }
            "--tables" => {
                let v = it.next().ok_or("--tables needs a value")?;
                parsed.only = Some(v.split(',').map(|t| t.trim().to_string()).collect());
            }
            flag if flag.starts_with("--") => return Err(format!("unrecognized flag: {flag}")),
            _ => parsed.dumps.push(arg.clone()),
        }
    }
    if parsed.dumps.len() != 2 {
        return Err("usage: kg-diff <dump_a> <dump_b> [--max-samples N] [--tables T,..]".into());
    }
    Ok(parsed)
}

fn load(path: &str) -> Result<Dump, String> {
    let text = fs::read_to_string(path).map_err(|_| format!("dump not found: {path}"))?;
    parse_dump(path, &text)
}

pub fn run(args: &[String]) -> ExitCode {
    let parsed = match parse_args(args) {
        Ok(p) => p,
        Err(msg) => {
            eprintln!("error: {msg}");
            return ExitCode::from(2);
        }
    };
    let (a, b) = match (load(&parsed.dumps[0]), load(&parsed.dumps[1])) {
        (Ok(a), Ok(b)) => (a, b),
        (Err(e), _) | (_, Err(e)) => {
            eprintln!("{e}");
            return ExitCode::from(2);
        }
    };
    let (report, diffs) = diff_dumps(&a, &b, parsed.max_samples, parsed.only.as_ref());
    if diffs == 0 {
        let wanted = |n: &&String| parsed.only.as_ref().is_none_or(|o| o.contains(*n));
        let tables = a.tables.keys().filter(wanted).count();
        let rows: usize = a
            .tables
            .iter()
            .filter(|(n, _)| wanted(n))
            .map(|(_, t)| t.1.len())
            .sum();
        println!("dumps identical: {tables} tables, {rows} rows");
        return ExitCode::SUCCESS;
    }
    println!("dumps differ ({diffs} difference(s)):");
    println!("{}", report.join("\n"));
    ExitCode::FAILURE
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dump(body: &str) -> Dump {
        parse_dump("t", &format!("{FORMAT_LINE}\n{body}")).unwrap()
    }

    #[test]
    fn verdicts_cover_identical_row_and_table_differences() {
        let a = dump("[table t]\n# columns: x\n1\n2\n[table u]\n# columns: y\n");
        assert_eq!(diff_dumps(&a, &a, 5, None).1, 0);

        let b = dump("[table t]\n# columns: x\n1\n3\n");
        let (report, diffs) = diff_dumps(&a, &b, 5, None);
        assert_eq!(diffs, 2);
        assert!(report.contains(&"only in A: [table u]".to_string()));
        assert!(report.contains(&"  - 2".to_string()) && report.contains(&"  + 3".to_string()));

        let only_t: BTreeSet<String> = ["t".to_string()].into();
        assert_eq!(diff_dumps(&a, &b, 5, Some(&only_t)).1, 1);
    }

    #[test]
    fn unsorted_or_foreign_input_is_rejected() {
        assert!(
            parse_dump(
                "t",
                &format!("{FORMAT_LINE}\n[table t]\n# columns: x\n2\n1\n")
            )
            .is_err()
        );
        assert!(parse_dump("t", "nope\n").is_err());
    }
}
