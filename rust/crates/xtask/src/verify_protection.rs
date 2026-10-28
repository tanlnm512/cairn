//! `verify-protection`: read-only check of GitHub main protection across legacy and ruleset APIs.

use std::collections::BTreeSet;
use std::io::Read;
use std::process::{Command, ExitCode, Stdio};
use std::sync::LazyLock;
use std::time::{Duration, Instant};

use clap::Parser;
use regex::Regex;
use serde_json::{Value, json};

use crate::audit_status::py_json_pretty;

const MANDATORY_CHECKS: [&str; 10] = [
    "PR title (conventional commits)",
    "pre-commit (all local gates)",
    "Quality ratchet (audit debt + comment style)",
    "Dependency review (PR)",
    "Container build (Dockerfile smoke)",
    "fmt",
    "clippy (-D warnings)",
    "test",
    "windows (clippy + test + release build)",
    "cargo-deny (advisories + licenses)",
];
const GH_TIMEOUT: Duration = Duration::from_secs(30);

static REPO_SLUG: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$").unwrap());
static HTTP_STATUS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"HTTP (\d{3})").unwrap());

pub(crate) enum GhRun {
    Done {
        code: i32,
        stdout: String,
        stderr: String,
    },
    TimedOut,
    SpawnFailed(String),
}

type Runner<'a> = &'a dyn Fn(&str) -> GhRun;

#[derive(Clone, Copy, PartialEq)]
enum Kind {
    Array,
    Object,
}

enum ApiResult {
    Payload(Value),
    Missing,
    Error(Value),
}

fn gh_command(endpoint: &str) -> Command {
    let mut cmd = Command::new("gh");
    cmd.args([
        "api",
        endpoint,
        "--method",
        "GET",
        "--header",
        "Accept: application/vnd.github+json",
    ]);
    cmd
}

fn gh_run(endpoint: &str) -> GhRun {
    let mut child = match gh_command(endpoint)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
    {
        Ok(child) => child,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            return GhRun::SpawnFailed("[Errno 2] No such file or directory: 'gh'".into());
        }
        Err(e) => return GhRun::SpawnFailed(e.to_string()),
    };
    let drain = |mut pipe: Box<dyn Read + Send>| {
        std::thread::spawn(move || {
            let mut buf = Vec::new();
            let _ = pipe.read_to_end(&mut buf);
            String::from_utf8_lossy(&buf).into_owned()
        })
    };
    let out = drain(Box::new(child.stdout.take().unwrap()));
    let err = drain(Box::new(child.stderr.take().unwrap()));
    let deadline = Instant::now() + GH_TIMEOUT;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() >= deadline => {
                let _ = child.kill();
                let _ = child.wait();
                return GhRun::TimedOut;
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(20)),
            Err(e) => return GhRun::SpawnFailed(e.to_string()),
        }
    };
    GhRun::Done {
        code: status.code().unwrap_or(-1),
        stdout: out.join().unwrap_or_default(),
        stderr: err.join().unwrap_or_default(),
    }
}

fn api_error(source: &str, endpoint: &str, message: String) -> ApiResult {
    ApiResult::Error(json!({"source": source, "endpoint": endpoint, "message": message}))
}

fn json_type_name(value: &Value) -> &'static str {
    match value {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(n) if n.is_f64() => "float",
        Value::Number(_) => "int",
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

fn gh_json(run: Runner, endpoint: &str, source: &str, expected: Kind) -> ApiResult {
    let (code, stdout, stderr) = match run(endpoint) {
        GhRun::TimedOut => {
            return api_error(
                source,
                endpoint,
                format!("gh api timed out after {}s: gh", GH_TIMEOUT.as_secs()),
            );
        }
        GhRun::SpawnFailed(e) => {
            return api_error(source, endpoint, format!("could not execute gh: {e}"));
        }
        GhRun::Done {
            code,
            stdout,
            stderr,
        } => (code, stdout, stderr),
    };
    if code != 0 {
        let stderr = stderr.trim();
        let message = if stderr.is_empty() {
            format!("gh exited with status {code}")
        } else {
            stderr.to_string()
        };
        if HTTP_STATUS
            .captures(&message)
            .is_some_and(|c| &c[1] == "404")
        {
            return ApiResult::Missing;
        }
        return api_error(source, endpoint, message);
    }
    let payload: Value = match serde_json::from_str(&stdout) {
        Ok(v) => v,
        Err(e) => return api_error(source, endpoint, format!("gh returned invalid JSON: {e}")),
    };
    let matches = match expected {
        Kind::Array => payload.is_array(),
        Kind::Object => payload.is_object(),
    };
    if !matches {
        let want = if expected == Kind::Array {
            "list"
        } else {
            "dict"
        };
        return api_error(
            source,
            endpoint,
            format!("gh returned {}, expected {want}", json_type_name(&payload)),
        );
    }
    ApiResult::Payload(payload)
}

fn fetch_rulesets(run: Runner, repo: &str) -> (Vec<Value>, Vec<Value>) {
    let mut summaries = Vec::new();
    let mut page = 1;
    loop {
        let endpoint = format!("repos/{repo}/rulesets?per_page=100&page={page}");
        match gh_json(run, &endpoint, "rulesets", Kind::Array) {
            ApiResult::Error(e) => return (Vec::new(), vec![e]),
            ApiResult::Missing => return (Vec::new(), Vec::new()),
            ApiResult::Payload(Value::Array(items)) => {
                let last = items.len() < 100;
                summaries.extend(items.into_iter().filter(Value::is_object));
                if last {
                    break;
                }
            }
            ApiResult::Payload(_) => break,
        }
        page += 1;
    }
    let mut detailed = Vec::new();
    let mut errors = Vec::new();
    for summary in &summaries {
        let Some(id) = summary.get("id").and_then(Value::as_i64) else {
            continue;
        };
        let endpoint = format!("repos/{repo}/rulesets/{id}");
        match gh_json(run, &endpoint, &format!("rulesets:{id}"), Kind::Object) {
            ApiResult::Error(e) => errors.push(e),
            ApiResult::Payload(p) => detailed.push(p),
            ApiResult::Missing => {}
        }
    }
    (detailed, errors)
}

fn fnmatch_regex(pattern: &str) -> Regex {
    let chars: Vec<char> = pattern.chars().collect();
    let mut re = String::from("(?s)^");
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        i += 1;
        match c {
            '*' => re.push_str(".*"),
            '?' => re.push('.'),
            '[' => {
                let mut j = i;
                if j < chars.len() && chars[j] == '!' {
                    j += 1;
                }
                if j < chars.len() && chars[j] == ']' {
                    j += 1;
                }
                while j < chars.len() && chars[j] != ']' {
                    j += 1;
                }
                if j >= chars.len() {
                    re.push_str("\\[");
                } else {
                    let mut body: String = chars[i..j].iter().collect();
                    body = body.replace('\\', "\\\\");
                    if let Some(rest) = body.strip_prefix('!') {
                        body = format!("^{rest}");
                    }
                    re.push('[');
                    re.push_str(&body);
                    re.push(']');
                    i = j + 1;
                }
            }
            c => re.push_str(&regex::escape(&c.to_string())),
        }
    }
    re.push('$');
    Regex::new(&re).unwrap_or_else(|_| Regex::new("$^").unwrap())
}

fn ref_matches(pattern: &Value, branch: &str) -> bool {
    let Some(pattern) = pattern.as_str() else {
        return false;
    };
    if pattern == "~DEFAULT_BRANCH" {
        return branch == "main";
    }
    let candidate = pattern.strip_prefix("refs/heads/").unwrap_or(pattern);
    fnmatch_regex(candidate).is_match(branch)
}

fn ruleset_targets_branch(ruleset: &Value, branch: &str) -> bool {
    if ruleset.get("target").and_then(Value::as_str) != Some("branch") {
        return false;
    }
    let Some(refs) = ruleset
        .get("conditions")
        .and_then(|c| c.get("ref_name"))
        .filter(|r| r.is_object())
    else {
        return true;
    };
    let included = refs.get("include").and_then(Value::as_array);
    if let Some(included) = included
        && !included.is_empty()
        && !included.iter().any(|p| ref_matches(p, branch))
    {
        return false;
    }
    let excluded = refs.get("exclude").and_then(Value::as_array);
    !excluded.is_some_and(|e| e.iter().any(|p| ref_matches(p, branch)))
}

fn int_of(value: Option<&Value>) -> Option<i64> {
    value.and_then(|v| if v.is_boolean() { None } else { v.as_i64() })
}

fn legacy_review_count(payload: &Value) -> Option<i64> {
    int_of(
        payload
            .get("required_pull_request_reviews")?
            .as_object()?
            .get("required_approving_review_count"),
    )
}

fn legacy_force_denied(payload: &Value) -> Option<bool> {
    let enabled = payload
        .get("allow_force_pushes")?
        .as_object()?
        .get("enabled")?
        .as_bool()?;
    Some(!enabled)
}

fn context_names(items: Option<&Value>, names: &mut BTreeSet<String>, direct: bool) {
    for item in items.and_then(Value::as_array).into_iter().flatten() {
        let name = if direct {
            item.as_str()
        } else {
            item.get("context").and_then(Value::as_str)
        };
        names.extend(name.map(str::to_owned));
    }
}

fn legacy_status_checks(payload: &Value) -> Option<Vec<String>> {
    let required = payload.get("required_status_checks")?.as_object()?;
    let mut names = BTreeSet::new();
    context_names(required.get("contexts"), &mut names, true);
    context_names(required.get("checks"), &mut names, false);
    Some(names.into_iter().collect())
}

fn rules_of_type<'a>(ruleset: &'a Value, rule_type: &str) -> Vec<&'a Value> {
    ruleset
        .get("rules")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|r| r.is_object() && r.get("type").and_then(Value::as_str) == Some(rule_type))
        .collect()
}

fn ruleset_review_count(ruleset: &Value) -> Option<i64> {
    ["pull_request", "required_reviews"]
        .iter()
        .flat_map(|t| rules_of_type(ruleset, t))
        .filter_map(|rule| {
            int_of(
                rule.get("parameters")
                    .filter(|p| p.is_object())?
                    .get("required_approving_review_count"),
            )
        })
        .max()
}

fn ruleset_status_checks(ruleset: &Value) -> Option<Vec<String>> {
    let mut names = BTreeSet::new();
    let mut found = false;
    for rule in rules_of_type(ruleset, "required_status_checks") {
        let Some(required) = rule
            .get("parameters")
            .filter(|p| p.is_object())
            .and_then(|p| p.get("required_status_checks"))
            .filter(|r| r.is_array())
        else {
            continue;
        };
        found = true;
        context_names(Some(required), &mut names, false);
    }
    found.then(|| names.into_iter().collect())
}

fn ruleset_source(ruleset: &Value) -> String {
    format!(
        "ruleset:{}",
        ruleset.get("id").map_or_else(|| "None".to_string(), py_str)
    )
}

fn py_str(value: &Value) -> String {
    match value {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

fn verify(run: Runner, repo: &str, mandatory: &[&str]) -> Result<Value, String> {
    if !REPO_SLUG.is_match(repo) {
        return Err(format!(
            "invalid repository slug: {}",
            crate::audit_status::py_repr_str(repo)
        ));
    }
    let mut source_errors = Vec::new();
    let legacy = match gh_json(
        run,
        &format!("repos/{repo}/branches/main/protection"),
        "legacy",
        Kind::Object,
    ) {
        ApiResult::Payload(p) => p,
        ApiResult::Missing => json!({}),
        ApiResult::Error(e) => {
            source_errors.push(e);
            json!({})
        }
    };
    let (rulesets, ruleset_errors) = fetch_rulesets(run, repo);
    source_errors.extend(ruleset_errors);
    let active: Vec<&Value> = rulesets
        .iter()
        .filter(|r| {
            r.get("enforcement").and_then(Value::as_str) == Some("active")
                && ruleset_targets_branch(r, "main")
        })
        .collect();
    let name_of = |r: &Value| r.get("name").cloned().unwrap_or(Value::Null);

    let mut review_sources = Vec::new();
    let mut review_counts = Vec::new();
    if let Some(count) = legacy_review_count(&legacy) {
        review_counts.push(count);
        review_sources.push(json!({"source": "legacy", "required_reviews": count}));
    }
    for r in &active {
        if let Some(count) = ruleset_review_count(r) {
            review_counts.push(count);
            review_sources.push(
                json!({"source": ruleset_source(r), "name": name_of(r), "required_reviews": count}),
            );
        }
    }
    let observed_reviews = review_counts.into_iter().max().unwrap_or(0);

    let mut force_sources = Vec::new();
    if legacy_force_denied(&legacy) == Some(true) {
        force_sources.push(json!({"source": "legacy", "force_pushes_allowed": false}));
    }
    for r in &active {
        if !rules_of_type(r, "non_fast_forward").is_empty() {
            force_sources.push(json!({"source": ruleset_source(r), "name": name_of(r), "rule": "non_fast_forward"}));
        }
    }

    let mut status_sources = Vec::new();
    let mut observed: BTreeSet<String> = BTreeSet::new();
    if let Some(checks) = legacy_status_checks(&legacy) {
        observed.extend(checks.iter().cloned());
        status_sources.push(json!({"source": "legacy", "status_checks": checks}));
    }
    for r in &active {
        if let Some(checks) = ruleset_status_checks(r) {
            observed.extend(checks.iter().cloned());
            status_sources.push(
                json!({"source": ruleset_source(r), "name": name_of(r), "status_checks": checks}),
            );
        }
    }
    let missing: Vec<&str> = mandatory
        .iter()
        .copied()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .filter(|m| !observed.contains(*m))
        .collect();
    // A required check no workflow emits blocks every merge.
    let unexpected: Vec<&str> = observed
        .iter()
        .map(String::as_str)
        .filter(|o| !mandatory.contains(o))
        .collect();
    let checks_ok = missing.is_empty() && unexpected.is_empty();

    let errors = Value::Array(source_errors.clone());
    let status = |ok: bool| if ok { "pass" } else { "fail" };
    let checks = vec![
        json!({"name": "required_reviews", "status": status(observed_reviews >= 1),
               "observed": observed_reviews, "provenance": review_sources, "errors": errors}),
        json!({"name": "force_push_denial", "status": status(!force_sources.is_empty()),
               "observed": !force_sources.is_empty(), "provenance": force_sources, "errors": errors}),
        json!({"name": "required_status_checks", "status": status(checks_ok),
               "observed": observed, "missing": missing, "unexpected": unexpected,
               "provenance": status_sources, "errors": errors}),
    ];
    let ok = checks.iter().all(|c| c["status"] == "pass") && source_errors.is_empty();
    Ok(json!({
        "schema": "cairn-protection-verification/1",
        "repo": repo,
        "branch": "main",
        "ok": ok,
        "checks": checks,
        "source_errors": source_errors,
    }))
}

fn source_names(check: &Value) -> String {
    let names: Vec<String> = check["provenance"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|p| py_str(&p["source"]))
        .collect();
    if names.is_empty() {
        "no source".into()
    } else {
        names.join(", ")
    }
}

/// Human report split by stream: (stdout lines, stderr lines).
fn human_report(report: &Value) -> (Vec<String>, Vec<String>) {
    let mut out = Vec::new();
    let mut err = Vec::new();
    for e in report["source_errors"].as_array().into_iter().flatten() {
        err.push(format!(
            "ERROR {} {}: {}",
            py_str(&e["source"]),
            py_str(&e["endpoint"]),
            py_str(&e["message"])
        ));
    }
    for check in report["checks"].as_array().into_iter().flatten() {
        let name = py_str(&check["name"]);
        if check["status"] == "pass" {
            out.push(format!("PASS {name} ({})", source_names(check)));
            continue;
        }
        let details = if name == "required_status_checks" {
            let names = |key: &str| -> String {
                let list: Vec<String> = check[key]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .map(py_str)
                    .collect();
                list.join(", ")
            };
            format!(
                "missing: {}; unexpected: {}",
                names("missing"),
                names("unexpected")
            )
        } else {
            format!("observed: {}", py_str(&check["observed"]))
        };
        err.push(format!("FAIL {name} ({details}; {})", source_names(check)));
    }
    if report["ok"] == true {
        out.push("protection verified".to_string());
    } else {
        err.push("protection not verified".to_string());
    }
    (out, err)
}

fn print_human(report: &Value) {
    let (out, err) = human_report(report);
    for line in out {
        println!("{line}");
    }
    for line in err {
        eprintln!("{line}");
    }
}

#[derive(Parser)]
#[command(
    name = "verify-protection",
    about = "Verify GitHub main protection across the legacy and rulesets APIs."
)]
struct Args {
    #[arg(long, required = true, help = "repository slug, owner/name")]
    repo: String,
    #[arg(long, help = "emit a machine-readable report")]
    json: bool,
}

pub fn run(args: &[String]) -> ExitCode {
    let parsed = match Args::try_parse_from(
        std::iter::once("verify-protection".to_string()).chain(args.iter().cloned()),
    ) {
        Ok(parsed) => parsed,
        Err(e) => {
            let _ = e.print();
            return ExitCode::from(e.exit_code() as u8);
        }
    };
    let report = match verify(&gh_run, &parsed.repo, &MANDATORY_CHECKS) {
        Ok(report) => report,
        Err(e) => {
            eprintln!("error: {e}");
            return ExitCode::FAILURE;
        }
    };
    if parsed.json {
        println!("{}", py_json_pretty(&report, true));
    } else {
        print_human(&report);
    }
    if report["ok"] == true {
        ExitCode::SUCCESS
    } else {
        ExitCode::FAILURE
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn legacy(reviews: bool, force_push: bool, checks: &[&str]) -> Value {
        let mut payload = json!({
            "required_status_checks": {"checks": checks.iter().map(|c| json!({"context": c})).collect::<Vec<_>>(), "contexts": []},
            "allow_force_pushes": {"enabled": force_push},
        });
        if reviews {
            payload["required_pull_request_reviews"] =
                json!({"required_approving_review_count": 1});
        }
        payload
    }

    fn ruleset(
        id: i64,
        branch: &str,
        enforcement: &str,
        reviews: bool,
        nff: bool,
        checks: &[&str],
    ) -> Value {
        let mut rules = Vec::new();
        if reviews {
            rules.push(json!({"type": "pull_request", "parameters": {"required_approving_review_count": 1}}));
        }
        if nff {
            rules.push(json!({"type": "non_fast_forward"}));
        }
        if !checks.is_empty() {
            rules.push(json!({"type": "required_status_checks", "parameters": {
                "required_status_checks": checks.iter().map(|c| json!({"context": c})).collect::<Vec<_>>()}}));
        }
        json!({"id": id, "name": "main", "target": "branch", "enforcement": enforcement,
               "conditions": {"ref_name": {"include": [branch], "exclude": []}}, "rules": rules})
    }

    fn done(code: i32, stdout: String, stderr: &str) -> GhRun {
        GhRun::Done {
            code,
            stdout,
            stderr: stderr.into(),
        }
    }

    fn fake<'a>(
        legacy: Option<Value>,
        rulesets: &'a [Value],
        legacy_err: Option<(i32, &'a str)>,
    ) -> impl Fn(&str) -> GhRun + 'a {
        move |endpoint| {
            if endpoint == "repos/example/repo/branches/main/protection" {
                return match legacy_err {
                    Some((code, msg)) => done(code, String::new(), msg),
                    None => done(0, legacy.clone().unwrap().to_string(), ""),
                };
            }
            if endpoint == "repos/example/repo/rulesets?per_page=100&page=1" {
                let summaries: Vec<Value> = rulesets
                    .iter()
                    .map(|r| {
                        let mut s = r.clone();
                        s.as_object_mut().unwrap().remove("rules");
                        s
                    })
                    .collect();
                return done(0, Value::Array(summaries).to_string(), "");
            }
            let id: i64 = endpoint.rsplit('/').next().unwrap().parse().unwrap();
            let found = rulesets.iter().find(|r| r["id"] == id).unwrap();
            done(0, found.to_string(), "")
        }
    }

    fn failed(report: &Value) -> Vec<String> {
        report["checks"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|c| c["status"] == "fail")
            .map(|c| c["name"].as_str().unwrap().to_string())
            .collect()
    }

    #[test]
    fn evidence_is_unioned_across_sources_with_provenance() {
        let rulesets = [ruleset(
            7,
            "refs/heads/main",
            "active",
            true,
            false,
            &["ruleset-check"],
        )];
        let run = fake(
            Some(legacy(false, false, &["legacy-check", "other-check"])),
            &rulesets,
            None,
        );
        let report = verify(
            &run,
            "example/repo",
            &["legacy-check", "other-check", "ruleset-check"],
        )
        .unwrap();
        assert_eq!(report["ok"], true);
        let checks = report["checks"].as_array().unwrap();
        assert_eq!(checks[0]["provenance"][0]["source"], "ruleset:7");
        assert_eq!(checks[1]["provenance"][0]["source"], "legacy");
        assert_eq!(
            checks[2]["observed"],
            json!(["legacy-check", "other-check", "ruleset-check"])
        );
    }

    #[test]
    fn each_drift_fails_only_its_own_check() {
        for (case, expected) in [
            ("reviews", "required_reviews"),
            ("force", "force_push_denial"),
            ("checks", "required_status_checks"),
            ("unexpected", "required_status_checks"),
        ] {
            let reviews = case != "reviews";
            let force = case == "force";
            let checks: &[&str] = match case {
                "checks" => &[],
                "unexpected" => &["required-check", "retired-check"],
                _ => &["required-check"],
            };
            let rulesets = [ruleset(
                7,
                "refs/heads/main",
                "active",
                reviews,
                !force,
                checks,
            )];
            let run = fake(Some(legacy(reviews, force, checks)), &rulesets, None);
            let report = verify(&run, "example/repo", &["required-check"]).unwrap();
            assert_eq!(report["ok"], false);
            assert_eq!(failed(&report), vec![expected], "case {case}");
        }
    }

    #[test]
    fn mandatory_checks_pin_the_gated_check_names() {
        assert_eq!(
            BTreeSet::from(MANDATORY_CHECKS),
            BTreeSet::from([
                "PR title (conventional commits)",
                "pre-commit (all local gates)",
                "Quality ratchet (audit debt + comment style)",
                "Dependency review (PR)",
                "Container build (Dockerfile smoke)",
                "fmt",
                "clippy (-D warnings)",
                "test",
                "windows (clippy + test + release build)",
                "cargo-deny (advisories + licenses)",
            ])
        );
    }

    #[test]
    fn human_report_renders_failures_and_the_verdict() {
        let rulesets = [ruleset(7, "refs/heads/main", "active", true, true, &[])];
        let run = fake(Some(legacy(true, true, &[])), &rulesets, None);
        let report = verify(&run, "example/repo", &["required-check"]).unwrap();
        let (out, err) = human_report(&report);
        assert!(out.iter().any(|line| line.starts_with("PASS ")), "{out:?}");
        let fail = err
            .iter()
            .find(|line| line.starts_with("FAIL required_status_checks"))
            .expect("FAIL line");
        assert!(fail.contains("missing: required-check"), "{fail}");
        assert!(err.contains(&"protection not verified".to_string()));

        let rulesets = [ruleset(
            7,
            "refs/heads/main",
            "active",
            true,
            false,
            &["required-check"],
        )];
        let run = fake(
            Some(legacy(false, false, &["required-check"])),
            &rulesets,
            None,
        );
        let report = verify(&run, "example/repo", &["required-check"]).unwrap();
        let (out, err) = human_report(&report);
        assert!(out.contains(&"protection verified".to_string()));
        assert!(err.is_empty(), "{err:?}");
    }

    #[test]
    fn only_active_rulesets_targeting_main_count() {
        let rulesets = [
            ruleset(1, "refs/heads/release/*", "active", false, true, &[]),
            ruleset(2, "refs/heads/main", "evaluate", true, false, &[]),
            ruleset(3, "~DEFAULT_BRANCH", "active", false, true, &[]),
        ];
        let run = fake(Some(legacy(false, true, &[])), &rulesets, None);
        let report = verify(&run, "example/repo", &[]).unwrap();
        assert_eq!(report["checks"][0]["status"], "fail");
        assert_eq!(report["checks"][0]["provenance"], json!([]));
        assert_eq!(report["checks"][1]["provenance"][0]["source"], "ruleset:3");
    }

    #[test]
    fn legacy_auth_failure_is_surfaced_but_404_is_not() {
        let rulesets = [ruleset(
            7,
            "refs/heads/main",
            "active",
            true,
            true,
            &MANDATORY_CHECKS,
        )];
        let forbidden = fake(
            None,
            &rulesets,
            Some((
                1,
                "gh: Forbidden (HTTP 403): administration permission required",
            )),
        );
        let report = verify(&forbidden, "example/repo", &MANDATORY_CHECKS).unwrap();
        assert_eq!(report["ok"], false);
        assert!(failed(&report).is_empty());
        assert_eq!(report["source_errors"][0]["source"], "legacy");
        assert!(
            report["checks"][0]["errors"][0]["message"]
                .as_str()
                .unwrap()
                .contains("HTTP 403")
        );

        let missing = fake(None, &rulesets, Some((1, "gh: Not Found (HTTP 404)")));
        let report = verify(&missing, "example/repo", &MANDATORY_CHECKS).unwrap();
        assert_eq!(report["ok"], true);
        assert_eq!(report["source_errors"], json!([]));
    }

    #[test]
    fn invalid_slug_is_rejected_and_fnmatch_globs_work() {
        let never = |_: &str| -> GhRun { unreachable!() };
        assert!(verify(&never, "not a slug", &[]).is_err());
        assert!(ref_matches(&json!("refs/heads/release/*"), "release/1.x"));
        assert!(ref_matches(&json!("ma?n"), "main"));
        assert!(!ref_matches(&json!("refs/heads/dev"), "main"));
    }
}
