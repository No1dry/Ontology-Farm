-- A DBA provisions the dedicated, non-owner maintenance role first.
CREATE TABLE telemetry_identities (
    id uuid PRIMARY KEY,
    source_id uuid NOT NULL,
    source_record_id text NOT NULL,
    version bigint NOT NULL CHECK(version>0),
    content_hash text NOT NULL,
    sampled_at timestamptz NOT NULL,
    UNIQUE(source_id,source_record_id,version)
);
CREATE TABLE telemetry_samples (
    sampled_at timestamptz NOT NULL,
    id uuid NOT NULL REFERENCES telemetry_identities(id),
    source_id uuid NOT NULL,
    object_id uuid NOT NULL,
    device_id uuid NOT NULL,
    kind text NOT NULL CHECK(kind IN ('observation','event','media','track')),
    payload jsonb NOT NULL,
    batch_id uuid NOT NULL,
    event_id uuid NOT NULL,
    PRIMARY KEY(sampled_at,id)
) PARTITION BY RANGE(sampled_at);
CREATE INDEX telemetry_samples_object_time ON telemetry_samples(object_id,sampled_at,id);
CREATE INDEX telemetry_samples_device_time ON telemetry_samples(device_id,sampled_at,id);
CREATE INDEX telemetry_samples_source ON telemetry_samples(source_id,sampled_at,id);
CREATE TABLE telemetry_supersessions (
    old_id uuid PRIMARY KEY,
    new_id uuid NOT NULL UNIQUE REFERENCES telemetry_identities(id)
);
CREATE TABLE telemetry_batch_receipts (
    batch_id uuid PRIMARY KEY,
    event_id uuid NOT NULL UNIQUE,
    payload_hash text NOT NULL,
    received_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
GRANT SELECT,INSERT ON telemetry_identities,telemetry_samples,telemetry_supersessions,telemetry_batch_receipts TO lzo_timeseries_app;

-- Only this fixed, bounded function exposes DDL. No identifiers or SQL supplied
-- by callers, no implicit search_path, and no evidence/partition deletion API.
CREATE FUNCTION telemetry_ensure_month(month_start date) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE table_name text;
BEGIN
    IF month_start IS NULL OR month_start <> date_trunc('month',month_start)::date
       OR month_start < DATE '2000-01-01' OR month_start >= DATE '2100-01-01' THEN
        RAISE EXCEPTION 'Unsupported partition month';
    END IF;
    PERFORM pg_advisory_xact_lock(503);
    table_name := 'telemetry_samples_' || to_char(month_start,'YYYYMM');
    EXECUTE format('CREATE TABLE IF NOT EXISTS public.%I PARTITION OF public.telemetry_samples FOR VALUES FROM (%L) TO (%L)',
        table_name,month_start::text || ' 00:00:00+00',
        (month_start + INTERVAL '1 month')::date::text || ' 00:00:00+00');
    RETURN table_name;
END;
$$;
REVOKE ALL ON FUNCTION telemetry_ensure_month(date) FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO lzo_timeseries_maintainer;
GRANT EXECUTE ON FUNCTION telemetry_ensure_month(date) TO lzo_timeseries_maintainer;
UPDATE store_schema_version SET version=3 WHERE version=2;
