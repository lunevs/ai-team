CREATE TABLE execution_runs (
 id uuid PRIMARY KEY, story_id uuid NOT NULL REFERENCES stories(id),revision int NOT NULL,
 status text NOT NULL DEFAULT 'REQUESTED', analytics_digest text NOT NULL,
 attempt int NOT NULL DEFAULT 0 CHECK(attempt BETWEEN 0 AND 3),
 sql_text text, rows jsonb, qa jsonb, review jsonb, git jsonb, artifacts jsonb,
 business_accepted_by text,business_accepted_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(story_id,revision)
);
CREATE TABLE execution_attempts (
 run_id uuid REFERENCES execution_runs(id), attempt int NOT NULL,sql_text text NOT NULL,
 result jsonb NOT NULL,qa jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(run_id,attempt)
);
