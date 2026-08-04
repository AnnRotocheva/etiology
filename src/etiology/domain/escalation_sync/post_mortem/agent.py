import json
from dataclasses import dataclass
from typing import Awaitable, Callable

from pydantic import BaseModel, ValidationError

from etiology.agent.model_gateway import ModelGateway, ModelMessage, ModelRequest, ModelTier
from etiology.platform_core.approval_gate import ApprovalGate
from etiology.platform_core.event_bus import EventPublisher, EventReader, StoredEvent


class PostMortemError(RuntimeError):
    pass


@dataclass
class PostMortemResult:
    incident_id: str
    approval_id: str
    title: str
    timeline: list[str]
    hypotheses: list[str]
    root_cause: str
    impact: str
    action_items: list[str]


class _PostMortem(BaseModel):
    title: str
    timeline: list[str]
    hypotheses: list[str]
    root_cause: str
    impact: str
    action_items: list[str]


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        _, _, rest = text.partition("\n")
        text = rest.removesuffix("```").strip()
    return text


def _find_latest(events: list[StoredEvent], event_type: str) -> StoredEvent | None:
    matches = [e for e in events if e.event_type == event_type]
    return matches[-1] if matches else None


def _build_prompt(events: list[StoredEvent]) -> tuple[str, str]:
    system = (
        "Ты — Post-mortem Agent службы поддержки Keitaro. По полному трейлу закрытого критического "
        "инцидента собери разбор: таймлайн, гипотезы, причину и импакт. Причину пиши как "
        "подтверждённую ТОЛЬКО если в трейле реально есть подтверждение (например, фикс уже "
        "выкачен) — если это не так, явно напиши, что причина не подтверждена и требуется "
        "дальнейшее расследование, а не изобретай правдоподобную причину. Верни ТОЛЬКО JSON-объект "
        'без markdown-разметки: {"title": "...", "timeline": ["..."], "hypotheses": ["..."], '
        '"root_cause": "...", "impact": "...", "action_items": ["..."]}.'
    )
    lines = [f"- {e.created_at.isoformat()} [{e.event_type}] {e.payload}" for e in events]
    user = "Трейл событий инцидента:\n" + "\n".join(lines)
    return system, user


def _parse_post_mortem(text: str) -> _PostMortem:
    data = json.loads(_strip_code_fence(text))
    return _PostMortem.model_validate(data)


async def draft_post_mortem(
    tenant_id: str,
    incident_id: str,
    *,
    gateway: ModelGateway,
    approval_gate: ApprovalGate,
    publisher: EventPublisher,
    read_aggregate_events: Callable[[str, str, str], Awaitable[list[StoredEvent]]] = (
        EventReader().read_aggregate_events
    ),
) -> PostMortemResult:
    events = await read_aggregate_events(tenant_id, "incident", incident_id)

    triaged = _find_latest(events, "incident.triaged")
    if triaged is None or triaged.payload.get("severity") != "critical":
        raise PostMortemError(
            f"Инцидент {incident_id!r} не критический (или не найден triaged) — "
            "Post-mortem Agent обрабатывает только критические инциденты"
        )

    terminal = _find_latest(events, "bug_report.created") or _find_latest(events, "incident.resolved")
    if terminal is None:
        raise PostMortemError(f"Инцидент {incident_id!r} ещё не закрыт")

    system, user = _build_prompt(events)
    messages = [ModelMessage(role="user", content=user)]

    report: _PostMortem | None = None
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
            ModelRequest(tier=ModelTier.STRONG, messages=messages, system=system, max_tokens=2048)
        )
        try:
            report = _parse_post_mortem(response.content)
            break
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = exc

    if report is None:
        raise PostMortemError(f"Не удалось собрать post-mortem после 2 попыток: {last_error}")

    payload = {
        "title": report.title,
        "timeline": report.timeline,
        "hypotheses": report.hypotheses,
        "root_cause": report.root_cause,
        "impact": report.impact,
        "action_items": report.action_items,
    }
    approval_id = await approval_gate.submit(tenant_id, "post_mortem", payload, created_by="post_mortem_agent")

    await publisher.publish(
        tenant_id=tenant_id,
        event_type="post_mortem.drafted",
        aggregate_type="incident",
        aggregate_id=incident_id,
        payload=payload,
    )

    return PostMortemResult(
        incident_id=incident_id,
        approval_id=approval_id,
        title=report.title,
        timeline=report.timeline,
        hypotheses=report.hypotheses,
        root_cause=report.root_cause,
        impact=report.impact,
        action_items=report.action_items,
    )
