# Release Checklist — AusTenancy.ai Staging

> Fail closed on destructive plans, mutable image tags, public Agent routes, or failed signed invocation.

| # | Gate | Check | Expected | PASS/FAIL |
|---|------|-------|----------|-----------|
| 1 | Git | Working tree clean | `git diff-index --quiet HEAD --` returns exit 0 | |
| 2 | Account | AWS caller identity | `aws sts get-caller-identity --query Account` matches allowed account | |
| 3 | Region | AWS configured region | `ap-southeast-2` | |
| 4 | ECR | Image digest exists in repository | `sha256:<64 hex>` found via `aws ecr batch-get-image` | |
| 5 | Image | Lambda uses digest URI | `@sha256:<64 hex>` (not `:latest` or `:tag`) | |
| 6 | Image SHA | Source SHA is valid commit | `git cat-file -t <sha>` returns `commit` | |
| 7 | HEAD match | Code matches intended release | `git rev-parse HEAD` == expected SHA | |
| 8 | Secrets | No tracked state/backend/vars/plans | `git ls-files` shows 0 matching sensitive patterns | |
| 9 | Drift | Terraform plan shows no drift | `terraform plan -detailed-exitcode` returns exit 0 | |
| 10 | Plan | No destruction | 0 resources to destroy | |
| 11 | IAM | Bedrock least privilege | `bedrock:InvokeModel` scoped to `arn:aws:bedrock:ap-southeast-2::foundation-model/amazon.nova-pro-v1:0` | |
| 12 | Route | Health route NONE auth | `GET /health` authorization_type == NONE | |
| 13 | Route | Invoke route AWS_IAM | `POST /api/agent/invoke` authorization_type == AWS_IAM | |
| 14 | Lambda perms | Route-specific source_arn | Two permissions, source_arn scoped to route (not `/*`) | |
| 15 | Smoke | Health check | `GET /health` -> 200, valid JSON schema | |
| 16 | Smoke | Anonymous denial | `POST /api/agent/invoke` (no auth) -> 403 | |
| 17 | Smoke | Signed Agent invocation | SigV4 POST -> 200, valid AgentResponse via Pydantic | |
| 18 | Safety | Disclaimer present | AgentResponse.answer contains mandatory legal disclaimer | |
| 19 | Safety | Injection gate | Known suspicious inputs are flagged before graph invocation | |
| 20 | Smoke | Citation grounded | AgentResponse contains verified citations (citation_count > 0 for in-scope) | |
| 21 | Alarms | No active alarms | All 4 alarms OK or INSUFFICIENT_DATA (WARNING, not FAILED) | |
| 22 | Logs | No errors or deny events | 0 ERROR, 0 AccessDenied, 0 throttles in recent Lambda logs | |

## Go / No-Go Decision

- **Go:** All items PASSED or WARNING (INSUFFICIENT_DATA only). No FAILED items.
- **No-Go:** Any item FAILED. Fix the issue and re-run the checklist.

**FAILED is final.** A single FAILED gate blocks the release. Do not bypass.

## Sign-off

| Field | Value |
|-------|-------|
| Release | `SOURCE_GIT_SHA` |
| Image digest | `sha256:...` |
| Checklist result | `20/22 PASSED, 2 WARNING` or `22/22 PASSED` |
| Decision | GO / NO-GO |
| Approved by | |
| Date | |
| Version | 1.0 |
