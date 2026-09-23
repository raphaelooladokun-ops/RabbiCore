"""Principal overview: the whole book at a glance — what's stalled, what's
blocked, what's done-but-unbilled, without asking anyone. Status counts are
clickable and open the list of jobs behind that count."""

from __future__ import annotations

import streamlit as st

from core import models
from core import ui
from core.constants import RISK_RED, STATUS_LABELS_SHORT, humanize, titlecase_name

STATUS_ORDER = ["new", "in_progress", "blocked", "done", "closed"]
STATUS_FILTER_KEY = "principal_status_filter"


def render(user: dict) -> None:
    ui.page_header(f"Good to see you, {titlecase_name(user['name']).split()[0]}", "Firm-wide oversight — every job, every client.")
    ui.risk_legend()

    summary = models.firm_summary()
    jobs = models.list_jobs(exclude_dismissed=True)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Open jobs", summary["total"] - summary["by_status"].get("closed", 0) - summary["by_status"].get("dismissed", 0))
    c2.metric("Stalled >48h", summary["stalled"])
    c3.metric("Done, unbilled", summary["done_unbilled"])
    c4.metric("Red flags", summary["red_flags"])

    st.write("")
    st.markdown("#### By status — click a count to see those jobs")
    status_cols = st.columns(len(STATUS_ORDER))
    for col, s in zip(status_cols, STATUS_ORDER):
        count = summary["by_status"].get(s, 0)
        label = f"{STATUS_LABELS_SHORT.get(s, humanize(s))} ({count})"
        active = st.session_state.get(STATUS_FILTER_KEY) == s
        if col.button(label, key=f"statusbtn_{s}", type="primary" if active else "secondary", use_container_width=True):
            st.session_state[STATUS_FILTER_KEY] = None if active else s
            st.rerun()

    active_status = st.session_state.get(STATUS_FILTER_KEY)
    if active_status:
        st.write("")
        st.caption(f"Jobs — {STATUS_LABELS_SHORT.get(active_status, humanize(active_status))}")
        ui.jobs_row_table(
            [j for j in jobs if j["status"] == active_status],
            key_prefix=f"principal_status_{active_status}",
        )

    st.write("")
    st.markdown("#### Red-flag jobs")
    red = [j for j in jobs if models.compute_risk(j) == RISK_RED]
    ui.jobs_row_table(red, key_prefix="principal_redflag")

    st.write("")
    st.markdown("#### Stalled — no movement in 48h+")
    stalled = [j for j in jobs if models.is_stalled(j)]
    ui.jobs_row_table(stalled, key_prefix="principal_stalled")

    st.write("")
    st.markdown("#### Done — awaiting invoice")
    unbilled = [j for j in jobs if j["status"] == "done" and j["invoice_id"] is None]
    ui.jobs_row_table(unbilled, key_prefix="principal_unbilled")

    st.write("")
    st.markdown("#### Updates — recent specialist comments")
    st.caption("The latest notes specialists have left on jobs, newest first — a quicker read than the notification list.")
    updates = models.list_recent_specialist_comments(limit=15)
    if not updates:
        st.caption("No specialist comments yet.")
    for c in updates:
        col1, col2 = st.columns([5, 1])
        with col1:
            st.markdown(
                f"**{titlecase_name(c['author_name'])}** on **{ui.short_job_id(c['job_code'])}** — "
                f"{titlecase_name(c['client_name']) or '—'}: {c['job_title']}  \n"
                f"<span style='color:#8A94A6'>{c['created_at'].strftime('%d %b %Y, %H:%M')}</span>",
                unsafe_allow_html=True,
            )
            st.write(c["body"])
        with col2:
            if st.button("Open job", key=f"updatefeed_{c['id']}"):
                ui.go_to_job(c["job_pk"])
        st.divider()
