CREATE TABLE product_workspaces (
 id uuid PRIMARY KEY, story_id uuid NOT NULL REFERENCES stories(id), revision int NOT NULL,
 definition jsonb NOT NULL, files jsonb NOT NULL DEFAULT '{}', version int NOT NULL DEFAULT 0,
 epic_index int NOT NULL DEFAULT 0, git jsonb NOT NULL DEFAULT '{"pulls":[],"milestones":[]}',
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(story_id,revision)
);
CREATE TABLE workspace_tasks (
 id uuid PRIMARY KEY, workspace_id uuid NOT NULL REFERENCES product_workspaces(id), task_key text NOT NULL,
 task_id int NOT NULL, epic_id int NOT NULL, role text NOT NULL, specification jsonb NOT NULL,
 state text NOT NULL DEFAULT 'PENDING', files jsonb, base_version int, sequence int NOT NULL DEFAULT 0,
 observations jsonb NOT NULL DEFAULT '[]', summary text, intent text, checks jsonb, git jsonb,
 operation_id uuid, lease_until timestamptz, snapshot_digest text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(workspace_id,task_key)
);
