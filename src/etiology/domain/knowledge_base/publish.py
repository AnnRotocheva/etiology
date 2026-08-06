from dataclasses import dataclass

from etiology.data.db.pool import tenant_connection
from etiology.platform_core.approval_gate import ApprovalGate
from etiology.platform_core.event_bus import EventPublisher


class PublishError(RuntimeError):
    pass


@dataclass
class PublishedArticle:
    id: str
    title: str
    topic_tag: str | None


async def publish_approved(
    tenant_id: str,
    approval_id: str,
    *,
    approval_gate: ApprovalGate,
    publisher: EventPublisher,
) -> PublishedArticle:
    """Материализует утверждённое человеком предложение Knowledge Curator'а (kb_suggestion)
    в реальную статью knowledge_base_articles. Approval Gate сам не содержит доменной логики
    (docs/architecture.md §8.1, "просто очередь pending-объектов + статус") — публикация
    конкретного типа объекта делается здесь, в его домене, не в самом гейте."""
    item = await approval_gate.get(tenant_id, approval_id)
    if item is None:
        raise PublishError(f"Approval {approval_id!r} не найден")
    if item.object_type != "kb_suggestion":
        raise PublishError(
            f"Approval {approval_id!r} имеет тип {item.object_type!r}, ожидался kb_suggestion"
        )
    if item.status != "approved":
        raise PublishError(f"Approval {approval_id!r} не утверждён (статус={item.status!r})")

    title = item.payload["title"]
    body = item.payload["body"]
    topic_tag = item.payload.get("topic_tag")

    async with tenant_connection(tenant_id) as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO knowledge_base_articles (tenant_id, kind, title, body, topic_tag)
            VALUES ($1::uuid, 'known_issue', $2, $3, $4)
            RETURNING id
            """,
            tenant_id,
            title,
            body,
            topic_tag,
        )
    article_id = str(row["id"])

    await publisher.publish(
        tenant_id=tenant_id,
        event_type="kb_article.published",
        aggregate_type="kb_article",
        aggregate_id=article_id,
        payload={"title": title, "topic_tag": topic_tag, "source_approval_id": approval_id},
    )

    return PublishedArticle(id=article_id, title=title, topic_tag=topic_tag)
