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
