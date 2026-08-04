from dataclasses import dataclass

from etiology.data.db.pool import tenant_connection
from etiology.platform_core.event_bus import EventPublisher


@dataclass
class CsatSummary:
    count: int
    avg_score: float | None


async def record_csat(
    tenant_id: str,
    incident_id: str,
    score: int,
    publisher: EventPublisher,
    comment: str | None = None,
) -> None:
    if not 1 <= score <= 5:
        raise ValueError(f"score должен быть в диапазоне 1..5, получено {score}")
    await publisher.publish(
        tenant_id=tenant_id,
        event_type="csat.recorded",
        aggregate_type="incident",
        aggregate_id=incident_id,
        payload={"score": score, "comment": comment},
    )


async def csat_summary(tenant_id: str) -> CsatSummary:
    async with tenant_connection(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT (payload->>'score')::int AS score FROM events WHERE event_type = 'csat.recorded'"
        )
    scores = [row["score"] for row in rows]
    if not scores:
        return CsatSummary(count=0, avg_score=None)
    return CsatSummary(count=len(scores), avg_score=sum(scores) / len(scores))
