from typing import Callable

from mcp.server.fastmcp import FastMCP

from etiology.agent.model_gateway import ModelGateway
from etiology.domain import knowledge_base
from etiology.domain.analytics import resolution_rate, top_topics
from etiology.domain.diagnostics.triage import triage
from etiology.platform_core.event_bus import EventPublisher


def build_server(
    *,
    gateway: ModelGateway,
    publisher: EventPublisher,
    triage_fn: Callable = triage,
    kb_search: Callable = knowledge_base.search,
    top_topics_fn: Callable = top_topics,
    resolution_rate_fn: Callable = resolution_rate,
) -> FastMCP:
    """MCP Gateway, server-режим (docs/architecture.md §9.1). Три инструмента ровно по
    зафиксированному реестру — тонкие обёртки над уже существующими функциями, без новой
    бизнес-логики. tenant_id — открытый аргумент вызывающей стороны в v1 (нет системы
    аутентификации внешних клиентов, чтобы выводить его из токена) — задокументированное
    ограничение, не тихая заглушка (см. дизайн-спек).
    """
    server = FastMCP("etiology")

    @server.tool(
        name="incident_create",
        description="Классифицировать сырое обращение клиента и создать инцидент (Triage Agent)",
    )
    async def incident_create(tenant_id: str, raw_message: str) -> dict:
        result = await triage_fn(tenant_id, raw_message, gateway=gateway, publisher=publisher)
        return {
            "incident_id": result.incident_id,
            "severity": result.severity,
            "topic_tag": result.topic_tag,
            "kb_closable": result.kb_closable,
            "kb_article_id": result.kb_article_id,
        }

    @server.tool(name="knowledge_base_search", description="Поиск по базе знаний тенанта")
    async def knowledge_base_search(tenant_id: str, query: str) -> dict:
        articles = await kb_search(tenant_id, query)
        return {
            "articles": [
                {"id": a.id, "title": a.title, "topic_tag": a.topic_tag} for a in articles
            ]
        }

    @server.tool(
        name="analytics_query",
        description="Сводная аналитика тенанта: топ тем и доля self-service resolution",
    )
    async def analytics_query(tenant_id: str) -> dict:
        topics = await top_topics_fn(tenant_id)
        rate = await resolution_rate_fn(tenant_id)
        return {
            "top_topics": [{"topic_tag": t.topic_tag, "count": t.count} for t in topics],
            "resolution_rate": {
                "triaged_count": rate.triaged_count,
                "resolved_count": rate.resolved_count,
                "rate": rate.rate,
            },
        }

    return server
