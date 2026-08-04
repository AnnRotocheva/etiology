import json
from dataclasses import dataclass
from datetime import datetime

from etiology.data.db.pool import tenant_connection


@dataclass
class StoredEvent:
    aggregate_id: str
    event_type: str
    payload: dict
    metadata: dict
    created_at: datetime


class EventReader:
    """Читающая сторона Event Bus (docs/architecture.md §8.2) — дополняет
    publish-сторону (EventPublisher). Используется агентами, которым по
    контракту положено читать историю событий, а не принимать результат
    напрямую от предыдущего шага (Bug Report Composer, Knowledge Curator,
    §4.3, §5), и агентами с межагрегатным доступом (Incident Coordination,
    §6.1 — осознанное исключение из принципа минимального доступа).
    """

    async def read_aggregate_events(
        self, tenant_id: str, aggregate_type: str, aggregate_id: str
    ) -> list[StoredEvent]:
        async with tenant_connection(tenant_id) as conn:
            rows = await conn.fetch(
                """
                SELECT event_type, payload, metadata, created_at
                FROM events
                WHERE aggregate_type = $1 AND aggregate_id = $2::uuid
                ORDER BY created_at ASC
                """,
                aggregate_type,
                aggregate_id,
            )
        return [
            StoredEvent(
                aggregate_id=aggregate_id,
                event_type=row["event_type"],
                payload=json.loads(row["payload"]),
                metadata=json.loads(row["metadata"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def read_events_by_type(
        self, tenant_id: str, event_type: str, since: datetime | None = None
    ) -> list[StoredEvent]:
        async with tenant_connection(tenant_id) as conn:
            if since is None:
                rows = await conn.fetch(
                    "SELECT aggregate_id, event_type, payload, metadata, created_at "
                    "FROM events WHERE event_type = $1 ORDER BY created_at ASC",
                    event_type,
                )
            else:
                rows = await conn.fetch(
                    "SELECT aggregate_id, event_type, payload, metadata, created_at "
                    "FROM events WHERE event_type = $1 AND created_at >= $2 ORDER BY created_at ASC",
                    event_type,
                    since,
                )
        return [
            StoredEvent(
                aggregate_id=str(row["aggregate_id"]),
                event_type=row["event_type"],
                payload=json.loads(row["payload"]),
                metadata=json.loads(row["metadata"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]
