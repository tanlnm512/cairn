//! The developer-workflow commands (`prs`, `bench`, `eval`) stay on the PyPI
//! CLI: the binary exits 1 naming it, whatever arguments follow.

use std::process::Command;

fn run(args: &[&str]) -> (Option<i32>, String) {
    let out = Command::new(env!("CARGO_BIN_EXE_cairn"))
        .args(args)
        .output()
        .expect("spawn cairn");
    let text = [out.stdout, out.stderr].concat();
    // The console fold breaks lines at 80 columns; rejoin before matching.
    let text = String::from_utf8_lossy(&text).replace('\n', "");
    (out.status.code(), text)
}

#[test]
fn pypi_only_commands_exit_one_naming_the_pypi_cli() {
    let cases: [&[&str]; 6] = [
        &["prs"],
        &["prs", "--conflicts", "--impact", "42", "--json"],
        &["bench"],
        &["bench", "--suite", "perf", "--repeats", "1", "--json"],
        &["eval"],
        &["eval", "--corpus", "L1", "--json"],
    ];
    for args in cases {
        let (code, out) = run(args);
        assert_eq!(code, Some(1), "{args:?}: {out}");
        let name = args[0];
        assert!(
            out.contains(&format!("cairn {name} is a developer-workflow command")),
            "{args:?}: {out}"
        );
        assert!(out.contains("pip install cairn-intel"), "{args:?}: {out}");
    }
}
