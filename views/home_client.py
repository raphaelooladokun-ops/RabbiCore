"""Client home: read-only, their jobs only, in plain language — plus what
we're waiting on from them, which is the accountability layer."""

from __future__ import annotations

import streamlit as st

from core import models
from core import ui
from core.constants import CATEGORY_LABELS, STATUS_LABELS, humanize


def render(user: dict) -> None:
    client = models.get_client(user["client_id"]) if user["client_id"] else None
    client_name = client["name"] if client else "your account"

    ui.page_header(f"Welcome, {user['name'].split()[0]}", f"Where things stand for {client_name}.")

    if not client:
        st.warning("Your account isn't linked to a client yet. Contact Rabbi Consult.")
        return

    ui.risk_legend()
    jobs = models.list_jobs(client_id=client["id"], exclude_dismissed=True)

    waiting = [j for j in jobs if j["waiting_on_client"] and j["status"] not in ("done", "closed")]
    if waiting:
        st.markdown("#### What we're waiting on from you")
        for j in waiting:
            due = f" — needed by **{j['sla_date'].isoformat()}**" if j["sla_date"] else ""
            st.warning(f"**{j['title']}** ({j['job_id']}): {j['waiting_on_client']}{due}")
        st.divider()

    st.markdown("#### Your jobs")
    if not jobs:
        st.caption("No jobs on file yet.")
        return

    for j in jobs:
        with st.container(border=True):
            badge = ui.risk_badge_html(models.compute_risk(j))
            st.markdown(f"**{j['title']}** &nbsp; {badge}", unsafe_allow_html=True)
            st.caption(f"{j['job_id']} · {humanize(j['category'], CATEGORY_LABELS)}")
            st.write(f"Status: **{humanize(j['status'], STATUS_LABELS)}**")
            if j["sla_date"]:
                st.write(f"Expected by: **{j['sla_date'].isoformat()}**")
