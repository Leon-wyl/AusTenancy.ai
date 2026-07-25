# Staging Release Runbook — AusTenancy.ai Agent

Step-by-step process for deploying the 7-node LangGraph Agent Lambda to staging.
Follow each section sequentially. Do not skip gates.

## Prerequisites

- AWS Profile `austenancy-dev` configured with credentials for account `891377120624`
- Terraform >= 1.10.0
- Docker with `linux/amd64` build support
- `jq`, `python3`, `awscli` installed
- Clean Git working tree (`git diff-index --quiet HEAD --`)
- ECR repository `austenancy-staging-agent` exists (created by foundation stack)

## 1. Build & Push Immutable Image

```bash
# Build linux/amd64 image
docker build --platform linux/amd64 --tag austenancy-staging-agent:latest .

# Run container smoke tests (optional but recommended)
bash scripts/run_container_smoke.sh

# Login to ECR
aws ecr get-login-password --region ap-southeast-2 | \
  docker login --username AWS --password-stdin 891377120624.dkr.ecr.ap-southeast-2.amazonaws.com

# Tag and push
GIT_SHA=$(git rev-parse HEAD)
docker tag austenancy-staging-agent:latest \
  891377120624.dkr.ecr.ap-southeast-2.amazonaws.com/austenancy-staging-agent:${GIT_SHA}
docker push 891377120624.dkr.ecr.ap-southeast-2.amazonaws.com/austenancy-staging-agent:${GIT_SHA}
```

## 2. Capture Immutable Digest

```bash
IMAGE_DIGEST=$(aws ecr describe-images \
  --repository-name austenancy-staging-agent \
  --image-ids imageTag=${GIT_SHA} \
  --query 'imageDetails[0].imageDigest' \
  --output text \
  --region ap-southeast-2)

echo "Digest: ${IMAGE_DIGEST}"
echo "Git SHA: ${GIT_SHA}"
```

## 3. Preflight Check

```bash
bash scripts/ops/preflight.sh \
  --expected-git-sha "${GIT_SHA}" \
  --allowed-account-id 891377120624
```

**Gate:** ALL 10 checks must pass. If preflight fails, fix the issue before continuing.

## 4. Inject Digest and Plan

```bash
cd terraform/runtime/staging

# Write tfvars with the new image digest
cat > runtime.staging.auto.tfvars.json << EOF
{
  "region": "ap-southeast-2",
  "allowed_account_id": "891377120624",
  "image_uri": "891377120624.dkr.ecr.ap-southeast-2.amazonaws.com/austenancy-staging-agent@${IMAGE_DIGEST}",
  "source_git_sha": "${GIT_SHA}"
}
EOF

# Generate plan
terraform plan \
  -var-file=runtime.staging.auto.tfvars.json \
  -out=runtime-staging.tfplan
```

## 5. Plan Review

Before apply, verify the plan using the checklist from `release-checklist.md`:

- [ ] No destruction (0 to destroy)
- [ ] Image URI uses `@sha256:` digest (not `:latest`)
- [ ] IAM: `bedrock:InvokeModel` scoped to model ARN
- [ ] Routes: `GET /health` NONE, `POST /api/agent/invoke` AWS_IAM
- [ ] Lambda permissions: route-specific `source_arn`
- [ ] All 4 alarms configured correctly

**Gate:** All checklist items must PASS. Never apply a plan with destruction or mutable image tags.

## 6. Apply

```bash
cd terraform/runtime/staging
terraform apply runtime-staging.tfplan

# Capture outputs
terraform output -json > outputs.json
HEALTH_URL=$(python3 -c "import sys,json; print(json.load(open('outputs.json'))['health_url']['value'])")
INVOKE_URL=$(python3 -c "import sys,json; print(json.load(open('outputs.json'))['invoke_route']['value'])")
```

**Note:** Lambda cold start may take 30-60 seconds on first invocation.

## 7. Smoke Tests

```bash
.venv/bin/python scripts/ops/smoke_test.py \
  --health-url "${HEALTH_URL}" \
  --invoke-url "${INVOKE_URL}"
```

**Gate:** All 3 tests must PASS:
1. Health check returns 200
2. Anonymous POST returns 403
3. SigV4-signed Agent invoke returns 200 with valid AgentResponse

## 8. Post-Deployment Verification

```bash
bash scripts/ops/verify_deployment.sh
```

**Gate:** All 16 checks should PASS. WARNING on `INSUFFICIENT_DATA` alarm states is acceptable immediately after deployment.

## 9. Log & Alarm Inspection

```bash
# Check recent logs (15-minute window)
bash scripts/ops/inspect_logs.sh --minutes 15

# Check specific time windows if needed
bash scripts/ops/inspect_logs.sh --minutes 60 --verbose
```

**Gate:** Zero ERROR lines, zero AccessDenied events, zero throttles in Lambda logs. API 5xx count should be 0.

## 10. Rollback (if needed)

If the release must be rolled back to a previous image:

```bash
# List available image digests
aws ecr describe-images \
  --repository-name austenancy-staging-agent \
  --region ap-southeast-2 \
  --query 'sort_by(imageDetails,&imagePushedAt)[*].[imageDigest,imagePushedAt]' \
  --output table

# Generate rollback plan (does NOT apply)
bash scripts/ops/rollback.sh \
  --digest sha256:<previous-digest> \
  --git-sha <previous-git-sha>

# Review the rollback plan
terraform -chdir=terraform/runtime/staging show rollback.tfplan

# Apply (manual — requires human approval)
terraform -chdir=terraform/runtime/staging apply rollback.tfplan
```

**Gate:** Rollback plan must NOT include destructions or unexpected resource changes. Only `aws_lambda_function.agent` and `aws_lambda_permission.*` may change.

## 11. Incident Triage

### Cold Start Timeout (>30s)
- Lambda cold start + Qdrant initialization may exceed API Gateway 30s timeout
- Check Lambda logs for initialization messages
- Re-invoke after first invocation (warm start is typically 3-10s)

### Bedrock Throttling
- Check Bedrock service quotas in AWS console
- Consider higher allocated throughput for `amazon.nova-pro-v1:0`

### Qdrant Lock Error
- `[REDACTED]` messages in Lambda logs indicate vector store contention
- This is a known limitation of local Qdrant in Lambda /tmp
- Cold restart typically resolves

### API 5xx Errors
- Check API Gateway access logs for `integrationLatency` > 30000ms
- Check Lambda logs for unhandled exceptions
- Bedrock Converse API errors appear as 5xx through Lambda

## 12. State Recovery

### State Layout
```
s3://austenancy-terraform-x7k3m/
  bootstrap/terraform.tfstate           -- S3 bucket
  foundation/staging/terraform.tfstate  -- ECR repository
  runtime/staging/terraform.tfstate     -- Lambda + API GW + IAM + alarms
```

### Recovery Commands

```bash
# Pull current state
terraform state pull > runtime-staging.tfstate.backup

# Check state for a specific resource
terraform state show aws_lambda_function.agent

# Never delete bootstrap or foundation state keys
# Never run terraform destroy without disabling log group deletion protection
```

## 13. Go/No-Go Decision

**Go criteria:**
- All 18 checklist items PASSED (WARNING acceptable for INSUFFICIENT_DATA)
- Smoke tests all PASSED
- No Lambda errors or access denied in recent logs
- All alarms in OK state (or expected INSUFFICIENT_DATA)

**No-Go if:**
- Any plan includes destruction
- Image uses mutable tag (`:latest`, `:GitSHA`)
- POST `/api/agent/invoke` returns anything other than 403 when unauthenticated
- SigV4-signed invocation fails or returns invalid schema
- Any Lambda error, timeout, or access denied in logs

### Sign-off

| Field | Value |
|-------|-------|
| Release version | `SOURCE_GIT_SHA` |
| Image digest | `sha256:...` |
| Smoke test | PASSED / FAILED |
| Checklist | 18/18 PASSED |
| Approved by | |
| Date | |
