"""Staging smoke tests -- validates health, anonymous auth, and SigV4-signed Agent invoke.

Uses botocore.auth.SigV4Auth for signing and src.api.models.AgentResponse for
response validation. Never outputs legal answer text or credentials.

Usage:
    python scripts/ops/smoke_test.py --health-url <url> --invoke-url <url>
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

import botocore.auth
import botocore.awsrequest
import botocore.session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from src.api.models import AgentResponse


def get_health(health_url: str) -> dict:
    start = time.monotonic()
    try:
        req = urllib.request.Request(health_url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            latency_ms = round((time.monotonic() - start) * 1000, 1)
            return {
                "status_code": resp.status,
                "latency_ms": latency_ms,
                "body": json.loads(body),
                "passed": resp.status == 200,
            }
    except urllib.error.HTTPError as e:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        body = e.read().decode()
        return {
            "status_code": e.code,
            "latency_ms": latency_ms,
            "body": body[:500],
            "passed": False,
            "error": f"HTTP {e.code}",
        }
    except Exception as e:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {
            "status_code": None,
            "latency_ms": latency_ms,
            "body": str(e)[:500],
            "passed": False,
            "error": type(e).__name__,
        }


def post_anonymous(invoke_url: str) -> dict:
    start = time.monotonic()
    try:
        data = json.dumps(
            {"question": "Is this reachable?", "jurisdiction": "VIC"}
        ).encode()
        req = urllib.request.Request(
            invoke_url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {
            "status_code": 200,
            "latency_ms": latency_ms,
            "passed": False,
            "error": "Expected 403, got 200 -- AWS_IAM auth may be disabled",
        }
    except urllib.error.HTTPError as e:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {
            "status_code": e.code,
            "latency_ms": latency_ms,
            "passed": e.code == 403,
        }
    except Exception as e:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {
            "status_code": None,
            "latency_ms": latency_ms,
            "passed": False,
            "error": type(e).__name__,
        }


def post_signed(invoke_url: str, region: str, profile: str) -> dict:
    body = json.dumps(
        {
            "question": "What notice period is required for unpaid rent in Victoria?",
            "jurisdiction": "VIC",
        }
    ).encode()

    session = botocore.session.Session(profile=profile)
    credentials = session.get_credentials()
    if credentials is None:
        return {
            "status_code": None,
            "latency_ms": 0,
            "passed": False,
            "error": f"No AWS credentials found for profile {profile}",
        }

    frozen = credentials.get_frozen_credentials()

    request = botocore.awsrequest.AWSRequest(
        method="POST",
        url=invoke_url,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    botocore.auth.SigV4Auth(frozen, "execute-api", region).add_auth(request)

    signed_headers = dict(request.headers)

    start = time.monotonic()
    try:
        req = urllib.request.Request(
            invoke_url, data=body, headers=signed_headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            resp_body = resp.read().decode()
            latency_ms = round((time.monotonic() - start) * 1000, 1)
            resp_json = json.loads(resp_body)

            try:
                parsed = AgentResponse.model_validate(resp_json)
            except Exception as ve:
                return {
                    "status_code": resp.status,
                    "latency_ms": latency_ms,
                    "passed": False,
                    "error": f"Schema validation failed: {ve}",
                    "response_status": resp_json.get("status"),
                    "request_id": resp_json.get("request_id"),
                }

            return {
                "status_code": resp.status,
                "latency_ms": latency_ms,
                "response_status": parsed.status,
                "citation_count": len(parsed.verified_citations),
                "jurisdiction": parsed.selected_jurisdiction,
                "request_id": parsed.request_id,
                "passed": resp.status == 200,
            }
    except urllib.error.HTTPError as e:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {
            "status_code": e.code,
            "latency_ms": latency_ms,
            "passed": False,
            "error": f"HTTP {e.code}",
        }
    except Exception as e:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {
            "status_code": None,
            "latency_ms": latency_ms,
            "passed": False,
            "error": type(e).__name__,
        }


def main():
    parser = argparse.ArgumentParser(description="Staging smoke tests")
    parser.add_argument("--health-url", required=True, help="GET /health URL")
    parser.add_argument(
        "--invoke-url", required=True, help="POST /api/agent/invoke URL"
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "ap-southeast-2"),
    )
    parser.add_argument(
        "--profile",
        default=os.environ.get("AWS_PROFILE", "austenancy-dev"),
    )
    args = parser.parse_args()

    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    results = {
        "status": "RUNNING",
        "health": {},
        "anonymous_auth": {},
        "agent_invoke": {},
    }
    print("=== Smoke Test ===\n", file=sys.stderr)

    # Test 1: Health
    print("[1/3] Health check...", file=sys.stderr, end=" ")
    health_result = get_health(args.health_url)
    results["health"] = {
        "status_code": health_result["status_code"],
        "latency_ms": health_result["latency_ms"],
    }
    if not health_result["passed"]:
        print(f"FAILED ({health_result.get('error', 'unknown')})", file=sys.stderr)
        results["status"] = "FAILED"
        results["health"]["error"] = health_result.get("error", "unknown")
        print(json.dumps(results, indent=2))
        sys.exit(1)

    health_body = health_result.get("body", {})
    if (
        health_body.get("status") != "healthy"
        or health_body.get("api_version") != "1.0"
    ):
        print(f"FAILED (invalid schema: {health_body})", file=sys.stderr)
        results["status"] = "FAILED"
        results["health"]["schema_error"] = str(health_body)[:200]
        print(json.dumps(results, indent=2))
        sys.exit(1)
    print("PASSED", file=sys.stderr)

    # Test 2: Anonymous auth
    print("[2/3] Anonymous auth (expect 403)...", file=sys.stderr, end=" ")
    auth_result = post_anonymous(args.invoke_url)
    results["anonymous_auth"] = {
        "status_code": auth_result["status_code"],
        "latency_ms": auth_result["latency_ms"],
    }
    if not auth_result["passed"]:
        print(f"FAILED (got {auth_result['status_code']})", file=sys.stderr)
        results["status"] = "FAILED"
        if auth_result.get("error"):
            results["anonymous_auth"]["error"] = auth_result["error"]
        print(json.dumps(results, indent=2))
        sys.exit(1)
    print("PASSED", file=sys.stderr)

    # Test 3: SigV4 signed Agent invoke
    print("[3/3] SigV4-signed Agent invoke...", file=sys.stderr, end=" ")
    agent_result = post_signed(args.invoke_url, args.region, args.profile)
    results["agent_invoke"] = {
        "status_code": agent_result["status_code"],
        "latency_ms": agent_result["latency_ms"],
        "response_status": agent_result.get("response_status"),
        "citation_count": agent_result.get("citation_count", 0),
        "jurisdiction": agent_result.get("jurisdiction"),
        "request_id": agent_result.get("request_id"),
    }
    if not agent_result["passed"]:
        print(
            f"FAILED ({agent_result.get('error', 'unknown')})",
            file=sys.stderr,
        )
        results["status"] = "FAILED"
        results["agent_invoke"]["error"] = agent_result.get("error", "unknown")
        print(json.dumps(results, indent=2))
        sys.exit(1)
    print("PASSED", file=sys.stderr)

    results["status"] = "PASSED"
    results["timestamp"] = timestamp
    print("\nSmoke: ALL 3 TESTS PASSED\n", file=sys.stderr)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
