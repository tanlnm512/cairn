"""Shrink-only comment-style gate over the ``src`` tree."""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import re
import sys
import tokenize
from collections.abc import Sequence
from pathlib import Path

MAX_BLOCK_LINES = 3
BASELINE_SCHEMA_VERSION = 1
DEFAULT_BASELINE = ("docs", "audits", "comment-style-baseline.json")
_FINGERPRINT_RE = re.compile(r"(?:comment|docstring):[0-9a-f]{64}")


class CommentStyleError(Exception):
    """Raised when the scan or the baseline cannot be trusted."""


def _comment_only_rows(source: str) -> list[int]:
    lines = source.splitlines()
    rows: list[int] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        row = token.start[0]
        if row <= len(lines) and lines[row - 1].lstrip().startswith("#"):
            rows.append(row)
    return rows


def _runs(rows: Sequence[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for row in rows:
        if runs and row == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], row)
        else:
            runs.append((row, row))
    return runs


def _strip_comment(line: str) -> str:
    text = line.lstrip()
    return text[1:].lstrip() if text.startswith("#") else text


def _fingerprint(kind: str, text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"{kind}:{digest}"


def _comment_violations(rel: str, source: str) -> list[dict]:
    lines = source.splitlines()
    violations: list[dict] = []
    for start, end in _runs(_comment_only_rows(source)):
        length = end - start + 1
        if length <= MAX_BLOCK_LINES:
            continue
        block = [lines[row - 1] for row in range(start, end + 1)]
        text = "\n".join(_strip_comment(line) for line in block)
        violations.append(
            {
                "kind": "comment",
                "path": rel,
                "line": start,
                "end_line": end,
                "length": length,
                "fingerprint": _fingerprint("comment", text),
            }
        )
    return violations


def _docstring_violations(rel: str, tree: ast.Module) -> list[dict]:
    violations: list[dict] = []
    owners = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, owners) or not node.body:
            continue
        first = node.body[0]
        if not isinstance(first, ast.Expr):
            continue
        constant = first.value
        if not isinstance(constant, ast.Constant) or not isinstance(constant.value, str):
            continue
        end = first.end_lineno or first.lineno
        length = end - first.lineno + 1
        if length <= MAX_BLOCK_LINES:
            continue
        text = ast.get_docstring(node) or ""
        violations.append(
            {
                "kind": "docstring",
                "path": rel,
                "line": first.lineno,
                "end_line": end,
                "length": length,
                "fingerprint": _fingerprint("docstring", text),
            }
        )
    return violations


def scan_comment_violations(root: Path) -> list[dict]:
    """Return every over-length comment block and docstring under ``root/src``."""
    source_root = root / "src"
    if not source_root.is_dir():
        raise CommentStyleError(f"source tree not found: {source_root}")
    violations: list[dict] = []
    for path in sorted(source_root.rglob("*.py"), key=lambda p: p.as_posix()):
        rel = path.relative_to(root).as_posix()
        try:
            with tokenize.open(path) as handle:
                source = handle.read()
            violations.extend(_comment_violations(rel, source))
            violations.extend(_docstring_violations(rel, ast.parse(source)))
        except (SyntaxError, tokenize.TokenError, UnicodeDecodeError) as exc:
            raise CommentStyleError(f"{rel}: {exc}") from exc
    violations.sort(key=lambda v: (v["path"], v["line"], v["kind"]))
    return violations


def _entries(violations: Sequence[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for violation in violations:
        entry = grouped.setdefault(
            violation["fingerprint"],
            {"fingerprint": violation["fingerprint"], "count": 0, "paths": set()},
        )
        entry["count"] += 1
        entry["paths"].add(violation["path"])
    return [
        {
            "fingerprint": fingerprint,
            "count": entry["count"],
            "paths": sorted(entry["paths"]),
        }
        for fingerprint, entry in sorted(grouped.items())
    ]


def _baseline_document(violations: Sequence[dict]) -> dict:
    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "violations": _entries(violations),
    }


def _write_baseline(path: Path, violations: Sequence[dict]) -> None:
    text = json.dumps(_baseline_document(violations), indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _load_baseline(path: Path) -> dict[str, dict]:
    if not path.is_file():
        raise CommentStyleError(f"baseline not found: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CommentStyleError(f"invalid baseline JSON: {path}: {exc}") from exc
    _validate_baseline(path, document)
    return {entry["fingerprint"]: entry for entry in document["violations"]}


def _validate_baseline(path: Path, document: object) -> None:
    where = f"invalid baseline: {path}"
    if not isinstance(document, dict) or set(document) != {"schema_version", "violations"}:
        raise CommentStyleError(where)
    if document["schema_version"] != BASELINE_SCHEMA_VERSION:
        raise CommentStyleError(f"unsupported baseline schema_version: {path}")
    rows = document["violations"]
    if not isinstance(rows, list):
        raise CommentStyleError(where)
    fingerprints: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"fingerprint", "count", "paths"}:
            raise CommentStyleError(where)
        fingerprint = row["fingerprint"]
        count = row["count"]
        paths = row["paths"]
        if not isinstance(fingerprint, str) or not _FINGERPRINT_RE.fullmatch(fingerprint):
            raise CommentStyleError(where)
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise CommentStyleError(where)
        if (
            not isinstance(paths, list)
            or not paths
            or any(not isinstance(item, str) or not item for item in paths)
            or len(set(paths)) != len(paths)
            or paths != sorted(paths)
        ):
            raise CommentStyleError(where)
        if fingerprint in fingerprints:
            raise CommentStyleError(f"duplicate baseline entry {fingerprint}: {path}")
        fingerprints.append(fingerprint)
    if fingerprints != sorted(fingerprints):
        raise CommentStyleError(f"unsorted baseline entries: {path}")


def check_comment_baseline(root: Path, baseline_path: Path) -> dict:
    """Require exact per-fingerprint occurrence equality between tree and baseline."""
    violations = scan_comment_violations(root)
    entries = _load_baseline(baseline_path)
    current: dict[str, list[dict]] = {}
    for violation in violations:
        current.setdefault(violation["fingerprint"], []).append(violation)

    new: list[dict] = []
    for fingerprint, occurrences in current.items():
        allowed = entries.get(fingerprint, {}).get("count", 0)
        excess = len(occurrences) - allowed
        if excess > 0:
            new.extend(occurrences[-excess:])

    stale: list[dict] = []
    for fingerprint, entry in entries.items():
        found = len(current.get(fingerprint, ()))
        if found < entry["count"]:
            stale.append(
                {
                    "fingerprint": fingerprint,
                    "expected": entry["count"],
                    "found": found,
                    "paths": entry["paths"],
                }
            )

    return {
        "ok": not new and not stale,
        "remaining": len(violations),
        "new": sorted(new, key=lambda v: (v["path"], v["line"], v["kind"])),
        "stale": stale,
    }


def _find_root() -> Path:
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / "src").is_dir() and (candidate / "pyproject.toml").is_file():
            return candidate
    return cwd


def _display(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _run_init(root: Path, baseline: Path, as_json: bool) -> int:
    if baseline.exists():
        raise CommentStyleError(f"baseline already exists: {baseline}")
    violations = scan_comment_violations(root)
    _write_baseline(baseline, violations)
    if as_json:
        payload = {
            "ok": True,
            "initialized": True,
            "remaining": len(violations),
            "baseline": _display(baseline, root),
        }
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"comment-style: initialized {_display(baseline, root)} with {len(violations)} grandfathered violations")
    return 0


def _run_update(root: Path, baseline: Path, as_json: bool) -> int:
    entries = _load_baseline(baseline)
    violations = scan_comment_violations(root)
    current = {entry["fingerprint"]: entry["count"] for entry in _entries(violations)}
    growth = [
        (fingerprint, entries.get(fingerprint, {}).get("count", 0), current[fingerprint])
        for fingerprint in current
        if current[fingerprint] > entries.get(fingerprint, {}).get("count", 0)
    ]
    if growth:
        details = ", ".join(f"{fingerprint} ({old} -> {new})" for fingerprint, old, new in growth)
        raise CommentStyleError(f"baseline update refused (growth): {details}: {baseline}")
    _write_baseline(baseline, violations)
    if as_json:
        payload = {
            "ok": True,
            "updated": True,
            "remaining": len(violations),
            "baseline": _display(baseline, root),
        }
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"comment-style: updated {_display(baseline, root)}, {len(violations)} grandfathered violations remaining")
    return 0


def _failure_lines(result: dict) -> list[str]:
    lines: list[str] = []
    for violation in result["new"]:
        label = "comment block" if violation["kind"] == "comment" else "docstring"
        lines.append(
            f"new {label}: {violation['path']}:{violation['line']}-{violation['end_line']} "
            f"({violation['length']} lines)"
        )
    for entry in result["stale"]:
        where = ", ".join(entry["paths"])
        lines.append(
            f"stale baseline entry {entry['fingerprint']}: "
            f"expected {entry['expected']}, found {entry['found']} ({where})"
        )
    return lines


def _run_check(root: Path, baseline: Path, as_json: bool) -> int:
    result = check_comment_baseline(root, baseline)
    if as_json:
        print(json.dumps(result, sort_keys=True))
        return 0 if result["ok"] else 1
    if not result["ok"]:
        for line in _failure_lines(result):
            print(line, file=sys.stderr)
        print(
            f"comment-style: {len(result['new'])} new, {len(result['stale'])} stale",
            file=sys.stderr,
        )
        return 1
    print(f"comment-style: {result['remaining']} grandfathered violations remaining")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the comment-style gate; writer modes never grow the baseline."""
    parser = argparse.ArgumentParser(
        prog="python -m cairn.cli.system.comment_style",
        description="Enforce the shrink-only comment-style baseline over src/.",
    )
    parser.add_argument("--json", dest="as_json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--root", type=Path, default=None, help="repository root (default: discovered from cwd)")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="baseline path (default: <root>/docs/audits/comment-style-baseline.json)",
    )
    writers = parser.add_mutually_exclusive_group()
    writers.add_argument("--init", action="store_true", help="create the baseline; refuses an existing file")
    writers.add_argument("--update", action="store_true", help="rewrite the baseline after shrinkage; refuses growth")
    args = parser.parse_args(argv)

    root = (args.root or _find_root()).resolve()
    baseline = args.baseline or root.joinpath(*DEFAULT_BASELINE)
    try:
        if args.init:
            return _run_init(root, baseline, args.as_json)
        if args.update:
            return _run_update(root, baseline, args.as_json)
        return _run_check(root, baseline, args.as_json)
    except CommentStyleError as exc:
        if args.as_json:
            print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        else:
            print(f"comment-style: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
