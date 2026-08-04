-- Каталог диагностических команд (§4.4 architecture.md) — курируемый актив,
-- пополняется только вручную. Diagnostic Collector имеет только SELECT
-- (см. scripts/grant_app_role.sql) — агент не может добавить команду сам.
-- is_read_only=true закреплено constraint'ом: клиент исполняет команду сам,
-- ни у одного агента нет инструмента выполнения команд на инфраструктуре клиента.
CREATE TABLE diagnostic_command_catalog (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    scenario TEXT NOT NULL,
    command TEXT NOT NULL,
    environment_version TEXT,
    is_read_only BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT diagnostic_command_catalog_read_only_chk CHECK (is_read_only = true)
);

CREATE INDEX diagnostic_command_catalog_tenant_idx ON diagnostic_command_catalog (tenant_id);

ALTER TABLE diagnostic_command_catalog ENABLE ROW LEVEL SECURITY;
ALTER TABLE diagnostic_command_catalog FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON diagnostic_command_catalog
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
