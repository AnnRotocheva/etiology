import uuid

from etiology.domain.analytics import resolution_rate, top_topics, ttfr_stats
from etiology.platform_core.event_bus import EventPublisher


async def test_top_topics_counts_by_topic_tag(tenant_id):
    publisher = EventPublisher()
    for topic in ["tracking", "tracking", "billing"]:
        await publisher.publish(
            tenant_id=tenant_id, event_type="incident.triaged", aggregate_type="incident",
            aggregate_id=str(uuid.uuid4()), payload={"topic_tag": topic},
        )

    results = await top_topics(tenant_id, limit=5)

    counts = {r.topic_tag: r.count for r in results}
    assert counts["tracking"] == 2
    assert counts["billing"] == 1


async def test_top_topics_returns_empty_list_when_no_data(tenant_id):
    results = await top_topics(tenant_id)

    assert results == []


async def test_resolution_rate_computes_ratio(tenant_id):
    publisher = EventPublisher()
    incident_a = str(uuid.uuid4())
    incident_b = str(uuid.uuid4())
    await publisher.publish(
        tenant_id=tenant_id, event_type="incident.triaged", aggregate_type="incident",
        aggregate_id=incident_a, payload={},
    )
    await publisher.publish(
        tenant_id=tenant_id, event_type="incident.triaged", aggregate_type="incident",
        aggregate_id=incident_b, payload={},
    )
    await publisher.publish(
        tenant_id=tenant_id, event_type="incident.resolved", aggregate_type="incident",
        aggregate_id=incident_a, payload={},
    )

    rate = await resolution_rate(tenant_id)

    assert rate.triaged_count == 2
    assert rate.resolved_count == 1
    assert rate.rate == 0.5


async def test_resolution_rate_is_zero_without_division_error_when_no_data(tenant_id):
    rate = await resolution_rate(tenant_id)

    assert rate.triaged_count == 0
    assert rate.resolved_count == 0
    assert rate.rate == 0.0


async def test_ttfr_stats_computes_average_and_median(tenant_id):
    publisher = EventPublisher()
    incident_id = str(uuid.uuid4())
    await publisher.publish(
        tenant_id=tenant_id, event_type="incident.triaged", aggregate_type="incident",
        aggregate_id=incident_id, payload={},
    )
    await publisher.publish(
        tenant_id=tenant_id, event_type="incident.resolved", aggregate_type="incident",
        aggregate_id=incident_id, payload={},
    )

    stats = await ttfr_stats(tenant_id)

    assert stats.count == 1
    assert stats.avg_seconds is not None
    assert stats.avg_seconds >= 0
    assert stats.median_seconds == stats.avg_seconds


async def test_ttfr_stats_returns_none_averages_when_no_data(tenant_id):
    stats = await ttfr_stats(tenant_id)

    assert stats.count == 0
    assert stats.avg_seconds is None
    assert stats.median_seconds is None
