#!/usr/bin/env python3
"""Наполняет демо-тенанта данными для сквозного прогона Triage -> Diagnostic
Collector (docs/architecture.md, v1 vertical slice). Идемпотентно: повторный
запуск не создаёт дублей. Использует postgres-суперпользователя напрямую,
т.к. diagnostic_command_catalog курируется только вручную и etiology_app
не имеет на него INSERT (см. scripts/grant_app_role.sql)."""
import asyncio
import os
import sys

import asyncpg

TENANT_SLUG = "keitaro-demo"
TENANT_NAME = "Keitaro (демо)"

KB_ARTICLES = [
    (
        "known_issue",
        "Ошибка SSL-сертификата при открытии трекера",
        "Проверьте срок действия сертификата в панели управления доменом. Если сертификат "
        "истёк — перевыпустите его через Let's Encrypt в разделе Domains вашего Keitaro-трекера "
        "и подождите до 10 минут на обновление. Если сертификат действителен, но браузер "
        "всё равно ругается — очистите кэш браузера и проверьте, что DNS A-запись домена "
        "указывает на актуальный IP сервера.",
        "ssl_certificate",
    ),
    (
        "known_issue",
        "Кампания не засчитывает клики",
        "Убедитесь, что в настройках кампании включён трекинг кликов (Stream -> Track type) "
        "и что ссылка кампании используется именно из Keitaro, а не прямая ссылка на оффер. "
        "Также проверьте, что бот-фильтрация (Bot filters) не блокирует тестовые переходы "
        "с вашего IP — добавьте его во White IP list на время проверки.",
        "clicks_not_tracked",
    ),
]

DIAGNOSTIC_COMMANDS = [
    # scenario пишется в стиле topic_tag, который выдаёт Triage Agent (см. промпт в
    # domain/diagnostics/triage/agent.py) — command_catalog.search() матчит по
    # вхождению topic_tag в scenario (docs/architecture.md §4.4), поэтому куратор каталога
    # обязан ориентироваться на реальную формулировку тегов, а не на произвольный текст.
    (
        "postbacks_not_received",
        "tail -n 200 /var/log/keitaro/postback.log | grep <click_id>",
        "read-only просмотр последних записей postback-лога по конкретному click_id — "
        "клиент выполняет сам на своём сервере, чтобы увидеть, дошёл ли постбэк от партнёрки",
    ),
    (
        "campaign_slow_loading",
        "curl -o /dev/null -s -w 'time_total: %{time_total}\\n' https://<ваш-домен>/click",
        "read-only замер времени ответа трекера на клик-запрос — помогает отличить "
        "проблему сети/хостинга от проблемы конфигурации кампании",
    ),
]


async def seed(database_url: str) -> None:
    conn = await asyncpg.connect(database_url)
    try:
        tenant_id = await conn.fetchval(
            """
            INSERT INTO tenants (slug, name) VALUES ($1, $2)
            ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            TENANT_SLUG,
            TENANT_NAME,
        )

        for kind, title, body, topic_tag in KB_ARTICLES:
            exists = await conn.fetchval(
                "SELECT 1 FROM knowledge_base_articles WHERE tenant_id = $1 AND title = $2",
                tenant_id,
                title,
            )
            if exists:
                continue
            await conn.execute(
                """
                INSERT INTO knowledge_base_articles (tenant_id, kind, title, body, topic_tag)
                VALUES ($1, $2::kb_article_kind, $3, $4, $5)
                """,
                tenant_id,
                kind,
                title,
                body,
                topic_tag,
            )

        for scenario, command, note in DIAGNOSTIC_COMMANDS:
            exists = await conn.fetchval(
                "SELECT 1 FROM diagnostic_command_catalog WHERE tenant_id = $1 AND scenario = $2",
                tenant_id,
                scenario,
            )
            if exists:
                continue
            await conn.execute(
                """
                INSERT INTO diagnostic_command_catalog (tenant_id, scenario, command, environment_version)
                VALUES ($1, $2, $3, $4)
                """,
                tenant_id,
                scenario,
                command,
                note,
            )

        print(f"Демо-тенант готов: slug={TENANT_SLUG} id={tenant_id}")
        print(f"  KB-статей: {len(KB_ARTICLES)}, диагностических команд: {len(DIAGNOSTIC_COMMANDS)}")
    finally:
        await conn.close()


def main() -> None:
    database_url = os.environ.get("SUPERUSER_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not database_url:
        print("SUPERUSER_DATABASE_URL или DATABASE_URL не заданы", file=sys.stderr)
        sys.exit(1)
    asyncio.run(seed(database_url))


if __name__ == "__main__":
    main()
