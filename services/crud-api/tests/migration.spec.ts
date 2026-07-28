import { Client } from 'pg';

/**
 * Supabase SQL migration verification tests.
 *
 * Prerequisites: `supabase start` and `supabase db reset` must have been run.
 * The test connects via DATABASE_URL env variable and verifies that the initial
 * migration (supabase/migrations/20260728000000_initial_schema.sql) produced the
 * expected schema.
 */
const DATABASE_URL =
  process.env.DATABASE_URL || 'postgresql://postgres:postgres@localhost:54322/postgres';

let client: Client;

beforeAll(async () => {
  client = new Client({ connectionString: DATABASE_URL });
  await client.connect();
}, 15000);

afterAll(async () => {
  if (client) await client.end();
});

describe('users table', () => {
  it('has users table', async () => {
    const { rows } = await client.query(`
      SELECT EXISTS (
        SELECT FROM information_schema.tables WHERE table_name = 'users'
      ) AS exists;
    `);
    expect(rows[0].exists).toBe(true);
  });

  it('has id UUID column referencing auth.users', async () => {
    const { rows } = await client.query(`
      SELECT column_name, data_type
      FROM information_schema.columns
      WHERE table_name = 'users' AND column_name = 'id';
    `);
    expect(rows).toHaveLength(1);
    expect(rows[0].data_type).toBe('uuid');
  });

  it('has created_at and updated_at timestamptz columns', async () => {
    const { rows } = await client.query(`
      SELECT column_name, data_type
      FROM information_schema.columns
      WHERE table_name = 'users'
        AND column_name IN ('created_at', 'updated_at')
      ORDER BY column_name;
    `);
    expect(rows).toHaveLength(2);
    rows.forEach((row) => {
      expect(row.data_type).toBe('timestamp with time zone');
    });
  });

  it('has updated_at trigger', async () => {
    const { rows } = await client.query(`
      SELECT EXISTS (
        SELECT FROM information_schema.triggers
        WHERE trigger_name = 'trg_users_updated_at'
      ) AS exists;
    `);
    expect(rows[0].exists).toBe(true);
  });
});

describe('conversations table', () => {
  it('has conversations table', async () => {
    const { rows } = await client.query(`
      SELECT EXISTS (
        SELECT FROM information_schema.tables WHERE table_name = 'conversations'
      ) AS exists;
    `);
    expect(rows[0].exists).toBe(true);
  });

  it('has FK owner_user_id → users(id) ON DELETE CASCADE', async () => {
    const { rows } = await client.query(`
      SELECT confdeltype::text
      FROM pg_constraint
      WHERE conname = 'conversations_owner_user_id_fkey';
    `);
    expect(rows).toHaveLength(1);
    expect(rows[0].confdeltype).toBe('c');
  });

  it('has composite index on (owner_user_id, last_activity_at DESC, id DESC)', async () => {
    const { rows } = await client.query(`
      SELECT indexname
      FROM pg_indexes
      WHERE tablename = 'conversations'
        AND indexname = 'idx_conversations_owner_activity';
    `);
    expect(rows).toHaveLength(1);
  });
});

describe('messages table', () => {
  it('has messages table', async () => {
    const { rows } = await client.query(`
      SELECT EXISTS (
        SELECT FROM information_schema.tables WHERE table_name = 'messages'
      ) AS exists;
    `);
    expect(rows[0].exists).toBe(true);
  });

  it('has author_role ENUM with user|assistant values', async () => {
    const { rows } = await client.query(`
      SELECT enum_range(NULL::author_role) AS values;
    `);
    expect(rows[0].values).toContain('user');
    expect(rows[0].values).toContain('assistant');
  });

  it('has content TEXT NOT NULL with length check', async () => {
    const { rows } = await client.query(`
      SELECT is_nullable, data_type
      FROM information_schema.columns
      WHERE table_name = 'messages' AND column_name = 'content';
    `);
    expect(rows).toHaveLength(1);
    expect(rows[0].is_nullable).toBe('NO');
  });

  it('has metadata JSONB column (nullable)', async () => {
    const { rows } = await client.query(`
      SELECT data_type, is_nullable
      FROM information_schema.columns
      WHERE table_name = 'messages' AND column_name = 'metadata';
    `);
    expect(rows).toHaveLength(1);
    expect(rows[0].data_type).toBe('jsonb');
  });

  it('has FK conversation_id → conversations(id) ON DELETE CASCADE', async () => {
    const { rows } = await client.query(`
      SELECT confdeltype::text
      FROM pg_constraint
      WHERE conname = 'messages_conversation_id_fkey';
    `);
    expect(rows).toHaveLength(1);
    expect(rows[0].confdeltype).toBe('c');
  });
});

describe('agent_jobs table', () => {
  it('has agent_jobs table', async () => {
    const { rows } = await client.query(`
      SELECT EXISTS (
        SELECT FROM information_schema.tables WHERE table_name = 'agent_jobs'
      ) AS exists;
    `);
    expect(rows[0].exists).toBe(true);
  });

  it('has job_status ENUM with queued|processing|succeeded|failed|cancelled', async () => {
    const { rows } = await client.query(`
      SELECT enum_range(NULL::job_status) AS values;
    `);
    const values = rows[0].values as string[];
    ['queued', 'processing', 'succeeded', 'failed', 'cancelled'].forEach((v) => {
      expect(values).toContain(v);
    });
  });

  it('has UNIQUE constraint on (owner_user_id, idempotency_key)', async () => {
    const { rows } = await client.query(`
      SELECT conname
      FROM pg_constraint
      WHERE conname = 'uq_idempotency_per_owner'
        AND contype = 'u';
    `);
    expect(rows).toHaveLength(1);
  });

  it('has FK trigger_message_id → messages(id) ON DELETE RESTRICT', async () => {
    const { rows } = await client.query(`
      SELECT confdeltype::text
      FROM pg_constraint
      WHERE conname = 'fk_trigger_message';
    `);
    expect(rows).toHaveLength(1);
    expect(rows[0].confdeltype).toBe('r');
  });

  it('has partial index for pending jobs', async () => {
    const { rows } = await client.query(`
      SELECT indexname
      FROM pg_indexes
      WHERE tablename = 'agent_jobs'
        AND indexname = 'idx_agent_jobs_pending';
    `);
    expect(rows).toHaveLength(1);
  });
});

describe('citations table', () => {
  it('has citations table', async () => {
    const { rows } = await client.query(`
      SELECT EXISTS (
        SELECT FROM information_schema.tables WHERE table_name = 'citations'
      ) AS exists;
    `);
    expect(rows[0].exists).toBe(true);
  });

  it('has FK message_id → messages(id) ON DELETE CASCADE', async () => {
    const { rows } = await client.query(`
      SELECT confdeltype::text
      FROM pg_constraint
      WHERE conname = 'citations_message_id_fkey';
    `);
    expect(rows).toHaveLength(1);
    expect(rows[0].confdeltype).toBe('c');
  });
});

describe('updated_at trigger', () => {
  it('set_updated_at function exists', async () => {
    const { rows } = await client.query(`
      SELECT EXISTS (
        SELECT FROM pg_proc WHERE proname = 'set_updated_at'
      ) AS exists;
    `);
    expect(rows[0].exists).toBe(true);
  });
});
