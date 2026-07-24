# Terraform Foundation — Staging ECR Repository

Creates the immutable ECR repository for the full-agent Lambda container image.

## Prerequisites

- Terraform >= 1.10.0
- Bootstrap stack applied and state migrated to S3 (see `../../bootstrap/README.md`)
- AWS IAM profile with ECR permissions
- Bootstrap S3 bucket name (from bootstrap outputs)

## Required IAM Permissions

```
ecr:CreateRepository
ecr:DeleteRepository
ecr:PutLifecyclePolicy
ecr:GetLifecyclePolicy
ecr:DescribeRepositories
ecr:GetRepositoryPolicy
ecr:SetRepositoryPolicy
```

## Quick Start

```bash
# Set AWS profile and region
export AWS_PROFILE=austenancy-dev
export AWS_REGION=ap-southeast-2
export AWS_DEFAULT_REGION=ap-southeast-2
aws sts get-caller-identity

# Copy and customise variables
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars — set real allowed_account_id

# Create backend config with real bootstrap bucket name
# (bucket name comes from: cd ../../bootstrap && terraform output bucket_name)
cp backend.staging.tfbackend.example backend.staging.tfbackend
# Edit backend.staging.tfbackend — set real bucket name

# Initialise with S3 backend
terraform init -backend-config=backend.staging.tfbackend

# Format and validate
terraform fmt -recursive ../../
terraform fmt -check -recursive ../../
terraform validate

# Plan and review
terraform plan -out=foundation-staging.tfplan
# HUMAN REVIEW — only ECR resources, no Lambda/API Gateway/IAM runtime

# Apply
terraform apply foundation-staging.tfplan

# Lock provider checksums for multi-platform CI
terraform providers lock \
  -platform=darwin_arm64 \
  -platform=linux_amd64
```

## Validation Without Remote Backend

```bash
# Useful for CI or when bootstrap bucket doesn't exist yet
terraform init -backend=false
terraform validate
```

## Outputs

| Output | Description |
|--------|-------------|
| `repository_url` | ECR repository URL (e.g. `*.dkr.ecr.ap-southeast-2.amazonaws.com/austenancy-staging-agent`) |
| `repository_arn` | ECR repository ARN |
| `repository_name` | ECR repository name (`austenancy-staging-agent`) |

## Image Push (manual — not Terraform)

```bash
GIT_SHA=$(git rev-parse --short HEAD)
ECR_URL=$(terraform output -raw repository_url)

aws ecr get-login-password --region ap-southeast-2 | \
  docker login --username AWS --password-stdin "$ECR_URL"

docker tag austenancy-agent:poc "${ECR_URL}:${GIT_SHA}"
docker push "${ECR_URL}:${GIT_SHA}"

IMAGE_DIGEST=$(aws ecr describe-images \
  --repository-name austenancy-staging-agent \
  --image-ids "imageTag=${GIT_SHA}" \
  --query 'imageDetails[0].imageDigest' \
  --output text)
echo "IMAGE_DIGEST=${IMAGE_DIGEST}"
```

## Design Decisions

- **IMMUTABLE** — image tags cannot be overwritten; Lambda must reference by digest.
- **AES256 encryption** — repository encryption at rest.
- **scan-on-push** — security scanning on every image push.
- **Lifecycle: untagged** — untagged images expire after 1 day.
- **Lifecycle: tagged** — retains the 5 most recent tagged images.
- **force_delete = false** — repository cannot be deleted while it contains images.
- **No Docker build/push** — Terraform manages only the ECR resource lifecycle; image operations are manual.
- **No Lambda/API Gateway/IAM runtime** — these belong in `terraform/runtime/staging/`.
