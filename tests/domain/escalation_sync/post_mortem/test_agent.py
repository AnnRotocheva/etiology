import json
from datetime import datetime, timezone

import pytest

from etiology.agent.model_gateway import ModelGateway, ModelRequest, ModelResponse, ModelTier
from etiology.agent.model_gateway.base import ModelProvider
from etiology.domain.escalation_sync.post_mortem import PostMortemError, draft_post_mortem
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
            content=content, stop_reason="end_turn", provider=self.name,
            model="fake-model", input_tokens=1, output_tokens=1,
        )


class FakeApprovalGate:
    def __init__(self):
        self.submissions = []

    async def submit(self, tenant_id, object_type, payload, created_by):
        approval_id = f"approval-{len(self.submissions) + 1}"
        self.submissions.append(
            dict(tenant_id=tenant_id, object_type=object_type, payload=payload, created_by=created_by)
        )
        return approval_id


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


def _closed_critical_trail():
    return [
        StoredEvent(
            aggregate_id="incident-1", event_type="incident.triaged",
            payload={"raw_message": "всё упало", "severity": "critical", "topic_tag": "outage"},
            metadata={}, created_at=_now(),
        ),
        StoredEvent(
            aggregate_id="incident-1", event_type="incident.needs_bug_report",
            payload={}, metadata={}, created_at=_now(),
        ),
        StoredEvent(
            aggregate_id="incident-1", event_type="bug_report.created",
            payload={"title": "t", "diagnostic_summary": "s"}, metadata={}, created_at=_now(),
        ),
    ]


def _closed_non_critical_trail():
    trail = _closed_critical_trail()
    trail[0].payload["severity"] = "medium"
    return trail


def _unclosed_critical_trail():
    return _closed_critical_trail()[:1]


async def _read_closed_critical(tenant_id, aggregate_type, aggregate_id):
    return _closed_critical_trail()


async def _read_closed_non_critical(tenant_id, aggregate_type, aggregate_id):
    return _closed_non_critical_trail()


async def _read_unclosed_critical(tenant_id, aggregate_type, aggregate_id):
    return _unclosed_critical_trail()


def _report_json():
    return json.dumps(
        {
            "title": "Полный сбой сервиса",
            "timeline": ["10:00 — инцидент зафиксирован", "10:05 — эскалация на разработку"],
            "hypotheses": ["Перегрузка после релиза"],
            "root_cause": "Причина не подтверждена, требуется дальнейшее расследование",
            "impact": "Все кампании тенанта недоступны",
            "action_items": ["Добавить алерт на перегрузку"],
        }
    )


async def test_draft_post_mortem_submits_and_publishes():
    provider = FakeProvider("fake", [_report_json()])
    gateway = ModelGateway([provider])
    approval_gate = FakeApprovalGate()
    publisher = FakePublisher()

    result = await draft_post_mortem(
        "tenant-1", "incident-1",
        gateway=gateway, approval_gate=approval_gate, publisher=publisher,
        read_aggregate_events=_read_closed_critical,
    )

    assert result.incident_id == "incident-1"
    assert result.title == "Полный сбой сервиса"
    assert len(approval_gate.submissions) == 1
    assert approval_gate.submissions[0]["object_type"] == "post_mortem"
    assert len(publisher.calls) == 1
    assert publisher.calls[0]["event_type"] == "post_mortem.drafted"
    assert publisher.calls[0]["aggregate_id"] == "incident-1"


async def test_draft_post_mortem_rejects_non_critical_incident():
    provider = FakeProvider("fake", [_report_json()])
    gateway = ModelGateway([provider])
    approval_gate = FakeApprovalGate()
    publisher = FakePublisher()

    with pytest.raises(PostMortemError):
        await draft_post_mortem(
            "tenant-1", "incident-1",
            gateway=gateway, approval_gate=approval_gate, publisher=publisher,
            read_aggregate_events=_read_closed_non_critical,
        )

    assert provider.calls == 0
    assert approval_gate.submissions == []


async def test_draft_post_mortem_rejects_unclosed_incident():
    provider = FakeProvider("fake", [_report_json()])
    gateway = ModelGateway([provider])
    approval_gate = FakeApprovalGate()
    publisher = FakePublisher()

    with pytest.raises(PostMortemError):
        await draft_post_mortem(
            "tenant-1", "incident-1",
            gateway=gateway, approval_gate=approval_gate, publisher=publisher,
            read_aggregate_events=_read_unclosed_critical,
        )

    assert provider.calls == 0
    assert approval_gate.submissions == []


async def test_draft_post_mortem_retries_once_on_malformed_json_then_succeeds():
    provider = FakeProvider("fake", ["не json", _report_json()])
    gateway = ModelGateway([provider])
    approval_gate = FakeApprovalGate()
    publisher = FakePublisher()

    result = await draft_post_mortem(
        "tenant-1", "incident-1",
        gateway=gateway, approval_gate=approval_gate, publisher=publisher,
        read_aggregate_events=_read_closed_critical,
    )

    assert provider.calls == 2
    assert result.title == "Полный сбой сервиса"


async def test_draft_post_mortem_raises_after_two_malformed_responses():
    provider = FakeProvider("fake", ["не json", "тоже не json"])
    gateway = ModelGateway([provider])
    approval_gate = FakeApprovalGate()
    publisher = FakePublisher()

    with pytest.raises(PostMortemError):
        await draft_post_mortem(
            "tenant-1", "incident-1",
            gateway=gateway, approval_gate=approval_gate, publisher=publisher,
            read_aggregate_events=_read_closed_critical,
        )

    assert provider.calls == 2
    assert approval_gate.submissions == []
