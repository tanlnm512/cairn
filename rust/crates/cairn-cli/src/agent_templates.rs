//! Embedded agent-integration templates (the crate's `templates/agent_integration` tree).

/// Relative path within the `agent_integration` tree, and its file content.
pub const TEMPLATES: &[(&str, &str)] = &[
    (
        "commands/cairn.md",
        include_str!("../templates/agent_integration/commands/cairn.md"),
    ),
    (
        "commands/cairn-audit.md",
        include_str!("../templates/agent_integration/commands/cairn-audit.md"),
    ),
    (
        "commands/cairn-prep.md",
        include_str!("../templates/agent_integration/commands/cairn-prep.md"),
    ),
    (
        "commands/cairn-refresh.md",
        include_str!("../templates/agent_integration/commands/cairn-refresh.md"),
    ),
    (
        "commands/cairn-ship.md",
        include_str!("../templates/agent_integration/commands/cairn-ship.md"),
    ),
    (
        "cursor/cairn-explorer.json",
        include_str!("../templates/agent_integration/cursor/cairn-explorer.json"),
    ),
    (
        "cursor/cairn.mdc",
        include_str!("../templates/agent_integration/cursor/cairn.mdc"),
    ),
    (
        "cursor/knowledge-steward.json",
        include_str!("../templates/agent_integration/cursor/knowledge-steward.json"),
    ),
    (
        "skill/SKILL.md",
        include_str!("../templates/agent_integration/skill/SKILL.md"),
    ),
    (
        "skill/evals/README.md",
        include_str!("../templates/agent_integration/skill/evals/README.md"),
    ),
    (
        "skill/evals/rule01-ambiguous-name.md",
        include_str!("../templates/agent_integration/skill/evals/rule01-ambiguous-name.md"),
    ),
    (
        "skill/evals/rule06-impact-common-name.md",
        include_str!("../templates/agent_integration/skill/evals/rule06-impact-common-name.md"),
    ),
    (
        "skill/evals/rule08-precise-then-fuzzy.md",
        include_str!("../templates/agent_integration/skill/evals/rule08-precise-then-fuzzy.md"),
    ),
    (
        "skill/evals/rule08b-empty-precise-gave-up.md",
        include_str!("../templates/agent_integration/skill/evals/rule08b-empty-precise-gave-up.md"),
    ),
    (
        "skill/evals/rule09-impact-plus-cross-repo.md",
        include_str!("../templates/agent_integration/skill/evals/rule09-impact-plus-cross-repo.md"),
    ),
    (
        "skill/evals/rule10-mutations-via-cli.md",
        include_str!("../templates/agent_integration/skill/evals/rule10-mutations-via-cli.md"),
    ),
    (
        "skill/references/cli-fallback.md",
        include_str!("../templates/agent_integration/skill/references/cli-fallback.md"),
    ),
    (
        "skill/references/decision-tree.md",
        include_str!("../templates/agent_integration/skill/references/decision-tree.md"),
    ),
    (
        "skill/references/golden-rules.md",
        include_str!("../templates/agent_integration/skill/references/golden-rules.md"),
    ),
    (
        "skill/references/task-queue.md",
        include_str!("../templates/agent_integration/skill/references/task-queue.md"),
    ),
    (
        "skill/references/tool-behaviors.md",
        include_str!("../templates/agent_integration/skill/references/tool-behaviors.md"),
    ),
    (
        "skill/references/tools.md",
        include_str!("../templates/agent_integration/skill/references/tools.md"),
    ),
    (
        "skill/scripts/impact_guard.py",
        include_str!("../templates/agent_integration/skill/scripts/impact_guard.py"),
    ),
];

/// Embedded template content for `rel`, or None when the path is not vendored.
pub fn read(rel: &str) -> Option<&'static str> {
    TEMPLATES
        .iter()
        .find(|(name, _)| *name == rel)
        .map(|(_, content)| *content)
}

/// Every `skill/` template as (relative path, content), sorted by path.
pub fn skill_tree() -> impl Iterator<Item = (&'static str, &'static str)> {
    TEMPLATES
        .iter()
        .filter_map(|(name, content)| name.strip_prefix("skill/").map(|rel| (rel, *content)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn skill_tree_covers_the_whole_vendored_package() {
        let paths: Vec<&str> = TEMPLATES.iter().map(|(name, _)| *name).collect();
        assert_eq!(paths.len(), 23);
        assert!(paths.iter().all(|p| p.starts_with("skill/")
            || p.starts_with("commands/")
            || p.starts_with("cursor/")));
        assert_eq!(skill_tree().count(), 15);
    }

    #[test]
    fn read_resolves_known_and_rejects_unknown() {
        assert!(read("cursor/cairn.mdc").is_some());
        assert!(read("skill/missing.md").is_none());
    }
}
