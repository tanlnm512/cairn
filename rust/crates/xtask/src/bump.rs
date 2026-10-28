//! `bump`: next SemVer from the conventional commits since the last tag; moves every version file, commits, and tags.

use std::fs;
use std::path::Path;
use std::process::{Command, ExitCode};
use std::sync::LazyLock;

use clap::Parser as _;
use regex::Regex;

use crate::check_doc_links::repo_root;

static CONVENTIONAL: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^(\w+)(?:\(([^)]*)\))?(!)?: (.+)$").unwrap());
static BREAKING_FOOTER: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?m)^BREAKING[- ]CHANGE:").unwrap());

#[derive(clap::Parser)]
#[command(
    name = "bump",
    about = "Bump the release version from the conventional commits since the last tag."
)]
struct Args {
    /// Print the next version and a draft changelog section; write nothing.
    #[arg(long, conflicts_with = "files_only")]
    dry_run: bool,
    /// Update the version files without committing or tagging.
    #[arg(long)]
    files_only: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum Increment {
    Patch,
    Minor,
    Major,
}

struct Commit {
    subject: String,
    body: String,
}

fn increment(commit: &Commit) -> Option<Increment> {
    if BREAKING_FOOTER.is_match(&commit.body) {
        return Some(Increment::Major);
    }
    let caps = CONVENTIONAL.captures(&commit.subject)?;
    if caps.get(3).is_some() {
        return Some(Increment::Major);
    }
    match &caps[1] {
        "feat" => Some(Increment::Minor),
        "fix" | "refactor" | "perf" => Some(Increment::Patch),
        _ => None,
    }
}

fn next_version(current: &str, increment: Increment) -> Result<String, String> {
    let parts: Vec<u64> = current
        .split('.')
        .map(str::parse)
        .collect::<Result<_, _>>()
        .map_err(|_| format!("version {current:?} is not MAJOR.MINOR.PATCH"))?;
    let [major, minor, patch] = parts[..] else {
        return Err(format!("version {current:?} is not MAJOR.MINOR.PATCH"));
    };
    Ok(match increment {
        Increment::Major => format!("{}.0.0", major + 1),
        Increment::Minor => format!("{major}.{}.0", minor + 1),
        Increment::Patch => format!("{major}.{minor}.{}", patch + 1),
    })
}

/// Rewrite `version = "<old>"` inside the `[section]` table of a TOML document.
fn set_section_version(text: &str, section: &str, old: &str, new: &str) -> Result<String, String> {
    let header = format!("[{section}]\n");
    let start = text
        .find(&header)
        .ok_or_else(|| format!("[{section}] not found"))?
        + header.len();
    let end = text[start..]
        .find("\n[")
        .map_or(text.len(), |offset| start + offset);
    let needle = format!("version = \"{old}\"");
    let at = text[start..end]
        .lines()
        .scan(start, |offset, line| {
            let line_start = *offset;
            *offset += line.len() + 1;
            Some((line_start, line))
        })
        .find(|(_, line)| line.starts_with(&needle))
        .map(|(line_start, _)| line_start)
        .ok_or_else(|| format!("[{section}] has no `{needle}`"))?;
    Ok(format!(
        "{}version = \"{new}\"{}",
        &text[..at],
        &text[at + needle.len()..]
    ))
}

fn changelog_draft(version: &str, date: &str, commits: &[Commit]) -> String {
    let mut sections: [(&str, Vec<String>); 3] = [
        ("Added", Vec::new()),
        ("Changed", Vec::new()),
        ("Fixed", Vec::new()),
    ];
    for commit in commits {
        let Some(caps) = CONVENTIONAL.captures(&commit.subject) else {
            continue;
        };
        let slot = match &caps[1] {
            "feat" => 0,
            "refactor" | "perf" => 1,
            "fix" => 2,
            _ => continue,
        };
        let entry = match caps.get(2) {
            Some(scope) => format!("- **{}**: {}", scope.as_str(), &caps[4]),
            None => format!("- {}", &caps[4]),
        };
        sections[slot].1.push(entry);
    }
    let mut out = format!("## [{version}] - {date}\n");
    for (title, entries) in sections.iter().filter(|(_, e)| !e.is_empty()) {
        out.push_str(&format!("\n### {title}\n{}\n", entries.join("\n")));
    }
    out
}

fn git(root: &Path, args: &[&str]) -> Result<String, String> {
    let output = Command::new("git")
        .args(args)
        .current_dir(root)
        .output()
        .map_err(|e| format!("git {}: {e}", args.join(" ")))?;
    if !output.status.success() {
        return Err(format!(
            "git {}: {}",
            args.join(" "),
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}

fn commits_since(root: &Path, tag: &str) -> Result<Vec<Commit>, String> {
    let log = git(
        root,
        &["log", &format!("{tag}..HEAD"), "--format=%s%x1f%b%x1e"],
    )?;
    Ok(log
        .split('\x1e')
        .filter_map(|record| {
            let (subject, body) = record.trim_start_matches('\n').split_once('\x1f')?;
            Some(Commit {
                subject: subject.to_string(),
                body: body.to_string(),
            })
        })
        .collect())
}

fn workspace_version(cargo_toml: &str) -> Result<String, String> {
    let section = cargo_toml
        .split_once("[workspace.package]\n")
        .ok_or("[workspace.package] not found in Cargo.toml")?
        .1;
    Regex::new(r#"(?m)^version = "([^"]+)""#)
        .unwrap()
        .captures(section)
        .map(|caps| caps[1].to_string())
        .ok_or_else(|| "[workspace.package] has no version".to_string())
}

fn execute(args: &Args) -> Result<(), String> {
    let root = repo_root();
    let cargo_path = root.join("Cargo.toml");
    let cargo_toml = fs::read_to_string(&cargo_path).map_err(|e| e.to_string())?;
    let current = workspace_version(&cargo_toml)?;
    let tag = format!("v{current}");
    git(
        &root,
        &[
            "rev-parse",
            "--verify",
            "--quiet",
            &format!("refs/tags/{tag}"),
        ],
    )
    .map_err(|_| format!("no tag {tag} for the workspace version {current}"))?;

    let commits = commits_since(&root, &tag)?;
    let Some(bump) = commits.iter().filter_map(increment).max() else {
        return Err(format!(
            "no feat/fix/refactor/perf or breaking commits since {tag}"
        ));
    };
    let next = next_version(&current, bump)?;
    println!("bump: version {current} → {next} ({bump:?})");

    if args.dry_run {
        let today = &cairn_build::timefmt::now_iso()[..10];
        println!("files: Cargo.toml, Cargo.lock\n");
        print!("{}", changelog_draft(&next, today, &commits));
        return Ok(());
    }
    if !args.files_only && !git(&root, &["status", "--porcelain"])?.trim().is_empty() {
        return Err("working tree is dirty; commit or stash first".into());
    }

    let cargo_toml = set_section_version(&cargo_toml, "workspace.package", &current, &next)?;
    fs::write(&cargo_path, cargo_toml).map_err(|e| e.to_string())?;
    let status = Command::new("cargo")
        .args(["update", "--workspace", "--offline"])
        .current_dir(&root)
        .status()
        .map_err(|e| format!("cargo update: {e}"))?;
    if !status.success() {
        return Err("cargo update --workspace failed".into());
    }
    if args.files_only {
        return Ok(());
    }

    git(&root, &["add", "Cargo.toml", "Cargo.lock"])?;
    git(
        &root,
        &["commit", "-m", &format!("bump: version {current} → {next}")],
    )?;
    git(&root, &["tag", &format!("v{next}")])?;
    println!("tagged v{next}");
    Ok(())
}

/// Run the release bump; `--dry-run` previews, `--files-only` skips the commit and tag.
pub fn run(args: &[String]) -> ExitCode {
    let argv = std::iter::once("bump".to_string()).chain(args.iter().cloned());
    let parsed = match Args::try_parse_from(argv) {
        Ok(parsed) => parsed,
        Err(err) => {
            let _ = err.print();
            return ExitCode::from(u8::try_from(err.exit_code()).unwrap_or(2));
        }
    };
    match execute(&parsed) {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("xtask bump: {e}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn commit(subject: &str, body: &str) -> Commit {
        Commit {
            subject: subject.into(),
            body: body.into(),
        }
    }

    #[test]
    fn commit_types_map_to_the_semver_increment() {
        for (subject, body, expected, next) in [
            ("feat(cli): add x", "", Some(Increment::Minor), "0.25.0"),
            ("fix: y", "", Some(Increment::Patch), "0.24.2"),
            ("refactor: z", "", Some(Increment::Patch), "0.24.2"),
            ("perf(store): w", "", Some(Increment::Patch), "0.24.2"),
            ("feat!: drop v", "", Some(Increment::Major), "1.0.0"),
            (
                "fix: u",
                "BREAKING CHANGE: gone",
                Some(Increment::Major),
                "1.0.0",
            ),
            ("docs: d", "", None, ""),
            ("Merge pull request #1 from a/b", "", None, ""),
        ] {
            let found = increment(&commit(subject, body));
            assert_eq!(found, expected, "{subject}");
            if let Some(found) = found {
                assert_eq!(next_version("0.24.1", found).unwrap(), next, "{subject}");
            }
        }
        assert!(next_version("1.0.0rc1", Increment::Patch).is_err());
    }

    #[test]
    fn only_the_named_tables_version_moves() {
        let doc = "[package]\nversion = \"0.24.1\"\n\n[workspace.package]\nedition = \"2024\"\nversion = \"0.24.1\" # shared\n\n[deps]\nversion = \"0.24.1\"\n";
        let moved = set_section_version(doc, "workspace.package", "0.24.1", "0.25.0").unwrap();
        assert_eq!(
            moved,
            doc.replace(
                "version = \"0.24.1\" # shared",
                "version = \"0.25.0\" # shared"
            )
        );
        assert!(set_section_version(doc, "workspace.package", "9.9.9", "1.0.0").is_err());
        assert!(set_section_version(doc, "missing", "0.24.1", "1.0.0").is_err());
    }

    #[test]
    fn changelog_draft_groups_by_type_in_keep_a_changelog_shape() {
        let commits = [
            commit("fix(store): b", ""),
            commit("feat: a", ""),
            commit("docs: skipped", ""),
            commit("perf: c", ""),
        ];
        assert_eq!(
            changelog_draft("0.25.0", "2026-10-28", &commits),
            "## [0.25.0] - 2026-10-28\n\n### Added\n- a\n\n### Changed\n- c\n\n### Fixed\n- **store**: b\n"
        );
    }
}
