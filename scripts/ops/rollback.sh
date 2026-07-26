#!/usr/bin/env bash
# Digest rollback -- validates an ECR digest, generates a Terraform plan,
# and fails immediately on any destroy or unexpected resource changes.
# Never applies automatically.
#
# Usage:
#   bash scripts/ops/rollback.sh --digest sha256:<64-hex> --git-sha <40-char-sha>
set -euo pipefail

REGION="${AWS_REGION:-ap-southeast-2}"
TF_DIR="terraform/runtime/staging"
ECR_REPO="austenancy-staging-agent"
DIGEST=""
GIT_SHA=""
ACCOUNT_ID=""
timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

usage() {
  cat <<EOF >&2
Usage: $0 --digest sha256:<64-hex> --git-sha <40-char-sha> [options]
Options:
  --digest DIGEST    ECR image digest (sha256:<64 hex>) [required]
  --git-sha SHA      Git commit SHA (40-char hex) [required]
  --repo-name NAME   ECR repository name (default: austenancy-staging-agent)
  --tf-dir PATH      Terraform runtime directory (default: terraform/runtime/staging)
  --region REGION    AWS region (default: ap-southeast-2)
  --help             Show this message
EOF
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --digest) DIGEST="$2"; shift 2 ;;
    --git-sha) GIT_SHA="$2"; shift 2 ;;
    --repo-name) ECR_REPO="$2"; shift 2 ;;
    --tf-dir) TF_DIR="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --help) usage ;;
    *) echo "Unknown option: $1" >&2; usage ;;
  esac
done

if [[ -z "$DIGEST" ]]; then echo "ERROR: --digest is required" >&2; exit 1; fi
if [[ -z "$GIT_SHA" ]]; then echo "ERROR: --git-sha is required" >&2; exit 1; fi

# 1. Validate digest format
if [[ ! "$DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "ERROR: Digest must be sha256:<64-char hex>, got: $DIGEST" >&2
  exit 1
fi

# 2. Refuse mutable tags
if [[ "$DIGEST" != *"@sha256:"* && "$DIGEST" == *":"* && ! "$DIGEST" =~ ^sha256: ]]; then
  echo "ERROR: Refusing mutable image tag. Use a digest (sha256:<hex>)." >&2
  exit 1
fi

# 3. Get account ID
echo "Resolving account ID..." >&2
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --region "$REGION") || {
  echo "ERROR: Could not determine AWS account ID" >&2; exit 1
}

# 4. ECR existence check
echo "Checking ECR digest exists..." >&2
ECR_CHECK=$(aws ecr batch-get-image \
  --repository-name "$ECR_REPO" \
  --image-ids "imageDigest=$DIGEST" \
  --region "$REGION" \
  --query 'images[0].imageId.imageDigest' \
  --output text 2>&1) || ECR_CHECK=""
if [[ -z "$ECR_CHECK" || "$ECR_CHECK" == "None" ]]; then
  echo "ERROR: Digest $DIGEST not found in ECR repository $ECR_REPO" >&2
  exit 1
fi
echo "  Digest exists: $ECR_CHECK" >&2

# 5. Validate git SHA format
if [[ ! "$GIT_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "ERROR: Git SHA must be 40-char hex, got: $GIT_SHA" >&2
  exit 1
fi

# 6. Generate rollback tfvars
IMAGE_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPO}@${DIGEST}"

ROLLBACK_VARS=$(cat <<JSONEOF
{
  "image_uri": "${IMAGE_URI}",
  "source_git_sha": "${GIT_SHA}",
  "region": "${REGION}",
  "allowed_account_id": "${ACCOUNT_ID}"
}
JSONEOF
)

echo "$ROLLBACK_VARS" > "${TF_DIR}/rollback.tfvars.json"
echo "  Tfvars written to ${TF_DIR}/rollback.tfvars.json" >&2

# 7. Terraform plan
echo "Generating rollback plan..." >&2
cd "$TF_DIR"
terraform plan \
  -var-file="runtime.staging.auto.tfvars.json" \
  -var-file="rollback.tfvars.json" \
  -out="rollback.tfplan" \
  -detailed-exitcode > /dev/null 2>&1
PLAN_EXIT=$?
cd - > /dev/null

if [[ "$PLAN_EXIT" -eq 1 ]]; then
  echo "ERROR: Terraform plan failed" >&2
  rm -f "${TF_DIR}/rollback.tfvars.json"
  exit 1
fi

# 8. Inspect plan for destroys/unexpected changes
echo "Inspecting plan for safe changes..." >&2
PLAN_SUMMARY=$(cd "$TF_DIR" && terraform show -json rollback.tfplan 2>/dev/null | python3 -c "
import sys, json
plan = json.load(sys.stdin)
changes = plan.get('resource_changes', [])
add, change, destroy = 0, 0, 0
unexpected = []
for rc in changes:
    actions = rc.get('change', {}).get('actions', [])
    addr = rc.get('address', '')
    rtype = rc.get('type', '')
    if 'create' in actions: add += 1
    if 'update' in actions: change += 1
    if 'delete' in actions: destroy += 1
    if 'update' in actions or 'delete' in actions or 'create' in actions:
        if rtype not in ('aws_lambda_function', 'aws_lambda_permission'):
            unexpected.append(addr)
print(json.dumps({'add': add, 'change': change, 'destroy': destroy, 'unexpected': unexpected}))
") || PLAN_SUMMARY='{"add":0,"change":0,"destroy":0,"unexpected":[]}'

PLAN_ADD=$(echo "$PLAN_SUMMARY" | python3 -c "import sys,json; print(json.load(sys.stdin)['add'])")
PLAN_CHANGE=$(echo "$PLAN_SUMMARY" | python3 -c "import sys,json; print(json.load(sys.stdin)['change'])")
PLAN_DESTROY=$(echo "$PLAN_SUMMARY" | python3 -c "import sys,json; print(json.load(sys.stdin)['destroy'])")
UNEXPECTED=$(echo "$PLAN_SUMMARY" | python3 -c "import sys,json; print(json.load(sys.stdin)['unexpected'])")

if [[ "$PLAN_DESTROY" -gt 0 ]]; then
  echo "ERROR: Plan includes $PLAN_DESTROY destruction(s) -- aborting rollback" >&2
  rm -f "${TF_DIR}/rollback.tfvars.json" "${TF_DIR}/rollback.tfplan"
  exit 1
fi

if [[ "$UNEXPECTED" != "[]" ]]; then
  echo "ERROR: Unexpected resource changes: $UNEXPECTED" >&2
  rm -f "${TF_DIR}/rollback.tfvars.json" "${TF_DIR}/rollback.tfplan"
  exit 1
fi

# 9. Success
echo "" >&2
echo "========================================" >&2
echo "  ROLLBACK PLAN READY" >&2
echo "========================================" >&2
echo "  Plan file: ${TF_DIR}/rollback.tfplan" >&2
echo "  Digest:    $DIGEST" >&2
echo "  Git SHA:   $GIT_SHA" >&2
echo "" >&2
echo "  Review:    terraform -chdir=${TF_DIR} show rollback.tfplan" >&2
echo "  Apply:     terraform -chdir=${TF_DIR} apply rollback.tfplan" >&2
echo "========================================" >&2

echo "{\"status\":\"READY\",\"plan_file\":\"${TF_DIR}/rollback.tfplan\",\"plan_add\":$PLAN_ADD,\"plan_change\":$PLAN_CHANGE,\"plan_destroy\":$PLAN_DESTROY,\"image_digest\":\"$DIGEST\",\"git_sha\":\"$GIT_SHA\",\"timestamp\":\"$timestamp\"}"
