//! `cargo xtask kg-normalize`: rewrite volatile content in a canonical dump to content keys.

use std::collections::HashMap;
use std::fs;
use std::process::ExitCode;

const DROP_TABLES: &[&str] = &["build_runs", "events", "tool_metrics", "dataflow"];
const TS: &str = "<TS>";
const NULL_CELL: &str = "\\N";

struct Section {
    table: String,
    header: Vec<String>,
    rows: Vec<Vec<String>>,
}

fn load_sections(text: &str) -> Vec<Section> {
    let mut sections: Vec<Section> = Vec::new();
    for line in text.split('\n') {
        if let Some(name) = line
            .strip_prefix("[table ")
            .and_then(|l| l.strip_suffix(']'))
        {
            sections.push(Section {
                table: name.to_string(),
                header: Vec::new(),
                rows: Vec::new(),
            });
            continue;
        }
        let Some(current) = sections.last_mut() else {
            continue;
        };
        if let Some(cols) = line.strip_prefix("# columns: ") {
            current.header = cols.split('\t').map(str::to_string).collect();
        } else if !line.is_empty() {
            let mut cells: Vec<String> = line.split('\t').map(str::to_string).collect();
            if cells[0].starts_with("\\[table ") {
                cells[0].remove(0);
            }
            current.rows.push(cells);
        }
    }
    sections
}

struct Ordinals(HashMap<String, usize>);

impl Ordinals {
    fn key(&mut self, key: String) -> String {
        let n = self.0.entry(key.clone()).or_insert(0);
        let out = if *n == 0 { key } else { format!("{key}#{n}") };
        *n += 1;
        out
    }
}

struct Cols<'a>(&'a [String]);

impl Cols<'_> {
    fn find(&self, name: &str) -> Option<usize> {
        self.0.iter().position(|c| c == name)
    }

    fn need(&self, name: &str) -> Result<usize, String> {
        self.find(name)
            .ok_or_else(|| format!("missing column {name}"))
    }
}

fn remap(row: &mut [String], cols: &Cols, name: &str, keys: &HashMap<String, String>) {
    let Some(i) = cols.find(name) else {
        return;
    };
    if row[i] != NULL_CELL
        && let Some(mapped) = keys.get(&row[i])
    {
        row[i] = mapped.clone();
    }
}

fn join_cells(row: &[String], cols: &Cols, names: &[&str]) -> Result<String, String> {
    let mut out = String::new();
    for name in names {
        out.push('|');
        out.push_str(&row[cols.need(name)?]);
    }
    Ok(out)
}

fn symbol_keys(
    sections: &[Section],
    file_keys: &HashMap<String, String>,
    ord: &mut Ordinals,
) -> Result<HashMap<String, String>, String> {
    let mut keys = HashMap::new();
    for s in sections.iter().filter(|s| s.table == "symbols") {
        let cols = Cols(&s.header);
        let file_id = cols.need("file_id")?;
        for r in &s.rows {
            let file = file_keys.get(&r[file_id]).unwrap_or(&r[file_id]);
            let rest = join_cells(
                r,
                &cols,
                &[
                    "name",
                    "kind",
                    "line_start",
                    "line_end",
                    "column_start",
                    "column_end",
                ],
            )?;
            keys.insert(r[0].clone(), ord.key(format!("@S|{file}{rest}")));
        }
    }
    Ok(keys)
}

fn file_keys(sections: &[Section], ord: &mut Ordinals) -> Result<HashMap<String, String>, String> {
    let mut keys = HashMap::new();
    for s in sections.iter().filter(|s| s.table == "files") {
        let cols = Cols(&s.header);
        for r in &s.rows {
            let rest = join_cells(r, &cols, &["repo_id", "path"])?;
            keys.insert(r[0].clone(), ord.key(format!("@F{rest}")));
        }
    }
    Ok(keys)
}

fn normalize_row(
    table: &str,
    cols: &Cols,
    mut r: Vec<String>,
    fkeys: &HashMap<String, String>,
    skeys: &HashMap<String, String>,
    ord: &mut Ordinals,
) -> Result<Vec<String>, String> {
    let stamp = ["indexed_at", "recorded_at", "timestamp"]
        .iter()
        .find_map(|c| cols.find(c));
    if let Some(i) = stamp {
        r[i] = TS.to_string();
    }
    if let Some(i) = cols.find("mtime") {
        r[i] = TS.to_string();
    }
    if let Some(i) = cols.find("stack_trace") {
        r[i] = "<TRACE>".to_string();
    }
    let id = cols.find("id");
    let Some(id) = id else {
        return Ok(r);
    };
    match table {
        "files" => remap(&mut r, cols, "id", fkeys),
        "symbols" => {
            remap(&mut r, cols, "file_id", fkeys);
            remap(&mut r, cols, "id", skeys);
        }
        "edges" => {
            let raw_target = r[cols.need("target_id")?].clone();
            let tname = r[cols.need("target_name")?].clone();
            remap(&mut r, cols, "source_id", skeys);
            remap(&mut r, cols, "target_id", skeys);
            let target = if raw_target != NULL_CELL {
                skeys.get(&raw_target).unwrap_or(&raw_target).clone()
            } else if tname != NULL_CELL {
                format!("n|{tname}")
            } else {
                "nn".to_string()
            };
            let mut key = format!(
                "@E{}",
                join_cells(&r, cols, &["source_id", "kind", "line", "column"])?
            );
            key.push('|');
            key.push_str(&target);
            for c in ["receiver_type", "call_arity"] {
                if let Some(i) = cols.find(c) {
                    key.push('|');
                    key.push_str(&r[i]);
                }
            }
            r[id] = ord.key(key);
        }
        "imports" => {
            remap(&mut r, cols, "file_id", fkeys);
            let rest = join_cells(
                &r,
                cols,
                &["file_id", "imported_path", "line", "local_alias"],
            )?;
            r[id] = ord.key(format!("@I{rest}"));
        }
        "parse_errors" => {
            let rest = join_cells(&r, cols, &["repo_id", "file_path"])?;
            r[id] = ord.key(format!("@P{rest}"));
        }
        "skipped_files" => {
            let rest = join_cells(&r, cols, &["repo_id", "path", "reason"])?;
            r[id] = ord.key(format!("@SK{rest}"));
        }
        "rationale" => {
            remap(&mut r, cols, "file_id", fkeys);
            remap(&mut r, cols, "symbol_id", skeys);
            let rest = join_cells(&r, cols, &["file_id", "line", "kind", "text"])?;
            r[id] = ord.key(format!("@R{rest}"));
        }
        _ => {}
    }
    Ok(r)
}

fn normalize_text(text: &str) -> Result<String, String> {
    let sections = load_sections(text);
    let mut ord = Ordinals(HashMap::new());
    let fkeys = file_keys(&sections, &mut ord)?;
    let skeys = symbol_keys(&sections, &fkeys, &mut ord)?;
    ord.0.clear();

    let mut out = vec!["# cairn-canonical-dump 1".to_string()];
    for s in sections
        .into_iter()
        .filter(|s| !DROP_TABLES.contains(&s.table.as_str()))
    {
        out.push(format!("[table {}]", s.table));
        out.push(format!("# columns: {}", s.header.join("\t")));
        let cols = Cols(&s.header);
        let mut lines: Vec<String> = Vec::new();
        if s.header.is_empty() {
            let mut rows = s.rows;
            rows.sort();
            out.extend(rows.iter().map(|r| r.join("\t")));
            continue;
        }
        for r in s.rows {
            let r = normalize_row(&s.table, &cols, r, &fkeys, &skeys, &mut ord)?;
            lines.push(r.join("\t"));
        }
        lines.sort();
        out.extend(lines);
    }
    let text = out.join("\n") + "\n";
    Ok(text.replace("/private<WORKSPACE>", "<WORKSPACE>"))
}

pub fn run(args: &[String]) -> ExitCode {
    let [src, dst] = args else {
        eprintln!("usage: cargo xtask kg-normalize <dump-in> <normalized-out>");
        return ExitCode::from(2);
    };
    let result = fs::read_to_string(src)
        .map_err(|e| format!("{src}: {e}"))
        .and_then(|text| normalize_text(&text))
        .and_then(|text| fs::write(dst, text).map_err(|e| format!("{dst}: {e}")));
    match result {
        Ok(()) => {
            eprintln!("normalized {src} -> {dst}");
            ExitCode::SUCCESS
        }
        Err(msg) => {
            eprintln!("xtask kg-normalize: {msg}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const DUMP: &str = "# cairn-canonical-dump 1\n\
[table events]\n# columns: id\tmsg\n1\tx\n\
[table files]\n# columns: id\trepo_id\tpath\tindexed_at\tmtime\nu-2\tr\ta.py\t2024\t99\n\
[table symbols]\n# columns: id\tfile_id\tname\tkind\tline_start\tline_end\tcolumn_start\tcolumn_end\nu-9\tu-2\tf\tfunction\t1\t2\t0\t1\n\
[table edges]\n# columns: id\tsource_id\ttarget_id\ttarget_name\tkind\tline\tcolumn\nu-5\tu-9\t\\N\tg\tcalls\t3\t4\n\
[table parse_errors]\n# columns: id\trepo_id\tfile_path\tstack_trace\nu-1\tr\t/private<WORKSPACE>/a\tboom\n";

    #[test]
    fn ids_become_content_keys_and_volatile_content_is_blanked() {
        let out = normalize_text(DUMP).unwrap();
        assert!(!out.contains("[table events]"));
        assert!(out.contains("@F|r|a.py\tr\ta.py\t<TS>\t<TS>"));
        assert!(out.contains("@S|@F|r|a.py|f|function|1|2|0|1\t@F|r|a.py\tf"));
        assert!(out.contains("@E|@S|@F|r|a.py|f|function|1|2|0|1|calls|3|4|n|g\t"));
        assert!(out.contains("@P|r|<WORKSPACE>/a\tr\t<WORKSPACE>/a\t<TRACE>"));
    }

    #[test]
    fn duplicate_keys_get_ordinal_suffixes() {
        let dump = "[table files]\n# columns: id\trepo_id\tpath\nu-1\tr\ta\nu-2\tr\ta\n";
        let out = normalize_text(dump).unwrap();
        assert!(out.contains("@F|r|a\tr\ta") && out.contains("@F|r|a#1\tr\ta"));
    }
}
