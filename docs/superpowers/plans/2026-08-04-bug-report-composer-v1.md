# Bug Report Composer v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Реализовать Bug Report Composer v1 (`incident_id` → чтение трейла событий из Event Store → `bug_report.created`), включая читающую сторону Event Bus — `EventReader`.

**Architecture:** Продолжение вертикали Diagnostics. `compose()` — простая async-функция (не LangGraph, тот же аргумент, что и у предыдущих двух агентов). В отличие от Triage→Diagnostic Collector, вход — не объект от предыдущего шага, а чтение полного трейла инцидента из Event Store через новый `EventReader` (так зафиксировано в архитектуре §4.3: "Read: весь диагностический трейл инцидента"). Один STRONG-tier LLM-вызов, та же анти-галлюцинаторная и retry-дисциплина, что уже дважды подтверждена на живом API.

**Tech Stack:** Python 3.12+, asyncpg, pydantic v2, pytest (session-scoped event loop), `ModelGateway`/`AnthropicProvider`, `EventPublisher`.

## Global Constraints

- RLS обязателен — через `tenant_connection(tenant_id)`.
- Не LangGraph.
- Модель — `ModelTier.STRONG`.
- Тот же паттерн парсинга ответа модели, что в Triage/Diagnostic Collector: снять markdown code
  fence перед `json.loads`, один retry на невалидный JSON/схему, вторая неудача — исключение
  (без тихого fallback).
- Dev Postgres должен быть запущен.

Ссылка на дизайн: `docs/superpowers/specs/2026-08-04-bug-report-composer-design.md`.

---

### Task 1: `EventReader`

**Files:**
- Create: `src/etiology/platform_core/event_bus/reader.py`
- Modify: `src/etiology/platform_core/event_bus/__init__.py`
- Test: `tests/platform_core/event_bus/test_reader.py`

**Interfaces:**
- Produces: `StoredEvent` (dataclass: `event_type: str, payload: dict, metadata: dict, created_at: datetime`), `EventReader` (класс с методом `async def read_aggregate_events(self, tenant_id: str, aggregate_type: str, aggregate_id: str) -> list[StoredEvent]`), экспортируются из `etiology.platform_core.event_bus`. Task 2 использует `EventReader().read_aggregate_events` как значение по умолчанию для DI-параметра.

- [ ] **Step 1: Написать падающий тест**

`tests/platform_core/event_bus/test_reader.py`:
```python
import uuid

from etiology.platform_core.event_bus import EventPublisher, EventReader


async def test_read_aggregate_events_returns_events_in_order(tenant_id):
    publisher = EventPublisher()
    reader = EventReader()
    aggregate_id = str(uuid.uuid4())

    await publisher.publish(
        tenant_id=tenant_id,
        event_type="incident.triaged",
        aggregate_type="incident",
        aggregate_id=aggregate_id,
        payload={"severity": "high"},
        metadata={"model": "fake"},
    )
    await publisher.publish(
        tenant_id=tenant_id,
        event_type="incident.needs_bug_report",
        aggregate_type="incident",
        aggregate_id=aggregate_id,
        payload={"advisory_text": "текст"},
    )

    events = await reader.read_aggregate_events(tenant_id, "incident", aggregate_id)

    assert [e.event_type for e in events] == ["incident.triaged", "incident.needs_bug_report"]
    assert events[0].payload == {"severity": "high"}
    assert events[0].metadata == {"model": "fake"}
    assert events[1].payload == {"advisory_text": "текст"}
    assert events[1].metadata == {}


async def test_read_aggregate_events_returns_empty_list_for_unknown_aggregate(tenant_id):
    reader = EventReader()

    events = await reader.read_aggregate_events(tenant_id, "incident", str(uuid.uuid4()))

    assert events == []
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `.venv/Scripts/python.exe -m pytest tests/platform_core/event_bus/test_reader.py -v`
Expected: FAIL (`ImportError: cannot import name 'EventReader'`)

- [ ] **Step 3: Реализовать `EventReader`**

`src/etiology/platform_core/event_bus/reader.py`:
```python
import json
from dataclasses import dataclass
from datetime import datetime

from etiology.data.db.pool import tenant_connection


@dataclass
class StoredEvent:
    event_type: str
    payload: dict
    metadata: dict
    created_at: datetime


class EventReader:
    """Читающая сторона Event Bus (docs/architecture.md §8.2) — дополняет
    publish-сторону (EventPublisher). Используется агентами, которым по
    контракту положено читать историю событий, а не принимать результат
    напрямую от предыдущего шага (например, Bug Report Composer, §4.3).
    """

    async def read_aggregate_events(
        self, tenant_id: str, aggregate_type: str, aggregate_id: str
    ) -> list[StoredEvent]:
        async with tenant_connection(tenant_id) as conn:
            rows = await conn.fetch(
                """
                SELECT event_type, payload, metadata, created_at
                FROM events
                WHERE aggregate_type = $1 AND aggregate_id = $2::uuid
                ORDER BY created_at ASC
                """,
                aggregate_type,
                aggregate_id,
            )
        return [
            StoredEvent(
                event_type=row["event_type"],
                payload=json.loads(row["payload"]),
                metadata=json.loads(row["metadata"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]
```

`src/etiology/platform_core/event_bus/__init__.py`:
```python
from .publisher import EventPublisher
from .reader import EventReader, StoredEvent

__all__ = ["EventPublisher", "EventReader", "StoredEvent"]
```

- [ ] **Step 4: Запустить тест, убедиться что проходит**

Run: `.venv/Scripts/python.exe -m pytest tests/platform_core/event_bus/test_reader.py -v`
Expected: PASS (2 теста)

- [ ] **Step 5: Commit**

```bash
git add src/etiology/platform_core/event_bus tests/platform_core/event_bus/test_reader.py
git commit -m "feat: добавлен EventReader — читающая сторона Event Bus"
```

---

### Task 2: Bug Report Composer

**Files:**
- Create: `src/etiology/domain/diagnostics/bug_report_composer/composer.py`
- Create: `src/etiology/domain/diagnostics/bug_report_composer/__init__.py`
- Create: `tests/domain/diagnostics/bug_report_composer/__init__.py`
- Test: `tests/domain/diagnostics/bug_report_composer/test_composer.py`

**Interfaces:**
- Consumes: `ModelGateway`, `ModelMessage`, `ModelRequest`, `ModelTier` из `etiology.agent.model_gateway`; `EventPublisher`, `EventReader`, `StoredEvent` из `etiology.platform_core.event_bus` (Task 1).
- Produces: `BugReportResult` (dataclass: `incident_id: str, title: str, severity: str, environment: str, steps_to_reproduce: list[str], expected_behavior: str, actual_behavior: str, diagnostic_summary: str`), `BugReportCompositionError(RuntimeError)`, `async def compose(tenant_id: str, incident_id: str, *, gateway: ModelGateway, publisher: EventPublisher, read_aggregate_events=EventReader().read_aggregate_events) -> BugReportResult`. Экспортируются из `etiology.domain.diagnostics.bug_report_composer`.

- [ ] **Step 1: Создать пакет для тестовой директории**

`tests/domain/diagnostics/bug_report_composer/__init__.py` — пустой файл.

- [ ] **Step 2: Написать падающие тесты**

`tests/domain/diagnostics/bug_report_composer/test_composer.py`:
```python
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
```

- [ ] **Step 3: Запустить тесты, убедиться что падают**

Run: `.venv/Scripts/python.exe -m pytest tests/domain/diagnostics/bug_report_composer/test_composer.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 4: Реализовать `composer.py`**

`src/etiology/domain/diagnostics/bug_report_composer/composer.py`:
```python
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
        response = await gateway.complete(ModelRequest(tier=ModelTier.STRONG, messages=messages, system=system))
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
```

`src/etiology/domain/diagnostics/bug_report_composer/__init__.py`:
```python
from .composer import BugReportCompositionError, BugReportResult, compose

__all__ = ["BugReportCompositionError", "BugReportResult", "compose"]
```

- [ ] **Step 5: Запустить тесты, убедиться что проходят**

Run: `.venv/Scripts/python.exe -m pytest tests/domain/diagnostics/bug_report_composer/test_composer.py -v`
Expected: PASS (4 теста)

- [ ] **Step 6: Прогнать весь набор тестов проекта**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: PASS (все тесты)

- [ ] **Step 7: Commit**

```bash
git add src/etiology/domain/diagnostics/bug_report_composer tests/domain/diagnostics/bug_report_composer
git commit -m "feat: добавлен Bug Report Composer"
```

---

## После выполнения плана

Ручная проверка на живом API: прогнать `triage()` → `collect()` (ветка `needs_bug_report`) →
`compose()` цепочкой с реальным `AnthropicProvider`, убедиться что в `events` появилась
осмысленная запись `bug_report.created`, а `compose()` корректно читает трейл через `EventReader`
(а не получает результат напрямую).
