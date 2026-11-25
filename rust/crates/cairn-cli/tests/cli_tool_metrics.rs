//! `tool_metrics` rows for CLI lookups: a lookup that finds nothing is a successful call.

mod common;

use common::Fixture;

#[test]
fn a_lookup_that_finds_nothing_is_recorded_as_a_successful_call() {
    let fx = Fixture::new("tool-metrics");
    fx.build();

    // (arguments, exits successfully, recorded status): a lookup that finds
    // nothing, one that answers, and a command that cannot run.
    for (args, exits_ok, status) in [
        (vec!["impact", "no_such_symbol"], false, "ok"),
        (vec!["def", "one"], true, "ok"),
        (vec!["impact", "--depth", "0", "one"], false, "error"),
    ] {
        let mut full = vec![args[0].to_string(), "--db".to_string(), fx.db.clone()];
        full.extend(args[1..].iter().map(|a| a.to_string()));
        let full: Vec<&str> = full.iter().map(String::as_str).collect();
        let (ok, out) = common::cairn(&fx.home, None, None, &full);
        assert_eq!(ok, exits_ok, "{args:?}: {out}");
        assert_eq!(
            fx.tool_metrics().last(),
            Some(&(args[0].to_string(), status.to_string())),
            "{args:?}"
        );
    }
}
