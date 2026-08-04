-- Event Store: единственный источник истины (§8.2 architecture.md).
-- Append-only — приложение получает только SELECT/INSERT (см. scripts/grant_app_role.sql),
-- UPDATE/DELETE не выдаются ни одной роли, кроме владельца.
CREATE TABLE events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    event_type TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id UUID NOT NULL,
    payload JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX events_tenant_created_idx ON events (tenant_id, created_at);
CREATE INDEX events_aggregate_idx ON events (tenant_id, aggregate_type, aggregate_id);
CREATE INDEX events_type_idx ON events (tenant_id, event_type);

ALTER TABLE events ENABLE ROW LEVEL SECURITY;
ALTER TABLE events FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON events
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
