//! Bounded auto-generation of missing SCIP indexes via known indexer binaries.

use std::path::Path;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

/// A real index run is a heavyweight step (full compile); bound it generously.
const INDEX_TIMEOUT: Duration = Duration::from_secs(30 * 60);

/// skipped_files.reason values for generation failures; the column is CHECK-free
/// TEXT and skipped_by_reason aggregates whatever values land there.
pub const GEN_MISSING_BINARY: &str = "scip_gen_missing_binary";
pub const GEN_NONZERO_EXIT: &str = "scip_gen_nonzero_exit";
pub const GEN_TIMEOUT: &str = "scip_gen_timeout";
pub const GEN_OS_ERROR: &str = "scip_gen_os_error";
/// No registered indexer for the language, or a scip.commands entry that
/// cannot express a command.
pub const GEN_MISCONFIGURED: &str = "scip_gen_misconfigured";

/// How to invoke one known SCIP indexer: PATH binary, argv template, env template, install hint.
#[derive(Debug, Clone, Copy)]
pub struct IndexerSpec {
    pub language: &'static str,
    pub tool: &'static str,
    pub build_command: fn(repo: &str, out: &str) -> Vec<String>,
    pub env: fn(out: &str) -> Vec<(String, String)>,
    pub install_hint: &'static str,
}

fn no_env(_out: &str) -> Vec<(String, String)> {
    Vec::new()
}

fn swift_cmd(repo: &str, out: &str) -> Vec<String> {
    // scip-swift: `scip-swift index <repo> --output <out>` (macOS/Xcode only).
    cmd(vec!["scip-swift", "index", repo, "--output", out])
}

fn scip_java_cmd(_repo: &str, out: &str) -> Vec<String> {
    // scip-java: `scip-java index --output <out>`, run from the project root.
    cmd(vec!["scip-java", "index", "--output", out])
}

fn scip_typescript_cmd(_repo: &str, out: &str) -> Vec<String> {
    // scip-typescript: `scip-typescript index --output <out>`.
    cmd(vec!["scip-typescript", "index", "--output", out])
}

fn scip_python_cmd(repo: &str, out: &str) -> Vec<String> {
    // scip-python: `scip-python index <repo> --output=<out>`; npm, not pip.
    cmd(vec![
        "scip-python",
        "index",
        repo,
        &format!("--output={out}"),
    ])
}

fn scip_go_cmd(_repo: &str, out: &str) -> Vec<String> {
    // scip-go: `scip-go --output=<out>` (no `index` subcommand).
    cmd(vec!["scip-go", &format!("--output={out}")])
}

fn rust_cmd(repo: &str, _out: &str) -> Vec<String> {
    // rust-analyzer scip subcommand: the output path comes from RA_SCIPOUT, not a flag.
    cmd(vec!["rust-analyzer", "scip", repo])
}

fn rust_env(out: &str) -> Vec<(String, String)> {
    vec![("RA_SCIPOUT".to_string(), out.to_string())]
}

fn cmd(parts: Vec<&str>) -> Vec<String> {
    parts.into_iter().map(str::to_string).collect()
}

const SCIP_JAVA_HINT: &str = "scip-java is the canonical SCIP indexer for Java AND Kotlin (the old \
scip-kotlin has been merged in). It requires a Gradle or Maven build. \
CAVEAT: plain `scip-java index` is known to fail on some Android/Gradle \
builds; for those, generate the index with a project-specific pipeline \
and point `scip.commands` at it (or commit the index and list it in \
`scip.indexes`). See https://github.com/sourcegraph/scip-java";

/// Keyed by the scanner language name (the `cairn.json` `scip.indexes` key).
/// Languages not in this registry are never auto-generated; a committed index
/// for them is still consumed unchanged.
const KNOWN_INDEXERS: &[IndexerSpec] = &[
    IndexerSpec {
        language: "go",
        tool: "scip-go",
        build_command: scip_go_cmd,
        env: no_env,
        install_hint: "scip-go is the SCIP indexer for Go. Install: \
`go install github.com/scip-code/scip-go/cmd/scip-go@latest`. \
See https://github.com/scip-code/scip-go",
    },
    IndexerSpec {
        language: "java",
        tool: "scip-java",
        build_command: scip_java_cmd,
        env: no_env,
        install_hint: SCIP_JAVA_HINT,
    },
    IndexerSpec {
        // scip-kotlin is archived; scip-java indexes mixed Java+Kotlin sources.
        language: "kotlin",
        tool: "scip-java",
        build_command: scip_java_cmd,
        env: no_env,
        install_hint: SCIP_JAVA_HINT,
    },
    IndexerSpec {
        language: "python",
        tool: "scip-python",
        build_command: scip_python_cmd,
        env: no_env,
        install_hint: "scip-python is the SCIP indexer for Python. It's an npm package \
(@sourcegraph/scip-python), not pip: \
`npm install -g @sourcegraph/scip-python`. \
See https://github.com/sourcegraph/scip-python",
    },
    IndexerSpec {
        language: "rust",
        tool: "rust-analyzer",
        build_command: rust_cmd,
        env: rust_env,
        install_hint: "rust-analyzer's `scip` subcommand indexes Rust (output via the \
RA_SCIPOUT env var). Install rust-analyzer via rustup or your \
toolchain. See https://github.com/rust-lang/rust-analyzer",
    },
    IndexerSpec {
        language: "swift",
        tool: "scip-swift",
        build_command: swift_cmd,
        env: no_env,
        install_hint: "scip-swift is the SCIP indexer for Swift. Build it from source on a \
Mac (macOS/Xcode only): https://github.com/phuongddx/scip-swift",
    },
    IndexerSpec {
        language: "typescript",
        tool: "scip-typescript",
        build_command: scip_typescript_cmd,
        env: no_env,
        install_hint: "scip-typescript is the SCIP indexer for TypeScript/JavaScript. \
Install: `npm install -g @sourcegraph/scip-typescript`. \
See https://github.com/sourcegraph/scip-typescript",
    },
];

/// Languages cairn can auto-generate a missing index for, sorted.
pub fn known_languages() -> Vec<&'static str> {
    let mut languages: Vec<&'static str> = KNOWN_INDEXERS.iter().map(|s| s.language).collect();
    languages.sort_unstable();
    languages
}

/// The registered indexer spec for `language`, or None.
pub fn spec_for(language: &str) -> Option<&'static IndexerSpec> {
    KNOWN_INDEXERS.iter().find(|spec| spec.language == language)
}

/// One generation attempt: ok=true, or a scip_gen_* reason with a human detail.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GenerationResult {
    pub ok: bool,
    pub reason: Option<&'static str>,
    pub detail: String,
}

impl GenerationResult {
    fn success() -> Self {
        Self {
            ok: true,
            reason: None,
            detail: String::new(),
        }
    }
}

/// Degrade one attempt: log the cause and the install hint, return the skip outcome.
fn failure(
    spec: &IndexerSpec,
    reason: &'static str,
    detail: &str,
    log: &mut dyn FnMut(&str),
) -> GenerationResult {
    let prefix = format!("  scip[{}]: ", spec.language);
    log(&format!("{prefix}{detail}; falling back to tree-sitter"));
    log(&format!("{prefix}{}", spec.install_hint));
    GenerationResult {
        ok: false,
        reason: Some(reason),
        detail: format!("{detail}; {}", spec.install_hint),
    }
}

/// Attempt one bounded index generation, classifying failure as a scip_gen_* reason. Never panics.
pub fn generate_index_result(
    language: &str,
    output_path: &Path,
    repo_path: &str,
    log: &mut dyn FnMut(&str),
) -> GenerationResult {
    let Some(spec) = spec_for(language) else {
        return GenerationResult {
            ok: false,
            reason: Some(GEN_MISCONFIGURED),
            detail: format!("no registered indexer for '{language}'"),
        };
    };

    if output_path.exists() {
        // Never rebuild an existing index -- the user (or CI) owns regeneration.
        return GenerationResult::success();
    }

    let path_env = std::env::var("PATH").unwrap_or_default();
    match run_tool(spec, output_path, repo_path, &path_env, INDEX_TIMEOUT, log) {
        Ok(()) => GenerationResult::success(),
        Err(ToolFailure::Timeout) => failure(
            spec,
            GEN_TIMEOUT,
            &format!("{} timed out after {}s", spec.tool, INDEX_TIMEOUT.as_secs()),
            log,
        ),
        Err(ToolFailure::Invocation(e)) => failure(
            spec,
            GEN_OS_ERROR,
            &format!("{} invocation failed ({e})", spec.tool),
            log,
        ),
        Err(ToolFailure::MissingBinary) => failure(
            spec,
            GEN_MISSING_BINARY,
            &format!("index missing and '{}' not on PATH", spec.tool),
            log,
        ),
        Err(ToolFailure::NonZeroExit(code)) => failure(
            spec,
            GEN_NONZERO_EXIT,
            &format!("{0} exited {code} without producing an index", spec.tool),
            log,
        ),
    }
}

/// Run a `scip.commands`-configured indexer: `$out`/`$repo` argv template,
/// argv[0] resolved on PATH, same failure taxonomy as the registry — for
/// builds the fixed per-language specs cannot express.
pub fn run_configured_command(
    language: &str,
    argv_template: &[String],
    output_path: &Path,
    repo_path: &str,
    log: &mut dyn FnMut(&str),
) -> GenerationResult {
    if output_path.exists() {
        // Never rebuild an existing index -- the user (or CI) owns regeneration.
        return GenerationResult::success();
    }
    let argv: Vec<String> = argv_template
        .iter()
        .map(|part| {
            part.replace("$out", &output_path.to_string_lossy())
                .replace("$repo", repo_path)
        })
        .collect();
    let Some((tool, args)) = argv.split_first() else {
        return GenerationResult {
            ok: false,
            reason: Some(GEN_MISCONFIGURED),
            detail: "empty scip.commands entry".to_string(),
        };
    };
    let path_env = std::env::var("PATH").unwrap_or_default();
    let prefix = format!("  scip[{language}]: ");
    match exec_generation(
        &prefix,
        tool,
        args,
        &[],
        &path_env,
        repo_path,
        output_path,
        INDEX_TIMEOUT,
        log,
    ) {
        Ok(()) => GenerationResult::success(),
        Err(ToolFailure::Timeout) => configured_fail(
            &prefix,
            log,
            GEN_TIMEOUT,
            format!("{tool} timed out after {}s", INDEX_TIMEOUT.as_secs()),
        ),
        Err(ToolFailure::Invocation(e)) => configured_fail(
            &prefix,
            log,
            GEN_OS_ERROR,
            format!("{tool} invocation failed ({e})"),
        ),
        Err(ToolFailure::MissingBinary) => configured_fail(
            &prefix,
            log,
            GEN_MISSING_BINARY,
            format!("index missing and '{tool}' not on PATH"),
        ),
        Err(ToolFailure::NonZeroExit(code)) => configured_fail(
            &prefix,
            log,
            GEN_NONZERO_EXIT,
            format!("{tool} exited {code} without producing an index"),
        ),
    }
}

/// `run_configured_command` failure wrapper: log the detail and return the
/// degraded result; a separate fn so the `log` borrow ends before use.
fn configured_fail(
    prefix: &str,
    log: &mut dyn FnMut(&str),
    reason: &'static str,
    detail: String,
) -> GenerationResult {
    log(&format!("{prefix}{detail}"));
    GenerationResult {
        ok: false,
        reason: Some(reason),
        detail,
    }
}

enum ToolFailure {
    MissingBinary,
    NonZeroExit(i32),
    Timeout,
    Invocation(String),
}

/// Shared generation pipeline of the registry and configured-command paths:
/// prepare the output parent, resolve the tool on PATH, log, and exec.
#[allow(clippy::too_many_arguments)]
fn exec_generation(
    prefix: &str,
    tool: &str,
    args: &[String],
    env: &[(String, String)],
    path_env: &str,
    repo_path: &str,
    output_path: &Path,
    timeout: Duration,
    log: &mut dyn FnMut(&str),
) -> Result<(), ToolFailure> {
    if let Some(parent) = output_path.parent()
        && let Err(e) = std::fs::create_dir_all(parent)
    {
        return Err(ToolFailure::Invocation(e.to_string()));
    }
    // Execute the which-resolved binary so the probe and the exec cannot
    // disagree about what is on PATH.
    let resolved = find_on_path(path_env, tool).ok_or(ToolFailure::MissingBinary)?;
    log(&format!(
        "{prefix}generating index with {tool} -> {}",
        output_path.display()
    ));
    exec_tool_argv(resolved, args, env, repo_path, output_path, timeout, log)
}

/// Run the spec's indexer once under the bounded timeout and classify the outcome.
fn run_tool(
    spec: &IndexerSpec,
    output_path: &Path,
    repo_path: &str,
    path_env: &str,
    timeout: Duration,
    log: &mut dyn FnMut(&str),
) -> Result<(), ToolFailure> {
    let argv = (spec.build_command)(repo_path, &output_path.to_string_lossy());
    let Some((_, args)) = argv.split_first() else {
        return Err(ToolFailure::Invocation(
            "empty command template".to_string(),
        ));
    };
    exec_generation(
        &format!("  scip[{}]: ", spec.language),
        spec.tool,
        args,
        &(spec.env)(&output_path.to_string_lossy()),
        path_env,
        repo_path,
        output_path,
        timeout,
        log,
    )
}

/// Spawn the resolved indexer binary and babysit it to completion: drained
/// pipes, deadline enforcement, and the nonzero/missing-output contract.
#[allow(clippy::too_many_arguments)]
fn exec_tool_argv(
    resolved: std::path::PathBuf,
    args: &[String],
    env: &[(String, String)],
    repo_path: &str,
    output_path: &Path,
    timeout: Duration,
    log: &mut dyn FnMut(&str),
) -> Result<(), ToolFailure> {
    let mut command = Command::new(resolved);
    command
        .args(args)
        .current_dir(repo_path)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    command.envs(env.iter().cloned());

    let mut child = command
        .spawn()
        .map_err(|e| ToolFailure::Invocation(e.to_string()))?;
    // Drain both pipes so a chatty indexer cannot deadlock on full buffers.
    let mut stdout_pipe = child.stdout.take();
    let mut stderr_pipe = child.stderr.take();
    let out_handle = std::thread::spawn(move || {
        let mut buf = Vec::new();
        if let Some(pipe) = stdout_pipe.as_mut() {
            let _ = std::io::Read::read_to_end(pipe, &mut buf);
        }
        buf
    });
    let err_handle = std::thread::spawn(move || {
        let mut buf = Vec::new();
        if let Some(pipe) = stderr_pipe.as_mut() {
            let _ = std::io::Read::read_to_end(pipe, &mut buf);
        }
        buf
    });

    let deadline = Instant::now() + timeout;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Some(status),
            Ok(None) if Instant::now() >= deadline => {
                let _ = child.kill();
                let _ = child.wait();
                break None;
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(50)),
            Err(e) => return Err(ToolFailure::Invocation(e.to_string())),
        }
    };
    let stderr = err_handle.join().unwrap_or_default();
    let _stdout = out_handle.join().unwrap_or_default();

    let Some(status) = status else {
        return Err(ToolFailure::Timeout);
    };
    let code = status.code().unwrap_or(-1);
    if code != 0 || !output_path.exists() {
        let stderr_text = String::from_utf8_lossy(&stderr).into_owned();
        let all_lines: Vec<&str> = stderr_text.trim().lines().collect();
        let tail = &all_lines[all_lines.len().saturating_sub(5)..];
        if !tail.is_empty() {
            log(&format!("    {}", tail.join("\n    ")));
        }
        return Err(ToolFailure::NonZeroExit(code));
    }
    Ok(())
}

/// shutil.which counterpart: first executable `tool` on a PATH-style string.
fn find_on_path(path_env: &str, tool: &str) -> Option<std::path::PathBuf> {
    if tool.contains('/') {
        return exec_bit(std::path::Path::new(tool)).then(|| tool.into());
    }
    let path_var: std::ffi::OsString = path_env.into();
    std::env::split_paths(&path_var).find_map(|dir| {
        cairn_store::exec::exec_candidates(&dir, tool)
            .into_iter()
            .find(|cand| exec_bit(cand))
    })
}

/// isfile + X_OK, the two checks shutil.which makes on POSIX; isfile on Windows.
fn exec_bit(path: &Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        path.is_file() && std::fs::metadata(path).is_ok_and(|m| m.permissions().mode() & 0o111 != 0)
    }
    #[cfg(not(unix))]
    {
        path.is_file()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn registry_covers_the_language_table() {
        assert_eq!(
            known_languages(),
            vec![
                "go",
                "java",
                "kotlin",
                "python",
                "rust",
                "swift",
                "typescript"
            ]
        );
        assert_eq!(spec_for("kotlin").map(|s| s.tool), Some("scip-java"));
        assert!(spec_for("cobol").is_none());
    }

    #[test]
    fn build_commands_match_the_templates() {
        let cases: &[(&str, &str, &str, Vec<&str>)] = &[
            (
                "swift",
                "/repo",
                "/out/index.scip",
                vec![
                    "scip-swift",
                    "index",
                    "/repo",
                    "--output",
                    "/out/index.scip",
                ],
            ),
            (
                "java",
                "/repo",
                "/out/index.scip",
                vec!["scip-java", "index", "--output", "/out/index.scip"],
            ),
            (
                "kotlin",
                "/repo",
                "/out/index.scip",
                vec!["scip-java", "index", "--output", "/out/index.scip"],
            ),
            (
                "typescript",
                "/repo",
                "/out/index.scip",
                vec!["scip-typescript", "index", "--output", "/out/index.scip"],
            ),
            (
                "python",
                "/repo",
                "/out/index.scip",
                vec!["scip-python", "index", "/repo", "--output=/out/index.scip"],
            ),
            (
                "go",
                "/repo",
                "/out/index.scip",
                vec!["scip-go", "--output=/out/index.scip"],
            ),
            (
                "rust",
                "/repo",
                "/out/index.scip",
                vec!["rust-analyzer", "scip", "/repo"],
            ),
        ];
        for (language, repo, out, expected) in cases {
            let spec = spec_for(language).expect("registered");
            assert_eq!(
                (spec.build_command)(repo, out),
                *expected,
                "language {language}"
            );
        }
        assert_eq!(
            (spec_for("rust").unwrap().env)("/out/index.scip"),
            vec![("RA_SCIPOUT".to_string(), "/out/index.scip".to_string())]
        );
        for language in ["swift", "java", "kotlin", "typescript", "python", "go"] {
            assert!(
                (spec_for(language).unwrap().env)("/o").is_empty(),
                "{language}"
            );
        }
    }

    #[test]
    fn unregistered_language_and_existing_index_short_circuit() {
        let dir = std::env::temp_dir().join(format!("scip-gen-{}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("temp");
        let out = dir.join("short-circuit.scip");

        let mut logs: Vec<String> = Vec::new();
        let res =
            generate_index_result("cobol", &out, "/nonexistent", &mut |l| logs.push(l.into()));
        assert!(!res.ok);
        assert_eq!(res.reason, Some(GEN_MISCONFIGURED));
        assert_eq!(res.detail, "no registered indexer for 'cobol'");

        std::fs::write(&out, b"scip-bytes").expect("seed output");
        let res = generate_index_result("python", &out, "/nonexistent", &mut |_| {});
        assert!(res.ok);
        assert_eq!(res.reason, None);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    #[cfg(unix)]
    fn run_tool_classifies_missing_binary_nonzero_exit_and_success() {
        let dir = std::env::temp_dir().join(format!("scip-run-{}", std::process::id()));
        let bin = dir.join("bin");
        std::fs::create_dir_all(&bin).expect("bin dir");
        let fake = bin.join("scip-fake");
        std::fs::write(&fake, "#!/bin/sh\necho boom >&2\nexit 3\n").expect("fake script");
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o755)).expect("chmod");

        let spec = IndexerSpec {
            language: "fixture",
            tool: "scip-fake",
            build_command: |_repo, _out| vec!["scip-fake".to_string()],
            env: no_env,
            install_hint: "install the fixture tool",
        };
        let out = dir.join("index.scip");

        // Missing binary: PATH without the bin dir.
        let mut logs: Vec<String> = Vec::new();
        let res = generate_with(&spec, &out, &dir.to_string_lossy(), "", &mut |l| {
            logs.push(l.to_string())
        });
        assert!(!res.ok);
        assert_eq!(res.reason, Some(GEN_MISSING_BINARY));
        assert!(res.detail.contains("not on PATH"), "{}", res.detail);
        assert!(
            res.detail.ends_with("; install the fixture tool"),
            "{}",
            res.detail
        );
        assert!(
            logs.iter()
                .any(|l| l.contains("falling back to tree-sitter"))
        );

        // Nonzero exit: stderr tail is logged, reason is scip_gen_nonzero_exit.
        let mut logs: Vec<String> = Vec::new();
        let res = generate_with(
            &spec,
            &out,
            &dir.to_string_lossy(),
            &bin.to_string_lossy(),
            &mut |l| logs.push(l.to_string()),
        );
        assert!(!res.ok);
        assert_eq!(res.reason, Some(GEN_NONZERO_EXIT), "detail: {}", res.detail);
        assert!(
            res.detail.contains("exited 3 without producing an index"),
            "{}",
            res.detail
        );
        assert!(logs.iter().any(|l| l.contains("boom")), "{logs:?}");

        // Success: the fake tool writes the output file, run is ok.
        std::fs::write(
            &fake,
            "#!/bin/sh\nfor a in \"$@\"; do case \"$a\" in --output=*) echo scip > \"${a#--output=}\";; esac; done\n",
        )
        .expect("fake script");
        let spec_python_shape = IndexerSpec {
            language: "fixture",
            tool: "scip-fake",
            build_command: |_repo, out| vec!["scip-fake".to_string(), format!("--output={out}")],
            env: no_env,
            install_hint: "install the fixture tool",
        };
        let mut logs: Vec<String> = Vec::new();
        let res = generate_with(
            &spec_python_shape,
            &out,
            &dir.to_string_lossy(),
            &bin.to_string_lossy(),
            &mut |l| logs.push(l.to_string()),
        );
        assert!(res.ok, "{}", res.detail);
        assert_eq!(res.reason, None);
        assert!(out.exists());

        let _ = std::fs::remove_dir_all(&dir);
    }

    /// generate_index_result with an injectable PATH for hermetic tests.
    #[cfg(unix)]
    fn generate_with(
        spec: &IndexerSpec,
        output_path: &Path,
        repo_path: &str,
        path_env: &str,
        log: &mut dyn FnMut(&str),
    ) -> GenerationResult {
        if output_path.exists() {
            return GenerationResult::success();
        }
        match run_tool(spec, output_path, repo_path, path_env, INDEX_TIMEOUT, log) {
            Ok(()) => GenerationResult::success(),
            Err(ToolFailure::MissingBinary) => failure(
                spec,
                GEN_MISSING_BINARY,
                &format!("index missing and '{}' not on PATH", spec.tool),
                log,
            ),
            Err(ToolFailure::NonZeroExit(code)) => failure(
                spec,
                GEN_NONZERO_EXIT,
                &format!("{0} exited {code} without producing an index", spec.tool),
                log,
            ),
            Err(ToolFailure::Timeout) => failure(
                spec,
                GEN_TIMEOUT,
                &format!("{} timed out after {}s", spec.tool, INDEX_TIMEOUT.as_secs()),
                log,
            ),
            Err(ToolFailure::Invocation(e)) => failure(
                spec,
                GEN_OS_ERROR,
                &format!("{} invocation failed ({e})", spec.tool),
                log,
            ),
        }
    }

    #[test]
    fn find_on_path_skips_non_executables_and_empty_entries() {
        let dir = std::env::temp_dir().join(format!("scip-which-{}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("dir");
        std::fs::write(dir.join("tool"), b"not executable").expect("plain file");
        #[cfg(unix)]
        {
            // Non-executable candidates are skipped, matching shutil.which on POSIX.
            assert_eq!(find_on_path(&dir.to_string_lossy(), "tool"), None);
        }
        #[cfg(windows)]
        {
            // No exec bit exists on Windows: a readable plain file is a candidate.
            assert_eq!(
                find_on_path(&dir.to_string_lossy(), "tool"),
                Some(dir.join("tool"))
            );
        }
        // An empty PATH entry resolves relative to the cwd, where no `tool`
        // exists; a separator-only PATH probes nothing but those entries.
        let sep = if cfg!(windows) { ";" } else { ":" };
        assert_eq!(find_on_path(sep, "tool"), None);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
