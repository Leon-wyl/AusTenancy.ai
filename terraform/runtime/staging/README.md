# Runtime Staging — AusTenancy.ai Agent

Lambda container + API Gateway HTTP API + CloudWatch alarms for the
7-node LangGraph agent (staging).

## Architecture

| Layer | Resource |
|---|---|
| Compute | Lambda container image (x86\_64, Image package type) |
| HTTP adapter | FastAPI + Mangum → API Gateway HTTP API v2 proxy |
| Auth | `GET /health` (public), `POST /api/agent/invoke` (AWS\_IAM) |
| LLM | Amazon Bedrock `amazon.nova-pro-v1:0` |
| Embeddings | FastEmbed (BGE-small-en-v1.5 + BM25), pre-warmed in image |
| Vector store | Static local Qdrant index (copied to `/tmp` at cold init) |
| Logs | Two separate CloudWatch Logs groups with deletion protection |
| Alarms | 4 CloudWatch alarms (errors, throttles, duration, API 5xx) |

## Prerequisites

- **Terraform** `>= 1.10.0`
- **AWS provider** `~> 6.0` (pinned to 6.56.0 by lock file)
- **Account** `891377120624` (guarded by `allowed_account_ids`)
- **Region** `ap-southeast-2`
- **IAM principal** running Terraform must have:
  - `ecr:GetRepositoryPolicy` + `ecr:SetRepositoryPolicy` (Lambda may auto-add repository policy)
  - `ecr:BatchGetImage` + `ecr:GetDownloadUrlForLayer` (Lambda reads image from ECR)
  - CloudWatch Logs delivery-management permissions for API access logging (see below)
  - Standard `lambda:CreateFunction`, `iam:*`, `apigatewayv2:*`, `cloudwatch:*`
- **ECR image** must exist with an immutable `@sha256:<digest>`
- **Bootstrap** S3 bucket `austenancy-terraform-x7k3m` must exist
- **Foundation** ECR repository `austenancy-staging-agent` must exist

## IAM Preflight: ECR Deployment

Lambda container deployment requires the Terraform caller to have ECR read access.
The Lambda service may attempt to auto-add a repository policy.

```bash
# Verify repository policy
aws ecr get-repository-policy \
  --repository-name austenancy-staging-agent \
  --region ap-southeast-2

# Required caller permissions
#   ecr:GetRepositoryPolicy
#   ecr:SetRepositoryPolicy
#   ecr:BatchGetImage
#   ecr:GetDownloadUrlForLayer
```

## IAM Preflight: API Access-Log Delivery

HTTP API access logging requires the Terraform caller to have CloudWatch Logs
delivery-management permissions. These are caller (deployment principal)
permissions — not Lambda execution role permissions.

```
logs:CreateLogDelivery
logs:GetLogDelivery
logs:UpdateLogDelivery
logs:DeleteLogDelivery
logs:ListLogDeliveries
logs:PutResourcePolicy
logs:DescribeResourcePolicies
logs:DescribeLogGroups
```

No `aws_cloudwatch_log_resource_policy` is created by this module unless the
AWS provider requires it during apply.

## Backend Initialization

The runtime stack stores state in the same S3 bucket as bootstrap and
foundation, under an isolated key.

```bash
cd terraform/runtime/staging

# First time (or after backend config change)
terraform init \
  -reconfigure \
  -backend-config=backend.staging.tfbackend

# Verify state isolation (hard gate — must pass before plan)
python3 -c "
import json
from pathlib import Path
data = json.loads(Path('.terraform/terraform.tfstate').read_text())
config = data['backend']['config']
assert config.get('key') == 'runtime/staging/terraform.tfstate', 'WRONG STATE KEY!'
print('Bucket:', config.get('bucket'))
print('Key:', config.get('key'))
print('Region:', config.get('region'))
print('STATE KEY VERIFIED')
"

# State must be empty — no bootstrap or foundation resources
terraform state list
# Expected: "No state file was found!" (empty state is correct)
```

If `terraform state list` shows `aws_s3_*`, `aws_ecr_*`, or any existing
resources, abort and troubleshoot — the wrong remote state is configured.

## Digest Variable Injection

The Lambda function uses an **immutable ECR digest URI**. Never use `:latest`
or a mutable tag.

```bash
# 1. Build, test, and push image
docker build --platform linux/amd64 --tag austenancy-staging-agent:latest .
aws ecr get-login-password --region ap-southeast-2 | \
  docker login --username AWS --password-stdin 891377120624.dkr.ecr.ap-southeast-2.amazonaws.com
docker tag austenancy-staging-agent:latest 891377120624.dkr.ecr.ap-southeast-2.amazonaws.com/austenancy-staging-agent:<tag>
docker push 891377120624.dkr.ecr.ap-southeast-2.amazonaws.com/austenancy-staging-agent:<tag>

# 2. Capture immutable digest
IMAGE_DIGEST=$(aws ecr describe-images \
  --repository-name austenancy-staging-agent \
  --image-ids imageTag=<tag> \
  --query 'imageDetails[0].imageDigest' \
  --output text \
  --region ap-southeast-2)

# 3. Inject into auto.tfvars.json
cat > runtime.staging.auto.tfvars.json << EOF
{
  "region": "ap-southeast-2",
  "allowed_account_id": "891377120624",
  "image_uri": "891377120624.dkr.ecr.ap-southeast-2.amazonaws.com/austenancy-staging-agent@${IMAGE_DIGEST}",
  "source_git_sha": "$(git rev-parse HEAD)"
}
EOF
```

The `image_uri` variable is validated to:
- Belong to account `891377120624`
- Be in region `ap-southeast-2`
- Use repository `austenancy-staging-agent`
- End with `@sha256:<64 hex chars>`

## Plan

```bash
# Format check
terraform fmt -recursive terraform/

# Validate configuration
cd terraform/runtime/staging
terraform validate

# Generate plan
terraform plan \
  -var-file=runtime.staging.auto.tfvars.json \
  -out=runtime-staging.tfplan
```

## Plan Review Checklist

Before applying, verify the plan:

- [ ] **State isolation**: No `aws_s3_*` or `aws_ecr_*` resources (bootstrap/foundation unchanged)
- [ ] **16 resources**, all in `ap-southeast-2`
- [ ] **Image URI**: Ends in `@sha256:<digest>` — no `:latest` or mutable tag
- [ ] **IAM**: `bedrock:InvokeModel` scoped to `arn:aws:bedrock:ap-southeast-2::foundation-model/amazon.nova-pro-v1:0` (no wildcard)
- [ ] **IAM**: `logs:CreateLogStream` + `logs:PutLogEvents` scoped to Lambda log group `:*.` No `logs:CreateLogGroup`
- [ ] **Routes**: `GET /health` → `NONE`, `POST /api/agent/invoke` → `AWS_IAM`
- [ ] **Lambda permissions**: Two route-specific `source_arn` (not API-wide `/*/*`)
- [ ] **Integration**: `timeout_milliseconds = 30000`, `AWS_PROXY`, `payload_format_version = "2.0"`
- [ ] **Log groups**: `deletion_protection_enabled = true` on both, no `kms_key_id`
- [ ] **Alarms**: Duration `Maximum` > 28000ms, API 5xx `Sum` > 0 on `AWS/ApiGateway`
- [ ] **Env vars**: 7 variables — `LLM_PROVIDER=bedrock`, `BEDROCK_MODEL_ID`, `BEDROCK_TEMPERATURE=0`, `QDRANT_PATH=/tmp/qdrant_storage`, `FASTEMBED_CACHE_PATH=/var/task/assets/fastembed_cache`, `HF_HUB_OFFLINE=1`, `SOURCE_GIT_SHA`

## Apply

```bash
cd terraform/runtime/staging
terraform apply runtime-staging.tfplan

# Capture outputs
terraform output -json
```

## Smoke Test

```bash
# Get the API endpoint
API_ENDPOINT=$(terraform output -json | python3 -c "import sys,json; print(json.load(sys.stdin)['api_endpoint']['value'])")
HEALTH_URL=$(terraform output -json | python3 -c "import sys,json; print(json.load(sys.stdin)['health_url']['value'])")

# 1. Health (public — no credentials)
curl -s "${HEALTH_URL}" | python3 -m json.tool
# {"status":"healthy","version":"0.1.0","api_version":"1.0"}

# 2. Agent invoke (AWS_IAM — requires sigv4 signing)
# Use awscurl or the AWS CLI to sign requests:
curl -X POST "${API_ENDPOINT}/api/agent/invoke" \
  --aws-sigv4 "aws:amz:ap-southeast-2:execute-api" \
  --user "${AWS_ACCESS_KEY_ID}:${AWS_SECRET_ACCESS_KEY}" \
  --header "x-amz-security-token: ${AWS_SESSION_TOKEN}" \
  --header "Content-Type: application/json" \
  -d '{"question":"What notice period is required for unpaid rent in Victoria?","jurisdiction":"VIC"}'
```

Alternatively, use the AWS CLI:

```bash
aws apigatewayv2 \
  --region ap-southeast-2 \
  --endpoint "${API_ENDPOINT}" \
  --cli-binary-format raw-in-base64-out \
  # Not directly invocable via standard CLI; use awscurl or Python requests-aws4auth
```

## Rollback by Digest

To roll back to a previous image:

```bash
# List available images
aws ecr describe-images \
  --repository-name austenancy-staging-agent \
  --region ap-southeast-2 \
  --query 'sort_by(imageDetails,&imagePushedAt)[*].[imageDigest,imagePushedAt]' \
  --output table

# Pin to a known-good digest
cat > rollback.tfvars.json << EOF
{
  "image_uri": "891377120624.dkr.ecr.ap-southeast-2.amazonaws.com/austenancy-staging-agent@sha256:<previous-digest>",
  "source_git_sha": "<previous-git-sha>"
}
EOF

# Apply the rollback
terraform apply \
  -var-file="rollback.tfvars.json" \
  -target='aws_lambda_function.agent'
```

## Deletion Protection

Both CloudWatch Logs groups use `deletion_protection_enabled = true`. Before
`terraform destroy`, disable it:

```bash
aws logs delete-log-group \
  --log-group-name /aws/lambda/austenancy-staging-agent \
  --region ap-southeast-2
aws logs delete-log-group \
  --log-group-name /aws/apigateway/austenancy-staging-agent \
  --region ap-southeast-2
```

Or apply with `deletion_protection_enabled = false` first.

## State Layout

```
s3://austenancy-terraform-x7k3m/
  bootstrap/terraform.tfstate           ← S3 bucket
  foundation/staging/terraform.tfstate  ← ECR repository
  runtime/staging/terraform.tfstate     ← Lambda + API GW + IAM + alarms
```

Each stack is fully independent. No cross-stack `data.terraform_remote_state`
references. The `allowed_account_ids` guard on each provider block prevents
accidental cross-account operations.
