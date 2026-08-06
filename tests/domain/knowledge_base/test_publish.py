from datetime import datetime, timezone

import pytest

from etiology.data.db.pool import tenant_connection
from etiology.domain.knowledge_base import PublishError, publish_approved
from etiology.platform_core.approval_gate import ApprovalGate
from etiology.platform_core.event_bus import EventPublisher


class FakeApprovalGate:
    def __init__(self, item=None):
        self._item = item

    async def get(self, tenant_id, approval_id):
        return self._item


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


class _Item:
    def __init__(self, object_type="kb_suggestion", status="approved", payload=None):
        self.id = "approval-1"
        self.object_type = object_type
        self.payload = payload or {"title": "Заголовок", "body": "Тело статьи", "topic_tag": "billing"}
        self.status = status
        self.created_by = "knowledge_curator_agent"
        self.reviewed_by = "ann"
        self.reviewed_at = datetime.now(timezone.utc)
        self.created_at = datetime.now(timezone.utc)


async def test_publish_approved_raises_when_not_found():
    approval_gate = FakeApprovalGate(item=None)
    publisher = FakePublisher()

    with pytest.raises(PublishError):
        await publish_approved("tenant-1", "approval-1", approval_gate=approval_gate, publisher=publisher)


async def test_publish_approved_raises_when_wrong_object_type():
    approval_gate = FakeApprovalGate(item=_Item(object_type="post_mortem"))
    publisher = FakePublisher()

    with pytest.raises(PublishError):
        await publish_approved("tenant-1", "approval-1", approval_gate=approval_gate, publisher=publisher)


async def test_publish_approved_raises_when_not_yet_approved():
    approval_gate = FakeApprovalGate(item=_Item(status="pending"))
    publisher = FakePublisher()

    with pytest.raises(PublishError):
        await publish_approved("tenant-1", "approval-1", approval_gate=approval_gate, publisher=publisher)


async def test_publish_approved_inserts_article_after_human_approval(tenant_id):
    approval_gate = ApprovalGate()
    publisher = EventPublisher()
    approval_id = await approval_gate.submit(
        tenant_id,
        "kb_suggestion",
        {"title": "Заголовок", "body": "Тело статьи", "topic_tag": "billing"},
        created_by="knowledge_curator_agent",
    )
    await approval_gate.approve(tenant_id, approval_id, reviewed_by="ann")

    article = await publish_approved(tenant_id, approval_id, approval_gate=approval_gate, publisher=publisher)

    assert article.title == "Заголовок"
    assert article.topic_tag == "billing"
    async with tenant_connection(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT title, body, topic_tag, kind FROM knowledge_base_articles WHERE id = $1::uuid", article.id
        )
    assert row["title"] == "Заголовок"
    assert row["body"] == "Тело статьи"
    assert row["topic_tag"] == "billing"
    assert row["kind"] == "known_issue"


async def test_publish_approved_raises_when_approval_id_unknown(tenant_id):
    approval_gate = ApprovalGate()
    publisher = EventPublisher()

    with pytest.raises(PublishError):
        await publish_approved(
            tenant_id, "00000000-0000-0000-0000-000000000000", approval_gate=approval_gate, publisher=publisher
        )
