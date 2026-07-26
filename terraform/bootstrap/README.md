# Terraform Bootstrap — S3 State Bucket

Creates the S3 backend bucket for all downstream Terraform root modules.

## Prerequisites

- Terraform >= 1.10.0
- AWS IAM profile with S3 permissions (see Required Permissions below)
- A globally unique bucket name (S3 names are global)

## Required IAM Permissions

```
s3:CreateBucket
s3:PutBucketVersioning
s3:PutBucketEncryption
s3:PutBucketPublicAccessBlock
s3:PutBucketOwnershipControls
s3:PutBucketPolicy
s3:GetBucketPolicy
s3:GetBucketVersioning
s3:GetBucketEncryption
s3:GetBucketPublicAccessBlock
s3:GetBucketOwnershipControls
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
# Edit terraform.tfvars — set real allowed_account_id and bucket_name

# Initialise (local state — no backend block yet)
terraform init

# Format and validate
terraform fmt -recursive ../../
terraform fmt -check -recursive ../../
terraform validate

# Plan and review
terraform plan -out=bootstrap.tfplan
# HUMAN REVIEW — only S3 resources, no IAM runtime roles, no Lambda

# Apply
terraform apply bootstrap.tfplan
```

## State Migration (after bucket created)

```bash
# 1. Verify bucket exists
terraform output bucket_name

# 2. Back up local state OUTSIDE the repo
BACKUP_DIR="$HOME/.terraform-state-backups/austenancy"
mkdir -p "$BACKUP_DIR"
cp terraform.tfstate "$BACKUP_DIR/bootstrap-$(date +%Y%m%d_%H%M%S).tfstate"

# 3. Create the real backend block
cat > backend.tf <<'EOF'
terraform {
  backend "s3" {
    use_lockfile = true
  }
}
EOF

# 4. Create backend config with real bucket name
cat > backend.bootstrap.tfbackend <<BACKEND
bucket  = "<real-bucket-name-from-output>"
key     = "bootstrap/terraform.tfstate"
region  = "ap-southeast-2"
encrypt = true
BACKEND

# 5. Migrate local state into S3
terraform init -migrate-state -backend-config=backend.bootstrap.tfbackend

# 6. Verify migration (non-sensitive metadata only)
terraform state pull | python3 -c "
import json, sys
state = json.load(sys.stdin)
print(json.dumps({
    'version': state.get('version'),
    'terraform_version': state.get('terraform_version'),
    'serial': state.get('serial'),
    'lineage': state.get('lineage'),
}, indent=2))
"
terraform state list

# 7. Lock provider checksums for multi-platform CI
terraform providers lock \
  -platform=darwin_arm64 \
  -platform=linux_amd64
```

## Outputs

| Output | Description |
|--------|-------------|
| `bucket_name` | S3 bucket name (passed to downstream backend configs) |
| `bucket_arn` | S3 bucket ARN |
| `bucket_region` | AWS region |

## Rollback

```bash
# If bucket was applied but migration failed:
terraform destroy  # from local state (before migration)

# If state was migrated to S3 and you need to recover:
# Restore from backup, re-init with local backend, destroy
cp "$BACKUP_DIR/bootstrap-*.tfstate" terraform.tfstate
terraform init -reconfigure
terraform destroy
```

## Design Decisions

- **No DynamoDB lock table** — uses S3-native `use_lockfile=true` (HashiCorp deprecated DynamoDB locking).
- **prevent_destroy** — lifecycle rule prevents accidental bucket deletion.
- **TLS enforcement** — bucket policy denies all non-HTTPS requests.
- **force_destroy = false** — bucket cannot be deleted while it contains state objects.
- **AES256 encryption** — server-side encryption enabled by default.
- **Public access blocked** — all four public access block settings enabled.
- **BucketOwnerEnforced** — ACLs disabled; bucket owner owns all objects.
- **No lifecycle expiration** — state versions are never automatically deleted (versioning preserves all history).
