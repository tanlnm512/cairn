"""Read-only GitHub PR state over the gh CLI."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

_GH_TIMEOUT = 30
_GH_VERBS = {"list", "view", "diff"}
_PR_LIST_FIELDS = "number,title,headRefName,author,state,statusCheckRollup,reviewDecision"
_COMMUNITIES_HINT = "community tables absent — run cairn communities"

_FAIL_CHECK_STATES = {
    "FAILURE",
    "TIMED_OUT",
    "CANCELLED",
    "ACTION_REQUIRED",
    "ERROR",
    "STARTUP_FAILURE",
}
_PASS_CHECK_STATES = {"SUCCESS", "NEUTRAL", "SKIPPED"}
_PENDING_CHECK_STATES = {
    "PENDING",
    "QUEUED",
    "IN_PROGRESS",
    "STARTING",
    "WAITING",
    "EXPECTED",
    "REQUESTED",
    "STALE",
}


class GhError(RuntimeError):
    """A gh invocation failed; the message carries gh's stderr or guidance."""


@dataclass(frozen=True)
class PrRecord:
    """One open PR's triage fields."""

    number: int
    title: str
    head_ref: str
    author: str
    ci_state: str
    review_decision: str


@dataclass(frozen=True)
class PrView:
    """The PR fields impact needs; number/head_ref tolerate minimal output."""

    number: int | None
    head_ref: str
    base_ref: str


def _gh(repo: Path, args: list[str]) -> str:
    """Run gh in repo with a pinned read-only argv; return stdout text."""
    if args != ["--version"] and not (
        len(args) >= 2 and args[0] == "pr" and args[1] in _GH_VERBS
    ):
        raise GhError(f"refusing non-read-only gh invocation: {' '.join(args)}")
    try:
        # errors="replace": output is rendered as text only, bad bytes are inert.
        result = subprocess.run(
            ["gh", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_GH_TIMEOUT,
        )
    except FileNotFoundError:
        raise GhError(
            "gh CLI not found — install gh, then run `gh auth login`"
        ) from None
    except subprocess.TimeoutExpired:
        raise GhError(
            f"gh {' '.join(args)} timed out after {_GH_TIMEOUT}s"
        ) from None
    if result.returncode != 0:
        raise GhError(
            result.stderr.strip() or f"gh {' '.join(args)} exited {result.returncode}"
        )
    return result.stdout


def _gh_version(repo: Path) -> str:
    """First line of `gh --version`; never raises."""
    try:
        return (_gh(repo, ["--version"]).splitlines() or ["unknown gh version"])[0].strip()
    except GhError:
        return "unknown gh version"


def _field(record: dict, key: str, types: type | tuple[type, ...], repo: Path):
    """Return record[key] typed as types; fail closed naming the gh version."""
    value = record.get(key)
    if type(value) is bool or not isinstance(value, types):
        raise GhError(
            f"gh output field '{key}' is missing or mistyped ({_gh_version(repo)})"
        )
    return value


def _author(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("login"), str):
        return value["login"]
    return ""


def _classify_ci(rollup: list) -> str:
    verdict = "none"
    trusted = True
    for item in rollup:
        state = ""
        if isinstance(item, dict):
            raw = item.get("conclusion") or item.get("state")
            if isinstance(raw, str):
                state = raw.upper()
        if state in _FAIL_CHECK_STATES:
            return "fail"
        if state in _PENDING_CHECK_STATES:
            verdict = "pending"
        elif state in _PASS_CHECK_STATES:
            if verdict == "none":
                verdict = "pass"
        else:
            trusted = False
    if verdict == "pass" and not trusted:
        return "pending"
    return verdict


def _parse_record(item: dict, repo: Path) -> PrRecord:
    number = _field(item, "number", int, repo)
    title = _field(item, "title", str, repo)
    head_ref = _field(item, "headRefName", str, repo)
    rollup = _field(item, "statusCheckRollup", list, repo)
    decision = item.get("reviewDecision")
    return PrRecord(
        number=number,
        title=title,
        head_ref=head_ref,
        author=_author(item.get("author")),
        ci_state=_classify_ci(rollup),
        review_decision=decision if isinstance(decision, str) else "",
    )


def parse_pr_list(text: str, repo: Path) -> list[PrRecord]:
    """Parse `gh pr list` JSON into records; unknown fields ignored."""
    try:
        items = json.loads(text)
    except json.JSONDecodeError:
        raise GhError(f"gh pr list output is not JSON ({_gh_version(repo)})") from None
    if not isinstance(items, list):
        raise GhError(f"gh pr list output is not a JSON array ({_gh_version(repo)})")
    records = []
    for item in items:
        if not isinstance(item, dict):
            raise GhError(
                f"gh pr list entry is not a JSON object ({_gh_version(repo)})"
            )
        records.append(_parse_record(item, repo))
    return records


def parse_pr_view(text: str, repo: Path) -> PrView:
    """Parse `gh pr view` JSON; baseRefName is required, the rest tolerated."""
    try:
        record = json.loads(text)
    except json.JSONDecodeError:
        raise GhError(f"gh pr view output is not JSON ({_gh_version(repo)})") from None
    if not isinstance(record, dict):
        raise GhError(f"gh pr view output is not a JSON object ({_gh_version(repo)})")
    number = record.get("number")
    head_ref = record.get("headRefName")
    return PrView(
        number=number if type(number) is int else None,
        head_ref=head_ref if isinstance(head_ref, str) else "",
        base_ref=_field(record, "baseRefName", str, repo),
    )


def list_open_prs(repo: Path) -> list[PrRecord]:
    """Open PRs over the workspace remote, parsed defensively."""
    stdout = _gh(repo, ["pr", "list", "--json", _PR_LIST_FIELDS])
    return parse_pr_list(stdout, repo)


def community_overlaps(conn, per_pr_seeds: dict[int, list[str]]) -> dict:
    """Rank PR pairs by shared community labels of seed symbol ids; graceful-absent payload when the tables are missing or empty."""
    try:
        labels = {
            row[0]: row[1] for row in conn.execute("SELECT id, label FROM communities")
        }
        membership: dict[str, set[int]] = {}
        for symbol_id, community_id in conn.execute(
            "SELECT symbol_id, community_id FROM symbol_communities"
        ):
            membership.setdefault(symbol_id, set()).add(community_id)
    except sqlite3.Error:
        return {"pairs": [], "hint": _COMMUNITIES_HINT}
    if not labels or not membership:
        return {"pairs": [], "hint": _COMMUNITIES_HINT}
    by_pr = {
        number: {
            community
            for seed_id in seeds
            for community in membership.get(seed_id, ())
        }
        for number, seeds in per_pr_seeds.items()
    }
    numbers = sorted(by_pr)
    pairs = []
    for i, a in enumerate(numbers):
        for b in numbers[i + 1 :]:
            shared = by_pr[a] & by_pr[b]
            if shared:
                pairs.append(
                    {
                        "a": a,
                        "b": b,
                        "shared": len(shared),
                        "communities": sorted(labels[c] for c in shared),
                    }
                )
    pairs.sort(key=lambda pair: (-pair["shared"], pair["a"], pair["b"]))
    return {"pairs": pairs, "hint": None}
