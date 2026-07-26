#!/usr/bin/env bash
# Safe log inspection -- query CloudWatch Logs for Lambda and API access logs.
# JSON summary by default. Never outputs raw legal-answer content.
# --verbose applies the SAME redaction filter.
#
# Usage:
#   bash scripts/ops/inspect_logs.sh [--minutes 15] [--verbose]
set -euo pipefail

REGION="${AWS_REGION:-ap-southeast-2}"
TF_DIR="terraform/runtime/staging"
MINUTES=15
VERBOSE=0
FUNCTION_NAME=""
LAMBDA_LOG_GROUP=""
API_LOG_GROUP=""
timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

usage() {
  cat <<EOF >&2
Usage: $0 [options]
Options:
  --minutes N        Lookback window in minutes (default: 15)
  --verbose          Print filtered raw log lines (same redaction rules apply)
  --tf-dir PATH      Terraform runtime directory (default: terraform/runtime/staging)
  --region REGION    AWS region (default: ap-southeast-2)
  --help             Show this message
EOF
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --minutes) MINUTES="$2"; shift 2 ;;
    --verbose) VERBOSE=1; shift ;;
    --tf-dir) TF_DIR="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --help) usage ;;
    *) echo "Unknown option: $1" >&2; usage ;;
  esac
done

TF_OUTPUT=$(cd "$TF_DIR" && terraform output -json 2>/dev/null) || TF_OUTPUT="{}"
FUNCTION_NAME=$(echo "$TF_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['lambda_function_name']['value'])" 2>/dev/null) || FUNCTION_NAME="austenancy-staging-agent"
LAMBDA_LOG_GROUP="/aws/lambda/$FUNCTION_NAME"
API_LOG_GROUP="/aws/apigateway/$FUNCTION_NAME"

START_TIME=$(( ( $(date +%s) - MINUTES * 60 ) * 1000 ))
END_TIME=$(( $(date +%s) * 1000 ))

echo "=== Log Inspection (last ${MINUTES}min) ===" >&2

# --- Lambda logs ---
echo "  Querying Lambda logs..." >&2
LAMBDA_QUERY="fields @timestamp, @message | sort @timestamp desc | limit 50"
LAMBDA_QID=$(aws logs start-query \
  --log-group-name "$LAMBDA_LOG_GROUP" \
  --start-time "$START_TIME" \
  --end-time "$END_TIME" \
  --query-string "$LAMBDA_QUERY" \
  --region "$REGION" \
  --query 'queryId' --output text 2>/dev/null) || LAMBDA_QID=""

LAMBDA_ERRORS=0; LAMBDA_TIMEOUTS=0; LAMBDA_THROTTLES=0; LAMBDA_ACCESS_DENIED=0; LAMBDA_QDRANT=0
if [[ -n "$LAMBDA_QID" ]]; then
  sleep 2
  LAMBDA_RESULTS=$(aws logs get-query-results --query-id "$LAMBDA_QID" --region "$REGION" --output json 2>/dev/null) || LAMBDA_RESULTS="{}"
  LAMBDA_ERRORS=$(echo "$LAMBDA_RESULTS" | python3 -c "
import sys, json
results = json.load(sys.stdin)
count = 0
for row in results.get('results', [[]]):
    for field in row:
        if field.get('field') == '@message':
            msg = field.get('value','')
            if 'ERROR' in msg.upper(): count += 1
print(count)
" 2>/dev/null) || LAMBDA_ERRORS=0
  LAMBDA_TIMEOUTS=$(echo "$LAMBDA_RESULTS" | python3 -c "
import sys, json
results = json.load(sys.stdin)
count = 0
for row in results.get('results', [[]]):
    for field in row:
        if field.get('field') == '@message':
            if 'Task timed out' in field.get('value',''): count += 1
print(count)
" 2>/dev/null) || LAMBDA_TIMEOUTS=0
  LAMBDA_THROTTLES=$(echo "$LAMBDA_RESULTS" | python3 -c "
import sys, json
results = json.load(sys.stdin)
count = 0
for row in results.get('results', [[]]):
    for field in row:
        if field.get('field') == '@message':
            if 'throttl' in field.get('value','').lower(): count += 1
print(count)
" 2>/dev/null) || LAMBDA_THROTTLES=0
  LAMBDA_ACCESS_DENIED=$(echo "$LAMBDA_RESULTS" | python3 -c "
import sys, json
results = json.load(sys.stdin)
count = 0
for row in results.get('results', [[]]):
    for field in row:
        if field.get('field') == '@message':
            if 'AccessDenied' in field.get('value',''): count += 1
print(count)
" 2>/dev/null) || LAMBDA_ACCESS_DENIED=0
  LAMBDA_QDRANT=$(echo "$LAMBDA_RESULTS" | python3 -c "
import sys, json
results = json.load(sys.stdin)
count = 0
for row in results.get('results', [[]]):
    for field in row:
        if field.get('field') == '@message':
            msg = field.get('value','').lower()
            if 'qdrant' in msg and 'lock' in msg: count += 1
print(count)
" 2>/dev/null) || LAMBDA_QDRANT=0

  if [[ "$VERBOSE" -eq 1 ]]; then
    echo "--- Lambda log lines (filtered) ---" >&2
    echo "$LAMBDA_RESULTS" | python3 -c "
import sys, json
results = json.load(sys.stdin)
for row in results.get('results', [[]]):
    ts = ''; msg = ''
    for field in row:
        if field.get('field') == '@timestamp': ts = field.get('value','')
        if field.get('field') == '@message': msg = field.get('value','')
    if not msg: continue
    if len(msg) > 200: msg = '[REDACTED -- long message]'
    elif '\"answer\":' in msg.lower(): msg = '[REDACTED -- legal answer]'
    print(f'{ts} {msg[:100]}')
" 2>/dev/null
  fi
fi

echo "  Lambda: errors=$LAMBDA_ERRORS timeouts=$LAMBDA_TIMEOUTS throttles=$LAMBDA_THROTTLES access_denied=$LAMBDA_ACCESS_DENIED qdrant_lock=$LAMBDA_QDRANT" >&2

# --- API access logs ---
echo "  Querying API access logs..." >&2
API_QUERY="fields @timestamp, requestId, routeKey, status, integrationStatus, integrationLatency, responseLatency, responseLength | sort @timestamp desc | limit 50"
API_QID=$(aws logs start-query \
  --log-group-name "$API_LOG_GROUP" \
  --start-time "$START_TIME" \
  --end-time "$END_TIME" \
  --query-string "$API_QUERY" \
  --region "$REGION" \
  --query 'queryId' --output text 2>/dev/null) || API_QID=""

API_TOTAL=0; API_2XX=0; API_4XX=0; API_5XX=0; LATENCY_P50=0; LATENCY_P95=0; LATENCY_P99=0
if [[ -n "$API_QID" ]]; then
  sleep 2
  API_RESULTS=$(aws logs get-query-results --query-id "$API_QID" --region "$REGION" --output json 2>/dev/null) || API_RESULTS="{}"
  API_STATS=$(echo "$API_RESULTS" | python3 -c "
import sys, json
results = json.load(sys.stdin)
total = 0; s2xx = 0; s4xx = 0; s5xx = 0
latencies = []
for row in results.get('results', [[]]):
    total += 1
    status = ''; lat = ''
    for field in row:
        if field.get('field') == 'status': status = field.get('value','')
        if field.get('field') == 'integrationLatency': lat = field.get('value','')
    if status.startswith('2'): s2xx += 1
    elif status.startswith('4'): s4xx += 1
    elif status.startswith('5'): s5xx += 1
    if lat:
        try: latencies.append(float(lat))
        except: pass
latencies.sort()
p50 = latencies[len(latencies)//2] if latencies else 0
p95 = latencies[int(len(latencies)*0.95)] if len(latencies) >= 20 else (latencies[-1] if latencies else 0)
p99 = latencies[int(len(latencies)*0.99)] if len(latencies) >= 100 else (latencies[-1] if latencies else 0)
print(json.dumps({'total':total,'s2xx':s2xx,'s4xx':s4xx,'s5xx':s5xx,'p50':p50,'p95':p95,'p99':p99}))
" 2>/dev/null) || API_STATS='{"total":0,"s2xx":0,"s4xx":0,"s5xx":0,"p50":0,"p95":0,"p99":0}'
  API_TOTAL=$(echo "$API_STATS" | python3 -c "import sys,json; print(json.load(sys.stdin)['total'])")
  API_2XX=$(echo "$API_STATS" | python3 -c "import sys,json; print(json.load(sys.stdin)['s2xx'])")
  API_4XX=$(echo "$API_STATS" | python3 -c "import sys,json; print(json.load(sys.stdin)['s4xx'])")
  API_5XX=$(echo "$API_STATS" | python3 -c "import sys,json; print(json.load(sys.stdin)['s5xx'])")
  LATENCY_P50=$(echo "$API_STATS" | python3 -c "import sys,json; print(json.load(sys.stdin)['p50'])")
  LATENCY_P95=$(echo "$API_STATS" | python3 -c "import sys,json; print(json.load(sys.stdin)['p95'])")
  LATENCY_P99=$(echo "$API_STATS" | python3 -c "import sys,json; print(json.load(sys.stdin)['p99'])")
fi

echo "  API: total=$API_TOTAL 2xx=$API_2XX 4xx=$API_4XX 5xx=$API_5XX p50=${LATENCY_P50}ms p95=${LATENCY_P95}ms p99=${LATENCY_P99}ms" >&2

# --- Alarm states ---
ALARMS=$(aws cloudwatch describe-alarms --region "$REGION" --query "MetricAlarms[?contains(AlarmName, '$FUNCTION_NAME')].{Name:AlarmName,State:StateValue}" --output json 2>/dev/null) || ALARMS="[]"
ALARM_STATES=$(echo "$ALARMS" | python3 -c "
import sys, json
alarms = json.load(sys.stdin)
results = []
w = 0
for a in alarms:
    state = a['State']
    status = 'PASSED' if state == 'OK' else ('WARNING' if state == 'INSUFFICIENT_DATA' else 'FAILED')
    if status == 'WARNING': w += 1
    results.append({'name': a['Name'], 'state': state, 'status': status})
print(json.dumps({'alarms': results, 'warnings': w}))
" 2>/dev/null) || ALARM_STATES='{"alarms":[],"warnings":0}'

ALARM_JSON=$(echo "$ALARM_STATES" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin)['alarms']))")
ALARM_WARNINGS=$(echo "$ALARM_STATES" | python3 -c "import sys,json; print(json.load(sys.stdin)['warnings'])")

# --- Final JSON ---
python3 -c "
import json
print(json.dumps({
    'lambda': {
        'error_count': $LAMBDA_ERRORS,
        'timeout_count': $LAMBDA_TIMEOUTS,
        'throttle_count': $LAMBDA_THROTTLES,
        'access_denied_count': $LAMBDA_ACCESS_DENIED,
        'qdrant_lock_error_count': $LAMBDA_QDRANT
    },
    'api': {
        'total_requests': $API_TOTAL,
        'status_2xx': $API_2XX,
        'status_4xx': $API_4XX,
        'status_5xx': $API_5XX,
        'latency_p50_ms': $LATENCY_P50,
        'latency_p95_ms': $LATENCY_P95,
        'latency_p99_ms': $LATENCY_P99
    },
    'window_minutes': $MINUTES,
    'alarms': $ALARM_JSON,
    'warnings': $ALARM_WARNINGS,
    'timestamp': '$timestamp'
}, indent=2))
"
