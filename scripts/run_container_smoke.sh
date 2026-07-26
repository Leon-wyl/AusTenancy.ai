#!/usr/bin/env bash
# Container RIE smoke tests — sends API Gateway HTTP API v2 events to
# http://localhost:9000/2015-03-31/functions/function/invocations
#
# Requires: docker, jq, python3, awscli (for AWS profile)
#
# Usage:
#   bash scripts/run_container_smoke.sh
#
# Assumes image austenancy-agent:poc is already built.
set -euo pipefail

IMAGE="${IMAGE:-austenancy-agent:poc}"
PORT="${PORT:-9000}"
AWS_PROFILE="${AWS_PROFILE:-austenancy-dev}"

PASS=0
FAIL=0

PASSED() { printf '  \033[32mPASS\033[0m: %s\n' "$1"; PASS=$((PASS + 1)); }
FAILED() { printf '  \033[31mFAIL\033[0m: %s (expected=%s, got=%s)\n' "$1" "$2" "$3"; FAIL=$((FAIL + 1)); }

# ── Build a proper API Gateway v2 event using jq ───────────────────────

v2_event() {
  local method="$1" path="$2" body="$3"
  jq -n \
    --arg method "$method" \
    --arg path "$path" \
    --arg body "$body" \
    '{
      version: "2.0",
      routeKey: "\($method) \($path)",
      rawPath: $path,
      requestContext: {
        accountId: "123456789012",
        apiId: "test-api-id",
        domainName: "test.execute-api.ap-southeast-2.amazonaws.com",
        http: { method: $method, path: $path, protocol: "HTTP/1.1",
                sourceIp: "127.0.0.1", userAgent: "smoke-test" },
        requestId: "test-req-id",
        routeKey: "\($method) \($path)",
        stage: "$default",
        timeEpoch: 1583348638390
      },
      headers: { "content-type": "application/json" },
      body: $body,
      isBase64Encoded: false
    }'
}

invoke() {
  curl -s -X POST "http://localhost:${PORT}/2015-03-31/functions/function/invocations" \
    -H 'Content-Type: application/json' \
    -d "$1"
}

invoke_get() {
  curl -s "http://localhost:${PORT}/2015-03-31/functions/function/invocations" \
    -H 'Content-Type: application/json' \
    -d "$1"
}

# ── Start container ────────────────────────────────────────────────────

echo "=== RIE Container Smoke Tests ==="
echo "Image: $IMAGE"
echo ""

CID=$(docker run -d --rm -p "${PORT}:8080" \
  -e AWS_PROFILE="${AWS_PROFILE}" \
  -e AWS_SDK_LOAD_CONFIG=1 \
  -e AWS_REGION=ap-southeast-2 \
  -e LLM_PROVIDER=bedrock \
  -e BEDROCK_MODEL_ID=amazon.nova-pro-v1:0 \
  -e BEDROCK_TEMPERATURE=0 \
  --mount type=bind,src="$HOME/.aws",dst=/root/.aws,readonly \
  --entrypoint /lambda-entrypoint.sh \
  "${IMAGE}" src.api.handler.handler)
trap 'docker stop "$CID" 2>/dev/null || true' EXIT

# ── Poll for readiness (NOT fixed sleep) ───────────────────────────────

echo "Waiting for cold init..."
for i in $(seq 1 60); do
  if docker logs "$CID" 2>&1 | grep -q "Qdrant seed copy complete"; then
    echo "Ready after ${i}s"
    break
  fi
  sleep 1
done

# ── Test 1: Health ─────────────────────────────────────────────────────

echo ""
echo "--- Test 1: Health ---"
R=$(invoke_get "$(v2_event "GET" "/health" "")")
INNER=$(echo "$R" | python3 -c "import sys,json; print(json.load(sys.stdin)['body'])")
if echo "$INNER" | python3 -c "
import sys,json
d=json.load(sys.stdin)
assert d=={'status':'healthy','version':'0.1.0','api_version':'1.0'}, f'wrong: {d}'
"; then
  PASSED "Health returns correct schema"
else
  FAILED "Health response" "{status:healthy,version:0.1.0,api_version:1.0}" "$INNER"
fi

# ── Test 2: VIC success ────────────────────────────────────────────────

echo ""
echo "--- Test 2: VIC success ---"
BODY=$(jq -n '{question: "What notice period does a landlord need to give for eviction due to unpaid rent in Victoria?", jurisdiction: "VIC"}')
R=$(invoke "$(v2_event "POST" "/api/agent/invoke" "$(echo "$BODY" | jq -c .)")")
S=$(echo "$R" | python3 -c "import sys,json; print(json.loads(json.load(sys.stdin)['body'])['status'])")
if [ "$S" = "success" ]; then
  PASSED "VIC success status"
else
  FAILED "VIC success status" "success" "$S"
fi

# ── Test 3: NSW success ────────────────────────────────────────────────

echo ""
echo "--- Test 3: NSW success ---"
BODY=$(jq -n '{question: "How much notice for a rent increase in NSW?", jurisdiction: "NSW"}')
R=$(invoke "$(v2_event "POST" "/api/agent/invoke" "$(echo "$BODY" | jq -c .)")")
S=$(echo "$R" | python3 -c "import sys,json; print(json.loads(json.load(sys.stdin)['body'])['status'])")
if [ "$S" = "success" ]; then
  PASSED "NSW success status"
else
  FAILED "NSW success status" "success" "$S"
fi

# ── Test 4: Out-of-scope fallback ──────────────────────────────────────

echo ""
echo "--- Test 4: Out-of-scope fallback ---"
BODY=$(jq -n '{question: "Give me a pizza recipe"}')
R=$(invoke "$(v2_event "POST" "/api/agent/invoke" "$(echo "$BODY" | jq -c .)")")
S=$(echo "$R" | python3 -c "import sys,json; print(json.loads(json.load(sys.stdin)['body'])['status'])")
REASON=$(echo "$R" | python3 -c "import sys,json; print(json.loads(json.load(sys.stdin)['body']).get('fallback_reason',''))")
if [ "$S" = "fallback" ] && [ -n "$REASON" ]; then
  PASSED "Out-of-scope fallback (reason=$REASON)"
else
  FAILED "Out-of-scope fallback" "fallback with reason" "$S reason=$REASON"
fi

# ── Test 5: Missing-jurisdiction clarification ─────────────────────────

echo ""
echo "--- Test 5: Missing-jurisdiction clarification ---"
BODY=$(jq -n '{question: "What are my rights as a tenant for repairs?"}')
R=$(invoke "$(v2_event "POST" "/api/agent/invoke" "$(echo "$BODY" | jq -c .)")")
S=$(echo "$R" | python3 -c "import sys,json; print(json.loads(json.load(sys.stdin)['body'])['status'])")
CLAR=$(echo "$R" | python3 -c "import sys,json; print(json.loads(json.load(sys.stdin)['body']).get('clarification',''))")
if [ "$S" = "clarification" ] && [ -n "$CLAR" ]; then
  PASSED "Missing-jurisdiction clarification"
else
  FAILED "Missing-jurisdiction clarification" "clarification with text" "$S clar=$CLAR"
fi

# ── Test 6: Verified citations ─────────────────────────────────────────

echo ""
echo "--- Test 6: Verified citations ---"
BODY=$(jq -n '{question: "Can my landlord evict me for being 10 days behind on rent in VIC?", jurisdiction: "VIC"}')
R=$(invoke "$(v2_event "POST" "/api/agent/invoke" "$(echo "$BODY" | jq -c .)")")
CITES=$(echo "$R" | python3 -c "import sys,json; d=json.loads(json.load(sys.stdin)['body']); print(len(d.get('verified_citations',[])))")
if [ "$CITES" -gt 0 ]; then
  PASSED "Verified citations present ($CITES)"
else
  FAILED "Verified citations" ">0" "$CITES"
fi

# ── Test 7: Warm invocation ────────────────────────────────────────────

echo ""
echo "--- Test 7: Warm invocation ---"
BODY=$(jq -n '{question: "What is the notice period for rent increases in VIC?", jurisdiction: "VIC"}')
R=$(invoke "$(v2_event "POST" "/api/agent/invoke" "$(echo "$BODY" | jq -c .)")")
S=$(echo "$R" | python3 -c "import sys,json; print(json.loads(json.load(sys.stdin)['body'])['status'])")
LATENCY=$(echo "$R" | python3 -c "import sys,json; print(json.loads(json.load(sys.stdin)['body']).get('latency_ms',0))")
if [ "$S" = "success" ]; then
  PASSED "Warm invocation (latency=${LATENCY}ms)"
else
  FAILED "Warm invocation" "success" "$S"
fi

echo ""
echo "========================================="
printf "  Results: \033[32m%d passed\033[0m, \033[31m%d failed\033[0m\n" "$PASS" "$FAIL"
echo "========================================="

[ "$FAIL" -eq 0 ] || exit 1
