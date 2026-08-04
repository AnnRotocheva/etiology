import json
import uuid

from etiology.data.db.pool import tenant_connection
from etiology.platform_core.event_bus import EventPublisher


async def test_publish_inserts_event_row(tenant_id):
    publisher = EventPublisher()
    aggregate_id = str(uuid.uuid4())

    await publisher.publish(
        tenant_id=tenant_id,
        event_type="incident.triaged",
        aggregate_type="incident",
        aggregate_id=aggregate_id,
        payload={"severity": "high"},
        metadata={"model": "fake"},
    )

    async with tenant_connection(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT tenant_id, event_type, aggregate_type, aggregate_id, payload, metadata "
            "FROM events WHERE aggregate_id = $1::uuid",
            aggregate_id,
        )

    assert row is not None
    assert str(row["tenant_id"]) == tenant_id
    assert row["event_type"] == "incident.triaged"
    assert row["aggregate_type"] == "incident"
    assert json.loads(row["payload"]) == {"severity": "high"}
    assert json.loads(row["metadata"]) == {"model": "fake"}
