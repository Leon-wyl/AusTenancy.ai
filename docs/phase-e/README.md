# Phase E: Full-Stack Chat Application

This directory is the canonical documentation entrypoint for the planned
full-stack application. It supplements the roadmap in `AGENT.md` and
`README.md`; it does not replace the deployment decisions in
[`agent-deployment-architecture-gate.md`](../agent-deployment-architecture-gate.md).

## Directory Ownership

| Path | Owner | Status |
|---|---|---|
| `src/agent/`, `src/api/`, `src/rag/` | Existing Agent Runtime | Stable; do not restructure for Phase E |
| `apps/web/` | Next.js application | Reserved in Step 13b; implementation begins in Step 17 |
| `services/crud-api/` | FastAPI CRUD service | Reserved in Step 13b; implementation begins in Step 14a/15 |
| `terraform/` | AWS infrastructure | Existing canonical cloud IaC |
| `compose.yaml` | Local multi-service development | Created in Step 14a |
| `supabase/` | Supabase CLI configuration and migrations | Initialized in Step 14a |

## Reading Order

1. Read [boundaries.md](./boundaries.md) before creating backend, frontend, or
   infrastructure code.
2. Read [local-development.md](./local-development.md) before implementing the
   Step 14a local stack.
3. Follow the Phase E roadmap in `AGENT.md` for delivery order: local stack
   and database, auth/RLS, data controls, CRUD, async orchestration, frontend,
   and deployment.
