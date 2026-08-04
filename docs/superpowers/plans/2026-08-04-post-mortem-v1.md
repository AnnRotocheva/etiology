# Post-mortem Agent v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Реализовать Post-mortem Agent (`incident_id` → черновик post-mortem через
`ApprovalGate` + `post_mortem.drafted` в Event Store), только для критических закрытых
инцидентов.

**Architecture:** Никаких новых платформенных зависимостей — переиспользует уже существующие
`EventReader.read_aggregate_events`, `ApprovalGate`, `EventPublisher`. Простая async-функция,
STRONG-tier, тот же markdown-fence-strip + retry, что и во всех предыдущих агентах.

**Tech Stack:** Python 3.12+, pydantic v2, pytest (session-scoped event loop).

## Global Constraints

- Жёсткая проверка `severity == "critical"` и наличия терминального события
  (`bug_report.created` либо `incident.resolved`) в коде — исключение, а не тихий no-op.
- Модель — `ModelTier.STRONG`, `max_tokens=2048` (post-mortem — развёрнутый документ с
  несколькими списками, как Bug Report Composer — сразу берём запас, не ждём обрезки на живом API).
- Событие `post_mortem.drafted` публикуется на `aggregate_type="incident"`,
  `aggregate_id=incident_id` (не новый aggregate — артефакт об одном конкретном инциденте, как
  `bug_report.created`).

Ссылка на дизайн: `docs/superpowers/specs/2026-08-04-post-mortem-design.md`.

---

### Task 1: Post-mortem Agent

**Files:**
- Create: `src/etiology/domain/escalation_sync/post_mortem/agent.py`
- Create: `src/etiology/domain/escalation_sync/post_mortem/__init__.py`
- Create: `tests/domain/escalation_sync/post_mortem/__init__.py`
- Test: `tests/domain/escalation_sync/post_mortem/test_agent.py`

**Interfaces:**
- Consumes: `EventReader`, `StoredEvent`, `EventPublisher` из `etiology.platform_core.event_bus`;
  `ApprovalGate` из `etiology.platform_core.approval_gate`; `ModelGateway`, `ModelMessage`,
  `ModelRequest`, `ModelTier` из `etiology.agent.model_gateway` — все уже существуют.
- Produces: `PostMortemResult` (dataclass: `incident_id, approval_id, title, timeline: list[str],
  hypotheses: list[str], root_cause: str, impact: str, action_items: list[str]`),
  `PostMortemError(RuntimeError)`, `async def draft_post_mortem(tenant_id: str, incident_id: str, *,
  gateway: ModelGateway, approval_gate: ApprovalGate, publisher: EventPublisher,
  read_aggregate_events=EventReader().read_aggregate_events) -> PostMortemResult`. Экспортируются
  из `etiology.domain.escalation_sync.post_mortem`.

- [ ] **Step 1: Создать пакеты для тестовой директории**

`tests/domain/escalation_sync/post_mortem/__init__.py` — пустой файл.

- [ ] **Step 2: Написать падающие тесты**

`tests/domain/escalation_sync/post_mortem/test_agent.py`:
```python
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
```

- [ ] **Step 3: Запустить тесты, убедиться что падают**

Run: `.venv/Scripts/python.exe -m pytest tests/domain/escalation_sync/post_mortem/test_agent.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 4: Реализовать Post-mortem Agent**

`src/etiology/domain/escalation_sync/post_mortem/agent.py`:
```python
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
    lines = [
        f"- {e.created_at.isoformat()} [{e.event_type}] {e.payload}"
        for e in events
    ]
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
```

`src/etiology/domain/escalation_sync/post_mortem/__init__.py`:
```python
from .agent import PostMortemError, PostMortemResult, draft_post_mortem

__all__ = ["PostMortemError", "PostMortemResult", "draft_post_mortem"]
```

- [ ] **Step 5: Запустить тесты, убедиться что проходят**

Run: `.venv/Scripts/python.exe -m pytest tests/domain/escalation_sync/post_mortem/test_agent.py -v`
Expected: PASS (5 тестов)

- [ ] **Step 6: Прогнать весь набор тестов проекта**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: PASS (все тесты)

- [ ] **Step 7: Commit**

```bash
git add src/etiology/domain/escalation_sync/post_mortem tests/domain/escalation_sync/post_mortem
git commit -m "feat: добавлен Post-mortem Agent"
```

---

## После выполнения плана

Ручная проверка на живом API: собрать критический инцидент (triage → collect → compose с
message, дающим severity=critical), вызвать `draft_post_mortem()` с реальным
`AnthropicProvider`, убедиться что `post_mortem.drafted` появился в `events`, а предложение — в
`approval_gate.list_pending(tenant_id, object_type="post_mortem")`.
