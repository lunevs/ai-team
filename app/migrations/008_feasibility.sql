CREATE TABLE feasibility_reports (
 story_id uuid NOT NULL REFERENCES stories(id), revision int NOT NULL,
 report jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(story_id,revision)
);
