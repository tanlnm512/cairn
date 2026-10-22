//! Resolution triage contract: every tier, tie-break, and repair path of the resolver.

use cairn_build::resolver::{
    EdgeSignal, ResolverIndexes, Stats, repair_incoming_edges, resolve_edge, resolve_repo_edges,
};
use rusqlite::Connection;

struct Fixture {
    conn: Connection,
}

fn fixture() -> Fixture {
    let conn = Connection::open_in_memory().expect("memory db");
    conn.execute_batch(
        "CREATE TABLE files (id TEXT PRIMARY KEY, repo_id TEXT NOT NULL, path TEXT, language TEXT);
         CREATE TABLE symbols (id TEXT PRIMARY KEY, file_id TEXT NOT NULL, name TEXT,
           qualified_name TEXT, kind TEXT, arity INTEGER);
         CREATE TABLE imports (id TEXT PRIMARY KEY, file_id TEXT NOT NULL,
           imported_path TEXT, local_alias TEXT);
         CREATE TABLE edges (id TEXT PRIMARY KEY, source_id TEXT, target_id TEXT,
           target_name TEXT, kind TEXT, line INTEGER, column INTEGER, resolution TEXT,
           ref_name TEXT, receiver_type TEXT, call_arity INTEGER);",
    )
    .expect("fixture schema");
    Fixture { conn }
}

impl Fixture {
    fn file(&mut self, id: &str, repo: &str, language: &str) -> &mut Self {
        self.conn
            .execute(
                "INSERT INTO files (id, repo_id, path, language) VALUES (?1, ?2, ?3, ?4)",
                rusqlite::params![id, repo, format!("{id}.src"), language],
            )
            .expect("file");
        self
    }

    fn symbol(
        &mut self,
        id: &str,
        file_id: &str,
        name: &str,
        qname: Option<&str>,
        kind: &str,
        arity: Option<i64>,
    ) -> &mut Self {
        self.conn
            .execute(
                "INSERT INTO symbols (id, file_id, name, qualified_name, kind, arity)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                rusqlite::params![id, file_id, name, qname, kind, arity],
            )
            .expect("symbol");
        self
    }

    fn import(&mut self, id: &str, file_id: &str, path: &str, alias: Option<&str>) -> &mut Self {
        self.conn
            .execute(
                "INSERT INTO imports (id, file_id, imported_path, local_alias) VALUES (?1, ?2, ?3, ?4)",
                rusqlite::params![id, file_id, path, alias],
            )
            .expect("import");
        self
    }

    fn inheritance(&mut self, child_id: &str, parent: &str) -> &mut Self {
        self.conn
            .execute(
                "INSERT INTO edges (id, source_id, target_name, kind) VALUES (?1, ?2, ?3, 'extends')",
                rusqlite::params![format!("anc-{child_id}"), child_id, parent],
            )
            .expect("inheritance edge");
        self
    }

    fn edge(&mut self, id: &str, source_id: &str, target_name: &str, kind: &str) -> &mut Self {
        self.conn
            .execute(
                "INSERT INTO edges (id, source_id, target_id, target_name, kind, resolution)
                 VALUES (?1, ?2, NULL, ?3, ?4, NULL)",
                rusqlite::params![id, source_id, target_name, kind],
            )
            .expect("edge");
        self
    }

    fn resolve(&mut self, repo: &str, entries: Vec<(&str, Vec<EdgeSignal>)>) -> Stats {
        let entries: Vec<(String, Vec<EdgeSignal>)> = entries
            .into_iter()
            .map(|(f, edges)| (f.to_string(), edges))
            .collect();
        resolve_repo_edges(&mut self.conn, repo, &entries).expect("resolve")
    }

    fn signal(edge_id: &str, target_name: &str) -> EdgeSignal {
        EdgeSignal {
            edge_id: edge_id.to_string(),
            target_name: target_name.to_string(),
            receiver_type: None,
            call_arity: None,
            generic_tier: false,
        }
    }

    /// (target_id, target_name, resolution) after resolution.
    fn row(&self, edge_id: &str) -> (Option<String>, Option<String>, Option<String>) {
        self.conn
            .query_row(
                "SELECT target_id, target_name, resolution FROM edges WHERE id = ?1",
                [edge_id],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .expect("edge row")
    }
}

#[test]
fn unresolved_keeps_the_bare_name() {
    let mut fx = fixture();
    fx.file("f1", "r", "python")
        .symbol("s1", "f1", "other", None, "function", None);
    fx.edge("e1", "s1", "ghost", "calls");
    let stats = fx.resolve("r", vec![("f1", vec![Fixture::signal("e1", "ghost")])]);
    assert_eq!(stats.unresolved, 1);
    assert_eq!(
        fx.row("e1"),
        (None, Some("ghost".into()), Some("unresolved".into()))
    );
}

#[test]
fn same_file_same_repo_and_global_tiers_pick_uniques() {
    let mut fx = fixture();
    fx.file("f1", "r", "python")
        .file("f2", "other", "python")
        .file("f2b", "other", "python")
        .file("f3", "r2", "python")
        .file("f4", "r3", "python")
        .symbol("sf1", "f1", "join", Some("a.join"), "function", None)
        .symbol("sf2", "f2", "join", Some("b.join"), "function", None)
        .symbol("sf3", "f3", "join", Some("c.join"), "function", None)
        .symbol("owner3", "f2b", "owner", None, "function", None)
        .symbol("owner4", "f4", "owner", None, "function", None)
        .edge("e_f1", "sf1", "join", "calls")
        .edge("e_t3", "owner3", "join", "calls")
        .edge("e_t4", "owner4", "join", "calls");
    // One resolve_repo_edges call per repo, matching the builder's contract.
    let mut stats = fx.resolve("r", vec![("f1", vec![Fixture::signal("e_f1", "join")])]);
    let s = fx.resolve(
        "other",
        vec![("f2b", vec![Fixture::signal("e_t3", "join")])],
    );
    stats.exact += s.exact;
    stats.ambiguous += s.ambiguous;
    let s = fx.resolve("r3", vec![("f4", vec![Fixture::signal("e_t4", "join")])]);
    stats.ambiguous += s.ambiguous;
    // tier 1 same-file, tier 3 same-repo, tier 4 three globals -> ambiguous.
    assert_eq!(
        fx.row("e_f1"),
        (Some("sf1".into()), None, Some("exact".into()))
    );
    assert_eq!(
        fx.row("e_t3"),
        (Some("sf2".into()), None, Some("exact".into()))
    );
    assert_eq!(
        fx.row("e_t4"),
        (None, Some("join".into()), Some("ambiguous".into()))
    );
    assert_eq!(
        stats,
        Stats {
            exact: 2,
            ambiguous: 1,
            unresolved: 0
        }
    );
}

#[test]
fn same_file_arity_tiebreak_and_ambiguity() {
    let mut fx = fixture();
    fx.file("f1", "r", "python")
        .symbol("s_one", "f1", "f", None, "function", Some(1))
        .symbol("s_two", "f1", "f", None, "function", Some(2))
        .edge("e_2", "s_one", "f", "calls")
        .edge("e_3", "s_one", "f", "calls")
        .edge("e_none", "s_one", "f", "calls");
    let signals = vec![
        EdgeSignal {
            call_arity: Some(2),
            ..Fixture::signal("e_2", "f")
        },
        EdgeSignal {
            call_arity: Some(3),
            ..Fixture::signal("e_3", "f")
        },
        Fixture::signal("e_none", "f"),
    ];
    fx.resolve("r", vec![("f1", signals)]);
    assert_eq!(
        fx.row("e_2"),
        (Some("s_two".into()), None, Some("exact".into()))
    );
    assert_eq!(
        fx.row("e_3"),
        (None, Some("f".into()), Some("ambiguous".into()))
    );
    assert_eq!(
        fx.row("e_none"),
        (None, Some("f".into()), Some("ambiguous".into()))
    );
}

#[test]
fn import_specificity_scores_longest_match() {
    let mut fx = fixture();
    fx.file("f1", "r", "python")
        .file("f2", "r", "python")
        .file("f3", "other", "python")
        .import("i1", "f1", "os.path", None)
        .symbol("s_os", "f2", "join", Some("os.path.join"), "function", None)
        .symbol("s_x", "f3", "join", Some("x.path.join"), "function", None)
        .edge("e_join", "s_os", "join", "calls");

    let ix = ResolverIndexes::build(&fx.conn, Some("r")).expect("indexes");
    let (id, label) = resolve_edge("join", "f1", "r", &ix, None, None);
    // "os.path.join" scores the [os, path] prefix window (2); "x.path.join" scores 0.
    use cairn_build::resolver::Label;
    assert_eq!((id.as_deref(), label), (Some("s_os"), Label::Exact));
}

#[test]
fn import_multi_match_is_ambiguous_and_miss_falls_through() {
    let mut fx = fixture();
    fx.file("f1", "r", "python")
        .file("f2", "r", "python")
        .import("i1", "f1", "pkg.mod", None)
        .symbol("s_a", "f2", "h", Some("pkg.mod.h"), "function", None)
        .symbol("s_b", "f2", "h", Some("pkg.mod2.h"), "function", None)
        .edge("e_h", "s_a", "h", "calls");
    let ix = ResolverIndexes::build(&fx.conn, Some("r")).expect("indexes");
    // "pkg.mod.h": suffix([pkg,mod,h],[pkg,mod])=0, prefix window [pkg,mod] -> 2.
    // "pkg.mod2.h": no prefix window, tail[-1]='mod' != qsegs[0]='pkg.mod2' -> 0.
    use cairn_build::resolver::Label;
    let (id, label) = resolve_edge("h", "f1", "r", &ix, None, None);
    assert_eq!((id.as_deref(), label), (Some("s_a"), Label::Exact));

    // A second candidate scoring equally forces the ambiguous verdict.
    fx.symbol("s_c", "f2", "h", Some("pkg.mod.g"), "function", None);
    let ix = ResolverIndexes::build(&fx.conn, Some("r")).expect("indexes");
    let (id, label) = resolve_edge("h", "f1", "r", &ix, None, None);
    assert_eq!((id, label), (None, Label::Ambiguous));
}

#[test]
fn receiver_dispatch_walks_ancestors_and_falls_through() {
    let mut fx = fixture();
    fx.file("f1", "r", "python")
        .file("f2", "r", "python")
        .symbol("s_base", "f1", "Base", Some("Base"), "class", None)
        .symbol("s_child", "f1", "Child", Some("Child"), "class", None)
        .symbol("s_sub", "f1", "Sub", Some("Sub"), "class", None)
        .symbol("s_base_m", "f1", "m", Some("Base.m"), "method", None)
        .symbol("s_child_m", "f1", "m", Some("Child.m"), "method", None)
        .symbol("s_other_m", "f2", "m", Some("T.m"), "method", None)
        .inheritance("s_child", "Base")
        .inheritance("s_sub", "Child")
        .inheritance("s_other_m", "Base");

    let ix = ResolverIndexes::build(&fx.conn, Some("r")).expect("indexes");
    // Sub -> Child -> Base walk finds Child.m before Base.m.
    use cairn_build::resolver::Label;
    let (id, label) = resolve_edge("m", "f1", "r", &ix, Some("Sub"), None);
    assert_eq!((id.as_deref(), label), (Some("s_child_m"), Label::Exact));
    // No receiver: two same-file candidates -> ambiguous.
    let (id, label) = resolve_edge("m", "f1", "r", &ix, None, None);
    assert_eq!((id, label), (None, cairn_build::resolver::Label::Ambiguous));
}

#[test]
fn typed_multi_match_binds_unique_same_file_survivor() {
    let mut fx = fixture();
    fx.file("f1", "r", "python")
        .file("f2", "r", "python")
        .file("f3", "r", "python")
        .symbol("s_m1", "f1", "m", Some("T.m"), "method", None)
        .symbol("s_m2", "f2", "m", Some("T.m"), "method", None)
        .symbol("s_owner", "f1", "T", Some("T"), "class", None)
        .edge("anc1", "s_m1", "T", "contains");

    let ix = ResolverIndexes::build(&fx.conn, Some("r")).expect("indexes");
    // Both members are typed candidates; only s_m1 shares the source file.
    use cairn_build::resolver::Label;
    let (id, label) = resolve_edge("m", "f1", "r", &ix, Some("T"), None);
    assert_eq!((id.as_deref(), label), (Some("s_m1"), Label::Exact));
    // From a third file no same-file survivor exists: name tiers see two
    // same-repo candidates -> ambiguous.
    let (id, label) = resolve_edge("m", "f3", "r", &ix, Some("T"), None);
    assert_eq!((id, label), (None, cairn_build::resolver::Label::Ambiguous));
}

#[test]
fn alias_rewrite_targets_the_last_path_segment() {
    let mut fx = fixture();
    fx.file("f1", "r", "python")
        .file("f2", "r", "python")
        .import("i1", "f1", "pkg.sub.Util", Some("U"))
        .import("i2", "f1", "pkg.trail.", Some("V"))
        .symbol("s_util", "f2", "Util", Some("pkg.sub.Util"), "class", None)
        .edge("e_u", "s_util", "U", "references")
        .edge("e_v", "s_util", "V", "references");
    let stats = fx.resolve(
        "r",
        vec![(
            "f1",
            vec![Fixture::signal("e_u", "U"), Fixture::signal("e_v", "V")],
        )],
    );
    // U rewrites to Util and resolves; V's path ends in an empty segment, so the
    // name stays V and goes unresolved.
    assert_eq!(
        fx.row("e_u"),
        (Some("s_util".into()), None, Some("exact".into()))
    );
    assert_eq!(
        fx.row("e_v"),
        (None, Some("V".into()), Some("unresolved".into()))
    );
    assert_eq!(stats.exact, 1);
    assert_eq!(stats.unresolved, 1);
}

#[test]
fn generic_tier_edges_stay_name_based() {
    let mut fx = fixture();
    fx.file("f1", "r", "python")
        .symbol("s1", "f1", "wrap", None, "function", None)
        .edge("e1", "s1", "wrap", "calls");
    let signal = EdgeSignal {
        generic_tier: true,
        ..Fixture::signal("e1", "wrap")
    };
    fx.resolve("r", vec![("f1", vec![signal])]);
    assert_eq!(
        fx.row("e1"),
        (None, Some("wrap".into()), Some("unresolved".into()))
    );
}

#[test]
fn repair_resolves_only_named_targets_with_rust_generic_tier() {
    let mut fx = fixture();
    fx.file("f1", "r", "rust")
        .file("f2", "r", "python")
        .symbol("s_owner", "f1", "main", None, "function", None)
        .symbol("s_target", "f2", "helper", None, "function", None)
        .edge("e_call", "s_owner", "helper", "calls")
        .edge("e_ref", "s_owner", "helper", "references");

    // Rust calls edges keep their generic tier; references resolve normally.
    let stats = repair_incoming_edges(&mut fx.conn, &["helper"]).expect("repair");
    assert_eq!(
        fx.row("e_call"),
        (None, Some("helper".into()), Some("unresolved".into()))
    );
    assert_eq!(
        fx.row("e_ref"),
        (Some("s_target".into()), None, Some("exact".into()))
    );
    assert_eq!(stats.unresolved, 1);
    assert_eq!(stats.exact, 1);

    // Empty name list and unknown names are zero-work no-ops.
    let empty = repair_incoming_edges(&mut fx.conn, &[]).expect("repair");
    assert_eq!(empty, Stats::default());
    let missed = repair_incoming_edges(&mut fx.conn, &["nope"]).expect("repair");
    assert_eq!(missed, Stats::default());
}

#[test]
fn contains_edges_outside_the_signal_set_are_untouched() {
    let mut fx = fixture();
    fx.file("f1", "r", "python")
        .symbol("s_parent", "f1", "P", Some("P"), "class", None)
        .symbol("s_child", "f1", "C", Some("P.C"), "class", None)
        .edge("e_contains", "s_parent", "C", "contains");
    fx.conn
        .execute(
            "UPDATE edges SET target_id = 's_child', resolution = 'exact' WHERE id = 'e_contains'",
            [],
        )
        .expect("pre-resolved contains edge");
    fx.resolve("r", vec![]);
    assert_eq!(
        fx.row("e_contains"),
        (
            Some("s_child".into()),
            Some("C".into()),
            Some("exact".into())
        )
    );
}
