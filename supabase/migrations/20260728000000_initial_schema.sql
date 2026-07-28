-- Initial schema: users, conversations, messages, agent_jobs, citations
-- Managed by Supabase SQL migrations (DDL authority).
-- Prisma schema mirrors these tables for client generation only (`prisma generate`).

-- Utility: updated_at trigger
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE users (
  id          UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_users_updated_at
  BEFORE UPDATE ON users
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- conversations

CREATE TABLE conversations (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title             TEXT NOT NULL DEFAULT 'New conversation',
  last_activity_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_conversations_owner_activity
  ON conversations (owner_user_id, last_activity_at DESC, id DESC);

CREATE TRIGGER trg_conversations_updated_at
  BEFORE UPDATE ON conversations
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- messages

CREATE TYPE author_role AS ENUM ('user', 'assistant');

CREATE TABLE messages (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  author_role     author_role NOT NULL,
  content         TEXT NOT NULL CHECK (length(content) > 0),
  metadata        JSONB,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_messages_conversation_time
  ON messages (conversation_id, created_at);

-- agent_jobs

CREATE TYPE job_status AS ENUM ('queued', 'processing', 'succeeded', 'failed', 'cancelled');

CREATE TABLE agent_jobs (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id  UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  trigger_message_id UUID NOT NULL,
  owner_user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  status           job_status NOT NULL DEFAULT 'queued',
  idempotency_key  TEXT NOT NULL,
  correlation_id   UUID NOT NULL DEFAULT gen_random_uuid(),
  attempt          INT NOT NULL DEFAULT 1 CHECK (attempt >= 1),
  max_attempts     INT NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),
  lease_until      TIMESTAMPTZ,
  next_attempt_at  TIMESTAMPTZ,
  completed_at     TIMESTAMPTZ,
  error_metadata   JSONB,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT fk_trigger_message
    FOREIGN KEY (trigger_message_id) REFERENCES messages(id)
    ON DELETE RESTRICT,

  CONSTRAINT uq_idempotency_per_owner
    UNIQUE (owner_user_id, idempotency_key)
);

CREATE INDEX idx_agent_jobs_pending
  ON agent_jobs (status, next_attempt_at)
  WHERE status IN ('queued', 'failed');

CREATE INDEX idx_agent_jobs_conversation
  ON agent_jobs (conversation_id, created_at);

CREATE TRIGGER trg_agent_jobs_updated_at
  BEFORE UPDATE ON agent_jobs
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- citations

CREATE TABLE citations (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  message_id         UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  label              TEXT NOT NULL CHECK (length(label) > 0),
  jurisdiction       TEXT NOT NULL DEFAULT 'VIC',
  instrument_type    TEXT NOT NULL DEFAULT 'rta',
  instrument_title   TEXT NOT NULL DEFAULT '',
  instrument_version TEXT NOT NULL DEFAULT '',
  section_reference  TEXT NOT NULL DEFAULT '',
  source_chunk_id    TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_citations_message
  ON citations (message_id);
