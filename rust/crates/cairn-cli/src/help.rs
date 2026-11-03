//! Root help presentation: clap styles, grouped command sections, examples.

use std::collections::HashSet;

use clap::Command;
use clap::builder::styling::{AnsiColor, Color, Style, Styles};

use crate::display::{DIM, LABEL, paint_with};

/// clap help palette mirroring the display theme constants.
pub const STYLES: Styles = Styles::styled()
    .header(Style::new().bold())
    .usage(Style::new().bold())
    .literal(Style::new().bold())
    .placeholder(Style::new().dimmed())
    .error(
        Style::new()
            .bold()
            .fg_color(Some(Color::Ansi(AnsiColor::Red))),
    )
    .valid(
        Style::new()
            .bold()
            .fg_color(Some(Color::Ansi(AnsiColor::Green))),
    )
    .invalid(
        Style::new()
            .bold()
            .fg_color(Some(Color::Ansi(AnsiColor::Yellow))),
    )
    .context(Style::new().dimmed());

/// Root help template: native usage/options blocks, grouped sections as after-help.
pub const ROOT_TEMPLATE: &str = "\
{before-help}{about-with-newline}
{usage-heading} {usage}

{options}{after-help}\
";

/// Root help section order; every visible top-level command appears once.
const SECTIONS: &[(&str, &[&str])] = &[
    (
        "Graph store",
        &[
            "init",
            "build",
            "update",
            "sync",
            "import-scip",
            "checkpoint",
            "embed",
            "dataflow",
        ],
    ),
    (
        "Query the graph",
        &[
            "def",
            "callers",
            "callees",
            "search",
            "grep",
            "semantic",
            "path",
            "taint",
            "impact",
            "blast",
            "deps",
            "federated-search",
            "communities",
            "map",
            "tree",
            "stats",
        ],
    ),
    (
        "Understand & review",
        &["ask", "context", "pack", "rationale", "review", "viz"],
    ),
    (
        "Knowledge & memory",
        &[
            "knowledge",
            "memory",
            "wiki",
            "compass",
            "skill",
            "task",
            "validate",
            "validate-paths",
            "verify",
        ],
    ),
    (
        "System",
        &[
            "config",
            "doctor",
            "status",
            "metrics",
            "report",
            "connect",
            "dashboard",
            "serve",
            "hooks",
            "install-agents",
            "uninstall-agents",
            "uninstall",
            "version",
        ],
    ),
];

/// Per-command `Examples:` footers, applied to the parsed command tree.
const EXAMPLES: &[(&str, &str)] = &[
    (
        "build",
        "Examples:\n  cairn build\n  cairn build --repo cairn",
    ),
    (
        "update",
        "Examples:\n  cairn update\n  cairn update --repo cairn --diff-ref HEAD~1..HEAD",
    ),
    (
        "search",
        "Examples:\n  cairn search \"build_graph*\"\n  cairn search --json --kind function \"*pack*\"",
    ),
    (
        "def",
        "Examples:\n  cairn def build_graph\n  cairn def --json build_graph",
    ),
    (
        "callers",
        "Examples:\n  cairn callers build_graph\n  cairn callers --fuzzy invoke",
    ),
    (
        "ask",
        "Examples:\n  cairn ask \"how does pack budgeting work\"\n  cairn ask --all-repos \"where is the store resolved\"",
    ),
    (
        "pack",
        "Examples:\n  cairn pack --task \"parse dispatch\" --budget 512\n  cairn pack --json --task \"review the graph builder\" --budget 1024",
    ),
    (
        "review",
        "Examples:\n  cairn review --base main\n  cairn review --pre-submit",
    ),
];

/// A subcommand that shows in the root listing (clap's auto `help` excluded).
fn visible(sc: &Command) -> bool {
    !sc.is_hide_set() && sc.get_name() != "help"
}

/// Grouped command sections for the root help; unlisted commands fall into "Other".
pub fn root_after_help(cmd: &Command, color: bool) -> String {
    let listed: HashSet<&str> = SECTIONS
        .iter()
        .flat_map(|(_, names)| names.iter().copied())
        .collect();
    let mut other: Vec<&str> = cmd
        .get_subcommands()
        .filter(|sc| visible(sc) && !listed.contains(sc.get_name()))
        .map(|sc| sc.get_name())
        .collect();
    other.sort_unstable();

    let width = cmd
        .get_subcommands()
        .filter(|sc| visible(sc))
        .map(|sc| sc.get_name().len())
        .max()
        .unwrap_or(0);

    let mut out = String::new();
    let mut render = |title: &str, names: &[&str]| {
        if !names.is_empty() {
            if !out.is_empty() {
                out.push('\n');
            }
            out.push_str(&paint_with(color, LABEL, title));
            out.push('\n');
            for name in names {
                let about = cmd
                    .find_subcommand(name)
                    .and_then(|sc| sc.get_about())
                    .map(|about| about.to_string())
                    .unwrap_or_default();
                out.push_str(&format!(
                    "  {}  {about}\n",
                    paint_with(color, LABEL, &format!("{name:<width$}"))
                ));
            }
        }
    };
    for (title, names) in SECTIONS {
        render(title, names);
    }
    if !other.is_empty() {
        render("Other", &other);
    }
    out.push_str(&format!(
        "\n{}",
        paint_with(
            color,
            DIM,
            "Run 'cairn <command> --help' for more information on a command."
        )
    ));
    out
}

/// Attach the per-command example footers; unknown names are skipped.
pub fn apply_examples(cmd: &mut Command) {
    for &(name, text) in EXAMPLES {
        if let Some(sub) = cmd.find_subcommand_mut(name) {
            *sub = sub.clone().after_help(text);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cli::Cli;
    use clap::CommandFactory;

    fn visible_commands() -> Vec<String> {
        Cli::command()
            .get_subcommands()
            .filter(|sc| visible(sc))
            .map(|sc| sc.get_name().to_string())
            .collect()
    }

    #[test]
    fn sections_cover_every_visible_top_level_command() {
        let listed: HashSet<&str> = SECTIONS
            .iter()
            .flat_map(|(_, names)| names.iter().copied())
            .collect();
        let visible = visible_commands();
        assert_eq!(visible.len(), listed.len(), "section/command count drift");
        for name in &visible {
            assert!(
                listed.contains(name.as_str()),
                "command {name} missing from SECTIONS"
            );
        }
        let cmd = Cli::command();
        for (_, names) in SECTIONS {
            for name in *names {
                let sc = cmd
                    .find_subcommand(name)
                    .unwrap_or_else(|| panic!("SECTIONS lists unknown command {name}"));
                assert!(!sc.is_hide_set(), "SECTIONS lists hidden command {name}");
            }
        }
    }

    #[test]
    fn root_after_help_renders_every_command_with_its_about() {
        let cmd = Cli::command();
        let text = root_after_help(&cmd, false);
        for name in visible_commands() {
            assert!(text.contains(name.as_str()), "after_help missing {name}");
        }
        for sc in cmd.get_subcommands().filter(|sc| visible(sc)) {
            if let Some(about) = sc.get_about() {
                assert!(
                    text.contains(&about.to_string()),
                    "after_help about drift for {}",
                    sc.get_name()
                );
            }
        }
        assert!(!text.contains("Other"), "a command fell out of SECTIONS");
    }

    #[test]
    fn root_after_help_paints_when_color_is_on() {
        let cmd = Cli::command();
        let plain = root_after_help(&cmd, false);
        let styled = root_after_help(&cmd, true);
        assert!(!plain.contains(LABEL));
        assert!(styled.contains(LABEL));
        assert!(styled.contains(DIM));
    }

    #[test]
    fn examples_land_on_real_commands() {
        let mut cmd = Cli::command();
        apply_examples(&mut cmd);
        for &(name, text) in EXAMPLES {
            let sc = cmd
                .find_subcommand(name)
                .unwrap_or_else(|| panic!("EXAMPLES names unknown command {name}"));
            assert_eq!(
                sc.get_after_help().map(|h| h.to_string()),
                Some(text.to_string())
            );
        }
    }
}
