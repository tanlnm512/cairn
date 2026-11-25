// F1
//! `cairn build` run from inside a repository names the repository by its directory.

mod common;

use common::Fixture;

#[test]
fn a_build_run_from_the_repository_root_names_it_by_its_directory() {
    let fx = Fixture::new("build-root");
    let (ok, out) = fx.build_from_repo();
    assert!(ok, "{out}");

    assert_eq!(fx.file_repo_ids(), ["repo"]);
}
