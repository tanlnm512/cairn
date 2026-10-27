//! `check-doc-links`: every relative link in docs/**/*.md and README.md resolves.

use std::fs;
use std::path::{Component, Path, PathBuf};
use std::process::ExitCode;
use std::sync::LazyLock;

use regex::Regex;
use walkdir::WalkDir;

const UNRESOLVABLE_PREFIXES: [&str; 3] = ["http", "mailto:", "#"];

static CODE_SPAN: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"`[^`]*`").unwrap());
static LINK_TARGET: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\]\(([^)]+)\)").unwrap());

pub(crate) fn repo_root() -> PathBuf {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let root = manifest.ancestors().nth(3).unwrap_or(manifest);
    fs::canonicalize(root).unwrap_or_else(|_| root.to_path_buf())
}

pub(crate) fn normalize(path: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for component in path.components() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => {
                if !out.pop() {
                    out.push("..");
                }
            }
            other => out.push(other),
        }
    }
    out
}

fn relpath(path: &Path, base: &Path) -> String {
    let path: Vec<_> = path.components().collect();
    let base: Vec<_> = base.components().collect();
    let common = path.iter().zip(&base).take_while(|(a, b)| a == b).count();
    let mut out = PathBuf::new();
    for _ in common..base.len() {
        out.push("..");
    }
    for component in &path[common..] {
        out.push(component);
    }
    if out.as_os_str().is_empty() {
        ".".into()
    } else {
        out.to_string_lossy().into_owned()
    }
}

fn read_text(path: &Path) -> String {
    fs::read(path)
        .map(|bytes| String::from_utf8_lossy(&bytes).into_owned())
        .unwrap_or_default()
}

fn scope_files(root: &Path, out: &mut String) -> Vec<PathBuf> {
    let mut files: Vec<PathBuf> = WalkDir::new(root.join("docs"))
        .into_iter()
        .filter_map(Result::ok)
        .filter(|e| e.file_type().is_file() && e.path().extension().is_some_and(|x| x == "md"))
        .map(|e| e.into_path())
        .collect();
    let readme = root.join("README.md");
    if readme.is_file() {
        files.push(readme);
    } else {
        out.push_str("note: README.md not present; skipped\n");
    }
    files.sort();
    files
}

fn link_targets(text: &str) -> Vec<String> {
    let stripped = CODE_SPAN.replace_all(text, " ");
    LINK_TARGET
        .captures_iter(&stripped)
        .filter_map(|cap| cap[1].split_whitespace().next().map(str::to_owned))
        .filter(|t| !UNRESOLVABLE_PREFIXES.iter().any(|p| t.starts_with(p)))
        .collect()
}

fn resolve_target(from_file: &Path, target: &str) -> PathBuf {
    let without_fragment = target.split('#').next().unwrap_or("");
    let dir = from_file.parent().unwrap_or(Path::new("."));
    let dir = fs::canonicalize(dir).unwrap_or_else(|_| dir.to_path_buf());
    let joined = normalize(&dir.join(without_fragment));
    fs::canonicalize(&joined).unwrap_or(joined)
}

pub(crate) fn report(root: &Path) -> (String, usize) {
    let mut out = String::new();
    let mut broken = 0;
    for md in scope_files(root, &mut out) {
        for target in link_targets(&read_text(&md)) {
            let resolved = resolve_target(&md, &target);
            if !resolved.exists() {
                broken += 1;
                out.push_str(&format!(
                    "{}: broken link [{target}] -> {}\n",
                    relpath(&md, root),
                    relpath(&resolved, root)
                ));
            }
        }
    }

    let index = fs::canonicalize(root.join("README.md")).unwrap_or_else(|_| root.join("README.md"));
    let mut pages: Vec<PathBuf> = fs::read_dir(root.join("docs"))
        .map(|rd| rd.filter_map(Result::ok).map(|e| e.path()).collect())
        .unwrap_or_default();
    pages.retain(|p| p.is_file() && p.extension().is_some_and(|x| x == "md"));
    pages.sort();
    let no_backlink: Vec<String> = pages
        .iter()
        .filter(|page| {
            !link_targets(&read_text(page))
                .iter()
                .any(|t| resolve_target(page, t) == index)
        })
        .map(|p| relpath(p, root))
        .collect();
    if !no_backlink.is_empty() {
        out.push_str(&format!(
            "warning (advisory, does not affect exit): {} docs/ page(s) have no link resolving to the repo README.md:\n",
            no_backlink.len()
        ));
        for page in no_backlink {
            out.push_str(&format!("  {page}\n"));
        }
    }
    out.push_str(&format!("TOTAL broken: {broken}\n"));
    (out, broken)
}

pub fn run(_args: &[String]) -> ExitCode {
    let (out, broken) = report(&repo_root());
    print!("{out}");
    if broken == 0 {
        ExitCode::SUCCESS
    } else {
        ExitCode::FAILURE
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extraction_skips_code_spans_external_and_anchor_targets() {
        let text = "[a](x.md) `[-> p](nope/)` [b](https://e.com) [c](#top) [d](y.md#s \"title\") [e](mailto:a@b)";
        assert_eq!(link_targets(text), vec!["x.md", "y.md#s"]);
    }

    #[test]
    fn committed_docs_have_no_broken_links() {
        let (out, broken) = report(&repo_root());
        assert_eq!(broken, 0, "{out}");
    }
}
