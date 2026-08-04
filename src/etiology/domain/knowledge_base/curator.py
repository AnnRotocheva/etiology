import json
from dataclasses import dataclass
from typing import Awaitable, Callable

from pydantic import BaseModel, ValidationError

from etiology.agent.model_gateway import ModelGateway, ModelMessage, ModelRequest, ModelTier
from etiology.platform_core.approval_gate import ApprovalGate
from etiology.platform_core.event_bus import EventPublisher, EventReader, StoredEvent

from .search import KbArticle
from .search import search as kb_search_default


class CurationError(RuntimeError):
    pass


@dataclass
class CuratorResult:
    incident_id: str
    proposed: bool
    suggestion_id: str | None
    title: str | None
    topic_tag: str | None


class _CuratorDecision(BaseModel):
    should_propose: bool
    title: str | None = None
    body: str | None = None
    topic_tag: str | None = None
    reasoning: str


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        _, _, rest = text.partition("\n")
        text = rest.removesuffix("```").strip()
    return text


def _find_latest(events: list[StoredEvent], event_type: str) -> StoredEvent | None:
    matches = [e for e in events if e.event_type == event_type]
    return matches[-1] if matches else None


def _build_prompt(
    triaged: StoredEvent | None, terminal: StoredEvent, existing: list[KbArticle]
) -> tuple[str, str]:
    system = (
        "Ты — Knowledge Curator службы поддержки Keitaro. По закрытому инциденту реши, стоит ли "
        "предложить новую статью базы знаний — только если случай представляет переиспользуемый "
        "паттерн (то же самое может повториться у других клиентов), а не одноразовую специфику "
        "этого клиента. Если в списке существующих статей ниже уже есть покрывающая эту тему — "
        "откажись предлагать дубликат. Верни ТОЛЬКО JSON-объект без markdown-разметки: "
        '{"should_propose": true|false, "title": "..." или null, "body": "..." или null, '
        '"topic_tag": "..." или null, "reasoning": "..."}.'
    )
    parts = []
    if triaged is not None:
        parts.append(
            f"Исходное сообщение клиента: {triaged.payload.get('raw_message')}\n"
            f"Тема (Triage): {triaged.payload.get('topic_tag')}"
        )
    if terminal.event_type == "bug_report.created":
        parts.append(
            f"Итог — создан bug report: {terminal.payload.get('title')}\n"
            f"Диагностическая сводка: {terminal.payload.get('diagnostic_summary')}\n"
            f"Фактическое поведение: {terminal.payload.get('actual_behavior')}"
        )
    else:
        parts.append(f"Итог — инцидент решён по базе знаний: {terminal.payload.get('advisory_text')}")
    if existing:
        existing_block = "\n".join(f"- id={a.id} topic={a.topic_tag!r} title={a.title!r}" for a in existing)
    else:
        existing_block = "(существующих статей по теме не найдено)"
    parts.append(f"Существующие статьи базы знаний:\n{existing_block}")
    user = "\n\n".join(parts)
    return system, user


def _parse_decision(text: str) -> _CuratorDecision:
    data = json.loads(_strip_code_fence(text))
    return _CuratorDecision.model_validate(data)


async def curate(
    tenant_id: str,
    incident_id: str,
    *,
    gateway: ModelGateway,
    approval_gate: ApprovalGate,
    publisher: EventPublisher,
    read_aggregate_events: Callable[[str, str, str], Awaitable[list[StoredEvent]]] = (
        EventReader().read_aggregate_events
    ),
    kb_search: Callable[[str, str], Awaitable[list[KbArticle]]] = kb_search_default,
) -> CuratorResult:
    events = await read_aggregate_events(tenant_id, "incident", incident_id)
    terminal = _find_latest(events, "bug_report.created") or _find_latest(events, "incident.resolved")
    if terminal is None:
        raise CurationError(
            f"Для инцидента {incident_id!r} не найдено ни bug_report.created, ни incident.resolved — "
            "Curator анализирует только закрытые инциденты"
        )
    triaged = _find_latest(events, "incident.triaged")

    search_query = (triaged.payload.get("topic_tag") if triaged else None) or terminal.event_type
    existing = await kb_search(tenant_id, search_query)

    system, user = _build_prompt(triaged, terminal, existing)
    messages = [ModelMessage(role="user", content=user)]

    decision: _CuratorDecision | None = None
    last_error: Exception | None = None
    response = None
    for _attempt in range(2):
        if last_error is not None:
            messages.append(ModelMessage(role="assistant", content=response.content))
            messages.append(
                ModelMessage(
                    role="user",
                    content=f"Ответ не прошёл валидацию: {last_error}. Верни только исправленный JSON-объект.",
                )
            )
        response = await gateway.complete(
            ModelRequest(tier=ModelTier.STANDARD, messages=messages, system=system, max_tokens=1536)
        )
        try:
            decision = _parse_decision(response.content)
            break
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = exc

    if decision is None:
        raise CurationError(f"Не удалось получить решение куратора после 2 попыток: {last_error}")

    if not decision.should_propose:
        return CuratorResult(
            incident_id=incident_id, proposed=False, suggestion_id=None, title=None, topic_tag=None
        )

    suggestion_id = await approval_gate.submit(
        tenant_id,
        "kb_suggestion",
        {
            "title": decision.title,
            "body": decision.body,
            "topic_tag": decision.topic_tag,
            "source_incident_id": incident_id,
            "reasoning": decision.reasoning,
        },
        created_by="knowledge_curator_agent",
    )

    await publisher.publish(
        tenant_id=tenant_id,
        event_type="kb_suggestion.created",
        aggregate_type="kb_suggestion",
        aggregate_id=suggestion_id,
        payload={
            "title": decision.title,
            "topic_tag": decision.topic_tag,
            "source_incident_id": incident_id,
        },
    )

    return CuratorResult(
        incident_id=incident_id,
        proposed=True,
        suggestion_id=suggestion_id,
        title=decision.title,
        topic_tag=decision.topic_tag,
    )
