"""Admin home: the intake workhorse — capture is one click away, and what's
urgent surfaces immediately."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from core import models
from core import ui
from core.constants import RISK_AMBER, RISK_RED, STATUS_LABELS_SHORT, humanize


def render(user: dict) -> None:
    ui.page_header(f"Good to see you, {user['name'].split()[0]}", "Log requests, keep the register straight.")

    st.info("Nothing is worked until it's logged. Use **Capture** in the sidebar to log a request in seconds.")

    jobs = models.list_jobs(exclude_dismissed=True)

    today_jobs = [j for j in jobs if j["created_at"].date() == date.today()]
    urgent = [j for j in jobs if models.compute_risk(j) in (RISK_RED, RISK_AMBER)]
    new_jobs = [j for j in jobs if j["status"] == "new"]

    c1, c2, c3 = st.columns(3)
    c1.metric("Logged today", len(today_jobs))
    c2.metric("Needs attention", len(urgent))
    c3.metric("Not yet started", len(new_jobs))

    st.write("")
    st.markdown("#### Needs attention")
    urgent.sort(key=lambda j: (j["sla_date"] or (date.today() + timedelta(days=999))))
    if not urgent:
        st.caption("Nothing overdue, due soon, or blocked right now.")
    else:
        rows = [
            {
                "Job ID": j["job_id"], "Client": j["client_name"], "What": j["title"],
                "Status": STATUS_LABELS_SHORT.get(j["status"], humanize(j["status"])),
                "SLA date": j["sla_date"].isoformat() if j["sla_date"] else "—",
                "Owner": j["owner_name"] or "—",
            }
            for j in urgent[:10]
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
