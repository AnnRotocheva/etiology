-- Выполнить вручную после применения миграций (см. create_app_role.sql).
-- Права по таблицам напрямую кодируют правила из CLAUDE.md: каталог
-- диагностических команд и библиотека скриншотов — курируемые активы,
-- пополняются только вручную (§4.4, §5 architecture.md) — агенты только читают.
-- events — append-only Event Store: без UPDATE/DELETE ни для кого, кроме владельца.

GRANT SELECT, INSERT, UPDATE ON tenants TO etiology_app;

GRANT SELECT, INSERT ON events TO etiology_app;

GRANT SELECT, INSERT, UPDATE ON approval_gate TO etiology_app;

GRANT SELECT, INSERT, UPDATE ON knowledge_base_articles TO etiology_app;

GRANT SELECT ON diagnostic_command_catalog TO etiology_app;

GRANT SELECT ON screenshot_library TO etiology_app;
