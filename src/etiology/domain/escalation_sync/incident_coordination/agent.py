import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from pydantic import BaseModel, ValidationError

from etiology.agent.model_gateway import ModelGateway, ModelMessage, ModelRequest, ModelTier
from etiology.platform_core.event_bus import EventPublisher, EventReader, StoredEvent


class CoordinationError(RuntimeError):
    pass


@dataclass
class IncidentGroup:
    incident_ids: list[str]
    master_incident_id: str
    status_summary: str


@dataclass
class CoordinationResult:
    correlated: bool
    groups: list[IncidentGroup]


class _Group(BaseModel):
    incident_ids: list[str]
    master_incident_id: str
    status_summary: str


class _Correlation(BaseModel):
    groups: list[_Group]
    reasoning: str


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        _, _, rest = text.partition("\n")
        text = rest.removesuffix("```").strip()
    return text


def _build_prompt(incidents: list[StoredEvent]) -> tuple[str, str]:
    system = (
        "Ты — Incident Coordination Agent службы поддержки Keitaro. Перед тобой список инцидентов "
        "за недавнее окно времени. Определи, представляют ли несколько из них ОДИН и тот же сбой "
        "(похожая тема/симптом и близкое время создания) — их нужно объединить в группу с "
        "master-инцидентом (самым ранним из группы). Не объединяй инциденты с разными темами "
        "просто потому что они рядом по времени. Верни ТОЛЬКО JSON-объект без markdown-разметки: "
        '{"groups": [{"incident_ids": ["...", ...], "master_incident_id": "...", '
        '"status_summary": "..."}], "reasoning": "..."}. "groups" — пустой список, если корреляций нет. '
        "master_incident_id обязан быть одним из incident_ids этой же группы."
    )
    lines = [
        f"- incident_id={e.aggregate_id} severity={e.payload.get('severity')} "
        f"topic={e.payload.get('topic_tag')!r} triaged_at={e.created_at.isoformat()}\n"
        f"  сообщение: {e.payload.get('raw_message')}"
        for e in incidents
    ]
    user = "Инциденты:\n" + "\n".join(lines)
    return system, user


def _parse_correlation(text: str, known_incident_ids: set[str]) -> _Correlation:
    data = json.loads(_strip_code_fence(text))
    correlation = _Correlation.model_validate(data)
    for group in correlation.groups:
        if group.master_incident_id not in group.incident_ids:
            raise ValueError(
                f"master_incident_id {group.master_incident_id!r} не входит в incident_ids группы"
            )
        if not set(group.incident_ids) <= known_incident_ids:
            raise ValueError("incident_ids группы содержат id, отсутствующий в переданном списке инцидентов")
    return correlation


async def coordinate(
    tenant_id: str,
    *,
    gateway: ModelGateway,
    publisher: EventPublisher,
    read_events_by_type: Callable[[str, str, datetime | None], Awaitable[list[StoredEvent]]] = (
        EventReader().read_events_by_type
    ),
    window_minutes: int = 60,
) -> CoordinationResult:
    since = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    incidents = await read_events_by_type(tenant_id, "incident.triaged", since)

    if len(incidents) < 2:
        return CoordinationResult(correlated=False, groups=[])

    known_ids = {e.aggregate_id for e in incidents}
    system, user = _build_prompt(incidents)
    messages = [ModelMessage(role="user", content=user)]

    correlation: _Correlation | None = None
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
            ModelRequest(tier=ModelTier.STANDARD, messages=messages, system=system, max_tokens=1536)
        )
        try:
            correlation = _parse_correlation(response.content, known_ids)
            break
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            last_error = exc

    if correlation is None:
        raise CoordinationError(f"Не удалось получить корреляцию после 2 попыток: {last_error}")

    groups: list[IncidentGroup] = []
    for group in correlation.groups:
        for incident_id in group.incident_ids:
            if incident_id == group.master_incident_id:
                continue
            await publisher.publish(
                tenant_id=tenant_id,
                event_type="incident.merged",
                aggregate_type="incident",
                aggregate_id=incident_id,
                payload={"merged_into": group.master_incident_id, "status_summary": group.status_summary},
            )
        await publisher.publish(
            tenant_id=tenant_id,
            event_type="incident.status_published",
            aggregate_type="incident",
            aggregate_id=group.master_incident_id,
            payload={"member_incident_ids": group.incident_ids, "status_summary": group.status_summary},
        )
        groups.append(
            IncidentGroup(
                incident_ids=group.incident_ids,
                master_incident_id=group.master_incident_id,
                status_summary=group.status_summary,
            )
        )

    return CoordinationResult(correlated=bool(groups), groups=groups)
