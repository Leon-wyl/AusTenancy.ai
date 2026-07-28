# Phase E Boundaries

## Runtime and CRUD Separation

`src/api/` is the existing FastAPI and Mangum adapter for the deployed
LangGraph Agent Runtime. It remains AWS-IAM protected and is not the home for
user CRUD routes, SQLAlchemy models, Alembic migrations, or browser-facing
authentication.

`services/crud-api/` is the future NestJS service for user-scoped application
data: conversations, messages, agent jobs, citations, and related ownership
checks. CRUD endpoints create durable job records and do not synchronously call
retrieval or the Agent Runtime.

The deployed Agent Runtime in `src/api/` is a separate FastAPI/Mangum service.
Do not place NestJS modules, Prisma schema, or CRUD routes there.

## Client and Credential Boundary

The browser application in `apps/web/` may use Supabase Auth and Realtime with
browser-safe `NEXT_PUBLIC_*` configuration. Business-data writes go through the
CRUD API. Browser code must not receive AWS IAM credentials, database
credentials, or a Supabase service-role key, and must not directly invoke the
AWS-IAM Agent Runtime route.

Service-role credentials are server-only. User-scoped operations must use the
authenticated user identity and must not rely on a browser-supplied owner ID.

## Infrastructure Boundary

`terraform/` remains the only cloud infrastructure-as-code directory. The
future root `compose.yaml` is limited to local development and does not modify
or replace staging resources. The future root `supabase/` directory contains
non-sensitive Supabase CLI configuration and migrations after Step 14a.

## Stable Runtime Contract

Step 13b does not change Agent Runtime endpoints, Pydantic models, Lambda
handlers, Agent graph behavior, Docker image behavior, Terraform resources, or
release automation.

Step 14a adds the NestJS CRUD service, Prisma client, Supabase SQL migrations,
Next.js placeholder, and root compose.yaml. It does not modify `src/agent/`,
`src/api/`, `src/rag/`, `terraform/`, `scripts/ops/`, or `Dockerfile`.
