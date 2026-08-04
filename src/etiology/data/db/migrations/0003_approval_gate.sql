-- Approval Gate: сквозной сервис "черновик агента -> человек -> публикация" (§8.1).
-- Не содержит доменной логики — просто очередь pending-объектов + статус.
CREATE TYPE approval_status AS ENUM ('pending', 'approved', 'rejected');

CREATE TABLE approval_gate (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    object_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    status approval_status NOT NULL DEFAULT 'pending',
    created_by TEXT NOT NULL,
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX approval_gate_tenant_status_idx ON approval_gate (tenant_id, status);

ALTER TABLE approval_gate ENABLE ROW LEVEL SECURITY;
ALTER TABLE approval_gate FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON approval_gate
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
