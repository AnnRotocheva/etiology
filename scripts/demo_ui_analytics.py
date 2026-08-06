"""Страница 'Аналитика': read-model поверх Event Store."""
import streamlit as st

from demo_ui_common import get_tenant_id, run_async
from etiology.domain.analytics import csat_summary, resolution_rate, top_topics


def render() -> None:
    st.title("Аналитика")
    tenant_id = get_tenant_id()
    topics = run_async(top_topics(tenant_id))
    rate = run_async(resolution_rate(tenant_id))
    csat = run_async(csat_summary(tenant_id))

    col1, col2, col3 = st.columns(3)
    col1.metric("Resolution rate", f"{rate.rate:.0%}", f"{rate.resolved_count}/{rate.triaged_count}")
    col2.metric(
        "CSAT среднее",
        f"{csat.avg_score:.1f}" if csat.avg_score is not None else "—",
        f"{csat.count} оценок",
    )
    col3.metric("Инцидентов (triaged)", rate.triaged_count)

    st.subheader("Топ тем")
    if topics:
        st.table({"topic_tag": [t.topic_tag for t in topics], "count": [t.count for t in topics]})
    else:
        st.info("Пока нет данных.")
