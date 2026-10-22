-- Independent domain migration 1. Schema marker is checked by the owner runner.
CREATE TABLE store_inbox (
 event_id uuid PRIMARY KEY, envelope_hash text NOT NULL, payload_hash text NOT NULL,
 entity_id uuid NOT NULL, aggregate_revision bigint NOT NULL CHECK(aggregate_revision>0),
 receipt jsonb NOT NULL, applied_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE TABLE store_heads (
 entity_id uuid PRIMARY KEY, aggregate_revision bigint NOT NULL CHECK(aggregate_revision>0),
 event_id uuid NOT NULL REFERENCES store_inbox(event_id), payload_hash text NOT NULL);
CREATE TABLE store_schema_version (version integer PRIMARY KEY CHECK(version>0));
INSERT INTO store_schema_version VALUES(1);
