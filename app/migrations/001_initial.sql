CREATE TABLE IF NOT EXISTS stories (
 id uuid PRIMARY KEY, idempotency_key text UNIQUE NOT NULL, request_hash text NOT NULL,
 text text NOT NULL, synthetic_only boolean NOT NULL CHECK(synthetic_only),
 status text NOT NULL DEFAULT 'NEW', revision int NOT NULL DEFAULT 1,
 answers text NOT NULL DEFAULT '', pm jsonb, plan jsonb, plan_digest text,
 approved boolean NOT NULL DEFAULT false, questions jsonb NOT NULL DEFAULT '[]',
 max_calls int NOT NULL, max_tokens int NOT NULL, max_cost_usd numeric NOT NULL,
 trace_id text NOT NULL, error_type text, created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS jobs (
 story_id uuid PRIMARY KEY REFERENCES stories(id), state text NOT NULL DEFAULT 'ready',
 attempts int NOT NULL DEFAULT 0, available_at timestamptz NOT NULL DEFAULT now(),
 lease_until timestamptz, owner text
);
CREATE TABLE IF NOT EXISTS events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, story_id uuid REFERENCES stories(id),
 event_key text UNIQUE, type text NOT NULL, payload jsonb NOT NULL DEFAULT '{}',
 trace_id text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS external_ids (
 story_id uuid NOT NULL REFERENCES stories(id), key text NOT NULL,
 external_id int NOT NULL, url text NOT NULL, PRIMARY KEY(story_id,key)
);
CREATE TABLE IF NOT EXISTS agent_results (
 story_id uuid NOT NULL REFERENCES stories(id), revision int NOT NULL,
 role text NOT NULL, result jsonb NOT NULL, PRIMARY KEY(story_id,revision,role)
);
CREATE TABLE IF NOT EXISTS approvals (
 story_id uuid REFERENCES stories(id), revision int NOT NULL, plan_digest text NOT NULL,
 actor text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(story_id,revision)
);
CREATE TABLE IF NOT EXISTS llm_calls (
 id uuid PRIMARY KEY, story_id uuid NOT NULL REFERENCES stories(id), role text NOT NULL,
 revision int NOT NULL, backend text NOT NULL, model text NOT NULL, state text NOT NULL,
 reserved_tokens int NOT NULL, reserved_cost numeric NOT NULL,
 actual_tokens int, actual_cost numeric, latency_ms int, error_type text,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS artifacts (
 story_id uuid REFERENCES stories(id), key text NOT NULL, url text NOT NULL,
 metadata jsonb NOT NULL DEFAULT '{}', PRIMARY KEY(story_id,key)
);
CREATE TABLE IF NOT EXISTS worker_heartbeats (
 id text PRIMARY KEY, last_seen timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(state,available_at);
CREATE INDEX IF NOT EXISTS events_story ON events(story_id,id);
CREATE TABLE IF NOT EXISTS status_outbox (
 story_id uuid PRIMARY KEY REFERENCES stories(id), next_attempt timestamptz NOT NULL DEFAULT now(),
 attempts int NOT NULL DEFAULT 0
);
