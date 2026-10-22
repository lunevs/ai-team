CREATE INDEX IF NOT EXISTS llm_calls_story_revision ON llm_calls(story_id,revision);
CREATE INDEX IF NOT EXISTS stories_status_updated ON stories(status,updated_at DESC);
CREATE INDEX IF NOT EXISTS events_type_story ON events(type,story_id);
