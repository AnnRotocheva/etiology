import json

from etiology.data.db.pool import tenant_connection


class EventPublisher:
    """Publish-сторона Event Bus (docs/architecture.md §8.2). Только запись
    в Event Store — LISTEN/NOTIFY и outbox добавляются отдельно, когда
    появится первый реальный подписчик.
    """

    async def publish(
        self,
        tenant_id: str,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict,
        metadata: dict | None = None,
    ) -> None:
        async with tenant_connection(tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO events (tenant_id, event_type, aggregate_type, aggregate_id, payload, metadata)
                VALUES ($1::uuid, $2, $3, $4::uuid, $5::jsonb, $6::jsonb)
                """,
                tenant_id,
                event_type,
                aggregate_type,
                aggregate_id,
                json.dumps(payload),
                json.dumps(metadata or {}),
            )
