"""Remaining-versus-total audit-finding counts from docs/audits."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_PRIORITIES = ("P0", "P1", "P2", "P3")
_INDEX_HEADING = re.compile(r"^## Findings index\b")
_PRIORITY_HEADING = re.compile(r"^### (P[0-3])\b.*\((\d+)\)\s*$")
_FINDING_ID = re.compile(r"^(?:C|Q|SY|S)\d+$")
_SEPARATOR_CELL = re.compile(r":?-+:?")


class AuditStatusError(Exception):
    """Malformed audit or sidecar data; the message names the offending file."""


def _read(path: Path, rel: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise AuditStatusError(f"{rel}: unreadable") from exc


def _parse_index(text: str, rel: str) -> dict[str, list[str]]:
    lines = text.splitlines()
    start = next((i + 1 for i, line in enumerate(lines) if _INDEX_HEADING.match(line)), None)
    if start is None:
        raise AuditStatusError(f"{rel}: no findings-index section")
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    findings: dict[str, list[str]] = {}
    declared: dict[str, int] = {}
    heading_counts: dict[str, int] = {}
    seen: set[str] = set()
    current: str | None = None
    for line in lines[start:end]:
        heading = _PRIORITY_HEADING.match(line)
        if heading:
            current = heading.group(1)
            declared[current] = int(heading.group(2))
            heading_counts[current] = heading_counts.get(current, 0) + 1
            findings[current] = []
        elif line.startswith("#"):
            current = None
        elif current is not None and line.startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if not cells or cells[0].lower() == "id":
                continue
            if all(_SEPARATOR_CELL.fullmatch(cell) for cell in cells if cell):
                continue
            finding_id = cells[0]
            if not _FINDING_ID.match(finding_id):
                raise AuditStatusError(
                    f"{rel}: invalid finding ID {finding_id!r} in {current} index"
                )
            if finding_id in seen:
                raise AuditStatusError(f"{rel}: duplicate finding ID {finding_id} in index")
            seen.add(finding_id)
            findings[current].append(finding_id)
    for priority in _PRIORITIES:
        count = heading_counts.get(priority, 0)
        if count != 1:
            raise AuditStatusError(
                f"{rel}: {priority} findings-index heading occurs {count} times"
            )
        rows = len(findings[priority])
        if rows != declared[priority]:
            raise AuditStatusError(
                f"{rel}: {priority} declares {declared[priority]} findings, index has {rows}"
            )
    return findings


def _load_sidecar(path: Path, rel: str, known_ids: set[str]) -> list[str]:
    try:
        data = json.loads(_read(path, rel))
    except json.JSONDecodeError as exc:
        raise AuditStatusError(f"{rel}: invalid JSON") from exc
    if not isinstance(data, dict):
        raise AuditStatusError(f"{rel}: top level must be a JSON object")
    if data.get("schema_version") != 1:
        raise AuditStatusError(f"{rel}: unsupported schema_version {data.get('schema_version')!r}")
    fixed = data.get("fixed")
    if not isinstance(fixed, list) or not all(isinstance(item, str) for item in fixed):
        raise AuditStatusError(f"{rel}: fixed must be a list of finding IDs")
    if len(set(fixed)) != len(fixed):
        raise AuditStatusError(f"{rel}: duplicate fixed finding ID")
    unknown = sorted(set(fixed) - known_ids)
    if unknown:
        raise AuditStatusError(f"{rel}: unknown fixed finding ID {unknown[0]}")
    return fixed


def collect_audit_status(root: Path) -> dict:
    """Return aggregated P0-P3 totals, fixed, and remaining counts under root."""
    audits_dir = Path(root) / "docs" / "audits"
    documents = {path.stem: path for path in sorted(audits_dir.glob("*.md"))}
    sidecars = {
        path.name.removesuffix(".status.json"): path
        for path in sorted(audits_dir.glob("*.status.json"))
    }
    if not documents and not sidecars:
        raise AuditStatusError("no audit documents under docs/audits")
    totals = {
        priority: {"total": 0, "fixed": 0, "remaining": 0} for priority in _PRIORITIES
    }
    audits = []
    for stem in sorted(documents.keys() | sidecars.keys()):
        audit_rel = f"docs/audits/{stem}.md"
        sidecar_rel = f"docs/audits/{stem}.status.json"
        if stem not in sidecars:
            raise AuditStatusError(f"missing status sidecar: {sidecar_rel}")
        if stem not in documents:
            raise AuditStatusError(f"missing audit document: {audit_rel}")
        findings = _parse_index(_read(documents[stem], audit_rel), audit_rel)
        known_ids = {
            finding_id: priority
            for priority, ids in findings.items()
            for finding_id in ids
        }
        for finding_id in _load_sidecar(sidecars[stem], sidecar_rel, set(known_ids)):
            totals[known_ids[finding_id]]["fixed"] += 1
        for priority, ids in findings.items():
            totals[priority]["total"] += len(ids)
        audits.append({"audit": audit_rel, "sidecar": sidecar_rel})
    for counts in totals.values():
        counts["remaining"] = counts["total"] - counts["fixed"]
    return {
        "audits": audits,
        "totals": totals,
        "total": sum(counts["total"] for counts in totals.values()),
        "remaining": sum(counts["remaining"] for counts in totals.values()),
    }


def main(argv: list[str] | None = None) -> int:
    """Run the audit-status command; return a process exit code."""
    parser = argparse.ArgumentParser(description="Report remaining audit findings by priority.")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    try:
        status = collect_audit_status(Path.cwd())
    except AuditStatusError as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"audit-status: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(status, indent=2))
    else:
        for priority in _PRIORITIES:
            counts = status["totals"][priority]
            print(f"{priority} {counts['remaining']}/{counts['total']} remaining")
        print(f"total {status['remaining']}/{status['total']} remaining")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
