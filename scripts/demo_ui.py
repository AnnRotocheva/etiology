#!/usr/bin/env python3
"""Точка входа демо веб-интерфейса. Запуск: streamlit run scripts/demo_ui.py
(см. DEMO.md). Никакой бизнес-логики — только сборка навигации из страниц."""
import streamlit as st

import demo_ui_analytics
import demo_ui_approval_gate
import demo_ui_mass_outage
import demo_ui_pipeline

st.set_page_config(page_title="Etiology — демо", layout="wide")

pg = st.navigation(
    [
        st.Page(demo_ui_pipeline.render, title="Пайплайн", icon="🔍", url_path="pipeline", default=True),
        st.Page(demo_ui_approval_gate.render, title="Approval Gate", icon="✅", url_path="approval-gate"),
        st.Page(demo_ui_mass_outage.render, title="Массовый сбой", icon="🔥", url_path="mass-outage"),
        st.Page(demo_ui_analytics.render, title="Аналитика", icon="📊", url_path="analytics"),
    ]
)
pg.run()
