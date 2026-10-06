"""Tests for scripts/verify_protection.py."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "verify_protection.py"

_spec = importlib.util.spec_from_file_location("verify_protection", SCRIPT)
vp = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("verify_protection", vp)
_spec.loader.exec_module(vp)


def _legacy_payload(*, reviews=True, force_push=False, checks=()):
    payload = {
        "required_status_checks": {
            "checks": [{"context": name} for name in checks],
            "contexts": [],
        },
        "allow_force_pushes": {"enabled": force_push},
    }
    if reviews:
        payload["required_pull_request_reviews"] = {
            "required_approving_review_count": 1
        }
    return payload


def _ruleset(
    ruleset_id,
    *,
    name="main",
    branch="refs/heads/main",
    enforcement="active",
    reviews=False,
    non_fast_forward=False,
    checks=(),
):
    rules = []
    if reviews:
        rules.append(
            {
                "type": "pull_request",
                "parameters": {"required_approving_review_count": 1},
            }
        )
    if non_fast_forward:
        rules.append({"type": "non_fast_forward"})
    if checks:
        rules.append(
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [{"context": value} for value in checks]
                },
            }
        )
    return {
        "id": ruleset_id,
        "name": name,
        "target": "branch",
        "enforcement": enforcement,
        "conditions": {"ref_name": {"include": [branch], "exclude": []}},
        "rules": rules,
    }


def _install_gh(monkeypatch, *, legacy, rulesets, legacy_error=None):
    calls = []
    ruleset_summaries = [
        {key: value for key, value in ruleset.items() if key != "rules"}
        for ruleset in rulesets
    ]

    def run(command, **_kwargs):
        calls.append(list(command))
        endpoint = command[2]
        if endpoint == "repos/example/repo/branches/main/protection":
            if legacy_error is not None:
                returncode, stdout, stderr = legacy_error
            else:
                returncode, stdout, stderr = 0, json.dumps(legacy), ""
        elif endpoint == "repos/example/repo/rulesets?per_page=100&page=1":
            returncode, stdout, stderr = 0, json.dumps(ruleset_summaries), ""
        elif endpoint.startswith("repos/example/repo/rulesets/"):
            ruleset_id = int(endpoint.rsplit("/", 1)[1])
            matching = [item for item in rulesets if item["id"] == ruleset_id]
            returncode, stdout, stderr = 0, json.dumps(matching[0]), ""
        else:
            returncode, stdout, stderr = 1, "", f"unexpected endpoint: {endpoint}"
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    monkeypatch.setattr(vp, "subprocess", SimpleNamespace(run=run))
    return calls


def test_union_semantics_and_per_check_provenance(monkeypatch):
    monkeypatch.setattr(vp, "MANDATORY_CHECKS", ("legacy-check", "ruleset-check"))
    calls = _install_gh(
        monkeypatch,
        legacy=_legacy_payload(reviews=False, checks=("legacy-check", "other-check")),
        rulesets=[
            _ruleset(7, reviews=True, checks=("ruleset-check",))
        ],
    )

    report = vp.verify_protection("example/repo")

    assert report["ok"] is True
    assert [check["name"] for check in report["checks"]] == [
        "required_reviews",
        "force_push_denial",
        "required_status_checks",
    ]
    assert all(check["status"] == "pass" for check in report["checks"])
    reviews = report["checks"][0]
    assert reviews["observed"] == 1
    assert [item["source"] for item in reviews["provenance"]] == ["ruleset:7"]
    force = report["checks"][1]
    assert [item["source"] for item in force["provenance"]] == ["legacy"]
    statuses = report["checks"][2]
    assert statuses["observed"] == ["legacy-check", "other-check", "ruleset-check"]
    assert statuses["missing"] == []
    assert {(item["source"], tuple(item["status_checks"])) for item in statuses["provenance"]} == {
        ("legacy", ("legacy-check", "other-check")),
        ("ruleset:7", ("ruleset-check",)),
    }
    assert calls[0][:2] == ["gh", "api"]
    assert all("--method" in call and "GET" in call for call in calls)
    assert [call[2] for call in calls] == [
        "repos/example/repo/branches/main/protection",
        "repos/example/repo/rulesets?per_page=100&page=1",
        "repos/example/repo/rulesets/7",
    ]


@pytest.mark.parametrize("failed_check", ["required_reviews", "force_push_denial", "required_status_checks"])
def test_drift_names_check_and_missing_value(monkeypatch, failed_check):
    monkeypatch.setattr(vp, "MANDATORY_CHECKS", ("required-check",))
    base_legacy = {"reviews": True, "force_push": False, "checks": ("required-check",)}
    base_ruleset = {"reviews": True, "non_fast_forward": True, "checks": ("required-check",)}
    if failed_check == "required_reviews":
        base_legacy["reviews"] = False
        base_ruleset["reviews"] = False
    elif failed_check == "force_push_denial":
        base_legacy["force_push"] = True
        base_ruleset["non_fast_forward"] = False
    else:
        base_legacy["checks"] = ()
        base_ruleset["checks"] = ()
    _install_gh(
        monkeypatch,
        legacy=_legacy_payload(**base_legacy),
        rulesets=[_ruleset(7, **base_ruleset)],
    )

    report = vp.verify_protection("example/repo")

    assert report["ok"] is False
    failed = {check["name"]: check for check in report["checks"] if check["status"] == "fail"}
    assert set(failed) == {failed_check}


def test_only_active_rulesets_targeting_main_supply_evidence(monkeypatch):
    _install_gh(
        monkeypatch,
        legacy=_legacy_payload(reviews=False, force_push=True),
        rulesets=[
            _ruleset(1, name="other branch", branch="refs/heads/release/*", non_fast_forward=True),
            _ruleset(2, name="audit", enforcement="evaluate", reviews=True),
            _ruleset(3, name="main", non_fast_forward=True),
        ],
    )

    report = vp.verify_protection("example/repo")

    assert report["ok"] is False
    reviews = report["checks"][0]
    force = report["checks"][1]
    assert reviews["status"] == "fail"
    assert reviews["provenance"] == []
    assert [item["source"] for item in force["provenance"]] == ["ruleset:3"]


def test_auth_failure_is_surfaced_on_every_check(monkeypatch, capsys):
    _install_gh(
        monkeypatch,
        legacy=None,
        rulesets=[_ruleset(7, reviews=True, non_fast_forward=True, checks=vp.MANDATORY_CHECKS)],
        legacy_error=(1, "", "gh: Forbidden (HTTP 403): administration permission required"),
    )

    code = vp.main(["--repo", "example/repo", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["ok"] is False
    assert all(check["status"] == "pass" for check in payload["checks"])
    for check in payload["checks"]:
        assert [error["source"] for error in check["errors"]] == ["legacy"]
        assert "HTTP 403" in check["errors"][0]["message"]
    assert payload["source_errors"][0]["source"] == "legacy"


def test_missing_legacy_protection_is_not_an_error_when_rulesets_satisfy_checks(monkeypatch):
    _install_gh(
        monkeypatch,
        legacy=None,
        rulesets=[
            _ruleset(
                7,
                reviews=True,
                non_fast_forward=True,
                checks=vp.MANDATORY_CHECKS,
            )
        ],
        legacy_error=(1, "", "gh: Not Found (HTTP 404)"),
    )

    report = vp.verify_protection("example/repo")

    assert report["ok"] is True
    assert report["source_errors"] == []


def test_human_output_names_failed_setting_and_source(monkeypatch, capsys):
    monkeypatch.setattr(vp, "MANDATORY_CHECKS", ("required-check",))
    _install_gh(
        monkeypatch,
        legacy=_legacy_payload(reviews=True),
        rulesets=[_ruleset(7, reviews=True)],
    )

    code = vp.main(["--repo", "example/repo"])
    captured = capsys.readouterr()

    assert code == 1
    assert "FAIL required_status_checks" in captured.err
    assert "missing: required-check" in captured.err
    assert "not verified" in captured.err


def test_mandatory_checks_name_the_hard_pr_ci_gates():
    assert set(vp.MANDATORY_CHECKS) == {
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
    }
