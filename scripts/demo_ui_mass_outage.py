"""Страница 'Массовый сбой': тот же сценарий, что scripts/demo_mass_outage.py."""
import streamlit as st

from demo_ui_common import get_gateway, get_publisher, get_tenant_id, run_async
from etiology.domain.diagnostics.triage import triage
from etiology.domain.escalation_sync.incident_coordination import coordinate

MESSAGES = [
    "Трекер вообще не открывается, все ссылки на кампании дают ошибку 502",
    "У нас со всех кампаний сайт трекера не отвечает уже минут 10, это авария?",
    "Помогите, весь трафик падает мимо — домен трекера не открывается в браузере",
]


def render() -> None:
    st.title("Массовый сбой — Incident Coordination")
    st.write("Три независимых обращения про одну и ту же аварию:")
    for message in MESSAGES:
        st.write(f"- {message}")

    if not st.button("Запустить сценарий", type="primary"):
        return

    tenant_id = get_tenant_id()
    gateway = get_gateway()
    publisher = get_publisher()

    with st.status("Triage трёх обращений...", expanded=True) as status:
        for raw_message in MESSAGES:
            result = run_async(triage(tenant_id, raw_message, gateway=gateway, publisher=publisher))
            st.write(f"[{result.incident_id}] severity={result.severity} topic_tag={result.topic_tag}")
        status.update(label="Triage завершён", state="complete")

    with st.status("Incident Coordination Agent...", expanded=True) as status:
        coordination = run_async(
            coordinate(tenant_id, gateway=gateway, publisher=publisher, window_minutes=60)
        )
        if not coordination.correlated:
            st.warning("Агент не нашёл корреляции в этом прогоне (LLM не детерминирован).")
        else:
            for group in coordination.groups:
                st.write(f"Master-инцидент: {group.master_incident_id}")
                st.write(f"В группе: {group.incident_ids}")
                st.write(f"Статус: {group.status_summary}")
        status.update(label="Готово", state="complete")
