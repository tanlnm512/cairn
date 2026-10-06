#!/usr/bin/env python3
"""Verify GitHub main protection across the legacy and rulesets APIs."""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any


MANDATORY_CHECKS = (
    "Security (pip-audit + bandit)",
    "Type check (mypy)",
    "PR title (conventional commits)",
    "pre-commit (all local gates)",
    "Quality ratchet (audit debt + comment style)",
    "Dependency review (PR)",
    "Test (Python 3.14)",
    "Closure budget gate (1000-file scaling point)",
    "Verify DS-v2 seal",
    "Container build (Dockerfile smoke)",
)

_REPO_SLUG = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_HTTP_STATUS = re.compile(r"HTTP (\d{3})")
_GH_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class _ApiResult:
    payload: Any = None
    error: dict[str, str] | None = None
    missing: bool = False


def _gh_json(endpoint: str, source: str, expected_type: type[Any]) -> _ApiResult:
    command = [
        "gh",
        "api",
        endpoint,
        "--method",
        "GET",
        "--header",
        "Accept: application/vnd.github+json",
    ]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=_GH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        return _ApiResult(
            error={
                "source": source,
                "endpoint": endpoint,
                "message": f"gh api timed out after {_GH_TIMEOUT_SECONDS}s: {exc.cmd[0]}",
            }
        )
    except OSError as exc:
        return _ApiResult(
            error={
                "source": source,
                "endpoint": endpoint,
                "message": f"could not execute gh: {exc}",
            }
        )

    if proc.returncode != 0:
        message = proc.stderr.strip() or f"gh exited with status {proc.returncode}"
        status = _HTTP_STATUS.search(message)
        if status and int(status.group(1)) == 404:
            return _ApiResult(missing=True)
        return _ApiResult(
            error={
                "source": source,
                "endpoint": endpoint,
                "message": message,
            }
        )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return _ApiResult(
            error={
                "source": source,
                "endpoint": endpoint,
                "message": f"gh returned invalid JSON: {exc}",
            }
        )
    if not isinstance(payload, expected_type):
        return _ApiResult(
            error={
                "source": source,
                "endpoint": endpoint,
                "message": f"gh returned {type(payload).__name__}, expected {expected_type.__name__}",
            }
        )
    return _ApiResult(payload=payload)


def _fetch_rulesets(repo: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    summaries: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    page = 1
    while True:
        endpoint = f"repos/{repo}/rulesets?per_page=100&page={page}"
        result = _gh_json(endpoint, "rulesets", list)
        if result.error:
            errors.append(result.error)
            return [], errors
        if result.missing:
            return [], []
        current = result.payload
        summaries.extend(item for item in current if isinstance(item, dict))
        if len(current) < 100:
            break
        page += 1

    detailed: list[dict[str, Any]] = []
    for summary in summaries:
        ruleset_id = summary.get("id")
        if not isinstance(ruleset_id, int):
            continue
        endpoint = f"repos/{repo}/rulesets/{ruleset_id}"
        result = _gh_json(endpoint, f"rulesets:{ruleset_id}", dict)
        if result.error:
            errors.append(result.error)
        elif not result.missing:
            detailed.append(result.payload)
    return detailed, errors


def _ref_matches(pattern: Any, branch: str) -> bool:
    if not isinstance(pattern, str):
        return False
    if pattern == "~DEFAULT_BRANCH":
        return branch == "main"
    candidate = pattern[len("refs/heads/") :] if pattern.startswith("refs/heads/") else pattern
    return fnmatch.fnmatchcase(branch, candidate)


def _ruleset_targets_branch(ruleset: dict[str, Any], branch: str) -> bool:
    if ruleset.get("target") != "branch":
        return False
    conditions = ruleset.get("conditions")
    if not isinstance(conditions, dict):
        return True
    ref_conditions = conditions.get("ref_name")
    if not isinstance(ref_conditions, dict):
        return True
    included = ref_conditions.get("include", [])
    excluded = ref_conditions.get("exclude", [])
    if isinstance(included, list) and included and not any(
        _ref_matches(pattern, branch) for pattern in included
    ):
        return False
    if isinstance(excluded, list) and any(_ref_matches(pattern, branch) for pattern in excluded):
        return False
    return True


def _review_count(payload: dict[str, Any]) -> int | None:
    reviews = payload.get("required_pull_request_reviews")
    if not isinstance(reviews, dict):
        return None
    count = reviews.get("required_approving_review_count")
    return count if isinstance(count, int) and not isinstance(count, bool) else None


def _legacy_force_denied(payload: dict[str, Any]) -> bool | None:
    force = payload.get("allow_force_pushes")
    if not isinstance(force, dict):
        return None
    enabled = force.get("enabled")
    return not enabled if isinstance(enabled, bool) else None


def _legacy_status_checks(payload: dict[str, Any]) -> tuple[str, ...] | None:
    required = payload.get("required_status_checks")
    if not isinstance(required, dict):
        return None
    names: set[str] = set()
    contexts = required.get("contexts", [])
    if isinstance(contexts, list):
        names.update(context for context in contexts if isinstance(context, str))
    checks = required.get("checks", [])
    if isinstance(checks, list):
        names.update(
            check["context"]
            for check in checks
            if isinstance(check, dict) and isinstance(check.get("context"), str)
        )
    return tuple(sorted(names))


def _rules_of_type(ruleset: dict[str, Any], rule_type: str) -> list[dict[str, Any]]:
    rules = ruleset.get("rules", [])
    if not isinstance(rules, list):
        return []
    return [
        rule
        for rule in rules
        if isinstance(rule, dict) and rule.get("type") == rule_type
    ]


def _ruleset_review_count(ruleset: dict[str, Any]) -> int | None:
    counts = []
    review_rules = [
        *_rules_of_type(ruleset, "pull_request"),
        *_rules_of_type(ruleset, "required_reviews"),
    ]
    for rule in review_rules:
        parameters = rule.get("parameters")
        if not isinstance(parameters, dict):
            continue
        count = parameters.get("required_approving_review_count")
        if isinstance(count, int) and not isinstance(count, bool):
            counts.append(count)
    return max(counts) if counts else None


def _ruleset_force_denied(ruleset: dict[str, Any]) -> bool | None:
    return True if _rules_of_type(ruleset, "non_fast_forward") else None


def _ruleset_status_checks(ruleset: dict[str, Any]) -> tuple[str, ...] | None:
    names: set[str] = set()
    found = False
    for rule in _rules_of_type(ruleset, "required_status_checks"):
        parameters = rule.get("parameters")
        if not isinstance(parameters, dict):
            continue
        required = parameters.get("required_status_checks", [])
        if not isinstance(required, list):
            continue
        found = True
        names.update(
            check["context"]
            for check in required
            if isinstance(check, dict) and isinstance(check.get("context"), str)
        )
    return tuple(sorted(names)) if found else None


def _error_copies(errors: list[dict[str, str]]) -> list[dict[str, str]]:
    return [dict(error) for error in errors]


def verify_protection(repo: str) -> dict[str, Any]:
    """Return the main-branch protection report for repo as JSON-compatible data."""
    if not _REPO_SLUG.fullmatch(repo):
        raise ValueError(f"invalid repository slug: {repo!r}")

    legacy_result = _gh_json(
        f"repos/{repo}/branches/main/protection", "legacy", dict
    )
    legacy = {} if legacy_result.payload is None else legacy_result.payload
    legacy_errors = [] if legacy_result.error is None else [legacy_result.error]
    rulesets, ruleset_errors = _fetch_rulesets(repo)
    active_rulesets = [
        ruleset
        for ruleset in rulesets
        if ruleset.get("enforcement") == "active"
        and _ruleset_targets_branch(ruleset, "main")
    ]
    source_errors = legacy_errors + ruleset_errors

    review_sources: list[dict[str, Any]] = []
    review_counts: list[int] = []
    legacy_reviews = _review_count(legacy)
    if legacy_reviews is not None:
        review_counts.append(legacy_reviews)
        review_sources.append({"source": "legacy", "required_reviews": legacy_reviews})
    for ruleset in active_rulesets:
        count = _ruleset_review_count(ruleset)
        if count is not None:
            review_counts.append(count)
            review_sources.append(
                {
                    "source": f"ruleset:{ruleset.get('id')}",
                    "name": ruleset.get("name"),
                    "required_reviews": count,
                }
            )
    observed_reviews = max(review_counts, default=0)

    force_sources: list[dict[str, Any]] = []
    legacy_force = _legacy_force_denied(legacy)
    if legacy_force:
        force_sources.append({"source": "legacy", "force_pushes_allowed": not legacy_force})
    for ruleset in active_rulesets:
        if _ruleset_force_denied(ruleset):
            force_sources.append(
                {
                    "source": f"ruleset:{ruleset.get('id')}",
                    "name": ruleset.get("name"),
                    "rule": "non_fast_forward",
                }
            )

    status_sources: list[dict[str, Any]] = []
    observed_status_checks: set[str] = set()
    legacy_checks = _legacy_status_checks(legacy)
    if legacy_checks is not None:
        observed_status_checks.update(legacy_checks)
        status_sources.append({"source": "legacy", "status_checks": legacy_checks})
    for ruleset in active_rulesets:
        checks = _ruleset_status_checks(ruleset)
        if checks is not None:
            observed_status_checks.update(checks)
            status_sources.append(
                {
                    "source": f"ruleset:{ruleset.get('id')}",
                    "name": ruleset.get("name"),
                    "status_checks": checks,
                }
            )
    missing_checks = sorted(set(MANDATORY_CHECKS) - observed_status_checks)

    checks = [
        {
            "name": "required_reviews",
            "status": "pass" if observed_reviews >= 1 else "fail",
            "observed": observed_reviews,
            "provenance": review_sources,
            "errors": _error_copies(source_errors),
        },
        {
            "name": "force_push_denial",
            "status": "pass" if force_sources else "fail",
            "observed": bool(force_sources),
            "provenance": force_sources,
            "errors": _error_copies(source_errors),
        },
        {
            "name": "required_status_checks",
            "status": "pass" if not missing_checks else "fail",
            "observed": sorted(observed_status_checks),
            "missing": missing_checks,
            "provenance": status_sources,
            "errors": _error_copies(source_errors),
        },
    ]
    return {
        "schema": "cairn-protection-verification/1",
        "repo": repo,
        "branch": "main",
        "ok": all(check["status"] == "pass" for check in checks) and not source_errors,
        "checks": checks,
        "source_errors": source_errors,
    }


def _source_names(provenance: list[dict[str, Any]]) -> str:
    return ", ".join(str(item["source"]) for item in provenance) or "no source"


def _print_human(report: dict[str, Any]) -> None:
    for error in report["source_errors"]:
        print(
            f"ERROR {error['source']} {error['endpoint']}: {error['message']}",
            file=sys.stderr,
        )
    for check in report["checks"]:
        if check["status"] == "pass":
            print(f"PASS {check['name']} ({_source_names(check['provenance'])})")
            continue
        details = (
            f"missing: {', '.join(check['missing'])}"
            if check["name"] == "required_status_checks"
            else f"observed: {check['observed']}"
        )
        print(
            f"FAIL {check['name']} ({details}; {_source_names(check['provenance'])})",
            file=sys.stderr,
        )
    if report["ok"]:
        print("protection verified")
    else:
        print("protection not verified", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="repository slug, owner/name")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    args = parser.parse_args(argv)

    try:
        report = verify_protection(args.repo)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_human(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
