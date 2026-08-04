import statistics
from dataclasses import dataclass

from etiology.data.db.pool import tenant_connection


@dataclass
class TopicCount:
    topic_tag: str | None
    count: int


@dataclass
class ResolutionRate:
    triaged_count: int
    resolved_count: int
    rate: float


@dataclass
class TtfrStats:
    count: int
    avg_seconds: float | None
    median_seconds: float | None


async def top_topics(tenant_id: str, limit: int = 10) -> list[TopicCount]:
    async with tenant_connection(tenant_id) as conn:
        rows = await conn.fetch(
            """
            SELECT payload->>'topic_tag' AS topic_tag, count(*) AS cnt
            FROM events
            WHERE event_type = 'incident.triaged'
            GROUP BY payload->>'topic_tag'
            ORDER BY cnt DESC
            LIMIT $1
            """,
            limit,
        )
    return [TopicCount(topic_tag=row["topic_tag"], count=row["cnt"]) for row in rows]


async def resolution_rate(tenant_id: str) -> ResolutionRate:
    async with tenant_connection(tenant_id) as conn:
        row = await conn.fetchrow(
            """
            SELECT
                (SELECT count(*) FROM events WHERE event_type = 'incident.triaged') AS triaged_count,
                (SELECT count(*) FROM events WHERE event_type = 'incident.resolved') AS resolved_count
            """
        )
    triaged_count = row["triaged_count"]
    resolved_count = row["resolved_count"]
    rate = resolved_count / triaged_count if triaged_count else 0.0
    return ResolutionRate(triaged_count=triaged_count, resolved_count=resolved_count, rate=rate)


async def ttfr_stats(tenant_id: str) -> TtfrStats:
    async with tenant_connection(tenant_id) as conn:
        rows = await conn.fetch(
            """
            WITH triaged AS (
                SELECT aggregate_id, created_at AS triaged_at
                FROM events
                WHERE event_type = 'incident.triaged'
            ),
            first_response AS (
                SELECT e.aggregate_id, min(e.created_at) AS response_at
                FROM events e
                JOIN triaged t ON t.aggregate_id = e.aggregate_id
                WHERE e.event_type <> 'incident.triaged' AND e.created_at >= t.triaged_at
                GROUP BY e.aggregate_id
            )
            SELECT t.triaged_at, f.response_at
            FROM triaged t
            JOIN first_response f ON f.aggregate_id = t.aggregate_id
            """
        )
    deltas = [(row["response_at"] - row["triaged_at"]).total_seconds() for row in rows]
    if not deltas:
        return TtfrStats(count=0, avg_seconds=None, median_seconds=None)
    return TtfrStats(
        count=len(deltas),
        avg_seconds=statistics.mean(deltas),
        median_seconds=statistics.median(deltas),
    )
