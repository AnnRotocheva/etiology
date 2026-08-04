-- Выполнить вручную от имени суперпользователя, ДО применения миграций и
-- grant_app_role.sql. Не часть цепочки миграций — секрет не должен попадать
-- в версионируемый SQL.
--
-- RLS не защищает суперпользователя и роли с BYPASSRLS (§8.3 architecture.md) —
-- приложение обязано подключаться под этой ролью, иначе политики
-- tenant_isolation молча не сработают.

CREATE ROLE etiology_app LOGIN PASSWORD '<замени-на-секрет-из-vault>' NOBYPASSRLS;
GRANT CONNECT ON DATABASE etiology TO etiology_app;
GRANT USAGE ON SCHEMA public TO etiology_app;

-- Права на таблицы выдаются grant_app_role.sql после применения миграций.
