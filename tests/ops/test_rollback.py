"""Tests for rollback.sh via subprocess mocking."""
import json
import subprocess
import unittest.mock as mock

RUN_ROLLBACK = [
    "bash", "scripts/ops/rollback.sh",
    "--digest", "sha256:f1ebca3f20ef5e6b470dc18db8f314949193db2dcb3a1d4f5b7139ba6a4817e0",
    "--git-sha", "8c453a6f60d7bc36b770628a34e08e204fcd3673",
]


class TestRollback:
    def test_valid_rollback(self):
        result_json = json.dumps({
            "status": "READY",
            "plan": {"resource_changes": [{"change": {"actions": ["update"]}}]},
            "timestamp": "2026-07-24T10:00:00Z",
        })
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout=result_json, stderr="")):
            result = subprocess.run(RUN_ROLLBACK, capture_output=True, text=True)
            output = json.loads(result.stdout)
            assert output["status"] == "READY"

    def test_invalid_digest_format(self):
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="ERROR: Digest must be sha256:<64-char hex>, got: invalid\n")):
            result = subprocess.run(
                ["bash", "scripts/ops/rollback.sh", "--digest", "invalid", "--git-sha", "8c453a6f60d7bc36b770628a34e08e204fcd3673"],
                capture_output=True, text=True
            )
            assert result.returncode == 1

    def test_rollback_destroy_fails(self):
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="ERROR: Plan contains destroy on aws_lambda_function.agent\n")):
            result = subprocess.run(RUN_ROLLBACK, capture_output=True, text=True)
            assert result.returncode == 1
