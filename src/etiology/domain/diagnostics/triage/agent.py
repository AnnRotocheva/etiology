import json
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from etiology.agent.model_gateway import ModelGateway, ModelMessage, ModelRequest, ModelTier
from etiology.domain.knowledge_base import KbArticle
from etiology.domain.knowledge_base import search as kb_search_default
from etiology.platform_core.event_bus import EventPublisher


class TriageClassificationError(RuntimeError):
    pass


@dataclass
class TriageResult:
    incident_id: str
    severity: str
    topic_tag: str
    kb_closable: bool
    kb_article_id: str | None


class _Classification(BaseModel):
    severity: Literal["critical", "high", "medium", "low"]
    topic_tag: str
    kb_closable: bool
    kb_article_id: str | None = None
    reasoning: str


def _build_prompt(raw_message: str, articles: list[KbArticle]) -> tuple[str, str]:
    system = (
        "Ты — Triage Agent службы поддержки Keitaro. Классифицируй обращение клиента. "
        "Верни ТОЛЬКО JSON-объект без пояснений и без markdown-разметки, со строго такими полями: "
        '{"severity": "critical|high|medium|low", "topic_tag": "краткий тег темы", '
        '"kb_closable": true|false, "kb_article_id": "id статьи из списка ниже или null", '
        '"reasoning": "короткое обоснование"}. '
        "kb_closable=true и kb_article_id разрешены только если одна из предложенных ниже статей "
        "действительно решает проблему клиента. Если подходящей статьи нет — kb_closable=false, kb_article_id=null."
    )
    if articles:
        articles_block = "\n".join(
            f"- id={a.id} topic={a.topic_tag!r} title={a.title!r}\n  {a.body[:300]}" for a in articles
        )
    else:
        articles_block = "(статей не найдено)"
    user = f"Сообщение клиента:\n{raw_message}\n\nСтатьи базы знаний:\n{articles_block}"
    return system, user


def _parse_classification(text: str, known_article_ids: set[str]) -> _Classification:
    data = json.loads(text)
    classification = _Classification.model_validate(data)
    if classification.kb_article_id is not None and classification.kb_article_id not in known_article_ids:
        raise ValueError(f"kb_article_id {classification.kb_article_id!r} не входит в список найденных статей")
    return classification


async def triage(
    tenant_id: str,
    raw_message: str,
    *,
    gateway: ModelGateway,
    publisher: EventPublisher,
    kb_search: Callable[[str, str], Awaitable[list[KbArticle]]] = kb_search_default,
) -> TriageResult:
    articles = await kb_search(tenant_id, raw_message)
    known_ids = {a.id for a in articles}
    system, user = _build_prompt(raw_message, articles)
    messages = [ModelMessage(role="user", content=user)]

    classification: _Classification | None = None
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
        response = await gateway.complete(ModelRequest(tier=ModelTier.FAST, messages=messages, system=system))
        try:
            classification = _parse_classification(response.content, known_ids)
            break
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            last_error = exc

    if classification is None:
        raise TriageClassificationError(f"Не удалось получить валидную классификацию после 2 попыток: {last_error}")

    incident_id = str(uuid4())
    await publisher.publish(
        tenant_id=tenant_id,
        event_type="incident.triaged",
        aggregate_type="incident",
        aggregate_id=incident_id,
        payload={
            "raw_message": raw_message,
            "severity": classification.severity,
            "topic_tag": classification.topic_tag,
            "kb_closable": classification.kb_closable,
            "kb_article_id": classification.kb_article_id,
            "reasoning": classification.reasoning,
        },
        metadata={"model_provider": response.provider, "model": response.model},
    )

    return TriageResult(
        incident_id=incident_id,
        severity=classification.severity,
        topic_tag=classification.topic_tag,
        kb_closable=classification.kb_closable,
        kb_article_id=classification.kb_article_id,
    )
