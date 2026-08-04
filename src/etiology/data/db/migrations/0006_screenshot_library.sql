-- Библиотека аннотированных скриншотов (§5) — реальные скриншоты, не
-- генеративные. Курируемый актив, пополняется только вручную.
CREATE TABLE screenshot_library (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    ui_version TEXT NOT NULL,
    step_description TEXT NOT NULL,
    image_ref TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX screenshot_library_tenant_ui_idx ON screenshot_library (tenant_id, ui_version);

ALTER TABLE screenshot_library ENABLE ROW LEVEL SECURITY;
ALTER TABLE screenshot_library FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON screenshot_library
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
