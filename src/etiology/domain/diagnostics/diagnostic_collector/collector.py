import json
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from pydantic import BaseModel, ValidationError

from etiology.agent.model_gateway import ModelGateway, ModelMessage, ModelRequest, ModelTier
from etiology.domain.diagnostics.triage import TriageResult
from etiology.domain.knowledge_base import KbArticle
from etiology.domain.knowledge_base import get_by_id as kb_get_by_id_default
from etiology.platform_core.event_bus import EventPublisher

from .command_catalog import DiagnosticCommand
from .command_catalog import search as command_search_default
from .screenshots import Screenshot
from .screenshots import search as screenshot_search_default


class DiagnosticCollectionError(RuntimeError):
    pass


@dataclass
class DiagnosticResult:
    incident_id: str
    outcome: Literal["resolved", "needs_bug_report"]
    advisory_text: str
    matched_command: DiagnosticCommand | None
    screenshot_refs: list[str]
    escalated_to_human: bool


class _Advisory(BaseModel):
    advisory_text: str
    escalated_to_human: bool


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        _, _, rest = text.partition("\n")
        text = rest.removesuffix("```").strip()
    return text


def _build_prompt(
    raw_message: str,
    topic_tag: str,
    commands: list[DiagnosticCommand],
    screenshots: list[Screenshot],
) -> tuple[str, str]:
    system = (
        "Ты — Diagnostic Collector службы поддержки Keitaro. База знаний не смогла закрыть "
        "обращение клиента самостоятельно. Собери короткую диагностическую сводку для клиента, "
        "опираясь ТОЛЬКО на переданные ниже команду диагностики (если есть) и скриншоты (если есть) — "
        "не придумывай команды или шаги, которых нет в списке. Верни ТОЛЬКО JSON-объект без "
        'markdown-разметки: {"advisory_text": "текст для клиента", "escalated_to_human": true|false}. '
        "escalated_to_human=true, если подходящей команды диагностики нет в каталоге — в этом случае "
        "advisory_text должен объяснить клиенту, что нужна эскалация на специалиста."
    )
    commands_block = "\n".join(f"- {c.command} (сценарий: {c.scenario})" for c in commands) or "(команд не найдено)"
    screenshots_block = (
        "\n".join(f"- {s.step_description} ({s.image_ref})" for s in screenshots) or "(скриншотов не найдено)"
    )
    user = (
        f"Сообщение клиента:\n{raw_message}\n\nТема: {topic_tag}\n\n"
        f"Команды диагностики:\n{commands_block}\n\nСкриншоты интерфейса:\n{screenshots_block}"
    )
    return system, user


def _parse_advisory(text: str) -> _Advisory:
    data = json.loads(_strip_code_fence(text))
    return _Advisory.model_validate(data)


async def collect(
    tenant_id: str,
    raw_message: str,
    triage_result: TriageResult,
    *,
    gateway: ModelGateway,
    publisher: EventPublisher,
    kb_get_by_id: Callable[[str, str], Awaitable[KbArticle | None]] = kb_get_by_id_default,
    command_search: Callable[[str, str], Awaitable[list[DiagnosticCommand]]] = command_search_default,
    screenshot_search: Callable[[str, str], Awaitable[list[Screenshot]]] = screenshot_search_default,
) -> DiagnosticResult:
    if triage_result.kb_closable:
        if triage_result.kb_article_id is None:
            raise DiagnosticCollectionError("kb_closable=true, но triage не передал kb_article_id")
        article = await kb_get_by_id(tenant_id, triage_result.kb_article_id)
        if article is None:
            raise DiagnosticCollectionError(
                f"kb_article_id {triage_result.kb_article_id!r} из triage не найден в базе знаний"
            )
        await publisher.publish(
            tenant_id=tenant_id,
            event_type="incident.resolved",
            aggregate_type="incident",
            aggregate_id=triage_result.incident_id,
            payload={
                "resolution": "kb_article",
                "kb_article_id": triage_result.kb_article_id,
                "advisory_text": article.body,
            },
        )
        return DiagnosticResult(
            incident_id=triage_result.incident_id,
            outcome="resolved",
            advisory_text=article.body,
            matched_command=None,
            screenshot_refs=[],
            escalated_to_human=False,
        )

    commands = await command_search(tenant_id, triage_result.topic_tag)
    screenshots = await screenshot_search(tenant_id, triage_result.topic_tag)
    system, user = _build_prompt(raw_message, triage_result.topic_tag, commands, screenshots)
    messages = [ModelMessage(role="user", content=user)]

    advisory: _Advisory | None = None
    last_error: Exception | None = None
    response = None
    for _attempt in range(2):
        if last_error is not None:
            messages.append(ModelMessage(role="assistant", content=response.content))
            messages.append(
                ModelMessage(
                    role="user",
                    content=f"Ответ не прошёл валидацию: {last_error}. Верни только исправленный JSON-объект.",
                )
            )
        response = await gateway.complete(ModelRequest(tier=ModelTier.STANDARD, messages=messages, system=system))
        try:
            advisory = _parse_advisory(response.content)
            break
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = exc

    if advisory is None:
        raise DiagnosticCollectionError(f"Не удалось собрать диагностическую сводку после 2 попыток: {last_error}")

    matched_command = commands[0] if commands else None
    escalated_to_human = advisory.escalated_to_human or not commands

    await publisher.publish(
        tenant_id=tenant_id,
        event_type="incident.needs_bug_report",
        aggregate_type="incident",
        aggregate_id=triage_result.incident_id,
        payload={
            "advisory_text": advisory.advisory_text,
            "matched_command": matched_command.command if matched_command else None,
            "screenshot_refs": [s.image_ref for s in screenshots],
            "escalated_to_human": escalated_to_human,
        },
    )

    return DiagnosticResult(
        incident_id=triage_result.incident_id,
        outcome="needs_bug_report",
        advisory_text=advisory.advisory_text,
        matched_command=matched_command,
        screenshot_refs=[s.image_ref for s in screenshots],
        escalated_to_human=escalated_to_human,
    )
