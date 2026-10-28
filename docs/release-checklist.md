# Pre-Release Checklist

← [README](../README.md)

Run through this before tagging a release or merging a non-trivial PR.
Each item maps to a real failure mode hit in this project's history —
don't skip on "I'm sure it's fine."

## Contents

| Section | What it covers |
|---------|----------------|
| [`## Tests`](#tests) | The full-suite local run and the no-new-skips check. |
| [`## Build verification (for changes touching indexing/embedding)`](#build-verification-for-changes-touching-indexingembedding) | Real-workspace build, path spot-checks, and embed sanity for indexing/embedding changes. |
| [`## "No behavior changed" claims`](#no-behavior-changed-claims) | AST verification for refactor/doc-only/comments-only changes. |
| [`## Concurrency / DB locks`](#concurrency--db-locks) | Parallel serve/build and read-only-daemon checks. |
| [`## Portability (the .kg is shared across machines)`](#portability-the-kg-is-shared-across-machines) | Repo-relative path checks for the cross-machine store. |
| [`## Breaking changes`](#breaking-changes) | Schema additivity, CLI/MCP signature shape, and CHANGELOG state. |
| [`## Agent / bulk-edit safety`](#agent--bulk-edit-safety) | Verification gates for sub-agent or bulk edits. |
| [`## Cutting a release`](#cutting-a-release) | Conventional commits, the changelog-first rule, the `cz` bump, and the tag-push paths. |

## Tests
- [ ] `cargo test --manifest-path Cargo.toml --workspace` — the engine
      suite plus the dev-gate and release-wiring contracts (`xtask`). Note
      the pass/ignore/fail count; compare to the last green run.
- [ ] `cargo xtask parity --store self` and `cargo xtask cli-golden` — when
      cutting a release or touching the frozen oracles (`rust/golden/`) or
      the binary, replay the oracles against the release binary.
- [ ] No new `#[ignore]`, `xfail` or `skip` slipped in to make a failure
      "pass"; skip-quarantining is forbidden.

## Build verification (for changes touching indexing/embedding)
- [ ] `cairn build` succeeds against the real workspace.
- [ ] Spot-check the DB: `sqlite3 <db> "SELECT path FROM files LIMIT 5"` —
      paths are repo-relative (portable), not absolute.
- [ ] If embeddings changed: `cairn embed` runs clean and a
      `semantic_search` returns sane hits.

## "No behavior changed" claims
If a change is supposed to be refactor/doc-only/comments-only:
- [ ] `cargo test --workspace` and the golden oracles stay green — self-reports
      of "no code changed" are unreliable; sub-agents and late-night edits
      silently drift into executable code.
- [ ] `git diff` the actual lines — read what changed, don't trust the commit
      message.

## Concurrency / DB locks
- [ ] If schema or write-path changed: start `cairn serve`, then run
      `cairn build` in parallel — must not deadlock or hit "database is locked"
      past the busy_timeout.
- [ ] Read-only MCP daemon (`cairn serve --read-only`) still answers queries
      while a CLI build holds the write lock.

## Portability (the .kg is shared across machines)
- [ ] Stored paths are repo-relative: `SELECT COUNT(*) FROM files WHERE path LIKE '/%'`
      returns 0 after a fresh build.
- [ ] `resolve_file_path` reconstructs an absolute path that exists on disk
      for a sampled symbol.

## Breaking changes
- [ ] Schema migrations are additive (ALTER ADD COLUMN), idempotent, and run
      on a pre-existing DB without error.
- [ ] Public CLI flags / MCP tool signatures haven't changed shape, or the
      change is documented in CHANGELOG.md.
- [ ] `[Unreleased]` in CHANGELOG.md reflects what shipped.

## Agent / bulk-edit safety
If changes were made by sub-agents or bulk find-replace:
- [ ] `cargo clippy --workspace --all-targets -- -D warnings` clean and the
      test suite green.
- [ ] No string literals (help text, print output, error messages) were
      silently rewritten — these are executable, not comments.

## Cutting a release

The version bump, version-file updates, and tagging are automated with
[commitizen](https://commitizen-tools.github.io/commitizen/) (`cz`), configured
in `pyproject.toml` under `[tool.commitizen]`. The CHANGELOG stays hand-curated
— `cz` drafts it, a human finalizes it. Install it once with
`uv sync --extra dev`.

### Main branch protection
`make verify-protection` is maintainer-run: `gh` must be authenticated with
admin scope because the legacy branch-protection endpoint requires it. The
check is read-only, unions evidence from the legacy and rulesets APIs, and is
deliberately not wired into CI. Use `make verify-protection ARGS=--json` for
machine-readable output. It exits zero only when `main` has:

- at least one required approving review;
- force-push denial;
- every status check named by `MANDATORY_CHECKS` in
  `rust/crates/xtask/src/verify_protection.rs`.

### Conventional commits (the prerequisite)
`cz` reads the conventional-commit history since the last tag to compute the
bump and draft the changelog. This project already follows the convention:
`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `ci:`, `chore:`, with an
optional `(scope)`. Keep doing that. `feat` → MINOR, `fix` → PATCH, and a
`BREAKING CHANGE:` footer (or `feat!:`/`fix!:`) → MAJOR.

### Release steps (run locally, on `main` after merging the PR cycle)
1. **Preview** the bump and the auto-drafted changelog section:
   ```sh
   cz bump --dry-run --changelog-to-stdout
   ```
   This prints the proposed `X.Y.Z`, which `version_files` it will touch, and
   a commit-derived changelog section. Nothing is written.

2. **Finalize `CHANGELOG.md` first.** Move the accumulated `[Unreleased]`
   entries under a dated header (`## [X.Y.Z] - YYYY-MM-DD`), expanding the
   terse auto-draft into the project's prose style if the release warrants it.
   Why manually: commitizen's generator emits one-line entries from commit
   subjects, which would discard the rich multi-paragraph entries this project
   keeps (e.g. the 0.6.1 notes). `update_changelog_on_bump = false` in
   `pyproject.toml` enforces this — `cz bump` will never rewrite CHANGELOG.md.
   Commit this change on its own (`docs: prepare X.Y.Z changelog`) before the
   bump so the bump commit only touches version files.

3. **Bump** the version, commit, and tag in one step:
   ```sh
   cz bump --yes
   ```
   This updates `version` in `pyproject.toml`, commits as
   `bump: version A -> B`, and tags `v$version`. (If the pre-release
   checklist above isn't done yet, run
   `cz bump --version-files-only` to update files without committing/tagging.)
   The Cargo workspace version (`Cargo.toml` `[workspace.package]`) is
   not a `cz` version file: bump it to the same `X.Y.Z` in the changelog
   commit — `release-rust.yml` refuses a tag that does not equal the
   workspace version, and the crates.io publish would otherwise 404 every
   binstall `pkg-url` at that version.

4. **Land the release commits on `main`, then push the tag.** `main` is
   branch-protected (changes must go through a PR with required status checks),
   so a direct `git push origin main` is rejected. Two equivalent paths:

   - **Fast path (PR):** push a branch off the current (post-bump) `main`,
     open a PR, and merge it once checks pass — then push the tag (below).
     ```sh
     git checkout -b release/$version-sync
     git push -u origin release/$version-sync
     gh pr create --title "release: sync main with $version" --body "..."
     # merge the PR, then locally:
     git checkout main && git pull --ff-only
     ```
   - **Tag-first path** (used for 0.7.0): push the tag immediately to trigger
     the release, then sync `main` via a PR afterward. PyPI publishes from the
     tag, so the release isn't blocked on `main` — but `main` MUST be synced
     afterward so it matches the released tag.

   Either way, the tag push triggers the pipelines:
   ```sh
   git push origin v$version
   ```
   `.github/workflows/release.yml` builds the per-platform bin wheels with
   maturin (the engine binary is each wheel's only payload) + the sdist,
   publishes to PyPI via Trusted Publishing, and cuts the GitHub Release from
   the `[X.Y.Z]` CHANGELOG section; `release-rust.yml` (below) attaches the
   binary tarballs and updates the Homebrew tap and crates.io. Watch it:
   `gh run watch $(gh run list --workflow=release.yml -L1 -q '.[0].databaseId')`.

### Notes
- **First release of a new PyPI project**: register a *pending* Trusted
  Publisher on pypi.org before the first tag push (Account settings →
  Publishing → add a pending publisher with owner/repo/workflow/`pypi`
  environment). The first successful publish converts it to a normal publisher
  and creates the project.
- **Rust distribution channels** (`.github/workflows/release-rust.yml`, same
  `v*` tag): tarballs + checksums attach to the GitHub Release unconditionally;
  the Homebrew tap push and the crates.io publish each skip with a workflow
  notice while their user-owned credentials are absent:
  - *Homebrew tap* — repo variable `HOMEBREW_TAP_REPO` (e.g.
    `tanlnm512/homebrew-cairn`) and secret `HOMEBREW_TAP_TOKEN` (fine-grained
    PAT, Contents read/write, scoped to the tap repo). Once set, every tag
    push updates `Formula/cairn.rb` in the tap.
  - *crates.io* — secret `CARGO_REGISTRY_TOKEN` (crates.io → Account
    Settings → API Tokens, `publish-new` + `publish-update`). Once set, the
    job `cargo publish`es the workspace crates in dependency order
    (store → parse → query → daemon → build → cli) and fails if the tag
    doesn't equal the workspace version (a mismatch would 404 every binstall
    pkg-url at that version).
  - *Before the first crates.io publish*, `cairn-parse` needs two packaging
    rulings — the job stops there today: the `tree-sitter-kotlin` git pin
    (registry dependencies must resolve to a crates.io version; vendor it
    like dart or wait for an upstream release) and the vendored
    `cairn-tree-sitter-dart` (`publish = false`, so it has no registry
    version to rewrite the path dep to). `cairn-cli` cannot publish before
    `cairn-parse` does; until then `cargo binstall --git
    https://github.com/tanlnm512/cairn cairn-cli` keeps working from the
    repo's own metadata.
- **Manual CHANGELOG section for a release**: `awk` extracts a version's
  section (the release workflow uses this to build GitHub Release notes):
  ```sh
  awk -v v="X.Y.Z" '/^## \[/ { if ($0 ~ "\\["v"\\]") { in=1; print; next } else in=0 } in { print }' CHANGELOG.md
  ```
- **Recovery if the release workflow partially fails**: PyPI publish and GitHub
  Release are separate jobs. If publish succeeds but release fails, the
  artifacts are safe on PyPI — cut the GitHub Release manually with
  `gh release create vX.Y.Z --notes-file <(awk …)` against the existing tag.
