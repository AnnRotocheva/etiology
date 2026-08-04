import json
from datetime import datetime, timezone

import pytest

from etiology.agent.model_gateway import ModelGateway, ModelRequest, ModelResponse, ModelTier
from etiology.agent.model_gateway.base import ModelProvider
from etiology.domain.diagnostics.bug_report_composer import BugReportCompositionError, compose
from etiology.platform_core.event_bus import StoredEvent


class FakeProvider(ModelProvider):
    def __init__(self, name: str, responses: list[str]):
        self.name = name
        self._responses = list(responses)
        self.calls = 0

    def supports_tier(self, tier: ModelTier) -> bool:
        return tier == ModelTier.STRONG

    async def complete(self, request: ModelRequest) -> ModelResponse:
        content = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return ModelResponse(
            content=content,
            stop_reason="end_turn",
            provider=self.name,
            model="fake-model",
            input_tokens=1,
            output_tokens=1,
        )


class FakePublisher:
    def __init__(self):
        self.calls = []

    async def publish(self, tenant_id, event_type, aggregate_type, aggregate_id, payload, metadata=None):
        self.calls.append(
            dict(
                tenant_id=tenant_id,
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                payload=payload,
                metadata=metadata,
            )
        )


def _now():
    return datetime.now(timezone.utc)


def _full_trail():
    return [
        StoredEvent(
            event_type="incident.triaged",
            payload={"raw_message": "Клики не фиксируются", "severity": "high", "topic_tag": "tracking"},
            metadata={},
            created_at=_now(),
        ),
        StoredEvent(
            event_type="incident.needs_bug_report",
            payload={
                "advisory_text": "Нужна эскалация",
                "matched_command": None,
                "screenshot_refs": [],
                "escalated_to_human": True,
            },
            metadata={},
            created_at=_now(),
        ),
    ]


async def _read_full_trail(tenant_id, aggregate_type, aggregate_id):
    return _full_trail()


async def _read_empty_trail(tenant_id, aggregate_type, aggregate_id):
    return []


def _report_json():
    return json.dumps(
        {
            "title": "Клики не фиксируются после обновления",
            "severity": "high",
            "environment": "Keitaro 10.x",
            "steps_to_reproduce": ["Запустить кампанию", "Проверить статистику по клику"],
            "expected_behavior": "Клик фиксируется в статистике",
            "actual_behavior": "Клик не фиксируется",
            "diagnostic_summary": "Нужна эскалация, диагностических команд не найдено",
        }
    )


async def test_compose_publishes_bug_report_created():
    provider = FakeProvider("fake", [_report_json()])
    gateway = ModelGateway([provider])
    publisher = FakePublisher()

    result = await compose(
        "tenant-1", "incident-1",
        gateway=gateway, publisher=publisher, read_aggregate_events=_read_full_trail,
    )

    assert result.incident_id == "incident-1"
    assert result.title == "Клики не фиксируются после обновления"
    assert result.steps_to_reproduce == ["Запустить кампанию", "Проверить статистику по клику"]
    assert len(publisher.calls) == 1
    assert publisher.calls[0]["event_type"] == "bug_report.created"
    assert publisher.calls[0]["aggregate_id"] == "incident-1"
    assert publisher.calls[0]["payload"]["title"] == result.title


async def test_compose_raises_when_no_needs_bug_report_event():
    provider = FakeProvider("fake", [_report_json()])
    gateway = ModelGateway([provider])
    publisher = FakePublisher()

    with pytest.raises(BugReportCompositionError):
        await compose(
            "tenant-1", "incident-2",
            gateway=gateway, publisher=publisher, read_aggregate_events=_read_empty_trail,
        )

    assert publisher.calls == []
    assert provider.calls == 0


async def test_compose_retries_once_on_malformed_json_then_succeeds():
    provider = FakeProvider("fake", ["не json", _report_json()])
    gateway = ModelGateway([provider])
    publisher = FakePublisher()

    result = await compose(
        "tenant-1", "incident-3",
        gateway=gateway, publisher=publisher, read_aggregate_events=_read_full_trail,
    )

    assert provider.calls == 2
    assert result.title == "Клики не фиксируются после обновления"


async def test_compose_raises_after_two_malformed_responses():
    provider = FakeProvider("fake", ["не json", "тоже не json"])
    gateway = ModelGateway([provider])
    publisher = FakePublisher()

    with pytest.raises(BugReportCompositionError):
        await compose(
            "tenant-1", "incident-4",
            gateway=gateway, publisher=publisher, read_aggregate_events=_read_full_trail,
        )

    assert provider.calls == 2
    assert publisher.calls == []
