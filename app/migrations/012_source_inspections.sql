CREATE TABLE source_inspections (
 id uuid PRIMARY KEY,
 workspace_id uuid NOT NULL REFERENCES product_workspaces(id),
 task_run_id uuid NOT NULL REFERENCES workspace_tasks(id),
 source_id text NOT NULL,
 receipt jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX source_inspections_workspace ON source_inspections(workspace_id,created_at);
