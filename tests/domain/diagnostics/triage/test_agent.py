import json
import uuid

import pytest

from etiology.agent.model_gateway import ModelGateway, ModelRequest, ModelResponse, ModelTier
from etiology.agent.model_gateway.base import ModelProvider
from etiology.domain.diagnostics.triage import TriageClassificationError, triage
from etiology.domain.knowledge_base import KbArticle


class FakeProvider(ModelProvider):
    def __init__(self, name: str, responses: list[str]):
        self.name = name
        self._responses = list(responses)
        self.calls = 0

    def supports_tier(self, tier: ModelTier) -> bool:
        return tier == ModelTier.FAST

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


async def _no_articles(tenant_id, query):
    return []


def _valid_json(kb_article_id=None, kb_closable=False):
    return json.dumps(
        {
            "severity": "high",
            "topic_tag": "billing",
            "kb_closable": kb_closable,
            "kb_article_id": kb_article_id,
            "reasoning": "тестовое обоснование",
        }
    )


async def test_triage_publishes_incident_triaged_event():
    provider = FakeProvider("fake", [_valid_json()])
    gateway = ModelGateway([provider])
    publisher = FakePublisher()

    result = await triage(
        "tenant-1",
        "У меня не работает биллинг",
        gateway=gateway,
        publisher=publisher,
        kb_search=_no_articles,
    )

    assert result.severity == "high"
    assert result.topic_tag == "billing"
    assert result.kb_closable is False
    assert len(publisher.calls) == 1
    call = publisher.calls[0]
    assert call["event_type"] == "incident.triaged"
    assert call["aggregate_type"] == "incident"
    assert call["aggregate_id"] == result.incident_id
    assert call["payload"]["raw_message"] == "У меня не работает биллинг"


async def test_triage_strips_markdown_code_fence_before_parsing():
    fenced = f"```json\n{_valid_json()}\n```"
    provider = FakeProvider("fake", [fenced])
    gateway = ModelGateway([provider])
    publisher = FakePublisher()

    result = await triage(
        "tenant-1",
        "У меня не работает биллинг",
        gateway=gateway,
        publisher=publisher,
        kb_search=_no_articles,
    )

    assert provider.calls == 1
    assert result.severity == "high"


async def test_triage_grounds_kb_closable_in_found_article():
    article = KbArticle(id=str(uuid.uuid4()), kind="known_issue", title="t", body="b", topic_tag="billing")

    async def _with_article(tenant_id, query):
        return [article]

    provider = FakeProvider("fake", [_valid_json(kb_article_id=article.id, kb_closable=True)])
    gateway = ModelGateway([provider])
    publisher = FakePublisher()

    result = await triage(
        "tenant-1",
        "У меня не работает биллинг",
        gateway=gateway,
        publisher=publisher,
        kb_search=_with_article,
    )

    assert result.kb_closable is True
    assert result.kb_article_id == article.id


async def test_triage_retries_once_on_malformed_json_then_succeeds():
    provider = FakeProvider("fake", ["не json", _valid_json()])
    gateway = ModelGateway([provider])
    publisher = FakePublisher()

    result = await triage(
        "tenant-1",
        "У меня не работает биллинг",
        gateway=gateway,
        publisher=publisher,
        kb_search=_no_articles,
    )

    assert provider.calls == 2
    assert result.severity == "high"
    assert len(publisher.calls) == 1


async def test_triage_raises_after_two_malformed_responses():
    provider = FakeProvider("fake", ["не json", "тоже не json"])
    gateway = ModelGateway([provider])
    publisher = FakePublisher()

    with pytest.raises(TriageClassificationError):
        await triage(
            "tenant-1",
            "У меня не работает биллинг",
            gateway=gateway,
            publisher=publisher,
            kb_search=_no_articles,
        )

    assert provider.calls == 2
    assert publisher.calls == []


async def test_triage_rejects_kb_article_id_not_in_search_results():
    unknown_id = str(uuid.uuid4())
    provider = FakeProvider(
        "fake",
        [
            _valid_json(kb_article_id=unknown_id, kb_closable=True),
            _valid_json(),
        ],
    )
    gateway = ModelGateway([provider])
    publisher = FakePublisher()

    result = await triage(
        "tenant-1",
        "У меня не работает биллинг",
        gateway=gateway,
        publisher=publisher,
        kb_search=_no_articles,
    )

    assert provider.calls == 2
    assert result.kb_article_id is None
