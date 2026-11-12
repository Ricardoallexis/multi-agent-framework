-- CAP-T04: input tokens the provider served from its prompt cache. NULL means the provider did not report it
-- (never 0 invented), including every row recorded before this migration. Both are already counted in tokens_in,
-- so they are never added to a total. cache_write_tokens stays NULL until an adapter reports cache writes.
ALTER TABLE telemetry ADD COLUMN cache_read_tokens INTEGER;
ALTER TABLE telemetry ADD COLUMN cache_write_tokens INTEGER;
