# Diagnostic Collector v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Реализовать Diagnostic Collector v1 (`TriageResult` → `incident.resolved`/`incident.needs_bug_report`), включая три вспомогательные lookup-функции: `KnowledgeBase.get_by_id`, `diagnostic_command_catalog.search`, `screenshot_library.search`.

**Architecture:** Продолжение вертикали после Triage Agent. `collect()` — простая async-функция (не LangGraph, см. дизайн-спек), две ветки: `kb_closable=True` — детерминированная (без LLM, текст статьи как есть), `kb_closable=False` — один STANDARD-tier вызов, грунтованный найденными командой/скриншотами (та же анти-галлюцинаторная схема, что у Triage). DI через параметры, как в Triage — юнит-тесты `collect()` не трогают Postgres.

**Tech Stack:** Python 3.12+, asyncpg, pydantic v2, pytest (session-scoped event loop — уже настроено), `ModelGateway`/`AnthropicProvider`, `EventPublisher`, `KnowledgeBase.search`.

## Global Constraints

- RLS обязателен — все запросы через `tenant_connection(tenant_id)`.
- **Важное отличие от KB/events:** `etiology_app` имеет только `SELECT` на `diagnostic_command_catalog`
  и `screenshot_library` (`scripts/grant_app_role.sql`) — оба курируемые активы, пополняются
  только вручную человеком (§4.4, §5 architecture.md). Тесты этих таблиц **не могут** делать
  INSERT через обычный тестовый pool — только `SELECT` на пустой (в dev-окружении) таблице.
  Поэтому: (а) реальный DB-тест проверяет `search()` на пустой таблице (это и есть текущее
  состояние v1 — каталог/скриншоты курируются вручную и сейчас пусты); (б) маппинг строки БД →
  dataclass выносится в отдельную чистую функцию (`_row_to_command`/`_row_to_screenshot`),
  тестируемую напрямую на обычном `dict` без обращения к БД — так проверяется вся логика
  маппинга без прав на запись.
- Не вводить LangGraph — `collect()` остаётся обычной async-функцией.
- Модель для ветки эскалации — `ModelTier.STANDARD`.
- Dev Postgres должен быть запущен (см. предыдущий план — уже настроено).

Ссылка на дизайн: `docs/superpowers/specs/2026-08-04-diagnostic-collector-design.md`.

---

### Task 1: `KnowledgeBase.get_by_id`

**Files:**
- Modify: `src/etiology/domain/knowledge_base/search.py`
- Modify: `src/etiology/domain/knowledge_base/__init__.py`
- Modify: `tests/domain/knowledge_base/test_search.py`

**Interfaces:**
- Produces: `async def get_by_id(tenant_id: str, article_id: str) -> KbArticle | None`, экспортируется из `etiology.domain.knowledge_base`. Task 4 использует для получения статьи по `kb_article_id` из `TriageResult`.

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/domain/knowledge_base/test_search.py` (добавить `import uuid` в начало файла, если его там ещё нет):
```python
async def test_get_by_id_returns_matching_article(tenant_id):
    async with tenant_connection(tenant_id) as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO knowledge_base_articles (tenant_id, kind, title, body, topic_tag)
            VALUES ($1::uuid, $2::kb_article_kind, $3, $4, $5)
            RETURNING id
            """,
            tenant_id,
            "known_issue",
            "Заголовок",
            "Тело статьи",
            "topic",
        )
    article_id = str(row["id"])

    article = await knowledge_base.get_by_id(tenant_id, article_id)

    assert article is not None
    assert article.id == article_id
    assert article.title == "Заголовок"


async def test_get_by_id_returns_none_when_not_found(tenant_id):
    result = await knowledge_base.get_by_id(tenant_id, str(uuid.uuid4()))

    assert result is None
```

- [ ] **Step 2: Запустить тесты, убедиться что падают**

Run: `pytest tests/domain/knowledge_base/test_search.py -v`
Expected: FAIL (`AttributeError: module 'etiology.domain.knowledge_base' has no attribute 'get_by_id'`)

- [ ] **Step 3: Реализовать `get_by_id`**

Добавить в `src/etiology/domain/knowledge_base/search.py`:
```python
async def get_by_id(tenant_id: str, article_id: str) -> KbArticle | None:
    async with tenant_connection(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT id, kind, title, body, topic_tag FROM knowledge_base_articles WHERE id = $1::uuid",
            article_id,
        )
    if row is None:
        return None
    return KbArticle(
        id=str(row["id"]),
        kind=row["kind"],
        title=row["title"],
        body=row["body"],
        topic_tag=row["topic_tag"],
    )
```

`src/etiology/domain/knowledge_base/__init__.py`:
```python
from .search import KbArticle, get_by_id, search

__all__ = ["KbArticle", "get_by_id", "search"]
```

- [ ] **Step 4: Запустить тесты, убедиться что проходят**

Run: `pytest tests/domain/knowledge_base/test_search.py -v`
Expected: PASS (4 теста)

- [ ] **Step 5: Commit**

```bash
git add src/etiology/domain/knowledge_base tests/domain/knowledge_base/test_search.py
git commit -m "feat: добавлен KnowledgeBase.get_by_id"
```

---

### Task 2: `diagnostic_command_catalog.search`

**Files:**
- Create: `src/etiology/domain/diagnostics/diagnostic_collector/command_catalog.py`
- Create: `tests/domain/diagnostics/diagnostic_collector/__init__.py`
- Test: `tests/domain/diagnostics/diagnostic_collector/test_command_catalog.py`

**Interfaces:**
- Produces: `DiagnosticCommand` (dataclass: `id: str, scenario: str, command: str, environment_version: str | None, is_read_only: bool`), `async def search(tenant_id: str, query: str, limit: int = 1) -> list[DiagnosticCommand]`. Task 4 использует оба имени через `from .command_catalog import DiagnosticCommand, search`.

- [ ] **Step 1: Создать пакет для тестовой директории**

`tests/domain/diagnostics/diagnostic_collector/__init__.py` — пустой файл.

- [ ] **Step 2: Написать падающие тесты**

`tests/domain/diagnostics/diagnostic_collector/test_command_catalog.py`:
```python
from etiology.domain.diagnostics.diagnostic_collector.command_catalog import (
    DiagnosticCommand,
    _row_to_command,
    search,
)


def test_row_to_command_maps_all_fields():
    row = {
        "id": "3b1f6f0e-9a3b-4a3b-8f0e-1a2b3c4d5e6f",
        "scenario": "campaign_not_tracking_clicks",
        "command": "tail -n 200 /var/log/keitaro/tracker.log",
        "environment_version": "10.x",
        "is_read_only": True,
    }

    command = _row_to_command(row)

    assert command == DiagnosticCommand(
        id="3b1f6f0e-9a3b-4a3b-8f0e-1a2b3c4d5e6f",
        scenario="campaign_not_tracking_clicks",
        command="tail -n 200 /var/log/keitaro/tracker.log",
        environment_version="10.x",
        is_read_only=True,
    )


async def test_search_returns_empty_list_on_empty_catalog(tenant_id):
    results = await search(tenant_id, "campaign_not_tracking_clicks")

    assert results == []
```

- [ ] **Step 3: Запустить тесты, убедиться что падают**

Run: `pytest tests/domain/diagnostics/diagnostic_collector/test_command_catalog.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'etiology.domain.diagnostics.diagnostic_collector.command_catalog'`)

- [ ] **Step 4: Реализовать `command_catalog.py`**

`src/etiology/domain/diagnostics/diagnostic_collector/command_catalog.py`:
```python
from dataclasses import dataclass

from etiology.data.db.pool import tenant_connection


@dataclass
class DiagnosticCommand:
    id: str
    scenario: str
    command: str
    environment_version: str | None
    is_read_only: bool


def _row_to_command(row) -> DiagnosticCommand:
    return DiagnosticCommand(
        id=str(row["id"]),
        scenario=row["scenario"],
        command=row["command"],
        environment_version=row["environment_version"],
        is_read_only=row["is_read_only"],
    )


async def search(tenant_id: str, query: str, limit: int = 1) -> list[DiagnosticCommand]:
    """Каталог — курируемый актив, пополняется только вручную (§4.4 architecture.md).
    etiology_app имеет только SELECT — эта функция никогда не пишет в каталог.
    """
    pattern = f"%{query}%"
    async with tenant_connection(tenant_id) as conn:
        rows = await conn.fetch(
            """
            SELECT id, scenario, command, environment_version, is_read_only
            FROM diagnostic_command_catalog
            WHERE scenario ILIKE $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            pattern,
            limit,
        )
    return [_row_to_command(row) for row in rows]
```

- [ ] **Step 5: Запустить тесты, убедиться что проходят**

Run: `pytest tests/domain/diagnostics/diagnostic_collector/test_command_catalog.py -v`
Expected: PASS (2 теста)

- [ ] **Step 6: Commit**

```bash
git add src/etiology/domain/diagnostics/diagnostic_collector tests/domain/diagnostics/diagnostic_collector
git commit -m "feat: добавлен diagnostic_command_catalog.search"
```

---

### Task 3: `screenshot_library.search`

**Files:**
- Create: `src/etiology/domain/diagnostics/diagnostic_collector/screenshots.py`
- Test: `tests/domain/diagnostics/diagnostic_collector/test_screenshots.py`

**Interfaces:**
- Produces: `Screenshot` (dataclass: `id: str, ui_version: str, step_description: str, image_ref: str`), `async def search(tenant_id: str, query: str, limit: int = 3) -> list[Screenshot]`. Task 4 использует через `from .screenshots import Screenshot, search`.

- [ ] **Step 1: Написать падающие тесты**

`tests/domain/diagnostics/diagnostic_collector/test_screenshots.py`:
```python
from etiology.domain.diagnostics.diagnostic_collector.screenshots import (
    Screenshot,
    _row_to_screenshot,
    search,
)


def test_row_to_screenshot_maps_all_fields():
    row = {
        "id": "3b1f6f0e-9a3b-4a3b-8f0e-1a2b3c4d5e6f",
        "ui_version": "10.x",
        "step_description": "Раздел Settings -> Licensing",
        "image_ref": "s3://etiology-kb/screenshots/licensing-settings.png",
    }

    screenshot = _row_to_screenshot(row)

    assert screenshot == Screenshot(
        id="3b1f6f0e-9a3b-4a3b-8f0e-1a2b3c4d5e6f",
        ui_version="10.x",
        step_description="Раздел Settings -> Licensing",
        image_ref="s3://etiology-kb/screenshots/licensing-settings.png",
    )


async def test_search_returns_empty_list_on_empty_library(tenant_id):
    results = await search(tenant_id, "licensing")

    assert results == []
```

- [ ] **Step 2: Запустить тесты, убедиться что падают**

Run: `pytest tests/domain/diagnostics/diagnostic_collector/test_screenshots.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'etiology.domain.diagnostics.diagnostic_collector.screenshots'`)

- [ ] **Step 3: Реализовать `screenshots.py`**

`src/etiology/domain/diagnostics/diagnostic_collector/screenshots.py`:
```python
from dataclasses import dataclass

from etiology.data.db.pool import tenant_connection


@dataclass
class Screenshot:
    id: str
    ui_version: str
    step_description: str
    image_ref: str


def _row_to_screenshot(row) -> Screenshot:
    return Screenshot(
        id=str(row["id"]),
        ui_version=row["ui_version"],
        step_description=row["step_description"],
        image_ref=row["image_ref"],
    )


async def search(tenant_id: str, query: str, limit: int = 3) -> list[Screenshot]:
    """Библиотека — курируемый актив, только реальные скриншоты, пополняется вручную
    (§5 architecture.md). etiology_app имеет только SELECT.
    """
    pattern = f"%{query}%"
    async with tenant_connection(tenant_id) as conn:
        rows = await conn.fetch(
            """
            SELECT id, ui_version, step_description, image_ref
            FROM screenshot_library
            WHERE step_description ILIKE $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            pattern,
            limit,
        )
    return [_row_to_screenshot(row) for row in rows]
```

- [ ] **Step 4: Запустить тесты, убедиться что проходят**

Run: `pytest tests/domain/diagnostics/diagnostic_collector/test_screenshots.py -v`
Expected: PASS (2 теста)

- [ ] **Step 5: Commit**

```bash
git add src/etiology/domain/diagnostics/diagnostic_collector/screenshots.py tests/domain/diagnostics/diagnostic_collector/test_screenshots.py
git commit -m "feat: добавлен screenshot_library.search"
```

---

### Task 4: Diagnostic Collector

**Files:**
- Create: `src/etiology/domain/diagnostics/diagnostic_collector/collector.py`
- Modify: `src/etiology/domain/diagnostics/diagnostic_collector/__init__.py` (сейчас пустой)
- Test: `tests/domain/diagnostics/diagnostic_collector/test_collector.py`

**Interfaces:**
- Consumes: `TriageResult` из `etiology.domain.diagnostics.triage` (уже существует: `incident_id, severity, topic_tag, kb_closable, kb_article_id`); `KbArticle`, `get_by_id` из `etiology.domain.knowledge_base` (Task 1); `DiagnosticCommand`, `search` из `.command_catalog` (Task 2); `Screenshot`, `search` из `.screenshots` (Task 3); `ModelGateway`, `ModelMessage`, `ModelRequest`, `ModelTier` из `etiology.agent.model_gateway`; `EventPublisher` из `etiology.platform_core.event_bus`.
- Produces: `DiagnosticResult` (dataclass: `incident_id: str, outcome: Literal["resolved", "needs_bug_report"], advisory_text: str, matched_command: DiagnosticCommand | None, screenshot_refs: list[str], escalated_to_human: bool`), `DiagnosticCollectionError(RuntimeError)`, `async def collect(tenant_id: str, raw_message: str, triage_result: TriageResult, *, gateway: ModelGateway, publisher: EventPublisher, kb_get_by_id=kb_get_by_id_default, command_search=command_search_default, screenshot_search=screenshot_search_default) -> DiagnosticResult`. Всё экспортируется из `etiology.domain.diagnostics.diagnostic_collector`.

- [ ] **Step 1: Написать падающие тесты**

`tests/domain/diagnostics/diagnostic_collector/test_collector.py`:
```python
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
```

- [ ] **Step 2: Запустить тесты, убедиться что падают**

Run: `pytest tests/domain/diagnostics/diagnostic_collector/test_collector.py -v`
Expected: FAIL (`ImportError: cannot import name 'collect'`)

- [ ] **Step 3: Реализовать `collector.py`**

`src/etiology/domain/diagnostics/diagnostic_collector/collector.py`:
```python
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
```

`src/etiology/domain/diagnostics/diagnostic_collector/__init__.py`:
```python
from .collector import DiagnosticCollectionError, DiagnosticResult, collect
from .command_catalog import DiagnosticCommand
from .screenshots import Screenshot

__all__ = [
    "DiagnosticCollectionError",
    "DiagnosticResult",
    "collect",
    "DiagnosticCommand",
    "Screenshot",
]
```

- [ ] **Step 4: Запустить тесты, убедиться что проходят**

Run: `pytest tests/domain/diagnostics/diagnostic_collector/test_collector.py -v`
Expected: PASS (6 тестов)

- [ ] **Step 5: Прогнать весь набор тестов проекта**

Run: `pytest -v`
Expected: PASS (все тесты, включая предыдущие планы)

- [ ] **Step 6: Commit**

```bash
git add src/etiology/domain/diagnostics/diagnostic_collector/collector.py src/etiology/domain/diagnostics/diagnostic_collector/__init__.py tests/domain/diagnostics/diagnostic_collector/test_collector.py
git commit -m "feat: добавлен Diagnostic Collector"
```

---

## После выполнения плана

Ручная проверка на живом API (как и с Triage Agent): вызвать `triage()`, затем `collect()` с
реальным `AnthropicProvider` на тестовом сообщении дважды — один раз так, чтобы KB не нашла
совпадение (ветка `needs_bug_report`), и вручную добавить тестовую статью в KB перед вызовом,
чтобы проверить ветку `resolved`. Убедиться, что в `events` появились осмысленные записи.

Bug Report Composer — следующий отдельный спек/план, не часть этого документа.
