"""Coverage gap detection: find modules without compass files."""
from __future__ import annotations

import sqlite3
from typing import List

from ..okf.bundle import OKFBundle


def detect_gaps(conn: sqlite3.Connection, bundle: OKFBundle) -> List[str]:
    """Find top-level modules (per repo) that lack a compass file.

    A module is a top-level package/directory grouping. We derive modules from
    the distinct top-level source directories of each repo, then check whether a
    compass concept covers it (by matching its `resource` field).
    """
    all_modules = _get_all_modules(conn)
    covered = set()
    for cid in bundle.list_concepts(prefix="compass/"):
        try:
            concept = bundle.read_concept(cid)
            if concept.resource:
                covered.add(concept.resource)
        except Exception:
            continue
    gaps = []
    for mod in all_modules:
        # A module is covered if any compass resource is a prefix of it or vice-versa.
        if not any(mod.startswith(c) or c.startswith(mod) for c in covered):
            gaps.append(mod)
    return gaps


def _get_all_modules(conn: sqlite3.Connection) -> List[str]:
    """Derive candidate modules: top-level package dirs per repo."""
    cur = conn.cursor()
    modules = []
    repos = cur.execute("SELECT id, path FROM repos").fetchall()
    for repo in repos:
        # Sample files and group by their top-level source dir.
        files = cur.execute(
            "SELECT path FROM files WHERE repo_id = ?", (repo["id"],)
        ).fetchall()
        dirs = set()
        for f in files:
            rel = f["path"].replace(repo["path"] + "/", "", 1)
            parts = rel.split("/")
            # Heuristic: take first 3 segments as a module identifier.
            if len(parts) >= 3:
                dirs.add(f"{repo['id']}/" + "/".join(parts[:3]))
        # Only keep dirs that have a reasonable number of symbols (real modules).
        for d in sorted(dirs):
            n = cur.execute(
                "SELECT COUNT(*) AS c FROM symbols s JOIN files f ON s.file_id=f.id "
                "WHERE f.path LIKE ?",
                (f"%{d.split('/', 1)[1]}%",),
            ).fetchone()["c"]
            if n >= 5:
                modules.append(d)
    return modules
