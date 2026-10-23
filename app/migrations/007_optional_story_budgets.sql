-- NULL explicitly means no story-wide budget limit; accounting remains durable.
ALTER TABLE stories ALTER COLUMN max_calls DROP NOT NULL;
ALTER TABLE stories ALTER COLUMN max_tokens DROP NOT NULL;
ALTER TABLE stories ALTER COLUMN max_cost_usd DROP NOT NULL;
