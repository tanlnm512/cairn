//! Release and packaging wiring contracts: maturin bin wheel, wheel matrix, and release tarballs.

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::process::Command;

use regex::Regex;
use yaml_rust2::{Yaml, YamlLoader};

use crate::check_doc_links::repo_root;

const WHEEL_TARGET_RUNNERS: [(&str, &str); 7] = [
    ("x86_64-unknown-linux-gnu", "ubuntu"),
    ("aarch64-unknown-linux-gnu", "ubuntu"),
    ("x86_64-unknown-linux-musl", "ubuntu"),
    ("aarch64-unknown-linux-musl", "ubuntu"),
    ("x86_64-apple-darwin", "macos"),
    ("aarch64-apple-darwin", "macos"),
    ("x86_64-pc-windows-msvc", "windows"),
];

const NATIVE_SMOKE_TARGETS: [&str; 3] = [
    "x86_64-unknown-linux-gnu",
    "aarch64-apple-darwin",
    "x86_64-pc-windows-msvc",
];

fn read(rel: &str) -> String {
    fs::read_to_string(repo_root().join(rel)).unwrap_or_else(|e| panic!("{rel}: {e}"))
}

fn toml_section(text: &str, header: &str) -> String {
    let pattern = format!(r"(?ms)^\[{}\]\n(.*?)(?:\n\n|\z)", regex::escape(header));
    let caps = Regex::new(&pattern)
        .unwrap()
        .captures(text)
        .unwrap_or_else(|| panic!("[{header}] section missing"));
    caps[1].to_string()
}

fn section_version(text: &str, header: &str) -> String {
    let section = toml_section(text, header);
    Regex::new(r#"(?m)^version = "([^"]+)""#)
        .unwrap()
        .captures(&section)
        .unwrap_or_else(|| panic!("[{header}] version missing"))[1]
        .to_string()
}

fn has_line(text: &str, line: &str) -> bool {
    text.lines().any(|l| l == line)
}

fn release_doc() -> Yaml {
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
    let text = read("pyproject.toml");
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
    assert_eq!(
        section_version(&text, "project"),
        section_version(&read("Cargo.toml"), "workspace.package"),
        "pyproject.toml and Cargo.toml workspace versions must stay aligned"
    );
}

#[test]
fn no_python_package_or_setuptools_glue_remains() {
    let text = read("pyproject.toml");
    assert!(!text.contains("[project.scripts]"));
    assert!(!text.contains("[tool.setuptools"));
    for gone in ["setup.py", "setup.cfg", "MANIFEST.in"] {
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
fn release_asset_naming_matches_binstall_and_installer() {
    assert!(
        read(".github/workflows/release-rust.yml")
            .contains(r#""cairn-cli-${version}-${{ matrix.target }}.tar.gz""#)
    );
    assert!(read("rust/crates/cairn-cli/Cargo.toml").contains(
        r#"pkg-url = "{ repo }/releases/download/v{ version }/cairn-cli-{ version }-{ target }.tar.gz""#
    ));
    let installer = read("scripts/install.sh");
    assert!(installer.contains(r#"ARCHIVE="cairn-cli-${VERSION}-${TARGET}.tar.gz""#));
    for target in [
        "aarch64-apple-darwin",
        "x86_64-apple-darwin",
        "aarch64-unknown-linux-musl",
        "x86_64-unknown-linux-musl",
        "x86_64-pc-windows-msvc",
    ] {
        assert!(installer.contains(target), "installer missing {target}");
    }
}

#[test]
fn every_target_builds_a_blocking_wheel_on_its_runner_family() {
    let doc = release_doc();
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
                .is_some_and(|r| r.contains("wheel-smoke.sh"))
        })
        .collect();
    assert_eq!(smoke_steps.len(), 1);
    let smoke = smoke_steps[0];
    assert_eq!(smoke["if"].as_str(), Some("matrix.smoke"));
    assert!(smoke["run"].as_str().unwrap().contains("dist/*.whl"));
    assert_eq!(smoke["shell"].as_str(), Some("bash"));
}

#[test]
fn maturin_builds_every_artifact_and_publish_gates_on_all_of_them() {
    let doc = release_doc();
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
