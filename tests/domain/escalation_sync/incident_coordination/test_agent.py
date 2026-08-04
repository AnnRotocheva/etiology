import json
from datetime import datetime, timezone

import pytest

from etiology.agent.model_gateway import ModelGateway, ModelRequest, ModelResponse, ModelTier
from etiology.agent.model_gateway.base import ModelProvider
from etiology.domain.escalation_sync.incident_coordination import CoordinationError, coordinate
from etiology.platform_core.event_bus import StoredEvent


class FakeProvider(ModelProvider):
    def __init__(self, name: str, responses: list[str]):
        self.name = name
        self._responses = list(responses)
        self.calls = 0

    def supports_tier(self, tier: ModelTier) -> bool:
        return tier == ModelTier.STANDARD

    async def complete(self, request: ModelRequest) -> ModelResponse:
        content = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return ModelResponse(
            content=content, stop_reason="end_turn", provider=self.name,
            model="fake-model", input_tokens=1, output_tokens=1,
        )


class FakePublisher:
    def __init__(self):
        self.calls = []

    async def publish(self, tenant_id, event_type, aggregate_type, aggregate_id, payload, metadata=None):
        self.calls.append(
            dict(
                tenant_id=tenant_id, event_type=event_type, aggregate_type=aggregate_type,
                aggregate_id=aggregate_id, payload=payload, metadata=metadata,
            )
        )


def _now():
    return datetime.now(timezone.utc)


def _triaged(incident_id, topic_tag):
    return StoredEvent(
        aggregate_id=incident_id, event_type="incident.triaged",
        payload={"topic_tag": topic_tag, "severity": "high", "raw_message": "msg"},
        metadata={}, created_at=_now(),
    )


async def _one_incident(tenant_id, event_type, since=None):
    return [_triaged("incident-1", "tracking")]


async def _no_incidents(tenant_id, event_type, since=None):
    return []


def _two_incidents():
    return [_triaged("incident-1", "tracking"), _triaged("incident-2", "tracking")]


async def _read_two_incidents(tenant_id, event_type, since=None):
    return _two_incidents()


def _correlation_json(master="incident-1"):
    return json.dumps(
        {
            "groups": [
                {"incident_ids": ["incident-1", "incident-2"], "master_incident_id": master, "status_summary": "сводка"}
            ],
            "reasoning": "обоснование",
        }
    )


def _no_correlation_json():
    return json.dumps({"groups": [], "reasoning": "не связаны"})


async def test_coordinate_short_circuits_with_fewer_than_two_incidents():
    provider = FakeProvider("fake", [])
    gateway = ModelGateway([provider])
    publisher = FakePublisher()

    result = await coordinate(
        "tenant-1", gateway=gateway, publisher=publisher, read_events_by_type=_one_incident
    )

    assert result.correlated is False
    assert result.groups == []
    assert provider.calls == 0
    assert publisher.calls == []


async def test_coordinate_publishes_merge_and_status_for_correlated_group():
    provider = FakeProvider("fake", [_correlation_json(master="incident-1")])
    gateway = ModelGateway([provider])
    publisher = FakePublisher()

    result = await coordinate(
        "tenant-1", gateway=gateway, publisher=publisher, read_events_by_type=_read_two_incidents
    )

    assert result.correlated is True
    assert result.groups[0].master_incident_id == "incident-1"
    assert len(publisher.calls) == 2
    merged = next(c for c in publisher.calls if c["event_type"] == "incident.merged")
    status = next(c for c in publisher.calls if c["event_type"] == "incident.status_published")
    assert merged["aggregate_id"] == "incident-2"
    assert merged["payload"]["merged_into"] == "incident-1"
    assert status["aggregate_id"] == "incident-1"


async def test_coordinate_returns_no_correlation_without_publishing():
    provider = FakeProvider("fake", [_no_correlation_json()])
    gateway = ModelGateway([provider])
    publisher = FakePublisher()

    result = await coordinate(
        "tenant-1", gateway=gateway, publisher=publisher, read_events_by_type=_read_two_incidents
    )

    assert result.correlated is False
    assert publisher.calls == []


async def test_coordinate_retries_when_master_incident_id_not_in_group():
    provider = FakeProvider(
        "fake", [_correlation_json(master="incident-does-not-exist"), _correlation_json(master="incident-1")]
    )
    gateway = ModelGateway([provider])
    publisher = FakePublisher()

    result = await coordinate(
        "tenant-1", gateway=gateway, publisher=publisher, read_events_by_type=_read_two_incidents
    )

    assert provider.calls == 2
    assert result.groups[0].master_incident_id == "incident-1"


async def test_coordinate_raises_after_two_malformed_responses():
    provider = FakeProvider("fake", ["не json", "тоже не json"])
    gateway = ModelGateway([provider])
    publisher = FakePublisher()

    with pytest.raises(CoordinationError):
        await coordinate(
            "tenant-1", gateway=gateway, publisher=publisher, read_events_by_type=_read_two_incidents
        )

    assert provider.calls == 2
    assert publisher.calls == []
