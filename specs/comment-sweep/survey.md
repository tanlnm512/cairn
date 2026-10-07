# Survey: comment-sweep

**Created**: 2026-10-07 | **Baseline**: HEAD @ 9217c4512f1220e660a72c85bdae68f6b9bffa2b (dirty: specs/INDEX.md modified, specs/comment-sweep/ untracked)
Context read first: specs/context/structure.md + specs/context/tech.md (not rewritten).

## Items

```
item FR-001: "reduce src/ comment-style baseline by ≥50% (1213 → ≤606), no semantic change"
  evidence:   Re-counted this session: `jq '.violations | length' docs/audits/comment-style-baseline.json` → 1213 (897 docstring + 316 comment fingerprints across 188 src files; top: src/cairn/graph/embeddings.py 55, src/cairn/dashboard/data.py 36, src/cairn/graph/builder.py 34). Live gate: `UV_CACHE_DIR=/tmp/cairn-uv-survey make comment-style` → "comment-style: 1213 grandfathered violations remaining"; `make comment-style ARGS=--json` → {"new": 0, "ok": true, "remaining": 1213, "stale": 0}. Threshold machinery: src/cairn/cli/system/comment_style.py line 15 `MAX_BLOCK_LINES = 3`; detectors src/cairn/cli/system/comment_style.py:_comment_violations:57 and src/cairn/cli/system/comment_style.py:_docstring_violations:79.
  status:     TODO
  verify:     UV_CACHE_DIR=/tmp/cairn-uv-survey make comment-style ARGS=--json
  gap:        No trims applied yet; remaining count is exactly the pre-sweep 1213.

item FR-002: "migrate durable rationale to cairn memory record / docs before deletion; delivery record lists every migration"
  evidence:   Migration sink exists and is runnable: `.venv/bin/cairn memory record --help` → "Usage: cairn memory record [OPTIONS] {decision|pattern|mistake|workaround} TITLE" with "For decision/mistake/workaround, structure --body as the fact/rule itself, then a `Why:` line and a `How to apply:` line". docs/ targets exist (docs/README.md:29 documents the gate; docs/grade-a-roadmap.md:48 "or docs before deletion"). No migration list or delivery record exists under specs/comment-sweep/ (only spawns/wave-1/surveyor.md).
  status:     TODO
  verify:     .venv/bin/cairn memory record --help
  gap:        Delivery-record file/location not established — unknown — verify; zero migrations recorded.

item FR-003: "full suite green at every intermediate step; touch only src/, docs/, memory records, FR-004 wiring"
  evidence:   Related suites green this session: `CAIRN_LIB=/tmp/__no_such_lib__ UV_CACHE_DIR=/tmp/cairn-uv-survey uv run --extra test pytest tests/test_check_comment_lengths.py tests/test_report.py -q` → "29 passed in 9.38s". Full suite not run in this sandbox: `python3 -c "...s.bind(('127.0.0.1',0))..."` → "PermissionError: [Errno 1] Operation not permitted" (environment, not a defect; binding tests include tests/test_embedding_backend_quality.py:377/575, tests/test_model_warmup.py:281). No sweep edits exist yet (git status: only specs/INDEX.md M, specs/comment-sweep/ untracked).
  status:     TODO
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ UV_CACHE_DIR=/tmp/cairn-uv-survey uv run --extra test pytest tests/test_check_comment_lengths.py tests/test_report.py -q
  gap:        Full-suite green unproven here — unknown — verify (loopback EPERM in sandbox); scope constraint untested until edits exist. (Typo in the verify command above is inert; canonical form shown in evidence.)

item FR-004: "D-012: stop runpy double-import RuntimeWarning from cairn.cli.system eager report import"
  evidence:   Reproduced this session: `UV_CACHE_DIR=/tmp/cairn-uv-survey make comment-style` prints on stderr "<frozen runpy>:128: RuntimeWarning: 'cairn.cli.system.comment_style' found in sys.modules after import of package 'cairn.cli.system', but prior to execution of 'cairn.cli.system.comment_style'". Chain: src/cairn/cli/system/__init__.py line 14 `from .report import _redact_paths as _redact_paths, report as report`; src/cairn/cli/system/report.py lines 17-18 `from .audit_status import collect_audit_status` / `from .comment_style import DEFAULT_BASELINE, check_comment_baseline`. Parking decision: specs/grade-a-ratchet/tech-spec.md:352-356 "D-012: runpy import warning parked ... Fix opportunistically in the comment-sweep follow-up spec."
  status:     TODO
  verify:     UV_CACHE_DIR=/tmp/cairn-uv-survey make comment-style 2>&1 | grep RuntimeWarning
  gap:        Warning still present on every python -m gate invocation.

item FR-005: "re-derive shrink-only baseline via documented entry point; make comment-style reports ≤606"
  evidence:   Regeneration entry point: Makefile:57-58 `comment-style-shrink:` → `@uv run --no-sync python -m cairn.cli.system.comment_style --update $(ARGS)`; docs/README.md:29 "`make comment-style-shrink` re-derives the baseline after fixes; growth is refused". Shrink-only enforcement: src/cairn/cli/system/comment_style.py:_run_update:275 (writer mode; argparse help "--update ... refuses growth", main at src/cairn/cli/system/comment_style.py:main:335). Baseline path: src/cairn/cli/system/comment_style.py line 17 `DEFAULT_BASELINE = ("docs", "audits", "comment-style-baseline.json")`.
  status:     TODO
  verify:     UV_CACHE_DIR=/tmp/cairn-uv-survey make comment-style-shrink ARGS=--json && jq '.violations | length' docs/audits/comment-style-baseline.json
  gap:        Baseline still holds all 1213 pre-sweep violations; target ≤606 not met.

item NFR-001: "security not applicable"
  evidence:   Gate code handles only local source text/baselines: src/cairn/cli/system/comment_style.py:_comment_only_rows:25 (tokenize over in-memory source); no secret/auth/untrusted-input surface in the touched machinery (rg password|secret|token|api_key over comment_style.py/audit_status.py → only `tokenize`/token hits).
  status:     DONE
  verify:     rg -n "password|secret|token|api_key" src/cairn/cli/system/comment_style.py src/cairn/cli/system/audit_status.py
  gap:        -

item NFR-002: "privacy not applicable; memory records stay local"
  evidence:   `.venv/bin/cairn memory record --help` records into the local store (types decision|pattern|mistake|workaround); no network upload path in the sweep machinery.
  status:     DONE
  verify:     .venv/bin/cairn memory record --help
  gap:        -

item NFR-003: "performance not applicable; checker budget already gated"
  evidence:   `make comment-style ARGS=--json` completed in ~1.9s wall for the whole src tree this session; gate is read-only scan (scan_comment_violations / src/cairn/cli/system/comment_style.py:check_comment_baseline:206).
  status:     DONE
  verify:     UV_CACHE_DIR=/tmp/cairn-uv-survey make comment-style ARGS=--json
  gap:        -

item NFR-004: "suite green after every intermediate step; prose-pinned tests updated, never deleted"
  evidence:   Gate-contract tests exist and pass: tests/test_check_comment_lengths.py (contracts incl. test_scan_flags_only_comment_blocks_longer_than_three_lines, test_scan_flags_only_docstrings_longer_than_three_lines) — 29 passed with tests/test_report.py this session. No prose-pinning inventory of src docstrings exists yet — unknown — verify (rg over tests for removed text must precede each trim step).
  status:     TODO
  verify:     CAIRN_LIB=/tmp/__no_such_lib__ UV_CACHE_DIR=/tmp/cairn-uv-survey uv run --extra test pytest tests/test_check_comment_lengths.py -q
  gap:        Per-step full-suite verification not yet performed; prose-pin inventory unbuilt.

item NFR-005: "observability = make comment-style count + delivery-record migration list"
  evidence:   Count surface live: src/cairn/cli/system/comment_style.py:main:335 prints "comment-style: {remaining} grandfathered violations remaining" (observed: 1213); report integration at src/cairn/cli/system/report.py:check-comment lines ("comment_style remaining: N", tests/test_report.py:246-258). Delivery record does not exist — unknown — verify.
  status:     TODO
  verify:     UV_CACHE_DIR=/tmp/cairn-uv-survey make comment-style
  gap:        No delivery record / migration list on disk yet.

item NFR-006: "accessibility not applicable; no UI surface"
  evidence:   Sweep targets are src comments/docstrings plus CLI gates; no UI files in the baseline inventory (docs/audits/comment-style-baseline.json paths all start "src/").
  status:     DONE
  verify:     jq -r '.violations[].paths[]' docs/audits/comment-style-baseline.json | cut -d/ -f1-2 | sort -u
  gap:        -
```

## Supporting evidence
- Baseline file shape (docs/audits/comment-style-baseline.json): `{"schema_version": 1, "violations": [...]}`; each entry `{"count", "fingerprint", "paths"}` with fingerprints `comment:<sha256>` / `docstring:<sha256>`; matched by `_FINGERPRINT_RE` in src/cairn/cli/system/comment_style.py.
- Gate wiring: Makefile:53-54 `comment-style:` → `python -m cairn.cli.system.comment_style`; Makefile:57-58 writer target passes `--update`. Check result shape pinned as `{ok, remaining, new, stale}` (specs/grade-a-ratchet/tech-spec.md D-010; observed JSON this session).
- D-012 chain (load-bearing for FR-004): `cairn.cli.system.__init__` (line 14, report import) → `report.py` (line 17 `from .audit_status import collect_audit_status`, line 18 `from .comment_style import DEFAULT_BASELINE, check_comment_baseline`) → runpy then executes `cairn.cli.system.comment_style` already in sys.modules → `<frozen runpy>:128: RuntimeWarning` (observed verbatim on every make invocation this session).
- Consumers of the gate: tests/test_check_comment_lengths.py (unit contracts), tests/test_report.py:83-86 (report invokes comment_style main with `--init`), docs/README.md:29, docs/grade-a-roadmap.md:49 (accept criterion ≤606), Makefile:53-58.
- Environment: sandbox cannot bind 127.0.0.1 (PermissionError EPERM) — full-suite runs requiring loopback fail here even when healthy; treat as environment, never a defect. uv needs UV_CACHE_DIR redirected (default ~/.cache/uv is unreadable in sandbox).

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
