"""Principal overview: the whole book at a glance — what's stalled, what's
blocked, what's done-but-unbilled, without asking anyone."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core import models
from core import ui
from core.constants import RISK_RED, STATUS_LABELS_SHORT, humanize

STATUS_ORDER = ["new", "in_progress", "blocked", "done", "closed"]


def render(user: dict) -> None:
    ui.page_header(f"Good to see you, {user['name'].split()[0]}", "Firm-wide oversight — every job, every client.")

    summary = models.firm_summary()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Open jobs", summary["total"] - summary["by_status"].get("closed", 0) - summary["by_status"].get("dismissed", 0))
    c2.metric("Stalled >48h", summary["stalled"])
    c3.metric("Done, unbilled", summary["done_unbilled"])
    c4.metric("Red flags", summary["red_flags"])

    st.write("")
    col_a, col_b = st.columns([1, 1])
    with col_a:
        st.markdown("#### By status")
        rows = [
            {"Status": STATUS_LABELS_SHORT.get(s, humanize(s)), "Jobs": summary["by_status"].get(s, 0)}
            for s in STATUS_ORDER
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    with col_b:
        st.markdown("#### Red-flag jobs")
        jobs = models.list_jobs(exclude_dismissed=True)
        red = [j for j in jobs if models.compute_risk(j) == RISK_RED]
        if not red:
            st.caption("Nothing overdue right now.")
        else:
            rows = [
                {
                    "Job ID": j["job_id"], "Client": j["client_name"],
                    "SLA date": j["sla_date"].isoformat() if j["sla_date"] else "—",
                    "Owner": j["owner_name"] or "—",
                }
                for j in red
            ]
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    st.write("")
    st.markdown("#### Stalled — no movement in 48h+")
    jobs = models.list_jobs(exclude_dismissed=True)
    stalled = [j for j in jobs if models.is_stalled(j)]
    if not stalled:
        st.caption("Nothing stalled.")
    else:
        rows = [
            {
                "Job ID": j["job_id"], "Client": j["client_name"], "Status": STATUS_LABELS_SHORT.get(j["status"]),
                "Owner": j["owner_name"] or "—", "Last moved": j["status_changed_at"].strftime("%d %b %Y"),
            }
            for j in stalled
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    st.write("")
    st.markdown("#### Done — awaiting invoice")
    unbilled = [j for j in jobs if j["status"] == "done" and j["invoice_id"] is None]
    if not unbilled:
        st.caption("Nothing done and unbilled.")
    else:
        rows = [
            {"Job ID": j["job_id"], "Client": j["client_name"], "Title": j["title"]}
            for j in unbilled
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
