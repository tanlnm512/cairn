//! Release and packaging wiring contracts: maturin bin wheel, crates.io publish, wheel matrix.

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::process::Command;

use regex::Regex;
use yaml_rust2::{Yaml, YamlLoader};

use crate::check_doc_links::repo_root;

/// crates.io needs every path dep registered first, ending with the binstall crate.
const PUBLISH_ORDER: [&str; 6] = [
    "cairn-store",
    "cairn-parse",
    "cairn-query",
    "cairn-daemon",
    "cairn-build",
    "cairn-cli",
];

const WHEEL_TARGET_RUNNERS: [(&str, &str); 7] = [
    ("x86_64-unknown-linux-gnu", "ubuntu"),
    ("aarch64-unknown-linux-gnu", "ubuntu"),
    ("x86_64-unknown-linux-musl", "ubuntu"),
    ("aarch64-unknown-linux-musl", "ubuntu"),
    ("x86_64-apple-darwin", "macos"),
    ("aarch64-apple-darwin", "macos"),
    ("x86_64-pc-windows-msvc", "windows"),
];

/// Legs whose runner can install its own wheel.
const NATIVE_SMOKE_TARGETS: [&str; 4] = [
    "x86_64-unknown-linux-gnu",
    "x86_64-apple-darwin",
    "aarch64-apple-darwin",
    "x86_64-pc-windows-msvc",
];

fn read(rel: &str) -> String {
    fs::read_to_string(repo_root().join(rel)).unwrap_or_else(|e| panic!("{rel}: {e}"))
}

fn pyproject() -> String {
    read("pyproject.toml")
}

fn toml_section(text: &str, header: &str) -> String {
    let pattern = format!(r"(?ms)^\[{}\]\n(.*?)(?:\n\n|\z)", regex::escape(header));
    let caps = Regex::new(&pattern)
        .unwrap()
        .captures(text)
        .unwrap_or_else(|| panic!("[{header}] section missing"));
    caps[1].to_string()
}

fn has_line(text: &str, line: &str) -> bool {
    text.lines().any(|l| l == line)
}

fn wheels_doc() -> Yaml {
    YamlLoader::load_from_str(&read(".github/workflows/release.yml"))
        .expect("release.yml parses")
        .remove(0)
}

fn wheel_legs(doc: &Yaml) -> BTreeMap<String, Yaml> {
    let include = doc["jobs"]["wheels"]["strategy"]["matrix"]["include"]
        .as_vec()
        .expect("wheels matrix include");
    include
        .iter()
        .map(|leg| (leg["target"].as_str().unwrap().to_string(), leg.clone()))
        .collect()
}

fn maturin_step(job: &Yaml) -> Yaml {
    job["steps"]
        .as_vec()
        .expect("job steps")
        .iter()
        .find(|s| {
            s["uses"]
                .as_str()
                .is_some_and(|u| u.starts_with("PyO3/maturin-action@"))
        })
        .cloned()
        .expect("maturin-action step not found")
}

#[test]
fn the_wheel_is_the_cli_binary_built_by_maturin_with_no_runtime_dependencies() {
    let text = pyproject();
    assert!(has_line(&text, r#"build-backend = "maturin""#));
    let maturin = toml_section(&text, "tool.maturin");
    assert!(has_line(&maturin, r#"bindings = "bin""#));
    assert!(has_line(
        &maturin,
        r#"manifest-path = "rust/crates/cairn-cli/Cargo.toml""#
    ));
    assert!(
        has_line(&text, "dependencies = []"),
        "the bin wheel must stay dependency-free"
    );
}

#[test]
fn no_python_package_or_setuptools_glue_remains() {
    let text = pyproject();
    assert!(!text.contains("[project.scripts]"));
    assert!(!text.contains("[tool.setuptools"));
    for gone in ["setup.py", "MANIFEST.in"] {
        assert!(!repo_root().join(gone).exists(), "{gone}");
    }
    let tracked = Command::new("git")
        .args(["ls-files", "--", "src"])
        .current_dir(repo_root())
        .output()
        .expect("git ls-files");
    assert!(tracked.status.success());
    assert!(
        tracked.stdout.is_empty(),
        "nothing may be tracked under src/"
    );
}

#[test]
fn only_tooling_extras_remain() {
    let text = pyproject();
    let section = toml_section(&text, "project.optional-dependencies");
    let extras: BTreeSet<String> = Regex::new(r"(?m)^(\w[\w.-]*) = \[")
        .unwrap()
        .captures_iter(&section)
        .map(|c| c[1].to_string())
        .collect();
    assert!(
        extras.iter().all(|e| e == "test" || e == "dev"),
        "{extras:?}"
    );
    assert!(!text.contains("graph-analytics") && !text.contains("networkx"));
}

#[test]
fn crates_publish_in_dependency_order_and_skip_without_a_token() {
    let text = read(".github/workflows/release-rust.yml");
    let order = Regex::new(r"(?m)^ *for crate in (.+); do$")
        .unwrap()
        .captures(&text)
        .expect("crates.io publish loop not found in release-rust.yml");
    assert_eq!(
        order[1].split_whitespace().collect::<Vec<_>>(),
        PUBLISH_ORDER
    );
    let gate = Regex::new(r#"(?s)\[ -z "\$CARGO_REGISTRY_TOKEN" \][^\n]*; then\n(.*?)\n\s*fi"#)
        .unwrap()
        .captures(&text)
        .expect("CARGO_REGISTRY_TOKEN gate not found in release-rust.yml");
    assert!(gate[1].contains("::notice::") && gate[1].contains("exit 0"));
}

#[test]
fn release_asset_naming_is_consistent_across_channels() {
    assert!(
        read(".github/workflows/release-rust.yml")
            .contains(r#""cairn-cli-${version}-${{ matrix.target }}.tar.gz""#)
    );
    assert!(read("rust/crates/cairn-cli/Cargo.toml").contains(
        r#"pkg-url = "{ repo }/releases/download/v{ version }/cairn-cli-{ version }-{ target }.tar.gz""#
    ));
    let formula = read("packaging/homebrew/cairn.rb.template");
    let targets: BTreeSet<&str> = Regex::new(r#"/cairn-cli-@VERSION@-([^"/]+)\.tar\.gz"#)
        .unwrap()
        .captures_iter(&formula)
        .map(|c| c.get(1).unwrap().as_str())
        .collect();
    assert_eq!(
        targets,
        BTreeSet::from([
            "aarch64-apple-darwin",
            "x86_64-apple-darwin",
            "aarch64-unknown-linux-musl",
            "x86_64-unknown-linux-musl",
        ])
    );
}

#[test]
fn every_target_builds_a_blocking_wheel_on_its_runner_family() {
    let doc = wheels_doc();
    let legs = wheel_legs(&doc);
    let runners: BTreeMap<&str, &str> = legs
        .iter()
        .map(|(target, leg)| {
            let os = leg["os"].as_str().unwrap();
            (target.as_str(), os.split('-').next().unwrap())
        })
        .collect();
    assert_eq!(runners, BTreeMap::from(WHEEL_TARGET_RUNNERS));
    for (target, leg) in &legs {
        assert!(leg["experimental"].is_badvalue(), "{target}");
        if target.contains("linux") {
            assert_eq!(leg["zig"].as_str(), Some("--zig"), "{target}");
            let tag = if target.ends_with("musl") {
                "musllinux_1_2"
            } else {
                "auto"
            };
            assert_eq!(leg["manylinux"].as_str(), Some(tag), "{target}");
        }
    }
    assert!(doc["jobs"]["wheels"]["continue-on-error"].is_badvalue());
    let smoked: BTreeSet<&str> = legs
        .iter()
        .filter(|(_, leg)| leg["smoke"].as_bool() == Some(true))
        .map(|(target, _)| target.as_str())
        .collect();
    assert_eq!(smoked, BTreeSet::from(NATIVE_SMOKE_TARGETS));
    let smoke_steps: Vec<&Yaml> = doc["jobs"]["wheels"]["steps"]
        .as_vec()
        .unwrap()
        .iter()
        .filter(|s| {
            s["run"]
                .as_str()
                .is_some_and(|r| r.contains("wheel_smoke.sh"))
        })
        .collect();
    assert_eq!(smoke_steps.len(), 1);
    let smoke = smoke_steps[0];
    assert_eq!(smoke["if"].as_str(), Some("matrix.smoke"));
    assert!(smoke["run"].as_str().unwrap().contains("dist/*.whl"));
    // The windows runner's default pwsh would not glob dist/*.whl.
    assert_eq!(smoke["shell"].as_str(), Some("bash"));
}

#[test]
fn maturin_builds_every_artifact_and_publish_gates_on_all_of_them() {
    let doc = wheels_doc();
    let wheel = maturin_step(&doc["jobs"]["wheels"]);
    assert_eq!(
        wheel["with"]["target"].as_str(),
        Some("${{ matrix.target }}")
    );
    assert_eq!(
        wheel["with"]["manylinux"].as_str(),
        Some("${{ matrix.manylinux }}")
    );
    let args = wheel["with"]["args"].as_str().unwrap();
    assert!(args.contains("--release") && args.contains("--out dist"));
    let text = read(".github/workflows/release.yml");
    for gone in ["cibuildwheel", "src/cairn/_bin", "setup.py"] {
        assert!(!text.contains(gone), "{gone}");
    }
    let sdist = maturin_step(&doc["jobs"]["sdist"]);
    assert_eq!(sdist["with"]["command"].as_str(), Some("sdist"));
    let publish = &doc["jobs"]["publish-pypi"];
    let needs: BTreeSet<&str> = publish["needs"]
        .as_vec()
        .unwrap()
        .iter()
        .filter_map(Yaml::as_str)
        .collect();
    assert_eq!(needs, BTreeSet::from(["wheels", "sdist"]));
    let permissions = publish["permissions"].as_hash().unwrap();
    assert_eq!(permissions.len(), 1);
    assert_eq!(publish["permissions"]["id-token"].as_str(), Some("write"));
    assert!(publish["steps"].as_vec().unwrap().iter().any(|s| {
        s["uses"]
            .as_str()
            .is_some_and(|u| u.starts_with("pypa/gh-action-pypi-publish@"))
    }));
}
