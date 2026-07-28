# Web Application

This directory is reserved for the Phase E Next.js web application. It is not
implemented in Step 13b.

## Ownership

- The application will use the Next.js App Router for the authenticated chat
  experience.
- Browser code may use only `NEXT_PUBLIC_*` configuration values.
- Browser code may use Supabase Auth and Realtime directly, but business-data
  writes must go through the CRUD API.
- Browser code must not receive AWS credentials, database credentials, or a
  Supabase service-role key. It must not call the AWS-IAM Agent Runtime route
  directly.

## Planned Layout

Step 17 will create the application files and establish this internal layout:

```text
app/          # App Router routes, layouts, and server components
components/   # Reusable UI components
lib/          # Browser-safe clients and utilities
tests/        # Frontend unit and end-to-end test support
```

Do not add `package.json`, application code, or environment files here before
Step 14a defines the local stack contract.
