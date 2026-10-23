//! Skill generation: selector resolution, centrality ranking, assembly,
//! the SKILL.md emitter, the pre-write critic gate, and the polish stage.

use std::collections::HashSet;
use std::path::{Path, PathBuf};

use rusqlite::Connection;
use yaml_rust2::Yaml;
use yaml_rust2::yaml::Hash;

use crate::tasks::{create_task, latest_task, list_tasks, read_result, result_critic_status};
use crate::tools_km::{
    self, Bundle, Concept, ToolError, emit_yaml, extract_file_refs, extract_symbol_refs,
    file_exists, find_compass_concept, graph_verification, symbol_exists, ys,
};

pub const DEFAULT_TOP_K: usize = 20;
pub const DEFAULT_TOP_N: usize = 5;
pub const REFERENCE_SPLIT_CHARS: usize = 4000;
pub const POLISH_KIND: &str = "skill-polish";

pub const POLISH_INSTRUCTIONS: &str = concat!(
    "Polish the SKILL.md named in facts.skill_path: improve its wording; keep ",
    "the YAML frontmatter and the section structure, and reuse only its ",
    "existing backtick-quoted references. Return the full polished document text."
);

// --- selector (skillgen/selector.py port) ----------------------------------------

/// Resolved selector: qualified symbol names plus tokens that matched nothing.
#[derive(Debug, Clone, Default)]
pub struct SelectorResolution {
    pub candidates: Vec<String>,
    pub unmatched: Vec<String>,
}

/// Resolve each whitespace-separated selector token against the graph.
pub fn resolve_selector(conn: &Connection, selector: &str) -> SelectorResolution {
    let mut candidates: Vec<String> = Vec::new();
    let mut unmatched: Vec<String> = Vec::new();
    for token in selector.split_whitespace() {
        match resolve_token(conn, token) {
            Some(resolved) => candidates.extend(resolved),
            None => unmatched.push(token.to_string()),
        }
    }
    let mut unique = candidates;
    unique.sort();
    unique.dedup();
    let mut unmatched_unique: Vec<String> = Vec::new();
    for token in unmatched {
        if !unmatched_unique.contains(&token) {
            unmatched_unique.push(token);
        }
    }
    SelectorResolution {
        candidates: unique,
        unmatched: unmatched_unique,
    }
}

fn resolve_token(conn: &Connection, token: &str) -> Option<Vec<String>> {
    let exact = exact_symbol_names(conn, token);
    if !exact.is_empty() {
        return Some(exact);
    }
    let under = symbols_under_path(conn, token);
    (!under.is_empty()).then_some(under)
}

fn exact_symbol_names(conn: &Connection, token: &str) -> Vec<String> {
    // Module-kind rows are packaging artifacts; a module-named token falls
    // through to module-path resolution.
    let Ok(mut stmt) = conn
        .prepare(
            "SELECT qualified_name, name FROM symbols \
             WHERE (name = ?1 OR qualified_name = ?2) AND kind != 'module'",
        )
        .map_err(|_| ())
    else {
        return Vec::new();
    };
    let Ok(mapped) = stmt
        .query_map(rusqlite::params![token, token], |r| {
            Ok((
                r.get::<_, Option<String>>(0)?,
                r.get::<_, Option<String>>(1)?,
            ))
        })
        .map_err(|_| ())
    else {
        return Vec::new();
    };
    let mut names: Vec<String> = mapped
        .flatten()
        .map(|(qualified, name)| qualified.or(name).unwrap_or_default())
        .filter(|n| !n.is_empty())
        .collect();
    names.sort();
    names.dedup();
    names
}

fn symbols_under_path(conn: &Connection, token: &str) -> Vec<String> {
    let Some((repo, module_path)) = crate::tools_km::resolve_module(conn, token, None).ok() else {
        return Vec::new();
    };
    let mut sql = String::from(
        "SELECT s.qualified_name, s.name FROM symbols s \
         JOIN files f ON s.file_id = f.id \
         WHERE (f.path = ?1 OR f.path LIKE ?2 ESCAPE '\\' OR f.path LIKE ?3 ESCAPE '\\')",
    );
    let (p0, p1, p2) = crate::tools_km::module_match_params(&module_path);
    let mut params: Vec<String> = vec![p0, p1, p2];
    if let Some(repo) = repo {
        sql.push_str(" AND f.repo_id = ?4");
        params.push(repo);
    }
    let Ok(mut stmt) = conn.prepare(&sql).map_err(|_| ()) else {
        return Vec::new();
    };
    let refs: Vec<&dyn rusqlite::ToSql> =
        params.iter().map(|p| p as &dyn rusqlite::ToSql).collect();
    let Ok(mapped) = stmt
        .query_map(refs.as_slice(), |r| {
            Ok((
                r.get::<_, Option<String>>(0)?,
                r.get::<_, Option<String>>(1)?,
            ))
        })
        .map_err(|_| ())
    else {
        return Vec::new();
    };
    let mut names: Vec<String> = mapped
        .flatten()
        .map(|(qualified, name)| qualified.or(name).unwrap_or_default())
        .filter(|n| !n.is_empty())
        .collect();
    names.sort();
    names.dedup();
    names
}

// --- ranking (skillgen/ranking.py port) ------------------------------------------

/// Candidates ranked by structural centrality, plus the tier that scored them.
#[derive(Debug, Clone)]
pub struct RankedSymbols {
    pub symbols: Vec<String>,
    pub tier: &'static str,
}

/// Rank candidate qualified names by centrality; drop unresolvable ones.
pub fn rank_candidates(conn: &Connection, candidates: &[String]) -> RankedSymbols {
    let mut unique = candidates.to_vec();
    unique.sort();
    unique.dedup();
    if crate::graph::impact::closure_available(conn) {
        RankedSymbols {
            symbols: rank_from_closure(conn, &unique),
            tier: "closure",
        }
    } else {
        RankedSymbols {
            symbols: rank_from_degrees(conn, &unique),
            tier: "degree",
        }
    }
}

/// Order candidates by their distinct transitive-ancestor count.
fn rank_from_closure(conn: &Connection, candidates: &[String]) -> Vec<String> {
    // Per-query cap on seed ids in an IN () clause, under SQLite's 999 limit.
    const SEED_ID_CHUNK: usize = 400;
    let mut scored: Vec<(String, i64)> = Vec::new();
    for candidate in candidates {
        let seed_ids = candidate_symbol_ids(conn, candidate);
        if seed_ids.is_empty() {
            continue;
        }
        let mut ancestors: HashSet<String> = HashSet::new();
        for chunk in seed_ids.chunks(SEED_ID_CHUNK) {
            let placeholders = vec!["?"; chunk.len()].join(",");
            let sql = format!(
                "SELECT DISTINCT t.source_id FROM transitive_edges t \
                 WHERE t.target_id IN ({placeholders})"
            );
            let Ok(mut stmt) = conn.prepare(&sql) else {
                continue;
            };
            let refs: Vec<&dyn rusqlite::ToSql> =
                chunk.iter().map(|id| id as &dyn rusqlite::ToSql).collect();
            if let Ok(mapped) = stmt.query_map(refs.as_slice(), |r| r.get::<_, String>(0)) {
                ancestors.extend(mapped.flatten());
            }
        }
        for id in &seed_ids {
            ancestors.remove(id);
        }
        scored.push((candidate.clone(), ancestors.len() as i64));
    }
    ordered(scored)
}

/// Order candidates by direct in/out degree from the build_repo_map rows.
fn rank_from_degrees(conn: &Connection, candidates: &[String]) -> Vec<String> {
    let wanted: HashSet<&String> = candidates.iter().collect();
    let mut degrees: std::collections::HashMap<String, i64> = Default::default();
    if let Ok(projection) = crate::tools_repo::projection(conn) {
        for row in projection.symbols {
            if wanted.contains(&row.qualified_name) {
                *degrees.entry(row.qualified_name).or_insert(0) += row.incoming + row.outgoing;
            }
        }
    }
    ordered(
        candidates
            .iter()
            .filter_map(|c| degrees.get(c).map(|d| (c.clone(), *d)))
            .collect(),
    )
}

fn candidate_symbol_ids(conn: &Connection, candidate: &str) -> Vec<String> {
    let Ok(mut stmt) = conn
        .prepare("SELECT id FROM symbols WHERE name = ?1 OR qualified_name = ?2")
        .map_err(|_| ())
    else {
        return Vec::new();
    };
    let Ok(mapped) = stmt
        .query_map(rusqlite::params![candidate, candidate], |r| {
            r.get::<_, String>(0)
        })
        .map_err(|_| ())
    else {
        return Vec::new();
    };
    mapped.flatten().collect()
}

/// Names sorted by score desc then name asc.
fn ordered(scored: Vec<(String, i64)>) -> Vec<String> {
    let mut scored = scored;
    scored.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    scored.into_iter().map(|(name, _)| name).collect()
}

// --- assembly (skillgen/assembly.py port) ----------------------------------------

/// Assembled skill draft: module, compass, ranked symbols, and memories.
#[derive(Debug, Clone, Default)]
pub struct SkillDraft {
    pub module: String,
    pub compass_body: String,
    pub symbols: Vec<String>,
    pub memories: Vec<String>,
    pub ranking_tier: String,
}

/// The knowledge bundle for a graph connection: env override > db adjacency.
pub fn knowledge_path_for(conn: &Connection, fallback: &Path) -> PathBuf {
    if let Ok(env) = std::env::var("CAIRN_KNOWLEDGE")
        && !env.is_empty()
    {
        return PathBuf::from(env);
    }
    let db_file: Option<String> = conn
        .query_row("PRAGMA database_list", [], |r| {
            r.get::<_, Option<String>>(2)
        })
        .ok()
        .flatten();
    if let Some(db_file) = db_file.filter(|f| !f.is_empty()) {
        return Path::new(&db_file)
            .parent()
            .map_or_else(|| fallback.to_path_buf(), |p| p.join(".knowledge"));
    }
    fallback.to_path_buf()
}

/// Assemble compass body, ranked top-K symbols, and top-N memories.
pub fn assemble_draft(
    conn: &Connection,
    bundle: &Bundle,
    resolution: &SelectorResolution,
    top_k: usize,
    top_n: usize,
) -> Result<SkillDraft, ToolError> {
    if top_k == 0 || top_n == 0 {
        return Err(ToolError("top_k and top_n must be positive".into()));
    }
    let (symbols, tier) = if resolution.candidates.is_empty() {
        (Vec::new(), "unranked".to_string())
    } else {
        let ranked = rank_candidates(conn, &resolution.candidates);
        (ranked.symbols, ranked.tier.to_string())
    };
    let symbols = symbols.into_iter().take(top_k).collect::<Vec<_>>();
    let module = module_of(conn, &resolution.candidates);
    Ok(SkillDraft {
        compass_body: compass_body(bundle, &module),
        memories: memories(conn, bundle, &module, &symbols, top_n)?,
        module,
        symbols,
        ranking_tier: tier,
    })
}

/// Longest common repo-relative directory of the candidates' files.
fn module_of(conn: &Connection, candidates: &[String]) -> String {
    let mut repo_id: Option<String> = None;
    let mut prefix: Option<Vec<String>> = None;
    for qualified in candidates {
        let Ok(mut stmt) = conn
            .prepare(
                "SELECT DISTINCT f.repo_id, f.path FROM symbols s \
                 JOIN files f ON s.file_id = f.id \
                 WHERE s.qualified_name = ?1 OR s.name = ?2 \
                 ORDER BY f.repo_id, f.path",
            )
            .map_err(|_| ())
        else {
            continue;
        };
        let Ok(mapped) = stmt
            .query_map(rusqlite::params![qualified, qualified], |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))
            })
            .map_err(|_| ())
        else {
            continue;
        };
        for row in mapped.flatten() {
            let (row_repo, path) = row;
            match &repo_id {
                None => repo_id = Some(row_repo),
                Some(current) if current != &row_repo => return String::new(),
                _ => {}
            }
            let dirs: Vec<String> = path
                .split('/')
                .map(str::to_string)
                .collect::<Vec<_>>()
                .split_last()
                .map(|(_, dirs)| dirs.to_vec())
                .unwrap_or_default();
            prefix = Some(match prefix {
                None => dirs,
                Some(prev) => common_prefix(&prev, &dirs),
            });
        }
    }
    prefix.unwrap_or_default().join("/")
}

fn common_prefix(a: &[String], b: &[String]) -> Vec<String> {
    a.iter()
        .zip(b)
        .take_while(|(x, y)| x == y)
        .map(|(x, _)| x.clone())
        .collect()
}

fn compass_body(bundle: &Bundle, module: &str) -> String {
    if module.is_empty() {
        return String::new();
    }
    find_compass_concept(bundle, module)
        .ok()
        .flatten()
        .map(|c| c.body)
        .unwrap_or_default()
}

/// Top-N memory entries (title + body) relevant to the module and symbols.
fn memories(
    conn: &Connection,
    bundle: &Bundle,
    module: &str,
    symbols: &[String],
    top_n: usize,
) -> Result<Vec<String>, ToolError> {
    let mut parts: Vec<String> = vec![module.to_string()];
    parts.extend(
        symbols
            .iter()
            .map(|s| s.rsplit('.').next().unwrap_or(s).to_string()),
    );
    let query = parts.join(" ").trim().to_string();
    if query.is_empty() {
        return Ok(Vec::new());
    }
    let hits = tools_km::search_memory(conn, bundle, &query, None, None, false, None)?;
    Ok(hits
        .iter()
        .take(top_n)
        .map(|c| {
            format!("{}\n\n{}", c.title.as_deref().unwrap_or(""), c.body)
                .trim()
                .to_string()
        })
        .collect())
}

// --- emitter (skillgen/emitter.py port) ------------------------------------------

/// Normalize text to a filesystem slug.
pub fn slugify(text: &str) -> String {
    let mut out = String::new();
    let mut prev_dash = false;
    for c in text.to_lowercase().chars() {
        if c.is_ascii_lowercase() || c.is_ascii_digit() {
            out.push(c);
            prev_dash = false;
        } else if !prev_dash {
            out.push('-');
            prev_dash = true;
        }
    }
    out.trim_matches('-').to_string()
}

/// Default landing directory `<workspace>/.agents/skills/cairn-<slug>/`.
pub fn landing_dir(workspace: &Path, slug: &str) -> Result<PathBuf, String> {
    if slug.is_empty() || slug.trim() != slug || slug == "." || slug == ".." {
        return Err(format!("slug must be a non-empty path segment: '{slug}'"));
    }
    if slug.contains('/') || slug.contains('\\') {
        return Err(format!("slug must be a single path segment: '{slug}'"));
    }
    Ok(workspace
        .join(".agents")
        .join("skills")
        .join(format!("cairn-{slug}")))
}

/// Render the exact SKILL.md bytes.
pub fn render_skill(
    name: &str,
    description: &str,
    sections: &[(&str, &str)],
) -> Result<String, String> {
    if name.trim().is_empty() {
        return Err("skill name must be non-empty".into());
    }
    if description.trim().is_empty() {
        return Err("skill description must be non-empty".into());
    }
    if sections
        .iter()
        .any(|(heading, _)| heading.trim().is_empty())
    {
        return Err("section headings must be non-empty".into());
    }
    let mut fm = Hash::new();
    fm.insert(ys("name"), ys(name));
    fm.insert(ys("description"), ys(description));
    let frontmatter = emit_yaml(&Yaml::Hash(fm)).trim().to_string();
    let mut lines: Vec<String> = vec![format!("# {name}"), String::new()];
    for (heading, markdown) in sections {
        lines.push(format!("## {heading}"));
        lines.push(String::new());
        lines.push(markdown.trim_end().to_string());
        lines.push(String::new());
    }
    let body = lines.join("\n").trim_end_matches('\n').to_string() + "\n";
    Ok(format!("---\n{frontmatter}\n---\n\n{body}"))
}

/// Inline (heading, markdown) sections.
type InlineSections = Vec<(String, String)>;
/// Reference files as (path, content) pairs.
type ReferenceFiles = Vec<(String, String)>;

/// Split sections into (inline sections, reference files).
pub fn split_references(
    sections: &[(&str, &str)],
    max_chars: usize,
) -> (InlineSections, ReferenceFiles) {
    let mut inline: InlineSections = Vec::new();
    let mut refs: ReferenceFiles = Vec::new();
    let mut used: HashSet<String> = HashSet::new();
    for (heading, markdown) in sections {
        if markdown.chars().count() <= max_chars {
            inline.push((heading.to_string(), markdown.to_string()));
            continue;
        }
        let base = {
            let slug = slugify(heading);
            if slug.is_empty() {
                "section".to_string()
            } else {
                slug
            }
        };
        let mut filename = format!("references/{base}.md");
        let mut counter = 2;
        while used.contains(&filename) {
            filename = format!("references/{base}-{counter}.md");
            counter += 1;
        }
        used.insert(filename.clone());
        // The pointer names the path without backticks: the critic rejects
        // backtick-quoted paths it cannot verify against the graph.
        refs.push((
            filename.clone(),
            format!("# {heading}\n\n{}\n", markdown.trim_end()),
        ));
        inline.push((heading.to_string(), format!("Full section: {filename}.")));
    }
    (inline, refs)
}

// --- gate (skillgen/gate.py port) -------------------------------------------------

/// Gate outcome: ok, or rejected with failing refs.
#[derive(Debug, Clone)]
pub struct GateResult {
    pub ok: bool,
    pub failing_refs: Vec<String>,
}

/// Verify the exact `render_skill` bytes against the graph.
pub fn verify_draft(conn: &Connection, rendered: &str) -> GateResult {
    let concept = Concept {
        type_: "skill-draft".to_string(),
        concept_id: "skillgen:draft".to_string(),
        body: rendered.to_string(),
        ..Default::default()
    };
    if graph_verification(&concept, conn).unwrap_or(0.0) >= 1.0 {
        return GateResult {
            ok: true,
            failing_refs: Vec::new(),
        };
    }
    GateResult {
        ok: false,
        failing_refs: failing_refs(conn, rendered),
    }
}

fn failing_refs(conn: &Connection, rendered: &str) -> Vec<String> {
    let mut out: Vec<String> = extract_file_refs(rendered)
        .into_iter()
        .filter(|r| !file_exists(conn, r).unwrap_or(false))
        .collect();
    out.extend(
        extract_symbol_refs(rendered)
            .into_iter()
            .filter(|r| !symbol_exists(conn, r).unwrap_or(false)),
    );
    let mut seen: Vec<String> = Vec::new();
    out.retain(|r| {
        if seen.contains(r) {
            false
        } else {
            seen.push(r.clone());
            true
        }
    });
    out
}

// --- polish stage (skillgen/polish.py port) ---------------------------------------

/// Outcome of a skill polishing step.
#[derive(Debug, Clone, Default)]
pub struct PolishOutcome {
    pub state: String,
    pub task_id: String,
    pub rendered: String,
    pub failing_refs: Vec<String>,
    pub reason: String,
}

/// Advance the polish stage for the skill `slug`.
pub fn run_polish_stage(
    bundle: &Bundle,
    conn: &Connection,
    slug: &str,
    rendered: &str,
    skill_path: &str,
) -> Result<PolishOutcome, ToolError> {
    let tasks: Vec<_> = list_tasks(bundle, None, None, Some(POLISH_KIND))?
        .into_iter()
        .filter(|t| t.resource == slug)
        .collect();
    let live: Vec<_> = tasks
        .iter()
        .filter(|t| matches!(t.status.as_str(), "pending" | "in-progress"))
        .cloned()
        .collect();
    if let Some(head) = latest_task(&live) {
        return Ok(PolishOutcome {
            state: "pending".into(),
            task_id: head.id.clone(),
            ..Default::default()
        });
    }
    let done: Vec<_> = tasks
        .iter()
        .filter(|t| t.status == "done")
        .cloned()
        .collect();
    if let Some(latest) = latest_task(&done) {
        // The concept wrapper pads the stored body; strip back to the document.
        let polished = read_result(bundle, &latest.id)
            .map(|body| body.trim_start_matches('\n').to_string())
            .filter(|body| !body.is_empty());
        let Some(polished) = polished else {
            return Ok(PolishOutcome {
                state: "unavailable".into(),
                task_id: latest.id.clone(),
                reason: "the last polish task completed without a readable result".into(),
                ..Default::default()
            });
        };
        if result_critic_status(bundle, &latest.id, Some("failed")).as_deref() == Some("failed") {
            return Ok(PolishOutcome {
                state: "unavailable".into(),
                task_id: latest.id.clone(),
                reason: "the last polish result failed the queue critic".into(),
                ..Default::default()
            });
        }
        return gate(conn, &latest.id, rendered, &polished);
    }
    let mut facts = Hash::new();
    facts.insert(ys("skill_path"), ys(skill_path));
    facts.insert(ys("instructions"), ys(POLISH_INSTRUCTIONS));
    let task = create_task(bundle, POLISH_KIND, slug, facts, 0)?;
    Ok(PolishOutcome {
        state: "queued".into(),
        task_id: task.id,
        ..Default::default()
    })
}

/// Gate the polished document; only accepted bytes may replace the draft.
fn gate(
    conn: &Connection,
    task_id: &str,
    deterministic: &str,
    polished: &str,
) -> Result<PolishOutcome, ToolError> {
    let name = frontmatter_name(deterministic);
    if frontmatter_name(polished) != name {
        let shown = name
            .map(|n| format!("'{n}'"))
            .unwrap_or_else(|| "None".to_string());
        return Ok(PolishOutcome {
            state: "rejected".into(),
            task_id: task_id.to_string(),
            reason: format!("polished frontmatter must keep name {shown}"),
            ..Default::default()
        });
    }
    let result = verify_draft(conn, polished);
    if result.ok {
        return Ok(PolishOutcome {
            state: "applied".into(),
            task_id: task_id.to_string(),
            rendered: polished.to_string(),
            ..Default::default()
        });
    }
    Ok(PolishOutcome {
        state: "rejected".into(),
        task_id: task_id.to_string(),
        failing_refs: result.failing_refs,
        reason: "the polished document failed the critic gate".into(),
        ..Default::default()
    })
}

/// The frontmatter `name`; None when the document is not the emitter's shape.
fn frontmatter_name(rendered: &str) -> Option<String> {
    let rest = rendered.strip_prefix("---\n")?;
    let (meta, _body) = rest.split_once("\n---\n")?;
    let docs = yaml_rust2::YamlLoader::load_from_str(meta).ok()?;
    let Yaml::Hash(hash) = docs.into_iter().next()? else {
        return None;
    };
    match hash.get(&ys("name")) {
        Some(Yaml::String(s)) => Some(s.clone()),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn slugify_collapses_and_strips() {
        assert_eq!(slugify("src/cairn/graph"), "src-cairn-graph");
        assert_eq!(slugify("  --Mixed__Case--  "), "mixed-case");
        // non-ascii collapses to a hyphen run, then strips
        assert_eq!(slugify("café bar"), "caf-bar");
    }

    #[test]
    fn render_skill_emits_frontmatter_and_sections() {
        let rendered = render_skill(
            "cairn-demo",
            "Module context for demo.",
            &[("Key Symbols", "- `Demo.run`\n"), ("Notes", "plain\n")],
        )
        .expect("render");
        assert_eq!(
            rendered,
            "---\nname: cairn-demo\ndescription: Module context for demo.\n---\n\n\
             # cairn-demo\n\n## Key Symbols\n\n- `Demo.run`\n\n## Notes\n\nplain\n"
        );
    }

    #[test]
    fn split_references_moves_long_sections_to_files() {
        let long = "x".repeat(REFERENCE_SPLIT_CHARS + 1);
        let (inline, refs) = split_references(
            &[("Short", "ok"), ("Module Memory", long.as_str())],
            REFERENCE_SPLIT_CHARS,
        );
        assert_eq!(inline[0], ("Short".to_string(), "ok".to_string()));
        assert_eq!(
            inline[1].1,
            "Full section: references/module-memory.md.".to_string()
        );
        assert_eq!(refs[0].0, "references/module-memory.md");
        assert!(refs[0].1.starts_with("# Module Memory\n\n"));
        assert!(refs[0].1.ends_with(&format!("{}\n", long.trim_end())));
    }

    #[test]
    fn landing_dir_rejects_bad_slugs() {
        let ws = Path::new("/tmp/ws");
        assert!(landing_dir(ws, "graph").is_ok());
        assert!(landing_dir(ws, "").is_err());
        assert!(landing_dir(ws, "a/b").is_err());
        assert!(landing_dir(ws, "..").is_err());
    }
}
