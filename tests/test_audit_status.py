"""Audit-status counting from findings indexes and fixed-ID sidecars."""
from __future__ import annotations

import json

import pytest


_GROUPS = {"P0": ["C77", "C1"], "P1": ["Q1"], "P2": ["S1"], "P3": ["SY1"]}


def _mod():
    from cairn.cli.system import audit_status

    return audit_status


def _index_markdown(groups: dict[str, list[str]]) -> str:
    labels = {"P0": "High", "P1": "Medium", "P2": "Low", "P3": "Systemic clusters"}
    second_column = {"P3": "Scope"}
    total = sum(len(ids) for ids in groups.values())
    parts = [f"# Fixture audit\n\n## Findings index — all {total} by priority\n"]
    for priority in ("P0", "P1", "P2", "P3"):
        ids = groups[priority]
        column = second_column.get(priority, "Location")
        parts.append(f"\n### {priority} — {labels[priority]} ({len(ids)})\n\n")
        parts.append(f"| ID | {column} | Issue |\n|----|{'-' * len(column)}|-------|\n")
        parts.extend(f"| {finding_id} | `x.py:1` | issue |\n" for finding_id in ids)
    return "".join(parts)


_APPENDIX = """
---

## Appendix — per-finding validation roll

### P0 (2/2 confirmed)

| ID | Verdict | Note |
|----|---------|------|
| C77 | CONFIRMED | index-only parser must skip this row |
| C1 | CONFIRMED | index-only parser must skip this row |

### C77 · repeated detail heading

Repeats C77, C1, Q1, S1, and SY1 outside the findings index.
"""


def _write_sidecar(root, stem: str, fixed=(), raw: str | None = None) -> None:
    path = root / "docs" / "audits" / f"{stem}.status.json"
    if raw is None:
        payload = {"schema_version": 1, "fixed": list(fixed)}
        raw = json.dumps(payload) + "\n"
    path.write_text(raw)


def _make_repo(tmp_path, stem: str = "2026-10-02", groups=None, fixed=()) -> object:
    from pathlib import Path

    root = Path(tmp_path)
    audits = root / "docs" / "audits"
    audits.mkdir(parents=True)
    (audits / f"{stem}.md").write_text(_index_markdown(groups or _GROUPS) + _APPENDIX)
    _write_sidecar(root, stem, fixed)
    return root


def test_counts_come_only_from_the_findings_index(tmp_path):
    status = _mod().collect_audit_status(_make_repo(tmp_path))
    assert status["totals"] == {
        "P0": {"total": 2, "fixed": 0, "remaining": 2},
        "P1": {"total": 1, "fixed": 0, "remaining": 1},
        "P2": {"total": 1, "fixed": 0, "remaining": 1},
        "P3": {"total": 1, "fixed": 0, "remaining": 1},
    }
    assert status["total"] == 5
    assert status["remaining"] == 5
    assert status["audits"] == [
        {
            "audit": "docs/audits/2026-10-02.md",
            "sidecar": "docs/audits/2026-10-02.status.json",
        }
    ]


def test_fixed_ids_burn_down_remaining_without_changing_totals(tmp_path):
    status = _mod().collect_audit_status(_make_repo(tmp_path, fixed=["C77"]))
    assert status["totals"]["P0"] == {"total": 2, "fixed": 1, "remaining": 1}
    assert status["totals"]["P1"]["total"] == 1
    assert status["total"] == 5
    assert status["remaining"] == 4


def test_aggregates_every_audit_under_docs_audits(tmp_path):
    root = _make_repo(tmp_path)
    (root / "docs" / "audits" / "2026-09-01.md").write_text(
        _index_markdown({"P0": ["C9"], "P1": ["Q9"], "P2": ["S9"], "P3": ["SY9"]})
        + _APPENDIX
    )
    _write_sidecar(root, "2026-09-01", fixed=["C9", "SY9"])
    status = _mod().collect_audit_status(root)
    assert [entry["audit"] for entry in status["audits"]] == [
        "docs/audits/2026-09-01.md",
        "docs/audits/2026-10-02.md",
    ]
    assert status["totals"]["P0"] == {"total": 3, "fixed": 1, "remaining": 2}
    assert status["totals"]["P3"] == {"total": 2, "fixed": 1, "remaining": 1}
    assert status["total"] == 9
    assert status["remaining"] == 7


@pytest.mark.parametrize(
    ("raw", "needle"),
    [
        ('{"schema_version": 2, "fixed": []}', "schema_version"),
        ('{"schema_version": 1}', "fixed"),
        ('{"schema_version": 1, "fixed": {}}', "fixed"),
        ('{"schema_version": 1, "fixed": ["C77", "C77"]}', "duplicate"),
        ('{"schema_version": 1, "fixed": ["NOPE1"]}', "unknown"),
        ("not-json{", "invalid JSON"),
    ],
)
def test_rejects_invalid_sidecars_naming_the_sidecar(tmp_path, raw, needle):
    root = _make_repo(tmp_path)
    _write_sidecar(root, "2026-10-02", raw=raw)
    with pytest.raises(_mod().AuditStatusError) as excinfo:
        _mod().collect_audit_status(root)
    assert needle in str(excinfo.value)
    assert "docs/audits/2026-10-02.status.json" in str(excinfo.value)


@pytest.mark.parametrize(
    ("remove", "missing"),
    [
        ("docs/audits/2026-10-02.md", "docs/audits/2026-10-02.md"),
        ("docs/audits/2026-10-02.status.json", "docs/audits/2026-10-02.status.json"),
    ],
)
def test_missing_inputs_fail_naming_the_file(tmp_path, remove, missing):
    root = _make_repo(tmp_path)
    (root / remove).unlink()
    with pytest.raises(_mod().AuditStatusError, match=missing):
        _mod().collect_audit_status(root)


def test_empty_audit_directory_fails_naming_the_directory(tmp_path):
    from pathlib import Path

    root = Path(tmp_path)
    (root / "docs" / "audits").mkdir(parents=True)
    with pytest.raises(_mod().AuditStatusError, match=r"docs/audits"):
        _mod().collect_audit_status(root)


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda text: text.replace("### P1 — Medium (1)", "### P1 — Medium (2)"), "P1"),
        (
            lambda text: text.replace(
                "| Q1 | `x.py:1` | issue |", "| Q1 | `x.py:1` | issue |\n| Q1 | `x.py:1` | issue |"
            ).replace("### P1 — Medium (1)", "### P1 — Medium (2)"),
            "duplicate",
        ),
        (
            lambda text: text.replace("### P3 — Systemic clusters (1)\n\n", "").replace(
                "| SY1 | `x.py:1` | issue |\n", ""
            ),
            "P3",
        ),
        (
            lambda text: text.replace(
                "| Q1 | `x.py:1` | issue |", "| X1 | `x.py:1` | issue |"
            ),
            "X1",
        ),
        (lambda text: text.replace("## Findings index", "## Something else"), "index"),
    ],
)
def test_rejects_malformed_indexes_naming_the_audit(tmp_path, mutate, needle):
    root = _make_repo(tmp_path)
    audit = root / "docs" / "audits" / "2026-10-02.md"
    audit.write_text(mutate(audit.read_text()))
    with pytest.raises(_mod().AuditStatusError) as excinfo:
        _mod().collect_audit_status(root)
    assert needle in str(excinfo.value)
    assert "docs/audits/2026-10-02.md" in str(excinfo.value)


def test_cli_prints_human_and_json_output(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(_make_repo(tmp_path, fixed=["C77"]))
    mod = _mod()
    assert mod.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["totals"]["P0"] == {"total": 2, "fixed": 1, "remaining": 1}
    assert payload["total"] == 5
    assert payload["remaining"] == 4
    assert mod.main([]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "P0 1/2 remaining",
        "P1 1/1 remaining",
        "P2 1/1 remaining",
        "P3 1/1 remaining",
        "total 4/5 remaining",
    ]


def test_cli_failures_are_single_line_and_name_the_file(tmp_path, monkeypatch, capsys):
    root = _make_repo(tmp_path)
    _write_sidecar(root, "2026-10-02", raw="not-json{")
    monkeypatch.chdir(root)
    mod = _mod()
    assert mod.main(["--json"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"].endswith("invalid JSON")
    assert "2026-10-02.status.json" in captured.out
    assert not captured.err
    assert mod.main([]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("\n") == 1
    assert "2026-10-02.status.json" in captured.err
    assert "Traceback" not in captured.err


def test_repeated_runs_produce_identical_output(tmp_path, monkeypatch, capsys):
    root = _make_repo(tmp_path)
    mod = _mod()
    first = mod.collect_audit_status(root)
    second = mod.collect_audit_status(root)
    assert first == second
    monkeypatch.chdir(root)
    runs = []
    for _ in range(2):
        assert mod.main([]) == 0
        runs.append(capsys.readouterr().out)
    assert runs[0] == runs[1]
