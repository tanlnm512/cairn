// F5
//! wiki_generate's plan→queue step: module clustering, test-majority pruning, cap,
//! and one pending wiki-page task per planned page.

use std::time::SystemTime;

use cairn_query::tasks::{get_task, list_tasks};
use cairn_query::tools_km::Bundle;
use cairn_query::tools_wiki::{PipelineError, build_page_plan, run_wiki_generate};
use rusqlite::Connection;

fn seeded_plan_store() -> Connection {
    let conn = Connection::open_in_memory().expect("mem db");
    cairn_store::schema::apply_schema(&conn).expect("schema");
    conn.execute(
        "INSERT INTO repos (id, name, path) VALUES ('cairn', 'cairn', '/w')",
        [],
    )
    .expect("repo row");
    let files = [
        "/w/rust/crates/cairn-store/src/lib.rs",
        "/w/rust/crates/cairn-store/src/swap.rs",
        "/w/rust/crates/cairn-query/src/lib.rs",
        // test-majority module: pruned from every plan
        "/w/tests/unit/a.py",
        "/w/tests/unit/b.py",
        "/w/tests/unit/c.py",
    ];
    for f in files {
        conn.execute(
            "INSERT INTO files (repo_id, path, language) VALUES ('cairn', ?1, 'rust')",
            [f],
        )
        .expect("file row");
    }
    conn
}

fn scratch_dir(tag: &str) -> std::path::PathBuf {
    let nanos = SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let dir = std::env::temp_dir().join(format!(
        "cairn-wikiplan-{tag}-{}-{nanos}",
        std::process::id()
    ));
    std::fs::create_dir_all(&dir).expect("bundle dir");
    dir
}

#[test]
fn plan_clusters_product_modules_prunes_test_majority_and_caps() {
    let conn = seeded_plan_store();
    let plan = build_page_plan(&conn, "cairn", 10).expect("plan");
    assert_eq!(
        plan[0].page_id, "overview",
        "overview page leads every plan"
    );
    assert_eq!(plan[0].module, "");
    let modules: Vec<&str> = plan[1..].iter().map(|p| p.module.as_str()).collect();
    assert_eq!(
        modules,
        vec!["rust/crates/cairn-query", "rust/crates/cairn-store"],
        "clustered product modules, test-majority module pruned"
    );
    assert_eq!(
        plan[1].page_id, "rust-crates-cairn-query",
        "slashed module slugs to dashes"
    );
    let store_page = plan
        .iter()
        .find(|p| p.module == "rust/crates/cairn-store")
        .unwrap();
    assert_eq!(store_page.seeds_files.len(), 2, "both module files seeded");

    // pages_cap counts the overview: cap 2 keeps overview + the first module.
    let capped = build_page_plan(&conn, "cairn", 2).expect("capped plan");
    assert_eq!(capped.len(), 2);
    assert_eq!(capped[0].page_id, "overview");
    assert_eq!(capped[1].module, "rust/crates/cairn-query");
}

#[test]
fn plan_names_the_repo_when_nothing_is_indexed() {
    let conn = Connection::open_in_memory().expect("mem db");
    cairn_store::schema::apply_schema(&conn).expect("schema");
    let err = build_page_plan(&conn, "ghost", 10).expect_err("unindexed repo");
    match &err {
        PipelineError::Planner(msg) => assert!(msg.contains("repo 'ghost'"), "{msg}"),
        other => panic!("expected Planner, got {other:?}"),
    }
}

#[test]
fn wiki_generate_queues_one_pending_task_per_planned_page() {
    let conn = seeded_plan_store();
    let dir = scratch_dir("queue");
    let bundle = Bundle::new(dir.clone());
    let outcome = run_wiki_generate(&conn, &bundle, "cairn", 10, false, false, false).expect("gen");
    assert_eq!(outcome.plan.len(), 3, "overview + two module pages");
    assert_eq!(
        outcome.queued_task_ids.len(),
        3,
        "one task per planned page"
    );

    let mut listed = list_tasks(&bundle, None, None, None).expect("list");
    listed.sort_by(|a, b| a.id.cmp(&b.id));
    assert_eq!(listed.len(), 3);
    for task in &listed {
        assert_eq!(task.status, "pending", "{}", task.id);
        assert!(task.task_kind.starts_with("wiki-page"));
        assert!(
            task.resource.starts_with("cairn/"),
            "resource is repo/page-id: {}",
            task.resource
        );
        assert_eq!(task.fact_str("repo"), "cairn");
        let reread = get_task(&bundle, &task.id).expect("read").expect("found");
        assert_eq!(reread.status, "pending");
    }
    let _ = std::fs::remove_dir_all(dir);
}
