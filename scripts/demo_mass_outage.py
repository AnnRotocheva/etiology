#!/usr/bin/env python3
"""Демо-сценарий Incident Coordination Agent (docs/architecture.md §6.1):
несколько клиентов пишут независимо про один и тот же массовый сбой — агент
должен распознать это как один инцидент, а не диагностировать каждое
обращение по отдельности. Перед первым запуском: python scripts/seed_demo.py
"""
import asyncio

from etiology.agent.model_gateway import ModelGateway
from etiology.agent.model_gateway.providers.anthropic_provider import AnthropicProvider
from etiology.config import get_settings
from etiology.data.db.pool import get_pool
from etiology.domain.diagnostics.triage import triage
from etiology.domain.escalation_sync.incident_coordination import coordinate
from etiology.platform_core.event_bus import EventPublisher

TENANT_SLUG = "keitaro-demo"

MESSAGES = [
    "Трекер вообще не открывается, все ссылки на кампании дают ошибку 502",
    "У нас со всех кампаний сайт трекера не отвечает уже минут 10, это авария?",
    "Помогите, весь трафик падает мимо — домен трекера не открывается в браузере",
]


async def _resolve_tenant_id() -> str:
    pool = await get_pool()
    tenant_id = await pool.fetchval("SELECT id FROM tenants WHERE slug = $1", TENANT_SLUG)
    if tenant_id is None:
        raise SystemExit(
            f"Демо-тенант {TENANT_SLUG!r} не найден. Сначала запустите: python scripts/seed_demo.py"
        )
    return str(tenant_id)


async def run() -> None:
    tenant_id = await _resolve_tenant_id()
    settings = get_settings()
    gateway = ModelGateway([AnthropicProvider(api_key=settings.anthropic_api_key)])
    publisher = EventPublisher()

    print("=== Три независимых обращения за одну и ту же аварию ===")
    incident_ids = []
    for raw_message in MESSAGES:
        result = await triage(tenant_id, raw_message, gateway=gateway, publisher=publisher)
        incident_ids.append(result.incident_id)
        print(f"[{result.incident_id}] severity={result.severity} topic_tag={result.topic_tag}  {raw_message!r}")

    print("\n=== Incident Coordination Agent ===")
    coordination = await coordinate(tenant_id, gateway=gateway, publisher=publisher, window_minutes=60)
    if not coordination.correlated:
        print("Агент не нашёл корреляции (могло не хватить схожести тем — LLM не детерминирован).")
        return
    for group in coordination.groups:
        print(f"Master-инцидент: {group.master_incident_id}")
        print(f"В группе: {group.incident_ids}")
        print(f"Статус для публикации: {group.status_summary}")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
