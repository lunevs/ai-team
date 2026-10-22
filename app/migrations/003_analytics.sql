CREATE TABLE analytics_runs (
 story_id uuid REFERENCES stories(id), revision int NOT NULL,
 status text NOT NULL DEFAULT 'REQUESTED' CHECK(status IN ('REQUESTED','RUNNING','REVIEW','BLOCKED','APPROVED')),
 source_spec jsonb, financial_spec jsonb, dataset_checksum text, reference_rows jsonb,
 digest text, documentation_url text, approved_by text, approved_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(story_id,revision)
);
