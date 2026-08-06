#!/usr/bin/env python3
"""Демо-прогон v1 vertical slice: Channel (CLI) -> Triage Agent ->
Diagnostic Collector -> Event Store (docs/architecture.md, CLAUDE.md).

Использует реальный AnthropicProvider — как build_app() в src/etiology/main.py,
но без MCP-обвязки, чтобы результат можно было показать в терминале напрямую.
Перед первым запуском: python scripts/seed_demo.py
"""
import asyncio
import sys

from etiology.agent.model_gateway import ModelGateway
from etiology.agent.model_gateway.providers.anthropic_provider import AnthropicProvider
from etiology.config import get_settings
from etiology.data.db.pool import get_pool
from etiology.domain.analytics import csat_summary, record_csat, resolution_rate, top_topics
from etiology.domain.diagnostics.bug_report_composer import compose
from etiology.domain.diagnostics.diagnostic_collector import collect
from etiology.domain.diagnostics.triage import triage
from etiology.domain.knowledge_base import curate
from etiology.platform_core.approval_gate import ApprovalGate
from etiology.platform_core.event_bus import EventPublisher, EventReader

TENANT_SLUG = "keitaro-demo"


async def _resolve_tenant_id() -> str:
    pool = await get_pool()
    tenant_id = await pool.fetchval("SELECT id FROM tenants WHERE slug = $1", TENANT_SLUG)
    if tenant_id is None:
        raise SystemExit(
            f"Демо-тенант {TENANT_SLUG!r} не найден. Сначала запустите: python scripts/seed_demo.py"
        )
    return str(tenant_id)


async def run(raw_message: str, csat_score: int | None) -> None:
    tenant_id = await _resolve_tenant_id()
    settings = get_settings()
    gateway = ModelGateway([AnthropicProvider(api_key=settings.anthropic_api_key)])
    publisher = EventPublisher()

    print(f"Клиент: {raw_message}\n")

    print("=== Triage Agent ===")
    triage_result = await triage(tenant_id, raw_message, gateway=gateway, publisher=publisher)
    print(f"severity={triage_result.severity}  topic_tag={triage_result.topic_tag}")
    print(f"kb_closable={triage_result.kb_closable}  kb_article_id={triage_result.kb_article_id}")

    print("\n=== Diagnostic Collector ===")
    diag_result = await collect(tenant_id, raw_message, triage_result, gateway=gateway, publisher=publisher)
    print(f"outcome={diag_result.outcome}  escalated_to_human={diag_result.escalated_to_human}")
    if diag_result.matched_command:
        print(f"matched_command: {diag_result.matched_command.command}")
    print(f"\nТекст клиенту:\n{diag_result.advisory_text}")

    if diag_result.outcome == "needs_bug_report":
        print("\n=== Bug Report Composer ===")
        bug_report = await compose(tenant_id, triage_result.incident_id, gateway=gateway, publisher=publisher)
        print(f"title: {bug_report.title}")
        print(f"environment: {bug_report.environment}")
        print("steps_to_reproduce:")
        for step in bug_report.steps_to_reproduce:
            print(f"  - {step}")
        print(f"diagnostic_summary: {bug_report.diagnostic_summary}")

        print("\n=== Knowledge Curator ===")
        approval_gate = ApprovalGate()
        curator_result = await curate(
            tenant_id, triage_result.incident_id, gateway=gateway, approval_gate=approval_gate, publisher=publisher
        )
        if curator_result.proposed:
            print(f"Предложена статья KB: {curator_result.title!r} (approval_id={curator_result.suggestion_id})")
            print("Черновик ждёт утверждения человеком в Approval Gate — не публикуется автоматически.")
        else:
            print("Curator решил не предлагать новую статью (не переиспользуемый паттерн или уже есть дубликат).")

    print("\n=== Event Store (audit trail инцидента) ===")
    reader = EventReader()
    events = await reader.read_aggregate_events(tenant_id, "incident", triage_result.incident_id)
    for event in events:
        print(f"[{event.created_at:%H:%M:%S}] {event.event_type}")

    if csat_score is not None:
        print("\n=== CSAT ===")
        await record_csat(tenant_id, triage_result.incident_id, csat_score, publisher, comment=None)
        summary = await csat_summary(tenant_id)
        print(f"Оценка клиента записана: {csat_score}/5")
        print(f"Сводка по тенанту: count={summary.count}  avg_score={summary.avg_score}")

    print("\n=== Analytics (read-model поверх Event Store) ===")
    topics = await top_topics(tenant_id)
    rate = await resolution_rate(tenant_id)
    print("Топ тем:")
    for t in topics:
        print(f"  {t.topic_tag}: {t.count}")
    print(
        f"Resolution rate: {rate.resolved_count}/{rate.triaged_count} "
        f"({rate.rate:.0%}) закрыто по базе знаний без эскалации"
    )


def main() -> None:
    if len(sys.argv) < 2:
        print(
            'Использование: python scripts/demo.py "текст обращения клиента" [csat_score 1..5]',
            file=sys.stderr,
        )
        sys.exit(1)
    csat_score = int(sys.argv[2]) if len(sys.argv) > 2 else None
    asyncio.run(run(sys.argv[1], csat_score))


if __name__ == "__main__":
    main()
