from etiology.platform_core.approval_gate import ApprovalGate


async def test_submit_creates_pending_item(tenant_id):
    gate = ApprovalGate()

    approval_id = await gate.submit(tenant_id, "kb_suggestion", {"title": "t"}, created_by="tester")

    pending = await gate.list_pending(tenant_id)
    assert any(item.id == approval_id and item.status == "pending" for item in pending)
    match = next(item for item in pending if item.id == approval_id)
    assert match.object_type == "kb_suggestion"
    assert match.payload == {"title": "t"}
    assert match.created_by == "tester"
    assert match.reviewed_by is None


async def test_list_pending_filters_by_object_type(tenant_id):
    gate = ApprovalGate()
    await gate.submit(tenant_id, "kb_suggestion", {"a": 1}, created_by="tester")
    await gate.submit(tenant_id, "post_mortem", {"b": 2}, created_by="tester")

    kb_only = await gate.list_pending(tenant_id, object_type="kb_suggestion")

    assert len(kb_only) == 1
    assert kb_only[0].object_type == "kb_suggestion"


async def test_approve_removes_item_from_pending(tenant_id):
    gate = ApprovalGate()
    approval_id = await gate.submit(tenant_id, "kb_suggestion", {"title": "t"}, created_by="tester")

    await gate.approve(tenant_id, approval_id, reviewed_by="reviewer")

    pending = await gate.list_pending(tenant_id)
    assert not any(item.id == approval_id for item in pending)


async def test_reject_removes_item_from_pending(tenant_id):
    gate = ApprovalGate()
    approval_id = await gate.submit(tenant_id, "kb_suggestion", {"title": "t"}, created_by="tester")

    await gate.reject(tenant_id, approval_id, reviewed_by="reviewer")

    pending = await gate.list_pending(tenant_id)
    assert not any(item.id == approval_id for item in pending)
