"""Shared fixtures and mocks for operations script tests."""
import pytest


@pytest.fixture
def sample_terraform_output():
    return {
        "health_url": {"value": "https://mqeu7yoxv8.execute-api.ap-southeast-2.amazonaws.com/health"},
        "invoke_route": {"value": "https://mqeu7yoxv8.execute-api.ap-southeast-2.amazonaws.com/api/agent/invoke"},
        "api_endpoint": {"value": "https://mqeu7yoxv8.execute-api.ap-southeast-2.amazonaws.com"},
        "deployed_image_uri": {
            "value": "891377120624.dkr.ecr.ap-southeast-2.amazonaws.com/austenancy-staging-agent@sha256:f1ebca3f20ef5e6b470dc18db8f314949193db2dcb3a1d4f5b7139ba6a4817e0"
        },
        "source_git_sha": {"value": "8c453a6f60d7bc36b770628a34e08e204fcd3673"},
        "lambda_function_name": {"value": "austenancy-staging-agent"},
        "lambda_function_arn": {"value": "arn:aws:lambda:ap-southeast-2:891377120624:function:austenancy-staging-agent"},
    }


@pytest.fixture
def sample_health_response():
    return {"status": "healthy", "version": "0.1.0", "api_version": "1.0"}


@pytest.fixture
def sample_agent_response():
    return {
        "request_id": "550e8400-e29b-41d4-a716-446655440000",
        "status": "success",
        "answer": "The notice period is 14 days.",
        "verified_citations": ["Residential Tenancies Act 1997 s.246"],
        "citation_verified_rate": 1.0,
        "clarification": None,
        "fallback_reason": None,
        "selected_jurisdiction": "VIC",
        "latency_ms": 4500.2,
        "trace_id": None,
        "api_version": "1.0",
        "generated_at": "2026-07-24T10:00:00Z",
    }
