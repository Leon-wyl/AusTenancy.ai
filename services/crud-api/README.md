# CRUD API Service

This directory holds the Phase E NestJS service that owns user-scoped application data.
It is implemented in Step 14a.

## Ownership

- This service owns CRUD APIs for conversations, messages, agent jobs, and citations.
- It validates Supabase JWTs and enforces authorization before every user-scoped operation.
- It creates Agent jobs for later asynchronous execution; it must not synchronously invoke
  retrieval or the Agent Runtime from CRUD endpoints.
- The deployed Agent Runtime remains in `src/api/`. Do not place CRUD routes there.

## Layout

```text
src/           # NestJS modules, controllers, services
  main.ts      # Bootstrap (port 3001)
  app.module.ts
  health/      # GET /health → { status: "ok" } (no database dependency)
prisma/        # Prisma schema (database-first: mirrors Supabase SQL migrations)
  schema.prisma
tests/         # Jest unit and integration tests
```

## Tech Stack

| Layer | Technology |
|---|---|
| Framework | NestJS (Express platform) |
| Database Client | Prisma (client generation only; schema managed by Supabase SQL migrations) |
| Schema Authority | Supabase SQL migrations in `supabase/migrations/` |
| Language | TypeScript 5.x, Node 22 |
| Test | Jest + @nestjs/testing |
| Lint/Format | ESLint 9 + Prettier |

## Getting Started

```bash
# Prerequisites: supabase start, Node 22, npm
cp .env.example .env
npm ci
npx prisma generate
npm run start:dev     # http://localhost:3001/health
```
