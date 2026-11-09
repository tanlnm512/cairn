// F7
//! Federated search store-walk contract over a synthetic registry.

mod common;

use std::ffi::OsString;
use std::fs;
use std::path::{Path, PathBuf};

use cairn_query::tools_federation::federated_search_text;
use common::repo_root;
use serde_json::{Map, Value};

/// Temp CAIRN_HOME owning the registry and store dirs; restores env on drop.
struct FedHome {
    dir: PathBuf,
    saved_home: Option<OsString>,
    saved_backend: Option<OsString>,
}

impl FedHome {
    fn new(tag: &str) -> Self {
        let dir = std::env::temp_dir().join(format!("cairn-fedwalk-{tag}-{}", std::process::id()));
        fs::create_dir_all(&dir).expect("temp cairn home");
        let saved_home = std::env::var_os("CAIRN_HOME");
        let saved_backend = std::env::var_os("CAIRN_EMBED_BACKEND");
        unsafe { std::env::set_var("CAIRN_HOME", &dir) };
        unsafe { std::env::set_var("CAIRN_EMBED_BACKEND", "local") };
        Self {
            dir,
            saved_home,
            saved_backend,
        }
    }

    fn register(&self, ws: &str, key: &str) {
        let path = self.dir.join("workspaces.json");
        let mut map = fs::read_to_string(&path)
            .ok()
            .and_then(|t| serde_json::from_str::<Map<String, Value>>(&t).ok())
            .unwrap_or_default();
        map.insert(ws.to_string(), Value::String(key.to_string()));
        fs::write(&path, serde_json::to_string(&map).expect("registry json"))
            .expect("write registry");
    }

    fn seed_store(&self, key: &str, kg_src: &Path) {
        let store = self.dir.join(key);
        fs::create_dir_all(&store).expect("store dir");
        fs::copy(kg_src, store.join(".kg")).expect("seed .kg");
    }

    fn seed_dir_only(&self, key: &str) {
        fs::create_dir_all(self.dir.join(key)).expect("store dir without .kg");
    }

    fn seed_corrupt(&self, key: &str) {
        let store = self.dir.join(key);
        fs::create_dir_all(&store).expect("corrupt store dir");
        fs::write(store.join(".kg"), b"not a sqlite database").expect("seed corrupt .kg");
    }
}

impl Drop for FedHome {
    fn drop(&mut self) {
        restore_env("CAIRN_HOME", &self.saved_home);
        restore_env("CAIRN_EMBED_BACKEND", &self.saved_backend);
        let _ = fs::remove_dir_all(&self.dir);
    }
}

fn restore_env(name: &str, saved: &Option<OsString>) {
    match saved {
        Some(v) => unsafe { std::env::set_var(name, v) },
        None => unsafe { std::env::remove_var(name) },
    }
}

/// Hit lines carry ` @ <workspace>` attribution.
fn hit_lines(out: &str) -> Vec<&str> {
    out.lines().filter(|l| l.contains("  @ ")).collect()
}

#[test]
fn federated_search_walks_and_classifies_every_registered_store() {
    // --- registry: two ok stores, one missing, one unindexed, one bad key ----
    let home = FedHome::new("registry");
    home.register("/wsgen/alpha", "aaaaaaaaaaaaaaaa");
    home.seed_store(
        "aaaaaaaaaaaaaaaa",
        &repo_root().join("rust/golden/flow/flow-store.kg"),
    );
    home.register("/wsgen/beta", "bbbbbbbbbbbbbbbb");
    home.seed_store(
        "bbbbbbbbbbbbbbbb",
        &repo_root().join("rust/golden/nav/nav-store.kg"),
    );
    home.register("/wsgen/gone", "cccccccccccccccc");
    home.register("/wsgen/empty", "dddddddddddddddd");
    home.seed_dir_only("dddddddddddddddd");
    home.register("/wsgen/bad", "zz-not-a-store-key");

    // Only alpha's corpus defines `recurse`: attribution stays single-store.
    let out = federated_search_text("recurse", 50).expect("federated output");
    assert!(
        out.contains("from 2/4 store(s)"),
        "two ok stores of four canonical entries: {out}"
    );
    assert!(
        out.contains("[bm25 ") && out.contains("@ /wsgen/alpha"),
        "lexical hits attributed to alpha: {out}"
    );
    assert!(
        !out.contains("@ /wsgen/beta"),
        "beta searched but attribute-free: {out}"
    );
    assert!(
        out.contains("/wsgen/gone (missing)") && out.contains("/wsgen/empty (unindexed)"),
        "dropped stores named with their state: {out}"
    );
    assert!(
        !out.contains("/wsgen/bad"),
        "non-canonical key never walks: {out}"
    );

    // --- a query both corpora match: hits from both workspaces ---------------
    let out = federated_search_text("service", 50).expect("federated output");
    assert!(
        out.contains("@ /wsgen/alpha") && out.contains("@ /wsgen/beta"),
        "cross-store hits attributed per workspace: {out}"
    );

    // --- limit truncates the fused ranking -----------------------------------
    let out = federated_search_text("service", 1).expect("federated output");
    assert_eq!(hit_lines(&out).len(), 1, "limit=1 keeps one hit: {out}");
    let out = federated_search_text("service", 0).expect("federated output");
    assert_eq!(hit_lines(&out).len(), 1, "limit=0 clamps to one hit: {out}");

    // --- no-match and corrupt-store degradation -------------------------------
    let out = federated_search_text("zzqqxxnothing", 50).expect("federated output");
    assert!(
        out.contains("No matches across the searched stores"),
        "no-hit guidance: {out}"
    );
    home.register("/wsgen/corrupt", "eeeeeeeeeeeeeeee");
    home.seed_corrupt("eeeeeeeeeeeeeeee");
    let out = federated_search_text("service", 50).expect("federated output");
    assert!(
        out.contains("from 2/5 store(s)") && out.contains("/wsgen/corrupt (error)"),
        "corrupt store named, never fatal: {out}"
    );
    drop(home);

    // --- empty registry, empty query ------------------------------------------
    let empty = FedHome::new("no-registry");
    let out = federated_search_text("service", 50).expect("federated output");
    assert!(
        out.contains("No workspace stores are registered"),
        "empty-registry guidance: {out}"
    );
    drop(empty);
    let out = federated_search_text("   ", 50).expect("federated output");
    assert!(
        out.contains("Query must not be empty"),
        "blank-query guidance: {out}"
    );
}
