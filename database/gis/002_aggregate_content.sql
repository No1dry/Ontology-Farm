-- Append-only upgrade: Python owner runner verifies original core envelopes,
-- backfills this field, then makes it NOT NULL before committing version 2.
ALTER TABLE store_heads ADD COLUMN aggregate_content_hash text;
