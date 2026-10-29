//! An incrementally grafted store must be content-equal to a full rebuild under g2 normalization.

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use cairn_build::builder::build_graph;
use cairn_build::incremental::reindex_paths;
use cairn_store::conn::open_read_only;
use cairn_store::dump::{DumpOptions, dump_connection};

fn temp_dir(tag: &str) -> PathBuf {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let dir =
        std::env::temp_dir().join(format!("cairn-graft-{tag}-{}-{nanos}", std::process::id()));
    fs::create_dir_all(dir.join("alpha").join("deep")).expect("temp dir");
    fs::create_dir_all(dir.join("alpha").join(".git")).expect("git marker");
    fs::create_dir_all(dir.join("beta").join(".git")).expect("git marker");
    dir
}

fn write(dir: &Path, rel: &str, content: &str) {
    let path = dir.join(rel);
    fs::create_dir_all(path.parent().expect("parent")).expect("mkdir");
    fs::write(path, content).expect("write fixture");
}

/// Two-repo multi-language corpus; per-file row counts differ so graft ids
/// and rebuild ids genuinely diverge.
fn corpus(tag: &str) -> PathBuf {
    let dir = temp_dir(tag);
    write(
        &dir,
        "alpha/util.py",
        "\"\"\"Helpers.\"\"\"\n\ndef add(a, b):\n    return a + b\n\n\ndef spread(a, b, c):\n    return add(add(a, b), c)\n",
    );
    write(
        &dir,
        "alpha/deep/app.py",
        "from util import add\n\n\ndef main():\n    return add(1, 2)\n",
    );
    write(
        &dir,
        "beta/service.ts",
        "export function run(name: string): number {\n  return name.length;\n}\n",
    );
    write(
        &dir,
        "beta/lib.rs",
        "pub fn compute(x: u64) -> u64 {\n    x + 1\n}\n",
    );
    dir
}

fn dump_lines(db: &Path) -> Vec<String> {
    let conn = open_read_only(db).expect("open store");
    let (_, lines) = dump_connection(&conn, &DumpOptions::default()).expect("dump");
    conn.close().expect("close");
    lines
}

type NormTables = BTreeMap<String, (Vec<String>, Vec<String>)>;

fn ordinal(key: String, dupes: &mut BTreeMap<String, usize>) -> String {
    let n = dupes.entry(key.clone()).or_insert(0);
    let out = if *n == 0 {
        key.clone()
    } else {
        format!("{key}#{n}")
    };
    *n += 1;
    out
}

fn col(header: &[String], name: &str) -> Option<usize> {
    header.iter().position(|c| c == name)
}

/// Port of the g2 gate's dump-time normalization: content-keyed ids, blanked
/// stamps, dropped telemetry tables, re-sorted rows.
fn normalize_dump(lines: &[String]) -> NormTables {
    const DROP_TABLES: &[&str] = &["build_runs", "events", "tool_metrics", "dataflow"];
    const TS: &str = "<TS>";

    // Parse into (table, columns, rows) sections.
    let mut sections: Vec<(String, Vec<String>, Vec<Vec<String>>)> = Vec::new();
    let mut current: Option<(String, Vec<String>, Vec<Vec<String>>)> = None;
    for line in lines {
        if line.starts_with("[table ") && line.ends_with("]") {
            if let Some(section) = current.take() {
                sections.push(section);
            }
            current = Some((
                line["[table ".len()..line.len() - 1].to_string(),
                Vec::new(),
                Vec::new(),
            ));
        } else if let Some((_, header, rows)) = current.as_mut() {
            if let Some(cols) = line.strip_prefix("# columns: ") {
                *header = cols.split('\t').map(str::to_string).collect();
            } else if !line.is_empty() && !header.is_empty() {
                let mut cells: Vec<String> = line.split('\t').map(str::to_string).collect();
                if cells[0].starts_with("\\[table ") {
                    cells[0] = cells[0][1..].to_string();
                }
                rows.push(cells);
            }
        }
    }
    if let Some(section) = current.take() {
        sections.push(section);
    }

    let by_table: BTreeMap<String, (Vec<String>, Vec<Vec<String>>)> = sections
        .iter()
        .map(|(t, h, r)| (t.clone(), (h.clone(), r.clone())))
        .collect();

    // Content keys for file and symbol ids, in the dumps' shared sorted order.
    let mut dupes: BTreeMap<String, usize> = BTreeMap::new();
    let mut fkey: BTreeMap<String, String> = BTreeMap::new();
    let mut skey: BTreeMap<String, String> = BTreeMap::new();
    if let Some((h, rows)) = by_table.get("files") {
        let (repo, path) = (
            col(h, "repo_id").expect("repo_id"),
            col(h, "path").expect("path"),
        );
        for r in rows {
            let key = format!("@F|{}|{}", r[repo], r[path]);
            let mapped = ordinal(key, &mut dupes);
            fkey.insert(r[0].clone(), mapped);
        }
    }
    if let Some((h, rows)) = by_table.get("symbols") {
        let file_id = col(h, "file_id").expect("file_id");
        let idx = |name: &str| col(h, name).expect(name);
        for r in rows {
            let key = format!(
                "@S|{}|{}|{}|{}|{}|{}|{}",
                fkey.get(&r[file_id]).unwrap_or(&r[file_id]),
                r[idx("name")],
                r[idx("kind")],
                r[idx("line_start")],
                r[idx("line_end")],
                r[idx("column_start")],
                r[idx("column_end")],
            );
            let mapped = ordinal(key, &mut dupes);
            skey.insert(r[0].clone(), mapped);
        }
    }
    dupes.clear();

    let mut out: NormTables = BTreeMap::new();
    for (table, header, rows) in &sections {
        if DROP_TABLES.contains(&table.as_str()) {
            continue;
        }
        let stamped = col(header, "indexed_at")
            .or_else(|| col(header, "recorded_at"))
            .or_else(|| col(header, "timestamp"));
        let trace = col(header, "stack_trace");
        let idcol = col(header, "id");

        let rewrite_ref = |r: &mut Vec<String>, name: &str| {
            let Some(i) = col(header, name) else {
                return;
            };
            if r[i] == "\\N" {
                return;
            }
            let map: &BTreeMap<String, String> =
                if matches!(name, "source_id" | "target_id" | "symbol_id") {
                    &skey
                } else {
                    &fkey
                };
            if let Some(mapped) = map.get(&r[i]) {
                r[i] = mapped.clone();
            }
        };

        let mut rewritten: Vec<String> = Vec::new();
        for r in rows.iter() {
            let mut r = r.clone();
            if let Some(i) = stamped {
                r[i] = TS.to_string();
            }
            if let Some(i) = trace {
                r[i] = "<TRACE>".to_string();
            }
            match table.as_str() {
                "files" => {
                    let i = idcol.expect("id");
                    if let Some(mapped) = fkey.get(&r[i]) {
                        r[i] = mapped.clone();
                    }
                }
                "symbols" => {
                    rewrite_ref(&mut r, "file_id");
                    let i = idcol.expect("id");
                    if let Some(mapped) = skey.get(&r[i]) {
                        r[i] = mapped.clone();
                    }
                }
                "edges" => {
                    let (src, tgt, tname_i) = (
                        col(header, "source_id").expect("source_id"),
                        col(header, "target_id").expect("target_id"),
                        col(header, "target_name").expect("target_name"),
                    );
                    let raw_target = r[tgt].clone();
                    let tname = r[tname_i].clone();
                    rewrite_ref(&mut r, "source_id");
                    rewrite_ref(&mut r, "target_id");
                    let target = if raw_target != "\\N" {
                        skey.get(&raw_target)
                            .cloned()
                            .unwrap_or_else(|| raw_target.clone())
                    } else if tname != "\\N" {
                        format!("n|{tname}")
                    } else {
                        "nn".to_string()
                    };
                    // target_name is derivable from target_id on resolved
                    // edges: a rebuild inserts it on contains edges, a
                    // graft's repair pass nulls it.
                    if raw_target != "\\N" {
                        r[tname_i] = "<RESOLVED>".to_string();
                    }
                    let i = idcol.expect("id");
                    let signals: String = ["receiver_type", "call_arity"]
                        .iter()
                        .filter_map(|c| col(header, c))
                        .map(|c| format!("|{}", r[c]))
                        .collect();
                    let key = format!(
                        "@E|{}|{}|{}|{}|{}{signals}",
                        r[src],
                        r[col(header, "kind").expect("kind")],
                        r[col(header, "line").expect("line")],
                        r[col(header, "column").expect("column")],
                        target,
                    );
                    r[i] = ordinal(key, &mut dupes);
                }
                "imports" => {
                    rewrite_ref(&mut r, "file_id");
                    let i = idcol.expect("id");
                    let key = format!(
                        "@I|{}|{}|{}|{}",
                        r[col(header, "file_id").expect("file_id")],
                        r[col(header, "imported_path").expect("imported_path")],
                        r[col(header, "line").expect("line")],
                        r[col(header, "local_alias").expect("local_alias")],
                    );
                    r[i] = ordinal(key, &mut dupes);
                }
                "parse_errors" => {
                    let i = idcol.expect("id");
                    let key = format!(
                        "@P|{}|{}",
                        r[col(header, "repo_id").expect("repo_id")],
                        r[col(header, "file_path").expect("file_path")],
                    );
                    r[i] = ordinal(key, &mut dupes);
                }
                "skipped_files" => {
                    let i = idcol.expect("id");
                    let key = format!(
                        "@SK|{}|{}|{}",
                        r[col(header, "repo_id").expect("repo_id")],
                        r[col(header, "path").expect("path")],
                        r[col(header, "reason").expect("reason")],
                    );
                    r[i] = ordinal(key, &mut dupes);
                }
                "rationale" => {
                    rewrite_ref(&mut r, "file_id");
                    rewrite_ref(&mut r, "symbol_id");
                    let i = idcol.expect("id");
                    let key = format!(
                        "@R|{}|{}|{}|{}",
                        r[col(header, "file_id").expect("file_id")],
                        r[col(header, "line").expect("line")],
                        r[col(header, "kind").expect("kind")],
                        r[col(header, "text").expect("text")],
                    );
                    r[i] = ordinal(key, &mut dupes);
                }
                _ => {}
            }
            rewritten.push(r.join("\t"));
        }
        rewritten.sort();
        out.insert(table.clone(), (header.clone(), rewritten));
    }
    out
}

/// Full-table equality with a pointed failure message.
fn assert_norm_equal(tag: &str, a: &NormTables, b: &NormTables) {
    let mut names: Vec<String> = a.keys().chain(b.keys()).cloned().collect();
    names.sort();
    names.dedup();
    let empty: (Vec<String>, Vec<String>) = (Vec::new(), Vec::new());
    for name in &names {
        let (ha, ra) = a.get(name).unwrap_or(&empty);
        let (hb, rb) = b.get(name).unwrap_or(&empty);
        assert_eq!(ha, hb, "[{tag}] columns diverged in {name}");
        for (i, (row_a, row_b)) in ra.iter().zip(rb.iter()).enumerate() {
            assert_eq!(
                row_a, row_b,
                "[{tag}] {name} row {i} diverged (graft vs rebuild)"
            );
        }
        assert_eq!(
            ra.len(),
            rb.len(),
            "[{tag}] {name} row count diverged: graft {} vs rebuild {}",
            ra.len(),
            rb.len()
        );
    }
}

fn graft_and_compare(tag: &str, mutate: impl FnOnce(&Path), reindex: &[&str]) {
    graft_and_compare_on(tag, |_| {}, mutate, reindex);
}

fn graft_and_compare_on(
    tag: &str,
    seed: impl FnOnce(&Path),
    mutate: impl FnOnce(&Path),
    reindex: &[&str],
) {
    let dir = corpus(tag);
    seed(&dir);
    let ws = dir.to_str().expect("ws");
    let base_db = dir.join("base.kg");

    let (base_summary, _) =
        build_graph(ws, base_db.to_str().expect("db"), None, Some(1)).expect("base build");
    assert_eq!(
        base_summary.parse_errors, 0,
        "[{tag}] base build must be clean"
    );

    mutate(&dir);

    // Graft: reindex only the touched paths on the pre-edit store.
    let paths: Vec<String> = reindex
        .iter()
        .map(|rel| dir.join(rel).to_string_lossy().into_owned())
        .collect();
    let mut conn = cairn_store::schema::open_initialized(&base_db).expect("reopen base store");
    let result = reindex_paths(&mut conn, ws, &paths);
    conn.close().expect("close");
    assert!(
        result.errors.is_empty(),
        "[{tag}] graft errors: {:?}",
        result.errors
    );

    // Oracle: full rust rebuild of the post-edit tree.
    let fresh_db = dir.join("fresh.kg");
    let (fresh_summary, _) =
        build_graph(ws, fresh_db.to_str().expect("db"), None, Some(1)).expect("rebuild");
    assert_eq!(
        fresh_summary.parse_errors, 0,
        "[{tag}] rebuild must be clean"
    );

    let graft_norm = normalize_dump(&dump_lines(&base_db));
    let rebuild_norm = normalize_dump(&dump_lines(&fresh_db));
    assert_norm_equal(tag, &graft_norm, &rebuild_norm);

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn graft_of_one_edited_file_matches_full_rebuild() {
    graft_and_compare(
        "edit",
        |dir| {
            write(
                dir,
                "alpha/util.py",
                "\"\"\"Helpers.\"\"\"\n\ndef add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return a * b\n\n\ndef spread(a, b, c):\n    return mul(add(a, b), c)\n",
            );
        },
        &["alpha/util.py"],
    );
}

#[test]
fn graft_of_one_deleted_file_matches_full_rebuild() {
    graft_and_compare(
        "delete",
        |dir| {
            fs::remove_file(dir.join("alpha/deep/app.py")).expect("delete fixture");
        },
        &["alpha/deep/app.py"],
    );
}

#[test]
fn graft_of_one_new_file_matches_full_rebuild() {
    graft_and_compare(
        "create",
        |dir| {
            write(dir, "alpha/newmod.py", "def helper(x):\n    return x\n");
        },
        &["alpha/newmod.py"],
    );
}

#[test]
fn graft_of_one_edited_rust_file_matches_full_rebuild() {
    graft_and_compare(
        "edit-rs",
        |dir| {
            write(
                dir,
                "beta/lib.rs",
                "pub fn compute(x: u64) -> u64 {\n    x + 1\n}\n\npub fn twice(x: u64) -> u64 {\n    compute(compute(x))\n}\n",
            );
        },
        &["beta/lib.rs"],
    );
}

#[test]
fn incremental_update_defers_while_a_build_holds_the_lock() {
    let dir = corpus("locked");
    let ws = dir.to_str().expect("ws");
    let db = dir.join("store.kg");
    let db_str = db.to_str().expect("db");
    build_graph(ws, db_str, None, Some(1)).expect("full build");

    let _held = cairn_store::swap::build_lock(&db).expect("hold build lock");
    let err = cairn_build::incremental::incremental_update(ws, db_str, None, None)
        .expect_err("update must not write under a held build lock");
    assert!(err.contains("Cannot acquire build lock"), "{err}");
    let _ = fs::remove_dir_all(&dir);
}

/// A store opened before the lock is taken survives a full-build swap and
/// sends the pass's writes to the replaced file; the deferral must happen
/// before the store is ever touched.
#[test]
fn incremental_update_never_opens_the_store_before_the_build_lock() {
    let dir = corpus("open-under-lock");
    let ws = dir.to_str().expect("ws");
    let db = dir.join("store.kg");

    let _held = cairn_store::swap::build_lock(&db).expect("hold build lock");
    let err =
        cairn_build::incremental::incremental_update(ws, db.to_str().expect("db"), None, None)
            .expect_err("update must defer under a held build lock");
    assert!(err.contains("Cannot acquire build lock"), "{err}");
    assert!(
        !db.exists(),
        "store was opened (created/initialized) before the build lock was acquired"
    );
    let _ = fs::remove_dir_all(&dir);
}

/// Edges that resolve exact through receiver dispatch, the ancestor walk,
/// call arity, the global tier from another repo, or a unique namesake.
fn seed_resolver_signals(dir: &Path) {
    for (rel, src) in [
        (
            "alpha/models.py",
            "class A:\n    def run(self):\n        return 1\n\n\nclass B:\n    def run(self):\n        return 2\n",
        ),
        (
            "alpha/child.py",
            "from models import A\n\n\nclass C(A):\n    pass\n",
        ),
        ("alpha/lib1.py", "def f(a):\n    return a\n"),
        ("alpha/lib2.py", "def f(a, b):\n    return a\n"),
        (
            "alpha/main.py",
            "def go():\n    a = A()\n    a.run()\n    c = C()\n    c.run()\n    f(1)\n    return helper()\n",
        ),
        ("alpha/helpers.py", "def helper():\n    return 0\n"),
        ("alpha/user.py", "def use():\n    return shared_fn()\n"),
        ("beta/shared.py", "def shared_fn():\n    return 1\n"),
        (
            "alpha/shapes.py",
            "class Base:\n    def spin(self):\n        return 1\n\n\nclass Other:\n    def spin(self):\n        return 2\n",
        ),
        (
            "alpha/child2.py",
            "from shapes import Base as BB\n\n\nclass D(BB):\n    pass\n",
        ),
        (
            "alpha/main2.py",
            "def go2():\n    d = D()\n    return d.spin()\n",
        ),
    ] {
        write(dir, rel, src);
    }
}

fn append_tweak(rel: &'static str) -> impl FnOnce(&Path) {
    move |dir: &Path| {
        let src = fs::read_to_string(dir.join(rel)).expect("read fixture");
        write(dir, rel, &format!("{src}\n# tweak\n"));
    }
}

/// Reindexing a provider file must re-materialize the imports edges of
/// consumer repos that had no file reindexed (the delete-leg backfill nulls
/// them store-wide).
#[test]
fn graft_rematerializes_cross_repo_imports_edges() {
    graft_and_compare_on(
        "xrepo-imports",
        |dir| {
            write(
                dir,
                "beta/app.py",
                "from util import add\n\n\ndef go():\n    return add(1, 2)\n",
            );
        },
        append_tweak("alpha/util.py"),
        &["alpha/util.py"],
    );
}

#[test]
fn graft_keeps_signal_resolved_edges_equal_to_rebuild() {
    for (tag, rel) in [
        ("sig-receiver", "alpha/models.py"),
        ("sig-arity", "alpha/lib1.py"),
        ("sig-ancestor", "alpha/main.py"),
        ("sig-cross-repo", "beta/shared.py"),
        ("sig-aliased-base", "alpha/shapes.py"),
    ] {
        graft_and_compare_on(tag, seed_resolver_signals, append_tweak(rel), &[rel]);
    }
    graft_and_compare_on(
        "sig-new-namesake",
        seed_resolver_signals,
        |dir| write(dir, "alpha/helpers2.py", "def helper():\n    return 9\n"),
        &["alpha/helpers2.py"],
    );
}
