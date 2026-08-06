from typing import Callable

from mcp.server.fastmcp import FastMCP

from etiology.agent.model_gateway import ModelGateway
from etiology.domain import knowledge_base
from etiology.domain.analytics import record_csat, resolution_rate, top_topics
from etiology.domain.diagnostics.bug_report_composer import compose
from etiology.domain.diagnostics.diagnostic_collector import collect
from etiology.domain.diagnostics.triage import TriageResult, triage
from etiology.domain.escalation_sync.incident_coordination import coordinate
from etiology.domain.escalation_sync.post_mortem import draft_post_mortem
from etiology.platform_core.approval_gate import ApprovalGate
from etiology.platform_core.event_bus import EventPublisher


def build_server(
    *,
    gateway: ModelGateway,
    publisher: EventPublisher,
    approval_gate: ApprovalGate | None = None,
    triage_fn: Callable = triage,
    collect_fn: Callable = collect,
    compose_fn: Callable = compose,
    coordinate_fn: Callable = coordinate,
    draft_post_mortem_fn: Callable = draft_post_mortem,
    curate_fn: Callable = knowledge_base.curate,
    kb_search: Callable = knowledge_base.search,
    top_topics_fn: Callable = top_topics,
    resolution_rate_fn: Callable = resolution_rate,
    record_csat_fn: Callable = record_csat,
) -> FastMCP:
    """MCP Gateway, server-режим (docs/architecture.md §9.1). Тонкие обёртки над уже
    существующими доменными функциями, без новой бизнес-логики. tenant_id — открытый
    аргумент вызывающей стороны в v1 (нет системы аутентификации внешних клиентов, чтобы
    выводить его из токена) — задокументированное ограничение, не тихая заглушка (см.
    дизайн-спек). Реестр шире исходного §9.1 (только incident.create/kb.search/analytics.query) —
    остальные агенты (Diagnostic Collector, Bug Report Composer, Incident Coordination,
    Post-mortem, Knowledge Curator, Approval Gate) уже реализованы и покрыты тестами,
    но не были подключены к точке входа; отдаём их тем же тонким-обёрточным паттерном,
    ничего в самих агентах не меняя.
    """
    approval_gate = approval_gate or ApprovalGate()
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

    @server.tool(
        name="diagnostic_collect",
        description=(
            "Собрать диагностику по инциденту после triage (Diagnostic Collector). "
            "Принимает результат incident_create как есть."
        ),
    )
    async def diagnostic_collect(
        tenant_id: str,
        raw_message: str,
        incident_id: str,
        severity: str,
        topic_tag: str,
        kb_closable: bool,
        kb_article_id: str | None = None,
    ) -> dict:
        triage_result = TriageResult(
            incident_id=incident_id,
            severity=severity,
            topic_tag=topic_tag,
            kb_closable=kb_closable,
            kb_article_id=kb_article_id,
        )
        result = await collect_fn(tenant_id, raw_message, triage_result, gateway=gateway, publisher=publisher)
        return {
            "incident_id": result.incident_id,
            "outcome": result.outcome,
            "advisory_text": result.advisory_text,
            "matched_command": result.matched_command.command if result.matched_command else None,
            "screenshot_refs": result.screenshot_refs,
            "escalated_to_human": result.escalated_to_human,
        }

    @server.tool(
        name="bug_report_compose",
        description="Собрать техническую спецификацию для разработки по трейлу инцидента (Bug Report Composer)",
    )
    async def bug_report_compose(tenant_id: str, incident_id: str) -> dict:
        result = await compose_fn(tenant_id, incident_id, gateway=gateway, publisher=publisher)
        return {
            "incident_id": result.incident_id,
            "title": result.title,
            "severity": result.severity,
            "environment": result.environment,
            "steps_to_reproduce": result.steps_to_reproduce,
            "expected_behavior": result.expected_behavior,
            "actual_behavior": result.actual_behavior,
            "diagnostic_summary": result.diagnostic_summary,
        }

    @server.tool(
        name="incident_coordinate",
        description="Найти инциденты одного массового сбоя за последнее окно времени и опубликовать статус (Incident Coordination Agent)",
    )
    async def incident_coordinate(tenant_id: str, window_minutes: int = 60) -> dict:
        result = await coordinate_fn(
            tenant_id, gateway=gateway, publisher=publisher, window_minutes=window_minutes
        )
        return {
            "correlated": result.correlated,
            "groups": [
                {
                    "incident_ids": g.incident_ids,
                    "master_incident_id": g.master_incident_id,
                    "status_summary": g.status_summary,
                }
                for g in result.groups
            ],
        }

    @server.tool(
        name="post_mortem_draft",
        description=(
            "Собрать черновик post-mortem по закрытому критическому инциденту и поставить "
            "в очередь Approval Gate (Post-mortem Agent) — публикация только после утверждения человеком"
        ),
    )
    async def post_mortem_draft(tenant_id: str, incident_id: str) -> dict:
        result = await draft_post_mortem_fn(
            tenant_id, incident_id, gateway=gateway, approval_gate=approval_gate, publisher=publisher
        )
        return {
            "incident_id": result.incident_id,
            "approval_id": result.approval_id,
            "title": result.title,
            "timeline": result.timeline,
            "hypotheses": result.hypotheses,
            "root_cause": result.root_cause,
            "impact": result.impact,
            "action_items": result.action_items,
        }

    @server.tool(
        name="kb_curate",
        description=(
            "Проанализировать закрытый инцидент и предложить статью базы знаний, если паттерн "
            "переиспользуемый (Knowledge Curator) — публикация только после утверждения человеком"
        ),
    )
    async def kb_curate(tenant_id: str, incident_id: str) -> dict:
        result = await curate_fn(tenant_id, incident_id, gateway=gateway, approval_gate=approval_gate, publisher=publisher)
        return {
            "incident_id": result.incident_id,
            "proposed": result.proposed,
            "suggestion_id": result.suggestion_id,
            "title": result.title,
            "topic_tag": result.topic_tag,
        }

    @server.tool(
        name="approval_gate_list_pending",
        description="Показать очередь черновиков, ожидающих утверждения человеком (post-mortem, kb_suggestion)",
    )
    async def approval_gate_list_pending(tenant_id: str, object_type: str | None = None) -> dict:
        items = await approval_gate.list_pending(tenant_id, object_type)
        return {
            "items": [
                {
                    "id": i.id,
                    "object_type": i.object_type,
                    "payload": i.payload,
                    "status": i.status,
                    "created_by": i.created_by,
                    "created_at": i.created_at.isoformat(),
                }
                for i in items
            ]
        }

    @server.tool(name="approval_gate_approve", description="Утвердить черновик из очереди Approval Gate")
    async def approval_gate_approve(tenant_id: str, approval_id: str, reviewed_by: str) -> dict:
        await approval_gate.approve(tenant_id, approval_id, reviewed_by)
        return {"approval_id": approval_id, "status": "approved"}

    @server.tool(name="approval_gate_reject", description="Отклонить черновик из очереди Approval Gate")
    async def approval_gate_reject(tenant_id: str, approval_id: str, reviewed_by: str) -> dict:
        await approval_gate.reject(tenant_id, approval_id, reviewed_by)
        return {"approval_id": approval_id, "status": "rejected"}

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

    @server.tool(name="csat_record", description="Записать оценку CSAT клиента по инциденту (1..5)")
    async def csat_record(tenant_id: str, incident_id: str, score: int, comment: str | None = None) -> dict:
        await record_csat_fn(tenant_id, incident_id, score, publisher, comment=comment)
        return {"incident_id": incident_id, "recorded": True}

    return server
