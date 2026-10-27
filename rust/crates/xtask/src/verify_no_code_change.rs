//! Verify a comments/docstrings-only change left every changed `.py` file's syntax tree intact.

use std::collections::HashSet;
use std::fmt::Write as _;
use std::path::Path;
use std::process::{Command, ExitCode};

use clap::Parser as _;
use tree_sitter::{Node, TreeCursor};

use crate::comment_style::{decode_source, docstrings, parse_python, string_literal_value};

#[derive(clap::Parser, Debug)]
#[command(
    name = "verify-no-code-change",
    about = "Exit 1 when a changed .py file differs beyond comments, docstrings, and whitespace."
)]
struct Args {
    /// Git ref to compare against (default: HEAD = uncommitted changes).
    #[arg(default_value = "HEAD")]
    git_ref: String,
}

/// Token-level dump of a Python module, blind to comments, docstring text, layout, quote style, and grouping parens.
fn code_dump(source: &str) -> Result<String, String> {
    let tree = parse_python(source)?;
    let blanked: HashSet<usize> = docstrings(&tree, source)
        .iter()
        .map(|doc| doc.statement.id())
        .collect();
    let mut out = String::new();
    dump_node(&mut tree.walk(), source, &blanked, &mut out);
    Ok(out)
}

fn dump_node(cursor: &mut TreeCursor, source: &str, blanked: &HashSet<usize>, out: &mut String) {
    let node: Node = cursor.node();
    if matches!(node.kind(), "comment" | "line_continuation") {
        return;
    }
    if let Some(field) = cursor.field_name() {
        out.push_str(field);
        out.push(':');
    }
    if blanked.contains(&node.id()) {
        out.push_str("(docstring)");
        return;
    }
    let text = &source[node.byte_range()];
    if let Some(value) = (node.kind() == "string")
        .then(|| string_literal_value(text))
        .flatten()
    {
        let _ = write!(out, "str={value:?} ");
        return;
    }
    if node.child_count() == 0 {
        let _ = write!(out, "{}={text:?} ", node.kind());
        return;
    }
    let grouping = node.kind() == "parenthesized_expression";
    if !grouping {
        out.push('(');
        out.push_str(node.kind());
        out.push(' ');
    }
    cursor.goto_first_child();
    loop {
        if !(grouping && matches!(cursor.node().kind(), "(" | ")")) {
            dump_node(cursor, source, blanked, out);
        }
        if !cursor.goto_next_sibling() {
            break;
        }
    }
    cursor.goto_parent();
    if !grouping {
        out.push_str(") ");
    }
}

fn git(repo: &Path, args: &[&str]) -> std::io::Result<std::process::Output> {
    Command::new("git").args(args).current_dir(repo).output()
}

fn git_show(repo: &Path, rev: &str, file: &str) -> Option<Vec<u8>> {
    git(repo, &["show", &format!("{rev}:{file}")])
        .ok()
        .filter(|out| out.status.success())
        .map(|out| out.stdout)
}

/// Changed `.py` files and the comparison target: `HEAD` for a clean tree, `None` for the working tree.
fn changed_files(
    repo: &Path,
    git_ref: &str,
) -> Result<(Vec<String>, Option<&'static str>), String> {
    let clean = git(repo, &["diff", "--quiet", "HEAD"]).is_ok_and(|out| out.status.success());
    let target = clean.then_some("HEAD");
    let range = target.map_or_else(|| git_ref.to_string(), |t| format!("{git_ref}..{t}"));
    let out = git(repo, &["diff", "--name-only", &range]).map_err(|err| err.to_string())?;
    if !out.status.success() {
        return Err(format!(
            "git diff --name-only {range} failed: {}",
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }
    let files = String::from_utf8_lossy(&out.stdout)
        .trim()
        .lines()
        .filter(|file| file.ends_with(".py"))
        .map(str::to_string)
        .collect();
    Ok((files, target))
}

fn dump_bytes(bytes: &[u8]) -> Result<String, String> {
    code_dump(&decode_source(bytes)?)
}

/// Return `true` when the file's code differs (or cannot be compared); `None` for a new file.
fn file_mismatch(repo: &Path, git_ref: &str, target: Option<&str>, file: &str) -> Option<bool> {
    let base = git_show(repo, git_ref, file)?;
    let base_dump = match dump_bytes(&base) {
        Ok(dump) => dump,
        Err(err) => {
            eprintln!("  {file}: baseline parse error: {err}");
            return Some(true);
        }
    };
    let (new, label) = match target {
        Some(rev) => match git_show(repo, rev, file) {
            Some(bytes) => (bytes, rev.to_string()),
            None => {
                eprintln!("  {file}: deleted at {rev}");
                return Some(true);
            }
        },
        None => match std::fs::read(repo.join(file)) {
            Ok(bytes) => (bytes, "working-tree".to_string()),
            Err(_) => {
                eprintln!("  {file}: deleted from the working tree");
                return Some(true);
            }
        },
    };
    match dump_bytes(&new) {
        Ok(new_dump) => Some(new_dump != base_dump),
        Err(err) => {
            eprintln!("  {file}: {label} parse error: {err}");
            Some(true)
        }
    }
}

fn verify(repo: &Path, git_ref: &str) -> u8 {
    let (files, target) = match changed_files(repo, git_ref) {
        Ok(found) => found,
        Err(err) => {
            eprintln!("verify-no-code-change: error: {err}");
            return 1;
        }
    };
    if files.is_empty() {
        println!("No changed .py files to verify.");
        return 0;
    }
    let mismatches: Vec<&String> = files
        .iter()
        .filter(|file| file_mismatch(repo, git_ref, target, file) == Some(true))
        .collect();
    let checked = files.len();
    if mismatches.is_empty() {
        println!(
            "OK: {checked} changed .py file(s) — comment/docstring changes only (AST identical)."
        );
        return 0;
    }
    eprintln!(
        "FAIL: {} of {checked} changed .py file(s) have executable-code differences \
         (docstrings stripped, AST still differs):",
        mismatches.len()
    );
    for file in mismatches {
        eprintln!("  {file}");
    }
    eprintln!(
        "\nRun `git diff <ref> -- <files>` and confirm whether the code change is intended. \
         If it is, this check isn't the problem — the commit message is."
    );
    1
}

/// Compare changed `.py` files between a git ref and HEAD (clean tree) or the working tree (dirty).
pub fn run(args: &[String]) -> ExitCode {
    let argv = std::iter::once("verify-no-code-change".to_string()).chain(args.iter().cloned());
    let parsed = match Args::try_parse_from(argv) {
        Ok(parsed) => parsed,
        Err(err) => {
            let _ = err.print();
            return ExitCode::from(u8::try_from(err.exit_code()).unwrap_or(2));
        }
    };
    let cwd = std::env::current_dir().unwrap_or_default();
    ExitCode::from(verify(&cwd, &parsed.git_ref))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::PathBuf;

    const BASE_PY: &str = "\"\"\"Original docstring.\"\"\"\n\n\ndef f():\n    return 'one'\n";

    fn git_ok(repo: &Path, args: &[&str]) {
        let out = git(repo, args).unwrap();
        assert!(out.status.success(), "git {args:?} failed");
    }

    fn init_repo(name: &str) -> PathBuf {
        let repo = std::env::temp_dir().join(format!(
            "xtask-verify-no-code-change-{}-{name}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&repo);
        fs::create_dir_all(&repo).unwrap();
        git_ok(&repo, &["init", "-q"]);
        fs::write(repo.join("mod.py"), BASE_PY).unwrap();
        git_ok(&repo, &["add", "."]);
        commit(&repo, "base");
        repo
    }

    fn commit(repo: &Path, message: &str) {
        git_ok(
            repo,
            &[
                "-c",
                "user.email=t@example.com",
                "-c",
                "user.name=T",
                "commit",
                "-qm",
                message,
            ],
        );
    }

    #[test]
    fn dump_ignores_comments_docstrings_and_layout_but_not_code() {
        let base = code_dump(BASE_PY).unwrap();
        let cosmetic = "\"\"\"Edited\n   docstring.\"\"\"\n# added\ndef f( ):\n    return (\n        \"one\"  # trailing\n    )\n";
        assert_eq!(code_dump(cosmetic).unwrap(), base);
        assert_ne!(code_dump(&BASE_PY.replace("'one'", "'two'")).unwrap(), base);
        assert_ne!(
            code_dump(&BASE_PY.replace("\"\"\"Original docstring.\"\"\"", "f'x'")).unwrap(),
            base
        );
    }

    #[test]
    fn comment_only_edit_verifies_clean_in_tree_mode() {
        let repo = init_repo("comment-only");
        fs::write(
            repo.join("mod.py"),
            BASE_PY
                .replace("Original", "Edited")
                .replace("    return", "    # note\n    return"),
        )
        .unwrap();
        assert_eq!(verify(&repo, "HEAD"), 0);
        fs::remove_dir_all(repo).unwrap();
    }

    #[test]
    fn deleted_file_fails_in_tree_and_commit_mode() {
        let repo = init_repo("deleted");
        fs::remove_file(repo.join("mod.py")).unwrap();
        assert_eq!(verify(&repo, "HEAD"), 1);

        git_ok(&repo, &["add", "-A"]);
        commit(&repo, "delete mod.py");
        assert_eq!(verify(&repo, "HEAD~1"), 1);
        fs::remove_dir_all(repo).unwrap();
    }
}
