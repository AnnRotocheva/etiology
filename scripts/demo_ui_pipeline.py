"""Страница 'Пайплайн': тот же сценарий, что scripts/demo.py, но в браузере."""
import streamlit as st

from demo_ui_common import get_approval_gate, get_gateway, get_publisher, get_tenant_id, run_async
from etiology.domain.analytics import record_csat
from etiology.domain.diagnostics.bug_report_composer import compose
from etiology.domain.diagnostics.diagnostic_collector import collect
from etiology.domain.diagnostics.triage import triage
from etiology.domain.knowledge_base import curate
from etiology.platform_core.event_bus import EventReader


def render() -> None:
    st.title("Пайплайн: обращение клиента")
    raw_message = st.text_area("Текст обращения клиента", height=100)
    csat_score = st.slider("CSAT-оценка (0 = не записывать)", 0, 5, 0)

    if not (st.button("Отправить", type="primary") and raw_message.strip()):
        return

    tenant_id = get_tenant_id()
    gateway = get_gateway()
    publisher = get_publisher()

    st.subheader(f"Клиент: {raw_message}")

    with st.status("Triage Agent...", expanded=True) as status:
        triage_result = run_async(triage(tenant_id, raw_message, gateway=gateway, publisher=publisher))
        st.write(f"severity={triage_result.severity}  topic_tag={triage_result.topic_tag}")
        st.write(f"kb_closable={triage_result.kb_closable}  kb_article_id={triage_result.kb_article_id}")
        status.update(label="Triage завершён", state="complete")

    with st.status("Diagnostic Collector...", expanded=True) as status:
        diag_result = run_async(
            collect(tenant_id, raw_message, triage_result, gateway=gateway, publisher=publisher)
        )
        st.write(f"outcome={diag_result.outcome}  escalated_to_human={diag_result.escalated_to_human}")
        if diag_result.matched_command:
            st.write(f"matched_command: {diag_result.matched_command.command}")
        st.write("Текст клиенту:")
        st.info(diag_result.advisory_text)
        status.update(label="Diagnostic Collector завершён", state="complete")

    if diag_result.outcome == "needs_bug_report":
        with st.status("Bug Report Composer...", expanded=True) as status:
            bug_report = run_async(
                compose(tenant_id, triage_result.incident_id, gateway=gateway, publisher=publisher)
            )
            st.write(f"**{bug_report.title}**")
            st.write(f"environment: {bug_report.environment}")
            st.write("steps_to_reproduce:")
            for step in bug_report.steps_to_reproduce:
                st.write(f"- {step}")
            st.write(f"diagnostic_summary: {bug_report.diagnostic_summary}")
            status.update(label="Bug Report составлен", state="complete")

        with st.status("Knowledge Curator...", expanded=True) as status:
            approval_gate = get_approval_gate()
            curator_result = run_async(
                curate(
                    tenant_id, triage_result.incident_id,
                    gateway=gateway, approval_gate=approval_gate, publisher=publisher,
                )
            )
            if curator_result.proposed:
                st.success(
                    f"Предложена статья KB: {curator_result.title!r} "
                    f"(approval_id={curator_result.suggestion_id})"
                )
                st.caption("Черновик ждёт утверждения человеком на странице Approval Gate.")
            else:
                st.write("Curator решил не предлагать новую статью.")
            status.update(label="Curator завершён", state="complete")

    if csat_score:
        run_async(record_csat(tenant_id, triage_result.incident_id, csat_score, get_publisher(), comment=None))
        st.write(f"CSAT записан: {csat_score}/5")

    st.subheader("Event Store (audit trail)")
    reader = EventReader()
    events = run_async(reader.read_aggregate_events(tenant_id, "incident", triage_result.incident_id))
    for event in events:
        st.write(f"`[{event.created_at:%H:%M:%S}]` {event.event_type}")
