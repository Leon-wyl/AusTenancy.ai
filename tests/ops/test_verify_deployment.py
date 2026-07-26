"""Tests for verify_deployment.sh via subprocess mocking."""
import json
import subprocess
import unittest.mock as mock

RUN_VERIFY = ["bash", "scripts/ops/verify_deployment.sh"]


class TestVerifyDeployment:
    def test_insufficient_data_is_warning(self):
        result_json = json.dumps({
            "status": "PASSED",
            "warnings": 1,
            "checks": [{"name": "cloudwatch_alarms", "status": "PASSED"}],
            "failures": [],
            "timestamp": "2026-07-24T10:00:00Z",
        })
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout=result_json, stderr="")):
            result = subprocess.run(RUN_VERIFY, capture_output=True, text=True)
            output = json.loads(result.stdout)
            assert output["status"] == "PASSED"
            assert output["warnings"] > 0
