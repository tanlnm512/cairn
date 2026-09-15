"""Diff-seeded reverse dependency radius."""

from __future__ import annotations

import re
import sqlite3
import subprocess
from pathlib import Path

from .traversal import impact_analysis
from .watcher import refresh_for_query


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class BlastBaseError(RuntimeError):
    """A requested base reference cannot be resolved."""


def _git(repo: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout


def _diff_path(value: str) -> str | None:
    if value == "/dev/null":
        return None
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    if value.startswith("a/") or value.startswith("b/"):
        value = value[2:]
    return value


def _parse_diff(text: str) -> list[dict]:
    files: list[dict] = []
    current: dict | None = None

    def finish() -> None:
        nonlocal current
        if current is not None:
            files.append(current)
            current = None

    for line in text.splitlines():
        if line.startswith("diff --git "):
            finish()
            current = {"path": None, "old_path": None, "hunks": []}
        elif current is None:
            continue
        elif line.startswith("--- "):
            current["old_path"] = _diff_path(line[4:])
        elif line.startswith("+++ "):
            current["path"] = _diff_path(line[4:])
        elif line.startswith("rename from "):
            current["old_path"] = _diff_path("b/" + line[12:])
        elif line.startswith("rename to "):
            current["path"] = _diff_path("b/" + line[10:])
        elif line.startswith("@@ "):
            match = _HUNK_RE.match(line)
            if match:
                start = int(match.group(1))
                count = int(match.group(2) or "1")
                current["hunks"].append(
                    {
                        "start": start,
                        "end": start + max(count - 1, 0),
                        "count": count,
                        "header": line,
                    }
                )
    finish()
    return files


def _repo_path(workspace: Path, stored_path: str) -> Path:
    path = Path(stored_path)
    return path if path.is_absolute() else workspace / path


def _resolve_base(repo: Path, base: str) -> str:
    verify = subprocess.run(
        [
            "git",
            "rev-parse",
            "--verify",
            "--quiet",
            f"{base}^{{commit}}",
        ],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=10,
    )
    if verify.returncode != 0:
        raise BlastBaseError(
            f"Unknown base ref '{base}'. Fetch the ref and full history "
            "(git fetch <remote> <ref>; in CI avoid shallow clones or use "
            "fetch-depth: 0)."
        )
    merge_base = _git(repo, ["merge-base", base, "HEAD"]).strip()
    if not merge_base:
        raise BlastBaseError(f"Base ref '{base}' has no common ancestor with HEAD.")
    return merge_base


def _changed_files(conn, workspace: Path, base: str | None) -> tuple[dict, list[dict]]:
    repos = list(
        conn.execute(
            "SELECT id, path FROM repos ORDER BY path, id"
        ).fetchall()
    )
    basis = {
        "kind": "worktree" if base is None else "merge-base",
        "base": base or "HEAD",
    }
    changed: list[dict] = []
    for repo in repos:
        repo_path = _repo_path(workspace, repo["path"])
        if not (repo_path / ".git").exists():
            continue
        if base is None:
            left = "HEAD"
        else:
            left = _resolve_base(repo_path, base)
            basis["merge_base"] = left
        args = (
            ["diff", "--unified=0", "HEAD"]
            if base is None
            else ["diff", "--unified=0", f"{left}..HEAD"]
        )
        for parsed in _parse_diff(_git(repo_path, args)):
            path = parsed["path"]
            if path is None:
                path = parsed["old_path"]
            if path is None:
                continue
            changed.append(
                {
                    "repo": repo["id"],
                    "path": path,
                    "old_path": parsed["old_path"],
                    "deleted": parsed["path"] is None,
                    "renamed": parsed["old_path"] is not None
                    and parsed["path"] is not None
                    and parsed["old_path"] != parsed["path"],
                    "hunks": parsed["hunks"],
                }
            )
    return basis, changed


def _seed_symbols(conn, changed: list[dict]) -> tuple[list[dict], list[str]]:
    seeds: dict[str, dict] = {}
    unindexed: list[str] = []
    for file_change in changed:
        rel = f"{file_change['repo']}:{file_change['path']}"
        if file_change["deleted"]:
            continue
        file_row = conn.execute(
            "SELECT id FROM files WHERE repo_id = ? AND path = ?",
            (file_change["repo"], file_change["path"]),
        ).fetchone()
        if file_row is None:
            unindexed.append(rel)
            continue
        for hunk in file_change["hunks"]:
            if hunk["count"] == 0:
                continue
            candidates = list(
                conn.execute(
                    "SELECT id, name, qualified_name, kind, line_start, line_end "
                    "FROM symbols WHERE file_id = ? "
                    "AND line_start IS NOT NULL AND line_end IS NOT NULL "
                    "AND line_start <= ? AND line_end >= ?",
                    (file_row["id"], hunk["end"], hunk["start"]),
                )
            )
            if not candidates:
                continue
            candidates.sort(
                key=lambda row: (
                    row["line_end"] - row["line_start"],
                    -row["line_start"],
                    row["name"],
                )
            )
            selected = candidates[0]
            seed = seeds.get(selected["id"])
            if seed is None:
                seed = {
                    "id": selected["id"],
                    "name": selected["name"],
                    "qualified_name": selected["qualified_name"],
                    "kind": selected["kind"],
                    "file_path": file_change["path"],
                    "repo": file_change["repo"],
                    "line_start": selected["line_start"],
                    "line_end": selected["line_end"],
                    "hunks": [],
                }
                seeds[selected["id"]] = seed
            seed["hunks"].append(
                {"start": hunk["start"], "end": hunk["end"]}
            )
    ordered = sorted(
        seeds.values(),
        key=lambda seed: (
            seed["repo"], seed["file_path"], seed["line_start"], seed["name"]
        ),
    )
    return ordered, sorted(set(unindexed))


def _radius(
    conn, seeds: list[dict], *, fuzzy: bool, limit: int
) -> tuple[list[dict], list[dict], bool]:
    radius: dict[tuple[str, str, str], dict] = {}
    cycles: dict[str, dict] = {}
    truncated = False
    for seed in seeds:
        result = impact_analysis(
            conn,
            seed["name"],
            max_depth=10,
            fuzzy=fuzzy,
            limit=limit,
            use_index=False,
            seed_id=seed["id"],
        )
        truncated = truncated or result["truncated"]
        for cycle in result["cycles"]:
            cycles.setdefault(cycle["symbol"], cycle)
        for row in result["impacted"]:
            key = (row["symbol"], row["file"], row["repo"])
            previous = radius.get(key)
            if previous is None or row["depth"] < previous["depth"]:
                radius[key] = dict(row)
    return sorted(
        radius.values(),
        key=lambda row: (row["depth"], row["repo"], row["file"], row["symbol"]),
    ), list(cycles.values()), truncated


def _annotate_radius(conn, seeds: list[dict], radius: list[dict]) -> None:
    seed_names = {seed["name"] for seed in seeds}
    by_depth = sorted(radius, key=lambda row: row["depth"])
    for row in radius:
        earlier = {
            other["symbol"]
            for other in by_depth
            if other["depth"] < row["depth"]
        }
        targets = seed_names | earlier
        matches = list(
            conn.execute(
                "SELECT e.target_id, COALESCE(t.name, e.target_name) AS target, "
                "e.resolution FROM edges e "
                "JOIN symbols source ON e.source_id = source.id "
                "JOIN files source_file ON source.file_id = source_file.id "
                "LEFT JOIN symbols t ON e.target_id = t.id "
                "WHERE source.name = ? AND source_file.path = ? "
                "AND source_file.repo_id = ?",
                (row["symbol"], row["file"], row["repo"]),
            )
        )
        applicable = [match for match in matches if match["target"] in targets]
        if applicable:
            applicable.sort(key=lambda match: match["resolution"] == "exact")
            row["depends_on"] = sorted(
                {match["target"] for match in applicable}
            )
            row["resolution"] = applicable[0]["resolution"] or "unresolved"
        else:
            row["depends_on"] = sorted(seed_names)
            row["resolution"] = "exact"


def _areas(seeds: list[dict], radius: list[dict]) -> list[dict]:
    names = {seed["repo"] for seed in seeds} | {row["repo"] for row in radius}
    return [
        {
            "name": name,
            "seeds": [seed for seed in seeds if seed["repo"] == name],
            "radius": [row for row in radius if row["repo"] == name],
        }
        for name in sorted(names)
    ]


def _render_text(result: dict) -> str:
    lines = ["Blast radius", f"Basis: {result['basis']['kind']} ({result['basis']['base']})"]
    for area in result["areas"]:
        lines.extend(["", f"Area: {area['name']}", "Changed symbols:"])
        if area["seeds"]:
            for seed in area["seeds"]:
                lines.append(
                    f"  {seed['name']} "
                    f"({seed['file_path']}:{seed['line_start']}-{seed['line_end']})"
                )
        else:
            lines.append("  (none)")
        lines.append("Dependents:")
        if area["radius"]:
            for row in area["radius"]:
                lines.append(
                    f"  {row['symbol']} (depth {row['depth']}) "
                    f"{row['file']} [{row['resolution']}]"
                )
        else:
            lines.append("  No dependents")
    return "\n".join(lines) + "\n"


def _render_markdown(result: dict) -> str:
    lines = ["# Blast radius", ""]
    for area in result["areas"]:
        lines.extend([f"## Area: {area['name']}", "", "### Changed symbols"])
        if area["seeds"]:
            for seed in area["seeds"]:
                lines.append(f"- `{seed['name']}` (`{seed['file_path']}`)")
        else:
            lines.append("- none")
        lines.extend(["", "### Dependents"])
        if area["radius"]:
            for row in area["radius"]:
                lines.append(
                    f"- `{row['symbol']}` — depth {row['depth']} "
                    f"(`{row['file']}`, {row['resolution']})"
                )
        else:
            lines.append("- No dependents")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _mermaid_id(name: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in name)[:80]


def _render_mermaid(result: dict) -> str:
    lines = ["flowchart TD"]
    if not result["radius"]:
        lines.append("    %% No dependents")
        return "\n".join(lines) + "\n"
    nodes = {}
    for seed in result["seeds"]:
        nodes[seed["name"]] = _mermaid_id(seed["name"])
    for row in result["radius"]:
        nodes.setdefault(row["symbol"], _mermaid_id(row["symbol"]))
    for name, node_id in nodes.items():
        lines.append(f'    {node_id}["{name}"]')
    for row in result["radius"]:
        source = nodes.get(row.get("depends_on", [None])[0], row.get("depends_on", [None])[0])
        target = nodes[row["symbol"]]
        if source:
            lines.append(f"    {source} --> {target}")
    return "\n".join(lines) + "\n"


def render_blast(result: dict, output_format: str) -> str:
    """Render a computed blast result without another graph traversal."""
    if output_format == "text":
        return _render_text(result)
    if output_format == "markdown":
        return _render_markdown(result)
    if output_format == "mermaid":
        return _render_mermaid(result)
    raise ValueError(f"unsupported blast format: {output_format}")


def compute_blast(
    conn: sqlite3.Connection,
    workspace: str,
    *,
    base: str | None = None,
    fuzzy: bool = False,
    limit: int = 500,
    refresh: bool | None = None,
) -> dict:
    """Refresh stored spans and return the reverse radius of a git diff."""
    refresh_for_query(conn, repair=refresh)
    workspace_path = Path(workspace).resolve()
    basis, changed = _changed_files(conn, workspace_path, base)
    seeds, unindexed = _seed_symbols(conn, changed)
    radius, cycles, truncated = _radius(
        conn, seeds, fuzzy=fuzzy, limit=limit
    )
    _annotate_radius(conn, seeds, radius)
    deleted = [
        f"{item['repo']}:{item['path']}"
        for item in changed
        if item["deleted"]
    ]
    return {
        "basis": basis,
        "changed_files": changed,
        "seeds": seeds,
        "areas": _areas(seeds, radius),
        "radius": radius,
        "cycles": cycles,
        "unindexed_files": unindexed,
        "deleted_files": sorted(set(deleted)),
        "truncated": truncated,
    }
