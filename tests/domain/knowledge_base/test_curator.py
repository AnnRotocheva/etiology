import json
from datetime import datetime, timezone

import pytest

from etiology.agent.model_gateway import ModelGateway, ModelRequest, ModelResponse, ModelTier
from etiology.agent.model_gateway.base import ModelProvider
from etiology.domain.knowledge_base import CurationError, curate
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


class FakeApprovalGate:
    def __init__(self):
        self.submissions = []

    async def submit(self, tenant_id, object_type, payload, created_by):
        suggestion_id = f"suggestion-{len(self.submissions) + 1}"
        self.submissions.append(
            dict(tenant_id=tenant_id, object_type=object_type, payload=payload, created_by=created_by)
        )
        return suggestion_id


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


def _trail_with_bug_report():
    return [
        StoredEvent(
            event_type="incident.triaged",
            payload={"raw_message": "msg", "topic_tag": "tracking"},
            metadata={}, created_at=_now(),
        ),
        StoredEvent(event_type="incident.needs_bug_report", payload={}, metadata={}, created_at=_now()),
        StoredEvent(
            event_type="bug_report.created",
            payload={"title": "t", "diagnostic_summary": "s", "actual_behavior": "a"},
            metadata={}, created_at=_now(),
        ),
    ]


async def _read_bug_report_trail(tenant_id, aggregate_type, aggregate_id):
    return _trail_with_bug_report()


async def _read_empty_trail(tenant_id, aggregate_type, aggregate_id):
    return []


async def _no_existing_articles(tenant_id, query):
    return []


def _decision_json(should_propose=True):
    return json.dumps(
        {
            "should_propose": should_propose,
            "title": "Заголовок статьи" if should_propose else None,
            "body": "Тело статьи" if should_propose else None,
            "topic_tag": "tracking" if should_propose else None,
            "reasoning": "обоснование",
        }
    )


async def test_curate_proposes_and_publishes_suggestion():
    provider = FakeProvider("fake", [_decision_json(should_propose=True)])
    gateway = ModelGateway([provider])
    approval_gate = FakeApprovalGate()
    publisher = FakePublisher()

    result = await curate(
        "tenant-1", "incident-1",
        gateway=gateway, approval_gate=approval_gate, publisher=publisher,
        read_aggregate_events=_read_bug_report_trail, kb_search=_no_existing_articles,
    )

    assert result.proposed is True
    assert result.title == "Заголовок статьи"
    assert len(approval_gate.submissions) == 1
    assert approval_gate.submissions[0]["payload"]["source_incident_id"] == "incident-1"
    assert len(publisher.calls) == 1
    assert publisher.calls[0]["event_type"] == "kb_suggestion.created"


async def test_curate_does_not_submit_when_model_declines():
    provider = FakeProvider("fake", [_decision_json(should_propose=False)])
    gateway = ModelGateway([provider])
    approval_gate = FakeApprovalGate()
    publisher = FakePublisher()

    result = await curate(
        "tenant-1", "incident-1",
        gateway=gateway, approval_gate=approval_gate, publisher=publisher,
        read_aggregate_events=_read_bug_report_trail, kb_search=_no_existing_articles,
    )

    assert result.proposed is False
    assert approval_gate.submissions == []
    assert publisher.calls == []


async def test_curate_raises_when_incident_not_closed():
    provider = FakeProvider("fake", [_decision_json()])
    gateway = ModelGateway([provider])
    approval_gate = FakeApprovalGate()
    publisher = FakePublisher()

    with pytest.raises(CurationError):
        await curate(
            "tenant-1", "incident-1",
            gateway=gateway, approval_gate=approval_gate, publisher=publisher,
            read_aggregate_events=_read_empty_trail, kb_search=_no_existing_articles,
        )

    assert provider.calls == 0
    assert approval_gate.submissions == []


async def test_curate_retries_once_on_malformed_json_then_succeeds():
    provider = FakeProvider("fake", ["не json", _decision_json(should_propose=True)])
    gateway = ModelGateway([provider])
    approval_gate = FakeApprovalGate()
    publisher = FakePublisher()

    result = await curate(
        "tenant-1", "incident-1",
        gateway=gateway, approval_gate=approval_gate, publisher=publisher,
        read_aggregate_events=_read_bug_report_trail, kb_search=_no_existing_articles,
    )

    assert provider.calls == 2
    assert result.proposed is True


async def test_curate_raises_after_two_malformed_responses():
    provider = FakeProvider("fake", ["не json", "тоже не json"])
    gateway = ModelGateway([provider])
    approval_gate = FakeApprovalGate()
    publisher = FakePublisher()

    with pytest.raises(CurationError):
        await curate(
            "tenant-1", "incident-1",
            gateway=gateway, approval_gate=approval_gate, publisher=publisher,
            read_aggregate_events=_read_bug_report_trail, kb_search=_no_existing_articles,
        )

    assert provider.calls == 2
    assert approval_gate.submissions == []
