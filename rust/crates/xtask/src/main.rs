//! Repository dev gates and replay oracles: `cargo xtask <command> [args]`.

use std::process::ExitCode;

mod audit_status;
mod baseline;
mod check_doc_links;
mod cli_golden;
mod cli_smoke;
mod closure_bench;
mod comment_style;
mod conformance_table;
mod corner_store;
mod golden_record;
mod kg_diff;
mod kg_dump;
mod kg_normalize;
mod parity;
#[cfg(test)]
mod release_contract;
mod skill_evals;
mod verify_no_code_change;
mod verify_protection;

const COMMANDS: &[&str] = &[
    "parity",
    "golden-record",
    "cli-golden",
    "kg-dump",
    "kg-normalize",
    "kg-diff",
    "corner-store",
    "baseline",
    "closure-bench",
    "comment-style",
    "verify-no-code-change",
    "check-doc-links",
    "conformance-table",
    "audit-status",
    "verify-protection",
    "skill-evals",
    "cli-smoke",
];

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let Some((command, rest)) = args.split_first() else {
        eprintln!(
            "usage: cargo xtask <command> [args]\ncommands: {}",
            COMMANDS.join(", ")
        );
        return ExitCode::from(2);
    };
    match command.as_str() {
        "parity" => parity::run(rest),
        "golden-record" => golden_record::run(rest),
        "cli-golden" => cli_golden::run(rest),
        "kg-dump" => kg_dump::run(rest),
        "kg-normalize" => kg_normalize::run(rest),
        "kg-diff" => kg_diff::run(rest),
        "corner-store" => corner_store::run(rest),
        "baseline" => baseline::run(rest),
        "closure-bench" => closure_bench::run(rest),
        "comment-style" => comment_style::run(rest),
        "verify-no-code-change" => verify_no_code_change::run(rest),
        "check-doc-links" => check_doc_links::run(rest),
        "conformance-table" => conformance_table::run(rest),
        "audit-status" => audit_status::run(rest),
        "verify-protection" => verify_protection::run(rest),
        "skill-evals" => skill_evals::run(rest),
        "cli-smoke" => cli_smoke::run(rest),
        other => {
            eprintln!(
                "unknown command `{other}`; commands: {}",
                COMMANDS.join(", ")
            );
            ExitCode::from(2)
        }
    }
}
