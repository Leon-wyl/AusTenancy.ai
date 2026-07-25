#!/usr/bin/env bash
# Release preflight checks -- validates Git, AWS account, Terraform outputs,
# ECR image digest, tracked secrets, and infrastructure drift.
#
# Usage:
#   bash scripts/ops/preflight.sh --expected-git-sha <40-char-sha>
#
# Environment:
#   ALLOWED_ACCOUNT_ID -- AWS account ID (default from arg or env)
#   AWS_REGION -- override default ap-southeast-2
set -euo pipefail

ALLOWED_ACCOUNT_ID="${ALLOWED_ACCOUNT_ID:-}"
ECR_REPO_NAME="austenancy-staging-agent"
REGION="${AWS_REGION:-ap-southeast-2}"
TF_DIR="terraform/runtime/staging"
EXPECTED_GIT_SHA=""

usage() {
  cat <<EOF >&2
Usage: $0 --expected-git-sha <40-char-sha> [options]

Options:
  --allowed-account-id ID   AWS account ID (default: \$ALLOWED_ACCOUNT_ID)
  --ecr-repo-name NAME      ECR repository name (default: austenancy-staging-agent)
  --region REGION           AWS region (default: ap-southeast-2)
  --tf-dir PATH             Terraform runtime directory (default: terraform/runtime/staging)
  --help                    Show this message
EOF
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --allowed-account-id) ALLOWED_ACCOUNT_ID="$2"; shift 2 ;;
    --ecr-repo-name) ECR_REPO_NAME="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --tf-dir) TF_DIR="$2"; shift 2 ;;
    --expected-git-sha) EXPECTED_GIT_SHA="$2"; shift 2 ;;
    --help) usage ;;
    *) echo "Unknown option: $1" >&2; usage ;;
  esac
done

if [[ -z "$EXPECTED_GIT_SHA" ]]; then
  echo "ERROR: --expected-git-sha is required" >&2
  usage
fi
if [[ -z "$ALLOWED_ACCOUNT_ID" ]]; then
  echo "ERROR: --allowed-account-id or \$ALLOWED_ACCOUNT_ID is required" >&2
  usage
fi
if [[ ! "$EXPECTED_GIT_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "ERROR: --expected-git-sha must be a 40-character hex string" >&2
  exit 1
fi

checks=()
failures=()
warnings=0
timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

check_pass() { checks+=("$(jq -n --arg name "$1" --arg status "PASSED" '{name: $name, status: $status}')"); }
check_fail() { local msg="$2"; failures+=("$(jq -n --arg name "$1" --arg msg "$msg" '{check: $name, message: $msg}')"); }

echo "=== Preflight: $(date -u +"%Y-%m-%d %H:%M:%S UTC") ===" >&2

# 1. Clean Git working tree
echo -n "[1/10] Git working tree clean... " >&2
if git diff-index --quiet HEAD --; then
  echo "PASSED" >&2
  check_pass "git_clean"
else
  echo "FAILED" >&2
  check_fail "git_clean" "Git working tree is dirty"
  echo '{"status":"FAILED","warnings":'"$warnings"',"failures":'"$(printf '%s\n' "${failures[@]}" | jq -s '.')"',"timestamp":"'"$timestamp"'"}'
  exit 1
fi

# 2. AWS account identity
echo -n "[2/10] AWS account identity... " >&2
ACCOUNT=$(aws sts get-caller-identity --query Account --output text --region "$REGION" 2>&1) || {
  echo "FAILED (aws sts failed: $ACCOUNT)" >&2
  check_fail "aws_account" "aws sts get-caller-identity failed: $ACCOUNT"
  echo '{"status":"FAILED","warnings":'"$warnings"',"failures":'"$(printf '%s\n' "${failures[@]}" | jq -s '.')"',"timestamp":"'"$timestamp"'"}'
  exit 1
}
if [[ "$ACCOUNT" == "$ALLOWED_ACCOUNT_ID" ]]; then
  echo "PASSED ($ACCOUNT)" >&2
  check_pass "aws_account"
else
  echo "FAILED (expected=$ALLOWED_ACCOUNT_ID, got=$ACCOUNT)" >&2
  check_fail "aws_account" "Expected account $ALLOWED_ACCOUNT_ID, got $ACCOUNT"
  echo '{"status":"FAILED","warnings":'"$warnings"',"failures":'"$(printf '%s\n' "${failures[@]}" | jq -s '.')"',"timestamp":"'"$timestamp"'"}'
  exit 1
fi

# 3. AWS region
echo -n "[3/10] AWS region... " >&2
CONFIGURED_REGION=$(aws configure get region 2>/dev/null || echo "$REGION")
if [[ "$CONFIGURED_REGION" == "$REGION" || "$CONFIGURED_REGION" == "ap-southeast-2" ]]; then
  echo "PASSED ($CONFIGURED_REGION)" >&2
  check_pass "aws_region"
else
  echo "WARNING (configured=$CONFIGURED_REGION, expected=$REGION)" >&2
  warnings=$((warnings + 1))
  check_pass "aws_region"
fi

# 4. Terraform outputs present
echo -n "[4/10] Terraform outputs... " >&2
cd "$TF_DIR"
TF_OUTPUT=$(terraform output -json 2>&1) || {
  echo "FAILED (terraform output failed)" >&2
  check_fail "tf_outputs" "terraform output -json failed in $TF_DIR"
  echo '{"status":"FAILED","warnings":'"$warnings"',"failures":'"$(printf '%s\n' "${failures[@]}" | jq -s '.')"',"timestamp":"'"$timestamp"'"}'
  exit 1
}
# shellcheck disable=SC2034
HEALTH_URL=$(echo "$TF_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['health_url']['value'])" 2>&1) || {
  echo "FAILED (missing health_url output)" >&2
  check_fail "tf_outputs" "Missing health_url in terraform outputs"
  echo '{"status":"FAILED","warnings":'"$warnings"',"failures":'"$(printf '%s\n' "${failures[@]}" | jq -s '.')"',"timestamp":"'"$timestamp"'"}'
  exit 1
}
# shellcheck disable=SC2034
INVOKE_URL=$(echo "$TF_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['invoke_route']['value'])" 2>&1) || {
  echo "FAILED (missing invoke_route output)" >&2
  check_fail "tf_outputs" "Missing invoke_route in terraform outputs"
  echo '{"status":"FAILED","warnings":'"$warnings"',"failures":'"$(printf '%s\n' "${failures[@]}" | jq -s '.')"',"timestamp":"'"$timestamp"'"}'
  exit 1
}
DEPLOYED_IMAGE=$(echo "$TF_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['deployed_image_uri']['value'])" 2>&1) || {
  echo "FAILED (missing deployed_image_uri output)" >&2
  check_fail "tf_outputs" "Missing deployed_image_uri in terraform outputs"
  echo '{"status":"FAILED","warnings":'"$warnings"',"failures":'"$(printf '%s\n' "${failures[@]}" | jq -s '.')"',"timestamp":"'"$timestamp"'"}'
  exit 1
}
SOURCE_SHA=$(echo "$TF_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('source_git_sha', {}).get('value',''))" 2>/dev/null || echo "")
# Fallback: read from tfvars if not in terraform outputs
if [[ -z "$SOURCE_SHA" ]]; then
  TFVARS_FILE="${TF_DIR}/runtime.staging.auto.tfvars.json"
  if [[ -f "$TFVARS_FILE" ]]; then
    SOURCE_SHA=$(python3 -c "
import json
with open('${TFVARS_FILE}') as f:
    d = json.load(f)
print(d.get('source_git_sha',''))
" 2>/dev/null || echo "")
  fi
fi
# shellcheck disable=SC2034
LAMBDA_NAME=$(echo "$TF_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['lambda_function_name']['value'])" 2>&1) || {
  echo "FAILED (missing lambda_function_name output)" >&2
  check_fail "tf_outputs" "Missing lambda_function_name in terraform outputs"
  echo '{"status":"FAILED","warnings":'"$warnings"',"failures":'"$(printf '%s\n' "${failures[@]}" | jq -s '.')"',"timestamp":"'"$timestamp"'"}'
  exit 1
}
echo "PASSED" >&2
check_pass "tf_outputs"
cd - > /dev/null

# 5. Image URI is digest-based
echo -n "[5/10] Image URI uses digest... " >&2
if echo "$DEPLOYED_IMAGE" | grep -qE '@sha256:[0-9a-f]{64}$'; then
  IMAGE_DIGEST=$(echo "$DEPLOYED_IMAGE" | grep -oE 'sha256:[0-9a-f]{64}')
  echo "PASSED ($IMAGE_DIGEST)" >&2
  check_pass "image_digest"
else
  echo "FAILED (not a digest URI: $DEPLOYED_IMAGE)" >&2
  check_fail "image_digest" "Image URI does not use @sha256 digest: $DEPLOYED_IMAGE"
  echo '{"status":"FAILED","warnings":'"$warnings"',"failures":'"$(printf '%s\n' "${failures[@]}" | jq -s '.')"',"timestamp":"'"$timestamp"'"}'
  exit 1
fi

# 6. Image source SHA exists in git history
echo -n "[6/10] Image source SHA in git history... " >&2
if [[ -n "$SOURCE_SHA" ]]; then
  SHA_TYPE=$(git cat-file -t "$SOURCE_SHA" 2>&1) || SHA_TYPE="missing"
  if [[ "$SHA_TYPE" == "commit" ]]; then
    echo "PASSED" >&2
    check_pass "image_source_sha"
  else
    echo "FAILED (SHA $SOURCE_SHA not a commit: type=$SHA_TYPE)" >&2
    check_fail "image_source_sha" "SOURCE_GIT_SHA $SOURCE_SHA is not a valid commit (type=$SHA_TYPE)"
    echo '{"status":"FAILED","warnings":'"$warnings"',"failures":'"$(printf '%s\n' "${failures[@]}" | jq -s '.')"',"timestamp":"'"$timestamp"'"}'
    exit 1
  fi
else
  echo "FAILED (no source_git_sha in terraform outputs)" >&2
  check_fail "image_source_sha" "No source_git_sha found in terraform outputs"
  echo '{"status":"FAILED","warnings":'"$warnings"',"failures":'"$(printf '%s\n' "${failures[@]}" | jq -s '.')"',"timestamp":"'"$timestamp"'"}'
  exit 1
fi

# 7. Current HEAD matches expected
echo -n "[7/10] HEAD matches expected... " >&2
HEAD_SHA=$(git rev-parse HEAD)
if [[ "$HEAD_SHA" == "$EXPECTED_GIT_SHA" ]]; then
  pass "head_match" \
    "HEAD matches image source SHA ($EXPECTED_GIT_SHA)"
elif git merge-base --is-ancestor "$EXPECTED_GIT_SHA" "$HEAD_SHA"; then
  printf 'WARNING (HEAD=%s is newer than image source SHA=%s)\n' \
    "$HEAD_SHA" \
    "$EXPECTED_GIT_SHA" >&2
else
  fail "head_match" \
    "Image source SHA ($EXPECTED_GIT_SHA) is not an ancestor of HEAD ($HEAD_SHA)"
fi

RUNTIME_CHANGED_FILES="$(
  git diff --name-only "$EXPECTED_GIT_SHA"..HEAD -- \
    src \
    app \
    Dockerfile \
    pyproject.toml \
    poetry.lock \
    uv.lock \
    requirements.txt \
    requirements.lock \
    2>/dev/null || true
)"

if [[ -n "$RUNTIME_CHANGED_FILES" ]]; then
  fail "runtime_source_match" \
    "Runtime-relevant files changed after image source SHA: $(echo "$RUNTIME_CHANGED_FILES" | tr '\n' ' ')"
else
  pass "runtime_source_match" \
    "No runtime-relevant files changed after image source SHA"
fi

# 8. ECR digest exists
echo -n "[8/10] ECR digest exists... " >&2
ECR_CHECK=$(aws ecr batch-get-image \
  --repository-name "$ECR_REPO_NAME" \
  --image-ids "imageDigest=$IMAGE_DIGEST" \
  --region "$REGION" \
  --query 'images[0].imageId.imageDigest' \
  --output text 2>&1) || ECR_CHECK=""
if [[ -n "$ECR_CHECK" && "$ECR_CHECK" != "None" ]]; then
  echo "PASSED" >&2
  check_pass "ecr_digest"
else
  echo "FAILED (digest $IMAGE_DIGEST not found in $ECR_REPO_NAME)" >&2
  check_fail "ecr_digest" "Digest $IMAGE_DIGEST not found in ECR repository $ECR_REPO_NAME"
  echo '{"status":"FAILED","warnings":'"$warnings"',"failures":'"$(printf '%s\n' "${failures[@]}" | jq -s '.')"',"timestamp":"'"$timestamp"'"}'
  exit 1
fi

# 9. No tracked secrets
echo -n "[9/10] No tracked secrets... " >&2
TRACKED_SECRETS=$(git ls-files | grep -E '\.(tfstate|tfbackend|tfvars|tfplan|auto\.tfvars\.json)$' | grep -v '\.example$' || true)
SECRET_COUNT=$(echo "$TRACKED_SECRETS" | grep -c . 2>/dev/null || echo 0)
if [[ "$SECRET_COUNT" -eq 0 ]]; then
  echo "PASSED (0 tracked)" >&2
  check_pass "tracked_files"
else
  echo "FAILED ($SECRET_COUNT file(s) tracked)" >&2
  check_fail "tracked_files" "$SECRET_COUNT tracked file(s) match sensitive patterns"
  echo '{"status":"FAILED","warnings":'"$warnings"',"failures":'"$(printf '%s\n' "${failures[@]}" | jq -s '.')"',"timestamp":"'"$timestamp"'"}'
  exit 1
fi

# 10. Terraform drift check
echo -n "[10/10] Terraform drift... " >&2
cd "$TF_DIR"
terraform plan -detailed-exitcode -var-file=runtime.staging.auto.tfvars.json -out=/dev/null > /dev/null 2>&1
DRIFT_EXIT=$?
cd - > /dev/null
case $DRIFT_EXIT in
  0) echo "PASSED (no drift)" >&2; check_pass "terraform_drift" ;;
  2) echo "FAILED (drift detected)" >&2
     check_fail "terraform_drift" "Terraform plan detected drift (exit code 2)"
     echo '{"status":"FAILED","warnings":'"$warnings"',"failures":'"$(printf '%s\n' "${failures[@]}" | jq -s '.')"',"timestamp":"'"$timestamp"'"}'
     exit 1 ;;
  *) echo "FAILED (terraform plan error, exit=$DRIFT_EXIT)" >&2
     check_fail "terraform_drift" "Terraform plan failed with exit code $DRIFT_EXIT"
     echo '{"status":"FAILED","warnings":'"$warnings"',"failures":'"$(printf '%s\n' "${failures[@]}" | jq -s '.')"',"timestamp":"'"$timestamp"'"}'
     exit 1 ;;
esac

# Summary
echo "" >&2
echo "Preflight: ALL 10 CHECKS PASSED (warnings=$warnings)" >&2
echo '{"status":"PASSED","warnings":'"$warnings"',"checks":'"$(printf '%s\n' "${checks[@]}" | jq -s '.')"',"timestamp":"'"$timestamp"'"}'
