"""Specialist workload reporting — super_admin + principal (EC) only. Pick
a date range and see, per specialist, how many jobs they carried out
(started and/or completed) in that window. A simple count, nothing more —
built on the same started_at/completed_at pair the job-detail elapsed-time
badge uses."""

from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from core import models
from core import ui

_PRESETS = ["This week", "This month", "Custom range"]


def render(user: dict) -> None:
    ui.page_header("Workload", "Per-specialist job counts over a date range you choose.")

    preset = st.radio("Range", _PRESETS, horizontal=True, key="workload_preset")
    today = date.today()

    if preset == "This week":
        date_from = today - timedelta(days=today.weekday())
        date_to = today
    elif preset == "This month":
        date_from = today.replace(day=1)
        date_to = today
    else:
        c1, c2 = st.columns(2)
        date_from = c1.date_input("From", value=today - timedelta(days=30), key="workload_from")
        date_to = c2.date_input("To", value=today, key="workload_to")

    if date_from > date_to:
        st.error("'From' must be on or before 'To'.")
        return

    st.caption(f"{date_from.strftime('%d %b %Y')} – {date_to.strftime('%d %b %Y')}")
    st.write("")

    rows = models.workload_report(date_from, date_to)
    if not rows:
        st.caption("No jobs started or completed by any specialist in this range.")
        return

    header = st.columns([3, 1.3, 1.6])
    for col, label in zip(header, ["Specialist", "User ID", "Jobs carried out"]):
        col.markdown(f"**{label}**")
    for r in rows:
        cols = st.columns([3, 1.3, 1.6])
        cols[0].write(r["staff_name"])
        cols[1].write(f"#{r['staff_id']}")
        with cols[2].popover(str(r["job_count"]), key=f"wl_pop_{r['staff_id']}"):
            st.markdown(f"**{r['staff_name']} — jobs in this range**")
            for j in models.workload_report_jobs(r["staff_id"], date_from, date_to):
                label = f"{j['job_id']} — {j['client_name'] or '—'} — {j['title']}"
                if st.button(label, key=f"wl_job_{r['staff_id']}_{j['id']}", type="tertiary", use_container_width=True):
                    ui.go_to_job(j["id"])

    st.caption(f"{len(rows)} specialist(s) with activity in this range.")
