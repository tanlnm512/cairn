"""Diff-seeded reverse dependency radius."""

from __future__ import annotations

import re
import sqlite3
import subprocess
from pathlib import Path

from .config import load_config
from .taint import build_registry, intersect_seeds
from .traversal import impact_analysis
from .watcher import refresh_for_query


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

# git quotes paths containing " or control characters even with
# core.quotePath=false; the quoted form C-escapes octal bytes and the
# characters below.
_C_ESCAPE_RE = re.compile(r"\\(?:(?P<octal>[0-7]{3})|(?P<char>.))")
_C_SIMPLE_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "v": "\v",
}


class BlastBaseError(RuntimeError):
    """A requested base reference cannot be resolved."""


def _git(repo: Path, args: list[str]) -> str:
    # Diff syntax tolerates replaced payload bytes; paths stay byte-compatible.
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        errors="replace",
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout


def _unquote_git_path(value: str) -> str:
    """Decode git's C-style quoted path; octal escapes decode as UTF-8 bytes."""
    if len(value) < 2 or not (value.startswith('"') and value.endswith('"')):
        return value
    body = value[1:-1]
    raw = bytearray()
    pos = 0
    for match in _C_ESCAPE_RE.finditer(body):
        raw.extend(body[pos : match.start()].encode("utf-8"))
        octal, char = match.group("octal"), match.group("char")
        if octal is not None:
            raw.append(int(octal, 8))
        else:
            raw.extend(_C_SIMPLE_ESCAPES.get(char, char).encode("utf-8"))
        pos = match.end()
    raw.extend(body[pos:].encode("utf-8"))
    return raw.decode("utf-8", errors="replace")


def _diff_path(value: str) -> str | None:
    if value == "/dev/null":
        return None
    value = _unquote_git_path(value)
    if value.startswith("a/") or value.startswith("b/"):
        value = value[2:]
    return value


def _parse_diff(text: str) -> list[dict]:
    files: list[dict] = []
    current: dict | None = None
    in_hunk = False

    def finish() -> None:
        nonlocal current
        if current is not None:
            files.append(current)
            current = None

    for line in text.splitlines():
        if line.startswith("diff --git "):
            finish()
            current = {"path": None, "old_path": None, "hunks": []}
            in_hunk = False
        elif current is None:
            continue
        elif in_hunk and line[:1] in (" ", "+", "-", "\\"):
            # Hunk body: content lines like "--- x"/"+++ x" are not headers.
            continue
        elif line.startswith("--- "):
            current["old_path"] = _diff_path(line[4:])
        elif line.startswith("+++ "):
            current["path"] = _diff_path(line[4:])
        elif line.startswith("rename from "):
            current["old_path"] = _diff_path("b/" + _unquote_git_path(line[12:]))
        elif line.startswith("rename to "):
            current["path"] = _diff_path("b/" + _unquote_git_path(line[10:]))
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
                in_hunk = True
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


def _changed_entry(repo_id: str, parsed: dict) -> dict | None:
    path = parsed["path"]
    if path is None:
        path = parsed["old_path"]
    if path is None:
        return None
    return {
        "repo": repo_id,
        "path": path,
        "old_path": parsed["old_path"],
        "deleted": parsed["path"] is None,
        "renamed": parsed["old_path"] is not None
        and parsed["path"] is not None
        and parsed["old_path"] != parsed["path"],
        "hunks": parsed["hunks"],
    }


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
            entry = _changed_entry(repo["id"], parsed)
            if entry is not None:
                changed.append(entry)
    return basis, changed


def _seed_record(row, file_change: dict) -> dict:
    """Return a blast seed record for an indexed symbol row."""
    return {
        "id": row["id"],
        "name": row["name"],
        "qualified_name": row["qualified_name"],
        "kind": row["kind"],
        "file_path": file_change["path"],
        "repo": file_change["repo"],
        "line_start": row["line_start"],
        "line_end": row["line_end"],
        "hunks": [],
    }


def _seed_symbols(conn, changed: list[dict]) -> tuple[list[dict], list[str]]:
    seeds: dict[str, dict] = {}
    unindexed: list[str] = []
    for file_change in changed:
        rel = f"{file_change['repo']}:{file_change['path']}"
        file_row = conn.execute(
            "SELECT id FROM files WHERE repo_id = ? AND path = ?",
            (file_change["repo"], file_change["path"]),
        ).fetchone()
        if file_row is None:
            unindexed.append(rel)
            continue
        if file_change["deleted"]:
            removed = conn.execute(
                """SELECT id, name, qualified_name, kind, line_start, line_end
                   FROM symbols WHERE file_id = ?
                   AND kind != 'module'
                   AND line_start IS NOT NULL AND line_end IS NOT NULL
                   ORDER BY line_start, name""",
                (file_row["id"],),
            ).fetchall()
            for selected in removed:
                seed = seeds.setdefault(
                    selected["id"], _seed_record(selected, file_change)
                )
                seed["hunks"].append(
                    {"start": selected["line_start"], "end": selected["line_end"]}
                )
            continue
        for hunk in file_change["hunks"]:
            # count == 0 is a pure deletion: seed the boundary line so the
            # symbol that lost content still reaches its dependents.
            candidates = list(
                conn.execute(
                    "SELECT id, name, qualified_name, kind, line_start, line_end "
                    "FROM symbols WHERE file_id = ? "
                    "AND kind != 'module' "
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
                seed = _seed_record(selected, file_change)
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
    seed_ids = {seed["id"] for seed in seeds}
    seed_names = {seed["name"] for seed in seeds}
    by_depth = sorted(radius, key=lambda row: row["depth"])
    for row in radius:
        earlier_names = {
            other["symbol"]
            for other in by_depth
            if other["depth"] < row["depth"]
        }
        row_earlier_keys = {
            (other["symbol"], other["file"], other["repo"])
            for other in by_depth
            if other["depth"] < row["depth"]
        }
        matches = conn.execute(
            "SELECT source.id AS source_id, e.target_id, "
            "COALESCE(t.name, e.target_name) AS target, "
            "target_file.path AS target_file, "
            "target_file.repo_id AS target_repo, e.resolution "
            "FROM edges e "
            "JOIN symbols source ON e.source_id = source.id "
            "JOIN files source_file ON source.file_id = source_file.id "
            "LEFT JOIN symbols t ON e.target_id = t.id "
            "LEFT JOIN files target_file ON t.file_id = target_file.id "
            "WHERE source.name = ? AND source_file.path = ? "
            "AND source_file.repo_id = ?",
            (row["symbol"], row["file"], row["repo"]),
        ).fetchall()

        def target_identity(match) -> tuple[str, str | None, str | None]:
            return (match["target"], match["target_file"], match["target_repo"])

        def is_applicable(match) -> bool:
            if match["target_id"] is None:
                return match["target"] in seed_names | earlier_names
            return match["target_id"] in seed_ids or target_identity(
                match
            ) in row_earlier_keys

        applicable = [match for match in matches if is_applicable(match)]
        row["symbol_id"] = matches[0]["source_id"] if matches else None
        if applicable:
            applicable.sort(key=lambda match: match["resolution"] != "exact")
            row["depends_on"] = sorted(
                {match["target"] for match in applicable}
            )
            row["depends_on_ids"] = sorted(
                {
                    match["target_id"]
                    for match in applicable
                    if match["target_id"] is not None
                    and (
                        match["target_id"] in seed_ids
                        or target_identity(match) in row_earlier_keys
                    )
                }
            )
            row["resolution"] = applicable[0]["resolution"] or "unresolved"
        else:
            row["depends_on"] = sorted(seed_names)
            row["depends_on_ids"] = sorted(seed_ids)
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
    if not result["areas"]:
        lines.extend(["", "Dependents:", "  No dependents"])
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
    if not result["areas"]:
        lines.extend(["## Dependents", "", "- No dependents", ""])
    return "\n".join(lines).rstrip() + "\n"


def _mermaid_esc(text: str) -> str:
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("|", "\\|")
        .replace("\n", " ")
    )


def _mermaid_id(name: str, used: set[str]) -> str:
    base = "".join(char if char.isalnum() else "_" for char in name)[:80]
    candidate = base or "node"
    suffix = 2
    while candidate in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _render_mermaid(result: dict) -> str:
    lines = ["flowchart TD"]
    if not result["radius"]:
        lines.append("    %% No dependents")
        return "\n".join(lines) + "\n"
    node_ids: dict[str, str] = {}
    nodes_by_name: dict[str, list[str]] = {}
    nodes_by_symbol_id: dict[str, str] = {}
    used_ids: set[str] = set()

    def add_node(
        key: str, symbol: str, label: str, symbol_id: str | None
    ) -> None:
        node_id = _mermaid_id(symbol, used_ids)
        node_ids[key] = node_id
        nodes_by_name.setdefault(symbol, []).append(node_id)
        if symbol_id is not None:
            nodes_by_symbol_id[symbol_id] = node_id
        lines.append(f'    {node_id}["{_mermaid_esc(label)}"]')

    for seed in result["seeds"]:
        add_node(
            f"seed:{seed['id']}",
            seed["name"],
            f"{seed['name']} ({seed['file_path']})",
            seed["id"],
        )
    for row in result["radius"]:
        key = row.get("symbol_id") or (
            f"radius:{row['repo']}:{row['file']}:{row['symbol']}"
        )
        if key not in node_ids:
            add_node(
                key,
                row["symbol"],
                f"{row['symbol']} ({row['file']})",
                row.get("symbol_id"),
            )
    for row in result["radius"]:
        target_key = row.get("symbol_id") or (
            f"radius:{row['repo']}:{row['file']}:{row['symbol']}"
        )
        target = node_ids[target_key]
        sources: set[str] = set()
        for symbol_id in row.get("depends_on_ids", []):
            source = nodes_by_symbol_id.get(symbol_id)
            if source is not None:
                sources.add(source)
        if not sources:
            for dependency in row.get("depends_on", []):
                sources.update(nodes_by_name.get(dependency, []))
        for source in sorted(sources):
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
    workspace_path = Path(workspace).resolve()
    basis, changed = _changed_files(conn, workspace_path, base)

    # Capture deleted symbols before refresh removes their file rows.
    deleted_changed = [item for item in changed if item["deleted"]]
    deleted_seeds, deleted_unindexed = _seed_symbols(conn, deleted_changed)
    deleted_radius, deleted_cycles, deleted_truncated = _radius(
        conn, deleted_seeds, fuzzy=fuzzy, limit=limit
    )

    refresh_for_query(conn, repair=refresh)
    live_changed = [item for item in changed if not item["deleted"]]
    live_seeds, live_unindexed = _seed_symbols(conn, live_changed)
    live_radius, live_cycles, live_truncated = _radius(
        conn, live_seeds, fuzzy=fuzzy, limit=limit
    )

    seeds = sorted(
        deleted_seeds + live_seeds,
        key=lambda seed: (
            seed["repo"], seed["file_path"], seed["line_start"], seed["name"]
        ),
    )
    unindexed = sorted(set(deleted_unindexed + live_unindexed))
    radius_by_key: dict[tuple[str, str, str], dict] = {}
    for row in deleted_radius + live_radius:
        key = (row["symbol"], row["file"], row["repo"])
        previous = radius_by_key.get(key)
        if previous is None or row["depth"] < previous["depth"]:
            radius_by_key[key] = dict(row)
    radius = sorted(
        radius_by_key.values(),
        key=lambda row: (row["depth"], row["repo"], row["file"], row["symbol"]),
    )
    cycles = {
        cycle["symbol"]: cycle for cycle in deleted_cycles + live_cycles
    }
    truncated = deleted_truncated or live_truncated
    _annotate_radius(conn, seeds, radius)
    config = load_config(workspace_path)
    taint_paths = intersect_seeds(
        conn,
        build_registry(config.taint_sources, config.taint_sinks),
        {seed["name"] for seed in seeds},
        fuzzy=fuzzy,
    )
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
        "taint_paths": taint_paths,
    }


def _pr_repo_id(conn, workspace_path: Path) -> str:
    repos = list(
        conn.execute("SELECT id, path FROM repos ORDER BY path, id").fetchall()
    )
    matches = [
        row["id"]
        for row in repos
        if _repo_path(workspace_path, row["path"]).resolve() == workspace_path
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise BlastBaseError(
            f"Workspace '{workspace_path}' matches multiple registered "
            "repositories; run from a single-repo workspace root."
        )
    if len(repos) == 1:
        return repos[0]["id"]
    raise BlastBaseError(
        f"Workspace '{workspace_path}' matches no repository registered in "
        "the store; run cairn update in the workspace first."
    )


def _pr_changed_files(conn, workspace_path: Path, diff_text: str) -> list[dict]:
    repo_id = _pr_repo_id(conn, workspace_path)
    changed: list[dict] = []
    for parsed in _parse_diff(diff_text):
        entry = _changed_entry(repo_id, parsed)
        if entry is not None:
            changed.append(entry)
    return changed


def pr_seed_symbols(
    conn: sqlite3.Connection, workspace: str, diff_text: str
) -> tuple[list[dict], list[str]]:
    """Resolve a PR diff's changed files to seed symbols and unindexed paths."""
    changed = _pr_changed_files(conn, Path(workspace).resolve(), diff_text)
    return _seed_symbols(conn, changed)


def compute_pr_impact(
    conn: sqlite3.Connection,
    workspace: str,
    *,
    diff_text: str,
    base_ref: str,
    fuzzy: bool = False,
    limit: int = 500,
) -> dict:
    """Return the reverse radius of a PR diff against the store as-is (no refresh)."""
    workspace_path = Path(workspace).resolve()
    changed = _pr_changed_files(conn, workspace_path, diff_text)
    seeds, unindexed = _seed_symbols(conn, changed)
    radius, cycles, truncated = _radius(conn, seeds, fuzzy=fuzzy, limit=limit)
    _annotate_radius(conn, seeds, radius)
    config = load_config(workspace_path)
    taint_paths = intersect_seeds(
        conn,
        build_registry(config.taint_sources, config.taint_sinks),
        {seed["name"] for seed in seeds},
        fuzzy=fuzzy,
    )
    deleted = [
        f"{item['repo']}:{item['path']}" for item in changed if item["deleted"]
    ]
    return {
        "basis": {"kind": "pr", "base": base_ref},
        "changed_files": changed,
        "seeds": seeds,
        "areas": _areas(seeds, radius),
        "radius": radius,
        "cycles": cycles,
        "unindexed_files": unindexed,
        "deleted_files": sorted(set(deleted)),
        "truncated": truncated,
        "taint_paths": taint_paths,
    }
