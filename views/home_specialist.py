"""Specialist home: just their queue — already-tracked jobs, never a loose
message. What needs attention comes first; finished work never crowds the
top of the screen."""

from __future__ import annotations

import streamlit as st

from core import models
from core import ui
from core.constants import RISK_AMBER, RISK_RED, STATUS_IN_PROGRESS, titlecase_name
from views import register


def render(user: dict) -> None:
    ui.page_header(f"Good to see you, {titlecase_name(user['name']).split()[0]}", "Jobs assigned to you — never a loose message.")

    jobs = models.list_jobs(owner_id=user["id"], exclude_dismissed=True)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("New", sum(1 for j in jobs if j["status"] == "new"))
    c2.metric("In progress", sum(1 for j in jobs if j["status"] == "in_progress"))
    c3.metric("Blocked", sum(1 for j in jobs if j["status"] == "blocked"))
    c4.metric("Done", sum(1 for j in jobs if j["status"] == "done"))
    st.write("")
    ui.risk_legend()

    needs_attention = [
        j for j in jobs if models.compute_risk(j) in (RISK_RED, RISK_AMBER) or models.is_pushed(j)
    ]
    needs_attention.sort(
        key=lambda j: (
            0 if models.is_pushed(j) else 1,
            0 if models.compute_risk(j) == RISK_RED else 1,
            j["sla_date"] or j["created_at"].date(),
        )
    )
    attention_ids = {j["id"] for j in needs_attention}

    st.markdown("#### Needs attention")
    ui.jobs_row_table(needs_attention, key_prefix="spec_attention")

    in_progress = [j for j in jobs if j["status"] == STATUS_IN_PROGRESS and j["id"] not in attention_ids]
    st.write("")
    st.markdown("#### In progress")
    ui.jobs_row_table(in_progress, key_prefix="spec_inprogress")

    st.write("")
    with st.expander("All my jobs, including done & closed"):
        register.render(user, only_own=True, show_header=False)
