-- Run ONCE against any database that was created before this change
-- (your dev database rag_platform AND the test database rag_platform_test).
-- Fresh databases get this table from schema.sql automatically.
-- Safe to run twice: every statement is guarded with IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS integration_events (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          TEXT NOT NULL,
    delivery_id     TEXT NOT NULL,
    event_type      TEXT,
    status          TEXT NOT NULL,
    summary         TEXT,
    owner           TEXT,
    run_id          UUID,
    detail          JSONB,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_integration_events_delivery
    ON integration_events (source, delivery_id)
    WHERE status NOT IN ('duplicate', 'rejected');
CREATE INDEX IF NOT EXISTS idx_integration_events_received ON integration_events (received_at DESC);
