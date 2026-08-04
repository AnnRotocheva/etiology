import json

import pytest

from etiology.agent.model_gateway import ModelGateway, ModelRequest, ModelResponse, ModelTier
from etiology.agent.model_gateway.base import ModelProvider
from etiology.domain.diagnostics.diagnostic_collector import DiagnosticCollectionError, collect
from etiology.domain.diagnostics.diagnostic_collector.command_catalog import DiagnosticCommand
from etiology.domain.diagnostics.diagnostic_collector.screenshots import Screenshot
from etiology.domain.diagnostics.triage import TriageResult
from etiology.domain.knowledge_base import KbArticle


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


async def _no_commands(tenant_id, query):
    return []


async def _no_screenshots(tenant_id, query):
    return []


def _advisory_json(escalated_to_human=True):
    return json.dumps({"advisory_text": "тестовая сводка", "escalated_to_human": escalated_to_human})


async def test_collect_resolves_via_kb_article_without_model_call():
    article = KbArticle(id="article-1", kind="known_issue", title="t", body="Текст решения", topic_tag="billing")

    async def _get_by_id(tenant_id, article_id):
        assert article_id == "article-1"
        return article

    provider = FakeProvider("fake", [])  # не должен быть вызван
    gateway = ModelGateway([provider])
    publisher = FakePublisher()
    triage_result = TriageResult(
        incident_id="incident-1", severity="high", topic_tag="billing",
        kb_closable=True, kb_article_id="article-1",
    )

    result = await collect(
        "tenant-1", "У меня не работает биллинг", triage_result,
        gateway=gateway, publisher=publisher, kb_get_by_id=_get_by_id,
    )

    assert provider.calls == 0
    assert result.outcome == "resolved"
    assert result.advisory_text == "Текст решения"
    assert len(publisher.calls) == 1
    assert publisher.calls[0]["event_type"] == "incident.resolved"
    assert publisher.calls[0]["aggregate_id"] == "incident-1"


async def test_collect_raises_when_kb_closable_but_article_missing():
    async def _get_by_id(tenant_id, article_id):
        return None

    provider = FakeProvider("fake", [])
    gateway = ModelGateway([provider])
    publisher = FakePublisher()
    triage_result = TriageResult(
        incident_id="incident-1", severity="high", topic_tag="billing",
        kb_closable=True, kb_article_id="article-1",
    )

    with pytest.raises(DiagnosticCollectionError):
        await collect(
            "tenant-1", "У меня не работает биллинг", triage_result,
            gateway=gateway, publisher=publisher, kb_get_by_id=_get_by_id,
        )

    assert publisher.calls == []


async def test_collect_escalates_with_matched_command_when_not_kb_closable():
    command = DiagnosticCommand(
        id="cmd-1", scenario="billing_not_tracking", command="tail -n 200 /var/log/x.log",
        environment_version="10.x", is_read_only=True,
    )
    screenshot = Screenshot(id="shot-1", ui_version="10.x", step_description="step", image_ref="ref.png")

    async def _commands(tenant_id, query):
        return [command]

    async def _screenshots(tenant_id, query):
        return [screenshot]

    provider = FakeProvider("fake", [_advisory_json(escalated_to_human=False)])
    gateway = ModelGateway([provider])
    publisher = FakePublisher()
    triage_result = TriageResult(
        incident_id="incident-2", severity="high", topic_tag="billing",
        kb_closable=False, kb_article_id=None,
    )

    result = await collect(
        "tenant-1", "У меня не работает биллинг", triage_result,
        gateway=gateway, publisher=publisher,
        command_search=_commands, screenshot_search=_screenshots,
    )

    assert result.outcome == "needs_bug_report"
    assert result.matched_command == command
    assert result.escalated_to_human is False
    assert result.screenshot_refs == ["ref.png"]
    assert publisher.calls[0]["event_type"] == "incident.needs_bug_report"
    assert publisher.calls[0]["aggregate_id"] == "incident-2"
    assert publisher.calls[0]["payload"]["matched_command"] == command.command


async def test_collect_forces_escalated_to_human_when_no_command_found():
    provider = FakeProvider("fake", [_advisory_json(escalated_to_human=False)])
    gateway = ModelGateway([provider])
    publisher = FakePublisher()
    triage_result = TriageResult(
        incident_id="incident-3", severity="high", topic_tag="billing",
        kb_closable=False, kb_article_id=None,
    )

    result = await collect(
        "tenant-1", "У меня не работает биллинг", triage_result,
        gateway=gateway, publisher=publisher,
        command_search=_no_commands, screenshot_search=_no_screenshots,
    )

    assert result.matched_command is None
    assert result.escalated_to_human is True


async def test_collect_retries_once_on_malformed_json_then_succeeds():
    provider = FakeProvider("fake", ["не json", _advisory_json()])
    gateway = ModelGateway([provider])
    publisher = FakePublisher()
    triage_result = TriageResult(
        incident_id="incident-4", severity="high", topic_tag="billing",
        kb_closable=False, kb_article_id=None,
    )

    result = await collect(
        "tenant-1", "У меня не работает биллинг", triage_result,
        gateway=gateway, publisher=publisher,
        command_search=_no_commands, screenshot_search=_no_screenshots,
    )

    assert provider.calls == 2
    assert result.outcome == "needs_bug_report"


async def test_collect_raises_after_two_malformed_responses():
    provider = FakeProvider("fake", ["не json", "тоже не json"])
    gateway = ModelGateway([provider])
    publisher = FakePublisher()
    triage_result = TriageResult(
        incident_id="incident-5", severity="high", topic_tag="billing",
        kb_closable=False, kb_article_id=None,
    )

    with pytest.raises(DiagnosticCollectionError):
        await collect(
            "tenant-1", "У меня не работает биллинг", triage_result,
            gateway=gateway, publisher=publisher,
            command_search=_no_commands, screenshot_search=_no_screenshots,
        )

    assert provider.calls == 2
    assert publisher.calls == []
