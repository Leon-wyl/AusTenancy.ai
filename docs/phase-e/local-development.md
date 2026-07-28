# Phase E Local Development Contract

Step 14a will add the local stack. Step 13b defines its ownership and command
conventions only; it does not add runnable services.

## Future Entry Points

Use the repository root as the working directory:

```bash
# Start the local multi-service stack after Step 14a creates compose.yaml.
docker compose up

# Initialize or run the local Supabase stack after Step 14a creates supabase/.
supabase start
```

`compose.yaml` will be the root entrypoint for the local Qdrant, CRUD API, and
web application services. `supabase/` will be initialized by the Supabase CLI
and will hold the committed, non-sensitive local configuration and migrations.

## Future Service Responsibilities

| Service | Responsibility | Local health contract |
|---|---|---|
| Supabase | Auth, PostgreSQL, Realtime, and RLS | Supabase CLI reports all local services healthy |
| Qdrant | Development retrieval data only | HTTP health endpoint responds successfully |
| CRUD API | User-scoped data APIs and job creation | FastAPI health endpoint responds successfully |
| Web | Authenticated browser application | Next.js development server serves the application |

The existing Agent Runtime retains its own local ASGI and Lambda-compatible
test contracts documented in
[`agent-deployment-architecture-gate.md`](../agent-deployment-architecture-gate.md).

## Configuration Ownership

| Configuration class | Future owner | Exposure rule |
|---|---|---|
| Current Agent Runtime values | Root `.env` copied from root `.env.example` | Server-only; never commit secrets |
| Browser Supabase URL and anon key | `apps/web/.env.local` | Must use `NEXT_PUBLIC_*`; public by design |
| CRUD API database, JWKS, and service configuration | `services/crud-api/.env` | Server-only; never commit |
| Local stack defaults | Root `compose.yaml` and Supabase CLI configuration | Non-sensitive values only |

Step 14a will add the service-specific environment examples after it defines the
actual service interfaces. Do not copy Phase E credentials into the root Agent
Runtime `.env`.
