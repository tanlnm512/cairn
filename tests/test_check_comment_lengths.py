"""Contracts for the shrink-only comment-style gate over ``src``."""
from __future__ import annotations

import json

import pytest

from cairn.cli.system.comment_style import (
    CommentStyleError,
    check_comment_baseline,
    main,
    scan_comment_violations,
)


LETTER_BLOCK = "# alpha\n# beta\n# gamma\n# delta\n"
WORD_BLOCK = "# north\n# south\n# east\n# west\n"


def _repo(tmp_path, files):
    root = tmp_path / "repo"
    for rel, text in files.items():
        target = root / "src" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def _cli(root, baseline, *flags):
    return main(["--root", str(root), "--baseline", str(baseline), *flags])


def test_scan_flags_only_comment_blocks_longer_than_three_lines(tmp_path):
    root = _repo(tmp_path, {"a.py": LETTER_BLOCK + "\n# one\n# two\n# three\n"})

    violations = scan_comment_violations(root)

    assert len(violations) == 1
    v = violations[0]
    assert v["kind"] == "comment"
    assert v["path"] == "src/a.py"
    assert (v["line"], v["end_line"], v["length"]) == (1, 4, 4)
    assert v["fingerprint"].startswith("comment:")


def test_scan_flags_only_docstrings_longer_than_three_lines(tmp_path):
    source = (
        '"""\nfirst\nsecond\n"""\n'
        "def short_but_long_text():\n"
        '    """One very long line that still respects the physical-line contract."""\n'
        "    return 1\n"
    )
    root = _repo(tmp_path, {"a.py": source})

    violations = scan_comment_violations(root)

    assert len(violations) == 1
    v = violations[0]
    assert v["kind"] == "docstring"
    assert v["path"] == "src/a.py"
    assert (v["line"], v["end_line"], v["length"]) == (1, 4, 4)
    assert v["fingerprint"].startswith("docstring:")


def test_blank_lines_and_code_split_comment_blocks(tmp_path):
    split_by_blank = "# one\n# two\n\n# three\n# four\n"
    split_by_code = "# one\n# two\n# three\nX = 1\n# four\n# five\n# six\n"
    root = _repo(tmp_path, {"blank.py": split_by_blank, "code.py": split_by_code})

    assert scan_comment_violations(root) == []


def test_trailing_comments_do_not_form_comment_blocks(tmp_path):
    trailing = "a = 1  # one\nb = 2  # two\nc = 3  # three\nd = 4  # four\n"
    mixed = "# one\n# two\n# three\nX = 1  # four\n"
    root = _repo(tmp_path, {"trailing.py": trailing, "mixed.py": mixed})

    assert scan_comment_violations(root) == []


def test_scan_fails_closed_on_broken_syntax(tmp_path):
    root = _repo(tmp_path, {"broken.py": "def broken(:\n    pass\n"})

    with pytest.raises(CommentStyleError, match=r"broken\.py"):
        scan_comment_violations(root)


def test_duplicate_growth_exceeds_the_baseline_occurrence_count(tmp_path, capsys):
    root = _repo(tmp_path, {"a.py": LETTER_BLOCK, "b.py": LETTER_BLOCK})
    baseline = tmp_path / "baseline.json"
    assert _cli(root, baseline, "--init") == 0
    capsys.readouterr()
    (root / "src" / "c.py").write_text(LETTER_BLOCK, encoding="utf-8")

    result = check_comment_baseline(root, baseline)

    assert result["ok"] is False
    assert [v["path"] for v in result["new"]] == ["src/c.py"]
    assert result["stale"] == []
    assert _cli(root, baseline, "--json") == 1
    failed = json.loads(capsys.readouterr().out)
    assert failed["ok"] is False
    assert [v["path"] for v in failed["new"]] == ["src/c.py"]


def test_stale_entries_fail_and_name_the_last_seen_path(tmp_path, capsys):
    root = _repo(tmp_path, {"b.py": LETTER_BLOCK + "X = 1\n"})
    baseline = tmp_path / "baseline.json"
    assert _cli(root, baseline, "--init") == 0
    capsys.readouterr()
    (root / "src" / "b.py").write_text("X = 1\n", encoding="utf-8")

    result = check_comment_baseline(root, baseline)
    rc = _cli(root, baseline)
    captured = capsys.readouterr()
    out = captured.out + captured.err

    assert result["ok"] is False
    assert result["new"] == []
    assert len(result["stale"]) == 1
    assert result["stale"][0]["paths"] == ["src/b.py"]
    assert rc == 1
    assert "src/b.py" in out
    assert "stale" in out


def test_missing_or_malformed_baseline_fails_naming_the_file(tmp_path, capsys):
    root = _repo(tmp_path, {"a.py": "X = 1\n"})
    baseline = tmp_path / "baseline.json"
    with pytest.raises(CommentStyleError, match="baseline.json"):
        check_comment_baseline(root, baseline)
    baseline.write_text("{not-json", encoding="utf-8")
    with pytest.raises(CommentStyleError, match="baseline.json"):
        check_comment_baseline(root, baseline)

    assert _cli(root, baseline) == 1
    captured = capsys.readouterr()
    assert "baseline.json" in captured.out + captured.err


def test_initialization_refuses_an_existing_baseline(tmp_path, capsys):
    root = _repo(tmp_path, {"a.py": LETTER_BLOCK})
    baseline = tmp_path / "baseline.json"
    assert _cli(root, baseline, "--init") == 0
    first = baseline.read_bytes()
    capsys.readouterr()

    assert _cli(root, baseline, "--init") == 1
    captured = capsys.readouterr()
    assert "baseline.json" in captured.out + captured.err
    assert baseline.read_bytes() == first


def test_updates_only_shrink_the_baseline(tmp_path, capsys):
    root = _repo(tmp_path, {"a.py": LETTER_BLOCK, "b.py": WORD_BLOCK})
    baseline = tmp_path / "baseline.json"
    assert _cli(root, baseline, "--init") == 0
    (root / "src" / "a.py").write_text("X = 1\n", encoding="utf-8")
    assert _cli(root, baseline) == 1
    capsys.readouterr()

    assert _cli(root, baseline, "--update") == 0
    shrunk = baseline.read_bytes()
    assert _cli(root, baseline) == 0

    (root / "src" / "c.py").write_text(LETTER_BLOCK, encoding="utf-8")
    assert _cli(root, baseline, "--update") == 1
    captured = capsys.readouterr()
    assert "growth" in captured.out + captured.err
    assert baseline.read_bytes() == shrunk


def test_json_mode_exposes_the_remaining_count(tmp_path, capsys):
    root = _repo(tmp_path, {"a.py": LETTER_BLOCK, "b.py": WORD_BLOCK})
    baseline = tmp_path / "baseline.json"
    assert _cli(root, baseline, "--init") == 0
    capsys.readouterr()

    assert _cli(root, baseline, "--json") == 0

    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["remaining"] == 2
    assert data["new"] == []
    assert data["stale"] == []


def test_success_output_prints_the_remaining_count(tmp_path, capsys):
    root = _repo(tmp_path, {"a.py": LETTER_BLOCK, "b.py": WORD_BLOCK})
    baseline = tmp_path / "baseline.json"
    assert _cli(root, baseline, "--init") == 0
    capsys.readouterr()

    assert _cli(root, baseline) == 0

    assert "2 grandfathered violations remaining" in capsys.readouterr().out


def test_baseline_regeneration_is_deterministic(tmp_path):
    root = _repo(tmp_path, {"a.py": LETTER_BLOCK, "b.py": LETTER_BLOCK + "\n" + WORD_BLOCK})
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    assert _cli(root, first, "--init") == 0
    assert _cli(root, second, "--init") == 0
    assert first.read_bytes() == second.read_bytes()
    assert scan_comment_violations(root) == scan_comment_violations(root)

    payload = json.loads(first.read_text(encoding="utf-8"))
    entries = payload["violations"]
    assert payload["schema_version"] == 1
    assert [e["fingerprint"] for e in entries] == sorted(e["fingerprint"] for e in entries)
    assert sum(e["count"] for e in entries) == 3
