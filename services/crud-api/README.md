# CRUD API Service

This directory is reserved for the Phase E FastAPI service that owns
user-scoped application data. It is not implemented in Step 13b.

## Ownership

- This service will own CRUD APIs for conversations, messages, agent jobs, and
  citations.
- It will validate Supabase JWTs and enforce authorization before every
  user-scoped operation.
- It will create Agent jobs for later asynchronous execution; it must not
  synchronously invoke retrieval or the Agent Runtime from CRUD endpoints.
- The deployed Agent Runtime remains in `src/api/`. Do not place CRUD routes
  or SQLAlchemy code there.

## Planned Layout

Step 14a will create the service files and establish this internal layout:

```text
app/          # FastAPI entrypoint, API routes, database, models, schemas, services
alembic/      # Alembic configuration and schema migrations
tests/        # Hermetic and local-stack API tests
```

Do not add a Python package, dependencies, migrations, or service environment
files here before Step 14a.
