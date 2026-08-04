import json
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from pydantic import BaseModel, ValidationError

from etiology.agent.model_gateway import ModelGateway, ModelMessage, ModelRequest, ModelTier
from etiology.platform_core.event_bus import EventPublisher, EventReader, StoredEvent


class BugReportCompositionError(RuntimeError):
    pass


@dataclass
class BugReportResult:
    incident_id: str
    title: str
    severity: str
    environment: str
    steps_to_reproduce: list[str]
    expected_behavior: str
    actual_behavior: str
    diagnostic_summary: str


class _BugReport(BaseModel):
    title: str
    severity: Literal["critical", "high", "medium", "low"]
    environment: str
    steps_to_reproduce: list[str]
    expected_behavior: str
    actual_behavior: str
    diagnostic_summary: str


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        _, _, rest = text.partition("\n")
        text = rest.removesuffix("```").strip()
    return text


def _find_latest(events: list[StoredEvent], event_type: str) -> StoredEvent | None:
    matches = [e for e in events if e.event_type == event_type]
    return matches[-1] if matches else None


def _build_prompt(triaged: StoredEvent | None, needs_report: StoredEvent) -> tuple[str, str]:
    system = (
        "Ты — Bug Report Composer службы поддержки Keitaro. По диагностическому трейлу инцидента "
        "собери исчерпывающую техническую спецификацию для разработки — не жалобу, а тех.спецификацию "
        "(ключевая ценность этой роли). Опирайся ТОЛЬКО на данные трейла ниже — не изобретай шаги "
        "воспроизведения или детали окружения, которых там нет; если данных не хватает, явно отметь "
        "это в diagnostic_summary, а не выдумывай. Верни ТОЛЬКО JSON-объект без markdown-разметки: "
        '{"title": "...", "severity": "critical|high|medium|low", "environment": "...", '
        '"steps_to_reproduce": ["...", ...], "expected_behavior": "...", "actual_behavior": "...", '
        '"diagnostic_summary": "..."}.'
    )
    parts = []
    if triaged is not None:
        parts.append(
            f"Исходное сообщение клиента: {triaged.payload.get('raw_message')}\n"
            f"Severity (Triage): {triaged.payload.get('severity')}\n"
            f"Тема: {triaged.payload.get('topic_tag')}"
        )
    parts.append(
        f"Диагностическая сводка (Diagnostic Collector): {needs_report.payload.get('advisory_text')}\n"
        f"Найденная команда диагностики: {needs_report.payload.get('matched_command')}\n"
        f"Скриншоты: {needs_report.payload.get('screenshot_refs')}\n"
        f"Эскалировано на человека: {needs_report.payload.get('escalated_to_human')}"
    )
    user = "\n\n".join(parts)
    return system, user


def _parse_report(text: str) -> _BugReport:
    data = json.loads(_strip_code_fence(text))
    return _BugReport.model_validate(data)


async def compose(
    tenant_id: str,
    incident_id: str,
    *,
    gateway: ModelGateway,
    publisher: EventPublisher,
    read_aggregate_events: Callable[[str, str, str], Awaitable[list[StoredEvent]]] = (
        EventReader().read_aggregate_events
    ),
) -> BugReportResult:
    events = await read_aggregate_events(tenant_id, "incident", incident_id)
    needs_report = _find_latest(events, "incident.needs_bug_report")
    if needs_report is None:
        raise BugReportCompositionError(
            f"Для инцидента {incident_id!r} не найдено событие incident.needs_bug_report — "
            "Bug Report Composer вызывается только после эскалации Diagnostic Collector'ом"
        )
    triaged = _find_latest(events, "incident.triaged")

    system, user = _build_prompt(triaged, needs_report)
    messages = [ModelMessage(role="user", content=user)]

    report: _BugReport | None = None
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
        response = await gateway.complete(
            ModelRequest(tier=ModelTier.STRONG, messages=messages, system=system, max_tokens=4096)
        )
        try:
            report = _parse_report(response.content)
            break
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = exc

    if report is None:
        raise BugReportCompositionError(f"Не удалось собрать bug report после 2 попыток: {last_error}")

    await publisher.publish(
        tenant_id=tenant_id,
        event_type="bug_report.created",
        aggregate_type="incident",
        aggregate_id=incident_id,
        payload={
            "title": report.title,
            "severity": report.severity,
            "environment": report.environment,
            "steps_to_reproduce": report.steps_to_reproduce,
            "expected_behavior": report.expected_behavior,
            "actual_behavior": report.actual_behavior,
            "diagnostic_summary": report.diagnostic_summary,
        },
    )

    return BugReportResult(
        incident_id=incident_id,
        title=report.title,
        severity=report.severity,
        environment=report.environment,
        steps_to_reproduce=report.steps_to_reproduce,
        expected_behavior=report.expected_behavior,
        actual_behavior=report.actual_behavior,
        diagnostic_summary=report.diagnostic_summary,
    )
