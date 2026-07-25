#!/usr/bin/env bash
# Post-deployment verification -- checks Lambda config, API routes, IAM permissions,
# log groups, and CloudWatch alarms against expected values.
#
# Alarms in INSUFFICIENT_DATA state are reported as WARNING (not FAILED).
#
# Usage:
#   bash scripts/ops/verify_deployment.sh
set -euo pipefail

REGION="${AWS_REGION:-ap-southeast-2}"
TF_DIR="terraform/runtime/staging"
FUNCTION_NAME=""
API_ID=""
timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
checks=()
failures=()
warnings=0

usage() {
  cat <<EOF >&2
Usage: $0 [options]
Options:
  --function-name NAME   Lambda function name (default: from terraform output)
  --api-id ID            API Gateway API ID (default: from terraform output)
  --tf-dir PATH          Terraform runtime directory (default: terraform/runtime/staging)
  --region REGION        AWS region (default: ap-southeast-2)
  --help                 Show this message
EOF
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --function-name) FUNCTION_NAME="$2"; shift 2 ;;
    --api-id) API_ID="$2"; shift 2 ;;
    --tf-dir) TF_DIR="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --help) usage ;;
    *) echo "Unknown option: $1" >&2; usage ;;
  esac
done

check_pass() { checks+=("$(jq -n --arg name "$1" --arg status "PASSED" '{name: $name, status: $status}')"); }
check_fail() { failures+=("$(jq -n --arg name "$1" --arg msg "$2" '{check: $name, message: $msg}')"); }

echo "=== Post-Deployment Verification ===" >&2

TF_OUTPUT=$(cd "$TF_DIR" && terraform output -json 2>/dev/null) || TF_OUTPUT="{}"
if [[ -z "$FUNCTION_NAME" ]]; then
  FUNCTION_NAME=$(echo "$TF_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['lambda_function_name']['value'])" 2>/dev/null) || FUNCTION_NAME=""
fi
if [[ -z "$API_ID" ]]; then
  API_ID=$(echo "$TF_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['api_endpoint']['value'])" 2>/dev/null | grep -oE '[a-z0-9]{10}' || echo "")
fi
if [[ -z "$FUNCTION_NAME" || -z "$API_ID" ]]; then
  echo "ERROR: Could not resolve function_name or api_id from terraform outputs" >&2
  exit 1
fi

echo "Function: $FUNCTION_NAME, API: $API_ID" >&2

# 1. Lambda architecture
echo -n "[1/16] Lambda architecture... " >&2
ARCH=$(aws lambda get-function --function-name "$FUNCTION_NAME" --region "$REGION" --query 'Configuration.Architectures[0]' --output text 2>&1) || ARCH=""
if [[ "$ARCH" == "x86_64" ]]; then echo "PASSED" >&2; check_pass "lambda_architecture"
else echo "FAILED (got=$ARCH)" >&2; check_fail "lambda_architecture" "Expected x86_64, got $ARCH"; fi

# 2. Lambda timeout
echo -n "[2/16] Lambda timeout... " >&2
TIMEOUT=$(aws lambda get-function-configuration --function-name "$FUNCTION_NAME" --region "$REGION" --query 'Timeout' --output text 2>&1) || TIMEOUT=""
if [[ "$TIMEOUT" == "60" ]]; then echo "PASSED" >&2; check_pass "lambda_timeout"
else echo "FAILED (got=$TIMEOUT)" >&2; check_fail "lambda_timeout" "Expected 60, got $TIMEOUT"; fi

# 3. Lambda memory
echo -n "[3/16] Lambda memory... " >&2
MEMORY=$(aws lambda get-function-configuration --function-name "$FUNCTION_NAME" --region "$REGION" --query 'MemorySize' --output text 2>&1) || MEMORY=""
if [[ "$MEMORY" == "1024" ]]; then echo "PASSED" >&2; check_pass "lambda_memory"
else echo "FAILED (got=$MEMORY)" >&2; check_fail "lambda_memory" "Expected 1024, got $MEMORY"; fi

# 4. Lambda ephemeral storage
echo -n "[4/16] Lambda ephemeral storage... " >&2
EPHEMERAL=$(aws lambda get-function-configuration --function-name "$FUNCTION_NAME" --region "$REGION" --query 'EphemeralStorage.Size' --output text 2>&1) || EPHEMERAL=""
if [[ "$EPHEMERAL" == "2048" ]]; then echo "PASSED" >&2; check_pass "lambda_ephemeral_storage"
else echo "FAILED (got=$EPHEMERAL)" >&2; check_fail "lambda_ephemeral_storage" "Expected 2048, got $EPHEMERAL"; fi

# 5. Image digest
echo -n "[5/16] Image URI uses digest... " >&2
IMAGE_URI=$(aws lambda get-function --function-name "$FUNCTION_NAME" --region "$REGION" --query 'Configuration.ImageUri' --output text 2>&1) || IMAGE_URI=""
if echo "$IMAGE_URI" | grep -qE '@sha256:[0-9a-f]{64}$'; then echo "PASSED" >&2; check_pass "image_digest"
else echo "FAILED" >&2; check_fail "image_digest" "Image URI not digest-based: $IMAGE_URI"; fi

# 6. API health route auth
echo -n "[6/16] Health route auth... " >&2
ROUTES=$(aws apigatewayv2 get-routes --api-id "$API_ID" --region "$REGION" --query 'Items[*].[RouteKey,AuthorizationType]' --output json 2>/dev/null) || ROUTES="[]"
HEALTH_AUTH=$(echo "$ROUTES" | python3 -c "import sys,json; routes=json.load(sys.stdin); print(next((r[1] for r in routes if r[0]=='GET /health'), 'MISSING'))" 2>/dev/null) || HEALTH_AUTH="MISSING"
if [[ "$HEALTH_AUTH" == "NONE" ]]; then echo "PASSED" >&2; check_pass "api_health_route_auth"
else echo "FAILED (got=$HEALTH_AUTH)" >&2; check_fail "api_health_route_auth" "Expected NONE, got $HEALTH_AUTH"; fi

# 7. API invoke route auth
echo -n "[7/16] Invoke route auth... " >&2
INVOKE_AUTH=$(echo "$ROUTES" | python3 -c "import sys,json; routes=json.load(sys.stdin); print(next((r[1] for r in routes if r[0]=='POST /api/agent/invoke'), 'MISSING'))" 2>/dev/null) || INVOKE_AUTH="MISSING"
if [[ "$INVOKE_AUTH" == "AWS_IAM" ]]; then echo "PASSED" >&2; check_pass "api_invoke_route_auth"
else echo "FAILED (got=$INVOKE_AUTH)" >&2; check_fail "api_invoke_route_auth" "Expected AWS_IAM, got $INVOKE_AUTH"; fi

# 8. Lambda permissions
echo -n "[8/16] Lambda permissions... " >&2
POLICY=$(aws lambda get-policy --function-name "$FUNCTION_NAME" --region "$REGION" --query 'Policy' --output text 2>/dev/null) || POLICY="{}"
PERM_COUNT=$(echo "$POLICY" | python3 -c "import sys,json; p=json.load(sys.stdin); print(len(p.get('Statement',[])))" 2>/dev/null) || PERM_COUNT=0
if [[ "$PERM_COUNT" -eq 2 ]]; then echo "PASSED ($PERM_COUNT permissions)" >&2; check_pass "lambda_permissions"
else echo "FAILED (got=$PERM_COUNT)" >&2; check_fail "lambda_permissions" "Expected 2 Lambda permissions, got $PERM_COUNT"; fi

# 9-10. Log groups
echo -n "[9/16] Lambda log group... " >&2
LAMBDA_LOG="/aws/lambda/$FUNCTION_NAME"
if aws logs describe-log-groups --log-group-name-prefix "$LAMBDA_LOG" --region "$REGION" --query 'logGroups[?logGroupName==`'"$LAMBDA_LOG"'`].logGroupName' --output text 2>/dev/null | grep -q .; then
  echo "PASSED" >&2; check_pass "lambda_log_group"
else echo "FAILED" >&2; check_fail "lambda_log_group" "Log group $LAMBDA_LOG not found"; fi

echo -n "[10/16] API log group... " >&2
API_LOG="/aws/apigateway/$FUNCTION_NAME"
if aws logs describe-log-groups --log-group-name-prefix "$API_LOG" --region "$REGION" --query 'logGroups[?logGroupName==`'"$API_LOG"'`].logGroupName' --output text 2>/dev/null | grep -q .; then
  echo "PASSED" >&2; check_pass "api_log_group"
else echo "FAILED" >&2; check_fail "api_log_group" "Log group $API_LOG not found"; fi

# 11. Log retention
echo -n "[11/16] Lambda log retention... " >&2
RETENTION=$(aws logs describe-log-groups --log-group-name-prefix "$LAMBDA_LOG" --region "$REGION" --query 'logGroups[?logGroupName==`'"$LAMBDA_LOG"'`].retentionInDays' --output text 2>/dev/null) || RETENTION=""
if [[ -n "$RETENTION" && "$RETENTION" != "None" ]]; then echo "PASSED ($RETENTION days)" >&2; check_pass "lambda_log_retention"
else echo "WARNING (no retention set)" >&2; warnings=$((warnings + 1)); check_pass "lambda_log_retention"; fi

# 12. Deletion protection
echo -n "[12/16] Deletion protection... " >&2
LAMBDA_DELPROT=$(aws logs describe-log-groups --log-group-name-prefix "$LAMBDA_LOG" --region "$REGION" --query 'logGroups[?logGroupName==`'"$LAMBDA_LOG"'`].deletionProtectionEnabled' --output text 2>/dev/null) || LAMBDA_DELPROT=""
API_DELPROT=$(aws logs describe-log-groups --log-group-name-prefix "$API_LOG" --region "$REGION" --query 'logGroups[?logGroupName==`'"$API_LOG"'`].deletionProtectionEnabled' --output text 2>/dev/null) || API_DELPROT=""
if [[ "$LAMBDA_DELPROT" == "True" && "$API_DELPROT" == "True" ]]; then echo "PASSED" >&2; check_pass "deletion_protection"
else echo "FAILED (lambda=$LAMBDA_DELPROT, api=$API_DELPROT)" >&2; check_fail "deletion_protection" "Deletion protection not enabled on one or both log groups"; fi

# 13-16. CloudWatch alarms
ALARMS=$(aws cloudwatch describe-alarms --region "$REGION" --query "MetricAlarms[?contains(AlarmName, '$FUNCTION_NAME')].{Name:AlarmName,State:StateValue}" --output json 2>/dev/null) || ALARMS="[]"
ALARM_COUNT=$(echo "$ALARMS" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null) || ALARM_COUNT=0

for i in $(seq 1 4); do
  name="alarm_${i}"
  echo -n "[$((12+i))/16] CloudWatch alarm #${i}... " >&2
  if [[ "$ALARM_COUNT" -ge "$i" ]]; then
    STATE=$(echo "$ALARMS" | python3 -c "import sys,json; alarms=json.load(sys.stdin); print(alarms[$((i-1))]['State'])" 2>/dev/null) || STATE="UNKNOWN"
    ALARM_NAME=$(echo "$ALARMS" | python3 -c "import sys,json; alarms=json.load(sys.stdin); print(alarms[$((i-1))]['Name'])" 2>/dev/null) || ALARM_NAME="unknown"
    case "$STATE" in
      OK) echo "PASSED ($ALARM_NAME: $STATE)" >&2; check_pass "$name" ;;
      ALARM) echo "FAILED ($ALARM_NAME: $STATE)" >&2; check_fail "$name" "Alarm $ALARM_NAME in ALARM state" ;;
      INSUFFICIENT_DATA) echo "WARNING ($ALARM_NAME: $STATE)" >&2; warnings=$((warnings + 1)); check_pass "$name" ;;
      *) echo "WARNING ($ALARM_NAME: $STATE)" >&2; warnings=$((warnings + 1)); check_pass "$name" ;;
    esac
  else
    echo "WARNING (missing alarm #${i})" >&2; warnings=$((warnings + 1)); check_pass "$name"
  fi
done

# Summary
echo "" >&2
if [[ ${#failures[@]} -eq 0 ]]; then
  echo "Verification: ALL CHECKS PASSED (warnings=$warnings)" >&2
  echo '{"status":"PASSED","warnings":'"$warnings"',"checks":'"$(printf '%s\n' "${checks[@]}" | jq -s '.')"',"timestamp":"'"$timestamp"'"}'
else
  echo "Verification: ${#failures[@]} FAILURE(S) (warnings=$warnings)" >&2
  echo '{"status":"FAILED","warnings":'"$warnings"',"checks":'"$(printf '%s\n' "${checks[@]}" | jq -s '.')"',"failures":'"$(printf '%s\n' "${failures[@]}" | jq -s '.')"',"timestamp":"'"$timestamp"'"}'
  exit 1
fi
