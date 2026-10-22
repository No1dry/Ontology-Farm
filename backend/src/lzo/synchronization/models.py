"""Delivery DDL shared by independently versioned migration entry points."""
CORE_DDL = """
CREATE TABLE store_deliveries (
 event_id uuid NOT NULL, target text NOT NULL CHECK(target IN ('gis','timeseries','catalog')),
 envelope jsonb NOT NULL, envelope_hash text NOT NULL, payload_hash text NOT NULL,
 entity_id uuid NOT NULL, aggregate_revision bigint NOT NULL CHECK(aggregate_revision>0),
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','retry','failed','applied','superseded')),
 attempts integer NOT NULL DEFAULT 0, available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 error text, receipt jsonb, recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(event_id,target));
CREATE INDEX store_delivery_claim ON store_deliveries(target,available_at) WHERE status IN ('pending','retry');
GRANT SELECT,INSERT,UPDATE ON store_deliveries TO lzo_app;
"""
