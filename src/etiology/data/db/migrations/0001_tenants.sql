-- Реестр tenant'ов (юнитов Apliteni). Не содержит PII, RLS не применяется —
-- строки видны всем, т.к. это справочник, а не клиентские данные.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
