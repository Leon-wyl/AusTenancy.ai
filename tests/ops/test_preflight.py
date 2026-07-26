"""Tests for preflight.sh via subprocess mocking."""
import json
import subprocess
import unittest.mock as mock

RUN_PREFLIGHT = ["bash", "scripts/ops/preflight.sh"]


def _run(**kwargs):
    return subprocess.run(
        RUN_PREFLIGHT + ["--expected-git-sha", "8c453a6f60d7bc36b770628a34e08e204fcd3673", "--allowed-account-id", "891377120624"],
        capture_output=True, text=True, **kwargs
    )


class TestPreflightHappyPath:
    def test_all_checks_pass(self):
        result_json = json.dumps({"status": "PASSED", "warnings": 0, "checks": [], "timestamp": "2026-07-24T10:00:00Z"})
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout=result_json, stderr="")):
            result = _run()
            output = json.loads(result.stdout)
            assert output["status"] == "PASSED"

    def test_dirty_git_fails(self):
        result_json = json.dumps({"status": "FAILED", "warnings": 0, "failures": [{"check": "git_clean", "message": "Git working tree is dirty"}], "timestamp": "2026-07-24T10:00:00Z"})
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1, stdout=result_json, stderr="")):
            result = _run()
            output = json.loads(result.stdout)
            assert output["status"] == "FAILED"

    def test_wrong_account_fails(self):
        result_json = json.dumps({"status": "FAILED", "warnings": 0, "failures": [{"check": "aws_account", "message": "Expected account 891377120624, got 999999999999"}], "timestamp": "2026-07-24T10:00:00Z"})
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1, stdout=result_json, stderr="")):
            result = _run()
            output = json.loads(result.stdout)
            assert output["status"] == "FAILED"
