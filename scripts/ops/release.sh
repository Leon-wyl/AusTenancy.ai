#!/usr/bin/env bash
# Release orchestrator — chains build, preflight, plan, apply, smoke, verify,
# and log inspection into a single gated pipeline.
#
# Usage:
#   bash scripts/ops/release.sh [--skip-build] [--skip-container-smoke]
#                               [--skip-smoke] [--dry-run]
#
# Environment:
#   ALLOWED_ACCOUNT_ID — AWS account ID (required)
#   AWS_REGION          — override default ap-southeast-2
set -euo pipefail

ALLOWED_ACCOUNT_ID="${ALLOWED_ACCOUNT_ID:-}"
REGION="${AWS_REGION:-ap-southeast-2}"
TF_DIR="terraform/runtime/staging"
ECR_REPO="austenancy-staging-agent"
IMAGE_NAME="austenancy-staging-agent"
OPS_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="$(cd "$OPS_DIR/.." && pwd)"

SKIP_BUILD=0
SKIP_CONTAINER_SMOKE=0
SKIP_SMOKE=0
DRY_RUN=0

usage() {
  cat <<EOF >&2
Usage: $0 [options]

Options:
  --allowed-account-id ID   AWS account ID (default: \$ALLOWED_ACCOUNT_ID)
  --skip-build              Skip Docker build + ECR push
  --skip-container-smoke    Skip container RIE smoke tests
  --skip-smoke              Skip post-deploy smoke/verify/inspect
  --dry-run                 Preflight + plan only (no apply, no smoke)
  --region REGION           AWS region (default: ap-southeast-2)
  --help                    Show this message
EOF
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --allowed-account-id) ALLOWED_ACCOUNT_ID="$2"; shift 2 ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    --skip-container-smoke) SKIP_CONTAINER_SMOKE=1; shift ;;
    --skip-smoke) SKIP_SMOKE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --region) REGION="$2"; shift 2 ;;
    --help) usage ;;
    *) echo "Unknown option: $1" >&2; usage ;;
  esac
done

if [[ -z "$ALLOWED_ACCOUNT_ID" ]]; then
  echo "ERROR: --allowed-account-id or \$ALLOWED_ACCOUNT_ID is required" >&2
  exit 1
fi

GIT_SHA=$(git rev-parse HEAD)
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
IMAGE_TAG="${GIT_SHA}"
ECR_URL="${ALLOWED_ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPO}"

phases_json='{}'

phase_ok()  { phases_json=$(echo "$phases_json" | jq -c --arg p "$1" --arg s "PASSED"  '. + {($p): $s}'); }
phase_skip(){ phases_json=$(echo "$phases_json" | jq -c --arg p "$1" --arg s "SKIPPED" '. + {($p): $s}'); }
phase_applied(){ phases_json=$(echo "$phases_json" | jq -c --arg p "$1" --arg s "APPLIED" '. + {($p): $s}'); }

banner() { printf '\n=== Phase: %s ===\n\n' "$1" >&2; }

# ── Phase 0: Environment ────────────────────────────────────────────────

banner "Environment Check"

if ! git diff-index --quiet HEAD --; then
  echo "ERROR: Working tree is dirty. Commit or stash changes first." >&2
  exit 1
fi

echo "  Git SHA:       $GIT_SHA" >&2
echo "  Account:       $ALLOWED_ACCOUNT_ID" >&2
echo "  Region:        $REGION" >&2
echo "  Skip build:    $([ $SKIP_BUILD -eq 1 ] && echo YES || echo NO)" >&2
echo "  Dry run:       $([ $DRY_RUN -eq 1 ] && echo YES || echo NO)" >&2

# ── Phase 1: Build ──────────────────────────────────────────────────────

if [[ "$SKIP_BUILD" -eq 1 ]]; then
  banner "Build — SKIPPED"
  phase_skip "build"

  if [[ ! -f "${TF_DIR}/runtime.staging.auto.tfvars.json" ]]; then
    echo "ERROR: --skip-build but no tfvars file found at ${TF_DIR}/runtime.staging.auto.tfvars.json" >&2
    exit 1
  fi

  IMAGE_DIGEST=$(python3 -c "
import json
with open('${TF_DIR}/runtime.staging.auto.tfvars.json') as f:
    d = json.load(f)
uri = d.get('image_uri','')
digest = uri.split('@')[-1] if '@' in uri else ''
if not digest.startswith('sha256:'):
    print('ERROR: no sha256 digest found in tfvars image_uri')
    exit(1)
print(digest)
") || { echo "ERROR: Could not extract image digest from tfvars" >&2; exit 1; }
  echo "  Existing digest: $IMAGE_DIGEST" >&2
else
  banner "Build"

  echo "[1/3] Building Docker image (linux/amd64)..." >&2
  docker build --platform linux/amd64 -t "${IMAGE_NAME}:${IMAGE_TAG}" . || {
    echo "ERROR: Docker build failed" >&2; exit 1
  }
  docker tag "${IMAGE_NAME}:${IMAGE_TAG}" "${IMAGE_NAME}:latest"
  echo "  Image built: ${IMAGE_NAME}:${IMAGE_TAG}" >&2

  if [[ "$SKIP_CONTAINER_SMOKE" -eq 1 ]]; then
    echo "[2/3] Container smoke — SKIPPED" >&2
    phase_skip "container_smoke"
  else
    echo "[2/3] Running container smoke tests..." >&2
    if bash "${SCRIPTS_DIR}/run_container_smoke.sh" > /dev/null 2>&1; then
      echo "  Container smoke: PASSED" >&2
      phase_ok "container_smoke"
    else
      echo "ERROR: Container smoke tests failed" >&2
      exit 1
    fi
  fi

  echo "[3/3] Pushing to ECR..." >&2
  aws ecr get-login-password --region "$REGION" 2>/dev/null | \
    docker login --username AWS --password-stdin "$ECR_URL" > /dev/null 2>&1

  docker tag "${IMAGE_NAME}:${IMAGE_TAG}" "${ECR_URL}:${IMAGE_TAG}"
  docker push "${ECR_URL}:${IMAGE_TAG}" > /dev/null 2>&1 || {
    echo "ERROR: Docker push failed" >&2; exit 1
  }

  IMAGE_DIGEST=$(aws ecr describe-images \
    --repository-name "$ECR_REPO" \
    --image-ids "imageTag=${IMAGE_TAG}" \
    --query 'imageDetails[0].imageDigest' \
    --output text \
    --region "$REGION") || {
    echo "ERROR: Could not capture image digest from ECR" >&2; exit 1
  }

  IMAGE_URI="${ECR_URL}@${IMAGE_DIGEST}"
  echo "  Pushed: ${IMAGE_URI}" >&2

  cat > "${TF_DIR}/runtime.staging.auto.tfvars.json" <<JSONEOF
{
  "region": "${REGION}",
  "allowed_account_id": "${ALLOWED_ACCOUNT_ID}",
  "image_uri": "${IMAGE_URI}",
  "source_git_sha": "${GIT_SHA}"
}
JSONEOF
  echo "  Tfvars updated: ${TF_DIR}/runtime.staging.auto.tfvars.json" >&2

  phase_ok "build"
fi

# ── Phase 2: Preflight ──────────────────────────────────────────────────

banner "Preflight (MANDATORY)"

if ! bash "${OPS_DIR}/preflight.sh" \
      --expected-git-sha "$GIT_SHA" \
      --allowed-account-id "$ALLOWED_ACCOUNT_ID" \
      --region "$REGION" \
      --tf-dir "$TF_DIR" > /dev/null; then
  echo "ERROR: Preflight failed. Fix issues before continuing." >&2
  exit 1
fi
echo "  Preflight: PASSED" >&2
phase_ok "preflight"

# ── Phase 3: Plan ───────────────────────────────────────────────────────

banner "Terraform Plan (MANDATORY)"

cd "$TF_DIR"

PLAN_OUTPUT=$(terraform plan \
  -var-file="runtime.staging.auto.tfvars.json" \
  -out="runtime-staging.tfplan" -detailed-exitcode 2>&1)
PLAN_EXIT=$?

if [[ "$PLAN_EXIT" -eq 0 ]]; then
  echo "  Plan: no changes (infrastructure matches configuration)" >&2
  PLAN_ADD=0; PLAN_CHANGE=0; PLAN_DESTROY=0
elif [[ "$PLAN_EXIT" -eq 2 ]]; then
  PLAN_ADD=$(echo "$PLAN_OUTPUT" | grep -oE 'Plan: [0-9]+ to add' | grep -oE '[0-9]+' || echo 0)
  PLAN_CHANGE=$(echo "$PLAN_OUTPUT" | grep -oE '[0-9]+ to change' | grep -oE '[0-9]+' || echo 0)
  PLAN_DESTROY=$(echo "$PLAN_OUTPUT" | grep -oE '[0-9]+ to destroy' | grep -oE '[0-9]+' || echo 0)
  echo "  Plan: +${PLAN_ADD} ~${PLAN_CHANGE} -${PLAN_DESTROY}" >&2
else
  echo "ERROR: Terraform plan failed" >&2
  echo "$PLAN_OUTPUT" >&2
  exit 1
fi

if [[ "$PLAN_DESTROY" -gt 0 ]]; then
  echo "WARNING: Plan includes ${PLAN_DESTROY} destruction(s) — review carefully" >&2
fi

phase_ok "plan"
cd - > /dev/null

# ── Phase 4: Apply (manual gate) ────────────────────────────────────────

if [[ "$DRY_RUN" -eq 1 ]]; then
  banner "Apply — SKIPPED (dry-run)"
  phase_skip "apply"
  echo "  Plan saved: ${TF_DIR}/runtime-staging.tfplan" >&2
else
  banner "Apply"

  if [[ "$PLAN_EXIT" -eq 0 ]]; then
    echo "  No changes to apply — skipping" >&2
    phase_skip "apply"
  else
    printf '\nReview:  terraform -chdir=%s show runtime-staging.tfplan\n' "$TF_DIR" >&2
    printf 'Apply this plan? [y/N]: ' >&2
    read -r APPLY_CONFIRM < /dev/tty

    if [[ ! "$APPLY_CONFIRM" =~ ^[Yy]$ ]]; then
      echo "  Apply skipped. Plan saved: ${TF_DIR}/runtime-staging.tfplan" >&2
      phase_skip "apply"
    else
      echo "  Applying..." >&2
      cd "$TF_DIR"
      terraform apply runtime-staging.tfplan || {
        echo "ERROR: Terraform apply failed" >&2; exit 1
      }
      phase_applied "apply"
      cd - > /dev/null
      echo "  Apply: COMPLETE" >&2
    fi
  fi
fi

# ── Phase 5: Smoke + Verify ─────────────────────────────────────────────

if [[ "$DRY_RUN" -eq 1 ]]; then
  banner "Smoke + Verify — SKIPPED (dry-run)"
  phase_skip "smoke"
  phase_skip "verify"
  phase_skip "inspect"
else
  cd "$TF_DIR"
  TF_OUTPUT=$(terraform output -json 2>/dev/null) || TF_OUTPUT="{}"
  HEALTH_URL=$(echo "$TF_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['health_url']['value'])" 2>/dev/null) || HEALTH_URL=""
  INVOKE_URL=$(echo "$TF_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['invoke_route']['value'])" 2>/dev/null) || INVOKE_URL=""
  API_ENDPOINT=$(echo "$TF_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['api_endpoint']['value'])" 2>/dev/null) || API_ENDPOINT=""
  cd - > /dev/null

  if [[ "$SKIP_SMOKE" -eq 1 ]]; then
    banner "Smoke + Verify — SKIPPED"
    phase_skip "smoke"
    phase_skip "verify"
    phase_skip "inspect"
  elif [[ -z "$HEALTH_URL" || -z "$INVOKE_URL" ]]; then
    echo "WARNING: Could not resolve API URLs from terraform outputs — skipping smoke" >&2
    phase_skip "smoke"
    phase_skip "verify"
    phase_skip "inspect"
  else
    banner "Smoke Tests"
    if .venv/bin/python "${OPS_DIR}/smoke_test.py" \
         --health-url "$HEALTH_URL" \
         --invoke-url "$INVOKE_URL" \
         --region "$REGION" > /dev/null; then
      echo "  Smoke tests: PASSED" >&2
      phase_ok "smoke"
    else
      echo "WARNING: Smoke tests failed" >&2
      phases_json=$(echo "$phases_json" | jq -c '. + {"smoke":"FAILED"}')
    fi

    banner "Verify Deployment"
    if bash "${OPS_DIR}/verify_deployment.sh" \
         --region "$REGION" \
         --tf-dir "$TF_DIR" > /dev/null; then
      echo "  Verify: PASSED" >&2
      phase_ok "verify"
    else
      echo "WARNING: Verification found issues" >&2
      phases_json=$(echo "$phases_json" | jq -c '. + {"verify":"FAILED"}')
    fi

    banner "Log Inspection"
    if bash "${OPS_DIR}/inspect_logs.sh" \
         --minutes 5 \
         --region "$REGION" \
         --tf-dir "$TF_DIR" > /dev/null; then
      echo "  Logs: CLEAN" >&2
      phase_ok "inspect"
    else
      echo "WARNING: Log inspection found issues" >&2
      phases_json=$(echo "$phases_json" | jq -c '. + {"inspect":"FAILED"}')
    fi
  fi
fi

# ── Phase 6: Go/No-Go ───────────────────────────────────────────────────

if [[ "$DRY_RUN" -eq 1 ]]; then
  RELEASE_STATUS="PLAN_ONLY"
elif echo "$phases_json" | jq -e '.smoke == "FAILED" or .verify == "FAILED" or .inspect == "FAILED"' > /dev/null 2>&1; then
  RELEASE_STATUS="DEPLOYED_WITH_WARNINGS"
else
  RELEASE_STATUS="RELEASED"
fi

FINAL_JSON=$(jq -n \
  --arg status "$RELEASE_STATUS" \
  --arg git_sha "$GIT_SHA" \
  --arg image_digest "$IMAGE_DIGEST" \
  --arg api_endpoint "${API_ENDPOINT:-}" \
  --arg timestamp "$TIMESTAMP" \
  '{status: $status, git_sha: $git_sha, image_digest: $image_digest,
    api_endpoint: $api_endpoint, phases: $phases, timestamp: $timestamp}' \
  --argjson phases "$phases_json")

echo "$FINAL_JSON"
