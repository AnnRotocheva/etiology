-- Knowledge Base (§5 architecture.md) — актив, не агент. Пустая на старте v1,
-- наполнение вручную. kind различает "известные проблемы/решения" и "стандарты L1/L2".
CREATE TYPE kb_article_kind AS ENUM ('known_issue', 'standard');

CREATE TABLE knowledge_base_articles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    kind kb_article_kind NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    product_version TEXT,
    topic_tag TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX kb_articles_tenant_kind_idx ON knowledge_base_articles (tenant_id, kind);

ALTER TABLE knowledge_base_articles ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_base_articles FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON knowledge_base_articles
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
