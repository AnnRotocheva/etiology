# MCP Gateway (server-режим) v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Реализовать `build_server()` — фабрику MCP-сервера (`FastMCP`) с тремя инструментами:
`incident_create`, `knowledge_base_search`, `analytics_query`, каждый — тонкая обёртка над уже
существующими функциями.

**Architecture:** Официальный MCP SDK (`mcp`, зафиксирован `>=1.9,<2`). Инструменты
регистрируются декоратором `@server.tool()`, тестируются через `FastMCP.call_tool()` — прямой
вызов без сетевого транспорта. `build_server()` — фабрика с DI-параметрами, тот же паттерн, что
и во всех агентах сессии.

**Tech Stack:** Python 3.12+, `mcp>=1.9,<2` (новая зависимость), pytest (session-scoped event
loop).

## Global Constraints

- Только server-режим — без client-режима (§9.2, нет внешних MCP-серверов для подключения).
- Ровно три инструмента, без добавления новых сверх зафиксированных в реестре §9.1.
- `tenant_id` — явный аргумент каждого инструмента (задокументированное ограничение v1 — см.
  дизайн-спек, реальная аутентификация внешних клиентов не входит в этот план).
- Инструменты не содержат бизнес-логики — только вызов существующей функции + сериализация
  результата в JSON-совместимый dict.

Ссылка на дизайн: `docs/superpowers/specs/2026-08-04-mcp-gateway-design.md`.

---

### Task 1: MCP Gateway server-режим

**Files:**
- Modify: `pyproject.toml` (добавить зависимость `mcp>=1.9,<2`)
- Create: `src/etiology/platform_core/mcp_gateway/server.py`
- Create: `src/etiology/platform_core/mcp_gateway/__init__.py`
- Create: `tests/platform_core/mcp_gateway/__init__.py`
- Test: `tests/platform_core/mcp_gateway/test_server.py`

**Interfaces:**
- Consumes: `ModelGateway` из `etiology.agent.model_gateway`; `EventPublisher` из
  `etiology.platform_core.event_bus`; `triage` из `etiology.domain.diagnostics.triage`;
  `search` из `etiology.domain.knowledge_base` (как `kb_search`); `top_topics`,
  `resolution_rate` из `etiology.domain.analytics` — все уже существуют.
- Produces: `async def build_server(*, gateway: ModelGateway, publisher: EventPublisher,
  triage_fn=triage, kb_search=knowledge_base.search, top_topics_fn=top_topics,
  resolution_rate_fn=resolution_rate) -> FastMCP`, экспортируется из
  `etiology.platform_core.mcp_gateway`.

- [ ] **Step 1: Добавить зависимость**

В `pyproject.toml`, в `[project].dependencies`, добавить `"mcp>=1.9,<2"` (уже установлена в
`.venv` вручную перед проектированием — `pip install` не требуется, только фиксация в
`pyproject.toml`).

- [ ] **Step 2: Создать пакет для тестовой директории**

`tests/platform_core/mcp_gateway/__init__.py` — пустой файл.

- [ ] **Step 3: Написать падающие тесты**

`tests/platform_core/mcp_gateway/test_server.py`:
```python
import json

from etiology.platform_core.mcp_gateway import build_server


class _Result:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


async def _fake_triage(tenant_id, raw_message, *, gateway, publisher):
    return _Result(
        incident_id="incident-1", severity="high", topic_tag="billing",
        kb_closable=False, kb_article_id=None,
    )


async def _fake_kb_search(tenant_id, query):
    return [_Result(id="article-1", title="Заголовок", topic_tag="billing")]


async def _fake_top_topics(tenant_id):
    return [_Result(topic_tag="billing", count=3)]


async def _fake_resolution_rate(tenant_id):
    return _Result(triaged_count=5, resolved_count=2, rate=0.4)


def _server():
    return build_server(
        gateway=object(),
        publisher=object(),
        triage_fn=_fake_triage,
        kb_search=_fake_kb_search,
        top_topics_fn=_fake_top_topics,
        resolution_rate_fn=_fake_resolution_rate,
    )


async def _call(server, name, args):
    result = await server.call_tool(name, args)
    return json.loads(result[0].text)


async def test_server_registers_exactly_three_tools():
    server = _server()

    tools = await server.list_tools()

    assert {t.name for t in tools} == {"incident_create", "knowledge_base_search", "analytics_query"}


async def test_incident_create_wraps_triage():
    server = _server()

    result = await _call(server, "incident_create", {"tenant_id": "tenant-1", "raw_message": "msg"})

    assert result == {
        "incident_id": "incident-1",
        "severity": "high",
        "topic_tag": "billing",
        "kb_closable": False,
        "kb_article_id": None,
    }


async def test_knowledge_base_search_wraps_search():
    server = _server()

    result = await _call(server, "knowledge_base_search", {"tenant_id": "tenant-1", "query": "billing"})

    assert result == {"articles": [{"id": "article-1", "title": "Заголовок", "topic_tag": "billing"}]}


async def test_analytics_query_combines_top_topics_and_resolution_rate():
    server = _server()

    result = await _call(server, "analytics_query", {"tenant_id": "tenant-1"})

    assert result == {
        "top_topics": [{"topic_tag": "billing", "count": 3}],
        "resolution_rate": {"triaged_count": 5, "resolved_count": 2, "rate": 0.4},
    }
```

- [ ] **Step 4: Запустить тесты, убедиться что падают**

Run: `.venv/Scripts/python.exe -m pytest tests/platform_core/mcp_gateway -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 5: Реализовать `build_server`**

`src/etiology/platform_core/mcp_gateway/server.py`:
```python
from typing import Awaitable, Callable

from mcp.server.fastmcp import FastMCP

from etiology.agent.model_gateway import ModelGateway
from etiology.domain import knowledge_base
from etiology.domain.analytics import resolution_rate, top_topics
from etiology.domain.diagnostics.triage import triage
from etiology.platform_core.event_bus import EventPublisher


def build_server(
    *,
    gateway: ModelGateway,
    publisher: EventPublisher,
    triage_fn: Callable = triage,
    kb_search: Callable = knowledge_base.search,
    top_topics_fn: Callable = top_topics,
    resolution_rate_fn: Callable = resolution_rate,
) -> FastMCP:
    """MCP Gateway, server-режим (docs/architecture.md §9.1). Три инструмента ровно по
    зафиксированному реестру — тонкие обёртки над уже существующими функциями, без новой
    бизнес-логики. tenant_id — открытый аргумент вызывающей стороны в v1 (нет системы
    аутентификации внешних клиентов, чтобы выводить его из токена) — задокументированное
    ограничение, не тихая заглушка (см. дизайн-спек).
    """
    server = FastMCP("etiology")

    @server.tool(
        name="incident_create",
        description="Классифицировать сырое обращение клиента и создать инцидент (Triage Agent)",
    )
    async def incident_create(tenant_id: str, raw_message: str) -> dict:
        result = await triage_fn(tenant_id, raw_message, gateway=gateway, publisher=publisher)
        return {
            "incident_id": result.incident_id,
            "severity": result.severity,
            "topic_tag": result.topic_tag,
            "kb_closable": result.kb_closable,
            "kb_article_id": result.kb_article_id,
        }

    @server.tool(name="knowledge_base_search", description="Поиск по базе знаний тенанта")
    async def knowledge_base_search(tenant_id: str, query: str) -> dict:
        articles = await kb_search(tenant_id, query)
        return {
            "articles": [
                {"id": a.id, "title": a.title, "topic_tag": a.topic_tag} for a in articles
            ]
        }

    @server.tool(
        name="analytics_query",
        description="Сводная аналитика тенанта: топ тем и доля self-service resolution",
    )
    async def analytics_query(tenant_id: str) -> dict:
        topics = await top_topics_fn(tenant_id)
        rate = await resolution_rate_fn(tenant_id)
        return {
            "top_topics": [{"topic_tag": t.topic_tag, "count": t.count} for t in topics],
            "resolution_rate": {
                "triaged_count": rate.triaged_count,
                "resolved_count": rate.resolved_count,
                "rate": rate.rate,
            },
        }

    return server
```

`src/etiology/platform_core/mcp_gateway/__init__.py`:
```python
from .server import build_server

__all__ = ["build_server"]
```

- [ ] **Step 6: Запустить тесты, убедиться что проходят**

Run: `.venv/Scripts/python.exe -m pytest tests/platform_core/mcp_gateway -v`
Expected: PASS (4 теста)

- [ ] **Step 7: Прогнать весь набор тестов проекта**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: PASS (все тесты)

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/etiology/platform_core/mcp_gateway tests/platform_core/mcp_gateway
git commit -m "feat: добавлен MCP Gateway (server-режим)"
```

---

## После выполнения плана

Ручная проверка не на живом Anthropic API (эта фича не требует ключа сама по себе, но
`incident_create` внутри реально дойдёт до `triage()`, которая его требует) — собрать
`build_server()` с реальными `ModelGateway`/`EventPublisher`/`AnthropicProvider`, вызвать
`incident_create` через `server.call_tool()` на тестовом сообщении, убедиться что инцидент
реально появляется в Event Store (не только в возвращённом dict).
