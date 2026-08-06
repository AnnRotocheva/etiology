import json

from etiology.platform_core.mcp_gateway import build_server

ALL_TOOL_NAMES = {
    "incident_create",
    "diagnostic_collect",
    "bug_report_compose",
    "incident_coordinate",
    "post_mortem_draft",
    "kb_curate",
    "approval_gate_list_pending",
    "approval_gate_approve",
    "approval_gate_reject",
    "kb_publish_approved",
    "knowledge_base_search",
    "analytics_query",
    "csat_record",
}


class _Result:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeApprovalGate:
    def __init__(self):
        self.approved = []
        self.rejected = []

    async def get(self, tenant_id, approval_id):
        return _Result(
            id=approval_id,
            object_type="kb_suggestion",
            payload={"title": "Заголовок", "body": "Тело", "topic_tag": "billing"},
            status="approved",
            created_by="knowledge_curator_agent",
            reviewed_by="ann",
            reviewed_at=_FakeDatetime(),
            created_at=_FakeDatetime(),
        )

    async def list_pending(self, tenant_id, object_type=None):
        return [
            _Result(
                id="approval-1",
                object_type=object_type or "post_mortem",
                payload={"title": "черновик"},
                status="pending",
                created_by="agent",
                reviewed_by=None,
                reviewed_at=None,
                created_at=_FakeDatetime(),
            )
        ]

    async def approve(self, tenant_id, approval_id, reviewed_by):
        self.approved.append((tenant_id, approval_id, reviewed_by))

    async def reject(self, tenant_id, approval_id, reviewed_by):
        self.rejected.append((tenant_id, approval_id, reviewed_by))


class _FakeDatetime:
    def isoformat(self):
        return "2026-08-06T00:00:00+00:00"


async def _fake_triage(tenant_id, raw_message, *, gateway, publisher):
    return _Result(
        incident_id="incident-1", severity="high", topic_tag="billing",
        kb_closable=False, kb_article_id=None,
    )


async def _fake_collect(tenant_id, raw_message, triage_result, *, gateway, publisher):
    return _Result(
        incident_id=triage_result.incident_id,
        outcome="needs_bug_report",
        advisory_text="advisory",
        matched_command=None,
        screenshot_refs=[],
        escalated_to_human=True,
    )


async def _fake_compose(tenant_id, incident_id, *, gateway, publisher):
    return _Result(
        incident_id=incident_id,
        title="title",
        severity="high",
        environment="env",
        steps_to_reproduce=["step1"],
        expected_behavior="expected",
        actual_behavior="actual",
        diagnostic_summary="summary",
    )


async def _fake_coordinate(tenant_id, *, gateway, publisher, window_minutes=60):
    return _Result(
        correlated=True,
        groups=[_Result(incident_ids=["incident-1", "incident-2"], master_incident_id="incident-1", status_summary="один сбой")],
    )


async def _fake_draft_post_mortem(tenant_id, incident_id, *, gateway, approval_gate, publisher):
    return _Result(
        incident_id=incident_id,
        approval_id="approval-1",
        title="title",
        timeline=["t1"],
        hypotheses=["h1"],
        root_cause="cause",
        impact="impact",
        action_items=["a1"],
    )


async def _fake_curate(tenant_id, incident_id, *, gateway, approval_gate, publisher):
    return _Result(
        incident_id=incident_id, proposed=True, suggestion_id="suggestion-1", title="title", topic_tag="billing",
    )


async def _fake_kb_search(tenant_id, query):
    return [_Result(id="article-1", title="Заголовок", topic_tag="billing")]


async def _fake_top_topics(tenant_id):
    return [_Result(topic_tag="billing", count=3)]


async def _fake_resolution_rate(tenant_id):
    return _Result(triaged_count=5, resolved_count=2, rate=0.4)


async def _fake_record_csat(tenant_id, incident_id, score, publisher, comment=None):
    return None


async def _fake_publish_approved(tenant_id, approval_id, *, approval_gate, publisher):
    return _Result(id="article-1", title="Заголовок", topic_tag="billing")


def _server(approval_gate=None):
    return build_server(
        gateway=object(),
        publisher=object(),
        approval_gate=approval_gate or _FakeApprovalGate(),
        triage_fn=_fake_triage,
        collect_fn=_fake_collect,
        compose_fn=_fake_compose,
        coordinate_fn=_fake_coordinate,
        draft_post_mortem_fn=_fake_draft_post_mortem,
        curate_fn=_fake_curate,
        publish_approved_fn=_fake_publish_approved,
        kb_search=_fake_kb_search,
        top_topics_fn=_fake_top_topics,
        resolution_rate_fn=_fake_resolution_rate,
        record_csat_fn=_fake_record_csat,
    )


async def _call(server, name, args):
    result = await server.call_tool(name, args)
    return json.loads(result[0].text)


async def test_server_registers_all_tools():
    server = _server()

    tools = await server.list_tools()

    assert {t.name for t in tools} == ALL_TOOL_NAMES


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


async def test_diagnostic_collect_wraps_collect():
    server = _server()

    result = await _call(
        server,
        "diagnostic_collect",
        {
            "tenant_id": "tenant-1",
            "raw_message": "msg",
            "incident_id": "incident-1",
            "severity": "high",
            "topic_tag": "billing",
            "kb_closable": False,
        },
    )

    assert result == {
        "incident_id": "incident-1",
        "outcome": "needs_bug_report",
        "advisory_text": "advisory",
        "matched_command": None,
        "screenshot_refs": [],
        "escalated_to_human": True,
    }


async def test_bug_report_compose_wraps_compose():
    server = _server()

    result = await _call(server, "bug_report_compose", {"tenant_id": "tenant-1", "incident_id": "incident-1"})

    assert result == {
        "incident_id": "incident-1",
        "title": "title",
        "severity": "high",
        "environment": "env",
        "steps_to_reproduce": ["step1"],
        "expected_behavior": "expected",
        "actual_behavior": "actual",
        "diagnostic_summary": "summary",
    }


async def test_incident_coordinate_wraps_coordinate():
    server = _server()

    result = await _call(server, "incident_coordinate", {"tenant_id": "tenant-1"})

    assert result == {
        "correlated": True,
        "groups": [
            {"incident_ids": ["incident-1", "incident-2"], "master_incident_id": "incident-1", "status_summary": "один сбой"}
        ],
    }


async def test_post_mortem_draft_wraps_draft_post_mortem():
    server = _server()

    result = await _call(server, "post_mortem_draft", {"tenant_id": "tenant-1", "incident_id": "incident-1"})

    assert result == {
        "incident_id": "incident-1",
        "approval_id": "approval-1",
        "title": "title",
        "timeline": ["t1"],
        "hypotheses": ["h1"],
        "root_cause": "cause",
        "impact": "impact",
        "action_items": ["a1"],
    }


async def test_kb_curate_wraps_curate():
    server = _server()

    result = await _call(server, "kb_curate", {"tenant_id": "tenant-1", "incident_id": "incident-1"})

    assert result == {
        "incident_id": "incident-1",
        "proposed": True,
        "suggestion_id": "suggestion-1",
        "title": "title",
        "topic_tag": "billing",
    }


async def test_approval_gate_list_pending_wraps_list_pending():
    server = _server()

    result = await _call(server, "approval_gate_list_pending", {"tenant_id": "tenant-1"})

    assert result == {
        "items": [
            {
                "id": "approval-1",
                "object_type": "post_mortem",
                "payload": {"title": "черновик"},
                "status": "pending",
                "created_by": "agent",
                "created_at": "2026-08-06T00:00:00+00:00",
            }
        ]
    }


async def test_approval_gate_approve_wraps_approve():
    fake_gate = _FakeApprovalGate()
    server = _server(approval_gate=fake_gate)

    result = await _call(
        server, "approval_gate_approve", {"tenant_id": "tenant-1", "approval_id": "approval-1", "reviewed_by": "ann"}
    )

    assert result == {"approval_id": "approval-1", "status": "approved"}
    assert fake_gate.approved == [("tenant-1", "approval-1", "ann")]


async def test_approval_gate_reject_wraps_reject():
    fake_gate = _FakeApprovalGate()
    server = _server(approval_gate=fake_gate)

    result = await _call(
        server, "approval_gate_reject", {"tenant_id": "tenant-1", "approval_id": "approval-1", "reviewed_by": "ann"}
    )

    assert result == {"approval_id": "approval-1", "status": "rejected"}
    assert fake_gate.rejected == [("tenant-1", "approval-1", "ann")]


async def test_kb_publish_approved_wraps_publish_approved():
    server = _server()

    result = await _call(server, "kb_publish_approved", {"tenant_id": "tenant-1", "approval_id": "approval-1"})

    assert result == {"id": "article-1", "title": "Заголовок", "topic_tag": "billing"}


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


async def test_csat_record_wraps_record_csat():
    server = _server()

    result = await _call(
        server, "csat_record", {"tenant_id": "tenant-1", "incident_id": "incident-1", "score": 5}
    )

    assert result == {"incident_id": "incident-1", "recorded": True}
