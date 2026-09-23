"""Admin home: the intake workhorse — capture is one click away, and what's
urgent surfaces immediately."""

from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from core import models
from core import ui
from core.constants import RISK_AMBER, RISK_RED, titlecase_name


def render(user: dict) -> None:
    ui.page_header(f"Good to see you, {titlecase_name(user['name']).split()[0]}", "Log requests, keep the register straight.")

    st.info("Nothing is worked until it's logged. Use **Capture** in the sidebar to log a request in seconds.")
    ui.risk_legend()

    jobs = models.list_jobs(exclude_dismissed=True)

    today_jobs = [j for j in jobs if j["created_at"].date() == date.today()]
    urgent = [
        j for j in jobs if models.compute_risk(j) in (RISK_RED, RISK_AMBER) or models.is_pushed(j)
    ]
    new_jobs = [j for j in jobs if j["status"] == "new"]

    c1, c2, c3 = st.columns(3)
    c1.metric("Logged today", len(today_jobs))
    c2.metric("Needs attention", len(urgent))
    c3.metric("Not yet started", len(new_jobs))

    st.write("")
    st.markdown("#### Needs attention")
    urgent.sort(
        key=lambda j: (
            0 if models.is_pushed(j) else 1,
            j["sla_date"] or (date.today() + timedelta(days=999)),
        )
    )
    ui.jobs_row_table(urgent[:10], key_prefix="admin_home_urgent")

    st.write("")
    ui.updates_feed()
