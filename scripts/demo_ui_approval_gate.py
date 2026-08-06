"""Страница 'Approval Gate': очередь черновиков, approve/reject, публикация
одобренных kb_suggestion (см. src/etiology/domain/knowledge_base/publish.py)."""
import streamlit as st

from demo_ui_common import get_approval_gate, get_publisher, get_tenant_id, run_async
from etiology.domain.knowledge_base import publish_approved


def render() -> None:
    st.title("Approval Gate")
    tenant_id = get_tenant_id()
    approval_gate = get_approval_gate()
    reviewed_by = st.text_input("Кто утверждает", value="ann")

    st.subheader("Ожидают решения человека")
    pending = run_async(approval_gate.list_pending(tenant_id))
    if not pending:
        st.info("Очередь пуста.")
    for item in pending:
        label = item.payload.get("title") or item.id
        with st.expander(f"[{item.object_type}] {label}"):
            st.json(item.payload)
            col1, col2 = st.columns(2)
            if col1.button("Утвердить", key=f"approve-{item.id}"):
                run_async(approval_gate.approve(tenant_id, item.id, reviewed_by=reviewed_by))
                st.session_state.setdefault("ready_to_publish", {})[item.id] = item
                st.rerun()
            if col2.button("Отклонить", key=f"reject-{item.id}"):
                run_async(approval_gate.reject(tenant_id, item.id, reviewed_by=reviewed_by))
                st.rerun()

    ready = st.session_state.get("ready_to_publish", {})
    if ready:
        st.subheader("Утверждено в этой сессии, готово к публикации")
        for approval_id, item in list(ready.items()):
            label = item.payload.get("title") or approval_id
            with st.expander(label):
                st.write(item.payload.get("body", ""))
                if item.object_type != "kb_suggestion":
                    st.caption(f"Публикация для типа {item.object_type!r} пока не реализована.")
                    continue
                if st.button("Опубликовать в базу знаний", key=f"publish-{approval_id}"):
                    article = run_async(
                        publish_approved(
                            tenant_id, approval_id,
                            approval_gate=approval_gate, publisher=get_publisher(),
                        )
                    )
                    st.success(f"Опубликовано: {article.title} (id={article.id})")
                    del ready[approval_id]
                    st.rerun()
