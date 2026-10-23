ALTER TABLE workspace_tasks ADD COLUMN attempt int NOT NULL DEFAULT 0;
ALTER TABLE workspace_tasks ADD COLUMN review jsonb;
ALTER TABLE workspace_tasks ADD COLUMN repair_context jsonb NOT NULL DEFAULT '{}';
ALTER TABLE product_releases ADD COLUMN review_round int NOT NULL DEFAULT 0;
CREATE TABLE product_defects (
 id uuid PRIMARY KEY, story_id uuid NOT NULL REFERENCES stories(id), revision int NOT NULL,
 scope text NOT NULL, fingerprint text NOT NULL, finding jsonb NOT NULL,
 status text NOT NULL, task_id int, documentation_url text, resolution jsonb,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(story_id,revision,scope,fingerprint)
);
CREATE TABLE repair_history (
 id uuid PRIMARY KEY, story_id uuid NOT NULL REFERENCES stories(id), revision int NOT NULL,
 scope text NOT NULL, attempt int NOT NULL, source_digest text NOT NULL, evidence jsonb NOT NULL,
 decision jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(story_id,revision,scope,attempt,source_digest)
);
