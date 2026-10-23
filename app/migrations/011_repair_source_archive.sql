ALTER TABLE repair_history ADD COLUMN source_files jsonb NOT NULL DEFAULT '{}';
