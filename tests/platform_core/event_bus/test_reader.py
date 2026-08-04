import uuid

from etiology.platform_core.event_bus import EventPublisher, EventReader


async def test_read_aggregate_events_returns_events_in_order(tenant_id):
    publisher = EventPublisher()
    reader = EventReader()
    aggregate_id = str(uuid.uuid4())

    await publisher.publish(
        tenant_id=tenant_id,
        event_type="incident.triaged",
        aggregate_type="incident",
        aggregate_id=aggregate_id,
        payload={"severity": "high"},
        metadata={"model": "fake"},
    )
    await publisher.publish(
        tenant_id=tenant_id,
        event_type="incident.needs_bug_report",
        aggregate_type="incident",
        aggregate_id=aggregate_id,
        payload={"advisory_text": "текст"},
    )

    events = await reader.read_aggregate_events(tenant_id, "incident", aggregate_id)

    assert [e.event_type for e in events] == ["incident.triaged", "incident.needs_bug_report"]
    assert events[0].payload == {"severity": "high"}
    assert events[0].metadata == {"model": "fake"}
    assert events[1].payload == {"advisory_text": "текст"}
    assert events[1].metadata == {}


async def test_read_aggregate_events_returns_empty_list_for_unknown_aggregate(tenant_id):
    reader = EventReader()

    events = await reader.read_aggregate_events(tenant_id, "incident", str(uuid.uuid4()))

    assert events == []
